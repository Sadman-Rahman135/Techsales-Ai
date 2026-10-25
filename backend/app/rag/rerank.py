"""Optional cross-encoder reranking (§8.5, ADR-028): the local bge-reranker-v2-m3, served by llama.cpp's
/v1/rerank.

It reads the question and each candidate together and scores how well the passage answers the question, so it
orders the context better than either retrieval list. It never decides access (retrieval already filtered by
tenant, audience and version) and never fails a question: if the server is down, retrieval's own order stays.
"""

import logging
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class Reranker(Protocol):
    model: str

    def score(self, question: str, passages: Sequence[str]) -> list[float]:
        """One relevance score per passage, in input order (higher is more relevant)."""
        ...


class LocalReranker:
    def __init__(self, base_url: str, model: str, timeout: float, client: httpx.Client | None = None) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/rerank"
        self._client = client or httpx.Client(timeout=timeout)  # reused: a new client costs ~200 ms

    def score(self, question: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        response = self._client.post(
            self._url, json={"model": self.model, "query": question, "documents": list(passages)}
        )
        response.raise_for_status()
        scores = [float("-inf")] * len(passages)
        for item in response.json()["results"]:
            scores[int(item["index"])] = float(item["relevance_score"])
        return scores


def build_reranker(settings: Settings) -> Reranker | None:
    if settings.reranker_provider == "none" or not settings.reranker_base_url:
        return None
    return LocalReranker(
        settings.reranker_base_url, settings.reranker_model, settings.reranker_timeout_seconds
    )


@lru_cache
def get_reranker() -> Reranker | None:
    return build_reranker(get_settings())
