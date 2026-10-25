"""Admin document center API (blueprint §7.1, §9): upload, list, detail, publish, delete, job control.

Uploads return `202 Accepted` with the job ID at once; the worker does the rest. Uploads and job control are
for managers and admins. Listing is role-filtered: other staff see only published documents for their
audiences, and never drafts or processing details.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import (
    CallerDep,
    EngineDep,
    IndexDep,
    ManagerDep,
    PrincipalDep,
    ScannerDep,
    SettingsDep,
    StorageDep,
)
from app.api.errors import COMMON_ERRORS, ApiError, request_id
from app.config import Settings
from app.core import links
from app.core.permissions import Principal
from app.db.models import (
    FINISHED_JOB_STATES,
    Audience,
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    JobState,
    User,
)
from app.db.session import tenant_session
from app.ingestion.errors import ErrorCode, IngestionError, message_for
from app.ingestion.scanning import ScannerUnavailable
from app.ingestion.validation import ValidatedFile, validate_upload
from app.services import documents as docs
from app.services import versions

router = APIRouter(prefix="/api/v1", tags=["documents"], responses=COMMON_ERRORS)

DocumentStatus = Literal["processing", "failed", "cancelled", "ready", "published", "archived", "draft"]
UploadStage = Literal["uploaded", "parsing", "chunking", "embedding", "indexing", "ready"]
MULTIPART_OVERHEAD = 64 * 1024

_UPLOAD_STATUS = {
    ErrorCode.FILE_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    ErrorCode.TOO_MANY_ACTIVE_INGESTIONS: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.DUPLICATE_FILE: status.HTTP_409_CONFLICT,
    ErrorCode.SCANNER_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


# ---------- response models ----------


class JobOut(BaseModel):
    id: uuid.UUID
    version_id: uuid.UUID
    state: JobState
    stage: UploadStage = Field(description="Furthest step reached, for the upload stepper")
    attempts: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    failed_stage: str | None
    cancel_requested: bool
    warnings: list[dict[str, Any]]
    stage_timings: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    run_after: datetime


class VersionOut(BaseModel):
    id: uuid.UUID
    version_number: int
    status: str
    original_filename: str
    mime_type: str
    size_bytes: int
    expected_chunk_count: int | None
    indexed_chunk_count: int
    created_at: datetime
    published_at: datetime | None
    job: JobOut | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    audience: Audience
    category: str | None
    product_model: str | None
    tags: list[str]
    brand: str | None = None
    department: str | None = None
    language: str = "en"
    effective_date: date | None = None
    owner: str | None = Field(description="Who created it (managers and admins only; None for others)")
    status: DocumentStatus
    active_version_number: int | None
    latest_version: VersionOut | None
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    versions: list[VersionOut]


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class UploadAccepted(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    job: JobOut


class PublishIn(BaseModel):
    version_id: uuid.UUID


# ---------- view helpers ----------


def job_out(job: IngestionJob) -> JobOut:
    return JobOut(
        id=job.id,
        version_id=job.version_id,
        state=JobState(job.state),
        stage=_stage(job),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error_code=job.error_code,
        error_message=message_for(job.error_code),
        failed_stage=job.failed_stage,
        cancel_requested=job.cancel_requested,
        warnings=job.warnings,
        stage_timings=job.stage_timings,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        run_after=job.run_after,
    )


def _stage(job: IngestionJob) -> UploadStage:
    state = JobState(job.state)
    if state is JobState.DONE:
        return "ready"
    if state in (JobState.QUEUED, JobState.FAILED, JobState.CANCELLED):
        stage = (
            job.failed_stage if job.failed_stage in ("parsing", "chunking", "embedding", "indexing") else None
        )
        return stage or "uploaded"  # type: ignore[return-value]
    return state.value  # type: ignore[return-value]


def version_out(version: DocumentVersion, job: IngestionJob | None, manager: bool) -> VersionOut:
    return VersionOut(
        id=version.id,
        version_number=version.version_number,
        status=version.status,
        original_filename=version.original_filename,
        mime_type=version.mime_type,
        size_bytes=version.size_bytes,
        expected_chunk_count=version.expected_chunk_count,
        indexed_chunk_count=version.indexed_chunk_count,
        created_at=version.created_at,
        published_at=version.published_at,
        job=job_out(job) if job is not None and manager else None,
    )


def _status(document: Document, latest: DocumentVersion | None, job: IngestionJob | None) -> DocumentStatus:
    if job is not None and job.state not in [s.value for s in FINISHED_JOB_STATES]:
        return "processing"
    if latest is not None and latest.id != document.active_version_id:
        if job is not None and job.state == JobState.CANCELLED.value:
            return "cancelled"
        if latest.status == "failed":
            return "failed"
        if latest.status == "ready":
            return "ready"
    if document.active_version_id is not None:
        return "published"
    if latest is not None and latest.status == "archived":
        return "archived"
    return "draft"


def _document_views(
    session: Session, principal: Principal, documents: list[Document], with_versions: bool = False
) -> list[DocumentDetailOut]:
    manager = principal.can_manage_documents
    ids = [d.id for d in documents]
    query = select(DocumentVersion).where(DocumentVersion.document_id.in_(ids))
    if not manager:
        query = query.where(
            DocumentVersion.id.in_([d.active_version_id for d in documents if d.active_version_id])
        )
    all_versions = list(session.scalars(query.order_by(DocumentVersion.version_number.desc())))
    jobs = (
        docs.latest_jobs(session, principal.organization_id, [v.id for v in all_versions]) if manager else {}
    )
    owners = _user_labels(session, [d.created_by for d in documents if d.created_by]) if manager else {}

    views = []
    for document in documents:
        own = [v for v in all_versions if v.document_id == document.id]
        latest = own[0] if own else None
        active = next((v for v in own if v.id == document.active_version_id), None)
        latest_job = jobs.get(latest.id) if latest else None
        views.append(
            DocumentDetailOut(
                id=document.id,
                title=document.title,
                audience=Audience(document.audience),
                category=document.category,
                product_model=document.product_model,
                tags=document.tags,
                brand=document.brand,
                department=document.department,
                language=document.language,
                effective_date=document.effective_date,
                owner=owners.get(document.created_by) if document.created_by else None,
                status=_status(document, latest, latest_job) if manager else "published",
                active_version_number=active.version_number if active else None,
                latest_version=version_out(latest, latest_job, manager) if latest else None,
                created_at=document.created_at,
                updated_at=document.updated_at,
                versions=[version_out(v, jobs.get(v.id), manager) for v in own] if with_versions else [],
            )
        )
    return views


def _user_labels(session: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Display name, else email, for each user (users are global rows without RLS)."""
    if not user_ids:
        return {}
    rows = session.execute(
        select(User.id, User.display_name, User.email).where(User.id.in_(set(user_ids)))
    ).all()
    return {r.id: r.display_name or r.email for r in rows}


def _visible_document(session: Session, principal: Principal, document_id: uuid.UUID) -> Document:
    query = select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    if not principal.can_manage_documents:
        query = query.where(
            Document.active_version_id.is_not(None),
            Document.audience.in_([a.value for a in principal.allowed_audiences]),
        )
    document = session.scalar(query)
    if document is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Document not found.")
    return document


# ---------- upload ----------


def _read_upload(request: Request, file: UploadFile, settings: Settings) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes + MULTIPART_OVERHEAD:
        raise _upload_error(request, IngestionError(ErrorCode.FILE_TOO_LARGE))
    data = file.file.read(settings.max_upload_bytes + 1)  # never read more than one byte past the limit
    return data


def _upload_error(request: Request, exc: IngestionError) -> ApiError:
    code = _UPLOAD_STATUS.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT)
    return ApiError(code, exc.code.value, message_for(exc.code) or "", None)


def _tags(raw: str | None) -> list[str]:
    try:
        return docs.normalize_tags(raw)
    except ValueError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "VALIDATION_ERROR",
            "Some fields are missing or invalid.",
            {"fields": [{"field": "tags", "message": str(exc)}]},
        ) from None


def _accept(
    request: Request,
    principal: Principal,
    settings: Settings,
    engine: EngineDep,
    storage: StorageDep,
    scanner: ScannerDep,
    upload: UploadFile,
    allow_duplicate: bool,
    **fields: Any,
) -> UploadAccepted:
    try:
        validated: ValidatedFile = validate_upload(
            upload.filename, upload.content_type, _read_upload(request, upload, settings), settings
        )
        try:
            scan = scanner.scan(validated.data)
        except ScannerUnavailable as exc:
            raise IngestionError(ErrorCode.SCANNER_UNAVAILABLE, str(exc)) from exc
        if not scan.clean:
            with tenant_session(principal.organization_id, engine) as session:
                docs.record_quarantine(
                    session, principal, validated, scan.signature or "unknown", request_id(request)
                )
            raise IngestionError(ErrorCode.FILE_MALWARE_DETECTED, scan.signature or "")
        with tenant_session(principal.organization_id, engine) as session:
            result = docs.create_upload(
                session,
                storage,
                settings,
                principal,
                validated,
                allow_duplicate=allow_duplicate,
                request_id=request_id(request),
                **fields,
            )
            return UploadAccepted(
                document_id=result.document.id,
                version_id=result.version.id,
                version_number=result.version.version_number,
                job=job_out(result.job),
            )
    except IngestionError as exc:
        raise _upload_error(request, exc) from None
    except docs.DuplicateFile as dup:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DUPLICATE_FILE",
            f'This exact file is already uploaded as "{dup.title}" (version {dup.version_number}).',
            {"document_id": str(dup.document_id), "title": dup.title, "version_number": dup.version_number},
        ) from None
    except docs.SameTitle as same:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "DOCUMENT_TITLE_EXISTS",
            f'A document called "{same.title}" already exists. Upload this file as version '
            f"{same.next_version} of it, or keep it as a separate document.",
            {"document_id": str(same.document_id), "title": same.title, "next_version": same.next_version},
        ) from None


Title = Annotated[str | None, Form(max_length=200)]
Optional = Annotated[str | None, Form(max_length=120)]
Language = Annotated[str, Form(pattern=r"^[a-z]{2}$", description="ISO 639-1 code")]


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    request: Request,
    principal: ManagerDep,
    settings: SettingsDep,
    engine: EngineDep,
    storage: StorageDep,
    scanner: ScannerDep,
    file: Annotated[UploadFile, File(description="PDF, DOCX or TXT")],
    audience: Annotated[Audience, Form()],
    title: Title = None,
    category: Optional = None,
    product_model: Optional = None,
    tags: Annotated[str | None, Form(max_length=500, description="Comma-separated, at most 10")] = None,
    brand: Optional = None,
    department: Optional = None,
    language: Language = "en",
    effective_date: Annotated[date | None, Form()] = None,
    allow_duplicate: Annotated[bool, Form()] = False,
    allow_same_title: Annotated[
        bool, Form(description="Confirms a new document even though a live one has the same title")
    ] = False,
) -> UploadAccepted:
    tag_list = _tags(tags)
    return _accept(
        request,
        principal,
        settings,
        engine,
        storage,
        scanner,
        file,
        allow_duplicate,
        title=title,
        audience=audience,
        category=(category or "").strip() or None,
        product_model=(product_model or "").strip() or None,
        tags=tag_list,
        metadata=docs.DocumentMetadata(
            brand=(brand or "").strip() or None,
            department=(department or "").strip() or None,
            language=language,
            effective_date=effective_date,
        ),
        allow_same_title=allow_same_title,
    )


class MetadataIn(BaseModel):
    """Fields a manager may change after upload (product model and audience are set at upload, ADR-028)."""

    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(default=None, max_length=120)
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] | None = Field(default=None, max_length=10)
    brand: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    effective_date: date | None = None


@router.patch("/documents/{document_id}")
def update_document(
    document_id: uuid.UUID, body: MetadataIn, request: Request, principal: ManagerDep, engine: EngineDep
) -> DocumentOut:
    """Change search metadata. Only the fields sent change; send null to clear one (not language)."""
    changes: dict[str, object] = {}
    for key in body.model_fields_set:
        value = getattr(body, key)
        if isinstance(value, str):
            value = value.strip() or None
        if key == "tags":
            value = sorted({tag.strip().lower() for tag in value or [] if tag.strip()})
        if key == "language" and value is None:
            continue  # every document has a language
        changes[key] = value
    try:
        with tenant_session(principal.organization_id, engine) as session:
            document = docs.update_metadata(session, principal, document_id, changes, request_id(request))
            view = _document_views(session, principal, [document])[0]
            return DocumentOut.model_validate(view.model_dump())
    except versions.NotFound:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Document not found.") from None


@router.post("/documents/{document_id}/versions", status_code=status.HTTP_202_ACCEPTED)
def upload_version(
    document_id: uuid.UUID,
    request: Request,
    principal: ManagerDep,
    settings: SettingsDep,
    engine: EngineDep,
    storage: StorageDep,
    scanner: ScannerDep,
    file: Annotated[UploadFile, File(description="PDF, DOCX or TXT")],
    allow_duplicate: Annotated[bool, Form()] = False,
) -> UploadAccepted:
    return _accept(
        request, principal, settings, engine, storage, scanner, file, allow_duplicate, document_id=document_id
    )


# ---------- read ----------


@router.get("/documents")
def list_documents(
    principal: PrincipalDep,
    engine: EngineDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    audience: Audience | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    brand: Annotated[str | None, Query(max_length=120)] = None,
    category: Annotated[str | None, Query(max_length=120)] = None,
    department: Annotated[str | None, Query(max_length=120)] = None,
    language: Annotated[str | None, Query(pattern=r"^[a-z]{2}$")] = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    sort: Literal["updated", "-updated", "title", "-title"] = "-updated",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    with tenant_session(principal.organization_id, engine) as session:
        query = select(Document).where(Document.deleted_at.is_(None))
        if not principal.can_manage_documents:
            query = query.where(
                Document.active_version_id.is_not(None),
                Document.audience.in_([a.value for a in principal.allowed_audiences]),
            )
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(
                or_(
                    Document.title.ilike(pattern, escape="\\"),
                    Document.product_model.ilike(pattern, escape="\\"),
                    Document.brand.ilike(pattern, escape="\\"),
                    Document.category.ilike(pattern, escape="\\"),
                    Document.department.ilike(pattern, escape="\\"),
                )
            )
        if audience is not None:
            query = query.where(Document.audience == audience.value)
        if tag:
            query = query.where(Document.tags.contains([tag.strip().lower()]))
        for field, value in (
            (Document.brand, brand),
            (Document.category, category),
            (Document.department, department),
        ):
            if value:
                query = query.where(func.lower(field) == value.strip().lower())
        if language:
            query = query.where(Document.language == language)
        if effective_from:
            query = query.where(Document.effective_date >= effective_from)
        if effective_to:
            query = query.where(Document.effective_date <= effective_to)
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        column = Document.title if sort.lstrip("-") == "title" else Document.updated_at
        order = column.desc() if sort.startswith("-") else column.asc()
        page = list(session.scalars(query.order_by(order, Document.id).limit(limit).offset(offset)))
        items = [
            DocumentOut.model_validate(v.model_dump()) for v in _document_views(session, principal, page)
        ]
        return DocumentPage(items=items, total=total, limit=limit, offset=offset)


class FacetsOut(BaseModel):
    product_models: list[str]
    tags: list[str]
    brands: list[str] = []
    categories: list[str] = []
    departments: list[str] = []
    languages: list[str] = []


@router.get("/documents/facets")
def document_facets(principal: PrincipalDep, engine: EngineDep) -> FacetsOut:
    """Filter values for the employee product filter and the admin tag filter, from documents the caller can
    see (staff: published ones in their audiences).
    """
    with tenant_session(principal.organization_id, engine) as session:
        query = select(
            Document.product_model,
            Document.tags,
            Document.brand,
            Document.category,
            Document.department,
            Document.language,
        ).where(Document.deleted_at.is_(None))
        if not principal.can_manage_documents:
            query = query.where(
                Document.active_version_id.is_not(None),
                Document.audience.in_([a.value for a in principal.allowed_audiences]),
            )
        rows = session.execute(query).all()
    models = sorted({r.product_model for r in rows if r.product_model}, key=str.lower)
    tags = sorted({t for r in rows for t in r.tags}) if principal.can_manage_documents else []

    def values(name: str) -> list[str]:
        return sorted({v for r in rows if (v := getattr(r, name))}, key=str.lower)

    return FacetsOut(
        product_models=models,
        tags=tags,
        brands=values("brand"),
        categories=values("category"),
        departments=values("department"),
        languages=values("language"),
    )


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, principal: PrincipalDep, engine: EngineDep) -> DocumentDetailOut:
    with tenant_session(principal.organization_id, engine) as session:
        document = _visible_document(session, principal, document_id)
        return _document_views(session, principal, [document], with_versions=True)[0]


@router.get("/ingestion-jobs/{job_id}")
def get_ingestion_job(job_id: uuid.UUID, principal: ManagerDep, engine: EngineDep) -> JobOut:
    with tenant_session(principal.organization_id, engine) as session:
        return job_out(docs.get_job(session, principal, job_id))


# ---------- write ----------


@router.post("/documents/{document_id}/publish")
def publish(
    document_id: uuid.UUID,
    body: PublishIn,
    request: Request,
    principal: ManagerDep,
    engine: EngineDep,
    index: IndexDep,
) -> DocumentDetailOut:
    with tenant_session(principal.organization_id, engine) as session:
        versions.publish_version(session, index, principal, document_id, body.version_id, request_id(request))
        document = _visible_document(session, principal, document_id)
        return _document_views(session, principal, [document], with_versions=True)[0]


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID, request: Request, principal: PrincipalDep, engine: EngineDep
) -> Response:
    with tenant_session(principal.organization_id, engine) as session:
        versions.soft_delete_document(session, principal, document_id, request_id(request))
        docs.cancel_document_jobs(session, principal.organization_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/ingestion-jobs/{job_id}/cancel")
def cancel_job(
    job_id: uuid.UUID, request: Request, principal: ManagerDep, engine: EngineDep, index: IndexDep
) -> JobOut:
    with tenant_session(principal.organization_id, engine) as session:
        return job_out(docs.cancel(session, index, principal, job_id, request_id(request)))


@router.post("/ingestion-jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: uuid.UUID, request: Request, principal: ManagerDep, engine: EngineDep) -> JobOut:
    with tenant_session(principal.organization_id, engine) as session:
        fresh = docs.retry(session, principal, job_id, request_id(request))
        session.flush()
        session.refresh(fresh)
        return job_out(fresh)


# ---------- diagnostics and activity (§10.3 document detail) ----------


class ChunkOut(BaseModel):
    chunk_index: int
    chroma_id: str
    page_start: int
    page_end: int
    section: str | None
    token_count: int | None
    text_checksum: str
    in_index: bool = Field(description="The vector store holds this record")
    preview: str | None = Field(description="First 240 characters of the indexed text")


class ChunkReport(BaseModel):
    version_id: uuid.UUID
    version_number: int
    status: str
    expected: int | None
    postgres_chunks: int
    chroma_chunks: int
    consistent: bool
    chunks: list[ChunkOut]


PREVIEW_CHARS = 240


@router.get("/documents/{document_id}/versions/{version_id}/chunks")
def version_chunks(
    document_id: uuid.UUID, version_id: uuid.UUID, principal: ManagerDep, engine: EngineDep, index: IndexDep
) -> ChunkReport:
    """What Postgres and the vector store each hold for one version: the reconciliation view per chunk."""
    with tenant_session(principal.organization_id, engine) as session:
        document = _visible_document(session, principal, document_id)
        version = session.get(DocumentVersion, version_id)
        if version is None or version.document_id != document.id:
            raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Version not found.")
        rows = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.version_id == version.id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
    indexed = index.version_chunks(principal, version.id)
    chunks = [
        ChunkOut(
            chunk_index=c.chunk_index,
            chroma_id=c.chroma_id,
            page_start=c.page_start,
            page_end=c.page_end,
            section=c.section,
            token_count=c.token_count,
            text_checksum=c.text_checksum,
            in_index=c.chroma_id in indexed,
            preview=indexed[c.chroma_id][:PREVIEW_CHARS] if c.chroma_id in indexed else None,
        )
        for c in rows
    ]
    return ChunkReport(
        version_id=version.id,
        version_number=version.version_number,
        status=version.status,
        expected=version.expected_chunk_count,
        postgres_chunks=len(rows),
        chroma_chunks=len(indexed),
        consistent=version.expected_chunk_count == len(rows) == len(indexed)
        and all(c.in_index for c in chunks),
        chunks=chunks,
    )


class ActivityOut(BaseModel):
    id: uuid.UUID
    action: str
    actor: str
    target_type: str
    target_id: uuid.UUID | None
    details: dict[str, Any]
    request_id: str | None
    created_at: datetime


@router.get("/documents/{document_id}/activity")
def document_activity(
    document_id: uuid.UUID,
    principal: ManagerDep,
    engine: EngineDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ActivityOut]:
    """Audit events for this document, its versions and their ingestion jobs, newest first (§11.1)."""
    with tenant_session(principal.organization_id, engine) as session:
        document = _visible_document(session, principal, document_id)
        version_ids = list(
            session.scalars(select(DocumentVersion.id).where(DocumentVersion.document_id == document.id))
        )
        job_ids = list(
            session.scalars(select(IngestionJob.id).where(IngestionJob.version_id.in_(version_ids)))
        )
        events = list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.target_id.in_([document.id, *version_ids, *job_ids]))
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .limit(limit)
            )
        )
        actors = _user_labels(session, [e.actor_user_id for e in events if e.actor_user_id])
    return [
        ActivityOut(
            id=e.id,
            action=e.action,
            actor=actors.get(e.actor_user_id, "Unknown user") if e.actor_user_id else "System",
            target_type=e.target_type,
            target_id=e.target_id,
            details=e.details,
            request_id=e.request_id,
            created_at=e.created_at,
        )
        for e in events
    ]


# ---------- preview (§9 `/documents/{id}/preview`, §9.1 `preview_url`, §11.1 short-lived links) ----------


class PreviewOut(BaseModel):
    url: str = Field(
        description="Short-lived link to the original file; append nothing, it is single-purpose"
    )
    expires_at: datetime
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    title: str
    filename: str
    mime_type: str
    page: int | None
    is_active_version: bool


def _page_count(session: Session, version_id: uuid.UUID) -> int | None:
    return session.scalar(
        select(func.max(DocumentChunk.page_end)).where(DocumentChunk.version_id == version_id)
    )


@router.get("/documents/{document_id}/preview")
def preview(
    document_id: uuid.UUID,
    caller: CallerDep,
    settings: SettingsDep,
    engine: EngineDep,
    version_id: uuid.UUID | None = None,
    page: Annotated[int | None, Query(ge=1)] = None,
) -> PreviewOut:
    """A link to the original file of a version the caller may see: published versions follow the document's
    audience (customers: public only); drafts and archived versions are for managers and admins. Anything else
    is a 404, the same as a missing document, so IDs can't be probed.
    """
    principal = caller.principal
    with tenant_session(principal.organization_id, engine) as session:
        document = session.scalar(
            select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
        )
        target = version_id or (document.active_version_id if document else None)
        version = session.get(DocumentVersion, target) if target else None
        if (
            document is None
            or version is None
            or version.document_id != document.id
            or not versions.can_view_version(principal, document, version)
        ):
            raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Document not found.")
        pages = _page_count(session, version.id)
        if page is not None and pages is not None and page > pages:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "PAGE_OUT_OF_RANGE",
                f"This version has {pages} page(s).",
                {"pages": pages},
            )
        token, expires_at = links.sign(settings, principal.organization_id, version.id)
        fragment = f"#page={page}" if page and version.mime_type == "application/pdf" else ""
        return PreviewOut(
            url=f"/api/v1/files/{token}{fragment}",
            expires_at=expires_at,
            document_id=document.id,
            version_id=version.id,
            version_number=version.version_number,
            title=document.title,
            filename=version.original_filename,
            mime_type=version.mime_type,
            page=page,
            is_active_version=version.id == document.active_version_id,
        )


# Inline where browsers render safely; Word files download. Text is forced to plain text so it can never
# render as HTML, and nothing is cached or sent onward as a referrer.
_INLINE = {"application/pdf": "application/pdf", "text/plain": "text/plain; charset=utf-8"}


@router.get(
    "/files/{token}",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "text/plain": {}, "application/octet-stream": {}}}},
)
def download(token: str, settings: SettingsDep, engine: EngineDep, storage: StorageDep) -> Response:
    """Serve one original file for a valid, unexpired link from `/preview`. The link is the credential."""
    try:
        link = links.verify(settings, token)
    except links.InvalidLink:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "NOT_FOUND", "This link is invalid or has expired."
        ) from None
    with tenant_session(link.organization_id, engine) as session:
        row = session.execute(
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == link.version_id, Document.deleted_at.is_(None))
        ).one_or_none()
    if row is None:  # deleted since the link was issued: deletion revokes open links
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "This link is invalid or has expired.")
    version: DocumentVersion = row[0]
    try:
        data = storage.get(version.storage_key)
    except FileNotFoundError:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "The file is no longer available.") from None
    inline = version.mime_type in _INLINE
    disposition = "inline" if inline else "attachment"
    return Response(
        content=data,
        media_type=_INLINE.get(version.mime_type, version.mime_type),
        headers={
            "Content-Disposition": f'{disposition}; filename="{version.original_filename}"',
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
