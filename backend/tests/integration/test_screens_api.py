"""API behaviour the §10 screens rely on: citation audience, the employee product filter, conversation
deletion, document facets, tags and owner, chunk diagnostics, the activity log, idempotent publishing."""

import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.ratelimit import limiter
from app.config import Settings, get_settings
from app.core.permissions import Principal
from app.core.security import issue_token
from app.db.models import Answer, AnswerFeedback, AuditEvent, Message
from app.db.session import get_engine, tenant_session
from app.demo.builders import pdf_bytes
from app.demo.corpus import _wait_for
from app.ingestion.embeddings import get_embedder
from app.ingestion.validation import validate_upload
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag.llm import FakeChatModel, get_chat_model
from app.services import documents as docs
from app.services.storage import get_storage
from tests.integration.conftest import RagEnv, make_tenant

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
    app.dependency_overrides[get_engine] = lambda: rag_env.ctx.engine
    app.dependency_overrides[get_knowledge_index] = lambda: rag_env.ctx.index
    app.dependency_overrides[get_embedder] = lambda: rag_env.ctx.embedder
    app.dependency_overrides[get_storage] = lambda: rag_env.ctx.storage
    app.dependency_overrides[get_chat_model] = FakeChatModel
    app.dependency_overrides[get_settings] = lambda: settings
    limiter.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()


def bearer(settings: Settings, principal: Principal) -> dict[str, str]:
    assert principal.user_id is not None
    return {
        "Authorization": f"Bearer {issue_token(settings, principal.user_id, principal.organization_id)[0]}"
    }


def guest() -> dict[str, str]:
    return {"X-Session-Ref": secrets.token_urlsafe(24)}


def ask(client: TestClient, headers: dict[str, str], question: str, **body: object) -> dict[str, Any]:
    response = client.post("/api/v1/chat/ask", json={"question": question, **body}, headers=headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


# ---------- chat (§10.1, §10.2) ----------


def test_citations_say_which_audience_they_come_from(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    staff = bearer(settings, rag_env.tenant.employee)
    answer = ask(client, staff, "When can we waive the diagnostic fee?")
    assert answer["answer_status"] == "answered"
    citations = answer["citations"]
    assert isinstance(citations, list) and citations
    assert {c["audience"] for c in citations} == {"employee"}

    public = ask(client, guest(), "Why is my DemoPrinter-X2 printing faded pages?")
    assert {c["audience"] for c in public["citations"]} == {"public"}

    # History carries the same audience and a working preview link.
    history = client.get(f"/api/v1/conversations/{answer['conversation_id']}", headers=staff).json()
    cited = history["messages"][1]["citations"][0]
    assert cited["audience"] == "employee"
    assert client.get(cited["preview_url"], headers=staff).status_code == 200


def test_the_product_filter_narrows_but_keeps_general_documents(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    staff = bearer(settings, rag_env.tenant.employee)
    question = "How do I clear a paper jam?"
    matching = ask(client, staff, question, product_model="demoprinter-x2")  # case doesn't matter
    assert matching["answer_status"] == "answered"
    assert {c["title"] for c in matching["citations"]} == {"DemoPrinter-X2 Troubleshooting Guide"}

    other = ask(client, staff, question, product_model="DemoLaptop-Z1")
    assert other["answer_status"] == "insufficient_evidence" and other["citations"] == []

    general = ask(
        client, staff, "What is the restocking fee for opened items?", product_model="DemoLaptop-Z1"
    )
    assert general["answer_status"] == "answered"  # the returns policy has no product model: it stays


def test_owners_delete_one_conversation_or_clear_their_history(client: TestClient, rag_env: RagEnv) -> None:
    me, someone_else = guest(), guest()
    first = ask(client, me, "Why is my DemoPrinter-X2 printing faded pages?")
    second = ask(client, me, "What is the restocking fee for opened items?")
    theirs = ask(client, someone_else, "What does error code E-04 mean?")
    answer_id = first["answer_id"]
    assert (
        client.post(
            f"/api/v1/answers/{answer_id}/feedback", json={"rating": "helpful"}, headers=me
        ).status_code
        == 201
    )

    # Someone else's conversation looks like a missing one, and survives.
    assert client.delete(f"/api/v1/conversations/{theirs['conversation_id']}", headers=me).status_code == 404

    assert client.delete(f"/api/v1/conversations/{first['conversation_id']}", headers=me).status_code == 204
    assert client.get(f"/api/v1/conversations/{first['conversation_id']}", headers=me).status_code == 404
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        conversation_id = uuid.UUID(str(first["conversation_id"]))
        assert db.scalar(select(func.count()).where(Message.conversation_id == conversation_id)) == 0
        assert db.get(Answer, uuid.UUID(str(answer_id))) is None
        assert (
            db.scalar(select(func.count()).where(AnswerFeedback.answer_id == uuid.UUID(str(answer_id)))) == 0
        )

    assert [c["id"] for c in client.get("/api/v1/conversations", headers=me).json()] == [
        second["conversation_id"]
    ]
    assert client.delete("/api/v1/conversations", headers=me).status_code == 204
    assert client.get("/api/v1/conversations", headers=me).json() == []
    assert len(client.get("/api/v1/conversations", headers=someone_else).json()) == 1


# ---------- documents (§10.2 filter values, §10.3 table and detail) ----------


def test_facets_follow_what_each_role_may_see(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    staff = client.get("/api/v1/documents/facets", headers=bearer(settings, rag_env.tenant.employee)).json()
    assert staff["product_models"] == ["DemoPrinter-X2"] and staff["tags"] == []  # tags are an admin filter
    assert "Internal" in staff["categories"] and staff["languages"] == ["en"]  # staff see employee documents
    other = make_tenant(rag_env.ctx.engine)
    empty = client.get("/api/v1/documents/facets", headers=bearer(settings, other.employee)).json()
    assert all(values == [] for values in empty.values())  # another tenant's values never appear


def upload(
    client: TestClient, settings: Settings, principal: Principal, title: str, **fields: str
) -> dict[str, Any]:
    data = pdf_bytes([[("h1", title), ("p", f"Revision {uuid.uuid4()} of the counter checklist.")]])
    response = client.post(
        "/api/v1/documents",
        files={"file": (f"{title}.pdf", data, "application/pdf")},
        data={"audience": "employee", "title": title, **fields},
        headers=bearer(settings, principal),
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


def test_tags_and_owner_appear_in_the_table_and_filter_it(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    title = f"Counter checklist {secrets.token_hex(3)}"
    upload(client, settings, rag_env.tenant.manager, title, tags=" Store Ops, checklists ,store ops")
    manager = bearer(settings, rag_env.tenant.manager)
    page = client.get("/api/v1/documents", params={"q": title}, headers=manager).json()
    row = page["items"][0]
    assert (row["tags"], row["owner"]) == (["store ops", "checklists"], "Manager")
    tagged = client.get("/api/v1/documents", params={"tag": "Checklists"}, headers=manager).json()
    assert title in [d["title"] for d in tagged["items"]]
    assert "store ops" in client.get("/api/v1/documents/facets", headers=manager).json()["tags"]

    too_many = ",".join(f"t{i}" for i in range(11))
    refused = client.post(
        "/api/v1/documents",
        files={"file": ("x.pdf", pdf_bytes([[("p", "x")]]), "application/pdf")},
        data={"audience": "public", "tags": too_many},
        headers=manager,
    )
    assert refused.status_code == 422 and refused.json()["error"]["details"]["fields"][0]["field"] == "tags"


def test_chunk_diagnostics_compare_postgres_with_the_index(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    accepted = upload(client, settings, rag_env.tenant.manager, f"Diagnostics {secrets.token_hex(3)}")
    _wait_for(rag_env.ctx, accepted["job"]["id"], 60)
    url = f"/api/v1/documents/{accepted['document_id']}/versions/{accepted['version_id']}/chunks"
    manager = bearer(settings, rag_env.tenant.manager)

    report = client.get(url, headers=manager).json()
    assert (
        report["consistent"] is True
        and report["expected"] == report["postgres_chunks"] == report["chroma_chunks"]
    )
    first = report["chunks"][0]
    assert first["in_index"] is True and "counter checklist" in first["preview"].lower()
    assert first["chroma_id"] == f"{rag_env.tenant.org.id}:{accepted['version_id']}:0"

    rag_env.ctx.index._collection.delete(ids=[first["chroma_id"]])  # simulate a lost vector
    drifted = client.get(url, headers=manager).json()
    assert drifted["consistent"] is False and drifted["chunks"][0]["in_index"] is False

    assert client.get(url, headers=bearer(settings, rag_env.tenant.employee)).status_code == 403
    warranty = rag_env.documents["TechCare Laptop Warranty"]
    mixed = f"/api/v1/documents/{warranty.id}/versions/{accepted['version_id']}/chunks"
    assert client.get(mixed, headers=manager).status_code == 404


def test_activity_lists_every_change_with_its_actor(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    accepted = upload(client, settings, rag_env.tenant.manager, f"Activity {secrets.token_hex(3)}")
    _wait_for(rag_env.ctx, accepted["job"]["id"], 60)
    document_id, version_id = accepted["document_id"], accepted["version_id"]
    manager = bearer(settings, rag_env.tenant.manager)
    publish = {"version_id": version_id}
    assert (
        client.post(f"/api/v1/documents/{document_id}/publish", json=publish, headers=manager).status_code
        == 200
    )
    # Idempotent: a repeated publish (double click) changes nothing and logs nothing.
    again = client.post(f"/api/v1/documents/{document_id}/publish", json=publish, headers=manager)
    assert again.status_code == 200 and again.json()["active_version_number"] == 1
    admin = bearer(settings, rag_env.tenant.admin)
    assert client.delete(f"/api/v1/documents/{document_id}", headers=admin).status_code == 204

    # A deleted document disappears from every screen, its activity tab included; the audit rows stay.
    assert client.get(f"/api/v1/documents/{document_id}/activity", headers=manager).status_code == 404
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        actions = list(
            db.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.target_id.in_([uuid.UUID(document_id), uuid.UUID(version_id)]))
                .order_by(AuditEvent.created_at)
            )
        )
    assert actions == ["document.version.uploaded", "document.version.published", "document.deleted"]


def test_activity_feed_names_actors_newest_first(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    accepted = upload(client, settings, rag_env.tenant.manager, f"Feed {secrets.token_hex(3)}")
    _wait_for(rag_env.ctx, accepted["job"]["id"], 60)
    document_id = accepted["document_id"]
    admin = bearer(settings, rag_env.tenant.admin)
    client.post(
        f"/api/v1/documents/{document_id}/publish", json={"version_id": accepted["version_id"]}, headers=admin
    )
    feed = client.get(f"/api/v1/documents/{document_id}/activity", headers=admin).json()
    assert [(e["action"], e["actor"]) for e in feed] == [
        ("document.version.published", "Admin"),
        ("document.version.uploaded", "Manager"),
    ]
    assert all(e["request_id"] for e in feed)
    assert (
        client.get(
            f"/api/v1/documents/{document_id}/activity", headers=bearer(settings, rag_env.tenant.employee)
        ).status_code
        == 403
    )
    other = make_tenant(rag_env.ctx.engine)
    assert (
        client.get(
            f"/api/v1/documents/{document_id}/activity", headers=bearer(settings, other.admin)
        ).status_code
        == 404
    )


def test_uploads_without_tags_still_work_through_the_service(rag_env: RagEnv) -> None:
    validated = validate_upload("plain.txt", None, f"Plain {uuid.uuid4()}".encode(), rag_env.ctx.settings)
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        result = docs.create_upload(
            db, rag_env.ctx.storage, rag_env.ctx.settings, rag_env.tenant.manager, validated
        )
        assert result.document.tags == []
