"""Blueprint §13: the trace shape on every answer, request metrics, the metrics API, alert rules, and the
retention of traces and metrics (ADR-021)."""

import json
import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.api.ratelimit import limiter
from app.config import Settings, get_settings
from app.core import request_metrics
from app.core.permissions import Principal
from app.core.security import issue_token
from app.db import models as m
from app.db.session import get_engine, tenant_session
from app.ingestion.embeddings import get_embedder
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag import service
from app.rag.graph import RAGContext
from app.rag.llm import FakeChatModel, get_chat_model
from app.services import alerts
from app.services.retention import apply_retention
from app.services.storage import get_storage
from tests.integration.conftest import RagEnv, sha256

pytestmark = pytest.mark.integration

FUTURE = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)  # alert tests run "then", away from every other test's rows


@pytest.fixture
def settings(rag_env: RagEnv) -> Settings:
    return get_settings().model_copy(
        update={
            "public_organization": rag_env.tenant.org.slug,
            "internal_service_organization": rag_env.tenant.org.slug,
            "rate_limit_anonymous_per_minute": 1000,
            "rate_limit_user_per_minute": 1000,
            "llm_price_per_1k_input_tokens": 0.5,
            "llm_price_per_1k_output_tokens": 1.5,
            "embedding_price_per_1k_tokens": 0.02,
        }
    )


@pytest.fixture
def client(rag_env: RagEnv, settings: Settings) -> Iterator[TestClient]:
    app.dependency_overrides[get_engine] = lambda: rag_env.ctx.engine
    app.dependency_overrides[get_knowledge_index] = lambda: rag_env.ctx.index
    app.dependency_overrides[get_embedder] = lambda: rag_env.ctx.embedder
    app.dependency_overrides[get_storage] = lambda: rag_env.ctx.storage
    app.dependency_overrides[get_chat_model] = FakeChatModel
    app.dependency_overrides[get_settings] = lambda: settings
    limiter.reset()
    yield TestClient(app, client=("127.0.0.1", 50000))
    app.dependency_overrides.clear()


def bearer(settings: Settings, principal: Principal) -> dict[str, str]:
    assert principal.user_id is not None
    return {
        "Authorization": f"Bearer {issue_token(settings, principal.user_id, principal.organization_id)[0]}"
    }


def answer_row(rag_env: RagEnv, answer_id: object) -> m.Answer:
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        return db.get_one(m.Answer, uuid.UUID(str(answer_id)))


# ---------- §13.2 trace shape ----------


def test_every_answer_records_the_trace_shape(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    guest = {"X-Session-Ref": secrets.token_urlsafe(24)}
    question = "How do I clear a paper jam in the DemoPrinter-X2?"
    answered = client.post("/api/v1/chat/ask", json={"question": question}, headers=guest).json()
    row = answer_row(rag_env, answered["answer_id"])
    assert (row.request_id, row.trace_id) == (answered["request_id"], answered["trace_id"])
    assert row.caller_role == "anonymous" and row.intent == "knowledge" and row.error_code is None
    assert row.graph_path[0] == "validate_request" and row.graph_path[-1] == "persist"
    assert row.retrieved_chunk_ids and all(i.count(":") == 2 for i in row.retrieved_chunk_ids)  # IDs only
    assert {"retrieve_documents", "generate_answer"} <= set(row.timings)
    usage = row.token_usage
    assert (
        usage["embedding"] > 0 and usage["prompt"] > 0 and usage["completion"] > 0 and usage["llm_calls"] == 1
    )
    expected = round(
        usage["embedding"] / 1000 * 0.02 + usage["prompt"] / 1000 * 0.5 + usage["completion"] / 1000 * 1.5, 6
    )
    assert usage["estimated_cost_usd"] == pytest.approx(expected)
    # No question or document text in any trace field (§13.2 "IDs, not full confidential text").
    trace = json.dumps(
        [row.graph_path, row.retrieved_chunk_ids, row.timings, row.token_usage, row.error_code]
    )
    assert "paper" not in trace.lower() and "jam" not in trace.lower()

    staff = client.post(
        "/api/v1/chat/ask",
        json={"question": "Do you sell electric bikes?"},
        headers=bearer(settings, rag_env.tenant.employee),
    ).json()
    refused = answer_row(rag_env, staff["answer_id"])
    assert refused.caller_role == "employee"
    assert refused.error_code in {"NO_RESULTS", "WEAK_EVIDENCE"}

    token = settings.internal_service_token.get_secret_value()
    voice = client.post(
        "/api/v1/internal/knowledge/answer",
        json={
            "question": "What are the store hours on Sunday?",
            "conversation_id": f"call-{uuid.uuid4().hex}",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert voice.status_code == 200
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        role = db.scalar(
            select(m.Answer.caller_role).where(m.Answer.request_id == voice.json()["request_id"])
        )
    assert role == "service"


def test_provider_failures_get_a_sanitized_code(rag_env: RagEnv) -> None:
    from tests.integration.test_rag_graph import BrokenModel

    ctx = RAGContext(
        principal=Principal(rag_env.tenant.org.id),
        engine=rag_env.ctx.engine,
        index=rag_env.ctx.index,
        embedder=rag_env.ctx.embedder,
        llm=BrokenModel(),
        settings=rag_env.ctx.settings,
        request_id=f"req-{uuid.uuid4().hex}",
        trace_id=f"trace-{uuid.uuid4().hex}",
        session_ref=sha256(),
    )
    result = service.ask(ctx, "How do I clear a paper jam in the DemoPrinter-X2?")
    row = answer_row(rag_env, result.answer_id)
    assert (row.answer_status, row.error_code) == ("error", "PROVIDER_FAILED")  # the message stays out


# ---------- §13.1 request metrics ----------


def test_requests_are_counted_by_route_template(client: TestClient, rag_env: RagEnv) -> None:
    request_metrics.counter.drain()
    missing = uuid.uuid4()
    client.get(f"/api/v1/documents/{missing}")  # 401, no credentials
    client.get(
        f"/api/v1/conversations/{missing}", headers={"X-Session-Ref": secrets.token_urlsafe(24)}
    )  # 404
    counts = request_metrics.counter.drain()
    routes = {(k[1], k[2], k[3]) for k in counts}
    assert ("GET", "/api/v1/documents/{document_id}", 401) in routes
    assert ("GET", "/api/v1/conversations/{conversation_id}", 404) in routes
    assert not any(str(missing) in k[2] for k in counts)  # templates, never IDs


def test_flushes_from_several_processes_add_up(app_engine: Engine) -> None:
    minute = FUTURE.replace(minute=7)
    a, b = request_metrics.RequestCounter(), request_metrics.RequestCounter()
    for counter_, n in ((a, 3), (b, 2)):
        for _ in range(n):
            counter_.record("GET", "/api/v1/test/{id}", 500, 10, now=minute)
    try:
        request_metrics.flush(app_engine, a)
        request_metrics.flush(app_engine, b)
        with Session(app_engine) as db:
            row = db.get_one(m.RequestMetric, (minute, "GET", "/api/v1/test/{id}", 500))
            assert (row.count, row.total_ms) == (5, 50)
    finally:
        with Session(app_engine) as db, db.begin():
            db.execute(delete(m.RequestMetric).where(m.RequestMetric.route == "/api/v1/test/{id}"))


# ---------- §13.1 metrics API ----------


def test_metrics_api_reports_tenant_and_service_numbers(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    guest = {"X-Session-Ref": secrets.token_urlsafe(24)}
    client.post("/api/v1/chat/ask", json={"question": "What does error code E-04 mean?"}, headers=guest)
    client.post("/api/v1/chat/ask", json={"question": "Can I pay with cryptocurrency?"}, headers=guest)
    request_metrics.flush(rag_env.ctx.engine)

    assert (
        client.get("/api/v1/admin/metrics", headers=bearer(settings, rag_env.tenant.employee)).status_code
        == 403
    )
    body = client.get("/api/v1/admin/metrics", headers=bearer(settings, rag_env.tenant.manager)).json()
    tenant, service_ = body["tenant"], body["service"]
    assert tenant["questions"] >= 2 and tenant["active"]["guest_sessions"] >= 1
    assert tenant["latency_ms"]["p50"] is not None and "retrieve_documents" in tenant["latency_ms"]["by_node"]
    assert tenant["tokens"]["prompt"] > 0 and tenant["tokens"]["estimated_cost_per_question_usd"] > 0
    assert tenant["refusals"]["count"] >= 1 and 0 < tenant["refusals"]["rate"] < 1
    assert set(tenant["ingestion"]) >= {"jobs", "parse_failures", "retries", "indexing_lag_ms"}
    assert service_["requests"]["total"] >= 2 and "server_errors" in service_["requests"]
    assert body["range"]["label"].startswith("Showing")


def test_evaluation_traffic_stays_out_of_analytics(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    manager = bearer(settings, rag_env.tenant.manager)
    before = client.get("/api/v1/admin/analytics", headers=manager).json()["totals"]["questions"]
    ctx = RAGContext(
        principal=Principal(rag_env.tenant.org.id),
        engine=rag_env.ctx.engine,
        index=rag_env.ctx.index,
        embedder=rag_env.ctx.embedder,
        llm=FakeChatModel(),
        settings=rag_env.ctx.settings,
        request_id=f"req-{uuid.uuid4().hex}",
        trace_id=f"trace-{uuid.uuid4().hex}",
        session_ref=sha256(),
        caller_role="evaluation",
    )
    service.ask(ctx, "What does error code E-04 mean?")
    after = client.get("/api/v1/admin/analytics", headers=manager).json()["totals"]["questions"]
    assert after == before


# ---------- §13.2 alerts ----------


def _failed_jobs(engine: Engine, org: uuid.UUID, n: int, when: datetime) -> list[uuid.UUID]:
    ids = []
    with Session(engine) as db, db.begin():
        from app.db.session import set_tenant

        set_tenant(db, org)
        document = m.Document(
            organization_id=org, title=f"Alert fixture {uuid.uuid4().hex[:6]}", audience="public"
        )
        db.add(document)
        db.flush()
        for i in range(n):
            version = m.DocumentVersion(
                organization_id=org,
                document_id=document.id,
                version_number=i + 1,
                checksum=sha256(),
                storage_key=f"{org}/{document.id}/{i}.pdf",
                original_filename="scan.pdf",
                mime_type="application/pdf",
                size_bytes=10,
                status="failed",
            )
            db.add(version)
            db.flush()
            job = m.IngestionJob(
                organization_id=org,
                version_id=version.id,
                state="failed",
                attempts=1,
                error_code="FILE_NO_TEXT",
                failed_stage="parsing",
                finished_at=when,
            )
            db.add(job)
            db.flush()
            ids.append(job.id)
    return ids


@pytest.fixture
def clean_alerts(app_engine: Engine) -> Iterator[None]:
    def wipe() -> None:
        with Session(app_engine) as db, db.begin():
            db.execute(delete(m.AlertEvent))
            db.execute(
                delete(m.RequestMetric).where(m.RequestMetric.bucket_start >= FUTURE - timedelta(days=1))
            )

    wipe()
    yield
    wipe()


def test_an_ingestion_failure_spike_fires_once_notifies_and_resolves(
    rag_env: RagEnv, app_engine: Engine, clean_alerts: None
) -> None:
    sent: list[dict[str, Any]] = []

    def hook(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    settings = get_settings().model_copy(
        update={"alert_ingestion_failures": 3, "alert_webhook_url": SecretStr("https://hooks.example/alerts")}
    )
    notify = alerts.webhook_notifier(settings, httpx.Client(transport=httpx.MockTransport(hook)))
    _failed_jobs(app_engine, rag_env.tenant.org.id, 3, FUTURE - timedelta(minutes=2))

    first = alerts.run_alerts(app_engine, settings, notify, now=FUTURE)
    second = alerts.run_alerts(app_engine, settings, notify, now=FUTURE + timedelta(minutes=1))
    assert (
        first.fired == ["ingestion_failures"] and second.fired == [] and "ingestion_failures" in second.open
    )
    assert sent == [{"text": "[KnowledgeAI] CRITICAL: 3 documents failed to process in the last 15 minutes."}]

    later = alerts.run_alerts(
        app_engine, settings, notify, now=FUTURE + timedelta(hours=1)
    )  # outside the window
    assert later.resolved == ["ingestion_failures"]
    assert sent[-1]["text"].startswith("[KnowledgeAI] Resolved:")
    with Session(app_engine) as db:
        events = db.scalars(select(m.AlertEvent).where(m.AlertEvent.rule == "ingestion_failures")).all()
    assert len(events) == 1 and events[0].resolved_at is not None and events[0].notified


def test_an_abnormal_server_error_rate_fires(app_engine: Engine, clean_alerts: None) -> None:
    settings = get_settings().model_copy(update={"alert_error_rate": 0.05, "alert_min_requests": 20})
    counter = request_metrics.RequestCounter()
    for i in range(40):
        counter.record(
            "POST", "/api/v1/chat/ask", 500 if i < 4 else 200, 5, now=FUTURE - timedelta(minutes=3)
        )
    request_metrics.flush(app_engine, counter)
    fired = alerts.run_alerts(app_engine, settings, lambda event, alert: None, now=FUTURE)
    assert "error_rate" in fired.fired
    with Session(app_engine) as db:
        alert = db.scalars(select(m.AlertEvent).where(m.AlertEvent.rule == "error_rate")).one()
    assert alert.details == {"errors": 4, "total": 40, "threshold": 0.05}

    quiet = request_metrics.RequestCounter()  # a low-traffic window never alerts on a couple of failures
    quiet.record("GET", "/api/v1/health", 503, 5, now=FUTURE + timedelta(hours=2))
    request_metrics.flush(app_engine, quiet)
    later = alerts.run_alerts(
        app_engine, settings, lambda event, alert: None, now=FUTURE + timedelta(hours=2)
    )
    assert later.resolved == ["error_rate"] and later.fired == []


def test_metrics_and_resolved_alerts_expire(rag_env: RagEnv, app_engine: Engine, clean_alerts: None) -> None:
    old = datetime.now(UTC) - timedelta(days=200)
    counter = request_metrics.RequestCounter()
    counter.record("GET", "/api/v1/expire-me", 200, 1, now=old)
    request_metrics.flush(app_engine, counter)
    with Session(app_engine) as db, db.begin():
        db.add(m.AlertEvent(rule="old_rule", severity="warning", summary="x", fired_at=old, resolved_at=old))
        db.add(m.AlertEvent(rule="open_rule", severity="warning", summary="x", fired_at=old))
    summary = apply_retention(app_engine, rag_env.ctx.index, rag_env.ctx.storage, rag_env.ctx.settings)
    assert summary.metric_rows_deleted >= 2
    with Session(app_engine) as db:
        assert db.scalar(select(m.RequestMetric).where(m.RequestMetric.route == "/api/v1/expire-me")) is None
        rules = set(db.scalars(select(m.AlertEvent.rule)))
    assert "old_rule" not in rules and "open_rule" in rules  # an open alert is never expired


def test_model_spend_over_the_daily_budget_fires_and_resolves(
    client: TestClient, app_engine: Engine, settings: Settings, clean_alerts: None
) -> None:
    """§16 "cost budgets; alerts when budgets exceeded": priced answers count, across tenants."""
    now = datetime.now(UTC)
    before = alerts.model_spend(app_engine, now - timedelta(hours=24))
    guest = {"X-Session-Ref": secrets.token_urlsafe(24)}
    for question in (
        "How do I clear a paper jam in the DemoPrinter-X2?",
        "What are the store hours on Sunday?",
    ):
        assert client.post("/api/v1/chat/ask", json={"question": question}, headers=guest).status_code == 200
    spent = alerts.model_spend(app_engine, now - timedelta(hours=24))
    assert spent > before  # the two priced answers were counted

    over = settings.model_copy(update={"llm_daily_budget_usd": spent})
    fired = alerts.run_alerts(app_engine, over, lambda event, alert: None, now=datetime.now(UTC))
    assert "model_budget" in fired.fired
    with Session(app_engine) as db:
        alert = db.scalars(select(m.AlertEvent).where(m.AlertEvent.rule == "model_budget")).one()
    assert alert.severity == "critical" and alert.details["budget_usd"] == spent

    raised = settings.model_copy(update={"llm_daily_budget_usd": spent * 100})
    later = alerts.run_alerts(app_engine, raised, lambda event, alert: None, now=datetime.now(UTC))
    assert later.resolved == ["model_budget"]
    unset = alerts.check_model_budget(app_engine, settings, datetime.now(UTC))
    assert unset is None  # no budget configured: no rule
