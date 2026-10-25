"""The review queue and CSV exports for managers (plan §2.2 "data export and administrative review workflows",
ADR-030)."""

import csv
import io
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.api.ratelimit import limiter
from app.config import Settings, get_settings
from app.db import models as m
from app.db.session import get_engine, tenant_session
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag.llm import FakeChatModel, get_chat_model
from tests.integration.conftest import RagEnv, make_tenant
from tests.integration.test_screens_api import ask, bearer, guest

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(rag_env: RagEnv) -> Settings:
    return get_settings().model_copy(
        update={
            "public_organization": rag_env.tenant.org.slug,
            "rate_limit_anonymous_per_minute": 1000,
            "rate_limit_user_per_minute": 1000,
        }
    )


@pytest.fixture
def client(rag_env: RagEnv, settings: Settings) -> Iterator[TestClient]:
    limiter.reset()
    app.dependency_overrides[get_engine] = lambda: rag_env.ctx.engine
    app.dependency_overrides[get_knowledge_index] = lambda: rag_env.ctx.index
    app.dependency_overrides[get_chat_model] = FakeChatModel
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def queued(client: TestClient) -> tuple[str, str]:
    """A guest's unanswered question and an answer they rated not helpful: both answer IDs."""
    me = guest()
    unanswered = ask(client, me, "Do you sell drones? Email me at pat.doe@example.com")
    assert unanswered["answer_status"] == "insufficient_evidence"
    answered = ask(client, me, "Why is my DemoPrinter-X2 printing faded pages?")
    assert answered["answer_status"] == "answered"
    rated = client.post(
        f"/api/v1/answers/{answered['answer_id']}/feedback",
        json={"rating": "not_helpful", "reasons": ["incomplete"], "note": "Call me on 555-010-7788"},
        headers=me,
    )
    assert rated.status_code == 201, rated.text
    return str(unanswered["answer_id"]), str(answered["answer_id"])


def page(client: TestClient, headers: dict[str, str], **params: Any) -> dict[str, Any]:
    response = client.get("/api/v1/admin/reviews", params={"limit": 100, **params}, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["answer_id"]: item for item in body["items"]}


# ---------- review queue ----------


def test_unanswered_and_not_helpful_answers_wait_in_the_queue_redacted(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    unanswered, not_helpful = queued(client)
    manager = bearer(settings, rag_env.tenant.manager)
    items = by_id(page(client, manager))
    assert items[unanswered]["reasons"] == ["unanswered"] and items[unanswered]["status"] == "open"
    assert "[email]" in items[unanswered]["question"] and "pat.doe" not in items[unanswered]["question"]
    assert items[not_helpful]["reasons"] == ["not_helpful"] and items[not_helpful]["answer"]
    feedback = items[not_helpful]["feedback"][0]
    assert feedback["reasons"] == ["incomplete"] and "7788" not in feedback["note"]
    only_rated = by_id(page(client, manager, reason="not_helpful"))
    assert not_helpful in only_rated and unanswered not in only_rated


def test_a_manager_resolves_dismisses_and_reopens_and_every_decision_is_audited(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    unanswered, not_helpful = queued(client)
    manager = bearer(settings, rag_env.tenant.manager)
    before = page(client, manager)["counts"]
    document = rag_env.documents["TechCare Returns Policy"]

    resolved = client.put(
        f"/api/v1/admin/reviews/{unanswered}",
        json={"status": "resolved", "note": "Added drones to the FAQ", "document_id": str(document.id)},
        headers=manager,
    )
    assert resolved.status_code == 200, resolved.text
    item = resolved.json()
    assert item["status"] == "resolved" and item["document"]["title"] == document.title
    assert item["reviewed_by"] and item["reviewed_at"]
    after = page(client, manager)["counts"]
    assert (after["open"], after["resolved"]) == (before["open"] - 1, before["resolved"] + 1)
    assert unanswered in by_id(page(client, manager, status="resolved"))

    dismissed = client.put(
        f"/api/v1/admin/reviews/{not_helpful}", json={"status": "dismissed"}, headers=manager
    )
    assert dismissed.json()["status"] == "dismissed" and dismissed.json()["document"] is None
    assert client.delete(f"/api/v1/admin/reviews/{not_helpful}", headers=manager).status_code == 204
    assert by_id(page(client, manager))[not_helpful]["status"] == "open"

    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        actions = set(
            session.scalars(
                select(m.AuditEvent.action).where(
                    m.AuditEvent.target_id.in_([uuid.UUID(unanswered), uuid.UUID(not_helpful)])
                )
            )
        )
    assert {"review.resolved", "review.dismissed", "review.reopened"} <= actions


def test_only_queued_answers_and_live_documents_can_be_decided(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    unanswered, _ = queued(client)
    manager = bearer(settings, rag_env.tenant.manager)
    fine = ask(client, guest(), "What is the restocking fee for opened items?")  # answered, never rated
    assert fine["answer_status"] == "answered"
    url = f"/api/v1/admin/reviews/{fine['answer_id']}"
    assert client.put(url, json={"status": "dismissed"}, headers=manager).status_code == 404
    missing_doc = client.put(
        f"/api/v1/admin/reviews/{unanswered}",
        json={"status": "resolved", "document_id": str(uuid.uuid4())},
        headers=manager,
    )
    assert missing_doc.status_code == 422


def test_reviews_are_for_this_tenants_managers_and_admins(
    client: TestClient, rag_env: RagEnv, settings: Settings, app_engine: Engine
) -> None:
    unanswered, _ = queued(client)
    for role in (rag_env.tenant.employee, rag_env.tenant.customer):
        assert client.get("/api/v1/admin/reviews", headers=bearer(settings, role)).status_code == 403
    assert client.get("/api/v1/admin/reviews", headers=guest()).status_code == 401
    assert unanswered in by_id(page(client, bearer(settings, rag_env.tenant.admin)))
    other = bearer(settings, make_tenant(app_engine).manager)
    assert page(client, other)["items"] == []
    decision = client.put(f"/api/v1/admin/reviews/{unanswered}", json={"status": "dismissed"}, headers=other)
    assert decision.status_code == 404


# ---------- exports ----------


def download(client: TestClient, headers: dict[str, str], dataset: str) -> list[dict[str, str]]:
    response = client.get(f"/api/v1/admin/exports/{dataset}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"].startswith(f'attachment; filename="{dataset}')
    assert response.content.startswith(b"\xef\xbb\xbf")  # UTF-8 byte-order mark for spreadsheet apps
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_exports_are_redacted_csv_and_audited(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    unanswered, not_helpful = queued(client)
    formula = ask(client, guest(), "=HYPERLINK(1) is the restocking fee refundable?")
    manager = bearer(settings, rag_env.tenant.manager)

    questions = {row["answer_id"]: row for row in download(client, manager, "questions")}
    assert questions[unanswered]["outcome"] == "insufficient_evidence"
    assert "[email]" in questions[unanswered]["question"]
    assert questions[not_helpful]["not_helpful"] == "1" and int(questions[not_helpful]["citations"]) >= 1
    assert questions[str(formula["answer_id"])]["question"].startswith("'=")  # never run as a formula

    feedback = [row for row in download(client, manager, "feedback") if row["answer_id"] == not_helpful]
    assert feedback[0]["rating"] == "not_helpful" and "7788" not in feedback[0]["note"]

    titles = {row["title"] for row in download(client, manager, "documents")}
    assert "TechCare Returns Policy" in titles
    download(client, manager, "reviews")
    download(client, manager, "tickets")

    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        events = list(session.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "export.downloaded")))
    datasets = {e.details["dataset"] for e in events}
    assert {"questions", "feedback", "documents", "reviews", "tickets"} <= datasets
    assert all(isinstance(e.details["rows"], int) for e in events)


def test_exports_are_for_managers_and_admins_with_a_valid_range(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    employee = bearer(settings, rag_env.tenant.employee)
    assert client.get("/api/v1/admin/exports/questions", headers=employee).status_code == 403
    manager = bearer(settings, rag_env.tenant.manager)
    assert client.get("/api/v1/admin/exports/conversations", headers=manager).status_code == 422
    backwards = client.get(
        "/api/v1/admin/exports/questions",
        params={"start": "2026-10-10", "end": "2026-10-01"},
        headers=manager,
    )
    assert backwards.status_code == 422 and backwards.json()["error"]["code"] == "INVALID_RANGE"


def test_the_dashboard_has_feedback_by_day(client: TestClient, rag_env: RagEnv, settings: Settings) -> None:
    queued(client)
    body = client.get("/api/v1/admin/analytics", headers=bearer(settings, rag_env.tenant.manager)).json()
    days = body["feedback_daily"]
    assert len(days) == 7 and set(days[0]) == {"date", "helpful", "not_helpful"}
    assert sum(d["not_helpful"] for d in days) == body["feedback"]["not_helpful"] >= 1
