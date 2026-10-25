"""Keyword retrieval and fusion (§8.5 hybrid search, ADR-028).

Vector search finds meaning but can miss an exact code ("H-07", "P-11") or a rare product word; keyword
search finds those. Postgres full-text search ranks chunks by the question's content words (any of them,
`ts_rank_cd`), and reciprocal rank fusion merges both lists: an item's score is the sum of 1 / (k + rank) over
the lists it is in. Authorization is the same as for the vector search: the query runs in the tenant's session
(RLS) and only over the active version IDs the caller may see (ADR-003/004), and the chunks it finds are then
fetched through the Principal-filtered index, never directly.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, desc, func, select
from sqlalchemy.orm import Session

from app.db.models import DocumentChunk
from app.integrations.chroma import RetrievedChunk
from app.rag.text import normalize_for_match, ordered_terms


def keyword_query(question: str) -> ColumnElement[Any] | None:
    """`plainto_tsquery` per content word, OR-ed: a chunk needs any of them, and more matches rank higher."""
    words = ordered_terms(question)
    if not words:
        return None
    query: ColumnElement[Any] = func.plainto_tsquery("english", words[0])
    for word in words[1:]:
        query = query.op("||")(func.plainto_tsquery("english", word))
    return query


def names_a_code(question: str) -> bool:
    """Keyword search runs only for questions naming a code or model number ("E-04", "H-07", "X2"): what it
    is for (§8.5 "exact part numbers and error codes"). On plain-word questions it surfaced chunks that share
    every word but answer something else (golden-v1 G128 with the offline model); vector search covers those.
    """
    terms = ordered_terms(question)
    return any(any(c.isdigit() for c in t) and any(c.isalpha() for c in t) for t in terms)


def search(session: Session, question: str, active_version_ids: Sequence[uuid.UUID], limit: int) -> list[str]:
    """Chroma IDs of the best keyword matches, best first. Empty when nothing is published or matches."""
    query = keyword_query(question)
    if query is None or not active_version_ids:
        return []
    rank = func.ts_rank_cd(DocumentChunk.search_vector, query)
    rows = session.execute(
        select(DocumentChunk.chroma_id)
        .where(DocumentChunk.version_id.in_(active_version_ids), DocumentChunk.search_vector.op("@@")(query))
        .order_by(desc(rank), DocumentChunk.chroma_id)
        .limit(limit)
    )
    return list(rows.scalars())


def fuse(
    vector_hits: Sequence[RetrievedChunk],
    keyword_ids: Sequence[str],
    keyword_hits: Sequence[RetrievedChunk],
    *,
    k: int,
    limit: int,
) -> list[RetrievedChunk]:
    """Reciprocal rank fusion of both lists, best first, without near-duplicates (the same text twice, e.g.
    one paragraph pasted into two documents, would only crowd out another source)."""
    by_id = {hit.chroma_id: hit for hit in [*keyword_hits, *vector_hits]}
    scores: dict[str, float] = {}
    for ranking in ([hit.chroma_id for hit in vector_hits], [i for i in keyword_ids if i in by_id]):
        for rank, chroma_id in enumerate(ranking, start=1):
            scores[chroma_id] = scores.get(chroma_id, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores, key=lambda i: (-scores[i], by_id[i].distance, i))
    fused: list[RetrievedChunk] = []
    seen: set[str] = set()
    for chroma_id in ordered:
        text = normalize_for_match(by_id[chroma_id].text)
        if text in seen:
            continue
        seen.add(text)
        fused.append(by_id[chroma_id])
        if len(fused) == limit:
            break
    return fused
