"""The review queue for unanswered questions and not-helpful answers (plan §2.2, ADR-030). Managers and admins
only, like the quality dashboard; every decision is audited."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.api.analytics import require_analytics
from app.api.deps import EngineDep
from app.api.errors import COMMON_ERRORS, ApiError, request_id
from app.core.permissions import Principal
from app.db.session import tenant_session
from app.services import reviews
from app.services.versions import NotFound

router = APIRouter(prefix="/api/v1/admin", tags=["reviews"], responses=COMMON_ERRORS)

ManagerDep = Annotated[Principal, Depends(require_analytics)]
QueueStatus = Literal["open", "resolved", "dismissed"]


class FeedbackOut(BaseModel):
    reasons: list[str]
    note: str | None
    created_at: datetime


class DocumentRef(BaseModel):
    id: uuid.UUID
    title: str


class ReviewOut(BaseModel):
    answer_id: uuid.UUID
    asked_at: datetime
    intent: str | None
    answer_status: str
    reasons: list[Literal["unanswered", "not_helpful"]]
    question: str  # redacted
    answer: str  # redacted, first 1,000 characters
    feedback: list[FeedbackOut]
    status: QueueStatus
    note: str | None
    document: DocumentRef | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class ReviewPage(BaseModel):
    items: list[ReviewOut]
    total: int
    counts: dict[str, int]


class DecisionIn(BaseModel):
    status: Literal["resolved", "dismissed"]
    note: Annotated[str | None, Field(max_length=1000)] = None
    document_id: uuid.UUID | None = None  # the document that now answers it


def _out(item: reviews.ReviewItem) -> ReviewOut:
    data: dict[str, Any] = {f: getattr(item, f) for f in ReviewOut.model_fields}
    return ReviewOut.model_validate(data)


def _not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "That question isn't in the review queue.")


@router.get("/reviews")
def list_reviews(
    principal: ManagerDep,
    engine: EngineDep,
    status_: Annotated[QueueStatus, Query(alias="status")] = "open",
    reason: Literal["unanswered", "not_helpful"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewPage:
    """Newest first. `counts` has every status for the same reason filter."""
    with tenant_session(principal.organization_id, engine) as session:
        found, total, counts = reviews.queue(
            session, status=status_, reason=reason, limit=limit, offset=offset
        )
        return ReviewPage(items=[_out(i) for i in found], total=total, counts=counts)


@router.put("/reviews/{answer_id}")
def decide(
    answer_id: uuid.UUID, body: DecisionIn, request: Request, principal: ManagerDep, engine: EngineDep
) -> ReviewOut:
    """Resolve or dismiss a queued question; deciding again replaces the earlier decision."""
    note = body.note.strip() if body.note else None
    try:
        with tenant_session(principal.organization_id, engine) as session:
            item = reviews.decide(
                session,
                principal,
                answer_id,
                status=body.status,
                note=note,
                document_id=body.document_id,
                request_id=request_id(request),
            )
            return _out(item)
    except NotFound:
        raise _not_found() from None
    except ValueError as exc:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "VALIDATION_ERROR",
            "Some fields are missing or invalid.",
            {"fields": [{"field": "document_id", "message": str(exc)}]},
        ) from None


@router.delete("/reviews/{answer_id}", status_code=status.HTTP_204_NO_CONTENT)
def reopen(answer_id: uuid.UUID, request: Request, principal: ManagerDep, engine: EngineDep) -> Response:
    """Put a decided question back in the open queue."""
    try:
        with tenant_session(principal.organization_id, engine) as session:
            reviews.reopen(session, principal, answer_id, request_id(request))
    except NotFound:
        raise _not_found() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
