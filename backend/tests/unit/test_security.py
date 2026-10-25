"""Passwords and tokens (blueprint §9 auth, ADR-005)."""

import uuid
from datetime import timedelta

import jwt
import pytest

from app.config import get_settings
from app.core.security import (
    ALGORITHM,
    InvalidToken,
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)
from app.core.time import utcnow

SETTINGS = get_settings()


def test_passwords_are_hashed_with_argon2id_and_verified() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery staple")
    assert not verify_password(hashed, "wrong")
    assert not verify_password(None, "anything")  # unknown user: still runs a hash, still fails
    assert not verify_password("not-a-hash", "anything")


def test_token_round_trip() -> None:
    user, org = uuid.uuid4(), uuid.uuid4()
    token, expires_at = issue_token(SETTINGS, user, org)
    claims = decode_token(SETTINGS, token)
    assert (claims.user_id, claims.organization_id) == (user, org)
    assert abs((claims.expires_at - expires_at).total_seconds()) < 1


def _forge(**overrides: object) -> str:
    now = utcnow()
    payload = {
        "sub": str(uuid.uuid4()),
        "org": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": "knowledgeai",
        "aud": "knowledgeai-api",
    } | overrides
    return jwt.encode(payload, SETTINGS.auth_jwt_secret.get_secret_value(), algorithm=ALGORITHM)


@pytest.mark.parametrize(
    "token",
    [
        _forge(exp=utcnow() - timedelta(seconds=1)),  # expired
        _forge(aud="someone-else"),
        _forge(iss="someone-else"),
        _forge(org="not-a-uuid"),
        jwt.encode({"sub": "x"}, "another-secret-that-is-long-enough-to-sign", algorithm=ALGORITHM),
        jwt.encode({"sub": str(uuid.uuid4())}, None, algorithm="none"),  # unsigned
        "garbage",
    ],
    ids=["expired", "audience", "issuer", "bad-org", "wrong-key", "alg-none", "garbage"],
)
def test_invalid_tokens_are_rejected(token: str) -> None:
    with pytest.raises(InvalidToken):
        decode_token(SETTINGS, token)
