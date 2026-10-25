"""Document version lifecycle (blueprint §6.3, ADR-002, ADR-003).

Every function expects a session already scoped to the principal's tenant (`app.db.session.set_tenant`);
row-level security blocks anything else.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.permissions import Principal
from app.core.time import utcnow
from app.db.models import (
    AnswerCitation,
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentVersion,
    Role,
    VersionStatus,
)
from app.ingestion.validation import ALLOWED
from app.integrations.chroma import KnowledgeIndex
from app.services.storage import ObjectStorage

# uploaded → processing → ready → published → archived; failed can be retried; drafts can be discarded.
ALLOWED_TRANSITIONS: dict[VersionStatus, frozenset[VersionStatus]] = {
    VersionStatus.UPLOADED: frozenset({VersionStatus.PROCESSING, VersionStatus.ARCHIVED}),
    VersionStatus.PROCESSING: frozenset({VersionStatus.READY, VersionStatus.FAILED}),
    VersionStatus.FAILED: frozenset({VersionStatus.PROCESSING, VersionStatus.ARCHIVED}),
    VersionStatus.READY: frozenset({VersionStatus.PUBLISHED, VersionStatus.ARCHIVED}),
    VersionStatus.PUBLISHED: frozenset({VersionStatus.ARCHIVED}),
    VersionStatus.ARCHIVED: frozenset(),
}


class VersionError(Exception):
    """Base class; `code` maps onto the API error payload (§9.3)."""

    code = "VERSION_ERROR"


class InvalidTransition(VersionError):
    code = "VERSION_INVALID_TRANSITION"


class VersionNotIndexed(VersionError):
    code = "VERSION_NOT_FULLY_INDEXED"


class PermissionDenied(VersionError):
    code = "FORBIDDEN"


class NotFound(VersionError):
    code = "NOT_FOUND"


def transition(version: DocumentVersion, to: VersionStatus) -> None:
    current = VersionStatus(version.status)
    if to not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"cannot move a version from {current} to {to}")
    version.status = to.value


# "pdf" → "application/pdf", …
FILE_TYPES: dict[str, str] = {source: mime for source, mime in ALLOWED.values()}


def active_version_ids(
    session: Session,
    principal: Principal,
    product_model: str | None = None,
    *,
    brand: str | None = None,
    category: str | None = None,
    file_type: str | None = None,
) -> list[uuid.UUID]:
    """ADR-003: the versions that may answer this principal's questions, resolved from Postgres.

    The assistant's filters (§10.2) only narrow what the principal may already see. Product and brand keep
    general documents (no product or brand, e.g. the return policy); category and file type ("pdf", "docx",
    "txt") are strict.
    """
    query = select(Document.active_version_id).where(
        Document.organization_id == principal.organization_id,
        Document.audience.in_([a.value for a in principal.allowed_audiences]),
        Document.deleted_at.is_(None),
        Document.active_version_id.is_not(None),
    )
    if product_model:
        query = query.where(
            or_(Document.product_model.is_(None), func.lower(Document.product_model) == product_model.lower())
        )
    if brand:
        query = query.where(or_(Document.brand.is_(None), func.lower(Document.brand) == brand.lower()))
    if category:
        query = query.where(func.lower(Document.category) == category.lower())
    if file_type:
        query = query.join(DocumentVersion, DocumentVersion.id == Document.active_version_id).where(
            DocumentVersion.mime_type == FILE_TYPES.get(file_type.lower(), "")
        )
    return [v for v in session.scalars(query) if v is not None]


def find_duplicates(session: Session, organization_id: uuid.UUID, checksum: str) -> list[DocumentVersion]:
    """Existing versions with this exact file. Same document → the DB rejects it (unique constraint);
    another document → the upload UI warns before creating duplicate index work (§6.3)."""
    return list(
        session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == organization_id, DocumentVersion.checksum == checksum
            )
        )
    )


def can_view_version(principal: Principal, document: Document, version: DocumentVersion) -> bool:
    """Published versions follow the document's audience; drafts and archived versions: managers only."""
    if principal.organization_id != document.organization_id or document.deleted_at is not None:
        return False
    if version.status == VersionStatus.PUBLISHED.value:
        return document.audience in {a.value for a in principal.allowed_audiences}
    return principal.can_manage_documents


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    version_id: uuid.UUID
    expected: int | None
    postgres_chunks: int
    chroma_chunks: int

    @property
    def consistent(self) -> bool:
        return self.expected is not None and self.expected == self.postgres_chunks == self.chroma_chunks


def reconcile_version(session: Session, index: KnowledgeIndex, version: DocumentVersion) -> ReconcileReport:
    """Compare what Postgres expects with what both stores hold (ADR-002 reconcile job)."""
    postgres_chunks = session.scalar(
        select(func.count()).select_from(DocumentChunk).where(DocumentChunk.version_id == version.id)
    )
    return ReconcileReport(
        version_id=version.id,
        expected=version.expected_chunk_count,
        postgres_chunks=postgres_chunks or 0,
        chroma_chunks=index.count_version(version.organization_id, version.id),
    )


def publish_version(
    session: Session,
    index: KnowledgeIndex,
    principal: Principal,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request_id: str | None = None,
) -> DocumentVersion:
    """Make `version_id` the one version that answers questions. Atomic: one transaction, row locks held.
    Idempotent: publishing the version that is already published changes nothing and logs nothing."""
    if not principal.can_manage_documents:
        raise PermissionDenied("only managers and admins can publish")

    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None)).with_for_update()
    )
    version = session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.id == version_id, DocumentVersion.document_id == document_id)
        .with_for_update()
    )
    if document is None or version is None:
        raise NotFound("document or version not found")
    if version.id == document.active_version_id:
        return version  # already the published version: a repeated publish (double click, retry) is a no-op
    if version.status != VersionStatus.READY.value:
        raise InvalidTransition(f"only a ready version can be published (this one is {version.status})")

    report = reconcile_version(session, index, version)
    if not report.consistent or version.indexed_chunk_count != report.expected:
        raise VersionNotIndexed(
            f"expected {report.expected} chunks; Postgres has {report.postgres_chunks}, "
            f"Chroma has {report.chroma_chunks}"
        )

    previous_id = document.active_version_id
    if previous_id is not None:
        previous = session.get(DocumentVersion, previous_id, with_for_update=True)
        if previous is not None:
            transition(previous, VersionStatus.ARCHIVED)
            session.flush()  # the "one published version" index must see the archive before the publish

    transition(version, VersionStatus.PUBLISHED)
    version.published_at = utcnow()
    session.flush()
    document.active_version_id = version.id

    _audit(
        session,
        principal,
        action="document.version.published",
        target_id=version.id,
        details={
            "document_id": str(document_id),
            "previous_version_id": str(previous_id) if previous_id else None,
            "version_number": version.version_number,
        },
        request_id=request_id,
    )
    session.flush()
    return version


def soft_delete_document(
    session: Session, principal: Principal, document_id: uuid.UUID, request_id: str | None = None
) -> Document:
    """Immediately stops the document answering questions; the purge job removes data afterwards."""
    if principal.role is not Role.ADMIN:
        raise PermissionDenied("only admins can delete documents")
    document = session.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if document is None or document.deleted_at is not None:
        raise NotFound("document not found")
    document.deleted_at = utcnow()
    _audit(session, principal, "document.deleted", document.id, {"title": document.title}, request_id)
    session.flush()
    return document


def purge_document(
    session: Session, index: KnowledgeIndex, storage: ObjectStorage, document: Document, actor: Principal
) -> None:
    """Remove vectors, stored files and rows for a soft-deleted document (§6.3). Citations keep their position
    and page but lose the link and quote, since the source content is gone."""
    if document.deleted_at is None:
        raise InvalidTransition("purge only runs on soft-deleted documents")
    versions = list(
        session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document.id))
    )
    version_ids = [v.id for v in versions]
    if version_ids:
        # The quoted source text goes with the source; the citation row (position, pages) stays for audit.
        session.execute(
            update(AnswerCitation)
            .where(AnswerCitation.document_version_id.in_(version_ids))
            .values(quote=None)
        )
    for version in versions:
        index.delete_version(version.organization_id, version.id)
        storage.delete(version.storage_key)
    _audit(session, actor, "document.purged", document.id, {"versions": [str(v) for v in version_ids]}, None)
    document.active_version_id = None
    session.flush()
    session.delete(document)  # cascades to versions, chunks and jobs; citation links are nulled (SET NULL)
    session.flush()


def _audit(
    session: Session,
    principal: Principal,
    action: str,
    target_id: uuid.UUID,
    details: dict[str, Any],
    request_id: str | None,
) -> None:
    session.add(
        AuditEvent(
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            actor_label=f"user:{principal.user_id}" if principal.user_id else "system",
            action=action,
            target_type="document_version" if action.startswith("document.version") else "document",
            target_id=target_id,
            details=details,
            request_id=request_id,
        )
    )
