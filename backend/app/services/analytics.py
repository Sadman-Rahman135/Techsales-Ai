"""Quality analytics for one tenant and date range (blueprint §9 `GET /admin/analytics`, §10.4, §13.1, US-06).

Everything is computed from Postgres: `answers` (status, intent, latency), `answer_citations`,
`answer_feedback` and `ingestion_jobs`. Days are bucketed in the tenant's timezone, so "Monday" means the
store's Monday. Question text and feedback notes are redacted (app/core/privacy.py) before they leave this
module; full conversations are never returned here (they need `conversations:read`, not granted to any role in
the MVP).
"""

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.privacy import redact_personal
from app.db.models import (
    Answer,
    AnswerCitation,
    AnswerFeedback,
    AnswerStatus,
    Document,
    DocumentVersion,
    IngestionJob,
    JobState,
    Message,
    MessageRole,
    Rating,
)
from app.ingestion.errors import message_for
from app.rag.text import content_terms

UNANSWERED_LIMIT = 50
FEEDBACK_LIMIT = 20
TOP_LIMIT = 10


@dataclass(frozen=True, slots=True)
class Range:
    start: date  # inclusive, tenant-local
    end: date  # inclusive, tenant-local
    timezone: str

    @property
    def utc_bounds(self) -> tuple[datetime, datetime]:
        zone = ZoneInfo(self.timezone)
        return (
            datetime.combine(self.start, time.min, zone),
            datetime.combine(self.end + timedelta(days=1), time.min, zone),
        )

    @property
    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


@dataclass(slots=True)
class Analytics:
    range: Range
    totals: dict[str, int]
    latency_ms: dict[str, int | None]
    daily: list[dict[str, Any]]
    intents: list[dict[str, Any]]
    most_cited: list[dict[str, Any]]
    unanswered: list[dict[str, Any]]
    missing_topics: list[dict[str, Any]]
    feedback: dict[str, Any]
    ingestion_failures: list[dict[str, Any]] = field(default_factory=list)
    feedback_daily: list[dict[str, Any]] = field(default_factory=list)


def compute(session: Session, organization_id: uuid.UUID, period: Range) -> Analytics:
    lo, hi = period.utc_bounds
    # Evaluation runs are not customer traffic: they stay out of every number here.
    in_range = (
        Answer.created_at >= lo,
        Answer.created_at < hi,
        or_(Answer.caller_role.is_(None), Answer.caller_role != "evaluation"),
    )

    statuses: dict[str, int] = {
        status: count
        for status, count in session.execute(
            select(Answer.answer_status, func.count()).where(*in_range).group_by(Answer.answer_status)
        )
    }
    totals = {s.value: int(statuses.get(s.value, 0)) for s in AnswerStatus}
    totals["questions"] = sum(totals.values())

    p50, p95 = session.execute(
        select(
            func.percentile_cont(0.5).within_group(Answer.latency_ms),
            func.percentile_cont(0.95).within_group(Answer.latency_ms),
        ).where(*in_range, Answer.answer_status != AnswerStatus.DIRECT.value)
    ).one()

    local_day = cast(func.timezone(period.timezone, Answer.created_at), Date)
    per_day: dict[date, Counter[str]] = {d: Counter() for d in period.days}
    for day, status, count in session.execute(
        select(local_day, Answer.answer_status, func.count())
        .where(*in_range)
        .group_by(local_day, Answer.answer_status)
    ):
        per_day.setdefault(day, Counter())[status] = count
    daily = [
        {"date": d.isoformat(), **{s.value: per_day[d][s.value] for s in AnswerStatus}} for d in period.days
    ]

    intents = [
        {"intent": intent or "unknown", "count": count}
        for intent, count in session.execute(
            select(Answer.intent, func.count())
            .where(*in_range)
            .group_by(Answer.intent)
            .order_by(func.count().desc())
        )
    ]

    most_cited = [
        {"document_id": doc_id, "title": title, "citations": count}
        for doc_id, title, count in session.execute(
            select(Document.id, Document.title, func.count())
            .select_from(AnswerCitation)
            .join(Answer, Answer.id == AnswerCitation.answer_id)
            .join(DocumentVersion, DocumentVersion.id == AnswerCitation.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(*in_range, Document.deleted_at.is_(None))
            .group_by(Document.id, Document.title)
            .order_by(func.count().desc(), Document.title)
            .limit(TOP_LIMIT)
        )
    ]

    unanswered_rows = session.execute(
        questions_for(select(Answer.id, Answer.created_at, Answer.intent))
        .where(*in_range, Answer.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE.value)
        .order_by(Answer.created_at.desc())
        .limit(500)  # topics use up to 500; the list shows the newest 50
    ).all()
    unanswered = [
        {
            "answer_id": answer_id,
            "asked_at": asked_at,
            "intent": intent,
            "question": redact_personal(question or ""),
            "conversation": "restricted",  # needs conversations:read (not granted in the MVP)
        }
        for answer_id, asked_at, intent, question in unanswered_rows[:UNANSWERED_LIMIT]
    ]
    topics: Counter[str] = Counter()
    for *_, question in unanswered_rows:
        topics.update(content_terms(redact_personal(question or "")))
    missing_topics = [{"term": term, "count": count} for term, count in topics.most_common(TOP_LIMIT)]

    feedback_range = (AnswerFeedback.created_at >= lo, AnswerFeedback.created_at < hi)
    ratings: dict[str, int] = {
        rating: count
        for rating, count in session.execute(
            select(AnswerFeedback.rating, func.count()).where(*feedback_range).group_by(AnswerFeedback.rating)
        )
    }
    feedback_day = cast(func.timezone(period.timezone, AnswerFeedback.created_at), Date)
    feedback_per_day: dict[date, Counter[str]] = {d: Counter() for d in period.days}
    for day, rating, count in session.execute(
        select(feedback_day, AnswerFeedback.rating, func.count())
        .where(*feedback_range)
        .group_by(feedback_day, AnswerFeedback.rating)
    ):
        feedback_per_day.setdefault(day, Counter())[rating] = count
    feedback_daily = [
        {"date": d.isoformat(), **{r.value: feedback_per_day[d][r.value] for r in Rating}}
        for d in period.days
    ]
    recent = session.execute(
        questions_for(
            select(
                AnswerFeedback.answer_id,
                AnswerFeedback.created_at,
                AnswerFeedback.reasons,
                AnswerFeedback.note,
            ).join(Answer, Answer.id == AnswerFeedback.answer_id)
        )
        .where(*feedback_range, AnswerFeedback.rating == Rating.NOT_HELPFUL.value)
        .order_by(AnswerFeedback.created_at.desc())
        .limit(FEEDBACK_LIMIT)
    ).all()
    feedback = {
        Rating.HELPFUL.value: int(ratings.get(Rating.HELPFUL.value, 0)),
        Rating.NOT_HELPFUL.value: int(ratings.get(Rating.NOT_HELPFUL.value, 0)),
        "recent_not_helpful": [
            {
                "answer_id": answer_id,
                "created_at": created_at,
                "reasons": list(reasons or []),
                "note": redact_personal(note) if note else None,
                "question": redact_personal(question or ""),
            }
            for answer_id, created_at, reasons, note, question in recent
        ],
    }

    failures = [
        {
            "job_id": job.id,
            "document_id": document_id,
            "title": title,
            "version_number": version_number,
            "error_code": job.error_code,
            "error_message": message_for(job.error_code),
            "failed_stage": job.failed_stage,
            "finished_at": job.finished_at,
        }
        for job, document_id, title, version_number in session.execute(
            select(IngestionJob, Document.id, Document.title, DocumentVersion.version_number)
            .join(DocumentVersion, DocumentVersion.id == IngestionJob.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                IngestionJob.organization_id
                == organization_id,  # ingestion_jobs has no RLS: filter explicitly
                IngestionJob.state == JobState.FAILED.value,
                IngestionJob.finished_at >= lo,
                IngestionJob.finished_at < hi,
                Document.deleted_at.is_(None),
            )
            .order_by(IngestionJob.finished_at.desc())
            .limit(UNANSWERED_LIMIT)
        )
    ]

    return Analytics(
        range=period,
        totals=totals,
        latency_ms={"p50": _ms(p50), "p95": _ms(p95)},
        daily=daily,
        intents=intents,
        most_cited=most_cited,
        unanswered=unanswered,
        missing_topics=missing_topics,
        feedback=feedback,
        ingestion_failures=failures,
        feedback_daily=feedback_daily,
    )


def questions_for(query: Any) -> Any:
    """Add the user's question for an answer: the user message just before the answer's assistant message."""
    assistant = aliased(Message)
    user = aliased(Message)
    question = (
        select(user.content)
        .where(
            user.conversation_id == assistant.conversation_id,
            user.role == MessageRole.USER.value,
            user.id < assistant.id,  # UUIDv7: inserted first in the same transaction
        )
        .order_by(user.id.desc())
        .limit(1)
        .correlate(assistant)
        .scalar_subquery()
    )
    return query.add_columns(question).join(assistant, assistant.id == Answer.message_id)


def _ms(value: float | None) -> int | None:
    return None if value is None else round(value)
