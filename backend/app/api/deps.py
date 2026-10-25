"""Request dependencies: who is calling (Principal, Caller, service) and the services they may use.

The Principal is built server-side from a verified token plus the current membership row, never from request
fields (CLAUDE.md "Authorization before retrieval"). Tests override the service getters with fixtures.
"""

import hashlib
import hmac
import ipaddress
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine, select

from app.api.errors import ApiError
from app.config import Settings, get_settings
from app.core.permissions import Principal
from app.core.security import InvalidToken, decode_token
from app.db.models import Membership, Organization, Role, User, UserStatus
from app.db.session import get_engine, session_factory
from app.ingestion.embeddings import Embedder, get_embedder
from app.ingestion.scanning import Scanner, get_scanner
from app.integrations.chroma import KnowledgeIndex, get_knowledge_index
from app.rag.llm import ChatModel, get_chat_model
from app.rag.rerank import Reranker, get_reranker
from app.services.storage import ObjectStorage, get_storage

_bearer = HTTPBearer(auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]
IndexDep = Annotated[KnowledgeIndex, Depends(get_knowledge_index)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
ScannerDep = Annotated[Scanner, Depends(get_scanner)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
ChatModelDep = Annotated[ChatModel, Depends(get_chat_model)]
RerankerDep = Annotated[Reranker | None, Depends(get_reranker)]


def unauthenticated() -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "Sign in to continue.")


def get_principal(
    settings: SettingsDep,
    engine: EngineDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise unauthenticated()
    try:
        claims = decode_token(settings, credentials.credentials)
    except InvalidToken:
        raise unauthenticated() from None
    # users and memberships have no RLS (they are read before a tenant is known), so no tenant scope here.
    with session_factory(engine)() as session:
        row = session.execute(
            select(Membership.role, User.status)
            .join(User, User.id == Membership.user_id)
            .where(Membership.user_id == claims.user_id, Membership.organization_id == claims.organization_id)
        ).one_or_none()
    if row is None or row.status != UserStatus.ACTIVE.value:
        raise unauthenticated()  # removed or disabled since the token was issued
    return Principal(organization_id=claims.organization_id, user_id=claims.user_id, role=Role(row.role))


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_document_manager(principal: PrincipalDep) -> Principal:
    if not principal.can_manage_documents:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, "FORBIDDEN", "Only managers and admins can manage documents."
        )
    return principal


ManagerDep = Annotated[Principal, Depends(require_document_manager)]


# ---------- chat callers: signed-in users, or anonymous sessions of the public site (ADR-017) ----------

SESSION_REF_PATTERN = r"^[A-Za-z0-9_-]{22,128}$"


@dataclass(frozen=True, slots=True)
class Caller:
    principal: Principal
    session_ref: str | None  # sha256 of the anonymous/voice session; None for signed-in users

    @property
    def rate_key(self) -> str:
        return f"user:{self.principal.user_id}" if self.principal.user_id else f"session:{self.session_ref}"


def hash_session_ref(raw: str) -> str:
    """Only the hash of a session reference is stored, so a database read can't resume someone's chat."""
    return hashlib.sha256(raw.encode()).hexdigest()


def organization_id_for(engine: Engine, slug: str) -> uuid.UUID:
    with session_factory(engine)() as session:
        org_id = session.scalar(select(Organization.id).where(Organization.slug == slug))
    if org_id is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "NOT_CONFIGURED", "The assistant isn't set up yet."
        )
    return org_id


def get_caller(
    settings: SettingsDep,
    engine: EngineDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session_ref: Annotated[str | None, Header(alias="X-Session-Ref", pattern=SESSION_REF_PATTERN)] = None,
) -> Caller:
    """A bearer token wins; otherwise the BFF's anonymous session reference, in the public organization."""
    if credentials is not None:
        return Caller(get_principal(settings, engine, credentials), None)
    if session_ref is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "Sign in or start a chat session.")
    org = organization_id_for(engine, settings.public_organization)
    return Caller(Principal(organization_id=org), hash_session_ref(session_ref))


CallerDep = Annotated[Caller, Depends(get_caller)]


# ---------- service credential (Project 2 voice agent) ----------


def _from_allowed_network(request: Request, settings: Settings) -> bool:
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in settings.internal_networks)


def require_service(
    request: Request,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """The voice credential (§11.1): a valid token **and** a caller inside INTERNAL_API_ALLOWED_NETWORKS. The
    network check uses the TCP peer, so a forged X-Forwarded-For can't pass it."""
    if not _from_allowed_network(request, settings):
        raise ApiError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "This network may not call the internal API.")
    expected = settings.internal_service_token.get_secret_value()
    if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "Service credential required.")
