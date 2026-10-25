"""Customer contact details as keyed hashes (ADR-029): orders verify a caller without storing who they are.

The email and phone used at checkout are normalized and hashed with HMAC-SHA256 under a server secret. A
leaked `customers` table reveals nothing readable, and without the secret the hashes can't be checked against
a list of known emails. Verification compares in constant time.
"""

import hashlib
import hmac
import re
from typing import Literal

from app.config import Settings

Kind = Literal["email", "phone"]
_KEY_LABEL = b"knowledgeai/customer-contact/v1"


def contact_key(settings: Settings) -> bytes:
    """CUSTOMER_LOOKUP_SECRET, or (development) a key derived from AUTH_JWT_SECRET with its own label."""
    if settings.customer_lookup_secret is not None:
        return settings.customer_lookup_secret.get_secret_value().encode()
    return hmac.new(settings.auth_jwt_secret.get_secret_value().encode(), _KEY_LABEL, hashlib.sha256).digest()


def kind_of(value: str) -> Kind:
    return "email" if "@" in value else "phone"


def normalize(kind: Kind, value: str) -> str:
    if kind == "email":
        return value.strip().lower()
    digits = re.sub(r"\D", "", value)
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits  # +1 (NANP) is optional


def contact_hash(key: bytes, kind: Kind, value: str) -> str:
    return hmac.new(key, f"{kind}:{normalize(kind, value)}".encode(), hashlib.sha256).hexdigest()


def matches(key: bytes, value: str, email_hash: str, phone_hash: str | None) -> bool:
    """Whether `value` (an email or a phone number) is the one on the order. Always does the same work."""
    kind = kind_of(value)
    candidate = contact_hash(key, kind, value)
    stored = email_hash if kind == "email" else (phone_hash or "0" * 64)
    return hmac.compare_digest(candidate, stored) and bool(normalize(kind, value))
