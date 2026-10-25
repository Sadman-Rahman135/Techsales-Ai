"""CSV exports for managers and admins (plan §2.2, ADR-030). Each download is audited with its dataset, range
and row count, never its contents."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.analytics import require_analytics, resolve_range
from app.api.deps import EngineDep
from app.api.errors import COMMON_ERRORS, ApiError, request_id
from app.core.permissions import Principal
from app.db.session import tenant_session
from app.services import audit, exports

router = APIRouter(prefix="/api/v1/admin", tags=["exports"], responses=COMMON_ERRORS)

Dataset = Literal["questions", "feedback", "reviews", "documents", "tickets"]


@router.get(
    "/exports/{dataset}",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "The dataset as CSV (UTF-8)"}},
)
def export_dataset(
    dataset: Dataset,
    request: Request,
    principal: Annotated[Principal, Depends(require_analytics)],
    engine: EngineDep,
    start: date | None = None,
    end: date | None = None,
) -> Response:
    """One dataset for a date range in the tenant's days (default: the last 7 days). `documents` is every live
    document, whatever the range. Question and note text are redacted; conversations are never exported."""
    with tenant_session(principal.organization_id, engine) as session:
        period = resolve_range(session, principal, start, end)
        try:
            header, rows = exports.table(session, dataset, period)
        except exports.TooLarge:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "EXPORT_TOO_LARGE",
                f"That's more than {exports.MAX_ROWS:,} rows. Choose a shorter date range.",
            ) from None
        audit.record(
            session,
            principal,
            "export.downloaded",
            "export",
            details={
                "dataset": dataset,
                "start": period.start.isoformat(),
                "end": period.end.isoformat(),
                "rows": len(rows),
            },
            request_id=request_id(request),
        )
    suffix = "" if dataset == "documents" else f"-{period.start.isoformat()}-to-{period.end.isoformat()}"
    return Response(
        content=exports.to_csv(header, rows, period.timezone),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{dataset}{suffix}.csv"',
            "Cache-Control": "no-store",
        },
    )
