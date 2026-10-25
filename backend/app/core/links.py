"""Short-lived, scoped links to original files (blueprint §9 `GET /documents/{id}/preview`, §11.1
"download/preview URLs are short-lived and scoped; mitigate IDOR").

A link is only issued after the preview endpoint has authorized the caller for that exact version. The link
names one version of one tenant, expires after `PREVIEW_LINK_SECONDS`, and is signed with a key derived from
AUTH_JWT_SECRET for this purpose only (a session token can't be used as a file link, or the reverse). The file
route re-checks that the document still exists before serving, so deleting a document revokes its open links.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from app.config import Settings
from app.core.time import utcnow

ALGORITHM = "HS256"
AUDIENCE = "knowledgeai-file"
_PURPOSE = b"knowledgeai:file-link:v1"


class InvalidLink(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FileLink:
    organization_id: uuid.UUID
    version_id: uuid.UUID


def _key(settings: Settings) -> bytes:
    return hmac.new(settings.auth_jwt_secret.get_secret_value().encode(), _PURPOSE, hashlib.sha256).digest()


def sign(settings: Settings, organization_id: uuid.UUID, version_id: uuid.UUID) -> tuple[str, datetime]:
    expires_at = utcnow() + timedelta(seconds=settings.preview_link_seconds)
    token = jwt.encode(
        {"org": str(organization_id), "ver": str(version_id), "exp": expires_at, "aud": AUDIENCE},
        _key(settings),
        algorithm=ALGORITHM,
    )
    return token, expires_at


def verify(settings: Settings, token: str) -> FileLink:
    try:
        claims = jwt.decode(
            token,
            _key(settings),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["exp", "org", "ver"]},
        )
        return FileLink(uuid.UUID(claims["org"]), uuid.UUID(claims["ver"]))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidLink(type(exc).__name__) from exc
