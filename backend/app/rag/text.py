"""Small, deterministic text helpers shared by intent detection, evidence grading and the offline generator.

Content terms are lowercase word tokens minus stopwords, with light suffix stripping so "laptops"/"laptop" and
"covered"/"cover" match. Both sides of every comparison go through the same function, so the stemming only has
to be consistent, not linguistically correct. English only for now; Bangla support is P1 (§2.2).
"""

import re

from app.ingestion.chunking import split_sentences

BREAK = chr(10) * 2  # between a chunk's breadcrumb and its body
_TOKEN = re.compile(r"[a-z0-9]+(?:[-'+][a-z0-9]+)*\+?")
STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are as at be because been before being below between
    both but by can could did do does doing down during each few for from further had has have having he her
    here hers herself him himself his how i if in into is it its itself just me more most my myself no nor not
    now of off on once only or other our ours ourselves out over own same she should so some such than that
    the their theirs them themselves then there these they this those through to too under until up very was
    we were what when where which while who whom why will with would you your yours yourself yourselves
    please tell know get got want need let lets like also much many may might must shall us via per
    techcare
    """.split()
)


def _undouble(stem: str) -> str:
    # "jammed" → "jamm" → "jam", "stopping" → "stopp" → "stop" (but keep "ll", "ss", "zz": "called", "passed")
    if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "aeioulsz":
        return stem[:-1]
    return stem


def stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return _undouble(word[:-3])
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("ed"):
        return _undouble(word[:-2])
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def content_terms(text: str) -> set[str]:
    return {stem(t.rstrip("+")) for t in _TOKEN.findall(text.lower()) if t.rstrip("+") not in STOPWORDS}


def ordered_terms(text: str) -> list[str]:
    """Content words in their original order and form (for keyword rewrites)."""
    seen: set[str] = set()
    words: list[str] = []
    for token in _TOKEN.findall(text.lower()):
        if token not in STOPWORDS and token not in seen:
            seen.add(token)
            words.append(token)
    return words


def coverage(terms: set[str], text: str) -> float:
    """Share of `terms` that appear in `text`."""
    if not terms:
        return 0.0
    return len(terms & content_terms(text)) / len(terms)


def body_of(chunk_text: str, section: str | None) -> str:
    """Chunk text without its "Section > Subsection" breadcrumb (prefixed by app/ingestion/chunking.py)."""
    prefix = f"{section}{BREAK}" if section else ""
    return chunk_text[len(prefix) :] if prefix and chunk_text.startswith(prefix) else chunk_text


def statements(chunk_text: str, section: str | None = None) -> list[str]:
    """Sentences, with table rows rewritten as "Header: value; …" so a row reads as a statement."""
    out: list[str] = []
    header: list[str] | None = None
    for line in body_of(chunk_text, section).split("\n"):
        line = line.strip()
        if not line:
            header = None
            continue
        if " | " in line:
            cells = [c.strip() for c in line.split(" | ")]
            if header is None:
                header = cells
                continue
            out.append("; ".join(f"{h}: {c}" for h, c in zip(header, cells, strict=False) if c))
            continue
        header = None
        out.extend(split_sentences(line))
    return out


def normalize_for_match(text: str) -> str:
    return " ".join(text.lower().split())
