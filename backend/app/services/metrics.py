"""Operational metrics for one date range (blueprint §13.1; ADR-021). Everything is computed from what is
already persisted: answer traces (§13.2), ingestion jobs, feedback, request metrics, heartbeats, alerts.

Tenant numbers (questions, latency, tokens, refusals, ingestion) come from the caller's tenant session.
Service numbers (requests, errors, queue, alerts) describe the whole deployment; in the single-tenant MVP they
are the tenant's too (a platform-operator role for multi-tenant deployments is P1).
"""

import statistics
import uuid
from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.db.models import (
    AlertEvent,
    Answer,
    AnswerFeedback,
    AnswerStatus,
    Conversation,
    IngestionJob,
    Intent,
    JobState,
    Message,
    Rating,
    RequestMetric,
    WorkerHeartbeat,
)
from app.db.session import session_factory
from app.services.analytics import Range

TRACE_LIMIT = 20_000  # answers read for percentiles in one range; beyond that the newest are used


def _pct(values: list[float], q: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0])
    return round(statistics.quantiles(values, n=100, method="inclusive")[round(q * 100) - 1])


def _rate(part: int, whole: int) -> dict[str, Any]:
    return {"count": part, "of": whole, "rate": round(part / whole, 4) if whole else None}


def tenant_metrics(session: Session, organization_id: uuid.UUID, period: Range) -> dict[str, Any]:
    lo, hi = period.utc_bounds
    in_range = (
        Answer.created_at >= lo,
        Answer.created_at < hi,
        or_(Answer.caller_role.is_(None), Answer.caller_role != "evaluation"),
    )
    traces = session.execute(
        select(
            Answer.answer_status,
            Answer.intent,
            Answer.latency_ms,
            Answer.timings,
            Answer.token_usage,
            Answer.error_code,
            Answer.caller_role,
            func.cardinality(Answer.retrieved_chunk_ids).label("retrieved"),
            Answer.graph_path,
        )
        .where(*in_range)
        .order_by(Answer.created_at.desc())
        .limit(TRACE_LIMIT)
    ).all()

    knowledge = [t for t in traces if t.intent == Intent.KNOWLEDGE.value]
    zero = [t for t in knowledge if not t.retrieved]
    refused = [t for t in knowledge if t.answer_status == AnswerStatus.INSUFFICIENT_EVIDENCE.value]
    latencies = [
        float(t.latency_ms) for t in traces if t.latency_ms is not None and t.answer_status != "direct"
    ]
    nodes: dict[str, list[float]] = {}
    for t in traces:
        for node, ms in (t.timings or {}).items():
            nodes.setdefault(node, []).append(float(ms))
    tokens = Counter[str]()
    cost = 0.0
    for t in traces:
        usage = t.token_usage or {}
        for key in ("embedding", "prompt", "completion", "llm_calls"):
            tokens[key] += int(usage.get(key, 0))
        cost += float(usage.get("estimated_cost_usd", 0.0))

    active = session.execute(
        select(
            func.count(func.distinct(Conversation.owner_user_id)),
            func.count(func.distinct(Conversation.session_ref)),
        )
        .join(Message, Message.conversation_id == Conversation.id)
        .where(Message.created_at >= lo, Message.created_at < hi)
    ).one()
    not_helpful = session.scalar(
        select(func.count()).where(
            AnswerFeedback.created_at >= lo,
            AnswerFeedback.created_at < hi,
            AnswerFeedback.rating == Rating.NOT_HELPFUL.value,
        )
    )
    return {
        "questions": len(traces),
        "active": {"signed_in_users": active[0], "guest_sessions": active[1]},
        "by_caller": dict(Counter(t.caller_role or "unrecorded" for t in traces)),
        "latency_ms": {
            "p50": _pct(latencies, 0.5),
            "p95": _pct(latencies, 0.95),
            "by_node": {
                node: {"p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "n": len(v)}
                for node, v in sorted(nodes.items())
            },
        },
        "tokens": {
            **tokens,
            "estimated_cost_usd": round(cost, 6),
            "estimated_cost_per_question_usd": round(cost / len(traces), 6) if traces else None,
        },
        "retrieval_zero_results": _rate(len(zero), len(knowledge)),
        "refusals": _rate(len(refused), len(knowledge)),
        "errors": _rate(sum(1 for t in traces if t.answer_status == AnswerStatus.ERROR.value), len(traces)),
        "error_codes": dict(Counter(t.error_code for t in traces if t.error_code).most_common()),
        "citation_failures": sum(1 for t in traces if t.error_code == "CITATIONS_INVALID"),
        "correction_reports": not_helpful or 0,
        "tool_calls": {
            "count": sum(1 for t in traces if "business_tool" in (t.graph_path or [])),
            "failures": 0,  # the P0 tool is a fixed handoff; verified lookups (P1) will record failures
        },
        "ingestion": _ingestion(session, organization_id, period),
    }


def _ingestion(session: Session, organization_id: uuid.UUID, period: Range) -> dict[str, Any]:
    lo, hi = period.utc_bounds
    jobs = session.execute(
        select(
            IngestionJob.state,
            IngestionJob.attempts,
            IngestionJob.error_code,
            IngestionJob.failed_stage,
            IngestionJob.created_at,
            IngestionJob.finished_at,
        ).where(
            IngestionJob.organization_id == organization_id,  # no RLS on ingestion_jobs: filter explicitly
            IngestionJob.created_at >= lo,
            IngestionJob.created_at < hi,
        )
    ).all()
    lags = [
        (j.finished_at - j.created_at).total_seconds() * 1000
        for j in jobs
        if j.state == JobState.DONE.value and j.finished_at
    ]
    return {
        "jobs": len(jobs),
        "by_state": dict(Counter(j.state for j in jobs)),
        "parse_failures": sum(
            1 for j in jobs if j.state == JobState.FAILED.value and j.failed_stage == "parsing"
        ),
        # Extra attempts after a transient failure (a later success clears the failure's stage and code).
        "retries": sum(max(0, j.attempts - 1) for j in jobs),
        "embedding_failures": sum(1 for j in jobs if j.error_code and j.error_code.startswith("EMBEDDING")),
        "indexing_lag_ms": {"p50": _pct(lags, 0.5), "p95": _pct(lags, 0.95), "n": len(lags)},
    }


def service_metrics(engine: Engine, period: Range) -> dict[str, Any]:
    lo, hi = period.utc_bounds
    with session_factory(engine)() as session:
        rows = session.execute(
            select(
                RequestMetric.route, RequestMetric.method, RequestMetric.status, func.sum(RequestMetric.count)
            )
            .where(RequestMetric.bucket_start >= lo, RequestMetric.bucket_start < hi)
            .group_by(RequestMetric.route, RequestMetric.method, RequestMetric.status)
        ).all()
        heartbeat = session.execute(
            select(func.max(WorkerHeartbeat.queue_depth), func.min(WorkerHeartbeat.oldest_queued_at)).where(
                WorkerHeartbeat.beat_at > utcnow() - timedelta(minutes=2)
            )
        ).one()
        open_alerts = session.scalars(
            select(AlertEvent).where(AlertEvent.resolved_at.is_(None)).order_by(AlertEvent.fired_at.desc())
        ).all()
        recent_alerts = session.scalars(
            select(AlertEvent)
            .where(AlertEvent.fired_at >= lo, AlertEvent.fired_at < hi)
            .order_by(AlertEvent.fired_at.desc())
            .limit(20)
        ).all()
    total = sum(int(r[3]) for r in rows)
    by_status = Counter[int]()
    failing_routes = Counter[str]()
    for route, method, status, n in rows:
        by_status[status] += int(n)
        if status >= 500:
            failing_routes[f"{method} {route}"] += int(n)
    server_errors = sum(n for s, n in by_status.items() if s >= 500)
    oldest = heartbeat[1]
    return {
        "requests": {
            "total": total,
            "server_errors": _rate(server_errors, total),
            "rate_limited": by_status.get(429, 0),
            "permission_denied": by_status.get(403, 0),
            "unauthenticated": by_status.get(401, 0),
            "by_status": {str(k): v for k, v in sorted(by_status.items())},
            "top_failing_routes": dict(failing_routes.most_common(5)),
        },
        "queue": {
            "depth": heartbeat[0],
            "oldest_wait_seconds": round((utcnow() - oldest).total_seconds()) if oldest else None,
        },
        "alerts": {
            "open": [_alert(a) for a in open_alerts],
            "recent": [_alert(a) for a in recent_alerts],
        },
    }


def _alert(alert: AlertEvent) -> dict[str, Any]:
    return {
        "id": str(alert.id),
        "rule": alert.rule,
        "severity": alert.severity,
        "summary": alert.summary,
        "fired_at": alert.fired_at.isoformat(),
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
    }
