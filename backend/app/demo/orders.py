"""Fictional catalogue, customers, orders and repairs for US-05 (plan §12.1: "50+ synthetic product records
and 100-200 synthetic orders"; repairs: ADR-031). Deterministic: the same seed gives the same rows, so tests
and demos can name orders and repairs.

Every name, email and phone number is invented (example.com, 555-01xx). Contact details are stored only as
keyed hashes (ADR-029); the plain values below exist in this file so a tester can verify a demo order.
"""

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.contact import contact_hash
from app.db.models import (
    Customer,
    DeliveryMethod,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Repair,
    RepairStatus,
    User,
)

SEED = 20261020
ANCHOR = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)  # "today" for the demo data

# (sku prefix, name, brand, category, product model, price in dollars)
CATALOGUE: tuple[tuple[str, str, str, str, str | None, int], ...] = (
    ("LAP", "TechBook 14", "TechCare", "Laptops", "TechBook 14", 899),
    ("LAP", "TechBook 14 Pro", "TechCare", "Laptops", "TechBook 14 Pro", 1299),
    ("LAP", "DemoLaptop-Z1", "DemoBrand", "Laptops", "DemoLaptop-Z1", 749),
    ("PRN", "DemoPrinter-X2", "DemoBrand", "Printers", "DemoPrinter-X2", 189),
    ("PRN", "DemoPrinter-X2 Pro", "DemoBrand", "Printers", "DemoPrinter-X2 Pro", 279),
    ("HUB", "Smart Hub H1", "TechCare", "Smart home", "Smart Hub H1", 129),
    ("AUD", "Wireless Earbuds E3", "TechCare", "Audio", "Earbuds E3", 99),
)
ACCESSORIES: tuple[tuple[str, str, int], ...] = (
    ("USB-C cable 1 m", "Cables", 15),
    ("USB-C cable 2 m", "Cables", 19),
    ("HDMI cable 2 m", "Cables", 17),
    ("65 W USB-C charger", "Chargers", 49),
    ("100 W USB-C charger", "Chargers", 69),
    ("Laptop sleeve 14 in", "Cases", 35),
    ("Phone case", "Cases", 25),
    ("Screen protector", "Accessories", 19),
    ("Wireless mouse", "Accessories", 29),
    ("Toner cartridge X2", "Printer supplies", 59),
    ("Photo paper A4 (50)", "Printer supplies", 18),
    ("Smart plug", "Smart home", 24),
    ("Care+ for laptops (24 months)", "Services", 199),
    ("Data transfer service", "Services", 39),
)
MAIN_CATEGORIES = ("Laptops", "Printers", "Audio", "Smart home")  # what gets repaired
CARRIERS = ("Parcelway", "SwiftShip", "MetroPost")
STORES = ("TechCare Downtown", "TechCare Riverside Mall", "TechCare Airport Plaza")
PICKUP_HOLD_DAYS = 7  # a ready pickup order is held this long (fictional store policy)


@dataclass(frozen=True, slots=True)
class DemoOrder:
    """A named order for manual tests: verify it with `email` (or `phone`)."""

    order_number: str
    email: str
    phone: str
    status: OrderStatus


# The demo customer account (`alex.customer@techcare.example`, app.cli.seed) owns the first one.
DEMO_ORDERS = (
    DemoOrder("TC-100001", "alex.customer@techcare.example", "(555) 010-0001", OrderStatus.SHIPPED),
    DemoOrder("TC-100002", "jordan.lee@example.com", "555-010-0002", OrderStatus.READY_FOR_PICKUP),
    DemoOrder("TC-100003", "sam.rivera@example.com", "555-010-0003", OrderStatus.DELIVERED),
)


@dataclass(frozen=True, slots=True)
class DemoRepair:
    """A named repair job for manual tests: verify it with the email of `DEMO_ORDERS[owner]`."""

    reference: str
    owner: int  # index into DEMO_ORDERS: whose repair it is
    product_name: str
    reported_fault: str
    diagnosis: str | None
    resolution: str | None
    status: RepairStatus
    store: str
    under_warranty: bool
    cost_cents: int | None
    received_days_ago: int
    ready_in_days: int | None  # from today; negative = already ready
    collected_days_ago: int | None = None


DEMO_REPAIRS = (
    DemoRepair(
        reference="RP-100001",
        owner=0,
        product_name="TechBook 14",
        reported_fault="Won't charge; the battery icon shows a red cross.",
        diagnosis="Faulty USB-C charging port. No liquid damage.",
        resolution="Replaced the charging port and tested a full charge.",
        status=RepairStatus.READY_FOR_PICKUP,
        store=STORES[0],
        under_warranty=True,
        cost_cents=0,
        received_days_ago=6,
        ready_in_days=-1,
    ),
    DemoRepair(
        reference="RP-100004",
        owner=0,
        product_name="DemoPrinter-X2",
        reported_fault="Prints faded pages.",
        diagnosis="Toner cartridge not seated; no hardware fault.",
        resolution="Reseated the toner and cleaned the drum.",
        status=RepairStatus.COLLECTED,
        store=STORES[1],
        under_warranty=False,
        cost_cents=2500,
        received_days_ago=34,
        ready_in_days=None,
        collected_days_ago=30,
    ),
    DemoRepair(
        reference="RP-100002",
        owner=1,
        product_name="DemoPrinter-X2 Pro",
        reported_fault="Paper jams on every print.",
        diagnosis=None,
        resolution=None,
        status=RepairStatus.DIAGNOSING,
        store=STORES[1],
        under_warranty=False,
        cost_cents=None,
        received_days_ago=2,
        ready_in_days=3,
    ),
    DemoRepair(
        reference="RP-100003",
        owner=2,
        product_name="Wireless Earbuds E3",
        reported_fault="No sound from the left earbud.",
        diagnosis="Left earbud driver failed.",
        resolution="Replaced both earbuds under warranty.",
        status=RepairStatus.COLLECTED,
        store=STORES[2],
        under_warranty=True,
        cost_cents=0,
        received_days_ago=12,
        ready_in_days=None,
        collected_days_ago=3,
    ),
)
FAULTS = (
    ("Screen flickers", "Loose display cable", "Reseated the display cable"),
    ("Battery drains in two hours", "Battery worn out (62% capacity)", "Replaced the battery"),
    ("Won't turn on", "Failed power board", "Replaced the power board"),
    ("Wi-Fi keeps dropping", "Antenna cable damaged", "Replaced the antenna cable"),
    ("Paper jams", "Worn pickup roller", "Replaced the pickup roller"),
    ("No sound from one side", "Driver failure", "Replaced the earbud"),
    ("Keyboard keys stuck", "Liquid residue under the keyboard", "Cleaned and replaced the keyboard"),
)


def seed_orders(
    session: Session, organization_id: uuid.UUID, key: bytes, orders: int = 150, customers: int = 80
) -> dict[str, int]:
    """Insert the catalogue, customers and orders (inside the tenant's session). Skips if products exist."""
    if session.scalar(select(func.count()).select_from(Product)):
        return {"products": 0, "customers": 0, "orders": 0}
    rng = random.Random(SEED)  # noqa: S311 - reproducible demo data, not a secret
    products = _products(session, organization_id)
    people = _customers(session, organization_id, key, rng, customers)
    count = 0
    for demo, person in zip(DEMO_ORDERS, people, strict=False):
        _order(session, organization_id, person, products, rng, demo.order_number, demo.status)
        count += 1
    for n in range(count, orders):
        status = rng.choices(list(OrderStatus), weights=(5, 10, 20, 5, 50, 5, 5))[0]
        # Random orders go to the other customers, so each demo customer has exactly its named order.
        buyer = rng.choice(people[len(DEMO_ORDERS) :])
        _order(session, organization_id, buyer, products, rng, f"TC-{200000 + n:06d}", status)
        count += 1
    session.flush()
    repairs = seed_repairs(session, organization_id)
    return {"products": len(products), "customers": len(people), "orders": count, "repairs": repairs}


def seed_repairs(session: Session, organization_id: uuid.UUID, random_repairs: int = 25) -> int:
    """The demo repairs plus random ones for other customers. Skips if repairs exist or nothing is seeded."""
    if session.scalar(select(func.count()).select_from(Repair)):
        return 0
    people = list(session.scalars(select(Customer).order_by(Customer.external_ref)))
    if len(people) <= len(DEMO_ORDERS):
        return 0
    today = ANCHOR.date()
    for demo in DEMO_REPAIRS:
        session.add(
            Repair(
                organization_id=organization_id,
                customer_id=people[demo.owner].id,
                reference=demo.reference,
                product_name=demo.product_name,
                reported_fault=demo.reported_fault,
                diagnosis=demo.diagnosis,
                resolution=demo.resolution,
                status=demo.status.value,
                store=demo.store,
                under_warranty=demo.under_warranty,
                cost_cents=demo.cost_cents,
                received_at=ANCHOR - timedelta(days=demo.received_days_ago),
                ready_on=today + timedelta(days=demo.ready_in_days)
                if demo.ready_in_days is not None
                else None,
                collected_at=(
                    ANCHOR - timedelta(days=demo.collected_days_ago)
                    if demo.collected_days_ago is not None
                    else None
                ),
            )
        )
    rng = random.Random(SEED + 1)  # noqa: S311 - its own stream, so orders stay the same as before repairs
    others = people[len(DEMO_ORDERS) :]
    for n in range(random_repairs):
        customer = rng.choice(others)
        order = session.scalar(
            select(Order).where(Order.customer_id == customer.id, Order.status == OrderStatus.DELIVERED.value)
        )
        product = (
            session.scalar(
                select(Product.name)
                .join(OrderItem, OrderItem.product_id == Product.id)
                .where(OrderItem.order_id == order.id, Product.category.in_(MAIN_CATEGORIES))
            )
            if order
            else None
        ) or rng.choice([name for _, name, *_ in CATALOGUE])
        fault, diagnosis, resolution = rng.choice(FAULTS)
        status = rng.choice(list(RepairStatus))
        diagnosed = status not in (RepairStatus.RECEIVED, RepairStatus.DIAGNOSING)
        done = status in (RepairStatus.READY_FOR_PICKUP, RepairStatus.COLLECTED)
        received = ANCHOR - timedelta(days=rng.randint(1, 40))
        warranty = rng.random() < 0.5
        session.add(
            Repair(
                organization_id=organization_id,
                customer_id=customer.id,
                order_id=order.id if order else None,
                reference=f"RP-{200000 + n:06d}",
                product_name=product,
                reported_fault=fault,
                diagnosis=diagnosis if diagnosed else None,
                resolution=resolution if done else None,
                status=status.value,
                store=rng.choice(STORES),
                under_warranty=warranty,
                cost_cents=None if not diagnosed else (0 if warranty else rng.choice((4900, 8900, 12900))),
                received_at=received,
                ready_on=(received + timedelta(days=rng.randint(3, 10))).date(),
                collected_at=received + timedelta(days=rng.randint(10, 14))
                if status is RepairStatus.COLLECTED
                else None,
            )
        )
    session.flush()
    return len(DEMO_REPAIRS) + random_repairs


def backfill_pickups(session: Session) -> int:
    """Give pickup orders seeded before ADR-031 their store and collect-by date. Returns how many changed."""
    changed = 0
    for order in session.scalars(
        select(Order).where(
            Order.delivery_method == DeliveryMethod.PICKUP.value, Order.pickup_store.is_(None)
        )
    ):
        order.pickup_store, order.pickup_by = _pickup(order.order_number, OrderStatus(order.status))
        changed += 1
    session.flush()
    return changed


def _pickup(number: str, status: OrderStatus) -> tuple[str, date | None]:
    """The pickup store (from the order number, so it's stable) and, once ready, the last day it's held."""
    store = STORES[int(number[-1]) % len(STORES)]
    held = (
        ANCHOR.date() + timedelta(days=PICKUP_HOLD_DAYS) if status is OrderStatus.READY_FOR_PICKUP else None
    )
    return store, held


def _products(session: Session, organization_id: uuid.UUID) -> list[Product]:
    rows: list[Product] = []
    for i, (prefix, name, brand, category, model, dollars) in enumerate(CATALOGUE, start=1):
        rows.append(
            Product(
                organization_id=organization_id,
                sku=f"{prefix}-{i:03d}",
                name=name,
                brand=brand,
                category=category,
                product_model=model,
                price_cents=dollars * 100,
            )
        )
    for i, (name, category, dollars) in enumerate(ACCESSORIES, start=1):
        rows.append(
            Product(
                organization_id=organization_id,
                sku=f"ACC-{i:03d}",
                name=name,
                brand="TechCare",
                category=category,
                price_cents=dollars * 100,
            )
        )
    # Colour and size variants make a realistic catalogue of 50+ lines.
    for i, colour in enumerate(("Black", "Silver", "Blue", "Red", "Green", "White"), start=1):
        for j, (base, category, dollars) in enumerate(
            (
                ("Phone case", "Cases", 25),
                ("Laptop sleeve 14 in", "Cases", 35),
                ("Wireless mouse", "Accessories", 29),
                ("Earbuds E3 ear tips", "Audio", 9),
                ("Smart Hub H1 stand", "Smart home", 19),
            ),
            start=1,
        ):
            rows.append(
                Product(
                    organization_id=organization_id,
                    sku=f"VAR-{i}{j:02d}",
                    name=f"{base}, {colour}",
                    brand="TechCare",
                    category=category,
                    price_cents=dollars * 100,
                )
            )
    session.add_all(rows)
    session.flush()
    return rows


def _customers(
    session: Session, organization_id: uuid.UUID, key: bytes, rng: random.Random, count: int
) -> list[Customer]:
    alex = session.scalar(select(User.id).where(func.lower(User.email) == DEMO_ORDERS[0].email))
    people: list[Customer] = []
    for n in range(1, count + 1):
        if n <= len(DEMO_ORDERS):
            email, phone = DEMO_ORDERS[n - 1].email, DEMO_ORDERS[n - 1].phone
        else:
            email, phone = f"customer{n:03d}@example.com", f"555-01{rng.randint(0, 99):02d}-{n:04d}"
        people.append(
            Customer(
                organization_id=organization_id,
                external_ref=f"CUST-{n:05d}",
                user_id=alex if n == 1 else None,
                email_hash=contact_hash(key, "email", email),
                phone_hash=contact_hash(key, "phone", phone),
            )
        )
    session.add_all(people)
    session.flush()
    return people


def _order(
    session: Session,
    organization_id: uuid.UUID,
    customer: Customer,
    products: list[Product],
    rng: random.Random,
    number: str,
    status: OrderStatus,
) -> None:
    pickup = status is OrderStatus.READY_FOR_PICKUP or rng.random() < 0.15
    method = (
        DeliveryMethod.PICKUP if pickup else rng.choice([DeliveryMethod.STANDARD, DeliveryMethod.EXPRESS])
    )
    placed = ANCHOR - timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 23))
    lines = rng.sample(products, rng.randint(1, 3))
    quantities = [rng.randint(1, 2) for _ in lines]
    shipped = delivered = None
    if status in (OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.RETURNED) and not pickup:
        shipped = placed + timedelta(days=rng.randint(1, 2))
    if status in (OrderStatus.DELIVERED, OrderStatus.RETURNED):
        delivered = (shipped or placed) + timedelta(days=rng.randint(1, 5))
    order = Order(
        organization_id=organization_id,
        customer_id=customer.id,
        order_number=number,
        status=status.value,
        delivery_method=method.value,
        placed_at=placed,
        shipped_at=shipped,
        delivered_at=delivered,
        expected_on=_expected(placed, shipped, method),
        carrier=rng.choice(CARRIERS) if shipped else None,
        tracking_number=f"TRK{rng.randint(10**9, 10**10 - 1)}" if shipped else None,
        total_cents=sum(p.price_cents * q for p, q in zip(lines, quantities, strict=True)),
    )
    if method is DeliveryMethod.PICKUP:
        order.pickup_store, order.pickup_by = _pickup(number, status)
    session.add(order)
    session.flush()
    session.add_all(
        OrderItem(
            organization_id=organization_id,
            order_id=order.id,
            product_id=p.id,
            quantity=q,
            unit_price_cents=p.price_cents,
        )
        for p, q in zip(lines, quantities, strict=True)
    )


def _expected(placed: datetime, shipped: datetime | None, method: DeliveryMethod) -> date | None:
    """Delivery estimate: from shipping once shipped (express 1 day, standard 3), else from the order."""
    if method is DeliveryMethod.PICKUP:
        return None
    if shipped is not None:
        return (shipped + timedelta(days=1 if method is DeliveryMethod.EXPRESS else 3)).date()
    return (placed + timedelta(days=2 if method is DeliveryMethod.EXPRESS else 5)).date()
