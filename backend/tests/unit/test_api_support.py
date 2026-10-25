"""§9 support code: personal-data redaction, signed file links, the range label, the frozen contract."""

import uuid
from datetime import date

import jwt
import pytest

from app.api.analytics import _label
from app.cli.contracts import OPENAPI_PATH, SCHEMA_PATH, current_openapi, current_schema, render
from app.config import get_settings
from app.core import links
from app.core.privacy import redact_personal
from app.core.security import issue_token

SETTINGS = get_settings()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Call me at 555-201-3344 about order TC-123456", "Call me at [phone] about order [order number]"),
        ("my email is jo.doe+x@mail.example.com", "my email is [email]"),
        ("phone +1 (312) 555-0199 please", "phone [phone] please"),
        ("card 4111 1111 1111 1111", "card [redacted payment card]"),
        ("SSN 123-45-6789", "SSN [redacted national ID]"),
    ],
)
def test_personal_data_is_redacted(text: str, expected: str) -> None:
    assert redact_personal(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Is the TC-Book 14 under 1,299 dollars?",
        "Does DemoPrinter-X2 support 2.4 GHz?",
        "Return within 30 days",
    ],
)
def test_product_text_is_left_alone(text: str) -> None:
    assert redact_personal(text) == text


def test_file_links_round_trip_and_expire() -> None:
    org, version = uuid.uuid4(), uuid.uuid4()
    token, _ = links.sign(SETTINGS, org, version)
    assert links.verify(SETTINGS, token) == links.FileLink(org, version)
    expired, _ = links.sign(SETTINGS.model_copy(update={"preview_link_seconds": -1}), org, version)
    with pytest.raises(links.InvalidLink):
        links.verify(SETTINGS, expired)


def test_file_links_and_session_tokens_are_not_interchangeable() -> None:
    session_token, _ = issue_token(SETTINGS, uuid.uuid4(), uuid.uuid4())
    with pytest.raises(links.InvalidLink):
        links.verify(SETTINGS, session_token)  # different key and audience
    forged = jwt.encode(
        {"org": str(uuid.uuid4()), "ver": str(uuid.uuid4()), "exp": 9999999999, "aud": links.AUDIENCE},
        SETTINGS.auth_jwt_secret.get_secret_value(),  # the session key is not the link key
        algorithm="HS256",
    )
    with pytest.raises(links.InvalidLink):
        links.verify(SETTINGS, forged)


def test_range_labels_follow_the_dashboard_copy() -> None:
    dash = chr(0x2013)
    assert _label(date(2026, 9, 1), date(2026, 9, 30), "America/Chicago") == (
        f"Showing 1{dash}30 Sep 2026 · store time (America/Chicago)"
    )
    assert (
        _label(date(2026, 9, 25), date(2026, 10, 1), "UTC")
        == f"Showing 25 Sep {dash} 1 Oct 2026 · store time (UTC)"
    )
    assert _label(date(2025, 12, 30), date(2026, 1, 2), "UTC") == (
        f"Showing 30 Dec 2025 {dash} 2 Jan 2026 · store time (UTC)"
    )


def test_the_voice_contract_matches_its_frozen_schema() -> None:
    """§9.2 "version this contract": changing the voice models without a reviewed schema update fails here."""
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(current_schema())
    response = current_schema()["response"]
    assert set(response["required"]) >= {"answer", "answer_status", "speech_summary", "citations", "trace_id"}
    assert response["properties"]["contract_version"]["const"] == "knowledge-answer.v1"


def test_the_committed_openapi_document_matches_the_routes() -> None:
    """§18 "API OpenAPI documentation": docs/contracts/openapi.json is regenerated with every API change."""
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render(current_openapi())
    paths = current_openapi()["paths"]
    assert "/api/v1/chat/ask/stream" in paths and "/api/v1/internal/knowledge/answer" in paths
