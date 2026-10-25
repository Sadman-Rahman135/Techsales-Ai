"""US-05 order status after verification, and the staff ticket queue (ADR-029)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update

from app.api.ratelimit import limiter, order_lookup_failures
from app.config import Settings, get_settings
from app.core.contact import contact_key
from app.db import models as m
from app.db.session import get_engine, tenant_session
from app.demo.orders import DEMO_ORDERS, seed_orders
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag import prompts
from app.rag.llm import FakeChatModel, get_chat_model
from tests.integration.conftest import RagEnv, make_tenant
from tests.integration.test_screens_api import ask, bearer, guest

pytestmark = pytest.mark.integration

SHIPPED = DEMO_ORDERS[0]  # TC-100001


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
    app.dependency_overrides[get_chat_model] = FakeChatModel
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seeded(rag_env: RagEnv) -> RagEnv:
    """The fictional catalogue and orders in the RAG tenant; CUST-00001 belongs to the tenant's customer."""
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


def lookup(client: TestClient, headers: dict[str, str], number: str, contact: str) -> Any:
    return client.post(
        "/api/v1/orders/lookup", json={"order_number": number, "contact": contact}, headers=headers
    )


# ---------- orders ----------


def test_an_order_question_shows_the_verification_card(client: TestClient, seeded: RagEnv) -> None:
    result = ask(client, guest(), "Where is my order TC-100001?")
    assert result["answer"] == prompts.ORDER_VERIFY and result["next_action"] == "verify_order"
    assert result["citations"] == []  # nothing from documents, nothing from the order


def test_the_right_email_or_phone_shows_the_order(client: TestClient, seeded: RagEnv) -> None:
    headers = guest()
    by_email = lookup(client, headers, "tc 100001", " Alex.Customer@TechCare.example ")  # normalized
    assert by_email.status_code == 200, by_email.text
    order = by_email.json()
    assert (order["order_number"], order["status"]) == (SHIPPED.order_number, "shipped")
    assert order["carrier"] and order["tracking_number"] and order["lines"]
    assert lookup(client, headers, "TC-100001", "+1 555 010 0001").status_code == 200  # the same phone


def test_a_failed_check_never_says_whether_the_order_exists(client: TestClient, seeded: RagEnv) -> None:
    headers = guest()
    wrong_contact = lookup(client, headers, "TC-100001", "someone.else@example.com")
    no_such_order = lookup(client, headers, "TC-999999", SHIPPED.email)
    assert wrong_contact.status_code == no_such_order.status_code == 404
    assert wrong_contact.json()["error"]["message"] == no_such_order.json()["error"]["message"]
    assert wrong_contact.json()["error"]["code"] == "ORDER_NOT_VERIFIED"
    with tenant_session(seeded.tenant.org.id, seeded.ctx.engine) as session:
        actions = list(
            session.scalars(select(m.AuditEvent.action).where(m.AuditEvent.action.like("order.lookup.%")))
        )
    assert "order.lookup.failed" in actions


def test_repeated_failures_lock_the_caller_and_the_order_number(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    headers = guest()
    for _ in range(settings.order_lookup_max_failures):
        assert lookup(client, headers, "TC-100002", "guess@example.com").status_code == 404
    locked = lookup(client, headers, "TC-100002", DEMO_ORDERS[1].email)  # even the right email now waits
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "ORDER_LOOKUP_LOCKED"
    # The order number is locked for everyone, so rotating sessions doesn't help a guesser.
    assert lookup(client, guest(), "TC-100002", DEMO_ORDERS[1].email).status_code == 429


def test_another_tenant_cannot_verify_this_tenants_order(
    client: TestClient, seeded: RagEnv, settings: Settings, app_engine: Engine
) -> None:
    other = make_tenant(app_engine)
    assert lookup(client, bearer(settings, other.customer), "TC-100001", SHIPPED.email).status_code == 404


def test_a_signed_in_customer_sees_only_their_own_orders(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    mine = client.get("/api/v1/orders/mine", headers=bearer(settings, seeded.tenant.customer)).json()
    assert [o["order_number"] for o in mine] == [SHIPPED.order_number]
    assert client.get("/api/v1/orders/mine", headers=bearer(settings, seeded.tenant.employee)).json() == []


def test_no_contact_detail_is_stored_readably(seeded: RagEnv) -> None:
    with tenant_session(seeded.tenant.org.id, seeded.ctx.engine) as session:
        rows = session.execute(select(m.Customer.email_hash, m.Customer.phone_hash)).all()
    values = [v for row in rows for v in row if v]
    assert values and all(len(v) == 64 and "@" not in v for v in values)


# ---------- tickets ----------


def conversation_of(client: TestClient, settings: Settings, principal: Any) -> str:
    result = ask(client, bearer(settings, principal), "What does error code E-04 mean on the DemoPrinter-X2?")
    return str(result["conversation_id"])


def test_an_employee_drafts_reviews_and_files_a_ticket(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    employee = bearer(settings, seeded.tenant.employee)
    conversation = conversation_of(client, settings, seeded.tenant.employee)
    draft = client.post(
        "/api/v1/tickets/draft", json={"conversation_id": conversation}, headers=employee
    ).json()
    assert draft["subject"].startswith("What does error code E-04") and "Needs:" in draft["summary"]
    created = client.post(
        "/api/v1/tickets",
        json={
            "subject": draft["subject"],
            "summary": draft["summary"] + "\nCustomer will call back.",  # the employee edits before saving
            "priority": "high",
            "conversation_id": conversation,
            "order_number": "tc100001",
            "summary_drafted": True,
        },
        headers=employee,
    )
    assert created.status_code == 201, created.text
    ticket = created.json()
    assert ticket["reference"].startswith("T-") and ticket["status"] == "open"
    assert (ticket["order_number"], ticket["customer_ref"]) == ("TC-100001", "CUST-00001")
    queue = client.get("/api/v1/tickets", headers=employee, params={"status": "open"}).json()
    assert ticket["id"] in [t["id"] for t in queue["items"]] and queue["counts"]["open"] >= 1


def test_staff_work_the_queue_and_every_change_is_audited(
    client: TestClient, seeded: RagEnv, settings: Settings
) -> None:
    manager = bearer(settings, seeded.tenant.manager)
    ticket = client.post(
        "/api/v1/tickets",
        json={"subject": "Printer E-04", "summary": "Needs a toner check."},
        headers=manager,
    ).json()
    url = f"/api/v1/tickets/{ticket['id']}"
    staff = client.get("/api/v1/tickets/staff", headers=manager).json()
    employee = next(s for s in staff if s["user_id"] == str(seeded.tenant.employee.user_id))
    assigned = client.patch(
        url, json={"assigned_to": employee["user_id"], "status": "in_progress"}, headers=manager
    )
    assert assigned.status_code == 200 and assigned.json()["assignee"] == employee["name"]
    customer_user = str(seeded.tenant.customer.user_id)
    assert (
        client.patch(url, json={"assigned_to": customer_user}, headers=manager).status_code == 422
    )  # not staff
    assert (
        client.post(f"{url}/notes", json={"body": "Called the customer."}, headers=manager).status_code == 201
    )
    resolved = client.patch(url, json={"status": "resolved"}, headers=manager).json()
    assert resolved["status"] == "resolved" and resolved["resolved_at"]
    detail = client.get(url, headers=manager).json()
    assert [n["body"] for n in detail["notes"]] == ["Called the customer."]
    reopened = client.patch(url, json={"status": "open"}, headers=manager).json()
    assert reopened["resolved_at"] is None
    with tenant_session(seeded.tenant.org.id, seeded.ctx.engine) as session:
        actions = list(
            session.scalars(
                select(m.AuditEvent.action).where(m.AuditEvent.target_id == uuid.UUID(ticket["id"]))
            )
        )
    assert {"ticket.created", "ticket.updated", "ticket.note_added"} <= set(actions)


def test_tickets_are_staff_only_and_drafts_only_from_your_own_conversation(
    client: TestClient, seeded: RagEnv, settings: Settings, app_engine: Engine
) -> None:
    customer = bearer(settings, seeded.tenant.customer)
    assert client.get("/api/v1/tickets", headers=customer).status_code == 403
    assert (
        client.post("/api/v1/tickets", json={"subject": "x", "summary": "y"}, headers=customer).status_code
        == 403
    )
    assert client.get("/api/v1/tickets", headers=guest()).status_code == 401
    # The manager can't draft from the employee's conversation: it isn't theirs.
    conversation = conversation_of(client, settings, seeded.tenant.employee)
    manager = bearer(settings, seeded.tenant.manager)
    response = client.post("/api/v1/tickets/draft", json={"conversation_id": conversation}, headers=manager)
    assert response.status_code == 404
    # Another tenant's staff never see this tenant's tickets.
    other = make_tenant(app_engine)
    page = client.get("/api/v1/tickets", headers=bearer(settings, other.manager)).json()
    assert page["total"] == 0
