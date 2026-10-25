"""Blueprint §7.3: normalized vectors, batching, bounded backoff on rate limits, spend logging."""

import json
import logging
import math
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.ingestion.embeddings import EmbeddingError, FakeEmbedder, OpenAIEmbedder, embed_all


def norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embeddings_are_deterministic_normalized_and_meaningful() -> None:
    embedder = FakeEmbedder("fake", 384)
    a, b, c, empty = embedder.embed(
        ["laptop warranty covers 12 months", "the laptop warranty lasts 12 months", "store opening hours", ""]
    ).vectors
    assert embedder.embed(["laptop warranty covers 12 months"]).vectors[0] == a
    assert all(len(v) == 384 and math.isclose(norm(v), 1.0) for v in (a, b, c, empty))
    assert cosine(a, b) > cosine(a, c)


def openai(
    handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float], **kw: Any
) -> OpenAIEmbedder:
    return OpenAIEmbedder(
        "sk-test",
        "text-embedding-3-small",
        4,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        **kw,
    )


def ok(request: httpx.Request) -> httpx.Response:
    texts = json.loads(request.content)["input"]
    data = [{"index": i, "embedding": [3.0, 4.0, 0.0, 0.0]} for i in reversed(range(len(texts)))]
    return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 10 * len(texts)}})


def test_openai_requests_are_batched_and_vectors_normalized() -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer sk-test"
        return ok(request)

    result = embed_all(
        openai(handler, []), [f"text {i}" for i in range(5)], batch_size=2, price_per_1k_tokens=0.02
    )
    assert [len(c["input"]) for c in calls] == [2, 2, 1]  # type: ignore[arg-type]
    assert calls[0]["dimensions"] == 4 and calls[0]["model"] == "text-embedding-3-small"
    assert result.vectors[0] == [0.6, 0.8, 0.0, 0.0]
    assert (result.batches, result.tokens) == (3, 50)
    assert math.isclose(result.cost_usd, 0.001)


def test_rate_limits_back_off_exponentially_and_honour_retry_after() -> None:
    responses = iter(
        [httpx.Response(429), httpx.Response(503), httpx.Response(429, headers={"Retry-After": "7"})]
    )
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses, None) or ok(request)

    embedder = openai(handler, sleeps, base_delay=1, max_delay=30)
    assert len(embedder.embed(["a"]).vectors) == 1
    assert len(sleeps) == 3
    assert 0.5 <= sleeps[0] <= 1.0 and 1.0 <= sleeps[1] <= 2.0  # jittered 1s, 2s
    assert sleeps[2] == 7.0  # server-specified


def test_backoff_is_bounded_then_gives_up_as_retryable() -> None:
    sleeps: list[float] = []
    embedder = openai(lambda _: httpx.Response(429, headers={"Retry-After": "999"}), sleeps, max_attempts=4)
    with pytest.raises(EmbeddingError) as info:
        embedder.embed(["a"])
    assert info.value.retryable is True
    assert len(sleeps) == 3 and max(sleeps) <= 30.0


def test_network_errors_are_retried() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("refused")
        return ok(request)

    assert len(openai(handler, []).embed(["a"]).vectors) == 1
    assert len(attempts) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_configuration_errors_are_not_retried(status: int) -> None:
    sleeps: list[float] = []
    with pytest.raises(EmbeddingError) as info:
        openai(lambda _: httpx.Response(status), sleeps).embed(["a"])
    assert info.value.retryable is False
    assert sleeps == []


def test_wrong_dimension_from_the_provider_is_refused() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}], "usage": {}})

    with pytest.raises(EmbeddingError, match="dimension"):
        openai(handler, []).embed(["a"])


def test_spend_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.ingestion.embeddings"):
        embed_all(openai(ok, []), ["a", "b"], batch_size=10, price_per_1k_tokens=0.5)
    assert "20 tokens, $0.010000 (text-embedding-3-small)" in caplog.text


def test_the_local_embedder_uses_its_server_no_key_and_task_prefixes() -> None:
    """ADR-026: llama.cpp's /v1/embeddings; nomic-embed wants different prefixes for questions and chunks."""
    sent: list[tuple[str, dict[str, str], list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append((str(request.url), dict(request.headers), body["input"]))
        data = [{"index": i, "embedding": [1.0, 0.0, 0.0, 0.0]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": data, "usage": {"total_tokens": 3}})

    embedder = OpenAIEmbedder(
        None,
        "nomic-embed-text-v1.5-q8_0",
        4,
        base_url="http://embedder:8080/v1/",
        query_prefix="search_query: ",
        document_prefix="search_document: ",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    embedder.embed(["Opened laptops have a fee."])
    embedder.embed_queries(["restocking fee?"])
    assert [url for url, _, _ in sent] == ["http://embedder:8080/v1/embeddings"] * 2
    assert all("authorization" not in headers for _, headers, _ in sent)
    assert sent[0][2] == ["search_document: Opened laptops have a fee."]
    assert sent[1][2] == ["search_query: restocking fee?"]
