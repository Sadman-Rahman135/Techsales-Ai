"""Operational metrics API (blueprint §13.1, ADR-021): `GET /api/v1/admin/metrics`, managers and admins.

Same date-range rules as `/admin/analytics` (tenant's calendar days, inclusive). Analytics answers "how good
are the answers"; this answers "how is the system doing": latency per step, tokens and cost, refusals, errors,
ingestion lag, request errors, the queue and alerts.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.analytics import RangeOut, range_out, require_analytics, resolve_range
from app.api.deps import EngineDep
from app.api.errors import COMMON_ERRORS
from app.core.permissions import Principal
from app.db.session import tenant_session
from app.services import metrics

router = APIRouter(prefix="/api/v1/admin", tags=["metrics"], responses=COMMON_ERRORS)


class MetricsOut(BaseModel):
    range: RangeOut
    tenant: dict[str, Any]
    service: dict[str, Any]


@router.get("/metrics")
def get_metrics(
    principal: Annotated[Principal, Depends(require_analytics)],
    engine: EngineDep,
    start: date | None = None,
    end: date | None = None,
) -> MetricsOut:
    with tenant_session(principal.organization_id, engine) as session:
        period = resolve_range(session, principal, start, end)
        tenant = metrics.tenant_metrics(session, principal.organization_id, period)
    return MetricsOut(range=range_out(period), tenant=tenant, service=metrics.service_metrics(engine, period))
