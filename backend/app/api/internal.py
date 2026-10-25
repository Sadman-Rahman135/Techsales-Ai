"""Service-to-service API for the Project 2 voice agent (blueprint §9.2, US-07, ADR-001, ADR-017).

Contract `knowledge-answer.v1`, frozen in docs/contracts/knowledge-answer.v1.schema.json (a test fails if the
models drift from it). Compatible additions keep v1; renames, removals or meaning changes need a v2 route
alongside v1.

- The caller authenticates with the internal service token. Its organization comes from settings and its
  audience is public. `audience=employee` is refused (403) until verified agent scopes exist (P1): Project 1
  computes effective rights and never trusts a role assertion from the caller.
- One conversation per call: the caller's `conversation_id` (its call ID) is hashed into the conversation
  owner.
- The response is safe to speak: `speech_summary` is at most two sentences without citation markers.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import (
    ChatModelDep,
    EmbedderDep,
    EngineDep,
    IndexDep,
    SettingsDep,
    hash_session_ref,
    organization_id_for,
    require_service,
)
from app.api.errors import COMMON_ERRORS, ApiError, request_id
from app.core.permissions import Principal
from app.db.models import AnswerStatus, Conversation
from app.db.session import tenant_session
from app.rag import service
from app.rag.graph import RAGContext

ContractVersion = Literal["knowledge-answer.v1"]
CONTRACT_VERSION: ContractVersion = "knowledge-answer.v1"
CONTRACT_HEADER = "X-Contract-Version"

router = APIRouter(
    prefix="/api/v1/internal",
    tags=["internal"],
    dependencies=[Depends(require_service)],
    responses=COMMON_ERRORS,
)


class KnowledgeQuestion(BaseModel):
    """Request body of `knowledge-answer.v1`."""

    request_id: str | None = Field(default=None, max_length=64, description="The caller's ID, echoed back")
    audience: Literal["public", "employee"] = Field(
        default="public", description="Requested scope. Only `public` is granted to this credential."
    )
    channel: Literal["voice"] = "voice"
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str = Field(min_length=1, max_length=128, description="The caller's call or session ID")


class KnowledgeCitation(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    page_start: int


class KnowledgeAnswer(BaseModel):
    """Response body of `knowledge-answer.v1`."""

    contract_version: ContractVersion = CONTRACT_VERSION
    request_id: str
    answer_status: AnswerStatus
    grounded: bool
    answer: str
    speech_summary: str = Field(description="At most two sentences, no citation markers: safe to speak")
    citations: list[KnowledgeCitation]
    trace_id: str


@router.post("/knowledge/answer")
def knowledge_answer(
    body: KnowledgeQuestion,
    request: Request,
    response: Response,
    settings: SettingsDep,
    engine: EngineDep,
    index: IndexDep,
    embedder: EmbedderDep,
    llm: ChatModelDep,
) -> KnowledgeAnswer:
    """Answer one spoken question from published, public TechCare documents (or say they don't cover it)."""
    if body.audience != "public":
        raise ApiError(
            status.HTTP_403_FORBIDDEN, "FORBIDDEN", "This credential can only ask public questions."
        )
    principal = Principal(organization_id=organization_id_for(engine, settings.internal_service_organization))
    session_ref = hash_session_ref(f"voice:{body.conversation_id}")
    with tenant_session(principal.organization_id, engine) as session:
        conversation_id = session.scalar(
            select(Conversation.id)
            .where(Conversation.session_ref == session_ref, Conversation.owner_user_id.is_(None))
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
    ctx = RAGContext(
        principal=principal,
        engine=engine,
        index=index,
        embedder=embedder,
        llm=llm,
        settings=settings,
        request_id=body.request_id or request_id(request),
        trace_id=f"trace-{uuid.uuid4().hex}",
        conversation_id=conversation_id,
        session_ref=session_ref,
        caller_role="service",
    )
    result = service.ask(ctx, body.question)
    response.headers[CONTRACT_HEADER] = CONTRACT_VERSION
    return KnowledgeAnswer(
        request_id=result.request_id,
        answer_status=result.answer_status,
        grounded=result.grounded,
        answer=result.answer,
        speech_summary=service.speech_summary(result),
        citations=[
            KnowledgeCitation(document_id=c.document_id, version_id=c.version_id, page_start=c.page_start)
            for c in result.citations
        ],
        trace_id=result.trace_id,
    )
