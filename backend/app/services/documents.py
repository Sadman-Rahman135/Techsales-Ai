"""Uploads and ingestion-job control for the admin document center (blueprint §7.1, §7.4).

Every function expects a session scoped to the principal's tenant. `ingestion_jobs` has no RLS (the worker
claims across tenants), so every job query here filters on `organization_id` explicitly.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.permissions import Principal
from app.core.time import utcnow
from app.db.models import (
    FINISHED_JOB_STATES,
    Audience,
    AuditEvent,
    Document,
    DocumentVersion,
    IngestionJob,
    JobState,
    VersionStatus,
)
from app.ingestion.errors import ErrorCode, IngestionError
from app.ingestion.validation import ValidatedFile
from app.integrations.chroma import KnowledgeIndex
from app.services.storage import ObjectStorage
from app.services.versions import InvalidTransition, NotFound, PermissionDenied, transition


@dataclass  # not frozen: raising an exception assigns __traceback__
class DuplicateFile(Exception):
    """The same bytes already exist in another document. The caller may confirm and upload anyway."""

    document_id: uuid.UUID
    title: str
    version_number: int


@dataclass  # not frozen, as above
class SameTitle(Exception):
    """A new document would share its title with a live one: usually an update meant as a new version."""

    document_id: uuid.UUID
    title: str
    next_version: int


@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    """Search metadata (§2.2 P1): Postgres only, so it can change without re-indexing. Product model and
    audience are copied into the index, so they are set at upload and never edited here."""

    brand: str | None = None
    department: str | None = None
    language: str = "en"
    effective_date: date | None = None

    def columns(self) -> dict[str, object]:
        return {
            "brand": self.brand,
            "department": self.department,
            "language": self.language,
            "effective_date": self.effective_date,
        }


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    version: DocumentVersion
    job: IngestionJob


def storage_key(org_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID, extension: str) -> str:
    return f"{org_id}/{document_id}/{version_id}{extension}"


def create_upload(
    session: Session,
    storage: ObjectStorage,
    settings: Settings,
    principal: Principal,
    file: ValidatedFile,
    *,
    document_id: uuid.UUID | None = None,
    title: str | None = None,
    audience: Audience = Audience.PUBLIC,
    category: str | None = None,
    product_model: str | None = None,
    tags: list[str] | None = None,
    metadata: DocumentMetadata | None = None,
    allow_duplicate: bool = False,
    allow_same_title: bool = True,
    request_id: str | None = None,
) -> UploadResult:
    """Create a document (or a new version of `document_id`), store the file and queue its ingestion job.
    The file is stored before commit; if the transaction then fails, reconciliation removes the orphan.
    Uploads through the API pass `allow_same_title=False` until the person confirms: a new document named like
    a live one is usually an update meant as its new version (M4 usability walkthrough)."""
    if not principal.can_manage_documents:
        raise PermissionDenied("only managers and admins can upload")
    org = principal.organization_id
    # Serialize this tenant's uploads so the concurrency limit can't be raced past.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"upload:{org}"})
    active = session.scalar(
        select(func.count())
        .select_from(IngestionJob)
        .where(
            IngestionJob.organization_id == org,
            IngestionJob.state.not_in([s.value for s in FINISHED_JOB_STATES]),
        )
    )
    if (active or 0) >= settings.max_active_ingestions_per_org:
        raise IngestionError(ErrorCode.TOO_MANY_ACTIVE_INGESTIONS, f"{active} active")

    if document_id is None:
        new_title = (title or _title_from_filename(file.display_filename)).strip()
        same = session.scalar(
            select(Document)
            .where(
                Document.organization_id == org,
                Document.deleted_at.is_(None),
                func.lower(Document.title) == new_title.lower(),
            )
            .order_by(Document.active_version_id.is_(None), Document.updated_at.desc())  # published first
            .limit(1)
        )
        if same is not None and not allow_same_title:
            latest = session.scalar(
                select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == same.id)
            )
            raise SameTitle(same.id, same.title, (latest or 0) + 1)
        document = Document(
            organization_id=org,
            title=new_title,
            audience=audience.value,
            category=category,
            product_model=product_model,
            tags=tags or [],
            created_by=principal.user_id,
            **(metadata or DocumentMetadata()).columns(),
        )
        session.add(document)
        session.flush()
        number = 1
    else:
        found = session.scalar(
            select(Document)
            .where(Document.id == document_id, Document.deleted_at.is_(None))
            .with_for_update()
        )
        if found is None:
            raise NotFound("document not found")
        document = found
        document.updated_at = utcnow()  # the list sorts by "updated"; a new version counts
        number = (
            session.scalar(
                select(func.max(DocumentVersion.version_number)).where(
                    DocumentVersion.document_id == document.id
                )
            )
            or 0
        ) + 1

    for existing in session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == org, DocumentVersion.checksum == file.checksum
        )
    ):
        owner = session.get(Document, existing.document_id)
        if owner is None or owner.deleted_at is not None:
            continue
        if existing.document_id == document.id:
            raise IngestionError(ErrorCode.DUPLICATE_FILE, f"same file as version {existing.version_number}")
        if not allow_duplicate:
            raise DuplicateFile(owner.id, owner.title, existing.version_number)

    version_id: uuid.UUID = session.scalar(select(func.uuidv7()))  # type: ignore[assignment]
    key = storage_key(org, document.id, version_id, file.extension)
    version = DocumentVersion(
        id=version_id,
        organization_id=org,
        document_id=document.id,
        version_number=number,
        checksum=file.checksum,
        storage_key=key,
        original_filename=file.display_filename,
        mime_type=file.mime_type,
        size_bytes=file.size_bytes,
        created_by=principal.user_id,
    )
    session.add(version)
    session.flush()
    job = IngestionJob(organization_id=org, version_id=version.id)
    session.add(job)
    session.flush()
    storage.put(key, file.data)
    _audit(
        session,
        principal,
        "document.version.uploaded",
        "document_version",
        version.id,
        {
            "document_id": str(document.id),
            "version_number": number,
            "filename": file.display_filename,
            "size_bytes": file.size_bytes,
            "checksum": file.checksum,
            "page_count": file.page_count,
        },
        request_id,
    )
    session.flush()
    return UploadResult(document, version, job)


def record_quarantine(
    session: Session, principal: Principal, file: ValidatedFile, signature: str, request_id: str | None
) -> None:
    """A blocked upload leaves only this audit record: no stored bytes, no version (§7.1 quarantine)."""
    _audit(
        session,
        principal,
        "document.upload.quarantined",
        "upload",
        None,
        {"filename": file.display_filename, "checksum": file.checksum, "signature": signature},
        request_id,
    )


def get_job(session: Session, principal: Principal, job_id: uuid.UUID, *, lock: bool = False) -> IngestionJob:
    query = select(IngestionJob).where(
        IngestionJob.id == job_id, IngestionJob.organization_id == principal.organization_id
    )
    job = session.scalar(query.with_for_update() if lock else query)
    if job is None:
        raise NotFound("ingestion job not found")
    return job


def latest_jobs(
    session: Session, organization_id: uuid.UUID, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, IngestionJob]:
    if not version_ids:
        return {}
    rows = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.organization_id == organization_id, IngestionJob.version_id.in_(version_ids))
        .order_by(IngestionJob.version_id, IngestionJob.created_at.desc(), IngestionJob.id.desc())
        .ext(distinct_on(IngestionJob.version_id))
    )
    return {job.version_id: job for job in rows}


def cancel(
    session: Session,
    index: KnowledgeIndex,
    principal: Principal,
    job_id: uuid.UUID,
    request_id: str | None = None,
) -> IngestionJob:
    """Stop a job before publication. Queued: cancelled now. Processing: the worker stops at the next stage.
    Finished with a ready (unpublished) version: the draft is discarded (archived) and its vectors removed."""
    if not principal.can_manage_documents:
        raise PermissionDenied("only managers and admins can cancel")
    job = get_job(session, principal, job_id, lock=True)
    version = session.get(DocumentVersion, job.version_id, with_for_update=True)
    if version is None:
        raise NotFound("version not found")
    state = JobState(job.state)
    if state is JobState.QUEUED:
        job.state, job.finished_at, job.cancel_requested = JobState.CANCELLED.value, utcnow(), True
        if version.status == VersionStatus.PROCESSING.value:
            transition(version, VersionStatus.FAILED)
    elif state in (JobState.CANCELLED, JobState.FAILED):
        pass  # already stopped; idempotent
    elif state is JobState.DONE:
        if version.status != VersionStatus.READY.value:
            raise InvalidTransition(
                f"a {version.status} version can't be cancelled; publish another version instead"
            )
        transition(version, VersionStatus.ARCHIVED)
        session.flush()
        index.delete_version(version.organization_id, version.id)
    else:
        job.cancel_requested = True
    _audit(
        session, principal, "ingestion.cancelled", "ingestion_job", job.id, {"state": state.value}, request_id
    )
    session.flush()
    return job


def retry(
    session: Session, principal: Principal, job_id: uuid.UUID, request_id: str | None = None
) -> IngestionJob:
    """Queue a fresh job (new attempt budget) for a failed or cancelled version."""
    if not principal.can_manage_documents:
        raise PermissionDenied("only managers and admins can retry")
    job = get_job(session, principal, job_id, lock=True)
    if job.state not in (JobState.FAILED.value, JobState.CANCELLED.value):
        raise InvalidTransition(f"only failed or cancelled jobs can be retried (this one is {job.state})")
    version = session.get(DocumentVersion, job.version_id, with_for_update=True)
    document = session.get(Document, version.document_id) if version else None
    if version is None or document is None or document.deleted_at is not None:
        raise NotFound("document not found")
    if version.status not in (VersionStatus.FAILED.value, VersionStatus.UPLOADED.value):
        raise InvalidTransition(f"a {version.status} version can't be retried")
    newer = latest_jobs(session, principal.organization_id, [version.id]).get(version.id)
    if newer is not None and newer.id != job.id:
        raise InvalidTransition("a newer job exists for this version")
    fresh = IngestionJob(organization_id=job.organization_id, version_id=version.id)
    session.add(fresh)
    _audit(
        session,
        principal,
        "ingestion.retried",
        "ingestion_job",
        job.id,
        {"previous_error": job.error_code},
        request_id,
    )
    session.flush()
    return fresh


def cancel_document_jobs(session: Session, organization_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """On delete: ask every unfinished job of the document to stop."""
    version_ids = select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
    for job in session.scalars(
        select(IngestionJob)
        .where(
            IngestionJob.organization_id == organization_id,
            IngestionJob.version_id.in_(version_ids),
            IngestionJob.state.not_in([s.value for s in FINISHED_JOB_STATES]),
        )
        .with_for_update()
    ):
        job.cancel_requested = True
        if job.state == JobState.QUEUED.value:
            job.state, job.finished_at = JobState.CANCELLED.value, utcnow()


MAX_TAGS = 10
MAX_TAG_LENGTH = 40


def normalize_tags(raw: str | None) -> list[str]:
    """ "Returns, Warranty , returns" → ["returns", "warranty"]: trimmed, lower-case, unique, in order."""
    tags: list[str] = []
    for part in (raw or "").split(","):
        tag = " ".join(part.split()).lower()
        if tag and tag not in tags:
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    if any(len(t) > MAX_TAG_LENGTH for t in tags):
        raise ValueError(f"each tag is at most {MAX_TAG_LENGTH} characters")
    return tags


EDITABLE = ("category", "tags", "brand", "department", "language", "effective_date")


def update_metadata(
    session: Session,
    principal: Principal,
    document_id: uuid.UUID,
    changes: dict[str, object],
    request_id: str | None = None,
) -> Document:
    """Change a document's search metadata (managers and admins). Audited with the old and new values."""
    if not principal.can_manage_documents:
        raise PermissionDenied("only managers and admins can edit documents")
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise ValueError(f"not editable: {', '.join(sorted(unknown))}")
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None)).with_for_update()
    )
    if document is None:
        raise NotFound("document not found")
    before = {k: getattr(document, k) for k in changes}
    for key, value in changes.items():
        setattr(document, key, value)
    document.updated_at = utcnow()
    changed: dict[str, object] = {
        k: [_jsonable(before[k]), _jsonable(v)] for k, v in changes.items() if before[k] != v
    }
    if changed:
        _audit(session, principal, "document.metadata_updated", "document", document.id, changed, request_id)
    session.flush()
    return document


def _jsonable(value: object) -> object:
    return value.isoformat() if isinstance(value, date) else value


def _title_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return " ".join(stem.replace("_", " ").replace("-", " ").split()) or "Untitled document"


def _audit(
    session: Session,
    principal: Principal,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    details: dict[str, object],
    request_id: str | None,
) -> None:
    session.add(
        AuditEvent(
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            actor_label=f"user:{principal.user_id}" if principal.user_id else "system",
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            request_id=request_id,
        )
    )
