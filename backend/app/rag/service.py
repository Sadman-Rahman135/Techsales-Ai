"""Run the question-answering graph for one request: as a single result (JSON clients, voice) or as a stream
of
SSE-ready events (chat UI, ADR-006: `status` and provisional `delta` events, then one authoritative `final`).
"""

import re
from collections.abc import Iterator
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamMode

from app.db.models import AnswerStatus
from app.rag import prompts
from app.rag.graph import RECURSION_LIMIT, RAGContext, RAGState, build_graph
from app.rag.schemas import AskResult, Citation

_CONFIG: RunnableConfig = {"recursion_limit": RECURSION_LIMIT}
_STREAM_MODES: list[StreamMode] = ["custom", "values"]
_MARKER = re.compile(r"\s*\[\d+\]")


FILTERS = ("brand", "category", "file_type")  # besides product_model (§10.2)


def _input(question: str, product_model: str | None, filters: dict[str, str | None] | None) -> RAGState:
    cleaned = {k: v.strip() for k, v in (filters or {}).items() if k in FILTERS and v and v.strip()}
    return {"question": question, "product_model": (product_model or "").strip() or None, "filters": cleaned}


def ask(
    ctx: RAGContext,
    question: str,
    product_model: str | None = None,
    filters: dict[str, str | None] | None = None,
) -> AskResult:
    # LangGraph types v1 results loosely (dict | Any); the graph's output schema is RAGState.
    state = cast(
        RAGState, build_graph().invoke(_input(question, product_model, filters), _CONFIG, context=ctx)
    )
    return to_result(ctx, state)


def ask_stream(
    ctx: RAGContext,
    question: str,
    product_model: str | None = None,
    filters: dict[str, str | None] | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yields (event, payload): `status`/`delta` while the graph runs, then (`final`, AskResult)."""
    final_state: RAGState | None = None
    for part in build_graph().stream(
        _input(question, product_model, filters), _CONFIG, context=ctx, stream_mode=_STREAM_MODES
    ):
        mode, chunk = cast(tuple[str, Any], part)  # multiple modes → (mode, chunk) pairs
        if mode == "custom":
            event = dict(chunk)
            yield event.pop("event"), event
        else:
            final_state = cast(RAGState, chunk)
    if final_state is None:  # pragma: no cover - the graph always produces a final state
        raise RuntimeError("graph produced no state")
    yield "final", to_result(ctx, final_state)


def to_result(ctx: RAGContext, state: RAGState) -> AskResult:
    status = AnswerStatus(state["answer_status"])
    citations = [
        Citation(
            position=position,
            document_id=source.document_id,
            version_id=source.version_id,
            chunk_id=source.chroma_id,
            title=source.title,
            page_start=source.page_start,
            page_end=source.page_end,
            section=source.section,
            quote=quote,
            audience=source.audience,
            # Every cited source is a published version in the caller's audiences (retrieval guarantees it),
            # so the caller may open it. The endpoint re-checks anyway and issues the short-lived file link.
            preview_url=f"/api/v1/documents/{source.document_id}/preview?version_id={source.version_id}"
            f"&page={source.page_start}",
        )
        for position, (source, quote) in enumerate(state.get("citations", []), start=1)
    ]
    return AskResult(
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
        conversation_id=state["conversation_id"],
        answer_id=state["answer_id"],
        answer_status=status,
        grounded=status is AnswerStatus.ANSWERED,
        answer=state["answer"],
        citations=citations,
        suggested_followups=state.get("suggested_followups", []),
        next_action=state.get("next_action"),
        records=state.get("records", []),
    )


def speech_summary(result: AskResult) -> str:
    """At most two sentences for voice (§9.2), without citation markers."""
    if result.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE:
        return prompts.SPEECH_NOT_IN_DOCUMENTS
    text = _MARKER.sub("", result.answer)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[:2])
