"""Passwords and session tokens (blueprint §9 `POST /auth/login`, ADR-005).

- Passwords: argon2id with the library's current defaults (RFC 9106 low-memory profile).
- Tokens: short-lived HS256 JWTs. They name the user and the organization only. The role is re-read from
  `memberships` on every request, so a demotion or removal takes effect immediately, not at token expiry.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import Settings
from app.core.time import utcnow

ALGORITHM = "HS256"
ISSUER = "knowledgeai"
AUDIENCE = "knowledgeai-api"

_hasher = PasswordHasher()
# Verified when the email is unknown, so a miss costs as long as a hit (no account enumeration by timing).
_DUMMY_HASH = _hasher.hash("knowledgeai-timing-equaliser")


class InvalidToken(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    expires_at: datetime


def issue_token(settings: Settings, user_id: uuid.UUID, organization_id: uuid.UUID) -> tuple[str, datetime]:
    now = utcnow()
    expires_at = now + timedelta(minutes=settings.auth_token_minutes)
    payload = {
        "sub": str(user_id),
        "org": str(organization_id),
        "iat": now,
        "exp": expires_at,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.auth_jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, expires_at


def decode_token(settings: Settings, token: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.auth_jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],  # pinned: never let the token choose (no "none", no RS/HS confusion)
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "org", "exp", "iat"]},
        )
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(payload["org"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=utcnow().tzinfo),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidToken(type(exc).__name__) from exc
