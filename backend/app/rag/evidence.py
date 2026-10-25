"""Evidence grading (§8.3 node 4). Deterministic on purpose: it decides whether to spend an LLM call, and it
is never a security decision (authorization already happened in retrieval, ADR-004).

A source is relevant when it is similar enough to the question *and* contains most of the question's content
terms. Similarity alone lets topic-adjacent chunks through ("warranty" questions about products we don't
sell); term coverage alone ignores meaning. Both thresholds are settings and get tuned on the golden set
(§11). Two refinements found with golden-v1 (§12):

- A question term that *no* candidate mentions weighs double: "How many vacation days do employees get?" must
  not be answered from "90 days of employment" just because two of three words match (G123). One typo among
  several matching words still passes (the typo cases).
- When the question names a product model, sources about a *different* model are not evidence: a
  DemoPrinter-X2 question is never answered from the DemoPrinter-X2 Pro guide (G075). "DemoPrinter-X2 Pro"
  in the question names the Pro, not both. Sources without a product model (policies) always stay.

And one from §15 (top evaluation misses, found on exploration queries, ADR-022):

- A misspelt word one edit away from a source word counts as that word ("expyre" → "expire"), so a typo is
  neither missing nor "unknown to every candidate". Only plain words of 5+ letters: codes and model numbers
  ("E-04", "H1", "X2") and short words ("card"/"care") must match exactly.
- A code the question names (letters and digits: "H9", "E-04") that no candidate mentions means no evidence:
  "What does error H9 mean?" was answered with the H-07 row because the other five words matched (X16).
  Plain numbers ("45 days") are not codes.
"""

import re
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from app.rag.schemas import Source
from app.rag.text import content_terms


@dataclass(frozen=True, slots=True)
class Grade:
    sufficient: bool
    sources: list[Source]  # relevant sources, best first, renumbered S1…Sn
    reason: str


def grade(
    question: str,
    candidates: Sequence[Source],
    active_version_ids: Collection[uuid.UUID],
    *,
    min_similarity: float,
    min_coverage: float,
    limit: int,
    relaxed: tuple[float, float] | None = None,
) -> Grade:
    """`relaxed` = (reranker score, coverage): a source the reranker scores at least that high passes with the
    lower coverage (ADR-028, off by default). Reranked candidates keep the reranker's order."""
    terms = content_terms(question)
    if not terms:
        return Grade(False, [], "no content terms in the question")
    active = set(active_version_ids)
    seen = set().union(*(vocabulary(s.text) for s in candidates)) if candidates else set()
    unknown_codes = sorted(t for t in terms if is_code(t) and t not in seen)
    if unknown_codes:
        return Grade(False, [], f"no candidate mentions {', '.join(unknown_codes)}")
    # Unknown to every candidate (not even as a typo): weighs double.
    weights = {t: 1.0 if mentions(seen, t) else 2.0 for t in terms}

    def covered(source: Source) -> float:
        present = vocabulary(source.text)
        return sum(w for t, w in weights.items() if mentions(present, t)) / sum(weights.values())

    named = mentioned_products(question, {s.product_model for s in candidates if s.product_model})

    def enough(source: Source) -> bool:
        if covered(source) >= min_coverage:
            return True
        score = source.rerank_score
        if relaxed is None or score is None:
            return False
        return score >= relaxed[0] and covered(source) >= relaxed[1]

    def allowed(source: Source) -> bool:
        # Defense in depth: a source from a version that is no longer active never counts (G6).
        return (
            source.version_id in active
            and source.similarity >= min_similarity
            and (not named or source.product_model is None or source.product_model.lower() in named)
        )

    relevant = [s for s in candidates if allowed(s) and enough(s)]
    if not relevant:
        best = max((covered(s) for s in candidates), default=0.0)
        return Grade(False, [], f"{len(candidates)} candidates, best term coverage {best:.2f}")
    if all(s.rerank_score is not None for s in relevant):
        relevant.sort(key=lambda s: s.rerank_score or 0.0, reverse=True)
    else:
        relevant.sort(key=lambda s: (covered(s), s.similarity), reverse=True)
    relevant += complementary(terms, relevant, [s for s in candidates if s not in relevant and allowed(s)])
    kept = [s.model_copy(update={"source_id": f"S{i}"}) for i, s in enumerate(relevant[:limit], start=1)]
    return Grade(True, kept, f"{len(relevant)} relevant of {len(candidates)}")


TYPO_MIN_LENGTH = 5
COMPLEMENT_MIN_TERMS = 2  # a second document must add at least this many of the question's missing words …
COMPLEMENT_MIN_SHARE = 0.5  # … and at least this share of them


def complementary(terms: set[str], kept: Sequence[Source], others: Sequence[Source]) -> list[Source]:
    """Sources that answer the *rest* of a two-part question (§8.5 multi-document, exploration X32): once one
    source passed, another one that covers most of the words the passing sources don't is evidence too. Each
    part of "What warranty comes with a screen repair, and how long does Care+ cover accidental damage?" lives
    in its own document, so neither document alone reaches the coverage threshold. Only ever adds to an
    answer that already has evidence; never makes an unanswerable question answerable."""
    if not kept:
        return []
    added: list[Source] = []
    covered_terms = {t for t in terms for s in kept if mentions(vocabulary(s.text), t)}
    for source in others:
        missing = terms - covered_terms
        gained = {t for t in missing if mentions(vocabulary(source.text), t)}
        enough_new = len(gained) >= max(COMPLEMENT_MIN_TERMS, COMPLEMENT_MIN_SHARE * len(missing))
        if missing and enough_new:
            added.append(source)
            covered_terms |= gained
    return added


def vocabulary(text: str) -> set[str]:
    """A source's content terms, plus the parts and the joined form of hyphenated ones: a question's "X2"
    is in "DemoPrinter-X2", and "E04" is "E-04" (exploration X17, X21). "H9" still isn't in "H-07"."""
    terms = content_terms(text)
    extra = {part for term in terms if "-" in term for part in term.split("-") if part}
    extra |= {term.replace("-", "") for term in terms if "-" in term}
    return terms | extra


def is_code(term: str) -> bool:
    """An identifier such as an error code or model number: letters and digits together."""
    return any(c.isdigit() for c in term) and any(c.isalpha() for c in term)


def mentions(vocabulary: Collection[str], term: str) -> bool:
    """`term` is in `vocabulary`, exactly or (for plain longer words) as a one-edit typo of a word in it."""
    if term in vocabulary:
        return True
    if len(term) < TYPO_MIN_LENGTH or not term.isalpha():
        return False
    return any(
        len(word) >= TYPO_MIN_LENGTH - 1 and word.isalpha() and one_edit(term, word) for word in vocabulary
    )


def one_edit(a: str, b: str) -> bool:
    """Exactly one insertion, deletion, substitution or swap of neighbours turns `a` into `b`."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) < len(b):  # one letter inserted at i
        return a[i:] == b[i + 1 :]
    if a[i + 1 :] == b[i + 1 :]:  # one letter substituted at i
        return True
    return a[i] == b[i + 1] and a[i + 1] == b[i] and a[i + 2 :] == b[i + 2 :]  # neighbours swapped


def mentioned_products(question: str, models: set[str]) -> set[str]:
    """Product models named in the question (lower-case). A model that only appears inside a longer named one
    ("DemoPrinter-X2" inside "DemoPrinter-X2 Pro") doesn't count."""
    found = {
        m.lower() for m in models if re.search(rf"(?<![\w-]){re.escape(m)}(?![\w-])", question, re.IGNORECASE)
    }
    return {m for m in found if not any(m != other and m in other for other in found)}
