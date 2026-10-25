"""PostgreSQL schema (blueprint §6.1, docs/data-model.md). P0 tables only; P1 tables are specified in the doc.

Conventions:
- UUIDv7 primary keys (`uuidv7()`, native in Postgres 18): time-ordered, so no index fragmentation.
- Every tenant-owned row carries `organization_id`. Children reference parents through composite foreign keys
  `(organization_id, parent_id)`, so a row can never point at another tenant's data.
- Status columns are text + CHECK (easier to evolve than Postgres enums); values are the StrEnums below.
- Delete behaviour is explicit on every foreign key; the reasoning is in docs/data-model.md §3.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------- allowed values ----------


class Audience(StrEnum):
    PUBLIC = "public"
    EMPLOYEE = "employee"


class Role(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    CUSTOMER = "customer"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class VersionStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    FAILED = "failed"


class JobState(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Stages a job moves through while being processed (a lease is held during these).
ACTIVE_JOB_STATES = (JobState.PARSING, JobState.CHUNKING, JobState.EMBEDDING, JobState.INDEXING)
FINISHED_JOB_STATES = (JobState.DONE, JobState.FAILED, JobState.CANCELLED)


class ConversationState(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ESCALATED = "escalated"
    ERROR = "error"
    DIRECT = (
        "direct"  # safe canned reply (greeting, help, order lookup not available): no sources, not grounded
    )


class Rating(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


def one_of(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


# ---------- column helpers ----------


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()"))


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def org_id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"))


# ---------- identity and tenancy (no RLS: needed before a tenant is known, e.g. at login) ----------


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    timezone: Mapped[str] = mapped_column(Text, server_default="America/Chicago")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'", name="slug_format"),)


class User(Base):
    """A global identity; tenant access and role come from Membership."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)  # argon2id; never logged or returned
    auth_subject: Mapped[str | None] = mapped_column(Text)  # external IdP subject (P2 SSO)
    status: Mapped[str] = mapped_column(Text, server_default=UserStatus.ACTIVE.value)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
        CheckConstraint(one_of("status", UserStatus), name="status"),
        CheckConstraint("password_hash IS NOT NULL OR auth_subject IS NOT NULL", name="has_credential"),
    )


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id"),
        Index("ix_memberships_user_id", "user_id"),
        CheckConstraint(one_of("role", Role), name="role"),
    )


# ---------- knowledge (RLS by organization_id) ----------


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    product_model: Mapped[str | None] = mapped_column(Text)
    # §2.2 metadata search (P1): who makes the product, which team owns the document, its language and the
    # date it takes effect. Filters only ever narrow what a caller may already see.
    brand: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, server_default="en")
    effective_date: Mapped[date | None] = mapped_column(Date)
    audience: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")  # admin filtering only (§10.3)
    # ADR-003: the single source of truth for "which version answers questions". Changed only by publish.
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete, then purge
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),  # target for tenant-safe composite FKs
        CheckConstraint(one_of("audience", Audience), name="audience"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        CheckConstraint("cardinality(tags) <= 10", name="tags_limit"),
        CheckConstraint("language ~ '^[a-z]{2}$'", name="language_code"),  # ISO 639-1
        # The active version must belong to this document (composite FK; NULL = nothing published).
        ForeignKeyConstraint(
            ["id", "active_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            use_alter=True,
            name="fk_documents_active_version",
        ),
        Index("ix_documents_created_by", "created_by"),
        Index("ix_documents_active_version_id", "active_version_id"),
        # Serves active_version_ids(): published, not deleted, by tenant and audience.
        Index(
            "ix_documents_answerable",
            "organization_id",
            "audience",
            postgresql_where=text("deleted_at IS NULL AND active_version_id IS NOT NULL"),
        ),
    )


class DocumentVersion(Base):
    """Immutable once uploaded: a replacement is always a new version (§6.3)."""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version_number: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(Text)  # sha256 hex of the original file
    storage_key: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default=VersionStatus.UPLOADED.value)
    expected_chunk_count: Mapped[int | None] = mapped_column(Integer)
    indexed_chunk_count: Mapped[int] = mapped_column(Integer, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("document_id", "version_number"),  # §6.1 constraint
        UniqueConstraint(
            "document_id", "checksum"
        ),  # §6.3: the same file twice is a duplicate, not a version
        UniqueConstraint("document_id", "id"),  # target for documents.active_version_id
        UniqueConstraint("organization_id", "id"),  # target for tenant-safe child FKs
        # At most one published version per document; publish archives the previous one first.
        Index(
            "uq_document_versions_one_published",
            "document_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index("ix_document_versions_org_checksum", "organization_id", "checksum"),
        Index("ix_document_versions_created_by", "created_by"),
        CheckConstraint(one_of("status", VersionStatus), name="status"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("indexed_chunk_count >= 0", name="indexed_count_non_negative"),
        CheckConstraint("status <> 'published' OR published_at IS NOT NULL", name="published_has_timestamp"),
    )


class IngestionJob(Base):
    """Durable job row claimed with FOR UPDATE SKIP LOCKED (ADR-008).

    No RLS: the worker claims across tenants, then sets the tenant context from the job before touching
    tenant tables."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text, server_default=JobState.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    error_code: Mapped[str | None] = mapped_column(Text)  # stable code; the UI maps it to friendly copy
    failed_stage: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Per-stage durations in ms, e.g. {"parsing": 120, "embedding": 2400} (§7.4 latency by stage).
    stage_timings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # Non-fatal findings shown to admins: redactions, empty or low-confidence pages (§7.2).
    warnings: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )  # lease; expired = reclaimable
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "version_id"],
            ["document_versions.organization_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        Index("ix_ingestion_jobs_version_id", "version_id"),
        Index("ix_ingestion_jobs_claimable", "run_after", postgresql_where=text("state = 'queued'")),
        # At most one unfinished job per version: a retry reuses the job instead of racing a second one.
        Index(
            "uq_ingestion_jobs_one_active_per_version",
            "version_id",
            unique=True,
            postgresql_where=text("state NOT IN ('done', 'failed', 'cancelled')"),
        ),
        CheckConstraint(one_of("state", JobState), name="state"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_bounded"),
    )


class DocumentChunk(Base):
    """Source mapping for citations and Postgres↔Chroma reconciliation (ADR-002)."""

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    chunk_index: Mapped[int] = mapped_column(Integer)
    chroma_id: Mapped[str] = mapped_column(
        Text, unique=True
    )  # "{org}:{version}:{index}", see integrations/chroma.py
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    text_checksum: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    # The chunk's (already redacted) text for keyword search (§8.5, ADR-028). Deleted with the row, so a purge
    # removes it; NULL only on rows written before ADR-028 until `app.ingestion.rebuild` fills them.
    text: Mapped[str | None] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(section, '') || ' ' || coalesce(text, ''))", persisted=True
        ),
        deferred=True,  # never needed in Python: only the search query reads it
    )
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "version_id"],
            ["document_versions.organization_id", "document_versions.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("version_id", "chunk_index"),
        UniqueConstraint("organization_id", "id"),
        Index("ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("page_start >= 1 AND page_end >= page_start", name="page_range"),
        CheckConstraint(
            "chroma_id = organization_id::text || ':' || version_id::text || ':' || chunk_index::text",
            name="chroma_id_deterministic",
        ),
    )


# ---------- conversations (RLS by organization_id) ----------


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),  # deleting a user deletes their chats
    )
    session_ref: Mapped[str | None] = mapped_column(Text)  # hashed anonymous browser session
    audience: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default=ConversationState.ACTIVE.value)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        Index("ix_conversations_owner_user_id", "owner_user_id"),
        Index(
            "ix_conversations_session_ref", "session_ref", postgresql_where=text("session_ref IS NOT NULL")
        ),
        CheckConstraint(one_of("audience", Audience), name="audience"),
        CheckConstraint(one_of("state", ConversationState), name="state"),
        CheckConstraint("owner_user_id IS NOT NULL OR session_ref IS NOT NULL", name="has_owner"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        CheckConstraint(one_of("role", MessageRole), name="role"),
    )


class Intent(StrEnum):
    SMALLTALK = "smalltalk"
    KNOWLEDGE = "knowledge"
    ORDER = "order"
    SUPPORT = "support"


class Answer(Base):
    """Result audit for one assistant message (§13 metrics, G1-G4 measurement).

    Trace fields store IDs and timings only, never retrieved text (§13 "IDs, not full confidential text")."""

    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    answer_status: Mapped[str] = mapped_column(Text)
    grounded: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(Text)
    graph_path: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    retrieved_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")  # chroma IDs
    retrieval_attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    timings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))  # ms per node
    # §13.2 trace shape: who asked (a role surrogate, never an identity), why it failed, what it cost.
    caller_role: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)  # sanitized: a code, never a message
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "message_id"],
            ["messages.organization_id", "messages.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("organization_id", "id"),
        Index("ix_answers_org_status_created", "organization_id", "answer_status", "created_at"),
        CheckConstraint(one_of("answer_status", AnswerStatus), name="answer_status"),
        CheckConstraint(f"intent IS NULL OR {one_of('intent', Intent)}", name="intent"),
        CheckConstraint(
            "caller_role IS NULL OR caller_role IN "
            "('anonymous', 'customer', 'employee', 'manager', 'admin', 'service', 'evaluation')",
            name="caller_role",
        ),
        CheckConstraint("retrieval_attempts BETWEEN 0 AND 2", name="retrieval_attempts_bounded"),
        # "Answered" means grounded; anything else must not claim grounding.
        CheckConstraint("(answer_status = 'answered') = grounded", name="grounded_matches_status"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_non_negative"),
    )


class AnswerCitation(Base):
    """Evidence actually shown to the user. Survives source deletion with the link nulled (§6.3 retention)."""

    __tablename__ = "answer_citations"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    answer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    position: Mapped[int] = mapped_column(SmallInteger)  # [1], [2], … as displayed
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    quote: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "answer_id"], ["answers.organization_id", "answers.id"], ondelete="CASCADE"
        ),
        # SET NULL on only the id column (Postgres 15+), keeping organization_id intact.
        ForeignKeyConstraint(
            ["organization_id", "chunk_id"],
            ["document_chunks.organization_id", "document_chunks.id"],
            ondelete="SET NULL (chunk_id)",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_version_id"],
            ["document_versions.organization_id", "document_versions.id"],
            ondelete="SET NULL (document_version_id)",
        ),
        UniqueConstraint("answer_id", "position"),
        Index("ix_answer_citations_chunk_id", "chunk_id"),
        Index("ix_answer_citations_document_version_id", "document_version_id"),
        CheckConstraint("position >= 1", name="position_positive"),
    )


class AnswerFeedback(Base):
    __tablename__ = "answer_feedback"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    answer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    rating: Mapped[str] = mapped_column(Text)
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "answer_id"], ["answers.organization_id", "answers.id"], ondelete="CASCADE"
        ),
        Index("ix_answer_feedback_answer_id", "answer_id"),
        Index("ix_answer_feedback_user_id", "user_id"),
        CheckConstraint(one_of("rating", Rating), name="rating"),
        CheckConstraint("note IS NULL OR length(note) <= 2000", name="note_length"),
    )


# ---------- audit (RLS + append-only trigger) ----------


class AuditEvent(Base):
    """Append-only. No FK to users: the actor is a snapshot, so deleting a user never rewrites history."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(Text)  # e.g. "user:morgan@techcare.example" or "system:worker"
    action: Mapped[str] = mapped_column(Text)  # e.g. "document.version.published"
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    request_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        Index("ix_audit_events_org_created", "organization_id", text("created_at DESC")),
        Index("ix_audit_events_target", "target_type", "target_id"),
    )


# ---------- operations (no tenant: one row per worker process) ----------


class WorkerHeartbeat(Base):
    """Written by each ingestion worker every loop; read by `GET /health` (§11.3 worker and queue checks).

    The worker sees the queue across tenants, so it also records the queue's depth and oldest wait here: the
    API's tenant-scoped role could not count it.
    """

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)  # "host:pid:start"
    hostname: Mapped[str] = mapped_column(Text)
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stopping: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    jobs_processed: Mapped[int] = mapped_column(Integer, server_default="0")
    queue_depth: Mapped[int] = mapped_column(Integer, server_default="0")
    oldest_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_worker_heartbeats_beat_at", "beat_at"),)


class RequestMetric(Base):
    """Per-minute request counts by route template and status, summed across API processes (§13.1 error rate,
    rate-limited and permission-denied requests). Route templates only: no IDs, no query strings."""

    __tablename__ = "request_metrics"

    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    method: Mapped[str] = mapped_column(Text, primary_key=True)
    route: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    total_ms: Mapped[int] = mapped_column(BigInteger)  # the primary key's leading column serves time ranges


class AlertEvent(Base):
    """One firing of an alert rule, open until its condition clears (§13.2 alerts)."""

    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    rule: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        CheckConstraint("severity IN ('warning', 'critical')", name="severity"),
        # At most one open alert per rule.
        Index("uq_alert_events_open_rule", "rule", unique=True, postgresql_where=text("resolved_at IS NULL")),
        Index("ix_alert_events_fired_at", "fired_at"),
    )


# ---------- orders and support tickets (P1, US-05, ADR-029; RLS by organization_id) ----------


class OrderStatus(StrEnum):
    PLACED = "placed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    READY_FOR_PICKUP = "ready_for_pickup"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class DeliveryMethod(StrEnum):
    STANDARD = "standard"
    EXPRESS = "express"
    PICKUP = "pickup"


class RepairStatus(StrEnum):
    RECEIVED = "received"
    DIAGNOSING = "diagnosing"
    WAITING_FOR_PARTS = "waiting_for_parts"
    REPAIRING = "repairing"
    READY_FOR_PICKUP = "ready_for_pickup"
    COLLECTED = "collected"
    CANCELLED = "cancelled"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class Product(Base):
    """Catalogue rows for order lines (fictional). Never in Chroma: products answer through documents."""

    __tablename__ = "products"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    sku: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    product_model: Mapped[str | None] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "sku"),
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
    )


class Customer(Base):
    """A customer as orders know them. No readable contact details are stored (ADR-029): only keyed hashes of
    the email and phone used at checkout, enough to verify a caller and useless if the table leaks."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    external_ref: Mapped[str] = mapped_column(Text)  # e.g. "CUST-00042", shown to staff
    user_id: Mapped[uuid.UUID | None] = mapped_column(  # set when the customer has a TechCare account
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    email_hash: Mapped[str] = mapped_column(Text)  # HMAC-SHA256 of the normalized email (app/core/contact.py)
    phone_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "external_ref"),
        Index("ix_customers_user_id", "user_id"),
        CheckConstraint("email_hash ~ '^[0-9a-f]{64}$'", name="email_hash_format"),
        CheckConstraint("phone_hash IS NULL OR phone_hash ~ '^[0-9a-f]{64}$'", name="phone_hash_format"),
    )


class Order(Base):
    """The source of truth for order questions (§2.4: never in Chroma, never from the model), shown only after
    verification (US-05) or to the signed-in customer it belongs to."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    order_number: Mapped[str] = mapped_column(Text)  # "TC-123456", printed on the receipt
    status: Mapped[str] = mapped_column(Text, server_default=OrderStatus.PLACED.value)
    delivery_method: Mapped[str] = mapped_column(Text, server_default=DeliveryMethod.STANDARD.value)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_on: Mapped[date | None] = mapped_column(Date)
    carrier: Mapped[str | None] = mapped_column(Text)
    tracking_number: Mapped[str | None] = mapped_column(Text)
    total_cents: Mapped[int] = mapped_column(Integer)
    # Store pickup (ADR-031): where to collect it and the last day it is held.
    pickup_store: Mapped[str | None] = mapped_column(Text)
    pickup_by: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "order_number"),
        Index("ix_orders_customer_id", "customer_id"),
        CheckConstraint(one_of("status", OrderStatus), name="status"),
        CheckConstraint(one_of("delivery_method", DeliveryMethod), name="delivery_method"),
        CheckConstraint("order_number ~ '^TC-[0-9]{6}$'", name="order_number_format"),
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
        CheckConstraint(
            "delivery_method = 'pickup' OR (pickup_store IS NULL AND pickup_by IS NULL)", name="pickup_fields"
        ),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price_cents: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "order_id"], ["orders.organization_id", "orders.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["organization_id", "product_id"],
            ["products.organization_id", "products.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_order_items_order_id", "order_id"),
        Index("ix_order_items_product_id", "product_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_non_negative"),
    )


class SupportTicket(Base):
    """A case handed from the employee assistant to the support team (design-system employee-assistant.md).
    The summary is drafted by the local model and always reviewed by the employee before it is saved."""

    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    number: Mapped[int] = mapped_column(Integer)  # per organization, shown as "T-0042"
    subject: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    # True when the employee started from the AI draft (they still reviewed and could edit it).
    summary_drafted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, server_default=TicketStatus.OPEN.value)
    priority: Mapped[str] = mapped_column(Text, server_default=TicketPriority.NORMAL.value)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    order_number: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="SET NULL (customer_id)",  # only the link: organization_id stays (Postgres 15+)
        ),
        # A deleted conversation leaves the ticket (its summary is the record); only the link goes.
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="SET NULL (conversation_id)",
        ),
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "number"),
        Index("ix_support_tickets_org_status", "organization_id", "status"),
        Index("ix_support_tickets_customer_id", "customer_id"),
        Index("ix_support_tickets_conversation_id", "conversation_id"),
        Index("ix_support_tickets_created_by", "created_by"),
        Index("ix_support_tickets_assigned_to", "assigned_to"),
        CheckConstraint(one_of("status", TicketStatus), name="status"),
        CheckConstraint(one_of("priority", TicketPriority), name="priority"),
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint("length(trim(subject)) BETWEEN 1 AND 200", name="subject_length"),
        CheckConstraint("length(summary) BETWEEN 1 AND 4000", name="summary_length"),
        CheckConstraint("(status = 'resolved') = (resolved_at IS NOT NULL)", name="resolved_has_timestamp"),
    )


class TicketNote(Base):
    __tablename__ = "ticket_notes"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["support_tickets.organization_id", "support_tickets.id"],
            ondelete="CASCADE",
        ),
        Index("ix_ticket_notes_ticket_id", "ticket_id"),
        Index("ix_ticket_notes_author_id", "author_id"),
        CheckConstraint("length(trim(body)) BETWEEN 1 AND 4000", name="body_length"),
    )


class ReviewStatus(StrEnum):
    RESOLVED = "resolved"  # a document was added or fixed, or the answer was otherwise dealt with
    DISMISSED = "dismissed"  # nothing to change (out of scope, spam, the answer was right)


class AnswerReview(Base):
    """A manager's decision on an unanswered question or a not-helpful answer (plan §2.2 "administrative
    review workflows", ADR-030). An answer with no row is still open; reopening deletes the row."""

    __tablename__ = "answer_reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    answer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )  # the document that now answers it
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        # The review goes with its answer (conversation deletion and retention, ADR-019).
        ForeignKeyConstraint(
            ["organization_id", "answer_id"],
            ["answers.organization_id", "answers.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
            ondelete="SET NULL (document_id)",
        ),
        UniqueConstraint("organization_id", "answer_id"),
        Index("ix_answer_reviews_document_id", "document_id"),
        Index("ix_answer_reviews_reviewed_by", "reviewed_by"),
        CheckConstraint(one_of("status", ReviewStatus), name="status"),
        CheckConstraint("note IS NULL OR length(note) <= 1000", name="note_length"),
    )


class Repair(Base):
    """A repair job at a TechCare store (ADR-031): the source of truth for "what was wrong with my laptop".
    Fictional demo data; like orders, never in Chroma and shown only to its verified customer."""

    __tablename__ = "repairs"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # the purchase, when known
    reference: Mapped[str] = mapped_column(Text)  # "RP-100001", printed on the repair receipt
    product_name: Mapped[str] = mapped_column(Text)
    reported_fault: Mapped[str] = mapped_column(Text)  # what the customer said at the desk
    diagnosis: Mapped[str | None] = mapped_column(Text)  # what the technician found
    resolution: Mapped[str | None] = mapped_column(Text)  # what was done
    status: Mapped[str] = mapped_column(Text, server_default=RepairStatus.RECEIVED.value)
    store: Mapped[str] = mapped_column(Text)
    under_warranty: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cost_cents: Mapped[int | None] = mapped_column(Integer)  # None while not yet quoted
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready_on: Mapped[date | None] = mapped_column(Date)  # promised or actual ready date
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "order_id"],
            ["orders.organization_id", "orders.id"],
            ondelete="SET NULL (order_id)",
        ),
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "reference"),
        Index("ix_repairs_customer_id", "customer_id"),
        Index("ix_repairs_order_id", "order_id"),
        CheckConstraint(one_of("status", RepairStatus), name="status"),
        CheckConstraint("reference ~ '^RP-[0-9]{6}$'", name="reference_format"),
        CheckConstraint("cost_cents IS NULL OR cost_cents >= 0", name="cost_non_negative"),
        CheckConstraint(
            "(status = 'collected') = (collected_at IS NOT NULL)", name="collected_has_timestamp"
        ),
    )


class AccountGrant(Base):
    """One order or repair a guest proved is theirs in one conversation (ADR-031). Lets follow-up questions in
    that chat answer from it, until it expires; signed-in customers don't need one for their own records."""

    __tablename__ = "account_grants"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_id()
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    repair_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        # Deleting the conversation (or the record) ends the access.
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "order_id"], ["orders.organization_id", "orders.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["organization_id", "repair_id"], ["repairs.organization_id", "repairs.id"], ondelete="CASCADE"
        ),
        Index("ix_account_grants_conversation_id", "conversation_id"),
        Index("ix_account_grants_order_id", "order_id"),
        Index("ix_account_grants_repair_id", "repair_id"),
        CheckConstraint("(order_id IS NULL) <> (repair_id IS NULL)", name="one_record"),
    )


# Tables protected by row-level security on organization_id (ADR-011). Used by the migration and tests.
RLS_TABLES = (
    "documents",
    "document_versions",
    "document_chunks",
    "conversations",
    "messages",
    "answers",
    "answer_citations",
    "answer_feedback",
    "audit_events",
    # ADR-029
    "products",
    "customers",
    "orders",
    "order_items",
    "support_tickets",
    "ticket_notes",
    # ADR-030
    "answer_reviews",
    # ADR-031
    "repairs",
    "account_grants",
)
