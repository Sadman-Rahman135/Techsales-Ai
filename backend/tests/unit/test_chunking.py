"""Blueprint §7.3: structure-aware chunks within size limits, sentence-safe splits, overlap, stable hashes."""

import itertools
import re

from app.ingestion.chunking import chunk_segments, estimate_tokens, split_sentences
from app.ingestion.parsing import Segment

TARGET, MAX, OVERLAP = 500, 700, 75
PATH = ("Warranty", "Laptops")
BREAK = chr(10) * 2  # blank line between the breadcrumb and the body


def sentence(n: int) -> str:
    return f"Clause {n} says the laptop warranty covers part {n} for twelve months from the purchase date."


def body(text: str, page: int = 1, path: tuple[str, ...] = PATH) -> Segment:
    return Segment("paragraph", text, page, path)


def heading(text: str, path: tuple[str, ...], page: int = 1) -> Segment:
    return Segment("heading", text, page, path, len(path))


def test_small_sections_become_one_chunk_with_a_breadcrumb() -> None:
    chunks = chunk_segments([heading("Laptops", PATH), body("Covered for 12 months.")], TARGET, MAX, OVERLAP)
    assert [c.text for c in chunks] == ["Warranty > Laptops\n\nCovered for 12 months."]
    assert chunks[0].section == "Warranty > Laptops"


def test_chunks_never_cross_a_section() -> None:
    other = ("Warranty", "Phones")
    chunks = chunk_segments(
        [
            heading("Laptops", PATH),
            body("Laptops: 12 months."),
            heading("Phones", other),
            body("Phones: 6 months.", path=other),
        ],
        TARGET,
        MAX,
        OVERLAP,
    )
    assert [c.section for c in chunks] == ["Warranty > Laptops", "Warranty > Phones"]


def test_long_sections_respect_the_size_limits() -> None:
    paragraphs = [body(" ".join(sentence(p * 10 + i) for i in range(6)), page=p + 1) for p in range(20)]
    chunks = chunk_segments(paragraphs, TARGET, MAX, OVERLAP)
    assert len(chunks) > 3
    assert all(c.token_count <= MAX for c in chunks)
    assert all(
        c.token_count >= TARGET // 3 for c in chunks[:-1]
    )  # no tiny fragments except possibly the last
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_splits_happen_between_sentences_never_inside_one() -> None:
    sentences = [sentence(i) for i in range(80)]
    chunks = chunk_segments([body(" ".join(sentences))], TARGET, MAX, OVERLAP)
    for chunk in chunks:
        text = chunk.text.split("\n\n", 1)[1]
        assert text.startswith("Clause ") and text.endswith("months from the purchase date.")


def test_consecutive_chunks_overlap_by_whole_sentences() -> None:
    chunks = chunk_segments([body(" ".join(sentence(i) for i in range(80)))], TARGET, MAX, OVERLAP)
    assert len(chunks) > 2
    for first, second in itertools.pairwise(chunks):
        before = split_sentences(first.text.split(BREAK, 1)[1])
        after = split_sentences(second.text.split(BREAK, 1)[1])
        shared = next(k for k in range(len(after), 0, -1) if before[-k:] == after[:k])
        assert 1 <= shared < len(after)  # the next chunk starts with the previous chunk's last sentences
        assert sum(estimate_tokens(s) for s in after[:shared]) <= OVERLAP


def test_page_ranges_follow_the_source() -> None:
    chunks = chunk_segments(
        [body(" ".join(sentence(p * 10 + i) for i in range(8)), page=p) for p in (4, 5, 6, 7, 8)],
        TARGET,
        MAX,
        OVERLAP,
    )
    assert chunks[0].page_start == 4
    assert chunks[-1].page_end == 8
    assert all(c.page_start <= c.page_end for c in chunks)


def test_a_single_huge_sentence_is_split_between_words() -> None:
    giant = "warranty " * 2000
    chunks = chunk_segments([body(giant.strip() + ".")], TARGET, MAX, OVERLAP)
    assert len(chunks) > 1
    assert all(c.token_count <= MAX for c in chunks)
    assert all(re.fullmatch(r"[a-z .>A-Z\n]+", c.text) for c in chunks)  # whole words only


def test_large_tables_split_between_rows_and_repeat_the_header() -> None:
    rows = ["Model | Warranty | Accidental damage"] + [f"TC-{n:04d} | 12 months | No" for n in range(400)]
    chunks = chunk_segments([Segment("table", "\n".join(rows), 3, PATH)], TARGET, MAX, OVERLAP)
    assert len(chunks) > 1
    for chunk in chunks:
        lines = chunk.text.split("\n\n", 1)[1].split("\n")
        assert lines[0] == "Model | Warranty | Accidental damage"
        assert all(re.fullmatch(r"TC-\d{4} \| 12 months \| No", line) for line in lines[1:])
        assert chunk.token_count <= MAX
    covered = {line for c in chunks for line in c.text.split("\n")[3:]}
    assert covered == set(rows[1:])  # every row survives


def test_checksums_are_stable_and_content_addressed() -> None:
    segments = [body("Covered for 12 months.")]
    first = chunk_segments(segments, TARGET, MAX, OVERLAP)[0]
    again = chunk_segments(segments, TARGET, MAX, OVERLAP)[0]
    changed = chunk_segments([body("Covered for 24 months.")], TARGET, MAX, OVERLAP)[0]
    assert first.checksum == again.checksum != changed.checksum
    assert re.fullmatch(r"[0-9a-f]{64}", first.checksum)


def test_token_estimate_counts_non_latin_scripts_denser() -> None:
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("\u0995" * 400) == 200
