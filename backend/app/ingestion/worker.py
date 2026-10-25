"""Ingestion worker (blueprint §7.3, §7.4, ADR-008): `python -m app.ingestion.worker`.

One job at a time per process; run several processes to scale out.

- **Claim:** `FOR UPDATE SKIP LOCKED` on a queued job whose `run_after` has passed. Claiming increments
  `attempts` and takes a lease (`locked_until`). Every later write is fenced on that attempt number, so a
  worker whose lease expired and whose job was requeued can never overwrite the new attempt's result.
- **Stages:** parsing → chunking → embedding → indexing, each timed into `stage_timings`. Between stages the
  lease is renewed and `cancel_requested` is checked (cancellation before publication).
- **Errors:** non-retryable codes (corrupt file, no text, bad embedding config) fail at once. Transient ones
  are requeued with exponential backoff until `max_attempts`, then fail.
- **Done:** chunk rows and vectors are written with deterministic IDs (idempotent re-runs), counts are
  verified against Chroma, and only then is the version `ready`. Publishing stays a separate, explicit
  admin action (app/services/versions.py), which re-verifies.
- **Maintenance** runs on a schedule inside the loop: reconciliation (app/ingestion/reconcile.py) and
  retention (app/services/retention.py). The loop also writes a heartbeat for `GET /health`
  (app/ingestion/heartbeat.py).
"""

import argparse
import logging
import signal
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.log_hygiene import configure_logging
from app.core.time import utcnow
from app.db.models import (
    ACTIVE_JOB_STATES,
    Document,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    JobState,
    VersionStatus,
)
from app.db.session import get_engine, session_factory, set_tenant, tenant_session
from app.ingestion import heartbeat
from app.ingestion.chunking import Chunk, chunk_segments
from app.ingestion.embeddings import Embedder, EmbeddingError, embed_all, get_embedder
from app.ingestion.errors import ErrorCode, IngestionError
from app.ingestion.parsing import parse_document
from app.ingestion.validation import ALLOWED, SourceType
from app.integrations.chroma import ChunkMetadata, ChunkRecord, KnowledgeIndex, get_knowledge_index
from app.services.storage import ObjectStorage, get_storage
from app.services.versions import reconcile_version, transition

log = logging.getLogger("app.ingestion.worker")

SOURCE_TYPES: dict[str, SourceType] = {mime: source for source, mime in ALLOWED.values()}


@dataclass(slots=True)
class WorkerContext:
    engine: Engine
    index: KnowledgeIndex
    storage: ObjectStorage
    embedder: Embedder
    settings: Settings


@dataclass(slots=True)
class ClaimedJob:
    id: uuid.UUID
    organization_id: uuid.UUID
    version_id: uuid.UUID
    attempt: int
    stage: JobState = JobState.PARSING
    timings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Source:
    document_id: uuid.UUID
    title: str
    audience: str
    product_model: str | None
    storage_key: str
    source_type: SourceType


class LeaseLost(Exception):
    """Another attempt owns the job now; stop without writing anything."""


class Cancelled(Exception):
    pass


# ---------- claiming ----------


def claim_next(ctx: WorkerContext) -> ClaimedJob | None:
    now = utcnow()
    with session_factory(ctx.engine)() as session, session.begin():
        job = session.scalar(
            select(IngestionJob)
            .where(IngestionJob.state == JobState.QUEUED.value, IngestionJob.run_after <= now)
            .order_by(IngestionJob.run_after, IngestionJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        if job.cancel_requested:  # cancelled while waiting: never start it
            job.state, job.finished_at = JobState.CANCELLED.value, now
            return None
        job.state = JobState.PARSING.value
        job.attempts += 1
        job.locked_until = now + timedelta(seconds=ctx.settings.worker_lease_seconds)
        job.started_at = job.started_at or now
        return ClaimedJob(
            job.id, job.organization_id, job.version_id, job.attempts, timings=dict(job.stage_timings)
        )


def _fenced_job(session: Session, job: ClaimedJob) -> IngestionJob:
    row = session.scalar(
        select(IngestionJob)
        .where(
            IngestionJob.id == job.id,
            IngestionJob.attempts == job.attempt,
            IngestionJob.state.in_([s.value for s in ACTIVE_JOB_STATES]),
        )
        .with_for_update()
    )
    if row is None:
        raise LeaseLost(str(job.id))
    return row


def _advance(ctx: WorkerContext, job: ClaimedJob, to: JobState) -> None:
    """Move to the next stage, renew the lease, and honour a pending cancellation."""
    with session_factory(ctx.engine)() as session, session.begin():
        row = _fenced_job(session, job)
        if row.cancel_requested:
            raise Cancelled
        row.state = to.value
        row.locked_until = utcnow() + timedelta(seconds=ctx.settings.worker_lease_seconds)
        row.stage_timings = dict(job.timings)
    job.stage = to


@contextmanager
def _timed(job: ClaimedJob, stage: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        job.timings[stage] = job.timings.get(stage, 0) + round((time.perf_counter() - started) * 1000)


# ---------- processing ----------


def process(ctx: WorkerContext, job: ClaimedJob) -> JobState:
    """Run one claimed job to its next resting state (done, failed, cancelled or re-queued)."""
    try:
        source = _start(ctx, job)
        try:
            data = ctx.storage.get(source.storage_key)
        except FileNotFoundError as exc:
            raise IngestionError(ErrorCode.FILE_MISSING, source.storage_key) from exc

        with _timed(job, "parsing"):
            parsed = parse_document(data, source.source_type)
        _advance(ctx, job, JobState.CHUNKING)

        settings = ctx.settings
        with _timed(job, "chunking"):
            chunks = chunk_segments(
                parsed.segments,
                settings.chunk_target_tokens,
                settings.chunk_max_tokens,
                settings.chunk_overlap_tokens,
            )
        if not chunks:
            raise IngestionError(ErrorCode.FILE_NO_TEXT, "no chunks")
        _advance(ctx, job, JobState.EMBEDDING)

        with _timed(job, "embedding"):
            try:
                embedded = embed_all(
                    ctx.embedder,
                    [c.text for c in chunks],
                    settings.embedding_batch_size,
                    settings.embedding_price_per_1k_tokens,
                )
            except EmbeddingError as exc:
                code = ErrorCode.EMBEDDING_FAILED if exc.retryable else ErrorCode.EMBEDDING_CONFIG_INVALID
                raise IngestionError(code, str(exc), retryable=exc.retryable) from exc
        job.timings["embedding_tokens"] = embedded.tokens
        _advance(ctx, job, JobState.INDEXING)

        with _timed(job, "indexing"):
            records = [
                ChunkRecord(c.index, c.text, vector, _metadata(job, source, c))
                for c, vector in zip(chunks, embedded.vectors, strict=True)
            ]
            try:
                ids = ctx.index.upsert_version(job.organization_id, job.version_id, records)
                ctx.index.prune_version(job.organization_id, job.version_id, ids)
            except Exception as exc:
                raise IngestionError(ErrorCode.INDEX_UNAVAILABLE, type(exc).__name__, retryable=True) from exc
        _finish(ctx, job, chunks, parsed.warnings)
        return JobState.DONE
    except LeaseLost:
        log.warning("job %s: lease lost; another attempt owns it", job.id)
        return JobState.QUEUED
    except Cancelled:
        _close(ctx, job, JobState.CANCELLED, None)
        return JobState.CANCELLED
    except IngestionError as exc:
        log.warning("job %s failed at %s: %s (%s)", job.id, job.stage, exc.code, exc.detail)
        return _fail_or_retry(ctx, job, exc.code, exc.retryable)
    except OperationalError as exc:
        log.warning("job %s: database unavailable (%s)", job.id, type(exc).__name__)
        return _fail_or_retry(ctx, job, ErrorCode.INTERNAL_ERROR, True)
    except Exception:
        log.exception("job %s: unexpected error at %s", job.id, job.stage)
        return _fail_or_retry(ctx, job, ErrorCode.INTERNAL_ERROR, True)


def _start(ctx: WorkerContext, job: ClaimedJob) -> _Source:
    with tenant_session(job.organization_id, ctx.engine) as session:
        _fenced_job(session, job)
        version = session.get(DocumentVersion, job.version_id)
        document = session.get(Document, version.document_id) if version else None
        if version is None or document is None or document.deleted_at is not None:
            raise IngestionError(ErrorCode.DOCUMENT_GONE)
        status = VersionStatus(version.status)
        if status in (VersionStatus.UPLOADED, VersionStatus.FAILED):
            transition(version, VersionStatus.PROCESSING)
        elif status is not VersionStatus.PROCESSING:  # e.g. archived meanwhile; resuming PROCESSING is fine
            raise IngestionError(ErrorCode.DOCUMENT_GONE, f"version is {status}")
        return _Source(
            document_id=document.id,
            title=document.title,
            audience=document.audience,
            product_model=document.product_model,
            storage_key=version.storage_key,
            source_type=SOURCE_TYPES[version.mime_type],
        )


def _metadata(job: ClaimedJob, source: _Source, chunk: Chunk) -> ChunkMetadata:
    return ChunkMetadata.model_validate(
        {
            "organization_id": job.organization_id,
            "document_id": source.document_id,
            "document_version_id": job.version_id,
            "title": source.title,
            "source_type": source.source_type,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "section": chunk.section,
            "audience": source.audience,
            "product_model": source.product_model,
        }
    )


def _finish(ctx: WorkerContext, job: ClaimedJob, chunks: list[Chunk], warnings: list[dict[str, Any]]) -> None:
    """Write chunk rows, verify both stores agree, then mark the version ready (one transaction)."""
    started = time.perf_counter()
    with tenant_session(job.organization_id, ctx.engine) as session:
        row = _fenced_job(session, job)
        if row.cancel_requested:
            raise Cancelled
        version = session.get(DocumentVersion, job.version_id, with_for_update=True)
        if version is None:
            raise IngestionError(ErrorCode.DOCUMENT_GONE)
        session.execute(delete(DocumentChunk).where(DocumentChunk.version_id == version.id))
        session.add_all(
            DocumentChunk(
                organization_id=job.organization_id,
                version_id=version.id,
                chunk_index=c.index,
                chroma_id=f"{job.organization_id}:{version.id}:{c.index}",
                page_start=c.page_start,
                page_end=c.page_end,
                section=c.section,
                text_checksum=c.checksum,
                token_count=c.token_count,
                text=c.text,
            )
            for c in chunks
        )
        version.expected_chunk_count = len(chunks)
        session.flush()
        report = reconcile_version(session, ctx.index, version)
        version.indexed_chunk_count = report.chroma_chunks
        if not report.consistent:
            raise IngestionError(
                ErrorCode.INDEX_VERIFY_FAILED,
                f"expected {report.expected}, postgres {report.postgres_chunks}, "
                f"chroma {report.chroma_chunks}",
                retryable=True,
            )
        transition(version, VersionStatus.READY)
        row.state, row.finished_at, row.locked_until = JobState.DONE.value, utcnow(), None
        row.error_code = row.failed_stage = None
        job.timings["verifying"] = round((time.perf_counter() - started) * 1000)
        row.stage_timings, row.warnings = dict(job.timings), warnings


def _fail_or_retry(ctx: WorkerContext, job: ClaimedJob, code: ErrorCode, retryable: bool) -> JobState:
    try:
        with session_factory(ctx.engine)() as session, session.begin():
            row = _fenced_job(session, job)
            if retryable and row.attempts < row.max_attempts and not row.cancel_requested:
                delay = min(
                    ctx.settings.worker_retry_max_seconds,
                    ctx.settings.worker_retry_base_seconds * 2 ** (row.attempts - 1),
                )
                row.state, row.run_after, row.locked_until = (
                    JobState.QUEUED.value,
                    utcnow() + timedelta(seconds=delay),
                    None,
                )
                row.error_code, row.failed_stage = code.value, job.stage.value
                row.stage_timings = dict(job.timings)
                return JobState.QUEUED
    except LeaseLost:
        return JobState.QUEUED
    _close(ctx, job, JobState.FAILED, code)
    return JobState.FAILED


def _close(ctx: WorkerContext, job: ClaimedJob, state: JobState, code: ErrorCode | None) -> None:
    """Finish the job as failed or cancelled; the draft version becomes `failed` and its vectors go."""
    try:
        with session_factory(ctx.engine)() as session, session.begin():
            row = _fenced_job(session, job)
            row.state, row.finished_at, row.locked_until = state.value, utcnow(), None
            row.error_code = code.value if code else None
            row.failed_stage = job.stage.value if code else None
            row.stage_timings = dict(job.timings)
            set_tenant(session, job.organization_id)
            version = session.get(DocumentVersion, job.version_id, with_for_update=True)
            if version is not None and version.status == VersionStatus.PROCESSING.value:
                transition(version, VersionStatus.FAILED)
    except LeaseLost:
        return
    try:
        ctx.index.delete_version(job.organization_id, job.version_id)
    except Exception:  # reconciliation removes vectors of failed versions later
        log.warning("job %s: could not delete vectors now; reconciliation will", job.id)


def run_once(ctx: WorkerContext) -> JobState | None:
    """Claim and process at most one job. Returns its resting state, or None when nothing was claimable."""
    job = claim_next(ctx)
    if job is None:
        return None
    log.info("job %s: attempt %d for version %s", job.id, job.attempt, job.version_id)
    state = process(ctx, job)
    log.info("job %s: %s %s", job.id, state.value, job.timings)
    return state


# ---------- process entry point ----------


def default_context() -> WorkerContext:
    return WorkerContext(get_engine(), get_knowledge_index(), get_storage(), get_embedder(), get_settings())


def maintain(ctx: WorkerContext) -> None:
    """Scheduled housekeeping: reconciliation (ADR-002), then retention (ADR-019). Failures are logged and
    retried next interval; they never stop job processing."""
    from app.ingestion.reconcile import reconcile_all  # local import: reconcile imports this module
    from app.services.retention import apply_retention

    try:
        log.info("reconcile: %s", reconcile_all(ctx))
    except Exception:
        log.exception("reconciliation failed; will try again next interval")
    try:
        log.info("retention: %s", apply_retention(ctx.engine, ctx.index, ctx.storage, ctx.settings))
    except Exception:
        log.exception("retention failed; will try again next interval")


def run_forever(ctx: WorkerContext, stop: threading.Event | None = None) -> int:
    """Process jobs until `stop` is set (SIGINT/SIGTERM in `main`). Graceful (§11.3): the current job is
    finished first, and the poll sleep wakes at once. A worker killed mid-job is recovered by its lease: the
    reconciliation requeues the job once `locked_until` passes (ADR-012). Returns the jobs processed."""
    from app.services.alerts import run_alerts, webhook_notifier

    stop = stop or threading.Event()
    started_at = utcnow()
    worker_id = heartbeat.new_worker_id()
    processed = 0
    next_maintenance = next_beat = next_alerts = time.monotonic()
    notify = webhook_notifier(ctx.settings)
    log.info("worker %s started", worker_id)
    while not stop.is_set():
        now = time.monotonic()
        try:
            if now >= next_beat:
                heartbeat.beat(ctx.engine, worker_id, started_at, processed)
                next_beat = now + ctx.settings.worker_heartbeat_seconds
            if now >= next_maintenance:
                maintain(ctx)
                next_maintenance = now + ctx.settings.reconcile_interval_seconds
            if now >= next_alerts:  # §13.2 alert rules (ADR-021)
                try:
                    run_alerts(ctx.engine, ctx.settings, notify)
                except OperationalError:
                    raise
                except Exception:
                    log.exception("alert rules failed; will try again next interval")
                next_alerts = now + ctx.settings.alert_check_seconds
            claimed = run_once(ctx)
        except OperationalError:
            log.warning("database unavailable; retrying in %.0fs", ctx.settings.worker_poll_seconds * 5)
            stop.wait(ctx.settings.worker_poll_seconds * 5)
            continue
        if claimed is None:
            stop.wait(ctx.settings.worker_poll_seconds)
        else:
            processed += 1
    try:
        heartbeat.remove(ctx.engine, worker_id)
    except OperationalError:
        log.warning("could not remove the heartbeat row; it will go stale")
    log.info("worker %s stopped after %d jobs", worker_id, processed)
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="KnowledgeAI ingestion worker")
    parser.add_argument("--once", action="store_true", help="process at most one job, then exit")
    parser.add_argument("--reconcile", action="store_true", help="run reconciliation once, then exit")
    parser.add_argument("--retention", action="store_true", help="apply the retention rules once, then exit")
    args = parser.parse_args()
    configure_logging(get_settings().log_level)
    ctx = default_context()
    if args.reconcile:
        from app.ingestion.reconcile import reconcile_all

        print(reconcile_all(ctx))
    elif args.retention:
        from app.services.retention import apply_retention

        print(apply_retention(ctx.engine, ctx.index, ctx.storage, ctx.settings))
    elif args.once:
        print(run_once(ctx))
    else:
        stop = threading.Event()

        def on_signal(signum: int, _frame: object) -> None:
            log.info("signal %d: finishing the current job, then stopping", signum)
            stop.set()

        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        run_forever(ctx, stop)


if __name__ == "__main__":
    main()
