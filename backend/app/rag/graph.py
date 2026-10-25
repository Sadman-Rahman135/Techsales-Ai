"""The question-answering workflow (blueprint §8.1-§8.3, ADR-007): one deterministic LangGraph.

Routes (every path ends in `persist`):
- validate → classify → direct_reply: greeting, help, "talk to a person", empty question
- validate → classify → business_tool: order/repair lookups (P0 handoff, P1 verified lookup)
- validate → classify → retrieve → grade → generate → check_citations: the grounded answer
- … grade → rewrite_query → retrieve → grade …: weak evidence, one rewrite at most (MAX_RETRIEVALS). A
  follow-up ("How do I fix it?") is rewritten with the conversation's previous question instead (§8.5,
  ADR-028): short history, only after the question failed on its own, so a standalone question never changes
- … → abstain: weak after the rewrite, provider error, model says insufficient, or citations fail validation

Security never depends on the model: the principal is fixed in the run context by the API, retrieval is
filtered by tenant, audience and active versions from Postgres (ADR-003/004), and citations are re-validated
against what was actually retrieved. Retrieved text lives only in graph state; only IDs are persisted (§13).
"""

import logging
import operator
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from sqlalchemy import Engine, select

from app.config import Settings
from app.core.permissions import Principal
from app.core.time import utcnow
from app.db.models import (
    Answer,
    AnswerCitation,
    AnswerStatus,
    Conversation,
    DocumentChunk,
    Intent,
    Message,
    MessageRole,
)
from app.db.session import tenant_session
from app.ingestion.embeddings import Embedder
from app.integrations.chroma import KnowledgeIndex
from app.rag import citations, evidence, intent, keyword, prompts
from app.rag.llm import ChatModel, ChatModelError
from app.rag.rerank import Reranker
from app.rag.schemas import Source
from app.rag.text import content_terms
from app.rag.usage import TokenUsage
from app.services import account
from app.services.versions import active_version_ids

log = logging.getLogger(__name__)

RECURSION_LIMIT = 12  # §3 budget; the longest path is 10 steps
MAX_RETRIEVALS = 2  # first attempt + one rewrite (§8.3 node 5)


# ---------- state and run context ----------


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class RAGState(TypedDict, total=False):
    question: str
    product_model: str | None
    filters: dict[str, str]  # brand, category, file_type: narrow only (§10.2)
    intent: str
    previous_question: str | None  # the conversation's last question (short history, §8.5)
    contextualized: bool  # the retry used the previous question
    search_query: str
    retrieval_attempts: int
    active_version_ids: list[uuid.UUID]
    candidates: list[Source]
    sources: list[Source]  # graded, relevant, renumbered S1…Sn
    draft: Any  # GeneratedAnswer before validation
    answer: str
    answer_status: str
    citations: list[tuple[Source, str]]
    suggested_followups: list[str]
    next_action: str | None  # "verify_order" (US-05) or "show_records" (ADR-031)
    records: list[Any]  # the caller's own orders/repairs the answer is about (ADR-031)
    model_id: str | None
    conversation_id: uuid.UUID
    answer_id: uuid.UUID
    path: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    timings: Annotated[dict[str, int], _merge]


@dataclass(frozen=True, slots=True)
class RAGContext:
    """Per-request dependencies and identity. Set by the API from verified credentials, never from the request
    body.
    """

    principal: Principal
    engine: Engine
    index: KnowledgeIndex
    embedder: Embedder
    llm: ChatModel
    settings: Settings
    request_id: str
    trace_id: str
    conversation_id: uuid.UUID | None = None  # already verified to belong to this caller
    reranker: Reranker | None = None  # optional (ADR-028); None keeps retrieval's own order
    session_ref: str | None = None  # hashed anonymous/voice session (owner of new conversations)
    caller_role: str | None = None  # trace surrogate override: "service" (voice), "evaluation" (eval runner)
    # perf_counter, not monotonic: Windows ticks monotonic every ~15 ms, so short deadlines never tripped.
    started: float = field(default_factory=time.perf_counter)
    usage: TokenUsage = field(default_factory=TokenUsage)  # filled by the embedder and the model (§13.2)

    @property
    def role_surrogate(self) -> str:
        """Who asked, as a role only (never an identity): anonymous, customer, employee, … (§13.2)."""
        if self.caller_role:
            return self.caller_role
        return self.principal.role.value if self.principal.role else "anonymous"

    @property
    def deadline_passed(self) -> bool:
        return time.perf_counter() - self.started > self.settings.request_deadline_seconds

    @property
    def audience_names(self) -> list[str]:
        return [a.value for a in self.principal.allowed_audiences]


Ctx = Runtime[RAGContext]


def _emit(event: str, **payload: Any) -> None:
    """Progress for SSE clients (custom stream). A no-op when the graph runs without streaming."""
    get_stream_writer()({"event": event, **payload})


class GraphNode(Protocol):
    """LangGraph passes the run context as the keyword `runtime` (its `_NodeWithRuntime` protocol)."""

    def __call__(self, state: RAGState, *, runtime: Ctx) -> dict[str, Any]: ...


def _timed(name: str, fn: Callable[[RAGState, Ctx], dict[str, Any]]) -> GraphNode:
    """Wrap a node: record its duration and append it to the graph path (both persisted for tracing, §13)."""

    def node(state: RAGState, *, runtime: Ctx) -> dict[str, Any]:
        started = time.perf_counter()
        update = fn(state, runtime)
        elapsed = round((time.perf_counter() - started) * 1000)
        update["path"] = [name]
        update["timings"] = {**update.get("timings", {}), name: elapsed}
        return update

    node.__name__ = name
    return node


# ---------- nodes (§8.3) ----------


def validate_request(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    """Normalize the text. Tenant and audience come from the context (the API resolved them), never from
    input.
    """
    question = " ".join("".join(ch for ch in state.get("question", "") if ch.isprintable()).split())
    question = question[: runtime.context.settings.max_question_chars]
    return {
        "question": question,
        "previous_question": _previous_question(runtime.context) if question else None,
        "retrieval_attempts": 0,
        "errors": [] if question else ["empty question"],
    }


def _previous_question(ctx: RAGContext) -> str | None:
    """The last question in this conversation (already verified as the caller's), or None. One turn only."""
    if ctx.conversation_id is None:
        return None
    with tenant_session(ctx.principal.organization_id, ctx.engine) as session:
        return session.scalar(
            select(Message.content)
            .where(Message.conversation_id == ctx.conversation_id, Message.role == MessageRole.USER.value)
            .order_by(Message.created_at.desc())
            .limit(1)
        )


_FOLLOW_UP = re.compile(
    r"^(and|also|but|so|what about|how about)\b|\b(it|its|it's|that|this|they|them|those|these)\b"
)


def is_follow_up(question: str) -> bool:
    """Leans on the previous turn: starts like a continuation, points back with a pronoun, or is almost
    empty."""
    lowered = question.lower()
    return bool(_FOLLOW_UP.search(lowered)) or len(content_terms(question)) <= 2


def grading_question(state: RAGState) -> str:
    """What evidence is graded against: the question, plus the previous one once the retry used it."""
    if state.get("contextualized") and state.get("previous_question"):
        return f"{state['previous_question']} {state['question']}"
    return state["question"]


def classify_intent(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    kind = intent.classify(state["question"])
    previous = state.get("previous_question")
    # "And when will it arrive?" right after an order question is about the same order (ADR-031).
    if (
        kind is Intent.KNOWLEDGE
        and previous
        and is_follow_up(state["question"])
        and intent.classify(previous) is Intent.ORDER
    ):
        kind = Intent.ORDER
    return {"intent": kind.value}


def retrieve_documents(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    ctx = runtime.context
    _emit("status", stage="searching")
    attempts = state.get("retrieval_attempts", 0) + 1
    query = state.get("search_query") or " ".join(
        filter(None, [state["question"], state.get("product_model")])
    )
    if ctx.deadline_passed:
        return {"retrieval_attempts": attempts, "candidates": [], "errors": ["TIMEOUT"]}
    try:
        limit = ctx.settings.retrieval_candidates
        with tenant_session(ctx.principal.organization_id, ctx.engine) as session:
            # ADR-003: from Postgres, per request; the product filter can only narrow it.
            active = active_version_ids(
                session, ctx.principal, state.get("product_model"), **state.get("filters", {})
            )
            # ADR-028: keyword candidates over the same active versions, in the same tenant session (RLS).
            keyword_ids = (
                keyword.search(session, query, active, limit)
                if ctx.settings.keyword_search and keyword.names_a_code(query)
                else []
            )
        batch = ctx.embedder.embed_queries([query])
        ctx.usage.embedding_tokens += batch.tokens
        vector = batch.vectors[0]
        hits = ctx.index.query(ctx.principal, vector, active, n_results=limit)
        if keyword_ids:
            found = {hit.chroma_id for hit in hits}
            extra = ctx.index.fetch(ctx.principal, [i for i in keyword_ids if i not in found], vector, active)
            hits = keyword.fuse(hits, keyword_ids, extra, k=ctx.settings.rrf_k, limit=limit)
    except Exception as exc:
        return {
            "retrieval_attempts": attempts,
            "candidates": [],
            "errors": [f"RETRIEVAL_FAILED:{type(exc).__name__}"],
        }
    candidates = [
        Source(
            source_id=f"C{i}",
            chroma_id=hit.chroma_id,
            document_id=uuid.UUID(str(hit.metadata["document_id"])),
            version_id=uuid.UUID(str(hit.metadata["document_version_id"])),
            title=str(hit.metadata["title"]),
            page_start=int(hit.metadata["page_start"]),
            page_end=int(hit.metadata["page_end"]),
            section=str(hit.metadata["section"]) if hit.metadata.get("section") else None,
            audience=str(hit.metadata["audience"]),
            product_model=str(hit.metadata["product_model"]) if hit.metadata.get("product_model") else None,
            text=hit.text,
            similarity=round(1.0 - hit.distance, 4),
        )
        for i, hit in enumerate(hits, start=1)
    ]
    if ctx.reranker is not None and candidates:
        candidates = _reranked(ctx.reranker, query, candidates)
    return {
        "retrieval_attempts": attempts,
        "search_query": query,
        "active_version_ids": active,
        "candidates": candidates,
    }


def _reranked(reranker: Reranker, query: str, candidates: list[Source]) -> list[Source]:
    """Candidates with their reranker scores, best first. A reranker failure keeps the retrieval order."""
    try:
        scores = reranker.score(query, [c.text for c in candidates])
    except Exception as exc:  # optional component: log and carry on
        log.warning("reranker failed (%s); keeping the retrieval order", type(exc).__name__)
        return candidates
    scored = [
        c.model_copy(update={"rerank_score": round(s, 3)}) for c, s in zip(candidates, scores, strict=True)
    ]
    return sorted(scored, key=lambda c: c.rerank_score or 0.0, reverse=True)


def grade_evidence(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    settings = runtime.context.settings
    result = evidence.grade(
        grading_question(state),
        state.get("candidates", []),
        state.get("active_version_ids", []),
        min_similarity=settings.min_evidence_similarity,
        min_coverage=settings.min_term_coverage,
        limit=settings.context_chunks,
        relaxed=(
            (settings.rerank_relaxed_score, settings.rerank_relaxed_coverage)
            if settings.rerank_relaxed_score is not None
            else None
        ),
    )
    return {"sources": result.sources}


def rewrite_query(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    _emit("status", stage="rewriting")
    previous = state.get("previous_question")
    if previous and is_follow_up(state["question"]):
        # Deterministic, no model call: the previous question supplies what "it" refers to.
        return {"search_query": f"{previous} {state['question']}", "contextualized": True}
    try:
        rewritten = runtime.context.llm.rewrite_query(state["question"], usage=runtime.context.usage)
    except ChatModelError as exc:
        return {"errors": [f"PROVIDER_FAILED:{exc}"]}
    return {"search_query": rewritten}


def generate_answer(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    ctx = runtime.context
    if ctx.deadline_passed:
        return {"errors": ["TIMEOUT"]}
    audience = "employee" if "employee" in ctx.audience_names else "public"
    question = state["question"]
    if state.get("contextualized"):
        # Exploration X37: told only "Earlier question … Follow-up …", the model answered the earlier one.
        question = (
            f"{question}\n(This is a follow-up. The customer's earlier question was: "
            f'"{state["previous_question"]}". Use it only to understand what the follow-up refers to, '
            "and answer the follow-up.)"
        )
    try:
        draft = ctx.llm.generate_answer(question, state["sources"], audience, usage=ctx.usage)
    except ChatModelError as exc:
        return {"errors": [f"PROVIDER_FAILED:{exc}"], "model_id": ctx.llm.model_id}
    if draft is None:
        return {"errors": ["OUTPUT_UNPARSEABLE"], "model_id": ctx.llm.model_id}
    if draft.answer_status == "answered":
        # ADR-006: provisional text is only ever sent after evidence was graded sufficient; `final` decides.
        _emit("delta", text=draft.answer)
    return {"draft": draft, "model_id": ctx.llm.model_id}


def check_citations(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    _emit("status", stage="checking")
    result = citations.validate(state["draft"], state["sources"], runtime.context.audience_names)
    if not result.valid:
        return {"errors": [f"CITATIONS_INVALID:{'; '.join(result.problems)}"]}
    draft = state["draft"]
    return {
        "answer": result.answer,
        "answer_status": AnswerStatus.ANSWERED.value,
        "citations": result.citations,
        "suggested_followups": list(draft.suggested_followups)[:3],
    }


def direct_reply(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    if not state.get("question"):
        text = prompts.EMPTY_QUESTION
    elif state.get("intent") == Intent.SUPPORT.value:
        text = prompts.SUPPORT_HANDOFF
    else:
        text = prompts.GREETING
    return {"answer": text, "answer_status": AnswerStatus.DIRECT.value, "citations": []}


_ABOUT_REPAIR = re.compile(r"\brepair|\bRP-?\d|technician|fault|\bfixed\b", re.IGNORECASE)


def business_tool(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    """Questions about one's own orders and repairs (US-05, ADR-029, ADR-031). Never answered from documents
    (non-goal: no orders in Chroma) and never from the model's memory:
    - The records come from fixed queries for this caller only: a signed-in customer's own, or the ones a
      guest verified in this conversation. With none, the chat shows the verification card.
    - The local model answers from those records alone. Its answer is used only if every reference and number
      in it is in the records; otherwise a summary built from the records is.
    The voice channel can't show a card or verify, so it keeps the support handoff."""
    ctx = runtime.context
    if ctx.caller_role == "service":
        return {
            "answer_status": AnswerStatus.DIRECT.value,
            "citations": [],
            "answer": prompts.ORDER_NOT_AVAILABLE,
        }
    answered = _from_records(state, ctx)
    if answered is not None:
        return answered
    verify = prompts.ACCOUNT_VERIFY if _ABOUT_REPAIR.search(state["question"]) else prompts.ORDER_VERIFY
    return {
        "answer_status": AnswerStatus.DIRECT.value,
        "citations": [],
        "answer": verify,
        "next_action": "verify_order",
    }


def _from_records(state: RAGState, ctx: RAGContext) -> dict[str, Any] | None:
    """The answer from the caller's own orders and repairs, or None when they have none to ask about."""
    question = state["question"]
    with tenant_session(ctx.principal.organization_id, ctx.engine) as session:
        found = account.records_for(session, ctx.principal, ctx.conversation_id)
        timezone = account.timezone_of(session, ctx.principal)
    if not found:
        return None
    _emit("status", stage="checking")
    facts = account.render(found, timezone, question=question)
    previous = state.get("previous_question")
    # Only a question that isn't about the records on its own leans on the last one ("and how much was it?");
    # the earlier question goes along as context, so the model answers the new one.
    follow_up = previous and intent.classify(question) is not Intent.ORDER and is_follow_up(question)
    asked = f"{question}\n(Earlier question, for context only: {previous})" if follow_up else question
    update: dict[str, Any] = {}
    try:
        reply = ctx.llm.answer_from_records(asked, facts, usage=ctx.usage)
        update["model_id"] = ctx.llm.model_id
    except ChatModelError as exc:
        log.warning("account answer failed (%s); using the record summary", exc)
        reply = ""
    answer = reply.strip() if account.grounded(reply, facts, question) else account.summary(found, timezone)
    return {
        "answer_status": AnswerStatus.DIRECT.value,
        "citations": [],
        **update,
        "answer": answer,
        "next_action": "show_records",
        "records": account.shown(found, answer, question),
    }


def abstain(state: RAGState, runtime: Ctx) -> dict[str, Any]:
    failed = any(
        e.startswith(("PROVIDER_FAILED", "RETRIEVAL_FAILED", "TIMEOUT")) for e in state.get("errors", [])
    )
    if failed:
        return {
            "answer": prompts.PROVIDER_FAILURE,
            "answer_status": AnswerStatus.ERROR.value,
            "citations": [],
        }
    # ADR-031: the documents don't answer it, but it's about orders or repairs and the caller has some
    # ("total cost of the orders ready for pickup"): answer from their records. Never for voice.
    ctx = runtime.context
    if ctx.caller_role != "service" and account.ACCOUNT_WORDS.search(state.get("question", "")):
        answered = _from_records(state, ctx)
        if answered is not None:
            return {**answered, "intent": Intent.ORDER.value}
    return {
        "answer": prompts.NOT_IN_DOCUMENTS,
        "answer_status": AnswerStatus.INSUFFICIENT_EVIDENCE.value,
        "citations": [],
    }


def persist(state: RAGState, *, runtime: Ctx) -> dict[str, Any]:
    """Store the turn: question, answer, status, citation links and trace IDs (no retrieved text, §13)."""
    ctx = runtime.context
    status = AnswerStatus(state["answer_status"])
    org = ctx.principal.organization_id
    with tenant_session(org, ctx.engine) as session:
        conversation_id = ctx.conversation_id
        if conversation_id is None:
            conversation = Conversation(
                organization_id=org,
                owner_user_id=ctx.principal.user_id,
                session_ref=None if ctx.principal.user_id else ctx.session_ref,
                audience="employee" if "employee" in ctx.audience_names else "public",
            )
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id
        else:
            conversation = session.get_one(Conversation, conversation_id)
            conversation.updated_at = utcnow()  # conversation lists sort by last activity
        session.add(
            Message(
                organization_id=org,
                conversation_id=conversation_id,
                role=MessageRole.USER.value,
                content=state.get("question", ""),
            )
        )
        reply = Message(
            organization_id=org,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT.value,
            content=state["answer"],
            model_id=state.get("model_id"),
        )
        session.add(reply)
        session.flush()
        latency = round((time.perf_counter() - ctx.started) * 1000)
        answer = Answer(
            organization_id=org,
            message_id=reply.id,
            answer_status=status.value,
            grounded=status is AnswerStatus.ANSWERED,
            latency_ms=latency,
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
            intent=state.get("intent"),
            graph_path=[*state.get("path", []), "persist"],
            retrieved_chunk_ids=[s.chroma_id for s in state.get("candidates", [])],
            retrieval_attempts=state.get("retrieval_attempts", 0),
            timings=state.get("timings", {}),
            caller_role=ctx.role_surrogate,
            error_code=error_code(state, status),
            token_usage=ctx.usage.as_record(ctx.settings),
        )
        session.add(answer)
        session.flush()
        cited = state.get("citations", [])
        rows = session.execute(
            select(DocumentChunk.chroma_id, DocumentChunk.id).where(
                DocumentChunk.chroma_id.in_([s.chroma_id for s, _ in cited])
            )
        )
        chunk_ids = {row.chroma_id: row.id for row in rows}
        for position, (source, quote) in enumerate(cited, start=1):
            session.add(
                AnswerCitation(
                    organization_id=org,
                    answer_id=answer.id,
                    position=position,
                    chunk_id=chunk_ids.get(source.chroma_id),
                    document_version_id=source.version_id,
                    page_start=source.page_start,
                    page_end=source.page_end,
                    quote=quote,
                )
            )
        return {"conversation_id": conversation_id, "answer_id": answer.id}


def error_code(state: RAGState, status: AnswerStatus) -> str | None:
    """A stable code for the trace and metrics (§13.2 "sanitized error code"): the first error's kind, never
    its detail; or why nothing was answered."""
    errors = state.get("errors", [])
    if errors:
        return re.sub(r"[^A-Z0-9_]", "_", errors[0].split(":", 1)[0].strip().upper())[:40]
    if status is AnswerStatus.INSUFFICIENT_EVIDENCE:
        return "NO_RESULTS" if not state.get("candidates") else "WEAK_EVIDENCE"
    return None


# ---------- routing ----------


def after_validate(state: RAGState) -> Literal["classify_intent", "direct_reply"]:
    return "classify_intent" if state.get("question") else "direct_reply"


def after_classify(state: RAGState) -> Literal["retrieve_documents", "business_tool", "direct_reply"]:
    match state["intent"]:
        case Intent.KNOWLEDGE.value:
            return "retrieve_documents"
        case Intent.ORDER.value:
            return "business_tool"
        case _:
            return "direct_reply"


def after_grade(state: RAGState) -> Literal["generate_answer", "rewrite_query", "abstain"]:
    if state.get("sources"):
        return "generate_answer"
    failed = any(e.startswith(("RETRIEVAL_FAILED", "TIMEOUT")) for e in state.get("errors", []))
    if not failed and state.get("retrieval_attempts", 0) < MAX_RETRIEVALS:
        return "rewrite_query"
    return "abstain"


def after_rewrite(state: RAGState) -> Literal["retrieve_documents", "abstain"]:
    failed = any(e.startswith("PROVIDER_FAILED") for e in state.get("errors", []))
    return "abstain" if failed else "retrieve_documents"


def after_generate(state: RAGState) -> Literal["check_citations", "abstain"]:
    draft = state.get("draft")
    return "check_citations" if draft is not None and draft.answer_status == "answered" else "abstain"


def after_check(state: RAGState) -> Literal["persist", "abstain"]:
    return "persist" if state.get("answer_status") == AnswerStatus.ANSWERED.value else "abstain"


@lru_cache
def build_graph() -> CompiledStateGraph[RAGState, RAGContext, RAGState, RAGState]:
    graph = StateGraph(RAGState, context_schema=RAGContext)
    for name, fn in [
        ("validate_request", validate_request),
        ("classify_intent", classify_intent),
        ("retrieve_documents", retrieve_documents),
        ("grade_evidence", grade_evidence),
        ("rewrite_query", rewrite_query),
        ("generate_answer", generate_answer),
        ("check_citations", check_citations),
        ("direct_reply", direct_reply),
        ("business_tool", business_tool),
        ("abstain", abstain),
    ]:
        graph.add_node(name, _timed(name, fn))
    graph.add_node("persist", persist)
    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges("validate_request", after_validate)
    graph.add_conditional_edges("classify_intent", after_classify)
    graph.add_edge("retrieve_documents", "grade_evidence")
    graph.add_conditional_edges("grade_evidence", after_grade)
    graph.add_conditional_edges("rewrite_query", after_rewrite)
    graph.add_conditional_edges("generate_answer", after_generate)
    graph.add_conditional_edges("check_citations", after_check)
    for terminal in ("direct_reply", "business_tool", "abstain"):
        graph.add_edge(terminal, "persist")
    graph.add_edge("persist", END)
    return graph.compile()
