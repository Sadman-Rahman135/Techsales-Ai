"""Questions about a customer's own orders and repairs, answered from their records (ADR-031)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.api.ratelimit import limiter, order_lookup_failures
from app.config import Settings, get_settings
from app.core.contact import contact_key
from app.core.time import utcnow
from app.db import models as m
from app.db.session import get_engine, tenant_session
from app.demo.orders import DEMO_ORDERS, seed_orders
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag import prompts
from app.rag.llm import FakeChatModel, get_chat_model
from app.rag.usage import TokenUsage
from tests.integration.conftest import RagEnv
from tests.integration.test_screens_api import ask, bearer, guest

pytestmark = pytest.mark.integration

ALEX, JORDAN = DEMO_ORDERS[0], DEMO_ORDERS[1]


@pytest.fixture
def settings(rag_env: RagEnv) -> Settings:
    return get_settings().model_copy(
        update={
            "public_organization": rag_env.tenant.org.slug,
            "rate_limit_anonymous_per_minute": 1000,
            "rate_limit_user_per_minute": 1000,
        }
    )


def serve(rag_env: RagEnv, settings: Settings, llm: Any) -> TestClient:
    app.dependency_overrides[get_engine] = lambda: rag_env.ctx.engine
    app.dependency_overrides[get_knowledge_index] = lambda: rag_env.ctx.index
    app.dependency_overrides[get_chat_model] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture
def client(rag_env: RagEnv, settings: Settings) -> Iterator[TestClient]:
    yield serve(rag_env, settings, FakeChatModel())
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seeded(rag_env: RagEnv) -> RagEnv:
    """Orders and repairs in the RAG tenant; CUST-00001 (TC-100001, RP-100001, RP-100004) is the customer."""
    org = rag_env.tenant.org.id
    with tenant_session(org, rag_env.ctx.engine) as session:
        seed_orders(session, org, contact_key(get_settings()))
        session.execute(
            update(m.Customer)
            .where(m.Customer.external_ref == "CUST-00001")
            .values(user_id=rag_env.tenant.customer.user_id)
        )
    return rag_env


@pytest.fixture(autouse=True)
def fresh_limits() -> Iterator[None]:
    limiter.reset()
    order_lookup_failures.reset()
    yield


def numbers(result: dict[str, Any]) -> set[str]:
    return {r.get("order_number") or r.get("reference") for r in result["records"]}


# ---------- signed-in customers ----------


def test_a_signed_in_customer_gets_answers_from_their_own_records(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    me = bearer(settings, seeded.tenant.customer)
    recent = ask(client, me, "what are my recent orders")
    assert (recent["next_action"], recent["answer_status"]) == ("show_records", "direct")
    assert numbers(recent) == {ALEX.order_number}  # the order card, from the database
    assert recent["citations"] == []

    fault = ask(client, me, "What was wrong with my laptop? What was the diagnosis?")
    assert "Faulty USB-C charging port" in fault["answer"]
    assert "RP-100001" in numbers(fault)

    count = ask(client, me, "How many products do I have in total?")
    assert "Items across these orders" in count["answer"] or "in total" in count["answer"]


def test_staff_and_other_customers_never_see_these_records(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    employee = ask(client, bearer(settings, seeded.tenant.employee), "what are my recent orders")
    assert employee["next_action"] == "verify_order" and employee["records"] == []
    stranger = ask(client, guest(), "Is my repair ready?")
    assert stranger["next_action"] == "verify_order" and stranger["answer"] == prompts.ACCOUNT_VERIFY


# ---------- guests: verify once, then ask in the same chat ----------


def test_a_guest_verifies_an_order_and_asks_follow_ups_about_it(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    me = guest()
    first = ask(client, me, f"Where is my order {JORDAN.order_number}?")
    assert first["next_action"] == "verify_order"
    conversation = first["conversation_id"]
    checked = client.post(
        "/api/v1/orders/lookup",
        json={"order_number": JORDAN.order_number, "contact": JORDAN.email, "conversation_id": conversation},
        headers=me,
    )
    assert checked.status_code == 200 and checked.json()["pickup_store"]

    pickup = ask(client, me, "When can I pick up my products?", conversation_id=conversation)
    assert pickup["next_action"] == "show_records" and numbers(pickup) == {JORDAN.order_number}
    follow_up = ask(client, me, "and what is in it?", conversation_id=conversation)  # a follow-up, no "order"
    assert follow_up["next_action"] == "show_records" and numbers(follow_up) == {JORDAN.order_number}

    # A new chat from the same browser starts over: the grant belongs to that conversation only.
    elsewhere = ask(client, me, "When can I pick up my products?")
    assert elsewhere["next_action"] == "verify_order"


def test_a_grant_ends_when_it_expires_and_never_crosses_to_another_persons_chat(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    me = guest()
    conversation = ask(client, me, f"Where is my order {JORDAN.order_number}?")["conversation_id"]
    # Verifying with someone else's conversation ID works, but remembers nothing.
    someone = guest()
    theirs = ask(client, someone, "Where is my order?")["conversation_id"]
    client.post(
        "/api/v1/orders/lookup",
        json={"order_number": JORDAN.order_number, "contact": JORDAN.email, "conversation_id": theirs},
        headers=me,
    )
    with tenant_session(seeded.tenant.org.id, seeded.ctx.engine) as session:
        granted = session.scalar(
            select(func.count())
            .select_from(m.AccountGrant)
            .where(m.AccountGrant.conversation_id == uuid.UUID(theirs))
        )
    assert granted == 0
    client.post(
        "/api/v1/orders/lookup",
        json={"order_number": JORDAN.order_number, "contact": JORDAN.email, "conversation_id": conversation},
        headers=me,
    )
    assert ask(client, me, "When can I pick up my products?", conversation_id=conversation)["records"]
    with tenant_session(seeded.tenant.org.id, seeded.ctx.engine) as session:
        session.execute(
            update(m.AccountGrant)
            .where(m.AccountGrant.conversation_id == uuid.UUID(conversation))
            .values(expires_at=utcnow())
        )
    expired = ask(client, me, "When can I pick up my products?", conversation_id=conversation)
    assert expired["next_action"] == "verify_order" and expired["records"] == []


# ---------- the model can't invent ----------


class InventingModel(FakeChatModel):
    def answer_from_records(self, question: str, facts: str, *, usage: TokenUsage | None = None) -> str:
        return "Your order TC-100001 will arrive on 31 December 2099 and cost $5.00."


def test_an_answer_with_facts_not_in_the_records_is_replaced_by_the_records_summary(
    seeded: RagEnv, settings: Settings
) -> None:
    client = serve(seeded, settings, InventingModel())
    try:
        result = ask(client, bearer(settings, seeded.tenant.customer), "When will my order arrive?")
    finally:
        app.dependency_overrides.clear()
    assert "2099" not in result["answer"] and result["answer"].startswith("Order TC-100001 is on its way")
    assert result["next_action"] == "show_records"


# ---------- repairs API ----------


def test_repairs_verify_like_orders_and_customers_see_their_own(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    me = guest()
    wrong = client.post(
        "/api/v1/repairs/lookup", json={"reference": "RP-100002", "contact": ALEX.email}, headers=me
    )
    assert wrong.status_code == 404 and wrong.json()["error"]["code"] == "REPAIR_NOT_VERIFIED"
    right = client.post(
        "/api/v1/repairs/lookup", json={"reference": "rp 100002", "contact": JORDAN.email}, headers=me
    )
    assert right.status_code == 200 and right.json()["status"] == "diagnosing"
    mine = client.get("/api/v1/repairs/mine", headers=bearer(settings, seeded.tenant.customer)).json()
    assert [r["reference"] for r in mine] == ["RP-100001", "RP-100004"]


class RecordingModel(FakeChatModel):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []

    def answer_from_records(self, question: str, facts: str, *, usage: TokenUsage | None = None) -> str:
        self.asked.append(question)
        return super().answer_from_records(question, facts, usage=usage)


def test_only_real_follow_ups_carry_the_earlier_question(seeded: RagEnv, settings: Settings) -> None:
    model = RecordingModel()
    client = serve(seeded, settings, model)
    me = bearer(settings, seeded.tenant.customer)
    try:
        conversation = ask(client, me, "When can I pick up my products?")["conversation_id"]
        ask(client, me, "how many products do I have", conversation_id=conversation)  # its own question
        ask(client, me, "and how much did it cost?", conversation_id=conversation)  # leans on the last one
    finally:
        app.dependency_overrides.clear()
    assert model.asked[1] == "how many products do I have"
    assert model.asked[2].startswith("and how much did it cost?")
    assert "Earlier question, for context only: how many products do I have" in model.asked[2]


def test_record_questions_the_documents_cant_answer_fall_back_to_the_records(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    question = "total cost of the orders that are ready for pickup"
    mine = ask(client, bearer(settings, seeded.tenant.customer), question)
    assert (mine["answer_status"], mine["next_action"]) == ("direct", "show_records")
    assert mine["records"]
    # Without records (a guest who verified nothing) it stays "not in our documents", not a verification card.
    stranger = ask(client, guest(), question)
    assert (stranger["answer_status"], stranger["records"]) == ("insufficient_evidence", [])
    # A policy question the documents answer stays a documents answer, even with "items" in it.
    policy = ask(
        client, bearer(settings, seeded.tenant.customer), "What is the restocking fee for opened items?"
    )
    assert policy["answer_status"] == "answered" and policy["citations"]
