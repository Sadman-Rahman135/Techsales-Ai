"""Alert rules (blueprint §13.2 "alerts for ingestion failure spikes or abnormal 5xx rate", §16 "alerts when
budgets exceeded"; ADR-021).

The worker evaluates the rules every ALERT_CHECK_SECONDS. A rule whose condition holds opens one alert (at
most one open alert per rule, enforced by a partial unique index) and notifies; when the condition clears, the
alert is resolved and that is notified too. Notifications always go to the log at ERROR/INFO, and to
ALERT_WEBHOOK_URL when set (a Slack-compatible JSON body). The worker sees every tenant's jobs and the summed
request metrics, so it is the one place that can judge spikes. The health check catches a worker that is down.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, Float, func, select

from app.config import Settings
from app.core.time import utcnow
from app.db.models import (
    AlertEvent,
    Answer,
    IngestionJob,
    JobState,
    Organization,
    RequestMetric,
    WorkerHeartbeat,
)
from app.db.session import session_factory, tenant_session

log = logging.getLogger("app.alerts")


@dataclass(frozen=True, slots=True)
class Condition:
    rule: str
    severity: str  # warning | critical
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def check_ingestion_failures(engine: Engine, settings: Settings, now: datetime) -> Condition | None:
    since = now - timedelta(minutes=settings.alert_window_minutes)
    with session_factory(engine)() as session:
        failed = (
            session.scalar(
                select(func.count()).where(
                    IngestionJob.state == JobState.FAILED.value, IngestionJob.finished_at >= since
                )
            )
            or 0
        )
    if failed < settings.alert_ingestion_failures:
        return None
    return Condition(
        "ingestion_failures",
        "critical",
        f"{failed} documents failed to process in the last {settings.alert_window_minutes} minutes.",
        {"failed": failed, "threshold": settings.alert_ingestion_failures},
    )


def check_error_rate(engine: Engine, settings: Settings, now: datetime) -> Condition | None:
    since = now - timedelta(minutes=settings.alert_window_minutes)
    with session_factory(engine)() as session:
        total, errors = session.execute(
            select(
                func.coalesce(func.sum(RequestMetric.count), 0),
                func.coalesce(func.sum(RequestMetric.count).filter(RequestMetric.status >= 500), 0),
            ).where(RequestMetric.bucket_start >= since)
        ).one()
    if total < settings.alert_min_requests or errors / total < settings.alert_error_rate:
        return None
    return Condition(
        "error_rate",
        "critical",
        f"{errors} of {total} requests failed with a server error in the last "
        f"{settings.alert_window_minutes} minutes ({errors / total:.0%}).",
        {"errors": int(errors), "total": int(total), "threshold": settings.alert_error_rate},
    )


def check_queue(engine: Engine, settings: Settings, now: datetime) -> Condition | None:
    with session_factory(engine)() as session:
        depth, oldest = session.execute(
            select(func.max(WorkerHeartbeat.queue_depth), func.min(WorkerHeartbeat.oldest_queued_at)).where(
                WorkerHeartbeat.beat_at > now - timedelta(seconds=settings.worker_stale_seconds)
            )
        ).one()
    if oldest is None or (now - oldest).total_seconds() <= settings.queue_max_wait_seconds:
        return None
    waited = round((now - oldest).total_seconds())
    return Condition(
        "queue_stuck",
        "warning",
        f"{depth} ingestion job(s) waiting; the oldest for {waited} s.",
        {"depth": depth, "oldest_wait_seconds": waited},
    )


def model_spend(engine: Engine, since: datetime) -> float:
    """Estimated model spend (USD) of every answer since `since`, all tenants. Answers are tenant data (RLS),
    so the sum is taken tenant by tenant; evaluation runs count too, since they cost the same."""
    with session_factory(engine)() as session:
        orgs = session.scalars(select(Organization.id)).all()
    cost = Answer.token_usage["estimated_cost_usd"].astext.cast(Float)
    total = 0.0
    for org in orgs:
        with tenant_session(org, engine) as session:
            total += (
                session.scalar(select(func.coalesce(func.sum(cost), 0.0)).where(Answer.created_at >= since))
                or 0.0
            )
    return total


def check_model_budget(engine: Engine, settings: Settings, now: datetime) -> Condition | None:
    budget = settings.llm_daily_budget_usd
    if not budget:
        return None
    spent = model_spend(engine, now - timedelta(hours=24))
    if spent < budget:
        return None
    return Condition(
        "model_budget",
        "critical",
        f"Model spend in the last 24 hours is ${spent:.2f}, over the ${budget:.2f} daily budget.",
        {"spent_usd": round(spent, 4), "budget_usd": budget},
    )


RULES: dict[str, Callable[[Engine, Settings, datetime], Condition | None]] = {
    "ingestion_failures": check_ingestion_failures,
    "error_rate": check_error_rate,
    "queue_stuck": check_queue,
    "model_budget": check_model_budget,
}


Notifier = Callable[[str, AlertEvent], None]  # (event: "fired" | "resolved", alert)


def webhook_notifier(settings: Settings, client: httpx.Client | None = None) -> Notifier:
    def notify(event: str, alert: AlertEvent) -> None:
        level = logging.ERROR if event == "fired" else logging.INFO
        log.log(level, "ALERT %s %s [%s]: %s", event, alert.rule, alert.severity, alert.summary)
        if not settings.alert_webhook_url:
            return
        prefix = "Resolved" if event == "resolved" else alert.severity.upper()
        url = settings.alert_webhook_url.get_secret_value()  # the URL is the credential
        body = {"text": f"[KnowledgeAI] {prefix}: {alert.summary}"}
        try:
            if client is not None:
                client.post(url, json=body).raise_for_status()
            else:  # alerts are rare: a short-lived client, closed after the call
                with httpx.Client(timeout=5.0) as own:
                    own.post(url, json=body).raise_for_status()
        except Exception as exc:  # the alert is still recorded and logged
            log.warning("alert webhook failed (%s)", type(exc).__name__)

    return notify


@dataclass(slots=True)
class AlertSummary:
    fired: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    open: list[str] = field(default_factory=list)


def run_alerts(
    engine: Engine, settings: Settings, notify: Notifier, now: datetime | None = None
) -> AlertSummary:
    now = now or utcnow()
    summary = AlertSummary()
    for rule, check in RULES.items():
        condition = check(engine, settings, now)
        with session_factory(engine)() as session, session.begin():
            current = session.scalar(
                select(AlertEvent)
                .where(AlertEvent.rule == rule, AlertEvent.resolved_at.is_(None))
                .with_for_update()
            )
            if condition and current is None:
                alert = AlertEvent(
                    rule=rule,
                    severity=condition.severity,
                    summary=condition.summary,
                    details=condition.details,
                    fired_at=now,
                )
                session.add(alert)
                session.flush()
                notify("fired", alert)
                alert.notified = True
                summary.fired.append(rule)
            elif condition and current is not None:
                current.details, current.summary = condition.details, condition.summary  # latest numbers
                summary.open.append(rule)
            elif current is not None:
                current.resolved_at = now
                notify("resolved", current)
                summary.resolved.append(rule)
    return summary
