"""Scheduled reconciliation (blueprint §6.3, §7.4, ADR-002). Postgres is the truth; storage and Chroma are
brought back in line with it. Runs inside the worker loop every `RECONCILE_INTERVAL_SECONDS`, or once with
`python -m app.ingestion.worker --reconcile`.

1. Incomplete jobs: a processing job whose lease expired (worker crashed or was killed) is requeued, or failed
   when it has no attempts left.
2. Orphaned files: stored originals that no version references, older than `ORPHAN_GRACE_SECONDS` (so an
   upload that is committing right now is never touched).
3. Orphaned chunks: vectors whose version no longer exists, failed, was archived, or whose document was
   deleted. Re-creatable from the stored original, so removing them is always safe.
4. Count drift: ready/published versions whose Postgres and Chroma counts disagree are reported (and logged).
   A drifted ready version cannot be published (publish re-verifies); a published one needs a re-index.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.time import utcnow
from app.db.models import (
    ACTIVE_JOB_STATES,
    Document,
    DocumentVersion,
    IngestionJob,
    JobState,
    Organization,
    VersionStatus,
)
from app.db.session import session_factory, set_tenant, tenant_session
from app.ingestion.errors import ErrorCode
from app.ingestion.worker import WorkerContext
from app.services.versions import ReconcileReport, reconcile_version, transition

log = logging.getLogger("app.ingestion.reconcile")

DEAD_VERSION_STATES = frozenset({VersionStatus.FAILED.value, VersionStatus.ARCHIVED.value})


@dataclass(slots=True)
class ReconcileSummary:
    requeued_jobs: int = 0
    failed_jobs: int = 0
    orphan_files_deleted: int = 0
    orphan_vector_versions_deleted: int = 0
    drift: list[ReconcileReport] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"requeued={self.requeued_jobs} failed={self.failed_jobs} files={self.orphan_files_deleted} "
            f"vector_versions={self.orphan_vector_versions_deleted} drift={len(self.drift)}"
        )


def reconcile_all(ctx: WorkerContext, now: datetime | None = None) -> ReconcileSummary:
    now = now or utcnow()
    summary = ReconcileSummary()
    _recover_stale_jobs(ctx, now, summary)
    with session_factory(ctx.engine)() as session:
        org_ids = list(session.scalars(select(Organization.id)))
    for org_id in org_ids:
        _reconcile_tenant(ctx, org_id, now, summary)
    for report in summary.drift:
        log.warning("count drift: %s", report)
    return summary


def _recover_stale_jobs(ctx: WorkerContext, now: datetime, summary: ReconcileSummary) -> None:
    with session_factory(ctx.engine)() as session, session.begin():
        stale = session.scalars(
            select(IngestionJob)
            .where(
                IngestionJob.state.in_([s.value for s in ACTIVE_JOB_STATES]),
                IngestionJob.locked_until < now,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.error_code, job.failed_stage, job.locked_until = (
                ErrorCode.LEASE_EXPIRED.value,
                job.state,
                None,
            )
            if job.attempts < job.max_attempts and not job.cancel_requested:
                job.state, job.run_after = JobState.QUEUED.value, now
                summary.requeued_jobs += 1
                continue
            job.state = (JobState.CANCELLED if job.cancel_requested else JobState.FAILED).value
            job.finished_at = now
            set_tenant(session, job.organization_id)
            version = session.get(DocumentVersion, job.version_id, with_for_update=True)
            if version is not None and version.status == VersionStatus.PROCESSING.value:
                transition(version, VersionStatus.FAILED)
            summary.failed_jobs += 1


def _reconcile_tenant(
    ctx: WorkerContext, org_id: uuid.UUID, now: datetime, summary: ReconcileSummary
) -> None:
    with tenant_session(org_id, ctx.engine) as session:
        rows = session.execute(
            select(DocumentVersion, Document.deleted_at).join(
                Document, Document.id == DocumentVersion.document_id
            )
        ).all()
        versions = {v.id: (v, deleted_at) for v, deleted_at in rows}

        referenced = {v.storage_key for v, _ in versions.values()}
        cutoff = now - timedelta(seconds=ctx.settings.orphan_grace_seconds)
        for obj in ctx.storage.list(f"{org_id}/"):
            if obj.key not in referenced and obj.modified_at < cutoff:
                ctx.storage.delete(obj.key)
                summary.orphan_files_deleted += 1

        for version_id in ctx.index.indexed_version_ids(org_id):
            entry = versions.get(version_id)
            if entry is None or entry[1] is not None or entry[0].status in DEAD_VERSION_STATES:
                ctx.index.delete_version(org_id, version_id)
                summary.orphan_vector_versions_deleted += 1

        for version, deleted_at in versions.values():
            if deleted_at is None and version.status in (
                VersionStatus.READY.value,
                VersionStatus.PUBLISHED.value,
            ):
                report = reconcile_version(session, ctx.index, version)
                if not report.consistent:
                    summary.drift.append(report)
