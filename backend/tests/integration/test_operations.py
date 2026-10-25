"""Blueprint §11 against the real stack: audit coverage, sign-in lockout, query caps, the internal network
rule, retention and deletion in every store, health of the worker and queue, graceful shutdown, timeouts."""

import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api import health
from app.api.ratelimit import limiter, login_failures
from app.config import Settings, get_settings
from app.core.log_hygiene import scrub
from app.core.security import hash_password
from app.core.time import utcnow
from app.db import models as m
from app.db.session import build_engine, get_engine, tenant_session
from app.ingestion import heartbeat
from app.ingestion import worker as w
from app.ingestion.embeddings import FakeEmbedder, get_embedder
from app.ingestion.scanning import BuiltinScanner, get_scanner
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag.llm import FakeChatModel, get_chat_model
from app.services.retention import apply_retention
from app.services.storage import get_storage
from app.services.versions import soft_delete_document
from tests.builders import pdf_bytes, warranty_pdf
from tests.integration.conftest import TEST_DB, Tenant, make_tenant

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(scope="module")
def hashed() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def api_tenant(app_engine: Engine, hashed: str) -> Tenant:
    return make_tenant(app_engine, password_hash=hashed)


@pytest.fixture
def test_settings(worker: w.WorkerContext) -> Settings:
    """Settings pointing at the test database (for code that connects itself, like the health checks)."""
    url = str(worker.engine.url.render_as_string(hide_password=False))
    return worker.settings.model_copy(update={"database_url": url})


@pytest.fixture
def client(worker: w.WorkerContext) -> Iterator[TestClient]:
    app.dependency_overrides[get_engine] = lambda: worker.engine
    app.dependency_overrides[get_knowledge_index] = lambda: worker.index
    app.dependency_overrides[get_storage] = lambda: worker.storage
    app.dependency_overrides[get_scanner] = BuiltinScanner
    app.dependency_overrides[get_embedder] = lambda: worker.embedder
    app.dependency_overrides[get_chat_model] = FakeChatModel
    app.dependency_overrides[get_settings] = lambda: worker.settings
    limiter.reset()
    login_failures.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def upload(
    client: TestClient, headers: dict[str, str], data: bytes, url: str = "/api/v1/documents"
) -> dict[str, Any]:
    files = {"file": ("doc.pdf", data, "application/pdf")}
    response = client.post(url, headers=headers, files=files, data={"audience": "public"})
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


# ---------- §11.1 admin changes produce audit events ----------


def test_every_admin_change_is_audited(
    client: TestClient, api_tenant: Tenant, worker: w.WorkerContext
) -> None:
    manager = login(client, api_tenant.emails[m.Role.MANAGER])
    admin = login(client, api_tenant.emails[m.Role.ADMIN])
    first = upload(client, manager, warranty_pdf())
    assert w.run_once(worker) is m.JobState.DONE
    document_id = first["document_id"]
    publish = client.post(
        f"/api/v1/documents/{document_id}/publish", json={"version_id": first["version_id"]}, headers=manager
    )
    assert publish.status_code == 200
    second = upload(
        client,
        manager,
        pdf_bytes([[("h1", "Warranty"), ("p", f"v2 {uuid.uuid4()}")]]),
        f"/api/v1/documents/{document_id}/versions",
    )
    job_id = second["job"]["id"]
    assert client.post(f"/api/v1/ingestion-jobs/{job_id}/cancel", headers=manager).status_code == 200
    assert client.post(f"/api/v1/ingestion-jobs/{job_id}/retry", headers=manager).status_code == 202
    assert client.delete(f"/api/v1/documents/{document_id}", headers=admin).status_code == 204

    with tenant_session(api_tenant.org.id, worker.engine) as db:
        events = db.execute(
            select(m.AuditEvent.action, m.AuditEvent.actor_user_id, m.AuditEvent.request_id)
            .where(m.AuditEvent.organization_id == api_tenant.org.id)
            .order_by(m.AuditEvent.created_at, m.AuditEvent.id)
        ).all()
    assert [e.action for e in events] == [
        "auth.login",
        "auth.login",
        "document.version.uploaded",
        "document.version.published",
        "document.version.uploaded",
        "ingestion.cancelled",
        "ingestion.retried",
        "document.deleted",
    ]
    assert all(e.actor_user_id is not None and e.request_id for e in events)  # who, and which request
    with (
        tenant_session(api_tenant.org.id, worker.engine) as db,
        pytest.raises(DBAPIError, match="append-only"),
    ):
        db.execute(
            text("UPDATE audit_events SET action = 'x' WHERE organization_id = :o"), {"o": api_tenant.org.id}
        )


# ---------- §11.2 limits by user / org / IP ----------


def test_repeated_wrong_passwords_lock_the_account_for_a_while(
    client: TestClient, api_tenant: Tenant
) -> None:
    email = api_tenant.emails[m.Role.ADMIN]
    for _ in range(get_settings().login_max_failures):
        assert (
            client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
        )
    locked = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "LOGIN_LOCKED"
    assert int(locked.headers["Retry-After"]) > 60
    # Unknown accounts lock the same way, so the lock can't confirm which emails exist.
    ghost = f"nobody-{secrets.token_hex(3)}@techcare.example"
    codes = [
        client.post("/api/v1/auth/login", json={"email": ghost, "password": "x"}).status_code
        for _ in range(6)
    ]
    assert codes == [401] * 5 + [429]
    # Other accounts are unaffected, and a success resets the count.
    manager = api_tenant.emails[m.Role.MANAGER]
    for _ in range(get_settings().login_max_failures - 1):
        client.post("/api/v1/auth/login", json={"email": manager, "password": "wrong"})
    login(client, manager)
    assert client.post("/api/v1/auth/login", json={"email": manager, "password": "wrong"}).status_code == 401


def test_new_anonymous_sessions_share_one_public_budget(
    client: TestClient, tenant: Tenant, worker: w.WorkerContext
) -> None:
    settings = worker.settings.model_copy(
        update={"public_organization": tenant.org.slug, "rate_limit_public_per_minute": 3}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    statuses = [
        client.post(
            "/api/v1/chat/ask",
            json={"question": "Hello"},
            headers={"X-Session-Ref": secrets.token_urlsafe(24)},
        ).status_code
        for _ in range(4)
    ]
    assert statuses == [200, 200, 200, 429]  # four "different" visitors, one cap


def test_the_internal_api_only_answers_allowed_networks(worker: w.WorkerContext) -> None:
    app.dependency_overrides[get_settings] = lambda: worker.settings
    token = {"Authorization": f"Bearer {worker.settings.internal_service_token.get_secret_value()}"}
    try:
        outside = TestClient(app, client=("203.0.113.9", 40000)).post(
            "/api/v1/internal/knowledge/answer", json={}, headers={**token, "X-Forwarded-For": "127.0.0.1"}
        )
        assert outside.status_code == 403 and outside.json()["error"]["code"] == "FORBIDDEN"
        inside = TestClient(app, client=("127.0.0.1", 40000)).post(
            "/api/v1/internal/knowledge/answer", json={}, headers=token
        )
        assert inside.status_code == 422  # past the network and token checks; the empty body is invalid
    finally:
        app.dependency_overrides.clear()


# ---------- §11.2 retention and deletion in every store ----------


def _conversation(db: Session, org: uuid.UUID, last_activity: timedelta) -> uuid.UUID:
    when = utcnow() - last_activity
    conversation = m.Conversation(
        organization_id=org,
        session_ref=secrets.token_hex(16),
        audience="public",
        created_at=when,
        updated_at=when,
    )
    db.add(conversation)
    db.flush()
    db.add(
        m.Message(
            organization_id=org, conversation_id=conversation.id, role="user", content="Hi", created_at=when
        )
    )
    return conversation.id


def test_retention_purges_every_store(worker: w.WorkerContext, tenant: Tenant) -> None:
    from app.ingestion.validation import validate_upload
    from app.services import documents as docs

    validated = validate_upload("old.pdf", None, warranty_pdf(), worker.settings)
    with tenant_session(tenant.org.id, worker.engine) as db:
        result = docs.create_upload(db, worker.storage, worker.settings, tenant.manager, validated)
    assert w.run_once(worker) is m.JobState.DONE
    key, version_id = result.version.storage_key, result.version.id
    assert worker.storage.exists(key) and worker.index.count_version(tenant.org.id, version_id) > 0
    with tenant_session(tenant.org.id, worker.engine) as db:
        soft_delete_document(db, tenant.admin, result.document.id)
        recent_delete = docs.create_upload(
            db,
            worker.storage,
            worker.settings,
            tenant.manager,
            validate_upload("new.txt", None, b"Recent doc", worker.settings),
        )
        soft_delete_document(db, tenant.admin, recent_delete.document.id)
        db.execute(
            update(m.Document)
            .where(m.Document.id == result.document.id)
            .values(deleted_at=utcnow() - timedelta(days=worker.settings.deleted_document_purge_days + 1))
        )
        stale = _conversation(
            db, tenant.org.id, timedelta(days=worker.settings.conversation_retention_days + 1)
        )
        fresh = _conversation(db, tenant.org.id, timedelta(days=1))

    summary = apply_retention(worker.engine, worker.index, worker.storage, worker.settings)
    assert summary.documents_purged >= 1 and summary.conversations_deleted >= 1 and summary.failures == 0

    assert not worker.storage.exists(key)  # object storage
    assert worker.index.count_version(tenant.org.id, version_id) == 0  # vector store
    with tenant_session(tenant.org.id, worker.engine) as db:  # database
        assert db.get(m.Document, result.document.id) is None
        assert db.scalar(select(func.count()).where(m.DocumentChunk.version_id == version_id)) == 0
        assert db.get(m.Document, recent_delete.document.id) is not None  # still inside its grace period
        assert db.get(m.Conversation, stale) is None
        assert db.scalar(select(func.count()).where(m.Message.conversation_id == stale)) == 0
        assert db.get(m.Conversation, fresh) is not None
        actions = set(
            db.scalars(select(m.AuditEvent.action).where(m.AuditEvent.organization_id == tenant.org.id))
        )
    assert {"document.purged", "retention.conversations_deleted"} <= actions


# ---------- §11.3 health of the worker and queue; graceful shutdown ----------


def test_a_running_worker_reports_healthy_then_stops_gracefully(
    worker: w.WorkerContext, tenant: Tenant, test_settings: Settings
) -> None:
    from app.ingestion.validation import validate_upload
    from app.services import documents as docs

    ctx = w.WorkerContext(
        worker.engine,
        worker.index,
        worker.storage,
        FakeEmbedder("test-embedding", 8),
        worker.settings.model_copy(update={"worker_poll_seconds": 5.0, "worker_heartbeat_seconds": 0.2}),
    )
    with tenant_session(tenant.org.id, ctx.engine) as db:
        job = docs.create_upload(
            db,
            ctx.storage,
            ctx.settings,
            tenant.manager,
            validate_upload("a.pdf", None, warranty_pdf(), ctx.settings),
        ).job
    stop = threading.Event()
    processed: list[int] = []
    thread = threading.Thread(target=lambda: processed.append(w.run_forever(ctx, stop)))
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with Session(ctx.engine) as db:
                if db.scalar(select(m.IngestionJob.state).where(m.IngestionJob.id == job.id)) == "done":
                    break
            time.sleep(0.1)
        health.check_worker(test_settings)  # a fresh heartbeat: no exception
        health.check_queue(test_settings)
    finally:
        started = time.monotonic()
        stop.set()
        thread.join(10)
    assert not thread.is_alive() and time.monotonic() - started < 3  # the 5 s poll sleep woke at once
    assert processed == [1]
    with pytest.raises(health.Unhealthy, match="worker"):
        health.check_worker(test_settings)  # a clean stop removes the heartbeat


def test_a_stuck_queue_is_reported(worker: w.WorkerContext, test_settings: Settings) -> None:
    worker_id = f"test:{uuid.uuid4().hex}"
    with Session(worker.engine) as db, db.begin():
        now = utcnow()
        db.add(
            m.WorkerHeartbeat(
                worker_id=worker_id,
                hostname="test",
                pid=1,
                started_at=now,
                beat_at=now,
                queue_depth=4,
                oldest_queued_at=now - timedelta(seconds=test_settings.queue_max_wait_seconds + 60),
            )
        )
    try:
        with pytest.raises(health.Unhealthy, match="4 job"):
            health.check_queue(test_settings)
    finally:
        heartbeat.remove(worker.engine, worker_id)


def test_heartbeats_count_the_claimable_queue(worker: w.WorkerContext, tenant: Tenant) -> None:
    from app.ingestion.validation import validate_upload
    from app.services import documents as docs

    with tenant_session(tenant.org.id, worker.engine) as db:
        docs.create_upload(
            db,
            worker.storage,
            worker.settings,
            tenant.manager,
            validate_upload("q.pdf", None, warranty_pdf(), worker.settings),
        )
    worker_id = f"test:{uuid.uuid4().hex}"
    heartbeat.beat(worker.engine, worker_id, utcnow(), 0)
    try:
        with Session(worker.engine) as db:
            row = db.get(m.WorkerHeartbeat, worker_id)
            assert row is not None and row.queue_depth >= 1 and row.oldest_queued_at is not None
    finally:
        heartbeat.remove(worker.engine, worker_id)


# ---------- §11.3 database timeouts; §11.2 no SQL parameters in errors ----------


def test_slow_statements_are_cancelled_and_parameters_stay_hidden(worker: w.WorkerContext) -> None:
    settings = worker.settings.model_copy(update={"db_statement_timeout_ms": 200})
    engine = build_engine(settings, worker.engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as conn, pytest.raises(DBAPIError, match="statement timeout"):
            conn.execute(text("SELECT pg_sleep(2)"))
        with engine.connect() as conn, pytest.raises(DBAPIError) as caught:
            conn.execute(text("SELECT CAST(:v AS integer)"), {"v": "jordan@example.com"})
        message = str(caught.value)
        assert "SQL parameters hidden" in message
        # Postgres itself echoes the bad value in its error text: the log filter is what removes it.
        assert "jordan@example.com" in message and "jordan@example.com" not in scrub(message)
        assert engine.url.database == TEST_DB
    finally:
        engine.dispose()
