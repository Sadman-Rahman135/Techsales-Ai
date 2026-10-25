"""Support tickets for staff (ADR-029, employee-assistant.md "Ticket handoff"). Employees, managers and admins
only; customers never see this API. Every change is audited (`audit_events`)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import ChatModelDep, EngineDep, PrincipalDep
from app.api.errors import COMMON_ERRORS, ApiError, request_id
from app.db.models import SupportTicket, TicketNote, TicketStatus
from app.db.session import tenant_session
from app.services import tickets
from app.services.versions import NotFound, PermissionDenied

router = APIRouter(prefix="/api/v1", tags=["tickets"], responses=COMMON_ERRORS)

Priority = Literal["low", "normal", "high"]
Status = Literal["open", "in_progress", "resolved"]


def _forbidden() -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "Support tickets are for TechCare staff.")


def _not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Ticket not found.")


class DraftIn(BaseModel):
    conversation_id: uuid.UUID


class DraftOut(BaseModel):
    subject: str
    summary: str
    drafted_by_model: bool = Field(
        description="False when the model was unavailable and a plain draft was used"
    )


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Annotated[str, Field(min_length=1, max_length=200)]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    priority: Priority = "normal"
    conversation_id: uuid.UUID | None = None
    order_number: Annotated[str | None, Field(max_length=20)] = None
    summary_drafted: bool = False


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status | None = None
    priority: Priority | None = None
    assigned_to: uuid.UUID | None = None


class NoteIn(BaseModel):
    body: Annotated[str, Field(min_length=1, max_length=4000)]


class NoteOut(BaseModel):
    id: uuid.UUID
    author: str | None
    body: str
    created_at: datetime


class TicketOut(BaseModel):
    id: uuid.UUID
    number: int
    reference: str = Field(description='"T-0042"')
    subject: str
    summary: str
    summary_drafted: bool
    status: Status
    priority: Priority
    order_number: str | None
    customer_ref: str | None
    conversation_id: uuid.UUID | None
    created_by: str | None
    assigned_to: uuid.UUID | None
    assignee: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class TicketDetailOut(TicketOut):
    notes: list[NoteOut]


class TicketPage(BaseModel):
    items: list[TicketOut]
    total: int
    counts: dict[str, int] = Field(description="Open tickets per status, for the queue's tabs")


class StaffOut(BaseModel):
    user_id: uuid.UUID
    name: str


def _views(session: Session, rows: list[SupportTicket]) -> list[TicketOut]:
    names = tickets.labels(session, {u for t in rows for u in (t.created_by, t.assigned_to) if u})
    return [
        TicketOut(
            id=t.id,
            number=t.number,
            reference=f"T-{t.number:04d}",
            subject=t.subject,
            summary=t.summary,
            summary_drafted=t.summary_drafted,
            status=t.status,
            priority=t.priority,
            order_number=t.order_number,
            customer_ref=tickets.customer_ref(session, t.customer_id),
            conversation_id=t.conversation_id,
            created_by=names.get(t.created_by) if t.created_by else None,
            assigned_to=t.assigned_to,
            assignee=names.get(t.assigned_to) if t.assigned_to else None,
            created_at=t.created_at,
            updated_at=t.updated_at,
            resolved_at=t.resolved_at,
        )
        for t in rows
    ]


@router.post("/tickets/draft")
def draft_ticket(body: DraftIn, principal: PrincipalDep, engine: EngineDep, llm: ChatModelDep) -> DraftOut:
    """A subject and summary drafted by the local model from the employee's own conversation. Nothing is
    saved: the employee reviews and edits it first."""
    try:
        with tenant_session(principal.organization_id, engine) as session:
            result = tickets.draft(session, principal, llm, body.conversation_id)
    except PermissionDenied:
        raise _forbidden() from None
    except NotFound:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Conversation not found.") from None
    return DraftOut(subject=result.subject, summary=result.summary, drafted_by_model=result.drafted_by_model)


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
def create_ticket(body: TicketIn, request: Request, principal: PrincipalDep, engine: EngineDep) -> TicketOut:
    try:
        with tenant_session(principal.organization_id, engine) as session:
            ticket = tickets.create(
                session,
                principal,
                subject=body.subject,
                summary=body.summary,
                priority=body.priority,
                conversation_id=body.conversation_id,
                order_number=body.order_number,
                drafted=body.summary_drafted,
                request_id=request_id(request),
            )
            return _views(session, [ticket])[0]
    except PermissionDenied:
        raise _forbidden() from None
    except NotFound:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "Conversation not found.") from None


@router.get("/tickets")
def list_tickets(
    principal: PrincipalDep,
    engine: EngineDep,
    status_: Annotated[Status | None, Query(alias="status")] = None,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TicketPage:
    """The queue, newest first. `mine=true`: assigned to the caller."""
    if not principal.is_staff:
        raise _forbidden()
    with tenant_session(principal.organization_id, engine) as session:
        query = select(SupportTicket)
        if status_:
            query = query.where(SupportTicket.status == status_)
        if mine:
            query = query.where(SupportTicket.assigned_to == principal.user_id)
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(session.scalars(query.order_by(SupportTicket.number.desc()).limit(limit).offset(offset)))
        query_counts = select(SupportTicket.status, func.count()).group_by(SupportTicket.status)
        counts = {row_status: n for row_status, n in session.execute(query_counts).all()}
        return TicketPage(
            items=_views(session, rows),
            total=total,
            counts={s.value: counts.get(s.value, 0) for s in TicketStatus},
        )


@router.get("/tickets/staff")
def ticket_staff(principal: PrincipalDep, engine: EngineDep) -> list[StaffOut]:
    """Who a ticket can be assigned to."""
    if not principal.is_staff:
        raise _forbidden()
    with tenant_session(principal.organization_id, engine) as session:
        return [StaffOut(user_id=s.user_id, name=s.name) for s in tickets.staff(session, principal)]


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: uuid.UUID, principal: PrincipalDep, engine: EngineDep) -> TicketDetailOut:
    try:
        with tenant_session(principal.organization_id, engine) as session:
            ticket = tickets.get(session, principal, ticket_id)
            notes = list(
                session.scalars(
                    select(TicketNote)
                    .where(TicketNote.ticket_id == ticket.id)
                    .order_by(TicketNote.created_at)
                )
            )
            authors = tickets.labels(session, {n.author_id for n in notes if n.author_id})
            view = _views(session, [ticket])[0]
            return TicketDetailOut(
                **view.model_dump(),
                notes=[
                    NoteOut(
                        id=n.id,
                        author=authors.get(n.author_id) if n.author_id else None,
                        body=n.body,
                        created_at=n.created_at,
                    )
                    for n in notes
                ],
            )
    except PermissionDenied:
        raise _forbidden() from None
    except NotFound:
        raise _not_found() from None


@router.patch("/tickets/{ticket_id}")
def update_ticket(
    ticket_id: uuid.UUID, body: TicketUpdate, request: Request, principal: PrincipalDep, engine: EngineDep
) -> TicketOut:
    """Change status, priority or assignee (send null to unassign). Only the fields sent change."""
    changes = {key: getattr(body, key) for key in body.model_fields_set}
    if changes.get("status") is None:
        changes.pop("status", None)
    if changes.get("priority") is None:
        changes.pop("priority", None)
    try:
        with tenant_session(principal.organization_id, engine) as session:
            ticket = tickets.update(session, principal, ticket_id, changes, request_id(request))
            return _views(session, [ticket])[0]
    except PermissionDenied:
        raise _forbidden() from None
    except NotFound:
        raise _not_found() from None
    except ValueError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "VALIDATION_ERROR",
            "Some fields are missing or invalid.",
            {"fields": [{"field": "assigned_to", "message": str(exc)}]},
        ) from None


@router.post("/tickets/{ticket_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(
    ticket_id: uuid.UUID, body: NoteIn, request: Request, principal: PrincipalDep, engine: EngineDep
) -> NoteOut:
    try:
        with tenant_session(principal.organization_id, engine) as session:
            note = tickets.add_note(session, principal, ticket_id, body.body.strip(), request_id(request))
            author = tickets.labels(session, {principal.user_id} if principal.user_id else set())
            return NoteOut(
                id=note.id,
                author=author.get(principal.user_id) if principal.user_id else None,
                body=note.body,
                created_at=note.created_at,
            )
    except PermissionDenied:
        raise _forbidden() from None
    except NotFound:
        raise _not_found() from None
