"""Structure-aware chunking (blueprint §7.3).

- Chunks never cross a section: each heading path is chunked on its own, and the path is prefixed to the
  chunk ("Returns > Laptops") so the embedding and the LLM both see where the text came from.
- Within a section, whole paragraphs are packed up to the target size. A paragraph that is too large is split
  between sentences, and only a single over-long sentence is ever split between words. Tables are split
  between rows, repeating the header row.
- Consecutive chunks of one section overlap by whole trailing sentences (never part of one).
- Sizes are estimated tokens (`estimate_tokens`), checked against the target, max and overlap settings.
  The estimate will be replaced by the embedding model's tokenizer once the provider is chosen (§19).
"""

import hashlib
import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from app.ingestion.parsing import Segment

BREADCRUMB_SEPARATOR = " > "
MAX_BREADCRUMB_CHARS = 200
# Split after ., ! or ? (and closing quotes/brackets) when the next sentence starts with a capital or digit.
_SENTENCE_END = re.compile(r"(?<=[.!?\u0964])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9\u0980-\u09ff])")


def estimate_tokens(text: str) -> int:
    """~4 characters per token for ASCII text; non-Latin scripts (e.g. Bengali) tokenize far denser."""
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return max(1, math.ceil(ascii_chars / 4 + (len(text) - ascii_chars) / 2))


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    text: str  # what is embedded, indexed and shown as evidence: breadcrumb + body
    page_start: int
    page_end: int
    section: str | None
    token_count: int

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    page: int
    tokens: int
    joins_previous: bool = False  # True: continues the previous unit's paragraph (join with a space)


def chunk_segments(
    segments: Sequence[Segment], target_tokens: int, max_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, group in _sections(segments):
        breadcrumb = BREADCRUMB_SEPARATOR.join(path)[:MAX_BREADCRUMB_CHARS] or None
        prefix_tokens = estimate_tokens(breadcrumb + "\n\n") if breadcrumb else 0
        body_max = max(max_tokens - prefix_tokens, 50)
        body_target = min(max(target_tokens - prefix_tokens, 50), body_max)
        overlap = min(overlap_tokens, body_max // 4)
        units = [u for seg in group for u in _units(seg, body_max - overlap)]
        for body, first, last in _pack(units, body_target, body_max, overlap):
            text = f"{breadcrumb}\n\n{body}" if breadcrumb else body
            chunks.append(Chunk(len(chunks), text, first, last, breadcrumb, estimate_tokens(text)))
    return chunks


def _sections(segments: Sequence[Segment]) -> Iterator[tuple[tuple[str, ...], list[Segment]]]:
    """Runs of body segments that share one heading path, in document order."""
    path: tuple[str, ...] | None = None
    group: list[Segment] = []
    for seg in segments:
        if seg.kind == "heading" or seg.section_path != path:
            if group and path is not None:
                yield path, group
            group, path = [], seg.section_path
        if seg.kind != "heading":
            group.append(seg)
    if group and path is not None:
        yield path, group


def _units(seg: Segment, limit: int) -> Iterator[_Unit]:
    tokens = estimate_tokens(seg.text)
    if tokens <= limit:
        yield _Unit(seg.text, seg.page, tokens)
    elif seg.kind == "table":
        yield from _table_units(seg, limit)
    else:
        first = True
        for piece in _pack_text(split_sentences(seg.text), limit, " "):
            yield _Unit(piece, seg.page, estimate_tokens(piece), joins_previous=not first)
            first = False


def _table_units(seg: Segment, limit: int) -> Iterator[_Unit]:
    header, *rows = seg.text.split("\n")
    budget = max(limit - estimate_tokens(header), limit // 2)
    for piece in _pack_text(rows, budget, "\n"):
        text = f"{header}\n{piece}"
        yield _Unit(text, seg.page, estimate_tokens(text))


def _pack_text(parts: Sequence[str], limit: int, joiner: str) -> Iterator[str]:
    """Greedily join parts up to `limit` tokens; a part that is too large alone is split between words."""
    current: list[str] = []
    for part in parts:
        for piece in _split_words(part, limit) if estimate_tokens(part) > limit else [part]:
            if current and estimate_tokens(joiner.join([*current, piece])) > limit:
                yield joiner.join(current)
                current = []
            current.append(piece)
    if current:
        yield joiner.join(current)


def _split_words(text: str, limit: int) -> Iterator[str]:
    words: list[str] = []
    for word in text.split():
        if words and estimate_tokens(" ".join([*words, word])) > limit:
            yield " ".join(words)
            words = []
        words.append(word)
    if words:
        yield " ".join(words)


def _pack(units: list[_Unit], target: int, maximum: int, overlap: int) -> Iterator[tuple[str, int, int]]:
    """Yield (body, first page, last page). Flush when the next unit would pass the max, or would pass the
    target while the chunk is already at least half the target (avoids tiny chunks)."""
    current: list[_Unit] = []
    size = 0
    for unit in units:
        if current and (
            size + unit.tokens > maximum or (size + unit.tokens > target and size >= target // 2)
        ):
            yield _render(current)
            tail = _overlap_tail(current, overlap)
            current = [tail] if tail and tail.tokens + unit.tokens <= maximum else []
            size = sum(u.tokens for u in current)
        current.append(unit)
        size += unit.tokens
    if current:
        yield _render(current)


def _render(units: list[_Unit]) -> tuple[str, int, int]:
    body = units[0].text
    for unit in units[1:]:
        body += (" " if unit.joins_previous else "\n\n") + unit.text
    return body, min(u.page for u in units), max(u.page for u in units)


def _overlap_tail(units: list[_Unit], overlap: int) -> _Unit | None:
    """Whole sentences from the end of the chunk, up to `overlap` tokens. Tables are not overlapped."""
    if overlap <= 0 or "\n" in units[-1].text:
        return None
    sentences = split_sentences(units[-1].text)
    tail: list[str] = []
    for sentence in reversed(sentences):
        if estimate_tokens(" ".join([sentence, *tail])) > overlap:
            break
        tail.insert(0, sentence)
    if not tail:
        return None
    text = " ".join(tail)
    return _Unit(text, units[-1].page, estimate_tokens(text))
