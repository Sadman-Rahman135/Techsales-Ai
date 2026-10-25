"""CSV exports for managers and admins (plan §2.2 "data export", ADR-030).

Each dataset is one tenant's rows for a date range (documents: every live document). The same redaction as the
quality dashboard applies: question, feedback and note text pass through `redact_personal`, conversations are
never exported, and tickets leave out their summaries. Cells that a spreadsheet would run as a formula are
quoted (CSV injection), and times are in the tenant's timezone.
"""

import codecs
import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.privacy import redact_personal
from app.db.models import (
    Answer,
    AnswerCitation,
    AnswerFeedback,
    AnswerReview,
    Document,
    DocumentVersion,
    Rating,
    SupportTicket,
    User,
)
from app.services.analytics import Range, questions_for

DATASETS = ("questions", "feedback", "reviews", "documents", "tickets")
MAX_ROWS = 50_000
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class TooLarge(Exception):
    """More rows than one export may hold; the caller narrows the range."""


def table(session: Session, dataset: str, period: Range) -> tuple[list[str], list[list[Any]]]:
    header, rows = _BUILDERS[dataset](session, period)
    found = list(rows)
    if len(found) > MAX_ROWS:
        raise TooLarge(dataset)
    return header, found


def to_csv(header: Sequence[str], rows: Iterable[Sequence[Any]], timezone: str) -> bytes:
    """UTF-8 with a byte-order mark, so spreadsheet apps read accents correctly."""
    zone = ZoneInfo(timezone)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([cell(v, zone) for v in row])
    return codecs.BOM_UTF8 + out.getvalue().encode("utf-8")


def cell(value: Any, zone: ZoneInfo) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.astimezone(zone).isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        value = "; ".join(str(v) for v in value)
    text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def _customer_traffic() -> Any:
    return or_(Answer.caller_role.is_(None), Answer.caller_role != "evaluation")


def _questions(session: Session, period: Range) -> tuple[list[str], Iterable[list[Any]]]:
    lo, hi = period.utc_bounds
    citations = (
        select(func.count()).where(AnswerCitation.answer_id == Answer.id).correlate(Answer).scalar_subquery()
    )

    def rated(rating: Rating) -> Any:
        return (
            select(func.count())
            .where(AnswerFeedback.answer_id == Answer.id, AnswerFeedback.rating == rating.value)
            .correlate(Answer)
            .scalar_subquery()
        )

    rows = session.execute(
        questions_for(
            select(
                Answer.created_at,
                Answer.id,
                Answer.caller_role,
                Answer.intent,
                Answer.answer_status,
                Answer.latency_ms,
                citations,
                rated(Rating.HELPFUL),
                rated(Rating.NOT_HELPFUL),
            )
        )
        .where(Answer.created_at >= lo, Answer.created_at < hi, _customer_traffic())
        .order_by(Answer.created_at)
        .limit(MAX_ROWS + 1)
    )
    header = [
        "asked_at",
        "answer_id",
        "caller",
        "intent",
        "outcome",
        "latency_ms",
        "citations",
        "helpful",
        "not_helpful",
        "question",
    ]
    return header, ([*row[:-1], redact_personal(row[-1] or "")] for row in rows)


def _feedback(session: Session, period: Range) -> tuple[list[str], Iterable[list[Any]]]:
    lo, hi = period.utc_bounds
    rows = session.execute(
        questions_for(
            select(
                AnswerFeedback.created_at,
                AnswerFeedback.answer_id,
                AnswerFeedback.rating,
                AnswerFeedback.reasons,
                AnswerFeedback.note,
            ).join(Answer, Answer.id == AnswerFeedback.answer_id)
        )
        .where(AnswerFeedback.created_at >= lo, AnswerFeedback.created_at < hi)
        .order_by(AnswerFeedback.created_at)
        .limit(MAX_ROWS + 1)
    )
    header = ["rated_at", "answer_id", "rating", "reasons", "note", "question"]
    return header, (
        [
            at,
            answer_id,
            rating,
            list(reasons or []),
            redact_personal(note) if note else None,
            redact_personal(q or ""),
        ]
        for at, answer_id, rating, reasons, note, q in rows
    )


def _reviews(session: Session, period: Range) -> tuple[list[str], Iterable[list[Any]]]:
    lo, hi = period.utc_bounds
    reviewer = aliased(User)
    rows = session.execute(
        questions_for(
            select(
                AnswerReview.reviewed_at,
                AnswerReview.answer_id,
                Answer.answer_status,
                AnswerReview.status,
                Document.title,
                AnswerReview.note,
                func.coalesce(reviewer.display_name, reviewer.email),
            )
            .join(Answer, Answer.id == AnswerReview.answer_id)
            .outerjoin(Document, Document.id == AnswerReview.document_id)
            .outerjoin(reviewer, reviewer.id == AnswerReview.reviewed_by)
        )
        .where(AnswerReview.reviewed_at >= lo, AnswerReview.reviewed_at < hi)
        .order_by(AnswerReview.reviewed_at)
        .limit(MAX_ROWS + 1)
    )
    header = [
        "reviewed_at",
        "answer_id",
        "outcome",
        "decision",
        "document",
        "note",
        "reviewed_by",
        "question",
    ]
    return header, (
        [*row[:5], redact_personal(row[5]) if row[5] else None, row[6], redact_personal(row[7] or "")]
        for row in rows
    )


def _documents(session: Session, period: Range) -> tuple[list[str], Iterable[list[Any]]]:
    active = aliased(DocumentVersion)
    versions = (
        select(func.count())
        .where(DocumentVersion.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            Document.id,
            Document.title,
            Document.audience,
            Document.category,
            Document.product_model,
            Document.brand,
            Document.department,
            Document.language,
            Document.effective_date,
            Document.tags,
            active.version_number,
            active.published_at,
            versions,
            Document.created_at,
            Document.updated_at,
        )
        .outerjoin(active, active.id == Document.active_version_id)
        .where(Document.deleted_at.is_(None))
        .order_by(Document.title)
        .limit(MAX_ROWS + 1)
    )
    header = [
        "document_id",
        "title",
        "audience",
        "category",
        "product_model",
        "brand",
        "department",
        "language",
        "effective_date",
        "tags",
        "published_version",
        "published_at",
        "versions",
        "created_at",
        "updated_at",
    ]
    return header, (list(row) for row in rows)


def _tickets(session: Session, period: Range) -> tuple[list[str], Iterable[list[Any]]]:
    lo, hi = period.utc_bounds
    creator, assignee = aliased(User), aliased(User)
    rows = session.execute(
        select(
            SupportTicket.number,
            SupportTicket.created_at,
            SupportTicket.subject,
            SupportTicket.status,
            SupportTicket.priority,
            SupportTicket.order_number,
            SupportTicket.summary_drafted,
            func.coalesce(creator.display_name, creator.email),
            func.coalesce(assignee.display_name, assignee.email),
            SupportTicket.resolved_at,
        )
        .outerjoin(creator, creator.id == SupportTicket.created_by)
        .outerjoin(assignee, assignee.id == SupportTicket.assigned_to)
        .where(SupportTicket.created_at >= lo, SupportTicket.created_at < hi)
        .order_by(SupportTicket.number)
        .limit(MAX_ROWS + 1)
    )
    header = [
        "reference",
        "created_at",
        "subject",
        "status",
        "priority",
        "order_number",
        "drafted_by_assistant",
        "created_by",
        "assigned_to",
        "resolved_at",
    ]
    return header, ([f"T-{row[0]:04d}", row[1], redact_personal(row[2]), *row[3:]] for row in rows)


_BUILDERS = {
    "questions": _questions,
    "feedback": _feedback,
    "reviews": _reviews,
    "documents": _documents,
    "tickets": _tickets,
}
