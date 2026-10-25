"""The review queue (plan §2.2 "administrative review workflows", ADR-030): unanswered questions and answers
rated not helpful, for managers to resolve (a document added or fixed) or dismiss.

An answer is in the queue when it was refused for lack of evidence or got a not-helpful rating (evaluation
runs never are). It stays open until a manager decides; reopening deletes the decision. Question and answer
text are redacted (app/core/privacy.py) like the quality dashboard; the full conversation stays restricted.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.permissions import Principal
from app.core.privacy import redact_personal
from app.core.time import utcnow
from app.db.models import (
    Answer,
    AnswerFeedback,
    AnswerReview,
    AnswerStatus,
    Document,
    Message,
    Rating,
    ReviewStatus,
)
from app.services import audit
from app.services.analytics import questions_for
from app.services.tickets import labels
from app.services.versions import NotFound

OPEN = "open"
ANSWER_CHARS = 1000  # enough to judge the answer; the page is not a conversation viewer


@dataclass(frozen=True, slots=True)
class ReviewItem:
    answer_id: uuid.UUID
    asked_at: datetime
    intent: str | None
    answer_status: str
    reasons: list[str]  # "unanswered", "not_helpful"
    question: str
    answer: str
    feedback: list[dict[str, Any]] = field(default_factory=list)
    status: str = OPEN
    note: str | None = None
    document: dict[str, Any] | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


def _not_helpful() -> Any:
    return exists().where(
        AnswerFeedback.answer_id == Answer.id, AnswerFeedback.rating == Rating.NOT_HELPFUL.value
    )


def _in_queue() -> tuple[Any, ...]:
    unanswered = Answer.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE.value
    customer_traffic = or_(Answer.caller_role.is_(None), Answer.caller_role != "evaluation")
    return customer_traffic, or_(unanswered, _not_helpful())


def queue(
    session: Session, *, status: str = OPEN, reason: str | None = None, limit: int = 25, offset: int = 0
) -> tuple[list[ReviewItem], int, dict[str, int]]:
    """One page of the queue, newest first, with the total and the count per status."""
    state = func.coalesce(AnswerReview.status, OPEN)
    base = select(Answer.id).outerjoin(AnswerReview, AnswerReview.answer_id == Answer.id).where(*_in_queue())
    if reason == "unanswered":
        base = base.where(Answer.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE.value)
    elif reason == "not_helpful":
        base = base.where(_not_helpful())
    counts = {s: 0 for s in (OPEN, *(r.value for r in ReviewStatus))}
    for name, count in session.execute(
        base.with_only_columns(state, func.count()).group_by(state).order_by(None)
    ):
        counts[name] = count
    page = base.where(state == status)
    total = counts.get(status, 0)
    ids = list(session.scalars(page.order_by(Answer.created_at.desc()).limit(limit).offset(offset)))
    return items(session, ids), total, counts


def items(session: Session, answer_ids: list[uuid.UUID]) -> list[ReviewItem]:
    if not answer_ids:
        return []
    rows = session.execute(
        questions_for(
            select(
                Answer.id,
                Answer.created_at,
                Answer.intent,
                Answer.answer_status,
                Answer.message_id,
                _not_helpful().label("not_helpful"),
            )
        ).where(Answer.id.in_(answer_ids))
    ).all()
    answers = {
        message_id: content
        for message_id, content in session.execute(
            select(Message.id, Message.content).where(Message.id.in_([r.message_id for r in rows]))
        )
    }
    feedback: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for answer_id, reasons, note, created_at in session.execute(
        select(
            AnswerFeedback.answer_id, AnswerFeedback.reasons, AnswerFeedback.note, AnswerFeedback.created_at
        )
        .where(AnswerFeedback.answer_id.in_(answer_ids), AnswerFeedback.rating == Rating.NOT_HELPFUL.value)
        .order_by(AnswerFeedback.created_at.desc())
    ):
        feedback.setdefault(answer_id, []).append(
            {
                "reasons": list(reasons or []),
                "note": redact_personal(note) if note else None,
                "created_at": created_at,
            }
        )
    reviews = {
        r.answer_id: r
        for r in session.scalars(select(AnswerReview).where(AnswerReview.answer_id.in_(answer_ids)))
    }
    documents = {
        document_id: title
        for document_id, title in session.execute(
            select(Document.id, Document.title).where(
                Document.id.in_({r.document_id for r in reviews.values() if r.document_id}),
                Document.deleted_at.is_(None),
            )
        )
    }
    names = labels(session, {r.reviewed_by for r in reviews.values() if r.reviewed_by})
    by_id = {}
    for row in rows:
        review = reviews.get(row.id)
        reasons = []
        if row.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE.value:
            reasons.append("unanswered")
        if row.not_helpful:
            reasons.append("not_helpful")
        document = None
        if review and review.document_id in documents:
            document = {"id": review.document_id, "title": documents[review.document_id]}
        by_id[row.id] = ReviewItem(
            answer_id=row.id,
            asked_at=row.created_at,
            intent=row.intent,
            answer_status=row.answer_status,
            reasons=reasons,
            question=redact_personal(row[-1] or ""),
            answer=redact_personal(answers.get(row.message_id, ""))[:ANSWER_CHARS],
            feedback=feedback.get(row.id, []),
            status=review.status if review else OPEN,
            note=review.note if review else None,
            document=document,
            reviewed_by=names.get(review.reviewed_by) if review and review.reviewed_by else None,
            reviewed_at=review.reviewed_at if review else None,
        )
    return [by_id[i] for i in answer_ids if i in by_id]


def _queued(session: Session, answer_id: uuid.UUID) -> None:
    found = session.scalar(select(Answer.id).where(Answer.id == answer_id, *_in_queue()))
    if found is None:
        raise NotFound("not in the review queue")  # another tenant's answer looks the same (RLS)


def decide(
    session: Session,
    principal: Principal,
    answer_id: uuid.UUID,
    *,
    status: str,
    note: str | None,
    document_id: uuid.UUID | None,
    request_id: str | None,
) -> ReviewItem:
    """Resolve or dismiss; deciding again replaces the earlier decision. Audited."""
    _queued(session, answer_id)
    if document_id is not None:
        live = session.scalar(
            select(Document.id).where(Document.id == document_id, Document.deleted_at.is_(None))
        )
        if live is None:
            raise ValueError("Choose a document that still exists.")
    review = session.scalar(select(AnswerReview).where(AnswerReview.answer_id == answer_id))
    if review is None:
        review = AnswerReview(organization_id=principal.organization_id, answer_id=answer_id)
        session.add(review)
    review.status = status
    review.note = note or None
    review.document_id = document_id
    review.reviewed_by = principal.user_id
    review.reviewed_at = utcnow()
    details: dict[str, object] = {}
    if document_id:
        details["document_id"] = str(document_id)
    audit.record(session, principal, f"review.{status}", "answer", answer_id, details, request_id=request_id)
    session.flush()
    return items(session, [answer_id])[0]


def reopen(session: Session, principal: Principal, answer_id: uuid.UUID, request_id: str | None) -> None:
    _queued(session, answer_id)
    review = session.scalar(select(AnswerReview).where(AnswerReview.answer_id == answer_id))
    if review is not None:
        session.delete(review)
        audit.record(session, principal, "review.reopened", "answer", answer_id, request_id=request_id)
