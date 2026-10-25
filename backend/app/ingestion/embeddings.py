"""Embedding providers (blueprint §7.3). Vectors are always L2-normalized, so cosine distance in Chroma
(ADR-010, `hnsw.space = cosine`) behaves the same for every provider.

- `fake`: deterministic feature hashing over Unicode word tokens and bigrams. No network, any language, and
  related wording lands near each other, which is enough for development and tests (§5.1 substitute).
- `openai`: the `/v1/embeddings` endpoint. Calls are batched; 429, 5xx and network errors are retried with
  bounded exponential backoff and jitter (honouring `Retry-After`); spend is logged per document.
- `local`: the same client against our own OpenAI-compatible llama.cpp server (ADR-026), with no key. Models
  like nomic-embed need task prefixes, so questions and chunks are embedded with different ones.

The model name and dimension are pinned in settings and recorded on the Chroma collection; the index
refuses to open with a different pair (app/integrations/chroma.py).
"""

import hashlib
import itertools
import logging
import math
import random
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import ModelProvider, Settings, get_settings
from app.ingestion.chunking import estimate_tokens
from app.rag.text import STOPWORDS, stem

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_WORD = re.compile(r"\w+", re.UNICODE)


class EmbeddingError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    tokens: int


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    tokens: int
    batches: int
    cost_usd: float


class Embedder(Protocol):
    model: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...

    def embed_queries(self, texts: Sequence[str]) -> EmbeddingBatch: ...


def l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return [1.0] + [0.0] * (len(vector) - 1)  # cosine is undefined for a zero vector
    return [x / norm for x in vector]


class FakeEmbedder:
    # Part of the model ID, so the index fingerprint (ADR-013) changes whenever the algorithm does.
    ALGORITHM = "hash-v2"  # v2: stemmed content words with log term frequency (v1 hashed every raw word)

    def __init__(self, model: str, dimension: int) -> None:
        self.model = f"{model}@{self.ALGORITHM}"
        self.dimension = dimension

    def _vector(self, text: str) -> list[float]:
        words = [stem(w) for w in _WORD.findall(text.lower()) if w not in STOPWORDS]
        counts = Counter(words)
        features = [(w, 1 + math.log(n)) for w, n in counts.items()]
        features += [(f"{a} {b}", 0.5) for a, b in itertools.pairwise(words)]
        vector = [0.0] * self.dimension
        for feature, weight in features:
            digest = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vector[digest % self.dimension] += weight if digest >> 63 else -weight
        return l2_normalize(vector)

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch([self._vector(t) for t in texts], sum(estimate_tokens(t) for t in texts))

    def embed_queries(self, texts: Sequence[str]) -> EmbeddingBatch:
        return self.embed(texts)  # hashing needs no task prefix


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        dimension: int,
        *,
        base_url: str = OPENAI_BASE_URL,
        query_prefix: str = "",
        document_prefix: str = "",
        client: httpx.Client | None = None,
        max_attempts: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.dimension = dimension
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=5.0))
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Document chunks."""
        return self._embed([self._document_prefix + t for t in texts])

    def embed_queries(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Questions (search queries)."""
        return self._embed([self._query_prefix + t for t in texts])

    def _embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        payload: dict[str, object] = {"model": self.model, "input": list(texts), "encoding_format": "float"}
        if self.model.startswith("text-embedding-3"):
            payload["dimensions"] = self.dimension  # v3 models can shorten vectors; others are fixed-size
        for attempt in range(1, self._max_attempts + 1):
            try:
                headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
                response = self._client.post(self._url, json=payload, headers=headers)
            except httpx.TransportError as exc:
                failure = EmbeddingError(f"network error: {type(exc).__name__}", retryable=True)
                retry_after = None
            else:
                if response.status_code == 200:
                    return self._parse(response.json(), len(texts))
                retryable = response.status_code in RETRYABLE_STATUS
                failure = EmbeddingError(f"HTTP {response.status_code}", retryable=retryable)
                if not retryable:
                    raise failure  # 400/401/403/404: configuration problems; retrying can't fix them
                retry_after = _retry_after(response)
            if attempt == self._max_attempts:
                raise failure
            delay = min(self._max_delay, self._base_delay * 2 ** (attempt - 1))
            delay = retry_after if retry_after is not None else delay * random.uniform(0.5, 1.0)  # noqa: S311
            log.warning("embedding call failed (%s); retry %d in %.1fs", failure, attempt, delay)
            self._sleep(min(delay, self._max_delay))
        raise AssertionError("unreachable")

    def _parse(self, body: dict[str, object], expected: int) -> EmbeddingBatch:
        data = sorted(body["data"], key=lambda item: item["index"])  # type: ignore[call-overload]
        vectors = [l2_normalize(item["embedding"]) for item in data]
        if len(vectors) != expected or any(len(v) != self.dimension for v in vectors):
            raise EmbeddingError("unexpected embedding count or dimension", retryable=False)
        usage = body.get("usage") or {}
        return EmbeddingBatch(vectors, int(usage.get("total_tokens", 0)))  # type: ignore[attr-defined]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def embed_all(
    embedder: Embedder, texts: Sequence[str], batch_size: int, price_per_1k_tokens: float = 0.0
) -> EmbeddingResult:
    vectors: list[list[float]] = []
    tokens = 0
    batches = 0
    for start in range(0, len(texts), batch_size):
        batch = embedder.embed(texts[start : start + batch_size])
        vectors.extend(batch.vectors)
        tokens += batch.tokens
        batches += 1
    cost = tokens / 1000 * price_per_1k_tokens
    log.info(
        "embedded %d texts in %d batches: %d tokens, $%.6f (%s)",
        len(texts),
        batches,
        tokens,
        cost,
        embedder.model,
    )
    return EmbeddingResult(vectors, tokens, batches, cost)


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider is ModelProvider.FAKE:
        return FakeEmbedder(settings.embedding_model, settings.embedding_dimension)
    if settings.embedding_provider is ModelProvider.LOCAL:
        return OpenAIEmbedder(
            None,
            settings.embedding_model,
            settings.embedding_dimension,
            base_url=settings.embedding_base_url or "",
            query_prefix=settings.embedding_query_prefix,
            document_prefix=settings.embedding_document_prefix,
        )
    if settings.llm_api_key is None:
        raise ValueError("LLM_API_KEY is required for the openai embedding provider")
    return OpenAIEmbedder(
        settings.llm_api_key.get_secret_value(), settings.embedding_model, settings.embedding_dimension
    )


@lru_cache
def get_embedder() -> Embedder:
    return build_embedder(get_settings())
