"""A customer's own orders and repairs, for questions about them in the chat (ADR-031).

Which records a caller may see is decided here with fixed queries, never by the model:
- a signed-in customer: the orders and repairs linked to their account (`customers.user_id`);
- anyone (guest included): the order or repair they verified in this conversation, until the grant expires.

The records are rendered as a short fact sheet for the local model, which answers the question from it. Every
order/repair reference and every number in its answer must appear in the sheet (`grounded`); otherwise the
caller gets `summary`, built from the records alone. The sheet includes totals ("items in total") so counting
questions don't depend on the model's arithmetic.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import Principal
from app.core.time import utcnow
from app.db.models import AccountGrant, Order, Organization, Repair
from app.rag.schemas import AccountRecord, OrderLineRecord, OrderRecord, RepairRecord
from app.rag.text import content_terms
from app.services import orders, repairs
from app.services.orders import OrderView
from app.services.repairs import RepairView

ORDER_STATUS = {
    "placed": "Order placed",
    "processing": "Being prepared",
    "shipped": "On its way",
    "ready_for_pickup": "Ready for pickup",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "returned": "Returned",
}
REPAIR_STATUS = {
    "received": "Received at the store",
    "diagnosing": "Being diagnosed",
    "waiting_for_parts": "Waiting for parts",
    "repairing": "Being repaired",
    "ready_for_pickup": "Ready for pickup",
    "collected": "Collected",
    "cancelled": "Cancelled",
}
DELIVERY = {"standard": "Standard delivery", "express": "Express delivery", "pickup": "Store pickup"}
# How a summary sentence says it: "Order TC-300104 is placed, expected on …".
_ORDER_STATE = {
    "placed": "placed",
    "processing": "being prepared",
    "shipped": "on its way",
    "ready_for_pickup": "ready for pickup",
    "delivered": "delivered",
    "cancelled": "cancelled",
    "returned": "returned",
}
OPEN_ORDER_STATUSES = ("placed", "processing", "shipped")  # not with the customer yet
MAX_ANSWER_CHARS = 800
_REFERENCE = re.compile(r"\b(?:TC|RP)-\d{6}\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+")
# Words that tie a question to the caller's own things when the documents can't answer it (ADR-031).
ACCOUNT_WORDS = re.compile(
    r"\b(?:orders?|repairs?|pick\s*up|pickup|collect(?:ed|ion)?|purchases?|bought|items?|products?"
    r"|deliver(?:y|ed)|shipped|shipping|tracking|parcel|technician|diagnosis|fault"
    r"|arriv(?:e|es|ed|ing|al)|coming|ordered|spent|spend|paid|receipt|receiv(?:e|ed|ing))\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Records:
    orders: list[OrderView] = field(default_factory=list)
    repairs: list[RepairView] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.orders or self.repairs)


def records_for(session: Session, principal: Principal, conversation_id: uuid.UUID | None) -> Records:
    """Everything this caller may ask about, newest first. Inside the tenant's session (RLS)."""
    org = principal.organization_id
    found = Records()
    if principal.user_id is not None:
        found.orders = orders.for_user(session, org, principal.user_id)
        found.repairs = repairs.for_user(session, org, principal.user_id)
    if conversation_id is not None:
        grants = list(
            session.scalars(
                select(AccountGrant).where(
                    AccountGrant.conversation_id == conversation_id, AccountGrant.expires_at > utcnow()
                )
            )
        )
        order_ids = [g.order_id for g in grants if g.order_id]
        repair_ids = [g.repair_id for g in grants if g.repair_id]
        known_orders = {o.order_number for o in found.orders}
        known_repairs = {r.reference for r in found.repairs}
        for order in session.scalars(select(Order).where(Order.id.in_(order_ids))):
            if order.order_number not in known_orders:
                found.orders.append(orders.view(session, order))
        for repair in session.scalars(select(Repair).where(Repair.id.in_(repair_ids))):
            if repair.reference not in known_repairs:
                found.repairs.append(repairs.view(session, repair))
    found.orders.sort(key=lambda o: o.placed_at, reverse=True)
    found.repairs.sort(key=lambda r: r.received_at, reverse=True)
    return found


def grant(
    session: Session,
    principal: Principal,
    conversation_id: uuid.UUID,
    minutes: int,
    *,
    order_id: uuid.UUID | None = None,
    repair_id: uuid.UUID | None = None,
) -> None:
    """Remember a verified record for this conversation. The caller has already checked the conversation is
    theirs and the record's contact matched."""
    session.add(
        AccountGrant(
            organization_id=principal.organization_id,
            conversation_id=conversation_id,
            order_id=order_id,
            repair_id=repair_id,
            expires_at=utcnow() + timedelta(minutes=minutes),
        )
    )


def timezone_of(session: Session, principal: Principal) -> str:
    return session.get_one(Organization, principal.organization_id).timezone


# ---------- the fact sheet ----------


def _day(value: date | datetime, zone: ZoneInfo, *, iso: bool = True) -> str:
    if isinstance(value, datetime):
        value = value.astimezone(zone).date()
    plain = f"{value:%A} {value.day} {value:%B %Y}"
    return f"{plain} ({value.isoformat()})" if iso else plain


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def render(records: Records, timezone: str, now: datetime | None = None, question: str = "") -> str:
    """The records as labelled lines; the only facts the model may use."""
    zone = ZoneInfo(timezone)
    today = (now or utcnow()).astimezone(zone)
    lines = [f"Today: {_day(today, zone)}"]
    for o in records.orders:
        items = sum(line.quantity for line in o.lines)
        lines += [
            f"Order {o.order_number}",
            f"- Status: {ORDER_STATUS.get(o.status, o.status)}",
            f"- Delivery: {DELIVERY.get(o.delivery_method, o.delivery_method)}",
            f"- Ordered on: {_day(o.placed_at, zone)}",
        ]
        if o.shipped_at:
            lines.append(f"- Shipped on: {_day(o.shipped_at, zone)}")
        if o.delivered_at:
            lines.append(f"- Delivered on: {_day(o.delivered_at, zone)}")
        elif o.expected_on and o.status in ("placed", "processing", "shipped"):
            lines.append(f"- Expected delivery: {_day(o.expected_on, zone)}")
        if o.carrier and o.tracking_number:
            lines.append(f"- Carrier and tracking number: {o.carrier} {o.tracking_number}")
        if o.pickup_store:
            lines.append(f"- Pickup store: {o.pickup_store}")
        if o.pickup_by:
            lines.append(f"- Collect by: {_day(o.pickup_by, zone)}")
        bought = "; ".join(
            f"{line.quantity} x {line.name} ({_money(line.unit_price_cents)} each)" for line in o.lines
        )
        lines += [f"- Items ({items} in total): {bought}", f"- Order total: {_money(o.total_cents)}"]
    for r in records.repairs:
        lines += [
            f"Repair {r.reference}",
            f"- Product: {r.product_name}",
            f"- Status: {REPAIR_STATUS.get(r.status, r.status)}",
            f"- Fault reported by the customer: {_text(r.reported_fault)}",
            f"- Technician's diagnosis: {_text(r.diagnosis) if r.diagnosis else 'not diagnosed yet'}",
        ]
        if r.resolution:
            lines.append(f"- Work done: {_text(r.resolution)}")
        lines += [
            f"- Store: {r.store}",
            f"- Received on: {_day(r.received_at, zone)}",
        ]
        if r.ready_on and r.status != "collected":
            lines.append(f"- Ready on: {_day(r.ready_on, zone)}")
        if r.collected_at:
            lines.append(f"- Collected on: {_day(r.collected_at, zone)}")
        lines.append(f"- Under warranty: {'yes' if r.under_warranty else 'no'}")
        lines.append(f"- Cost: {_money(r.cost_cents) if r.cost_cents is not None else 'not quoted yet'}")
        if r.order_number:
            lines.append(f"- Bought in order: {r.order_number}")
    lines += ["Totals", *_totals(records), "Timeline", *_timeline(records, zone)]
    window = window_in(question, today.date())
    if window is not None:  # the question names dates: the matching orders, filtered here, not by the model
        lines += [f"Matching the question ({window.label})", *_in_window(records, window, zone)]
    return "\n".join(lines)


def _timeline(records: Records, zone: ZoneInfo) -> list[str]:
    """Deliveries in date order, so "what's coming after 15 November" or "what arrived this month" is read off
    one list instead of pieced together from every order."""
    coming = [o for o in records.orders if o.status in OPEN_ORDER_STATUSES and o.expected_on]
    arrived = [o for o in records.orders if o.delivered_at]
    lines = []
    if coming:
        coming.sort(key=lambda o: o.expected_on or date.max)
        steps = "; ".join(
            f"{o.order_number} ({_items(o)}) on {_day(o.expected_on, zone)}" for o in coming if o.expected_on
        )
        lines.append(f"- Expected deliveries, earliest first: {steps}")
    if arrived:
        arrived.sort(key=lambda o: o.delivered_at or o.placed_at)
        steps = "; ".join(
            f"{o.order_number} ({_items(o)}) on {_day(o.delivered_at, zone)}"
            for o in arrived
            if o.delivered_at
        )
        lines.append(f"- Deliveries received, earliest first: {steps}")
    return lines


def _items(order: OrderView) -> str:
    return ", ".join(line.name for line in order.lines)


def _totals(records: Records) -> list[str]:
    """Counts and sums people ask for ("total cost of the orders ready for pickup"), worked out here so the
    answer never depends on the model's arithmetic."""
    items = sum(line.quantity for o in records.orders for line in o.lines)
    lines = [
        f"- Orders: {len(records.orders)}, together {_money(sum(o.total_cents for o in records.orders))}",
        f"- Items across these orders: {items}",
    ]
    for label, statuses in (
        ("Orders not delivered yet (placed, being prepared or on its way)", OPEN_ORDER_STATUSES),
        ("Orders already delivered or collected", ("delivered",)),
    ):
        group = [o for o in records.orders if o.status in statuses]
        if group:
            numbers = ", ".join(o.order_number for o in group)
            total = _money(sum(o.total_cents for o in group))
            lines.append(f"- {label}: {len(group)} ({numbers}), together {total}")
    for status, label in ORDER_STATUS.items():
        group = [o for o in records.orders if o.status == status]
        if group:
            numbers = ", ".join(o.order_number for o in group)
            total = _money(sum(o.total_cents for o in group))
            lines.append(f"- Orders {label.lower()}: {len(group)} ({numbers}), together {total}")
    lines.append(f"- Repairs: {len(records.repairs)}")
    for status, label in REPAIR_STATUS.items():
        jobs = [r for r in records.repairs if r.status == status]
        if jobs:
            numbers = ", ".join(r.reference for r in jobs)
            cost = sum(r.cost_cents or 0 for r in jobs)
            lines.append(
                f"- Repairs {label.lower()}: {len(jobs)} ({numbers}), costing {_money(cost)} together"
            )
    return lines


def _text(value: str) -> str:
    """Free text written at the repair desk: one line, no markup that could pose as a new record."""
    return " ".join(value.replace("<", "(").replace(">", ")").split())[:300]


def grounded(answer: str, facts: str, question: str = "") -> bool:
    """Every order/repair reference and every number in the answer appears in the fact sheet (or in the
    question: repeating the customer's own "after November 15" isn't inventing)."""
    text = answer.strip()
    if not text or len(text) > MAX_ANSWER_CHARS:
        return False
    known_refs = {ref.upper() for ref in _REFERENCE.findall(facts)}
    if any(ref.upper() not in known_refs for ref in _REFERENCE.findall(text)):
        return False
    known_numbers = set(_NUMBER.findall(facts)) | set(_NUMBER.findall(question))
    return all(n in known_numbers for n in _NUMBER.findall(_REFERENCE.sub(" ", text)))


def summary(records: Records, timezone: str) -> str:
    """A plain answer from the records alone, for when the model's answer can't be used."""
    zone = ZoneInfo(timezone)
    parts: list[str] = []
    for o in records.orders[:5]:
        sentence = f"Order {o.order_number} is {_ORDER_STATE.get(o.status, o.status)}"
        if o.delivered_at:
            sentence += f", delivered on {_day(o.delivered_at, zone, iso=False)}"
        elif o.pickup_store and o.status == "ready_for_pickup":
            sentence += f" at {o.pickup_store}"
            if o.pickup_by:
                sentence += f" until {_day(o.pickup_by, zone, iso=False)}"
        elif o.expected_on and o.status in OPEN_ORDER_STATUSES:
            sentence += f", expected on {_day(o.expected_on, zone, iso=False)}"
        parts.append(sentence + ".")
    for r in records.repairs[:3]:
        parts.append(
            f"Repair {r.reference} ({r.product_name}) is {REPAIR_STATUS.get(r.status, r.status).lower()}"
            f" at {r.store}."
        )
    return " ".join(parts) + " The details are below."


# ---------- date windows in the question ----------

_MONTHS = {
    name.lower(): i
    for i, name in enumerate(
        "January February March April May June July August September October November December".split(),
        start=1,
    )
}
_MONTH = "(" + "|".join(sorted({*_MONTHS, *(m[:3] for m in _MONTHS)}, key=len, reverse=True)) + r")"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_EDGE = re.compile(
    r"\b(after|since|from|before|until|till|by)\s+(?:the\s+)?(?:"
    + _MONTH
    + r"\.?\s+"
    + _DAY
    + r"|"
    + _DAY
    + r"(?:\s+of)?\s+"
    + _MONTH
    + r")",
    re.IGNORECASE,
)
_RELATIVE = re.compile(r"\b(this|next|last)\s+month\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Window:
    """The dates a question is about: [start, end], either side open."""

    label: str
    start: date | None = None
    end: date | None = None

    def holds(self, day: date) -> bool:
        return (self.start is None or day >= self.start) and (self.end is None or day <= self.end)


def window_in(question: str, today: date) -> Window | None:
    """ "after November 15", "before 20th Nov", "this month", "next month"... or None."""
    edge = _EDGE.search(question)
    if edge:
        word = edge.group(1).lower()
        month_name = (edge.group(2) or edge.group(5) or "").lower()
        day_text = edge.group(3) or edge.group(4)
        month = _MONTHS.get(month_name) or next(
            i for name, i in _MONTHS.items() if name.startswith(month_name[:3])
        )
        year = today.year + (1 if month < today.month - 6 else 0)  # "after January 5" in November: next year
        try:
            day = date(year, month, int(day_text))
        except ValueError:
            return None
        nice = f"{day:%A} {day.day} {day:%B %Y}"
        if word in ("after",):
            return Window(f"after {nice}", start=day + timedelta(days=1))
        if word in ("since", "from"):
            return Window(f"from {nice} on", start=day)
        if word == "before":
            return Window(f"before {nice}", end=day - timedelta(days=1))
        return Window(f"up to {nice}", end=day)  # until, till, by
    relative = _RELATIVE.search(question)
    if relative:
        shift = {"this": 0, "next": 1, "last": -1}[relative.group(1).lower()]
        month_index = today.year * 12 + today.month - 1 + shift
        first = date(month_index // 12, month_index % 12 + 1, 1)
        following = date((month_index + 1) // 12, (month_index + 1) % 12 + 1, 1)
        return Window(f"in {first:%B %Y}", start=first, end=following - timedelta(days=1))
    return None


def _in_window(records: Records, window: Window, zone: ZoneInfo) -> list[str]:
    """The orders whose expected or actual delivery falls in the question's window, worked out here."""
    expected = sorted(
        (
            o
            for o in records.orders
            if o.status in OPEN_ORDER_STATUSES and o.expected_on and window.holds(o.expected_on)
        ),
        key=lambda o: o.expected_on or date.max,
    )
    delivered = sorted(
        (
            o
            for o in records.orders
            if o.delivered_at and window.holds(o.delivered_at.astimezone(zone).date())
        ),
        key=lambda o: o.delivered_at or o.placed_at,
    )

    def listed(orders: list[OrderView], when: str) -> str:
        if not orders:
            return "none"
        return "; ".join(f"{o.order_number} ({_items(o)}) on {_day(getattr(o, when), zone)}" for o in orders)

    return [
        f"- Orders expected {window.label}: {len(expected)}: {listed(expected, 'expected_on')}",
        f"- Orders delivered {window.label}: {len(delivered)}: {listed(delivered, 'delivered_at')}",
    ]


# ---------- what the client shows ----------


def to_record(record: OrderView | RepairView) -> AccountRecord:
    return order_record(record) if isinstance(record, OrderView) else repair_record(record)


def order_record(record: OrderView) -> OrderRecord:
    return OrderRecord(
        order_number=record.order_number,
        status=record.status,
        delivery_method=record.delivery_method,
        placed_at=record.placed_at,
        shipped_at=record.shipped_at,
        delivered_at=record.delivered_at,
        expected_on=record.expected_on,
        carrier=record.carrier,
        tracking_number=record.tracking_number,
        total_cents=record.total_cents,
        pickup_store=record.pickup_store,
        pickup_by=record.pickup_by,
        lines=[
            OrderLineRecord(
                name=line.name, sku=line.sku, quantity=line.quantity, unit_price_cents=line.unit_price_cents
            )
            for line in record.lines
        ],
    )


def repair_record(record: RepairView) -> RepairRecord:
    return RepairRecord(
        reference=record.reference,
        product_name=record.product_name,
        status=record.status,
        reported_fault=record.reported_fault,
        diagnosis=record.diagnosis,
        resolution=record.resolution,
        store=record.store,
        under_warranty=record.under_warranty,
        cost_cents=record.cost_cents,
        received_at=record.received_at,
        ready_on=record.ready_on,
        collected_at=record.collected_at,
        order_number=record.order_number,
    )


def shown(records: Records, answer: str, question: str = "", limit: int = 5) -> list[AccountRecord]:
    """The cards under the answer: the records it names; else the ones whose products, items or faults share
    words with the question and answer ("is my laptop ready" -> the TechBook repair); else the newest few."""
    named = {ref.upper() for ref in _REFERENCE.findall(answer)}
    every: list[OrderView | RepairView] = [*records.orders, *records.repairs]
    chosen = [r for r in every if _number(r) in named]
    if not chosen:  # the products the answer mentions ("you received the TechBook 14 and the Earbuds E3")
        lowered = answer.lower()
        chosen = [r for r in every if any(name.lower() in lowered for name in _names(r))]
    if not chosen:
        asked = content_terms(f"{question} {answer}")
        scores = [(len(asked & content_terms(_words(r))), r) for r in every]
        best = max((score for score, _ in scores), default=0)
        chosen = [r for score, r in scores if best and score == best] or every
    return [to_record(r) for r in chosen[:limit]]


def _number(record: OrderView | RepairView) -> str:
    return record.order_number if isinstance(record, OrderView) else record.reference


def _names(record: OrderView | RepairView) -> list[str]:
    return [line.name for line in record.lines] if isinstance(record, OrderView) else [record.product_name]


def _words(record: OrderView | RepairView) -> str:
    if isinstance(record, OrderView):
        return " ".join(["order", record.status, *(line.name for line in record.lines)])
    found = [record.reported_fault, record.diagnosis or "", record.resolution or ""]
    return " ".join(["repair", record.product_name, *found])
