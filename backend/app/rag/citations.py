"""Citation validation (§8.3 node 7, gate G4). The last check before anything is shown as "answered".

An answer is accepted only if every citation and every [Sn] marker points at a source that was retrieved for
this principal, it cites at least one source, and it introduces no URL or page number that its sources don't
contain. Quotes must appear verbatim in the cited source; a quote that doesn't is replaced with the source
sentence that best supports the answer, so the user never sees an invented quote. Markers are renumbered to
the displayed [1], [2], ….
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from app.rag.schemas import GeneratedAnswer, Source
from app.rag.text import content_terms, normalize_for_match, statements

_MARKER = re.compile(r"\[(S\d+)\]")
_URL = re.compile(r"https?://[^\s)\]]+|www\.[^\s)\]]+", re.IGNORECASE)
_PAGE = re.compile(r"\b(?:page|pages|p\.|pp\.)\s*(\d{1,4})", re.IGNORECASE)
MAX_QUOTE_CHARS = 300


@dataclass(slots=True)
class Validated:
    valid: bool
    answer: str = ""
    citations: list[tuple[Source, str]] = field(
        default_factory=list
    )  # (source, verified quote) in display order
    problems: list[str] = field(default_factory=list)


def validate(
    generated: GeneratedAnswer, sources: Sequence[Source], allowed_audiences: Collection[str]
) -> Validated:
    by_id = {s.source_id: s for s in sources}
    problems: list[str] = []

    if generated.answer_status != "answered":
        return Validated(False, problems=["model reported insufficient evidence"])
    if not generated.answer.strip():
        return Validated(False, problems=["empty answer"])

    cited_ids = [c.source_id for c in generated.citations]
    marker_ids = _MARKER.findall(generated.answer)
    unknown = sorted({i for i in [*cited_ids, *marker_ids] if i not in by_id})
    if unknown:
        return Validated(False, problems=[f"unknown source ids: {', '.join(unknown)}"])

    order: list[str] = []
    for source_id in [*marker_ids, *cited_ids]:  # display order: as referenced in the text, then the rest
        if source_id not in order:
            order.append(source_id)
    if not order:
        return Validated(False, problems=["no citations"])

    cited = [by_id[i] for i in order]
    leaked = [s.source_id for s in cited if s.audience not in allowed_audiences]
    if leaked:  # retrieval already filters; this is the last line of defense (G5)
        return Validated(False, problems=[f"sources outside the caller's audiences: {leaked}"])

    source_text = normalize_for_match(" ".join(s.text for s in cited))
    for url in _URL.findall(generated.answer):
        if normalize_for_match(url.rstrip(".,;")) not in source_text:
            return Validated(False, problems=[f"URL not in sources: {url}"])
    pages = {p for s in cited for p in range(s.page_start, s.page_end + 1)}
    for page in _PAGE.findall(generated.answer):
        if int(page) not in pages:
            return Validated(False, problems=[f"page {page} is not a cited page"])

    quotes: dict[str, str] = {}
    for cited_source in generated.citations:  # one chip per source: keep its first (most relevant) quote
        quotes.setdefault(cited_source.source_id, cited_source.quote)
    result: list[tuple[Source, str]] = []
    for source in cited:
        quote = quotes.get(source.source_id, "").strip()
        if not quote or not _supported(quote, source):
            if quote:
                problems.append(f"{source.source_id}: quote not found verbatim; replaced")
            quote = best_sentence(source, generated.answer)
        result.append((source, quote[:MAX_QUOTE_CHARS]))

    numbers = {source_id: str(n) for n, source_id in enumerate(order, start=1)}
    answer = _MARKER.sub(lambda m: f"[{numbers[m.group(1)]}]", generated.answer).strip()
    if not marker_ids:
        # Small local models often list citations without marking sentences: the whole answer then rests on
        # all of its (validated) citations, which is what the appended markers say.
        answer = f"{answer} " + "".join(f"[{numbers[i]}]" for i in order)
    return Validated(True, answer, result, problems)


def _supported(quote: str, source: Source) -> bool:
    """Verbatim in the source, or one of its statements (table rows are quoted as "Header: value; …")."""
    wanted = normalize_for_match(quote)
    if wanted in normalize_for_match(source.text):
        return True
    return any(wanted == normalize_for_match(s) for s in statements(source.text, source.section))


def best_sentence(source: Source, answer: str) -> str:
    """The source statement sharing the most content terms with the answer."""
    terms = content_terms(answer)
    candidates = statements(source.text, source.section) or [source.text]
    return max(candidates, key=lambda s: len(terms & content_terms(s)))
