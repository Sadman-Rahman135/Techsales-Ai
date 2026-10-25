"""CSV export cells (ADR-030): no formulas, the tenant's time, readable lists."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.services.exports import cell, to_csv

ZONE = ZoneInfo("America/Chicago")


def test_cells_a_spreadsheet_would_run_are_quoted() -> None:
    for text in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx", "\rx"):
        assert cell(text, ZONE) == "'" + text
    assert cell("Is 2-1 = 1?", ZONE) == "Is 2-1 = 1?"  # only the first character matters
    assert cell(-5, ZONE) == "-5"  # numbers are data, never quoted


def test_values_are_written_the_way_people_read_them() -> None:
    noon_utc = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)
    assert cell(noon_utc, ZONE) == "2026-10-20T12:00:00-05:00"  # store time
    assert cell(date(2026, 10, 20), ZONE) == "2026-10-20"
    assert cell(["wrong", "=bad"], ZONE) == "wrong; =bad"
    assert cell(None, ZONE) == "" and cell(True, ZONE) == "yes"


def test_the_file_opens_in_spreadsheet_apps() -> None:
    data = to_csv(["question"], [['Café, "open"?']], "UTC")
    assert (
        data.startswith(b"\xef\xbb\xbf") and data.decode("utf-8-sig") == 'question\r\n"Café, ""open""?"\r\n'
    )
