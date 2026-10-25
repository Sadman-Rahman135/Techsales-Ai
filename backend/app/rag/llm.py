"""Chat model adapters for the two LLM steps of the graph: query rewrite and grounded answer generation.

- `fake` (development/test, §5.1 substitute): extractive and deterministic. It answers with the source
  sentences that share the most terms with the question and cites them, so it can't invent anything, and tests
  and the M3 gate are reproducible. It is not a measure of real answer quality: that waits for the provider
  choice (§19).
- `openai`: `ChatOpenAI` with strict JSON-schema output parsed into `GeneratedAnswer`. One client-side retry,
  a hard timeout, temperature 0. A response that doesn't parse returns `None`, and the graph abstains (§8.4).
- `local`: the same adapter against our own llama.cpp server (ADR-026, model in LLM/), which turns the JSON
  schema into a grammar, so the small local model can only produce valid answers. No key, no cost.

Provider failures raise `ChatModelError`; the graph turns them into an `error` answer, never a guess.
"""

import logging
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

import httpx
from pydantic import SecretStr, ValidationError

from app.config import ModelProvider, Settings, get_settings
from app.ingestion.chunking import estimate_tokens
from app.rag.prompts import (
    ACCOUNT_PROMPT,
    ANSWER_TEMPLATE,
    CASE_SUMMARY_PROMPT,
    REWRITE_PROMPT,
    render_sources,
    system_prompt,
)
from app.rag.schemas import CitedSource, GeneratedAnswer, Source
from app.rag.text import content_terms, ordered_terms, statements
from app.rag.usage import TokenUsage

log = logging.getLogger(__name__)


class ChatModelError(Exception):
    """The provider failed (timeout, rate limit after retries, 5xx, auth). Never shown verbatim to users."""


class ChatModel(Protocol):
    """Both calls add their token counts to `usage` when given (§13 trace shape)."""

    model_id: str

    def rewrite_query(self, question: str, *, usage: TokenUsage | None = None) -> str: ...

    def generate_answer(
        self, question: str, sources: Sequence[Source], audience: str, *, usage: TokenUsage | None = None
    ) -> GeneratedAnswer | None: ...

    def draft_case_summary(self, transcript: str, *, usage: TokenUsage | None = None) -> str:
        """A support ticket's draft summary from a conversation transcript (ADR-029)."""
        ...

    def answer_from_records(self, question: str, facts: str, *, usage: TokenUsage | None = None) -> str:
        """A short answer about the customer's own orders/repairs from their fact sheet only (ADR-031). The
        graph checks it against the sheet before anyone sees it."""
        ...


_ASKS_FOR_NUMBER = re.compile(
    r"\b(?:how (?:long|many|much|soon)|when|what time|hours?|price|cost|fee|days?)\b", re.IGNORECASE
)


class FakeChatModel:
    MAX_SENTENCES = 2

    def __init__(self, model_id: str = "fake-llm", token_budget: int = 6000) -> None:
        self.model_id = model_id
        self._budget = token_budget

    def rewrite_query(self, question: str, *, usage: TokenUsage | None = None) -> str:
        rewritten = " ".join(ordered_terms(question)) or question
        if usage is not None:  # what the same call would cost a real model
            usage.add_llm(
                estimate_tokens(REWRITE_PROMPT.format(question=question)), estimate_tokens(rewritten)
            )
        return rewritten

    def generate_answer(
        self, question: str, sources: Sequence[Source], audience: str, *, usage: TokenUsage | None = None
    ) -> GeneratedAnswer | None:
        result = self._extract(question, sources)
        if usage is not None:
            prompt = system_prompt(audience) + ANSWER_TEMPLATE.format(
                sources=render_sources(sources, self._budget), question=question
            )
            usage.add_llm(estimate_tokens(prompt), estimate_tokens(result.model_dump_json()))
        return result

    def draft_case_summary(self, transcript: str, *, usage: TokenUsage | None = None) -> str:
        """Deterministic: the first question and the last reply, quoted from the transcript."""
        lines = [line for line in transcript.splitlines() if line.strip()]
        asked = next((line.split(": ", 1)[1] for line in lines if line.startswith("Question: ")), "")
        replied = next(
            (line.split(": ", 1)[1] for line in reversed(lines) if line.startswith("Answer: ")), ""
        )
        summary = (
            f"Needs: {asked}\nAssistant: {replied or 'no answer yet'}\nOpen: follow up with the customer."
        )
        if usage is not None:
            usage.add_llm(
                estimate_tokens(CASE_SUMMARY_PROMPT.format(transcript=transcript)), estimate_tokens(summary)
            )
        return summary

    def answer_from_records(self, question: str, facts: str, *, usage: TokenUsage | None = None) -> str:
        """Deterministic: the one or two fact lines sharing the most words with the question, each with its
        record ("Order TC-100001: Status: On its way.")."""
        terms = content_terms(question)
        record = ""
        scored: list[tuple[int, int, str]] = []
        for position, line in enumerate(facts.splitlines()):
            if not line.startswith("- "):
                record = line
                continue
            overlap = len(terms & content_terms(line))
            if overlap:
                scored.append(
                    (overlap, -position, line[2:] if record == "Totals" else f"{record}: {line[2:]}")
                )
        scored.sort(reverse=True)
        answer = " ".join(_as_sentence(text) for _, _, text in scored[:2])
        if usage is not None:
            usage.add_llm(
                estimate_tokens(ACCOUNT_PROMPT.format(facts=facts, question=question)),
                estimate_tokens(answer),
            )
        return answer

    def _extract(self, question: str, sources: Sequence[Source]) -> GeneratedAnswer:
        terms = content_terms(question)
        needed = max(1, math.ceil(len(terms) / 3))
        wants_number = bool(_ASKS_FOR_NUMBER.search(question))
        scored: list[tuple[float, int, str, str]] = []
        for rank, source in enumerate(sources):
            heading = content_terms(source.section or "")  # "How to claim" gives its sentences their topic
            for sentence in statements(source.text, source.section):
                matched = terms & content_terms(sentence)
                own = len(matched) + sum(1 for t in matched if any(ch.isdigit() for ch in t))  # codes, models
                overlap = len(terms & (content_terms(sentence) | heading))
                if own and overlap >= needed:
                    # "How long / how many / when / what fee": the answer is a sentence with a number in it.
                    bonus = 1.5 if wants_number and any(ch.isdigit() for ch in sentence) else 0.0
                    scored.append((overlap + bonus + own / 10, -rank, sentence, source.source_id))
        if not scored:
            return GeneratedAnswer(
                answer="The sources don't answer this.",
                answer_status="insufficient_evidence",
                citations=[],
                suggested_followups=[],
            )
        scored.sort(reverse=True)
        chosen: list[tuple[str, str]] = []
        for _, _, sentence, source_id in scored:
            if sentence not in (s for s, _ in chosen):
                chosen.append((sentence, source_id))
            if len(chosen) == self.MAX_SENTENCES:
                break
        answer = " ".join(f"{_as_sentence(sentence)} [{source_id}]" for sentence, source_id in chosen)
        citations = [CitedSource(source_id=source_id, quote=sentence) for sentence, source_id in chosen]
        return GeneratedAnswer(
            answer=answer, answer_status="answered", citations=citations, suggested_followups=[]
        )


def _as_sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


class OpenAIChatModel:
    def __init__(
        self,
        settings: Settings,
        api_key: SecretStr | None,
        http_client: httpx.Client | None = None,
        base_url: str | None = None,
    ) -> None:
        from langchain_openai import ChatOpenAI  # imported lazily: the fake provider never needs it

        self.model_id = settings.llm_model
        self._budget = settings.context_token_budget
        self._llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=api_key or SecretStr("local"),  # a local server ignores it; the client requires one
            base_url=base_url,
            temperature=0,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,  # bounded (§3 budget); the graph abstains with an error after that
            max_completion_tokens=settings.answer_max_tokens,
            http_client=http_client,  # tests pass an httpx.MockTransport client
        )
        # include_raw: the provider's own usage counts come back with the parsed answer (§13 trace shape).
        self._structured = self._llm.with_structured_output(
            GeneratedAnswer, method="json_schema", strict=True, include_raw=True
        )

    def rewrite_query(self, question: str, *, usage: TokenUsage | None = None) -> str:
        try:
            reply = self._llm.invoke(REWRITE_PROMPT.format(question=question))
        except Exception as exc:
            raise ChatModelError(type(exc).__name__) from exc
        _count(reply, usage)
        text = reply.content if isinstance(reply.content, str) else ""
        return text.strip().strip('"')[:300] or question

    def draft_case_summary(self, transcript: str, *, usage: TokenUsage | None = None) -> str:
        try:
            reply = self._llm.invoke(CASE_SUMMARY_PROMPT.format(transcript=transcript))
        except Exception as exc:
            raise ChatModelError(type(exc).__name__) from exc
        _count(reply, usage)
        text = reply.content if isinstance(reply.content, str) else ""
        return text.strip()[:2000]

    def answer_from_records(self, question: str, facts: str, *, usage: TokenUsage | None = None) -> str:
        try:
            reply = self._llm.invoke(ACCOUNT_PROMPT.format(facts=facts, question=question))
        except Exception as exc:
            raise ChatModelError(type(exc).__name__) from exc
        _count(reply, usage)
        text = reply.content if isinstance(reply.content, str) else ""
        return text.strip()

    def generate_answer(
        self, question: str, sources: Sequence[Source], audience: str, *, usage: TokenUsage | None = None
    ) -> GeneratedAnswer | None:
        messages = [
            ("system", system_prompt(audience)),
            (
                "human",
                ANSWER_TEMPLATE.format(sources=render_sources(sources, self._budget), question=question),
            ),
        ]
        try:
            out = self._structured.invoke(messages)
        except ValidationError:
            log.warning("structured output did not match the schema; abstaining")
            return None
        except Exception as exc:
            if type(exc).__name__ in {"OutputParserException", "JSONDecodeError"}:
                log.warning("structured output could not be parsed; abstaining")
                return None
            raise ChatModelError(type(exc).__name__) from exc
        raw = out.get("raw") if isinstance(out, dict) else None
        _count(raw, usage)
        parsed = out.get("parsed") if isinstance(out, dict) else out
        if parsed is None or (isinstance(out, dict) and out.get("parsing_error") is not None):
            log.warning("structured output could not be parsed; abstaining")
            return None
        try:
            return parsed if isinstance(parsed, GeneratedAnswer) else GeneratedAnswer.model_validate(parsed)
        except ValidationError:
            log.warning("structured output did not match the schema; abstaining")
            return None


def _count(message: object, usage: TokenUsage | None) -> None:
    """Add a LangChain message's `usage_metadata` (input/output tokens) to the request's usage."""
    meta = getattr(message, "usage_metadata", None)
    if usage is not None and isinstance(meta, dict):
        usage.add_llm(int(meta.get("input_tokens", 0)), int(meta.get("output_tokens", 0)))


def build_chat_model(settings: Settings) -> ChatModel:
    if settings.llm_provider is ModelProvider.FAKE:
        return FakeChatModel(settings.llm_model, settings.context_token_budget)
    if settings.llm_provider is ModelProvider.LOCAL:
        return OpenAIChatModel(settings, None, base_url=settings.llm_base_url)
    if settings.llm_api_key is None:
        raise ValueError("LLM_API_KEY is required for the openai provider")
    return OpenAIChatModel(settings, settings.llm_api_key)


@lru_cache
def get_chat_model() -> ChatModel:
    return build_chat_model(get_settings())
