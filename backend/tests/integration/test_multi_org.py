"""Plan §2.2 "multiple organizations with independently tested access isolation": one sweep over every
tenant-scoped route. Organization B's staff and customers hold real IDs from organization A and get nothing:
404 for an ID (never 403, which would confirm it exists), empty lists, and zero counts. RLS (ADR-011) is the
second layer under each of these; test_schema checks it on its own."""

import json
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
from tests.integration.conftest import RagEnv, Tenant, make_tenant
from tests.integration.test_screens_api import ask, bearer

pytestmark = pytest.mark.integration

QUESTION = "Why is my DemoPrinter-X2 printing faded pages?"


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


@pytest.fixture
def org_b(app_engine: Engine) -> Tenant:
    return make_tenant(app_engine)


def a_ids(client: TestClient, rag_env: RagEnv, settings: Settings) -> dict[str, Any]:
    """Real things in organization A: a document, its version and job, a conversation, a rated unanswered
    answer, a ticket."""
    customer = bearer(settings, rag_env.tenant.customer)
    answered = ask(client, customer, QUESTION)
    unanswered = ask(client, customer, "Do you sell drones?")
    assert unanswered["answer_status"] == "insufficient_evidence"
    client.post(
        f"/api/v1/answers/{unanswered['answer_id']}/feedback",
        json={"rating": "not_helpful"},
        headers=customer,
    )
    ticket = client.post(
        "/api/v1/tickets",
        json={"subject": "Org A printer case", "summary": "Org A only."},
        headers=bearer(settings, rag_env.tenant.manager),
    ).json()
    document = rag_env.documents["DemoPrinter-X2 Troubleshooting Guide"]
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        job = session.scalar(
            select(m.IngestionJob.id).where(m.IngestionJob.version_id == document.active_version_id)
        )
    return {
        "document": document.id,
        "version": document.active_version_id,
        "job": job,
        "conversation": answered["conversation_id"],
        "answer": unanswered["answer_id"],
        "ticket": ticket["id"],
    }


def test_another_organizations_ids_lead_nowhere(
    client: TestClient, rag_env: RagEnv, settings: Settings, org_b: Tenant
) -> None:
    a = a_ids(client, rag_env, settings)
    admin, customer = bearer(settings, org_b.admin), bearer(settings, org_b.customer)
    by_id: list[tuple[str, str, dict[str, str], dict[str, Any] | None]] = [
        ("GET", f"/api/v1/documents/{a['document']}", admin, None),
        ("PATCH", f"/api/v1/documents/{a['document']}", admin, {"brand": "B"}),
        ("GET", f"/api/v1/documents/{a['document']}/activity", admin, None),
        ("GET", f"/api/v1/documents/{a['document']}/versions/{a['version']}/chunks", admin, None),
        ("GET", f"/api/v1/documents/{a['document']}/preview", admin, None),
        ("GET", f"/api/v1/ingestion-jobs/{a['job']}", admin, None),
        ("POST", f"/api/v1/ingestion-jobs/{a['job']}/retry", admin, None),
        ("GET", f"/api/v1/conversations/{a['conversation']}", admin, None),
        ("POST", f"/api/v1/answers/{a['answer']}/feedback", admin, {"rating": "helpful"}),
        ("GET", f"/api/v1/tickets/{a['ticket']}", admin, None),
        ("PATCH", f"/api/v1/tickets/{a['ticket']}", admin, {"status": "resolved"}),
        ("POST", f"/api/v1/tickets/{a['ticket']}/notes", admin, {"body": "From org B"}),
        ("POST", "/api/v1/tickets/draft", admin, {"conversation_id": a["conversation"]}),
        ("PUT", f"/api/v1/admin/reviews/{a['answer']}", admin, {"status": "dismissed"}),
        ("DELETE", f"/api/v1/admin/reviews/{a['answer']}", admin, None),
        ("GET", f"/api/v1/conversations/{a['conversation']}", customer, None),
    ]
    for method, url, headers, body in by_id:
        response = client.request(method, url, headers=headers, json=body)
        assert response.status_code == 404, (method, url, response.status_code, response.text)


def test_another_organizations_lists_counts_and_exports_are_empty(
    client: TestClient, rag_env: RagEnv, settings: Settings, org_b: Tenant
) -> None:
    a = a_ids(client, rag_env, settings)
    admin = bearer(settings, org_b.admin)
    leaked = {str(v) for v in a.values()} | {"DemoPrinter-X2", "Org A printer case"}

    def visible(url: str, **params: Any) -> str:
        response = client.get(url, params=params, headers=admin)
        assert response.status_code == 200, (url, response.text)
        return response.text

    for url in (
        "/api/v1/documents",
        "/api/v1/documents/facets",
        "/api/v1/conversations",
        "/api/v1/tickets",
        "/api/v1/tickets/staff",
        "/api/v1/admin/reviews",
        "/api/v1/admin/analytics",
        "/api/v1/admin/exports/questions",
        "/api/v1/admin/exports/feedback",
        "/api/v1/admin/exports/documents",
        "/api/v1/admin/exports/tickets",
    ):
        text = visible(url)
        assert not any(value in text for value in leaked), url
    analytics = json.loads(visible("/api/v1/admin/analytics"))
    assert analytics["totals"]["questions"] == 0 and analytics["most_cited"] == []
    assert json.loads(visible("/api/v1/tickets"))["total"] == 0
    assert json.loads(visible("/api/v1/admin/reviews"))["counts"] == {
        "open": 0,
        "resolved": 0,
        "dismissed": 0,
    }


def test_another_organizations_customers_get_no_answers_from_its_documents(
    client: TestClient, rag_env: RagEnv, settings: Settings, org_b: Tenant
) -> None:
    for principal in (org_b.customer, org_b.employee):
        answer = ask(client, bearer(settings, principal), QUESTION)
        assert answer["answer_status"] == "insufficient_evidence" and answer["citations"] == []
    assert client.get("/api/v1/orders/mine", headers=bearer(settings, org_b.customer)).json() == []
