"""Blueprint §8/§9 over HTTP: /chat/ask (JSON + SSE), conversations, feedback, rate limits, voice."""

import hashlib
import json
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
from app.db import models as m
from app.db.session import get_engine, tenant_session
from app.demo.corpus import EMPLOYEE_CANARY
from app.ingestion.embeddings import get_embedder
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag.llm import FakeChatModel, get_chat_model
from tests.integration.conftest import RagEnv

pytestmark = pytest.mark.integration

WARRANTY_Q = "How long are TechCare laptops covered by the warranty?"


@pytest.fixture
def settings(rag_env: RagEnv) -> Settings:
    return get_settings().model_copy(
        update={
            "public_organization": rag_env.tenant.org.slug,
            "internal_service_organization": rag_env.tenant.org.slug,
            "rate_limit_anonymous_per_minute": 1000,
            "rate_limit_user_per_minute": 1000,
        }
    )


@pytest.fixture
def client(rag_env: RagEnv, settings: Settings) -> Iterator[TestClient]:
    app.dependency_overrides[get_engine] = lambda: rag_env.ctx.engine
    app.dependency_overrides[get_knowledge_index] = lambda: rag_env.ctx.index
    app.dependency_overrides[get_embedder] = lambda: rag_env.ctx.embedder
    app.dependency_overrides[get_chat_model] = FakeChatModel
    app.dependency_overrides[get_settings] = lambda: settings
    limiter.reset()
    yield TestClient(app, client=("127.0.0.1", 50000))  # inside INTERNAL_API_ALLOWED_NETWORKS
    app.dependency_overrides.clear()
    limiter.reset()


def session_headers() -> dict[str, str]:
    return {"X-Session-Ref": secrets.token_urlsafe(24)}


def bearer(settings: Settings, principal: Principal) -> dict[str, str]:
    assert principal.user_id is not None
    token, _ = issue_token(settings, principal.user_id, principal.organization_id)
    return {"Authorization": f"Bearer {token}"}


def sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":")
        )
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


# ---------- ask (§9.1) ----------


def test_anonymous_ask_returns_the_documented_shape(client: TestClient) -> None:
    response = client.post("/api/v1/chat/ask", json={"question": WARRANTY_Q}, headers=session_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "request_id",
        "trace_id",
        "conversation_id",
        "answer_id",
        "answer_status",
        "grounded",
        "answer",
        "citations",
        "suggested_followups",
        "next_action",  # US-05: "verify_order" asks the client to show the order card
        "records",  # ADR-031: the caller's own orders/repairs an answer is about
    }
    assert (body["answer_status"], body["grounded"]) == ("answered", True)
    assert body["next_action"] is None and body["records"] == []
    assert body["request_id"] == response.headers["X-Request-ID"]
    citation = body["citations"][0]
    assert set(citation) == {
        "position",
        "document_id",
        "version_id",
        "chunk_id",
        "title",
        "page_start",
        "page_end",
        "section",
        "quote",
        "audience",
        "preview_url",
    }
    assert citation["preview_url"] == (
        f"/api/v1/documents/{citation['document_id']}/preview?version_id={citation['version_id']}"
        f"&page={citation['page_start']}"
    )


def test_callers_need_a_session_or_a_token(client: TestClient) -> None:
    assert client.post("/api/v1/chat/ask", json={"question": WARRANTY_Q}).status_code == 401
    short = client.post("/api/v1/chat/ask", json={"question": WARRANTY_Q}, headers={"X-Session-Ref": "short"})
    assert short.status_code == 422


def test_invalid_questions_get_422(client: TestClient) -> None:
    for body in ({"question": ""}, {"question": "x" * 4001}, {}):
        assert client.post("/api/v1/chat/ask", json=body, headers=session_headers()).status_code == 422


def test_staff_see_employee_answers_customers_never_do(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    question = {"question": "When can we waive the diagnostic fee?"}
    staff = client.post(
        "/api/v1/chat/ask", json=question, headers=bearer(settings, rag_env.tenant.employee)
    ).json()
    assert staff["answer_status"] == "answered" and staff["citations"][0]["title"] == "Repair Desk Procedures"
    for headers in (session_headers(), bearer(settings, rag_env.tenant.customer)):
        body = client.post("/api/v1/chat/ask", json=question, headers=headers).json()
        assert body["answer_status"] == "insufficient_evidence" and body["citations"] == []
        assert EMPLOYEE_CANARY not in json.dumps(body)


# ---------- streaming (docs/architecture.md §4) ----------


def test_the_stream_sends_meta_status_delta_then_final(client: TestClient) -> None:
    response = client.post(
        "/api/v1/chat/ask/stream", json={"question": WARRANTY_Q}, headers=session_headers()
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = sse(response.text)
    assert [name for name, _ in events] == ["meta", "status", "delta", "status", "final"]
    meta, final = events[0][1], events[-1][1]
    assert meta["request_id"] == final["request_id"] and meta["trace_id"] == final["trace_id"]
    assert final["answer_status"] == "answered" and final["citations"]


def test_refusals_happen_before_the_stream_starts(client: TestClient) -> None:
    owner = session_headers()
    first = client.post("/api/v1/chat/ask", json={"question": WARRANTY_Q}, headers=owner).json()
    stranger = client.post(
        "/api/v1/chat/ask/stream",
        json={"question": "And batteries?", "conversation_id": first["conversation_id"]},
        headers=session_headers(),
    )
    assert stranger.status_code == 404
    assert stranger.headers["content-type"].startswith("application/json")
    assert stranger.json()["error"]["code"] == "NOT_FOUND"


# ---------- conversations ----------


def test_conversations_are_private_to_their_owner(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    owner = session_headers()
    first = client.post("/api/v1/chat/ask", json={"question": WARRANTY_Q}, headers=owner).json()
    client.post(
        "/api/v1/chat/ask",
        json={"question": "What are the store hours on Sunday?", "conversation_id": first["conversation_id"]},
        headers=owner,
    )
    listing = client.get("/api/v1/conversations", headers=owner).json()
    assert [(c["id"], c["title"], c["message_count"]) for c in listing] == [
        (first["conversation_id"], WARRANTY_Q, 4)
    ]
    detail = client.get(f"/api/v1/conversations/{first['conversation_id']}", headers=owner).json()
    assert [msg["role"] for msg in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assistant = detail["messages"][1]
    assert assistant["answer_status"] == "answered" and assistant["grounded"] is True
    assert assistant["citations"][0]["title"] == "TechCare Laptop Warranty"

    other = session_headers()
    assert client.get("/api/v1/conversations", headers=other).json() == []
    assert client.get(f"/api/v1/conversations/{first['conversation_id']}", headers=other).status_code == 404
    staff = bearer(
        settings, rag_env.tenant.admin
    )  # not even an admin reads a customer's chat through this API
    assert client.get(f"/api/v1/conversations/{first['conversation_id']}", headers=staff).status_code == 404
    assert client.get(f"/api/v1/conversations/{uuid.uuid4()}", headers=owner).status_code == 404


def test_signed_in_users_own_their_conversations(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    headers = bearer(settings, rag_env.tenant.employee)
    asked = client.post("/api/v1/chat/ask", json={"question": WARRANTY_Q}, headers=headers).json()
    ids = [c["id"] for c in client.get("/api/v1/conversations", headers=headers).json()]
    assert asked["conversation_id"] in ids


# ---------- feedback ----------


def test_feedback_on_your_own_answer(client: TestClient) -> None:
    owner = session_headers()
    answer = client.post("/api/v1/chat/ask", json={"question": WARRANTY_Q}, headers=owner).json()
    url = f"/api/v1/answers/{answer['answer_id']}/feedback"
    good = client.post(
        url,
        json={"rating": "not_helpful", "reasons": ["incomplete"], "note": "Also batteries?"},
        headers=owner,
    )
    assert good.status_code == 201 and uuid.UUID(good.json()["id"])
    assert client.post(url, json={"rating": "helpful"}, headers=session_headers()).status_code == 404
    assert client.post(url, json={"rating": "meh"}, headers=owner).status_code == 422
    assert client.post(url, json={"rating": "helpful", "note": "x" * 2001}, headers=owner).status_code == 422


# ---------- rate limits (§3 budget) ----------


def test_rate_limited_callers_get_429_with_retry_after(client: TestClient, settings: Settings) -> None:
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"rate_limit_anonymous_per_minute": 2}
    )
    headers = session_headers()
    codes = [
        client.post("/api/v1/chat/ask", json={"question": "hi"}, headers=headers).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    limited = client.post("/api/v1/chat/ask", json={"question": "hi"}, headers=headers)
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert (
        client.post("/api/v1/chat/ask", json={"question": "hi"}, headers=session_headers()).status_code == 200
    )


# ---------- voice contract (§9.2) ----------


def test_voice_contract(client: TestClient, settings: Settings) -> None:
    url = "/api/v1/internal/knowledge/answer"
    body = {
        "request_id": "voice-request-123",
        "audience": "public",
        "channel": "voice",
        "question": "What are the store hours on Sunday?",
        "conversation_id": f"call-{uuid.uuid4()}",
    }
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    service = {"Authorization": f"Bearer {settings.internal_service_token.get_secret_value()}"}
    assert client.post(url, json={**body, "audience": "employee"}, headers=service).status_code == 403

    first = client.post(url, json=body, headers=service)
    assert first.status_code == 200, first.text
    answer = first.json()
    assert answer["request_id"] == "voice-request-123" and answer["answer_status"] == "answered"
    assert "[" not in answer["speech_summary"] and answer["speech_summary"].count(".") <= 2
    assert set(answer["citations"][0]) == {"document_id", "version_id", "page_start"}

    unknown = client.post(url, json={**body, "question": "Do you sell drones?"}, headers=service).json()
    assert unknown["answer_status"] == "insufficient_evidence"
    assert unknown["speech_summary"] == "Our documents don't cover this yet. Our support team can help."
    assert unknown["citations"] == []


def test_a_voice_call_keeps_one_conversation(client: TestClient, settings: Settings, rag_env: RagEnv) -> None:
    service = {"Authorization": f"Bearer {settings.internal_service_token.get_secret_value()}"}
    call = f"call-{uuid.uuid4()}"
    for question in ["What are the store hours on Sunday?", "And on holidays?"]:
        client.post(
            "/api/v1/internal/knowledge/answer",
            json={"question": question, "conversation_id": call},
            headers=service,
        )
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        ref = hashlib.sha256(f"voice:{call}".encode()).hexdigest()
        conversations = session.scalar(
            select(func.count()).select_from(m.Conversation).where(m.Conversation.session_ref == ref)
        )
    assert conversations == 1


def test_customers_cannot_open_employee_documents_by_id(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    internal = rag_env.documents["Repair Desk Procedures"]
    customer = bearer(settings, rag_env.tenant.customer)
    assert client.get(f"/api/v1/documents/{internal.id}", headers=customer).status_code == 404
    staff = bearer(settings, rag_env.tenant.employee)
    assert client.get(f"/api/v1/documents/{internal.id}", headers=staff).status_code == 200
