"""Rebuild the vector index from Postgres + stored originals (ADR-002, ADR-013; plan §7.3 "use a migration or
rebuild process" when the embedding model or dimension changes).

Usage (from backend/):

    python -m app.ingestion.rebuild                  # into CHROMA_COLLECTION, with EMBEDDING_* from settings
    python -m app.ingestion.rebuild --drop-existing  # development only: delete the collection first

Procedure for a model change (staging/production): set a new `CHROMA_COLLECTION` and the new `EMBEDDING_*`
values for this command only, run it, check its report, then deploy the API and worker with the same settings.
The old collection stays untouched until then, so switching back is a settings change.

Every ready or published version of every tenant is re-parsed, re-chunked and re-embedded. Chunk IDs are
deterministic, so chunk rows and citations stay valid when the chunker is unchanged; rows are rewritten only
for versions whose chunks differ. Each version is verified (expected = Postgres = Chroma) before moving on.
"""

import argparse
import logging
import uuid
from dataclasses import dataclass, field

import chromadb
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import AppEnv, get_settings
from app.db.models import Document, DocumentChunk, DocumentVersion, Organization, VersionStatus
from app.db.session import get_engine, session_factory, tenant_session
from app.ingestion.chunking import Chunk, chunk_segments
from app.ingestion.embeddings import embed_all, get_embedder
from app.ingestion.parsing import parse_document
from app.ingestion.worker import SOURCE_TYPES, WorkerContext
from app.integrations.chroma import ChunkMetadata, ChunkRecord, KnowledgeIndex
from app.services.storage import get_storage
from app.services.versions import reconcile_version

log = logging.getLogger("app.ingestion.rebuild")

LIVE_STATES = (VersionStatus.READY.value, VersionStatus.PUBLISHED.value)


@dataclass(slots=True)
class RebuildReport:
    versions: int = 0
    chunks: int = 0
    rows_rewritten: int = 0
    failed: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"versions={self.versions} chunks={self.chunks} rows_rewritten={self.rows_rewritten} "
            f"failed={len(self.failed)}"
        )


def rebuild(ctx: WorkerContext) -> RebuildReport:
    report = RebuildReport()
    with session_factory(ctx.engine)() as session:
        org_ids = list(session.scalars(select(Organization.id)))
    for org_id in org_ids:
        with tenant_session(org_id, ctx.engine) as session:
            rows = session.execute(
                select(
                    DocumentVersion.id,
                    Document.id,
                    Document.title,
                    Document.audience,
                    Document.product_model,
                    DocumentVersion.storage_key,
                    DocumentVersion.mime_type,
                )
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(DocumentVersion.status.in_(LIVE_STATES), Document.deleted_at.is_(None))
            ).all()
        for row in rows:
            try:
                written = _rebuild_version(ctx, org_id, LiveVersion(*row))
            except Exception as exc:  # report and continue: one bad file must not stop the rebuild
                log.exception("version %s failed to rebuild", row[0])
                report.failed.append(f"{row[0]}: {type(exc).__name__}")
                continue
            report.versions += 1
            report.chunks += written[0]
            report.rows_rewritten += written[1]
    return report


@dataclass(frozen=True, slots=True)
class LiveVersion:
    version_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    audience: str
    product_model: str | None
    storage_key: str
    mime_type: str


def _rebuild_version(ctx: WorkerContext, org_id: uuid.UUID, live: LiveVersion) -> tuple[int, int]:
    version_id, document_id, title = live.version_id, live.document_id, live.title
    audience, product_model, storage_key, mime_type = (
        live.audience,
        live.product_model,
        live.storage_key,
        live.mime_type,
    )
    source_type = SOURCE_TYPES[mime_type]
    parsed = parse_document(ctx.storage.get(storage_key), source_type)
    settings = ctx.settings
    chunks = chunk_segments(
        parsed.segments,
        settings.chunk_target_tokens,
        settings.chunk_max_tokens,
        settings.chunk_overlap_tokens,
    )
    vectors = embed_all(ctx.embedder, [c.text for c in chunks], settings.embedding_batch_size).vectors
    records = [
        ChunkRecord(
            c.index,
            c.text,
            vector,
            ChunkMetadata.model_validate(
                {
                    "organization_id": org_id,
                    "document_id": document_id,
                    "document_version_id": version_id,
                    "title": title,
                    "source_type": source_type,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "section": c.section,
                    "audience": audience,
                    "product_model": product_model,
                }
            ),
        )
        for c, vector in zip(chunks, vectors, strict=True)
    ]
    ids = ctx.index.upsert_version(org_id, version_id, records)
    ctx.index.prune_version(org_id, version_id, ids)

    with tenant_session(org_id, ctx.engine) as session:
        version = session.get_one(DocumentVersion, version_id, with_for_update=True)
        rows = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.version_id == version_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        rewritten = 0
        if [r.text_checksum for r in rows] != [c.checksum for c in chunks]:
            _rewrite_rows(session, org_id, version_id, chunks)
            rewritten = len(chunks)
        else:
            for row, chunk in zip(rows, chunks, strict=True):
                row.text = chunk.text  # rows from before ADR-028 have no text; citations keep their links
        version.expected_chunk_count = len(chunks)
        session.flush()
        verified = reconcile_version(session, ctx.index, version)
        if not verified.consistent:
            raise RuntimeError(f"verification failed: {verified}")
        version.indexed_chunk_count = verified.chroma_chunks
    return len(chunks), rewritten


def _rewrite_rows(session: Session, org_id: uuid.UUID, version_id: uuid.UUID, chunks: list[Chunk]) -> None:
    # Citations keep their text and pages; their chunk link is nulled (ON DELETE SET NULL), as on a purge.
    session.execute(delete(DocumentChunk).where(DocumentChunk.version_id == version_id))
    session.add_all(
        DocumentChunk(
            organization_id=org_id,
            version_id=version_id,
            chunk_index=c.index,
            chroma_id=f"{org_id}:{version_id}:{c.index}",
            page_start=c.page_start,
            page_end=c.page_end,
            section=c.section,
            text_checksum=c.checksum,
            token_count=c.token_count,
            text=c.text,
        )
        for c in chunks
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--drop-existing", action="store_true", help="development only: delete the collection first"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    client = chromadb.HttpClient(
        host=settings.chroma_host, port=settings.chroma_port, ssl=settings.chroma_ssl
    )
    if args.drop_existing:
        if settings.app_env not in (AppEnv.DEVELOPMENT, AppEnv.TEST):
            raise SystemExit(
                "--drop-existing is for development only; rebuild into a new CHROMA_COLLECTION instead"
            )
        if settings.chroma_collection in {c.name for c in client.list_collections()}:
            client.delete_collection(settings.chroma_collection)
            log.info("dropped collection %s", settings.chroma_collection)
    embedder = get_embedder()
    index = KnowledgeIndex(client, settings.embedding_dimension, embedder.model, settings.chroma_collection)
    ctx = WorkerContext(get_engine(), index, get_storage(), embedder, settings)
    report = rebuild(ctx)
    print(f"rebuilt {settings.chroma_collection} with {embedder.model}: {report}")
    if report.failed:
        raise SystemExit(f"{len(report.failed)} version(s) failed: {report.failed}")


if __name__ == "__main__":
    main()
