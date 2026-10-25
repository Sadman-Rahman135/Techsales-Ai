"""Order status (US-05, ADR-029). Orders come from Postgres only, never from Chroma or the model (§2.4).

A guest or customer sees an order after proving it's theirs with the order number and the email or phone
used at checkout; a signed-in customer also sees the orders linked to their account. Staff look an order
up the same way, for a customer who gives them those details. A failed check never says if the order exists.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import contact
from app.db.models import Customer, Order, OrderItem, Product

ORDER_NUMBER_PATTERN = r"^TC-[0-9]{6}$"
# Spent on a missing order too, so a wrong number and a wrong email take the same time.
_DUMMY_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class OrderLine:
    name: str
    sku: str
    quantity: int
    unit_price_cents: int


@dataclass(frozen=True, slots=True)
class OrderView:
    order_number: str
    status: str
    delivery_method: str
    placed_at: datetime
    shipped_at: datetime | None
    delivered_at: datetime | None
    expected_on: date | None
    carrier: str | None
    tracking_number: str | None
    total_cents: int
    lines: list[OrderLine]
    pickup_store: str | None = None
    pickup_by: date | None = None


def normalize_number(raw: str) -> str:
    """ "tc 123456", "TC123456" and "TC-123456" all mean TC-123456."""
    digits = "".join(c for c in raw if c.isdigit())
    return f"TC-{digits}" if len(digits) == 6 else raw.strip().upper()


def verify(
    session: Session, organization_id: uuid.UUID, order_number: str, contact_value: str, key: bytes
) -> tuple[uuid.UUID, OrderView] | None:
    """The order (its ID and view), if `contact_value` is the email or phone used for it; None for any
    mismatch."""
    row = session.execute(
        select(Order, Customer.email_hash, Customer.phone_hash)
        .join(Customer, Customer.id == Order.customer_id)
        .where(Order.organization_id == organization_id, Order.order_number == normalize_number(order_number))
    ).one_or_none()
    if row is None:
        contact.matches(key, contact_value, _DUMMY_HASH, None)  # same work as a real check
        return None
    order, email_hash, phone_hash = row
    if not contact.matches(key, contact_value, email_hash, phone_hash):
        return None
    return order.id, view(session, order)


def for_user(
    session: Session, organization_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10
) -> list[OrderView]:
    """A signed-in customer's own orders (linked through `customers.user_id`), newest first."""
    orders = session.scalars(
        select(Order)
        .join(Customer, Customer.id == Order.customer_id)
        .where(Order.organization_id == organization_id, Customer.user_id == user_id)
        .order_by(Order.placed_at.desc())
        .limit(limit)
    )
    return [view(session, order) for order in orders]


def view(session: Session, order: Order) -> OrderView:
    lines = session.execute(
        select(Product.name, Product.sku, OrderItem.quantity, OrderItem.unit_price_cents)
        .join(Product, Product.id == OrderItem.product_id)
        .where(OrderItem.order_id == order.id)
        .order_by(Product.name)
    ).all()
    return OrderView(
        order_number=order.order_number,
        status=order.status,
        delivery_method=order.delivery_method,
        placed_at=order.placed_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
        expected_on=order.expected_on,
        carrier=order.carrier,
        tracking_number=order.tracking_number,
        total_cents=order.total_cents,
        lines=[OrderLine(*line) for line in lines],
        pickup_store=order.pickup_store,
        pickup_by=order.pickup_by,
    )
