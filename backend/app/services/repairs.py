"""Repair jobs (ADR-031). Like orders (ADR-029): Postgres only, never Chroma, and shown only to the customer
it belongs to: after the repair reference and the email or phone on the job, or to the signed-in customer. A
failed check never says if the repair exists.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import contact
from app.db.models import Customer, Order, Repair

REFERENCE_PATTERN = r"^RP-[0-9]{6}$"
# Spent on a missing repair too, so a wrong reference and a wrong email take the same time.
_DUMMY_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class RepairView:
    reference: str
    product_name: str
    status: str
    reported_fault: str
    diagnosis: str | None
    resolution: str | None
    store: str
    under_warranty: bool
    cost_cents: int | None
    received_at: datetime
    ready_on: date | None
    collected_at: datetime | None
    order_number: str | None


def normalize_reference(raw: str) -> str:
    """ "rp 100001", "RP100001" and "RP-100001" all mean RP-100001."""
    digits = "".join(c for c in raw if c.isdigit())
    return f"RP-{digits}" if len(digits) == 6 else raw.strip().upper()


def verify(
    session: Session, organization_id: uuid.UUID, reference: str, contact_value: str, key: bytes
) -> tuple[uuid.UUID, RepairView] | None:
    """The repair (its ID and view), if `contact_value` is the email or phone on it; None for any mismatch."""
    row = session.execute(
        select(Repair, Customer.email_hash, Customer.phone_hash)
        .join(Customer, Customer.id == Repair.customer_id)
        .where(Repair.organization_id == organization_id, Repair.reference == normalize_reference(reference))
    ).one_or_none()
    if row is None:
        contact.matches(key, contact_value, _DUMMY_HASH, None)  # same work as a real check
        return None
    repair, email_hash, phone_hash = row
    if not contact.matches(key, contact_value, email_hash, phone_hash):
        return None
    return repair.id, view(session, repair)


def for_user(
    session: Session, organization_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10
) -> list[RepairView]:
    """A signed-in customer's own repairs (linked through `customers.user_id`), newest first."""
    repairs = session.scalars(
        select(Repair)
        .join(Customer, Customer.id == Repair.customer_id)
        .where(Repair.organization_id == organization_id, Customer.user_id == user_id)
        .order_by(Repair.received_at.desc())
        .limit(limit)
    )
    return [view(session, repair) for repair in repairs]


def view(session: Session, repair: Repair) -> RepairView:
    order_number = (
        session.scalar(select(Order.order_number).where(Order.id == repair.order_id))
        if repair.order_id
        else None
    )
    return RepairView(
        reference=repair.reference,
        product_name=repair.product_name,
        status=repair.status,
        reported_fault=repair.reported_fault,
        diagnosis=repair.diagnosis,
        resolution=repair.resolution,
        store=repair.store,
        under_warranty=repair.under_warranty,
        cost_cents=repair.cost_cents,
        received_at=repair.received_at,
        ready_on=repair.ready_on,
        collected_at=repair.collected_at,
        order_number=order_number,
    )
