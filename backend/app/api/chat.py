"""Question answering API (blueprint §8, §9): ask, conversation history, deletion and feedback.

Who is asking is always resolved server-side (app/api/deps.py `get_caller`, ADR-017):
- Signed-in staff or customers: the bearer token (re-checked against memberships, ADR-014).
- Anonymous public chat: the configured public organization, public audience only, and a per-browser session
  reference (`X-Session-Ref`) that the Next.js BFF keeps in an httpOnly cookie. Only its SHA-256 is stored.
The voice agent's contract lives in app/api/internal.py.
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    Caller,
    CallerDep,
    ChatModelDep,
    EmbedderDep,
    EngineDep,
    IndexDep,
    RerankerDep,
    SettingsDep,
)
from app.api.errors import COMMON_ERRORS, JSON_ERRORS, ApiError, request_id
from app.api.ratelimit import limiter
from app.config import Settings
from app.db.models import (
    Answer,
    AnswerCitation,
    AnswerFeedback,
    Conversation,
    Document,
    DocumentChunk,
    DocumentVersion,
    Message,
    MessageRole,
    Rating,
)
from app.db.session import tenant_session
from app.rag import service
from app.rag.graph import RAGContext
from app.rag.schemas import AskResult

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["chat"], responses=COMMON_ERRORS)


def _rate_limit(caller: Caller, settings: Settings) -> None:
    limit = (
        settings.rate_limit_user_per_minute
        if caller.principal.user_id
        else settings.rate_limit_anonymous_per_minute
    )
    retry_after = limiter.check(caller.rate_key, limit)
    if retry_after is None and caller.principal.user_id is None:
        # All anonymous sessions share one budget, so minting new sessions can't multiply the per-session one.
        org_key = f"public:{caller.principal.organization_id}"
        retry_after = limiter.check(org_key, settings.rate_limit_public_per_minute)
    if retry_after is not None:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "RATE_LIMITED",
            f"You've asked a lot of questions quickly. You can ask again in {retry_after} seconds.",
            {"retry_after_seconds": retry_after},
            {"Retry-After": str(retry_after)},
        )


def _owns(caller: Caller, conversation: Conversation) -> bool:
    if caller.principal.user_id is not None:
        return conversation.owner_user_id == caller.principal.user_id
    return conversation.owner_user_id is None and conversation.session_ref == caller.session_ref


def _owned_conversation(session: Session, caller: Caller, conversation_id: uuid.UUID) -> Conversation:
    conversation = session.get(Conversation, conversation_id)  # RLS: other tenants' rows are invisible
    if conversation is None or not _owns(caller, conversation):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Conversation not found.")  # no existence leak
    return conversation


# ---------- ask ----------


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    product_model: str | None = Field(default=None, max_length=120)
    # The assistant's other filters (§10.2): they only narrow the documents the caller may already see.
    brand: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    file_type: Literal["pdf", "docx", "txt"] | None = None


@dataclass(frozen=True, slots=True)
class PreparedAsk:
    ctx: RAGContext
    question: str
    product_model: str | None
    filters: dict[str, str | None]


def prepare_ask(
    body: AskIn,
    request: Request,
    caller: CallerDep,
    settings: SettingsDep,
    engine: EngineDep,
    index: IndexDep,
    embedder: EmbedderDep,
    llm: ChatModelDep,
    reranker: RerankerDep,
) -> PreparedAsk:
    """Everything that can refuse the request (401, 404, 429) runs here, as a dependency, so it completes
    before a streaming response has sent its headers."""
    _rate_limit(caller, settings)
    if body.conversation_id is not None:
        with tenant_session(caller.principal.organization_id, engine) as session:
            _owned_conversation(session, caller, body.conversation_id)
    ctx = RAGContext(
        principal=caller.principal,
        engine=engine,
        index=index,
        embedder=embedder,
        llm=llm,
        settings=settings,
        request_id=request_id(request),
        trace_id=f"trace-{uuid.uuid4().hex}",
        conversation_id=body.conversation_id,
        reranker=reranker,
        session_ref=caller.session_ref,
    )
    filters = {"brand": body.brand, "category": body.category, "file_type": body.file_type}
    return PreparedAsk(ctx, body.question, body.product_model, filters)


PreparedAskDep = Annotated[PreparedAsk, Depends(prepare_ask)]


@router.post("/chat/ask")
def ask(prepared: PreparedAskDep) -> AskResult:
    """Answer one question. JSON for voice, tests and simple clients; the chat UI uses `/chat/ask/stream`."""
    return service.ask(prepared.ctx, prepared.question, prepared.product_model, prepared.filters)


@router.post("/chat/ask/stream", response_class=EventSourceResponse, responses=JSON_ERRORS)
def ask_stream(prepared: PreparedAskDep) -> Iterator[ServerSentEvent]:
    """SSE (docs/architecture.md §4): `meta`, `status`, provisional `delta`, then the authoritative `final`.
    Failures after the stream started arrive as an `error` event in the §9.3 shape.
    """
    ctx = prepared.ctx
    yield ServerSentEvent(event="meta", data={"request_id": ctx.request_id, "trace_id": ctx.trace_id})
    try:
        for event, payload in service.ask_stream(
            ctx, prepared.question, prepared.product_model, prepared.filters
        ):
            data = payload.model_dump(mode="json") if isinstance(payload, AskResult) else payload
            yield ServerSentEvent(event=event, data=data)
    except Exception:
        log.exception("answer stream failed", extra={"request_id": ctx.request_id})
        yield ServerSentEvent(
            event="error",
            data={
                "code": "INTERNAL_ERROR",
                "message": "Something went wrong while answering. Try again in a moment.",
                "request_id": ctx.request_id,
                "retryable": True,
            },
        )


# ---------- conversations ----------


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime
    message_count: int


class CitationOut(BaseModel):
    position: int
    document_id: uuid.UUID | None  # None once the source document was deleted
    version_id: uuid.UUID | None
    title: str | None
    audience: str | None
    section: str | None
    page_start: int | None
    page_end: int | None
    quote: str | None
    preview_url: str | None  # same as in a live answer; None once the source is gone


class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    answer_id: uuid.UUID | None = None
    answer_status: str | None = None
    grounded: bool | None = None
    citations: list[CitationOut] = []


class ConversationOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]


def _owner_filter(caller: Caller) -> ColumnElement[bool]:
    if caller.principal.user_id is not None:
        return Conversation.owner_user_id == caller.principal.user_id
    return and_(Conversation.owner_user_id.is_(None), Conversation.session_ref == caller.session_ref)


@router.get("/conversations")
def list_conversations(caller: CallerDep, engine: EngineDep) -> list[ConversationSummary]:
    with tenant_session(caller.principal.organization_id, engine) as session:
        first_question = (
            select(Message.content)
            .where(Message.conversation_id == Conversation.id, Message.role == MessageRole.USER.value)
            .order_by(Message.created_at)
            .limit(1)
            .scalar_subquery()
        )
        count = select(func.count()).where(Message.conversation_id == Conversation.id).scalar_subquery()
        rows = session.execute(
            select(Conversation.id, Conversation.updated_at, first_question.label("title"), count.label("n"))
            .where(_owner_filter(caller))
            .order_by(Conversation.updated_at.desc())
            .limit(50)
        ).all()
    return [
        ConversationSummary(
            id=r.id, title=(r.title or "New conversation")[:80], updated_at=r.updated_at, message_count=r.n
        )
        for r in rows
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID, caller: CallerDep, engine: EngineDep) -> ConversationOut:
    with tenant_session(caller.principal.organization_id, engine) as session:
        conversation = _owned_conversation(session, caller, conversation_id)
        messages = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at, Message.id)
        ).all()
        answers = {
            a.message_id: a
            for a in session.scalars(select(Answer).where(Answer.message_id.in_([m.id for m in messages])))
        }
        cited = session.execute(
            select(
                AnswerCitation,
                DocumentVersion.document_id,
                Document.title,
                Document.audience,
                DocumentChunk.section,
            )
            .outerjoin(DocumentChunk, DocumentChunk.id == AnswerCitation.chunk_id)
            .outerjoin(DocumentVersion, DocumentVersion.id == AnswerCitation.document_version_id)
            .outerjoin(Document, Document.id == DocumentVersion.document_id)
            .where(AnswerCitation.answer_id.in_([a.id for a in answers.values()]))
            .order_by(AnswerCitation.position)
        ).all()
        by_answer: dict[uuid.UUID, list[CitationOut]] = {}
        for citation, document_id, title, audience, section in cited:
            preview = (
                f"/api/v1/documents/{document_id}/preview?version_id={citation.document_version_id}"
                f"&page={citation.page_start}"
                if document_id and citation.page_start
                else None
            )
            by_answer.setdefault(citation.answer_id, []).append(
                CitationOut(
                    position=citation.position,
                    document_id=document_id,
                    version_id=citation.document_version_id,
                    title=title,
                    audience=audience,
                    section=section,
                    page_start=citation.page_start,
                    page_end=citation.page_end,
                    quote=citation.quote,
                    preview_url=preview,
                )
            )
        out = []
        for m in messages:
            answer = answers.get(m.id)
            out.append(
                MessageOut(
                    id=m.id,
                    role=MessageRole(m.role),
                    content=m.content,
                    created_at=m.created_at,
                    answer_id=answer.id if answer else None,
                    answer_status=answer.answer_status if answer else None,
                    grounded=answer.grounded if answer else None,
                    citations=by_answer.get(answer.id, []) if answer else [],
                )
            )
        return ConversationOut(
            id=conversation.id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            messages=out,
        )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: uuid.UUID, caller: CallerDep, engine: EngineDep) -> Response:
    """Delete one of your conversations now (§10.1). Messages, answers, citations and feedback go with it
    (database cascades); analytics stop counting it. Nothing is kept in Chroma or storage for chats.
    """
    with tenant_session(caller.principal.organization_id, engine) as session:
        session.delete(_owned_conversation(session, caller, conversation_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/conversations", status_code=status.HTTP_204_NO_CONTENT)
def clear_history(caller: CallerDep, engine: EngineDep) -> Response:
    """Delete every conversation you own: "Clear history" (§10.1)."""
    with tenant_session(caller.principal.organization_id, engine) as session:
        session.execute(delete(Conversation).where(_owner_filter(caller)))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- feedback ----------


class FeedbackIn(BaseModel):
    rating: Rating
    reasons: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=5)
    note: str | None = Field(default=None, max_length=2000)


class FeedbackOut(BaseModel):
    id: uuid.UUID


@router.post("/answers/{answer_id}/feedback", status_code=status.HTTP_201_CREATED)
def give_feedback(
    answer_id: uuid.UUID, body: FeedbackIn, caller: CallerDep, engine: EngineDep
) -> FeedbackOut:
    with tenant_session(caller.principal.organization_id, engine) as session:
        row = session.execute(
            select(Answer, Conversation)
            .join(Message, Message.id == Answer.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Answer.id == answer_id)
        ).one_or_none()
        if row is None or not _owns(caller, row.Conversation):
            raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Answer not found.")
        feedback = AnswerFeedback(
            organization_id=caller.principal.organization_id,
            answer_id=answer_id,
            user_id=caller.principal.user_id,
            rating=body.rating.value,
            reasons=body.reasons,
            note=(body.note or "").strip() or None,
        )
        session.add(feedback)
        session.flush()
        return FeedbackOut(id=feedback.id)
