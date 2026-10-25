"""Redact personal data from customer-written text before staff see it in analytics (US-06,
quality-dashboard.md
"PII redacted server-side").

Covers what can be detected reliably: emails, phone numbers, TechCare order numbers, plus everything the
ingestion redactor removes (payment cards, national IDs, keys and tokens). Names and street addresses are not
detected (that needs an NER model, P1), which is why full conversations stay behind `conversations:read`.
"""

import re

from app.ingestion.redaction import redact

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_ORDER = re.compile(r"\bTC-\d{4,}\b", re.IGNORECASE)
# 7+ digits with optional separators and country code: too long to match prices or model numbers.
_PHONE = re.compile(
    r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{2,4}(?:[\s.-]?\d{2,4}){2,4}(?![\w-])"
)


def redact_personal(text: str) -> str:
    text, _ = redact(text)  # cards, national IDs, secrets
    text = _EMAIL.sub("[email]", text)
    text = _ORDER.sub("[order number]", text)
    return _PHONE.sub(
        lambda m: "[phone]" if sum(ch.isdigit() for ch in m.group(0)) >= 7 else m.group(0), text
    )
