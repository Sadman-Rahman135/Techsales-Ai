"""Blueprint §9 `GET /admin/analytics` (US-06): counts match activity, PII is redacted, roles and ranges."""

import hashlib
import secrets
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.config import Settings, get_settings
from app.core.permissions import Principal
from app.core.security import issue_token
from app.core.time import utcnow
from app.db import models as m
from app.db.session import get_engine, tenant_session
from app.demo.builders import scanned_pdf_bytes
from app.demo.corpus import _wait_for
from app.ingestion.validation import validate_upload
from app.main import app
from app.rag import service
from app.rag.graph import RAGContext
from app.rag.llm import ChatModelError, FakeChatModel
from app.services import documents as docs
from tests.integration.conftest import RagEnv, make_tenant

pytestmark = pytest.mark.integration

PRIVATE_QUESTION = "Can you email jo.doe@mail.example or call 555-201-3344 about my drone warranty?"


class BrokenModel(FakeChatModel):
    def generate_answer(self, *args: Any, **kwargs: Any) -> Any:
        raise ChatModelError("APITimeoutError")


@pytest.fixture(scope="module")
def activity(rag_env: RagEnv) -> dict[str, Any]:
    """A known mix in this tenant: 3 answered, 2 not in documents, 1 direct, 1 error, feedback, and a failed
    ingestion."""

    def ask(question: str, llm: Any = None) -> Any:
        ctx = RAGContext(
            principal=Principal(rag_env.tenant.org.id),
            engine=rag_env.ctx.engine,
            index=rag_env.ctx.index,
            embedder=rag_env.ctx.embedder,
            llm=llm or FakeChatModel(),
            settings=rag_env.ctx.settings,
            request_id=f"req-{uuid.uuid4().hex}",
            trace_id=f"trace-{uuid.uuid4().hex}",
            session_ref=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        )
        return service.ask(ctx, question)

    answered = [
        ask("How long are TechCare laptops covered by the warranty?"),
        ask("How long are TechCare laptops covered by the warranty?"),
        ask("How do I clear a paper jam in the DemoPrinter-X2?"),
    ]
    unanswered = [ask(PRIVATE_QUESTION), ask("Do you sell electric bikes?")]
    direct = ask("Where is my order TC-123456?")
    failed = ask("How do I connect the DemoPrinter-X2 printer to Wi-Fi?", llm=BrokenModel())

    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        session.add(
            m.AnswerFeedback(
                organization_id=rag_env.tenant.org.id, answer_id=answered[0].answer_id, rating="helpful"
            )
        )
        session.add(
            m.AnswerFeedback(
                organization_id=rag_env.tenant.org.id,
                answer_id=answered[2].answer_id,
                rating="not_helpful",
                reasons=["incomplete"],
                note="Call me on 555-201-3344, the door won't open",
            )
        )
    validated = validate_upload("scan.pdf", None, scanned_pdf_bytes(), rag_env.ctx.settings)
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        upload = docs.create_upload(
            session,
            rag_env.ctx.storage,
            rag_env.ctx.settings,
            rag_env.tenant.manager,
            validated,
            title="Scanned service form",
        )
    try:
        _wait_for(rag_env.ctx, upload.job.id, 60)
    except RuntimeError:
        pass  # expected: the scan has no text
    return {
        "answered": answered,
        "unanswered": unanswered,
        "direct": direct,
        "failed": failed,
        "upload": upload,
    }


@pytest.fixture
def settings(rag_env: RagEnv) -> Settings:
    return get_settings().model_copy(update={"public_organization": rag_env.tenant.org.slug})


@pytest.fixture
def client(rag_env: RagEnv, settings: Settings) -> Iterator[TestClient]:
    app.dependency_overrides[get_engine] = lambda: rag_env.ctx.engine
    app.dependency_overrides[get_settings] = lambda: settings
    limiter.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()


def bearer(settings: Settings, principal: Principal) -> dict[str, str]:
    assert principal.user_id is not None
    return {
        "Authorization": f"Bearer {issue_token(settings, principal.user_id, principal.organization_id)[0]}"
    }


def analytics(client: TestClient, settings: Settings, principal: Principal, **params: str) -> Any:
    return client.get("/api/v1/admin/analytics", params=params, headers=bearer(settings, principal))


@pytest.mark.usefixtures("activity")
def test_counts_match_the_activity(client: TestClient, rag_env: RagEnv, settings: Settings) -> None:
    body = analytics(client, settings, rag_env.tenant.manager).json()
    assert body["totals"] == {
        "answered": 3,
        "insufficient_evidence": 2,
        "escalated": 0,
        "error": 1,
        "direct": 1,
        "questions": 7,
    }
    assert sum(day["answered"] for day in body["daily"]) == 3 and len(body["daily"]) == 7
    intents = {row["intent"]: row["count"] for row in body["intents"]}
    assert intents == {"knowledge": 6, "order": 1}
    assert body["latency_ms"]["p50"] is not None and body["latency_ms"]["p95"] >= body["latency_ms"]["p50"]
    cited = {row["title"]: row["citations"] for row in body["most_cited"]}
    assert cited["TechCare Laptop Warranty"] == 2 and cited["DemoPrinter-X2 Troubleshooting Guide"] == 1


@pytest.mark.usefixtures("activity")
def test_unanswered_questions_are_listed_redacted(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    body = analytics(client, settings, rag_env.tenant.admin).json()
    questions = [row["question"] for row in body["unanswered"]]
    assert questions == [  # newest first
        "Do you sell electric bikes?",
        "Can you email [email] or call [phone] about my drone warranty?",
    ]
    assert all(row["conversation"] == "restricted" for row in body["unanswered"])  # needs conversations:read
    text = str(body)
    assert "jo.doe@mail.example" not in text and "555-201-3344" not in text
    topics = {row["term"] for row in body["missing_topics"]}
    assert {"drone", "electric", "bike"} <= topics


def test_feedback_and_ingestion_failures(
    client: TestClient, rag_env: RagEnv, settings: Settings, activity: dict[str, Any]
) -> None:
    body = analytics(client, settings, rag_env.tenant.manager).json()
    feedback = body["feedback"]
    assert (feedback["helpful"], feedback["not_helpful"]) == (1, 1)
    recent = feedback["recent_not_helpful"][0]
    assert recent["note"] == "Call me on [phone], the door won't open" and recent["reasons"] == ["incomplete"]
    assert recent["question"] == "How do I clear a paper jam in the DemoPrinter-X2?"
    failure = next(f for f in body["ingestion_failures"] if f["title"] == "Scanned service form")
    assert (failure["error_code"], failure["failed_stage"]) == ("FILE_NO_TEXT", "parsing")
    assert failure["job_id"] == str(activity["upload"].job.id)


def test_range_label_and_timezone(client: TestClient, rag_env: RagEnv, settings: Settings) -> None:
    body = analytics(client, settings, rag_env.tenant.manager, start="2026-09-01", end="2026-09-30").json()
    assert body["range"]["timezone"] == rag_env.tenant.org.timezone
    assert (
        body["range"]["label"]
        == f"Showing 1{chr(0x2013)}30 Sep 2026 · store time ({rag_env.tenant.org.timezone})"
    )
    assert body["totals"]["questions"] == 0  # nothing happened in September
    today = utcnow().astimezone(ZoneInfo(rag_env.tenant.org.timezone)).date()
    default = analytics(client, settings, rag_env.tenant.manager).json()["range"]
    assert (date.fromisoformat(default["start"]), date.fromisoformat(default["end"])) == (
        today - timedelta(days=6),
        today,
    )


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"start": "2026-10-10", "end": "2026-10-01"}, "INVALID_RANGE"),
        ({"start": "2024-01-01", "end": "2026-01-01"}, "INVALID_RANGE"),
    ],
)
def test_bad_ranges_are_rejected(
    client: TestClient, rag_env: RagEnv, settings: Settings, params: dict[str, str], code: str
) -> None:
    response = analytics(client, settings, rag_env.tenant.manager, **params)
    assert response.status_code == 422 and response.json()["error"]["code"] == code


def test_only_managers_and_admins(client: TestClient, rag_env: RagEnv, settings: Settings) -> None:
    for principal in (rag_env.tenant.employee, rag_env.tenant.customer):
        response = analytics(client, settings, principal)
        assert (
            response.status_code == 403
            and response.json()["error"]["message"] == "Only managers and admins can see analytics."
        )
    anonymous = client.get("/api/v1/admin/analytics", headers={"X-Session-Ref": secrets.token_urlsafe(24)})
    assert anonymous.status_code == 401


@pytest.mark.usefixtures("activity")
def test_other_tenants_activity_is_never_counted(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    other = make_tenant(rag_env.ctx.engine)
    body = analytics(client, settings, other.admin).json()
    assert body["totals"]["questions"] == 0 and body["unanswered"] == [] and body["ingestion_failures"] == []
