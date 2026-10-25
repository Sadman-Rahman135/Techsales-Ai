"""The optional reranker (ADR-028): its client, its place in the graph, and the evidence rule it can relax."""

import json
import uuid
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.rag import evidence
from app.rag.graph import _reranked
from app.rag.rerank import LocalReranker, build_reranker
from app.rag.schemas import Source
from tests.unit.test_config import make


def _source(text: str, score: float | None = None) -> Source:
    version = uuid.uuid4()
    return Source(
        source_id="C1",
        chroma_id=f"org:{version}:0",
        document_id=uuid.uuid4(),
        version_id=version,
        title="T",
        page_start=1,
        page_end=1,
        section=None,
        audience="public",
        text=text,
        similarity=0.5,
        rerank_score=score,
    )


def test_the_local_reranker_posts_question_and_passages_and_keeps_input_order() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 3.5}, {"index": 0, "relevance_score": -2.0}]},
        )

    reranker = LocalReranker(
        "http://reranker:8080/v1", "bge", 5.0, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert reranker.score("Is theft covered?", ["Delivery takes 3 days.", "Theft is not covered."]) == [
        -2.0,
        3.5,
    ]
    assert seen["url"] == "http://reranker:8080/v1/rerank"
    assert seen["body"]["query"] == "Is theft covered?" and len(seen["body"]["documents"]) == 2
    assert reranker.score("anything", []) == []  # nothing to send


def test_candidates_are_ordered_by_score_and_a_failing_reranker_changes_nothing() -> None:
    first, second = _source("a"), _source("b")

    class Scores:
        model = "test"

        def score(self, question: str, passages: Sequence[str]) -> list[float]:
            return [0.1, 2.0]

    class Down:
        model = "test"

        def score(self, question: str, passages: Sequence[str]) -> list[float]:
            raise httpx.ConnectError("reranker is down")

    ordered = _reranked(Scores(), "q", [first, second])
    assert [s.text for s in ordered] == ["b", "a"] and ordered[0].rerank_score == 2.0
    assert _reranked(Down(), "q", [first, second]) == [first, second]  # never fails the question


def test_the_relaxed_rule_needs_both_a_high_score_and_some_coverage() -> None:
    def grade(source: Source, relaxed: tuple[float, float] | None) -> bool:
        return evidence.grade(
            "how many days does a normal parcel take to arrive",
            [source],
            [source.version_id],
            min_similarity=0.05,
            min_coverage=0.6,
            limit=5,
            relaxed=relaxed,
        ).sufficient

    parcel = "Standard delivery takes 3 to 5 business days. Express delivery arrives the next business day."
    assert not grade(_source(parcel, 2.0), None)  # off by default: coverage alone decides
    assert grade(_source(parcel, 2.0), (1.5, 0.3))
    assert not grade(_source(parcel, 1.0), (1.5, 0.3))  # score too low
    assert not grade(_source("Gift cards never expire.", 9.0), (1.5, 0.3))  # no words in common: never


def test_a_local_reranker_needs_its_url_and_none_means_off() -> None:
    with pytest.raises(ValidationError, match="RERANKER_BASE_URL"):
        make(reranker_provider="local")
    assert build_reranker(make()) is None
    assert build_reranker(make(reranker_provider="local", reranker_base_url="http://reranker:8080/v1"))
