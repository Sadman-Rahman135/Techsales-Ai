"""Blueprint §7.1-§7.4 on real Postgres and Chroma: upload → worker → ready → publish, and failure paths."""

import threading
import uuid
from collections.abc import Sequence
from datetime import timedelta

import chromadb
import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.permissions import Principal
from app.core.time import utcnow
from app.db import models as m
from app.db.session import tenant_session
from app.ingestion import worker as w
from app.ingestion.embeddings import EmbeddingBatch, EmbeddingError, FakeEmbedder
from app.ingestion.errors import ErrorCode, IngestionError
from app.ingestion.reconcile import reconcile_all
from app.ingestion.validation import validate_upload
from app.integrations.chroma import IndexMismatch, KnowledgeIndex
from app.services import documents as docs
from app.services import versions
from app.services.storage import LocalStorage
from tests.builders import docx_bytes, pdf_bytes, scanned_pdf_bytes, warranty_pdf
from tests.integration.conftest import Tenant

pytestmark = pytest.mark.integration


def upload(
    ctx: w.WorkerContext,
    principal: Principal,
    data: bytes,
    filename: str = "warranty.pdf",
    **kw: object,
) -> docs.UploadResult:
    validated = validate_upload(filename, None, data, ctx.settings)
    with tenant_session(principal.organization_id, ctx.engine) as session:
        return docs.create_upload(session, ctx.storage, ctx.settings, principal, validated, **kw)  # type: ignore[arg-type]


def load(
    engine: Engine, org_id: uuid.UUID, version_id: uuid.UUID
) -> tuple[m.DocumentVersion, m.IngestionJob, int]:
    with tenant_session(org_id, engine) as session:
        version = session.get_one(m.DocumentVersion, version_id)
        job = docs.latest_jobs(session, org_id, [version_id])[version_id]
        chunks = session.scalar(select(func.count()).where(m.DocumentChunk.version_id == version_id)) or 0
        return version, job, chunks


def publish(ctx: w.WorkerContext, principal: Principal, result: docs.UploadResult) -> None:
    with tenant_session(principal.organization_id, ctx.engine) as session:
        versions.publish_version(session, ctx.index, principal, result.document.id, result.version.id)


def search(ctx: w.WorkerContext, principal: Principal, text: str) -> list[str]:
    with tenant_session(principal.organization_id, ctx.engine) as session:
        active = versions.active_version_ids(session, principal)
    vector = FakeEmbedder("test-embedding", 8).embed([text]).vectors[0]
    return [hit.text for hit in ctx.index.query(principal, vector, active, n_results=10)]


def make_due(engine: Engine, job_id: uuid.UUID) -> None:
    with Session(engine) as session, session.begin():
        session.execute(update(m.IngestionJob).where(m.IngestionJob.id == job_id).values(run_after=utcnow()))


# ---------- happy path ----------


def test_upload_queues_a_job_and_the_worker_makes_the_version_ready(
    worker: w.WorkerContext, tenant: Tenant
) -> None:
    result = upload(worker, tenant.manager, warranty_pdf(), audience=m.Audience.PUBLIC)
    assert result.job.state == m.JobState.QUEUED.value
    assert result.version.status == m.VersionStatus.UPLOADED.value
    assert result.version.storage_key == f"{tenant.org.id}/{result.document.id}/{result.version.id}.pdf"
    assert worker.storage.exists(result.version.storage_key)

    assert w.run_once(worker) is m.JobState.DONE
    version, job, chunk_rows = load(worker.engine, tenant.org.id, result.version.id)
    assert version.status == m.VersionStatus.READY.value
    assert version.expected_chunk_count == version.indexed_chunk_count == chunk_rows
    assert worker.index.count_version(tenant.org.id, version.id) == chunk_rows > 0
    assert set(job.stage_timings) >= {
        "parsing",
        "chunking",
        "embedding",
        "indexing",
        "verifying",
        "embedding_tokens",
    }
    assert (job.attempts, job.error_code, job.locked_until) == (1, None, None)

    # Ready is not answerable: only an explicit publish makes it so (§6.3, §7.3).
    assert search(worker, tenant.customer, "laptop warranty") == []
    publish(worker, tenant.manager, result)
    hits = search(worker, tenant.customer, "how to claim the laptop warranty")
    assert any("TechCare Laptop Warranty > How to claim" in hit for hit in hits)


def test_chunk_rows_keep_source_references(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    w.run_once(worker)
    with tenant_session(tenant.org.id, worker.engine) as session:
        rows = session.scalars(
            select(m.DocumentChunk)
            .where(m.DocumentChunk.version_id == result.version.id)
            .order_by(m.DocumentChunk.chunk_index)
        ).all()
        sections = [(r.section, r.page_start, r.page_end) for r in rows]
    assert ("TechCare Laptop Warranty > What is not covered", 2, 2) in sections
    assert ("TechCare Laptop Warranty > How to claim", 3, 3) in sections


def test_reprocessing_a_version_is_idempotent(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    w.run_once(worker)
    first = load(worker.engine, tenant.org.id, result.version.id)
    with tenant_session(tenant.org.id, worker.engine) as session:
        session.get_one(m.DocumentVersion, result.version.id).status = m.VersionStatus.FAILED.value
        session.add(m.IngestionJob(organization_id=tenant.org.id, version_id=result.version.id))
    assert w.run_once(worker) is m.JobState.DONE
    again = load(worker.engine, tenant.org.id, result.version.id)
    assert again[2] == first[2]  # same chunk rows, none duplicated
    assert worker.index.count_version(tenant.org.id, result.version.id) == first[2]


def test_docx_and_txt_are_ingested(worker: w.WorkerContext, tenant: Tenant) -> None:
    upload(
        worker, tenant.manager, docx_bytes([("h1", "Store hours"), ("p", "Open 9am to 9pm.")]), "hours.docx"
    )
    upload(worker, tenant.manager, b"# Contact\nCall 1-800-555-0199.", "contact.txt")
    assert [w.run_once(worker), w.run_once(worker)] == [m.JobState.DONE, m.JobState.DONE]


def test_two_versions_and_a_deleted_document(worker: w.WorkerContext, tenant: Tenant) -> None:
    v1 = upload(
        worker, tenant.manager, pdf_bytes([[("h1", "Returns"), ("p", "Return laptops within 14 days.")]])
    )
    w.run_once(worker)
    publish(worker, tenant.manager, v1)
    v2 = upload(
        worker,
        tenant.manager,
        pdf_bytes([[("h1", "Returns"), ("p", "Return laptops within 30 days.")]]),
        document_id=v1.document.id,
    )
    assert v2.version.version_number == 2
    w.run_once(worker)
    assert any(
        "14 days" in hit for hit in search(worker, tenant.customer, "return laptops days")
    )  # v2 not live yet

    publish(worker, tenant.manager, v2)
    hits = search(worker, tenant.customer, "return laptops days")
    assert any("30 days" in hit for hit in hits) and not any("14 days" in hit for hit in hits)

    other = upload(worker, tenant.manager, pdf_bytes([[("h1", "Hours"), ("p", "Open 9am to 9pm daily.")]]))
    w.run_once(worker)
    publish(worker, tenant.manager, other)
    with tenant_session(tenant.org.id, worker.engine) as session:
        versions.soft_delete_document(session, tenant.admin, v1.document.id)
    assert not any("days" in hit for hit in search(worker, tenant.customer, "return laptops days"))

    summary = reconcile_all(worker)
    assert summary.orphan_vector_versions_deleted == 2  # archived v1 and the deleted document's v2
    assert worker.index.count_version(tenant.org.id, v1.version.id) == 0
    assert worker.index.count_version(tenant.org.id, v2.version.id) == 0
    assert worker.index.count_version(tenant.org.id, other.version.id) > 0  # untouched
    assert any("9am" in hit for hit in search(worker, tenant.customer, "opening hours"))


# ---------- upload rules ----------


def test_concurrent_ingestions_per_tenant_are_limited(worker: w.WorkerContext, tenant: Tenant) -> None:
    worker.settings = worker.settings.model_copy(update={"max_active_ingestions_per_org": 2})
    upload(worker, tenant.manager, pdf_bytes([[("p", "one")]]), "a.pdf")
    upload(worker, tenant.manager, pdf_bytes([[("p", "two")]]), "b.pdf")
    with pytest.raises(IngestionError) as info:
        upload(worker, tenant.manager, pdf_bytes([[("p", "three")]]), "c.pdf")
    assert info.value.code is ErrorCode.TOO_MANY_ACTIVE_INGESTIONS
    w.run_once(worker)
    upload(worker, tenant.manager, pdf_bytes([[("p", "three")]]), "c.pdf")  # a slot freed up


def test_duplicate_files(worker: w.WorkerContext, tenant: Tenant) -> None:
    data = pdf_bytes([[("p", "Same bytes")]])
    first = upload(worker, tenant.manager, data)
    with pytest.raises(IngestionError) as info:
        upload(worker, tenant.manager, data, document_id=first.document.id)
    assert info.value.code is ErrorCode.DUPLICATE_FILE
    with pytest.raises(docs.DuplicateFile) as dup:
        upload(worker, tenant.manager, data)
    assert dup.value.document_id == first.document.id
    assert upload(worker, tenant.manager, data, allow_duplicate=True).document.id != first.document.id


def test_only_managers_and_admins_upload(worker: w.WorkerContext, tenant: Tenant) -> None:
    with pytest.raises(versions.PermissionDenied):
        upload(worker, tenant.employee, pdf_bytes([[("p", "x")]]))


# ---------- failures, retries, cancellation ----------


class FlakyEmbedder:
    model, dimension = "test-embedding", 8

    def __init__(self, failures: int, retryable: bool = True) -> None:
        self.failures, self.retryable, self.calls = failures, retryable, 0

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls += 1
        if self.calls <= self.failures:
            raise EmbeddingError("HTTP 429", retryable=self.retryable)
        return FakeEmbedder(self.model, self.dimension).embed(texts)

    def embed_queries(self, texts: Sequence[str]) -> EmbeddingBatch:
        return self.embed(texts)


def test_parse_errors_fail_at_once_without_retry(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, scanned_pdf_bytes(), "scan.pdf")
    assert w.run_once(worker) is m.JobState.FAILED
    version, job, _ = load(worker.engine, tenant.org.id, result.version.id)
    assert (job.state, job.error_code, job.failed_stage, job.attempts) == (
        "failed",
        "FILE_NO_TEXT",
        "parsing",
        1,
    )
    assert version.status == m.VersionStatus.FAILED.value


def test_transient_errors_retry_with_backoff_until_attempts_run_out(
    worker: w.WorkerContext, tenant: Tenant
) -> None:
    worker.embedder = FlakyEmbedder(failures=99)
    result = upload(worker, tenant.manager, warranty_pdf())
    assert w.run_once(worker) is m.JobState.QUEUED
    version, job, _ = load(worker.engine, tenant.org.id, result.version.id)
    assert (job.error_code, job.failed_stage, job.attempts) == ("EMBEDDING_FAILED", "embedding", 1)
    assert job.run_after > utcnow()  # backoff: not claimable yet
    assert version.status == m.VersionStatus.PROCESSING.value
    assert w.run_once(worker) is None

    for _ in range(2):
        make_due(worker.engine, job.id)
        w.run_once(worker)
    version, job, _ = load(worker.engine, tenant.org.id, result.version.id)
    assert (job.state, job.attempts) == ("failed", 3)
    assert version.status == m.VersionStatus.FAILED.value
    assert worker.index.count_version(tenant.org.id, version.id) == 0


def test_backoff_grows_exponentially(worker: w.WorkerContext, tenant: Tenant) -> None:
    worker.embedder = FlakyEmbedder(failures=99)
    worker.settings = worker.settings.model_copy(
        update={"worker_retry_base_seconds": 100, "worker_retry_max_seconds": 150}
    )
    result = upload(worker, tenant.manager, warranty_pdf())
    delays = []
    for _ in range(2):
        before = utcnow()
        w.run_once(worker)
        job = load(worker.engine, tenant.org.id, result.version.id)[1]
        delays.append((job.run_after - before).total_seconds())
        make_due(worker.engine, job.id)
    assert 99 <= delays[0] <= 102 and 149 <= delays[1] <= 152  # 100s, then 200s capped at 150s


def test_a_transient_error_then_success(worker: w.WorkerContext, tenant: Tenant) -> None:
    worker.embedder = FlakyEmbedder(failures=1)
    result = upload(worker, tenant.manager, warranty_pdf())
    w.run_once(worker)
    make_due(worker.engine, result.job.id)
    assert w.run_once(worker) is m.JobState.DONE
    _, job, _ = load(worker.engine, tenant.org.id, result.version.id)
    assert (job.attempts, job.error_code) == (2, None)


def test_bad_embedding_configuration_is_not_retried(worker: w.WorkerContext, tenant: Tenant) -> None:
    worker.embedder = FlakyEmbedder(failures=1, retryable=False)
    upload(worker, tenant.manager, warranty_pdf())
    assert w.run_once(worker) is m.JobState.FAILED


def test_retry_queues_a_fresh_job(worker: w.WorkerContext, tenant: Tenant) -> None:
    worker.embedder = FlakyEmbedder(failures=1, retryable=False)
    result = upload(worker, tenant.manager, warranty_pdf())
    w.run_once(worker)
    with tenant_session(tenant.org.id, worker.engine) as session:
        fresh = docs.retry(session, tenant.manager, result.job.id)
        with pytest.raises(versions.InvalidTransition):
            docs.retry(session, tenant.manager, result.job.id)  # a newer job exists now
    assert fresh.id != result.job.id
    assert w.run_once(worker) is m.JobState.DONE


def test_cancel_a_queued_job(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    with tenant_session(tenant.org.id, worker.engine) as session:
        assert docs.cancel(session, worker.index, tenant.manager, result.job.id).state == "cancelled"
    assert w.run_once(worker) is None


def test_cancel_while_processing_stops_at_the_next_stage(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())

    class CancellingEmbedder(FakeEmbedder):
        def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
            with tenant_session(tenant.org.id, worker.engine) as session:
                docs.cancel(session, worker.index, tenant.manager, result.job.id)
            return super().embed(texts)

    worker.embedder = CancellingEmbedder("test-embedding", 8)
    assert w.run_once(worker) is m.JobState.CANCELLED
    version, job, chunks = load(worker.engine, tenant.org.id, result.version.id)
    assert (job.state, version.status, chunks) == ("cancelled", "failed", 0)
    assert worker.index.count_version(tenant.org.id, version.id) == 0


def test_cancel_a_ready_draft_discards_it_but_published_versions_cannot_be_cancelled(
    worker: w.WorkerContext, tenant: Tenant
) -> None:
    draft = upload(worker, tenant.manager, warranty_pdf())
    w.run_once(worker)
    with tenant_session(tenant.org.id, worker.engine) as session:
        docs.cancel(session, worker.index, tenant.manager, draft.job.id)
    version, _, _ = load(worker.engine, tenant.org.id, draft.version.id)
    assert version.status == m.VersionStatus.ARCHIVED.value
    assert worker.index.count_version(tenant.org.id, version.id) == 0

    live = upload(worker, tenant.manager, pdf_bytes([[("p", "Live policy text.")]]))
    w.run_once(worker)
    publish(worker, tenant.manager, live)
    with tenant_session(tenant.org.id, worker.engine) as session, pytest.raises(versions.InvalidTransition):
        docs.cancel(session, worker.index, tenant.manager, live.job.id)


def test_document_deleted_while_queued(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    with tenant_session(tenant.org.id, worker.engine) as session:
        session.get_one(m.Document, result.document.id).deleted_at = utcnow()
    assert w.run_once(worker) is m.JobState.FAILED
    assert load(worker.engine, tenant.org.id, result.version.id)[1].error_code == "DOCUMENT_GONE"


def test_missing_stored_file_fails_clearly(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    worker.storage.delete(result.version.storage_key)
    assert w.run_once(worker) is m.JobState.FAILED
    assert load(worker.engine, tenant.org.id, result.version.id)[1].error_code == "FILE_MISSING"


# ---------- leases and concurrency ----------


def test_an_expired_attempt_cannot_overwrite_the_new_one(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    stale = w.claim_next(worker)
    assert stale is not None
    with Session(worker.engine) as session, session.begin():
        session.execute(
            update(m.IngestionJob)
            .where(m.IngestionJob.id == stale.id)
            .values(locked_until=utcnow() - timedelta(seconds=1))
        )
    assert reconcile_all(worker).requeued_jobs == 1
    assert w.run_once(worker) is m.JobState.DONE  # attempt 2 finishes

    assert w.process(worker, stale) is m.JobState.QUEUED  # attempt 1 wakes up: lease lost, nothing written
    version, job, _ = load(worker.engine, tenant.org.id, result.version.id)
    assert (job.state, job.attempts, version.status) == ("done", 2, "ready")


def test_expired_job_without_attempts_left_fails(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    claimed = w.claim_next(worker)
    assert claimed is not None
    with Session(worker.engine) as session, session.begin():
        session.execute(
            update(m.IngestionJob)
            .where(m.IngestionJob.id == claimed.id)
            .values(locked_until=utcnow() - timedelta(seconds=1), attempts=3)
        )
    with tenant_session(tenant.org.id, worker.engine) as session:
        session.get_one(m.DocumentVersion, result.version.id).status = m.VersionStatus.PROCESSING.value
    assert reconcile_all(worker).failed_jobs == 1
    version, job, _ = load(worker.engine, tenant.org.id, result.version.id)
    assert (job.state, job.error_code, version.status) == ("failed", "LEASE_EXPIRED", "failed")


def test_concurrent_workers_never_claim_the_same_job(worker: w.WorkerContext, tenant: Tenant) -> None:
    worker.settings = worker.settings.model_copy(update={"max_active_ingestions_per_org": 20})
    for n in range(6):
        upload(worker, tenant.manager, pdf_bytes([[("p", f"Document number {n}.")]]), f"d{n}.pdf")
    barrier = threading.Barrier(6)
    claimed: list[uuid.UUID] = []

    def claim() -> None:
        barrier.wait()
        job = w.claim_next(worker)
        if job is not None:
            claimed.append(job.id)

    threads = [threading.Thread(target=claim) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == len(set(claimed)) == 6


def test_concurrent_publishes_leave_exactly_one_published_version(
    worker: w.WorkerContext, tenant: Tenant
) -> None:
    v1 = upload(worker, tenant.manager, pdf_bytes([[("p", "Version one text.")]]))
    v2 = upload(worker, tenant.manager, pdf_bytes([[("p", "Version two text.")]]), document_id=v1.document.id)
    w.run_once(worker)
    w.run_once(worker)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def go(result: docs.UploadResult) -> None:
        barrier.wait()
        try:
            publish(worker, tenant.manager, result)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(r,)) for r in (v1, v2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(isinstance(e, versions.InvalidTransition) for e in errors)  # a loser sees "not ready" at most
    with tenant_session(tenant.org.id, worker.engine) as session:
        published = session.scalars(
            select(m.DocumentVersion.id).where(
                m.DocumentVersion.document_id == v1.document.id, m.DocumentVersion.status == "published"
            )
        ).all()
        active = session.get_one(m.Document, v1.document.id).active_version_id
    assert len(published) == 1 and active == published[0]


# ---------- reconciliation ----------


def test_orphan_files_are_removed_only_after_the_grace_period(
    worker: w.WorkerContext, tenant: Tenant
) -> None:
    kept = upload(worker, tenant.manager, warranty_pdf())
    orphan = f"{tenant.org.id}/{uuid.uuid4()}/{uuid.uuid4()}.pdf"
    worker.storage.put(orphan, b"%PDF-1.7 leftover")
    assert reconcile_all(worker).orphan_files_deleted == 0  # too young
    assert reconcile_all(worker, now=utcnow() + timedelta(hours=2)).orphan_files_deleted == 1
    assert not worker.storage.exists(orphan)
    assert worker.storage.exists(kept.version.storage_key)


def test_count_drift_is_reported(worker: w.WorkerContext, tenant: Tenant) -> None:
    result = upload(worker, tenant.manager, warranty_pdf())
    w.run_once(worker)
    worker.index.prune_version(tenant.org.id, result.version.id, keep=[])  # vectors lost behind our back
    drift = {r.version_id for r in reconcile_all(worker).drift}
    assert result.version.id in drift  # (other tenants' committed test data may drift too)
    with tenant_session(tenant.org.id, worker.engine) as session, pytest.raises(versions.VersionNotIndexed):
        versions.publish_version(session, worker.index, tenant.manager, result.document.id, result.version.id)


# ---------- embedding fingerprint (§7.3: never mix models in one index) ----------


def test_an_index_refuses_a_different_embedding_model() -> None:
    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    name = f"test_{uuid.uuid4().hex}"
    try:
        index = KnowledgeIndex(client, dimension=8, embedding_model="model-a", collection_name=name)
        KnowledgeIndex(
            client, dimension=8, embedding_model="model-a", collection_name=name
        )  # same pair: fine
        org, version = uuid.uuid4(), uuid.uuid4()
        index.prune_version(org, version, [])  # no-op on an empty index
        with pytest.raises(IndexMismatch):
            KnowledgeIndex(client, dimension=8, embedding_model="model-b", collection_name=name)
        with pytest.raises(IndexMismatch):
            KnowledgeIndex(client, dimension=16, embedding_model="model-a", collection_name=name)
    finally:
        client.delete_collection(name)


def test_storage_round_trip(storage: LocalStorage) -> None:
    storage.put("org/doc/v.pdf", b"bytes")
    assert storage.get("org/doc/v.pdf") == b"bytes"
    assert [o.key for o in storage.list("org/")] == ["org/doc/v.pdf"]


# ---------- rebuild (§7.3, ADR-013: a model change means a new collection, rebuilt from originals) ----------


def test_rebuild_reindexes_live_versions_into_a_new_collection(
    worker: w.WorkerContext, tenant: Tenant
) -> None:
    from app.ingestion.rebuild import rebuild

    result = upload(worker, tenant.manager, warranty_pdf())
    w.run_once(worker)
    publish(worker, tenant.manager, result)
    expected = load(worker.engine, tenant.org.id, result.version.id)[2]

    settings = get_settings()
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    name = f"test_rebuild_{uuid.uuid4().hex}"
    new_model = FakeEmbedder("next-model", 16)
    try:
        new_index = KnowledgeIndex(
            client, dimension=16, embedding_model=new_model.model, collection_name=name
        )
        ctx = w.WorkerContext(worker.engine, new_index, worker.storage, new_model, worker.settings)
        # Other tests' versions have no stored file in this storage: reported as failed, never fatal.
        report = rebuild(ctx)
        assert new_index.count_version(tenant.org.id, result.version.id) == expected > 0
        assert (
            report.versions >= 1 and report.rows_rewritten == 0
        )  # same chunker: chunk rows and citations kept
        version, _, chunks = load(worker.engine, tenant.org.id, result.version.id)
        assert version.indexed_chunk_count == chunks == expected
        with pytest.raises(IndexMismatch):  # the old model can no longer open the rebuilt collection
            KnowledgeIndex(client, dimension=16, embedding_model="test-embedding", collection_name=name)
    finally:
        client.delete_collection(name)
