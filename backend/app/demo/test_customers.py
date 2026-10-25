"""Three fictional customers with sign-in accounts for hands-on testing (development only).

Each has five orders of one product each: two delivered this month (October 2026) and three still on the way,
expected after 15 November 2026. Dates are fixed, so the same questions get the same answers every run.
Every name, email and phone number is invented (example.com, 555-01xx).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.contact import contact_hash
from app.db.models import Customer, DeliveryMethod, Order, OrderItem, OrderStatus, Product

STORE = "TechCare Downtown"


@dataclass(frozen=True, slots=True)
class PlannedOrder:
    number: str
    product: str  # a catalogue name (app/demo/orders.py)
    status: OrderStatus
    method: DeliveryMethod
    placed: date
    expected: date | None = None  # promised delivery date
    delivered: date | None = None  # when it arrived (or was collected)


@dataclass(frozen=True, slots=True)
class TestCustomer:
    email: str
    name: str
    phone: str
    external_ref: str
    orders: tuple[PlannedOrder, ...]


def _done(number: str, product: str, placed: date, delivered: date, method: DeliveryMethod) -> PlannedOrder:
    return PlannedOrder(number, product, OrderStatus.DELIVERED, method, placed, delivered, delivered)


def _coming(number: str, product: str, status: OrderStatus, placed: date, expected: date) -> PlannedOrder:
    return PlannedOrder(number, product, status, DeliveryMethod.STANDARD, placed, expected)


OCT, NOV = 10, 11
TEST_CUSTOMERS = (
    TestCustomer(
        "maya.chen@example.com",
        "Maya Chen",
        "555-010-0101",
        "CUST-90001",
        (
            _done(
                "TC-300101", "TechBook 14", date(2026, OCT, 3), date(2026, OCT, 8), DeliveryMethod.STANDARD
            ),
            _done(
                "TC-300102",
                "Wireless Earbuds E3",
                date(2026, OCT, 17),
                date(2026, OCT, 21),
                DeliveryMethod.EXPRESS,
            ),
            _coming(
                "TC-300103",
                "DemoPrinter-X2",
                OrderStatus.PROCESSING,
                date(2026, OCT, 22),
                date(2026, NOV, 18),
            ),
            _coming(
                "TC-300104", "Smart Hub H1", OrderStatus.PLACED, date(2026, OCT, 23), date(2026, NOV, 24)
            ),
            _coming(
                "TC-300105",
                "100 W USB-C charger",
                OrderStatus.PLACED,
                date(2026, OCT, 23),
                date(2026, NOV, 20),
            ),
        ),
    ),
    TestCustomer(
        "omar.haddad@example.com",
        "Omar Haddad",
        "555-010-0102",
        "CUST-90002",
        (
            _done(
                "TC-300201", "DemoLaptop-Z1", date(2026, OCT, 1), date(2026, OCT, 5), DeliveryMethod.STANDARD
            ),
            _done(
                "TC-300202",
                "Toner cartridge X2",
                date(2026, OCT, 12),
                date(2026, OCT, 15),
                DeliveryMethod.PICKUP,
            ),
            _coming(
                "TC-300203",
                "TechBook 14 Pro",
                OrderStatus.PROCESSING,
                date(2026, OCT, 21),
                date(2026, NOV, 17),
            ),
            _coming(
                "TC-300204", "Wireless mouse", OrderStatus.PLACED, date(2026, OCT, 22), date(2026, NOV, 21)
            ),
            _coming(
                "TC-300205",
                "Laptop sleeve 14 in",
                OrderStatus.PLACED,
                date(2026, OCT, 23),
                date(2026, NOV, 26),
            ),
        ),
    ),
    TestCustomer(
        "lena.fischer@example.com",
        "Lena Fischer",
        "555-010-0103",
        "CUST-90003",
        (
            _done(
                "TC-300301",
                "DemoPrinter-X2 Pro",
                date(2026, OCT, 6),
                date(2026, OCT, 12),
                DeliveryMethod.STANDARD,
            ),
            _done(
                "TC-300302", "Phone case", date(2026, OCT, 19), date(2026, OCT, 22), DeliveryMethod.EXPRESS
            ),
            _coming(
                "TC-300303",
                "Wireless Earbuds E3",
                OrderStatus.PROCESSING,
                date(2026, OCT, 20),
                date(2026, NOV, 16),
            ),
            _coming("TC-300304", "Smart plug", OrderStatus.PLACED, date(2026, OCT, 22), date(2026, NOV, 19)),
            _coming("TC-300305", "TechBook 14", OrderStatus.PLACED, date(2026, OCT, 23), date(2026, NOV, 30)),
        ),
    ),
)


def _at(day: date, hour: int = 17) -> datetime:
    """Midday in the store's timezone (America/Chicago is UTC-5 in October)."""
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def seed_customer(
    session: Session, organization_id: uuid.UUID, person: TestCustomer, user_id: uuid.UUID, key: bytes
) -> int:
    """The customer record (linked to the sign-in account) and any of their orders not there yet. Inside the
    tenant's session. Returns how many orders were added."""
    customer = session.scalar(select(Customer).where(Customer.external_ref == person.external_ref))
    if customer is None:
        customer = Customer(
            organization_id=organization_id,
            external_ref=person.external_ref,
            email_hash=contact_hash(key, "email", person.email),
            phone_hash=contact_hash(key, "phone", person.phone),
        )
        session.add(customer)
    customer.user_id = user_id
    session.flush()
    added = 0
    for planned in person.orders:
        if session.scalar(select(Order.id).where(Order.order_number == planned.number)):
            continue
        product = session.scalar(select(Product).where(func.lower(Product.name) == planned.product.lower()))
        if product is None:
            raise SystemExit(
                f"product {planned.product!r} missing: run `python -m app.cli.seed_orders` first"
            )
        pickup = planned.method is DeliveryMethod.PICKUP
        shipped = planned.delivered is not None and not pickup
        order = Order(
            organization_id=organization_id,
            customer_id=customer.id,
            order_number=planned.number,
            status=planned.status.value,
            delivery_method=planned.method.value,
            placed_at=_at(planned.placed, 15),
            shipped_at=_at(planned.delivered - timedelta(days=1), 14)
            if shipped and planned.delivered
            else None,
            delivered_at=_at(planned.delivered) if planned.delivered else None,
            expected_on=None if pickup else planned.expected,
            carrier="Parcelway" if shipped else None,
            tracking_number=f"TRK{planned.number[3:]}00" if shipped else None,
            total_cents=product.price_cents,
            pickup_store=STORE if pickup else None,
        )
        session.add(order)
        session.flush()
        session.add(
            OrderItem(
                organization_id=organization_id,
                order_id=order.id,
                product_id=product.id,
                quantity=1,
                unit_price_cents=product.price_cents,
            )
        )
        added += 1
    session.flush()
    return added
