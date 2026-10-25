"""Orders and repairs (US-05, ADR-029, ADR-031): `POST /orders/lookup` and `POST /repairs/lookup` after
verification, `GET /orders/mine` and `GET /repairs/mine` for a signed-in customer's own. Data comes from
Postgres only, by fixed queries.

A failed check gets one generic answer whether the record exists or not (content-guide.md "order-status
verification"), and repeated failures lock the caller and the number for a while (no guessing). A successful
check made from a conversation the caller owns is remembered for that conversation (`account_grants`), so the
chat can answer follow-up questions about that one record until the grant expires.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Caller, CallerDep, EngineDep, PrincipalDep, SettingsDep
from app.api.errors import COMMON_ERRORS, ApiError, request_id
from app.api.ratelimit import limiter, order_lookup_failures
from app.config import Settings
from app.core.contact import contact_key
from app.db.models import Conversation
from app.db.session import tenant_session
from app.rag.schemas import OrderRecord, RepairRecord
from app.services import account, audit, orders, repairs

router = APIRouter(prefix="/api/v1", tags=["orders"], responses=COMMON_ERRORS)

NOT_VERIFIED = (
    "We couldn't match those details. Check the order number on your receipt and try again, "
    "or contact support."
)
REPAIR_NOT_VERIFIED = (
    "We couldn't match those details. Check the repair number on your repair receipt and try again, "
    "or contact support."
)
LOOKUPS_PER_MINUTE = 10

# The API's order shape is the chat's record shape (ADR-031): one definition, so the two never drift.
OrderOut = OrderRecord
RepairOut = RepairRecord


class OrderLookupIn(BaseModel):
    order_number: Annotated[str, Field(min_length=6, max_length=20, description='e.g. "TC-123456"')]
    contact: Annotated[
        str, Field(min_length=3, max_length=254, description="Email or phone used at checkout")
    ]
    # The chat conversation the check was made from: follow-up questions there may use this order (ADR-031).
    conversation_id: uuid.UUID | None = None


class RepairLookupIn(BaseModel):
    reference: Annotated[str, Field(min_length=6, max_length=20, description='e.g. "RP-123456"')]
    contact: Annotated[
        str, Field(min_length=3, max_length=254, description="Email or phone given at the repair desk")
    ]
    conversation_id: uuid.UUID | None = None


def _refuse(code: str, message: str, seconds: int) -> ApiError:
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        code,
        message,
        {"retry_after_seconds": seconds},
        {"Retry-After": str(seconds)},
    )


def _guard(caller: Caller, settings: Settings, number_key: str) -> tuple[str, str]:
    """Rate limit, then the lockout for this caller and for this number. Returns the two lockout keys."""
    window = settings.order_lookup_window_seconds
    keys = (f"lookup:{caller.rate_key}", f"lookup-{caller.principal.organization_id}:{number_key}")
    wait = limiter.check(f"orders:{caller.rate_key}", LOOKUPS_PER_MINUTE)
    if wait is not None:
        raise _refuse("RATE_LIMITED", f"Too many order checks. Try again in {wait} seconds.", wait)
    locked = [
        s
        for k in keys
        if (s := order_lookup_failures.locked_for(k, settings.order_lookup_max_failures, window))
    ]
    if locked:
        wait = max(locked)
        raise _refuse(
            "ORDER_LOOKUP_LOCKED",
            f"Too many attempts that didn't match. Try again in {max(1, wait // 60)} minutes, "
            "or contact support.",
            wait,
        )
    return keys


def _failed(keys: tuple[str, str], settings: Settings) -> None:
    for key in keys:
        order_lookup_failures.fail(key, settings.order_lookup_window_seconds)


def _owned_conversation(
    session: Session, caller: Caller, conversation_id: uuid.UUID | None
) -> uuid.UUID | None:
    """The conversation, if it's the caller's (else None: the check still works, it just isn't remembered)."""
    if conversation_id is None:
        return None
    conversation = session.get(Conversation, conversation_id)  # RLS: other tenants' rows are invisible
    if conversation is None:
        return None
    if caller.principal.user_id is not None:
        mine = conversation.owner_user_id == caller.principal.user_id
    else:
        mine = conversation.owner_user_id is None and conversation.session_ref == caller.session_ref
    return conversation.id if mine else None


@router.post("/orders/lookup")
def lookup_order(
    body: OrderLookupIn, request: Request, caller: CallerDep, settings: SettingsDep, engine: EngineDep
) -> OrderOut:
    """The order, if the contact (email or phone) is the one used at checkout. Guests, customers and staff
    (looking up for a customer) all verify the same way."""
    number = orders.normalize_number(body.order_number)
    keys = _guard(caller, settings, f"order:{number}")
    principal = caller.principal
    with tenant_session(principal.organization_id, engine) as session:
        found = orders.verify(session, principal.organization_id, number, body.contact, contact_key(settings))
        audit.record(
            session,
            principal,
            "order.lookup.succeeded" if found else "order.lookup.failed",
            "order",
            details={"order_number": number, "role": principal.role.value if principal.role else "guest"},
            request_id=request_id(request),
        )
        if found is not None and (conversation := _owned_conversation(session, caller, body.conversation_id)):
            account.grant(session, principal, conversation, settings.account_grant_minutes, order_id=found[0])
    if found is None:
        _failed(keys, settings)
        raise ApiError(status.HTTP_404_NOT_FOUND, "ORDER_NOT_VERIFIED", NOT_VERIFIED)
    order_lookup_failures.clear(keys[0])
    return account.order_record(found[1])


@router.post("/repairs/lookup")
def lookup_repair(
    body: RepairLookupIn, request: Request, caller: CallerDep, settings: SettingsDep, engine: EngineDep
) -> RepairOut:
    """The repair job, if the contact (email or phone) is the one given at the repair desk."""
    reference = repairs.normalize_reference(body.reference)
    keys = _guard(caller, settings, f"repair:{reference}")
    principal = caller.principal
    with tenant_session(principal.organization_id, engine) as session:
        found = repairs.verify(
            session, principal.organization_id, reference, body.contact, contact_key(settings)
        )
        audit.record(
            session,
            principal,
            "repair.lookup.succeeded" if found else "repair.lookup.failed",
            "repair",
            details={"reference": reference, "role": principal.role.value if principal.role else "guest"},
            request_id=request_id(request),
        )
        if found is not None and (conversation := _owned_conversation(session, caller, body.conversation_id)):
            account.grant(
                session, principal, conversation, settings.account_grant_minutes, repair_id=found[0]
            )
    if found is None:
        _failed(keys, settings)
        raise ApiError(status.HTTP_404_NOT_FOUND, "REPAIR_NOT_VERIFIED", REPAIR_NOT_VERIFIED)
    order_lookup_failures.clear(keys[0])
    return account.repair_record(found[1])


@router.get("/orders/mine")
def my_orders(principal: PrincipalDep, engine: EngineDep) -> list[OrderOut]:
    """A signed-in customer's own orders (their account is linked to the orders); newest first."""
    if principal.user_id is None:  # pragma: no cover - PrincipalDep requires a session
        return []
    with tenant_session(principal.organization_id, engine) as session:
        views = orders.for_user(session, principal.organization_id, principal.user_id)
        return [account.order_record(v) for v in views]


@router.get("/repairs/mine")
def my_repairs(principal: PrincipalDep, engine: EngineDep) -> list[RepairOut]:
    """A signed-in customer's own repair jobs; newest first."""
    if principal.user_id is None:  # pragma: no cover - PrincipalDep requires a session
        return []
    with tenant_session(principal.organization_id, engine) as session:
        views = repairs.for_user(session, principal.organization_id, principal.user_id)
        return [account.repair_record(v) for v in views]
