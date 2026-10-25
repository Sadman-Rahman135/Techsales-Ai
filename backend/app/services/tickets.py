"""Support tickets (ADR-029): the employee assistant's handoff to the support team (employee-assistant.md).

An employee drafts a ticket from their own conversation; the local model writes a draft summary, and the
employee reviews and edits it before it is saved (content-guide.md: "Review the summary before sending").
Staff then work the queue: status, priority, assignment and internal notes, every change audited.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.permissions import STAFF_ROLES, Principal
from app.core.time import utcnow
from app.db.models import (
    Conversation,
    Customer,
    Membership,
    Message,
    MessageRole,
    Order,
    SupportTicket,
    TicketNote,
    TicketStatus,
    User,
)
from app.rag.llm import ChatModel, ChatModelError, FakeChatModel
from app.services import audit
from app.services.orders import normalize_number
from app.services.versions import NotFound, PermissionDenied

TRANSCRIPT_MESSAGES = 20  # the most recent turns only
TRANSCRIPT_CHARS = 600  # per message


@dataclass(frozen=True, slots=True)
class Draft:
    subject: str
    summary: str
    drafted_by_model: bool  # False when the model failed and the plain fallback was used


def _require_staff(principal: Principal) -> None:
    if not principal.is_staff:
        raise PermissionDenied("support tickets are for staff")


def _own_conversation(session: Session, principal: Principal, conversation_id: uuid.UUID) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None or conversation.owner_user_id != principal.user_id:
        raise NotFound("conversation not found")  # another person's conversation looks the same as none
    return conversation


def transcript(session: Session, conversation_id: uuid.UUID) -> str:
    rows = list(
        session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(TRANSCRIPT_MESSAGES)
        )
    )
    lines = []
    for message in reversed(rows):
        label = "Question" if message.role == MessageRole.USER.value else "Answer"
        lines.append(f"{label}: {' '.join(message.content.split())[:TRANSCRIPT_CHARS]}")
    return "\n".join(lines)


def draft(session: Session, principal: Principal, llm: ChatModel, conversation_id: uuid.UUID) -> Draft:
    """A subject and summary for the employee to review. A model failure falls back to a plain summary."""
    _require_staff(principal)
    _own_conversation(session, principal, conversation_id)
    text_ = transcript(session, conversation_id)
    first = next((line.split(": ", 1)[1] for line in text_.splitlines() if line.startswith("Question: ")), "")
    subject = (first[:117] + "…") if len(first) > 120 else (first or "Assistant conversation")
    try:
        summary, by_model = llm.draft_case_summary(text_), True
    except ChatModelError:
        summary, by_model = FakeChatModel().draft_case_summary(text_), False
    return Draft(subject, summary.strip() or FakeChatModel().draft_case_summary(text_), by_model)


def create(
    session: Session,
    principal: Principal,
    *,
    subject: str,
    summary: str,
    priority: str,
    conversation_id: uuid.UUID | None,
    order_number: str | None,
    drafted: bool,
    request_id: str | None,
) -> SupportTicket:
    _require_staff(principal)
    org = principal.organization_id
    if conversation_id is not None:
        _own_conversation(session, principal, conversation_id)
    customer_id = None
    number_ref = normalize_number(order_number) if order_number else None
    if number_ref:
        customer_id = session.scalar(
            select(Order.customer_id).where(Order.organization_id == org, Order.order_number == number_ref)
        )
    # Ticket numbers count up per organization; the advisory lock makes concurrent creates take turns.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"ticket:{org}"})
    number = (session.scalar(select(func.max(SupportTicket.number))) or 0) + 1
    ticket = SupportTicket(
        organization_id=org,
        number=number,
        subject=subject.strip(),
        summary=summary.strip(),
        summary_drafted=drafted,
        priority=priority,
        customer_id=customer_id,
        conversation_id=conversation_id,
        order_number=number_ref,
        created_by=principal.user_id,
    )
    session.add(ticket)
    session.flush()
    audit.record(
        session, principal, "ticket.created", "ticket", ticket.id, {"number": number}, request_id=request_id
    )
    return ticket


def get(session: Session, principal: Principal, ticket_id: uuid.UUID) -> SupportTicket:
    _require_staff(principal)
    ticket = session.get(SupportTicket, ticket_id)
    if ticket is None:
        raise NotFound("ticket not found")
    return ticket


def update(
    session: Session,
    principal: Principal,
    ticket_id: uuid.UUID,
    changes: dict[str, object],
    request_id: str | None,
) -> SupportTicket:
    """Status, priority and assignee. An assignee must be staff in this organization."""
    ticket = get(session, principal, ticket_id)
    if "assigned_to" in changes and changes["assigned_to"] is not None:
        if changes["assigned_to"] not in {s.user_id for s in staff(session, principal)}:
            raise ValueError("the assignee must be staff in this organization")
    before = {k: getattr(ticket, k) for k in changes}
    for key, value in changes.items():
        setattr(ticket, key, value)
    if "status" in changes:
        ticket.resolved_at = utcnow() if ticket.status == TicketStatus.RESOLVED.value else None
    ticket.updated_at = utcnow()
    changed = {k: [str(before[k]), str(v)] for k, v in changes.items() if before[k] != v}
    if changed:
        audit.record(
            session, principal, "ticket.updated", "ticket", ticket.id, changed, request_id=request_id
        )
    session.flush()
    return ticket


def add_note(
    session: Session, principal: Principal, ticket_id: uuid.UUID, body: str, request_id: str | None
) -> TicketNote:
    ticket = get(session, principal, ticket_id)
    note = TicketNote(
        organization_id=principal.organization_id, ticket_id=ticket.id, author_id=principal.user_id, body=body
    )
    session.add(note)
    ticket.updated_at = utcnow()
    session.flush()
    audit.record(session, principal, "ticket.note_added", "ticket", ticket.id, request_id=request_id)
    return note


@dataclass(frozen=True, slots=True)
class StaffMember:
    user_id: uuid.UUID
    name: str


def staff(session: Session, principal: Principal) -> list[StaffMember]:
    """Who a ticket can be assigned to: staff memberships of this organization (no RLS on memberships)."""
    rows = session.execute(
        select(User.id, func.coalesce(User.display_name, User.email))
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == principal.organization_id,
            Membership.role.in_([r.value for r in STAFF_ROLES]),
        )
        .order_by(func.coalesce(User.display_name, User.email))
    ).all()
    return [StaffMember(user_id, name) for user_id, name in rows]


def customer_ref(session: Session, customer_id: uuid.UUID | None) -> str | None:
    return (
        session.scalar(select(Customer.external_ref).where(Customer.id == customer_id))
        if customer_id
        else None
    )


def labels(session: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = session.execute(
        select(User.id, func.coalesce(User.display_name, User.email)).where(User.id.in_(user_ids))
    )
    return {user_id: name for user_id, name in rows}


def stamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
