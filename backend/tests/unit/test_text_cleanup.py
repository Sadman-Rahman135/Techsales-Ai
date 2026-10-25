"""Blueprint §7.2: normalization keeps meaning and tables; redaction removes secrets and personal IDs."""

import pytest

from app.ingestion.normalize import normalize_text
from app.ingestion.redaction import luhn_valid, redact

NBSP, SOFT_HYPHEN, ZWSP, ZWJ, ZWNJ, FI = "\u00a0", "\u00ad", "\u200b", "\u200d", "\u200c", "\ufb01"


def test_ligatures_invisible_characters_and_odd_spaces_are_cleaned() -> None:
    raw = f"Warranty{NBSP}co{SOFT_HYPHEN}verage is {FI}ve{ZWSP} years."
    assert normalize_text(raw) == "Warranty coverage is five years."


def test_unicode_is_composed_but_not_compatibility_folded() -> None:
    assert normalize_text("Cafe\u0301 \u00bd price, 10 m\u00b2") == "Caf\u00e9 \u00bd price, 10 m\u00b2"


def test_bengali_joiners_are_kept() -> None:
    word = f"\u0995\u09cd{ZWNJ}\u09b7 \u0995\u09cd{ZWJ}\u09b7"
    assert normalize_text(word) == word


def test_soft_wraps_join_and_hyphenation_is_undone() -> None:
    raw = "Bring the laptop to any store with the\noriginal receipt. The warran-\nty lasts 12 months."
    assert (
        normalize_text(raw)
        == "Bring the laptop to any store with the original receipt. The warranty lasts 12 months."
    )


def test_bullets_and_numbered_steps_keep_their_lines() -> None:
    raw = "Steps:\n1. Back up your data\n2. Reset the device\n\u2022 Keep the box\n- Keep the receipt"
    assert (
        normalize_text(raw)
        == "Steps:\n1. Back up your data\n2. Reset the device\n- Keep the box\n- Keep the receipt"
    )


def test_paragraph_breaks_survive_and_whitespace_collapses() -> None:
    assert normalize_text("One   two\n\n\n\nThree \t four") == "One two\n\nThree four"


def test_tables_keep_one_row_per_line() -> None:
    table = "Plan | Length\nStandard  |  12 months\n\nCare+ | 24 months"
    assert normalize_text(table, keep_lines=True) == "Plan | Length\nStandard | 12 months\nCare+ | 24 months"


# ---------- redaction ----------

# Assembled at runtime so secret scanners don't flag this test file.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
OPENAI_KEY = "sk-proj-" + "a1B2c3D4e5F6g7H8i9J0kLmN"
GITHUB_TOKEN = "ghp_" + "x" * 36
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
PRIVATE_KEY = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("secret", "kind"),
    [
        (AWS_KEY, "access key"),
        (OPENAI_KEY, "API key"),
        (GITHUB_TOKEN, "API key"),
        (JWT, "token"),
        (PRIVATE_KEY, "private key"),
        ("4111 1111 1111 1111", "payment card"),
        ("5500-0000-0000-0004", "payment card"),
        ("123-45-6789", "national ID"),
    ],
)
def test_secrets_and_personal_identifiers_are_redacted(secret: str, kind: str) -> None:
    text, found = redact(f"Before {secret} after.")
    assert secret not in text
    assert f"[redacted {kind}]" in text
    assert text.startswith("Before ") and text.endswith(" after.")
    assert found == {kind: 1}


@pytest.mark.parametrize(
    "harmless",
    [
        "Call 1-800-555-0199 for support.",  # phone number
        "Order 4111 1111 1111 1112 shipped.",  # 16 digits that fail the Luhn check
        "Model TC-2024-15 ships in 2-3 days.",
        "Return within 30 days for a full refund of $1,299.00.",
        "SSN-like but invalid: 000-12-3456 and 666-12-3456.",
        "Email support@techcare.example any time.",
    ],
)
def test_ordinary_policy_text_is_left_alone(harmless: str) -> None:
    assert redact(harmless) == (harmless, {})


def test_luhn() -> None:
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    assert not luhn_valid("0000000000000000")  # all one digit
    assert not luhn_valid("411111111111")  # too short
