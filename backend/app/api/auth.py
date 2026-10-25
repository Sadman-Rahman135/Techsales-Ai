"""Sign-in and the current user (blueprint §9: `POST /auth/login`, `GET /me`).

The Next.js BFF calls these and keeps the token in an httpOnly cookie (ADR-005); the browser never sees it.
Failures are deliberately uniform ("email or password is incorrect") so the endpoint can't confirm accounts.
After LOGIN_MAX_FAILURES wrong passwords an account is locked for LOGIN_LOCKOUT_SECONDS (§11.2); the lock
applies whether or not the email exists, so it can't confirm accounts either.
"""

import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import func, select

from app.api.deps import EngineDep, PrincipalDep, SettingsDep
from app.api.errors import COMMON_ERRORS, ApiError, request_id
from app.api.ratelimit import login_failures
from app.core.security import hash_password, issue_token, needs_rehash, verify_password
from app.db.models import AuditEvent, Membership, Organization, Role, User, UserStatus
from app.db.session import session_factory, set_tenant

router = APIRouter(prefix="/api/v1", tags=["auth"], responses=COMMON_ERRORS)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)
    organization: str | None = Field(default=None, max_length=63, description="Organization slug")


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    role: Role
    organization: OrganizationOut


class SessionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_at: datetime
    me: MeOut


def _bad_credentials() -> ApiError:
    return ApiError(
        status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "The email or password is incorrect."
    )


@router.post("/auth/login")
def login(body: LoginIn, request: Request, settings: SettingsDep, engine: EngineDep) -> SessionOut:
    email = body.email.strip().lower()
    lock_key, window = f"login:{email}", settings.login_lockout_seconds
    locked = login_failures.locked_for(lock_key, settings.login_max_failures, window)
    if locked is not None:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "LOGIN_LOCKED",
            f"Too many failed sign-ins. Try again in {math.ceil(locked / 60)} minutes.",
            {"retry_after_seconds": locked},
            {"Retry-After": str(locked)},
        )
    with session_factory(engine)() as session, session.begin():
        user = session.scalar(select(User).where(func.lower(User.email) == email))
        password = body.password.get_secret_value()
        if not verify_password(user.password_hash if user else None, password) or user is None:
            login_failures.fail(lock_key, window)
            raise _bad_credentials()
        login_failures.clear(lock_key)
        if user.status != UserStatus.ACTIVE.value:
            raise _bad_credentials()

        query = (
            select(Membership, Organization)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(Membership.user_id == user.id)
            .order_by(Membership.created_at)
        )
        if body.organization:
            query = query.where(Organization.slug == body.organization)
        memberships = session.execute(query).all()
        if not memberships:
            raise _bad_credentials()
        if len(memberships) > 1 and not body.organization:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST,
                "ORGANIZATION_REQUIRED",
                "This account belongs to several organizations. Choose one.",
                {"organizations": [org.slug for _, org in memberships]},
            )
        membership, org = memberships[0]

        if needs_rehash(user.password_hash or ""):
            user.password_hash = hash_password(password)
        set_tenant(session, org.id)
        session.add(
            AuditEvent(
                organization_id=org.id,
                actor_user_id=user.id,
                actor_label=f"user:{user.id}",
                action="auth.login",
                target_type="user",
                target_id=user.id,
                details={},
                request_id=request_id(request),
            )
        )
        token, expires_at = issue_token(settings, user.id, org.id)
        return SessionOut(
            access_token=token,
            expires_at=expires_at,
            me=MeOut(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                role=Role(membership.role),
                organization=OrganizationOut(id=org.id, name=org.name, slug=org.slug, timezone=org.timezone),
            ),
        )


@router.get("/me")
def me(principal: PrincipalDep, engine: EngineDep) -> MeOut:
    with session_factory(engine)() as session:
        user = session.get(User, principal.user_id)
        org = session.get(Organization, principal.organization_id)
    if user is None or org is None or principal.role is None:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "Sign in to continue.")
    return MeOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=principal.role,
        organization=OrganizationOut(id=org.id, name=org.name, slug=org.slug, timezone=org.timezone),
    )
