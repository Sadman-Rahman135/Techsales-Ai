"""Retention (blueprint §11.2 "define conversation/document retention and deletion", ADR-019).

Runs in the worker's maintenance cycle, or once with `python -m app.ingestion.worker --retention`.

- Soft-deleted documents are purged DELETED_DOCUMENT_PURGE_DAYS after deletion: vectors (Chroma), the stored
  originals (object storage) and every row (Postgres). Citations keep position and pages but lose the quote.
- Conversations with no activity for CONVERSATION_RETENTION_DAYS are deleted with their messages, answers,
  citations and feedback (database cascades). Nothing about a conversation lives in Chroma or storage.
- Audit events are kept (append-only); each purge or bulk deletion is itself recorded there.
- Traces live on the answer rows (§13.2), so they go with their conversation. Request metrics and resolved
  alerts are deleted after METRICS_RETENTION_DAYS. Logs belong to the log platform and follow its retention.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, exists, select

from app.config import Settings
from app.core.permissions import Principal
from app.core.time import utcnow
from app.db.models import AlertEvent, AuditEvent, Conversation, Document, Message, Organization, RequestMetric
from app.db.session import session_factory, tenant_session
from app.integrations.chroma import KnowledgeIndex
from app.services.storage import ObjectStorage
from app.services.versions import purge_document

log = logging.getLogger("app.services.retention")


@dataclass(slots=True)
class RetentionSummary:
    documents_purged: int = 0
    conversations_deleted: int = 0
    metric_rows_deleted: int = 0
    failures: int = 0

    def __str__(self) -> str:
        return (
            f"documents_purged={self.documents_purged} conversations_deleted={self.conversations_deleted} "
            f"metric_rows_deleted={self.metric_rows_deleted} failures={self.failures}"
        )


def apply_retention(
    engine: Engine,
    index: KnowledgeIndex,
    storage: ObjectStorage,
    settings: Settings,
    now: datetime | None = None,
) -> RetentionSummary:
    now = now or utcnow()
    summary = RetentionSummary()
    with session_factory(engine)() as session:
        org_ids = list(session.scalars(select(Organization.id)))
    for org_id in org_ids:
        _purge_documents(
            engine,
            index,
            storage,
            org_id,
            now - timedelta(days=settings.deleted_document_purge_days),
            summary,
        )
        _expire_conversations(
            engine, org_id, now - timedelta(days=settings.conversation_retention_days), summary
        )
    _expire_metrics(engine, now - timedelta(days=settings.metrics_retention_days), summary)
    return summary


def _expire_metrics(engine: Engine, cutoff: datetime, summary: RetentionSummary) -> None:
    with session_factory(engine)() as session, session.begin():
        metrics = session.execute(
            delete(RequestMetric).where(RequestMetric.bucket_start < cutoff).returning(RequestMetric.route)
        ).all()
        alerts = session.execute(
            delete(AlertEvent)
            .where(AlertEvent.resolved_at.is_not(None), AlertEvent.resolved_at < cutoff)
            .returning(AlertEvent.id)
        ).all()
    summary.metric_rows_deleted += len(metrics) + len(alerts)


def _purge_documents(
    engine: Engine,
    index: KnowledgeIndex,
    storage: ObjectStorage,
    org_id: uuid.UUID,
    cutoff: datetime,
    summary: RetentionSummary,
) -> None:
    with tenant_session(org_id, engine) as session:
        due = list(
            session.scalars(
                select(Document.id).where(Document.deleted_at.is_not(None), Document.deleted_at <= cutoff)
            )
        )
    for document_id in due:  # one transaction each: a failure leaves the others purged
        try:
            with tenant_session(org_id, engine) as session:
                document = session.get(Document, document_id, with_for_update=True)
                if document is not None:
                    purge_document(session, index, storage, document, Principal(organization_id=org_id))
                    summary.documents_purged += 1
        except Exception:
            summary.failures += 1
            log.exception("purge of document %s failed; it will be retried next cycle", document_id)


def _expire_conversations(
    engine: Engine, org_id: uuid.UUID, cutoff: datetime, summary: RetentionSummary
) -> None:
    recent_message = exists().where(Message.conversation_id == Conversation.id, Message.created_at >= cutoff)
    with tenant_session(org_id, engine) as session:
        deleted = session.execute(
            delete(Conversation)
            .where(Conversation.updated_at < cutoff, ~recent_message)
            .returning(Conversation.id)
        ).all()
        if deleted:
            session.add(
                AuditEvent(
                    organization_id=org_id,
                    actor_label="system:retention",
                    action="retention.conversations_deleted",
                    target_type="conversation",
                    details={"count": len(deleted), "inactive_before": cutoff.isoformat()},
                )
            )
    summary.conversations_deleted += len(deleted)
