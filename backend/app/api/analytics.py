"""Quality analytics API (blueprint §9 `GET /admin/analytics`, §10.4, US-06): managers and admins only.

`start`/`end` are calendar dates in the tenant's timezone (inclusive); the default is the last 7 days
including today. The response states the exact range and timezone, so a dashboard can label it precisely.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import EngineDep, PrincipalDep
from app.api.errors import COMMON_ERRORS, ApiError
from app.core.permissions import Principal
from app.core.time import utcnow
from app.db.models import Organization
from app.db.session import tenant_session
from app.services import analytics

router = APIRouter(prefix="/api/v1/admin", tags=["analytics"], responses=COMMON_ERRORS)

MAX_RANGE_DAYS = 366


def require_analytics(principal: PrincipalDep) -> Principal:
    if not principal.can_manage_documents:  # permission matrix: managers and admins (product-vision.md §3)
        raise ApiError(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "Only managers and admins can see analytics.")
    return principal


class RangeOut(BaseModel):
    start: date
    end: date
    timezone: str
    label: str


class AnalyticsOut(BaseModel):
    range: RangeOut
    totals: dict[str, int]
    latency_ms: dict[str, int | None]
    daily: list[dict[str, Any]]
    intents: list[dict[str, Any]]
    most_cited: list[dict[str, Any]]
    unanswered: list[dict[str, Any]]
    missing_topics: list[dict[str, Any]]
    feedback: dict[str, Any]
    feedback_daily: list[dict[str, Any]]  # helpful / not_helpful per tenant day
    ingestion_failures: list[dict[str, Any]]


@router.get("/analytics")
def get_analytics(
    principal: Annotated[Principal, Depends(require_analytics)],
    engine: EngineDep,
    start: date | None = None,
    end: date | None = None,
) -> AnalyticsOut:
    """Answer outcomes, intents, latency, most-cited documents, unanswered questions (redacted), missing
    topics, feedback and ingestion failures for one date range. Days are the tenant's days (its timezone, not
    the caller's)."""
    with tenant_session(principal.organization_id, engine) as session:
        period = resolve_range(session, principal, start, end)
        result = analytics.compute(session, principal.organization_id, period)
    return AnalyticsOut(
        range=range_out(period),
        totals=result.totals,
        latency_ms=result.latency_ms,
        daily=result.daily,
        intents=result.intents,
        most_cited=[_jsonable(r) for r in result.most_cited],
        unanswered=[_jsonable(r) for r in result.unanswered],
        missing_topics=result.missing_topics,
        feedback={
            **result.feedback,
            "recent_not_helpful": [_jsonable(r) for r in result.feedback["recent_not_helpful"]],
        },
        feedback_daily=result.feedback_daily,
        ingestion_failures=[_jsonable(r) for r in result.ingestion_failures],
    )


def resolve_range(
    session: Session, principal: Principal, start: date | None, end: date | None
) -> analytics.Range:
    """Calendar dates in the tenant's timezone, inclusive; default: the last 7 days including today."""
    org_tz = session.get_one(Organization, principal.organization_id).timezone
    today = utcnow().astimezone(ZoneInfo(org_tz)).date()
    end = end or today
    start = start or end - timedelta(days=6)
    if start > end:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "INVALID_RANGE", "`start` must be on or before `end`."
        )
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "INVALID_RANGE",
            f"Choose a range of {MAX_RANGE_DAYS} days or fewer.",
        )
    return analytics.Range(start, end, org_tz)


def range_out(period: analytics.Range) -> RangeOut:
    return RangeOut(
        start=period.start,
        end=period.end,
        timezone=period.timezone,
        label=_label(period.start, period.end, period.timezone),
    )


EN_DASH = chr(0x2013)  # the dashboard copy uses an en dash for ranges


def _label(start: date, end: date, timezone: str) -> str:
    """ "Showing 1-30 Sep 2026 · store time (America/Chicago)" (design-system/pages/quality-dashboard.md)."""
    if (start.year, start.month) == (end.year, end.month):
        span = f"{start.day}{EN_DASH}{end.day} {end:%b %Y}"
    elif start.year == end.year:
        span = f"{start.day} {start:%b} {EN_DASH} {end.day} {end:%b %Y}"
    else:
        span = f"{start.day} {start:%b %Y} {EN_DASH} {end.day} {end:%b %Y}"
    return f"Showing {span} · store time ({timezone})"


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v)
        for k, v in row.items()
    }
