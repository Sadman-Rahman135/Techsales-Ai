"""Blueprint §7.2: reading order, hierarchy, page references, headers/footers, tables, empty pages."""

import pytest

from app.ingestion.errors import ErrorCode, IngestionError
from app.ingestion.parsing import parse_document
from tests.builders import docx_bytes, pdf_bytes, scanned_pdf_bytes, warranty_pdf


def test_pdf_segments_keep_heading_path_and_page() -> None:
    parsed = parse_document(warranty_pdf(), "pdf")
    headings = [(s.level, s.text, s.page) for s in parsed.segments if s.kind == "heading"]
    assert headings == [
        (1, "TechCare Laptop Warranty", 1),
        (2, "Coverage period", 1),
        (2, "What is not covered", 2),
        (2, "How to claim", 3),
    ]
    body = next(s for s in parsed.segments if s.text.startswith("Bring the laptop"))
    assert body.page == 3
    assert body.section_path == ("TechCare Laptop Warranty", "How to claim")
    assert parsed.page_count == 3


def test_pdf_running_headers_and_page_numbers_are_removed() -> None:
    text = " ".join(s.text for s in parse_document(warranty_pdf(), "pdf").segments)
    assert "internal copy" not in text
    assert "Page 2 of 3" not in text


def test_pdf_tables_are_kept_row_by_row_in_reading_order() -> None:
    segments = parse_document(warranty_pdf(), "pdf").segments
    table = next(s for s in segments if s.kind == "table")
    assert table.text.split("\n") == [
        "Plan | Length | Accidental damage",
        "Standard | 12 months | No",
        "Care+ | 24 months | Yes",
    ]
    assert table.page == 2
    kinds = [s.kind for s in segments if s.page == 2]
    assert kinds == ["heading", "paragraph", "table"]  # the table follows the paragraph above it


def test_a_pdf_table_cell_that_wraps_stays_one_value() -> None:
    long = "Hold the reset button for 10 seconds and pair again after the light turns blue"
    rows = f"Code|Meaning|What to do\nH-03|Pairing timed out|{long}\nH-07|Failed|Restart"
    data = pdf_bytes([[("table", rows)]])
    table = next(s for s in parse_document(data, "pdf").segments if s.kind == "table")
    assert table.text.split("\n") == [
        "Code | Meaning | What to do",
        f"H-03 | Pairing timed out | {long}",  # nothing cut at the cell edge, no stray row
        "H-07 | Failed | Restart",
    ]


def test_pdf_pages_without_text_are_flagged() -> None:
    data = pdf_bytes([[("p", "Page one has text.")], [], [("p", "Page three has text.")]])
    parsed = parse_document(data, "pdf")
    assert {"code": "PAGES_WITHOUT_TEXT", "pages": [2]} in parsed.warnings


def test_scanned_pdf_without_any_text_fails_with_a_clear_code() -> None:
    with pytest.raises(IngestionError) as info:
        parse_document(scanned_pdf_bytes(), "pdf")
    assert info.value.code is ErrorCode.FILE_NO_TEXT
    assert info.value.retryable is False


def test_secrets_are_redacted_and_counted_but_never_recorded() -> None:
    card = "4111 1111 1111 1111"
    data = pdf_bytes([[("h1", "Payments"), ("p", f"Test card {card} must never be used in store.")]])
    parsed = parse_document(data, "pdf")
    assert card not in " ".join(s.text for s in parsed.segments)
    assert parsed.warnings == [{"code": "REDACTED", "counts": {"payment card": 1}}]


def test_docx_headings_lists_tables_and_page_breaks() -> None:
    data = docx_bytes(
        [
            ("title", "Returns policy"),
            ("p", "You can return most items within 30 days."),
            ("h1", "Laptops"),
            ("list", "Unopened laptops get a full refund"),
            ("pagebreak", ""),
            ("h2", "Opened laptops"),
            ("p", "Opened laptops have a 15% restocking fee."),
            ("table", "Condition|Refund\nUnopened|100%\nOpened|85%"),
        ]
    )
    parsed = parse_document(data, "docx")
    by_text = {s.text: s for s in parsed.segments}
    assert by_text["- Unopened laptops get a full refund"].section_path == ("Returns policy", "Laptops")
    assert by_text["- Unopened laptops get a full refund"].page == 1
    opened = by_text["Opened laptops have a 15% restocking fee."]
    assert opened.section_path == ("Returns policy", "Laptops", "Opened laptops")
    assert opened.page == 2
    table = next(s for s in parsed.segments if s.kind == "table")
    assert table.text == "Condition | Refund\nUnopened | 100%\nOpened | 85%"
    assert parsed.page_count == 2


def test_docx_with_only_headings_has_no_text() -> None:
    with pytest.raises(IngestionError) as info:
        parse_document(docx_bytes([("h1", "Heading only")]), "docx")
    assert info.value.code is ErrorCode.FILE_NO_TEXT


def test_txt_pages_and_markdown_headings() -> None:
    text = "# Store hours\nWe open at 9am.\n\n## Holidays\nClosed on public holidays.\f# Contact\nCall us."
    parsed = parse_document(text.encode(), "txt")
    segments = [(s.kind, s.text, s.page, s.section_path) for s in parsed.segments]
    assert segments == [
        ("heading", "Store hours", 1, ("Store hours",)),
        ("paragraph", "We open at 9am.", 1, ("Store hours",)),
        ("heading", "Holidays", 1, ("Store hours", "Holidays")),
        ("paragraph", "Closed on public holidays.", 1, ("Store hours", "Holidays")),
        ("heading", "Contact", 2, ("Contact",)),
        ("paragraph", "Call us.", 2, ("Contact",)),
    ]
    assert parsed.page_count == 2


def test_pages_full_of_unreadable_characters_count_as_low_confidence() -> None:
    from app.ingestion.parsing import LOW_CONFIDENCE_RATIO, _unreadable_ratio

    garbled = "Warranty " + chr(0xFFFD) * 5 + chr(0xE000) * 5  # replacement + private-use glyphs
    assert _unreadable_ratio(garbled) > LOW_CONFIDENCE_RATIO
    assert _unreadable_ratio("Warranty covers 12 months.") == 0
    assert _unreadable_ratio("") == 0
