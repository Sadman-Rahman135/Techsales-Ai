"""Blueprint §7.1 and §9 over HTTP: auth, upload contract (202 + job), §9.3 errors, roles, isolation."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import hash_password
from app.db import models as m
from app.db.session import get_engine, tenant_session
from app.ingestion import worker as w
from app.ingestion.scanning import EICAR, BuiltinScanner, get_scanner
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.services.storage import get_storage
from tests.builders import pdf_bytes, warranty_pdf
from tests.integration.conftest import Tenant, make_tenant

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple"
PDF = "application/pdf"


@pytest.fixture(scope="module")
def hashed() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def api_tenant(app_engine: Engine, hashed: str) -> Tenant:
    return make_tenant(app_engine, password_hash=hashed)


@pytest.fixture
def client(worker: w.WorkerContext) -> Iterator[TestClient]:
    app.dependency_overrides[get_engine] = lambda: worker.engine
    app.dependency_overrides[get_knowledge_index] = lambda: worker.index
    app.dependency_overrides[get_storage] = lambda: worker.storage
    app.dependency_overrides[get_scanner] = BuiltinScanner
    app.dependency_overrides[get_settings] = lambda: worker.settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def as_role(client: TestClient, tenant: Tenant, role: m.Role) -> dict[str, str]:
    return login(client, tenant.emails[role])


def send(
    client: TestClient,
    headers: dict[str, str],
    data: bytes,
    name: str = "warranty.pdf",
    mime: str = PDF,
    **form: Any,
) -> Any:
    fields = {"audience": "public"} | {k: str(v) for k, v in form.items()}
    return client.post("/api/v1/documents", headers=headers, files={"file": (name, data, mime)}, data=fields)


def error(response: Any) -> dict[str, Any]:
    body = response.json()["error"]
    assert body["request_id"] == response.headers["X-Request-ID"]
    return dict(body)


# ---------- auth ----------


def test_login_returns_a_token_and_me_reports_the_role(client: TestClient, api_tenant: Tenant) -> None:
    headers = as_role(client, api_tenant, m.Role.MANAGER)
    me = client.get("/api/v1/me", headers=headers).json()
    assert me["role"] == "manager"
    assert me["organization"]["id"] == str(api_tenant.org.id)


@pytest.mark.parametrize("password", ["wrong-password", ""])
def test_bad_credentials_get_one_uniform_answer(
    client: TestClient, api_tenant: Tenant, password: str
) -> None:
    wrong = client.post(
        "/api/v1/auth/login", json={"email": api_tenant.emails[m.Role.ADMIN], "password": password}
    )
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@techcare.example", "password": PASSWORD}
    )
    if password:
        assert wrong.status_code == unknown.status_code == 401
        assert error(wrong)["code"] == error(unknown)["code"] == "INVALID_CREDENTIALS"
    else:
        assert wrong.status_code == 422  # empty password: validation, never a lookup


def test_requests_without_a_valid_token_are_rejected(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer not-a-token"}):
        response = client.get("/api/v1/documents", headers=headers)
        assert response.status_code == 401
        assert error(response)["code"] == "UNAUTHENTICATED"
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_role_changes_apply_immediately_not_at_token_expiry(
    client: TestClient, api_tenant: Tenant, app_engine: Engine
) -> None:
    headers = as_role(client, api_tenant, m.Role.MANAGER)
    assert client.get("/api/v1/documents", headers=headers).status_code == 200
    with Session(app_engine) as session, session.begin():
        membership = session.scalar(
            select(m.Membership).where(m.Membership.user_id == api_tenant.manager.user_id)
        )
        assert membership is not None
        membership.role = m.Role.EMPLOYEE.value
    assert send(client, headers, warranty_pdf()).status_code == 403
    with Session(app_engine) as session, session.begin():
        user = session.get_one(m.User, api_tenant.manager.user_id)
        user.status = m.UserStatus.DISABLED.value
    assert client.get("/api/v1/documents", headers=headers).status_code == 401


# ---------- upload ----------


def test_upload_returns_202_with_the_job_then_reports_progress(
    client: TestClient, api_tenant: Tenant, worker: w.WorkerContext
) -> None:
    headers = as_role(client, api_tenant, m.Role.MANAGER)
    response = send(client, headers, warranty_pdf(), title="Laptop warranty", product_model="TC-Book 14")
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["version_number"] == 1
    assert (accepted["job"]["state"], accepted["job"]["stage"]) == ("queued", "uploaded")

    job_url = f"/api/v1/ingestion-jobs/{accepted['job']['id']}"
    w.run_once(worker)
    job = client.get(job_url, headers=headers).json()
    assert (job["state"], job["stage"], job["error_code"]) == ("done", "ready", None)

    listing = client.get("/api/v1/documents", headers=headers).json()
    row = next(item for item in listing["items"] if item["id"] == accepted["document_id"])
    assert (row["title"], row["status"], row["product_model"]) == ("Laptop warranty", "ready", "TC-Book 14")

    publish = client.post(
        f"/api/v1/documents/{accepted['document_id']}/publish",
        headers=headers,
        json={"version_id": accepted["version_id"]},
    )
    assert publish.status_code == 200, publish.text
    assert (publish.json()["status"], publish.json()["active_version_number"]) == ("published", 1)


def test_upload_requires_a_manager_or_admin(client: TestClient, api_tenant: Tenant) -> None:
    for role in (m.Role.EMPLOYEE, m.Role.CUSTOMER):
        response = send(client, as_role(client, api_tenant, role), warranty_pdf())
        assert response.status_code == 403
        assert error(response)["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    ("name", "data", "mime", "status", "code"),
    [
        ("tool.exe", b"MZ" + b"\x00" * 50, "application/octet-stream", 422, "FILE_TYPE_NOT_ALLOWED"),
        ("fake.pdf", b"not a pdf at all", PDF, 422, "FILE_TYPE_MISMATCH"),
        ("empty.pdf", b"", PDF, 422, "FILE_EMPTY"),
        ("locked.pdf", pdf_bytes([[("p", "x")]], password="hunter2-hunter2"), PDF, 422, "FILE_ENCRYPTED"),
        ("eicar.txt", EICAR, "text/plain", 422, "FILE_MALWARE_DETECTED"),
    ],
    ids=["executable", "mismatch", "empty", "encrypted", "malware"],
)
def test_rejected_uploads_get_a_stable_code_and_a_friendly_message(
    client: TestClient, api_tenant: Tenant, name: str, data: bytes, mime: str, status: int, code: str
) -> None:
    response = send(client, as_role(client, api_tenant, m.Role.ADMIN), data, name, mime)
    assert response.status_code == status
    body = error(response)
    assert body["code"] == code
    assert body["message"] and code not in body["message"]  # human copy, not the code


def test_malware_is_blocked_and_audited_but_never_stored(
    client: TestClient, api_tenant: Tenant, worker: w.WorkerContext
) -> None:
    send(client, as_role(client, api_tenant, m.Role.ADMIN), b"note " + EICAR, "note.txt", "text/plain")
    assert list(worker.storage.list(f"{api_tenant.org.id}/")) == []
    with tenant_session(api_tenant.org.id, worker.engine) as session:
        actions = session.scalars(select(m.AuditEvent.action)).all()
        versions = session.scalars(select(m.DocumentVersion.id)).all()
    assert "document.upload.quarantined" in actions
    assert versions == []


def test_too_large_uploads_get_413(client: TestClient, api_tenant: Tenant, worker: w.WorkerContext) -> None:
    data = warranty_pdf()
    worker.settings = worker.settings.model_copy(update={"max_upload_bytes": len(data) - 1})
    response = send(client, as_role(client, api_tenant, m.Role.ADMIN), data)
    assert response.status_code == 413
    assert error(response)["code"] == "FILE_TOO_LARGE"


def test_duplicate_upload_warns_and_can_be_confirmed(client: TestClient, api_tenant: Tenant) -> None:
    headers = as_role(client, api_tenant, m.Role.ADMIN)
    data = pdf_bytes([[("p", f"Unique {uuid.uuid4()}")]])
    first = send(client, headers, data, title="Original").json()
    again = send(client, headers, data)
    assert again.status_code == 409
    assert error(again)["details"] == {
        "document_id": first["document_id"],
        "title": "Original",
        "version_number": 1,
    }
    assert send(client, headers, data, allow_duplicate="true").status_code == 202


def test_a_new_document_named_like_a_live_one_asks_first(client: TestClient, api_tenant: Tenant) -> None:
    headers = as_role(client, api_tenant, m.Role.MANAGER)
    title = f"Engraving Service {uuid.uuid4().hex[:6]}"
    first = send(client, headers, pdf_bytes([[("p", "Engraving costs 15 dollars.")]]), title=title).json()
    update = pdf_bytes([[("p", "Engraving is free for loyalty members.")]])
    clash = send(client, headers, update, title=title.upper())  # case doesn't matter
    assert clash.status_code == 409
    body = error(clash)
    assert body["code"] == "DOCUMENT_TITLE_EXISTS"
    assert body["details"] == {"document_id": first["document_id"], "title": title, "next_version": 2}
    assert "version 2" in body["message"]
    # Nothing was created by the refused upload; confirming makes a separate document.
    separate = send(client, headers, update, title=title, allow_same_title="true")
    assert separate.status_code == 202 and separate.json()["document_id"] != first["document_id"]


def test_new_versions_upload_to_the_same_document(client: TestClient, api_tenant: Tenant) -> None:
    headers = as_role(client, api_tenant, m.Role.MANAGER)
    first = send(client, headers, pdf_bytes([[("p", "v1")]])).json()
    response = client.post(
        f"/api/v1/documents/{first['document_id']}/versions",
        headers=headers,
        files={"file": ("v2.pdf", pdf_bytes([[("p", "v2")]]), PDF)},
    )
    assert response.status_code == 202
    assert response.json()["version_number"] == 2


def test_validation_errors_use_the_shared_shape(client: TestClient, api_tenant: Tenant) -> None:
    headers = as_role(client, api_tenant, m.Role.MANAGER)
    response = client.post("/api/v1/documents", headers=headers, files={"file": ("a.pdf", b"x", PDF)})
    assert response.status_code == 422
    body = error(response)
    assert body["code"] == "VALIDATION_ERROR"
    assert {"field": "audience", "message": "Field required"} in body["details"]["fields"]


def test_request_ids_from_the_bff_are_echoed(client: TestClient) -> None:
    response = client.get("/api/v1/documents", headers={"X-Request-ID": "req-bff-12345678"})
    assert response.headers["X-Request-ID"] == "req-bff-12345678"
    assert response.json()["error"]["request_id"] == "req-bff-12345678"


# ---------- isolation and role filtering ----------


def test_other_tenants_documents_and_jobs_are_invisible(
    client: TestClient, api_tenant: Tenant, app_engine: Engine, hashed: str
) -> None:
    mine = send(client, as_role(client, api_tenant, m.Role.MANAGER), warranty_pdf()).json()
    other = make_tenant(app_engine, password_hash=hashed)
    headers = as_role(client, other, m.Role.ADMIN)
    assert client.get(f"/api/v1/documents/{mine['document_id']}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/ingestion-jobs/{mine['job']['id']}", headers=headers).status_code == 404
    assert (
        client.post(f"/api/v1/ingestion-jobs/{mine['job']['id']}/cancel", headers=headers).status_code == 404
    )
    assert client.get("/api/v1/documents", headers=headers).json()["total"] == 0


def test_employees_see_only_published_documents_without_processing_details(
    client: TestClient, api_tenant: Tenant, worker: w.WorkerContext
) -> None:
    manager = as_role(client, api_tenant, m.Role.MANAGER)
    draft = send(client, manager, pdf_bytes([[("p", "Draft policy")]]), title="Draft").json()
    live = send(
        client, manager, pdf_bytes([[("p", "Live policy")]]), title="Live", audience="employee"
    ).json()
    w.run_once(worker)
    w.run_once(worker)
    client.post(
        f"/api/v1/documents/{live['document_id']}/publish",
        headers=manager,
        json={"version_id": live["version_id"]},
    )

    employee = as_role(client, api_tenant, m.Role.EMPLOYEE)
    items = client.get("/api/v1/documents", headers=employee).json()["items"]
    assert [i["title"] for i in items] == ["Live"]
    assert items[0]["latest_version"]["job"] is None
    assert client.get(f"/api/v1/documents/{draft['document_id']}", headers=employee).status_code == 404
    assert client.get(f"/api/v1/ingestion-jobs/{live['job']['id']}", headers=employee).status_code == 403

    customer = as_role(client, api_tenant, m.Role.CUSTOMER)
    assert client.get("/api/v1/documents", headers=customer).json()["items"] == []  # employee-only document


def test_list_supports_search_filter_and_paging(client: TestClient, api_tenant: Tenant) -> None:
    headers = as_role(client, api_tenant, m.Role.ADMIN)
    for title in ("Laptop warranty", "Phone warranty", "Store hours"):
        send(client, headers, pdf_bytes([[("p", f"{title} {uuid.uuid4()}")]]), title=title)
    found = client.get("/api/v1/documents", headers=headers, params={"q": "warranty", "sort": "title"}).json()
    assert [i["title"] for i in found["items"]] == ["Laptop warranty", "Phone warranty"]
    page = client.get(
        "/api/v1/documents", headers=headers, params={"limit": 1, "offset": 1, "sort": "title"}
    ).json()
    assert (page["total"], [i["title"] for i in page["items"]]) == (3, ["Phone warranty"])
    assert client.get("/api/v1/documents", headers=headers, params={"q": "100%_"}).json()["total"] == 0


# ---------- job control and delete ----------


def test_failed_job_shows_the_step_and_can_be_retried(
    client: TestClient, api_tenant: Tenant, worker: w.WorkerContext
) -> None:
    from tests.builders import scanned_pdf_bytes

    headers = as_role(client, api_tenant, m.Role.MANAGER)
    accepted = send(client, headers, scanned_pdf_bytes(), "scan.pdf").json()
    w.run_once(worker)
    job = client.get(f"/api/v1/ingestion-jobs/{accepted['job']['id']}", headers=headers).json()
    assert (job["state"], job["stage"], job["error_code"]) == ("failed", "parsing", "FILE_NO_TEXT")
    assert "scanned" in job["error_message"]
    retried = client.post(f"/api/v1/ingestion-jobs/{accepted['job']['id']}/retry", headers=headers)
    assert retried.status_code == 202
    assert retried.json()["state"] == "queued"
    again = client.post(f"/api/v1/ingestion-jobs/{accepted['job']['id']}/retry", headers=headers)
    assert again.status_code == 409


def test_delete_is_admin_only_and_hides_the_document(
    client: TestClient, api_tenant: Tenant, worker: w.WorkerContext
) -> None:
    manager = as_role(client, api_tenant, m.Role.MANAGER)
    accepted = send(client, manager, warranty_pdf()).json()
    url = f"/api/v1/documents/{accepted['document_id']}"
    assert client.delete(url, headers=manager).status_code == 403
    admin = as_role(client, api_tenant, m.Role.ADMIN)
    assert client.delete(url, headers=admin).status_code == 204
    assert client.get(url, headers=admin).status_code == 404
    assert w.run_once(worker) is None  # its queued job was cancelled


# ---------- search metadata (§2.2 P1, ADR-028) ----------


def test_metadata_is_set_at_upload_edited_with_an_audit_and_searchable(
    client: TestClient, api_tenant: Tenant, app_engine: Engine
) -> None:
    headers = as_role(client, api_tenant, m.Role.MANAGER)
    tag = uuid.uuid4().hex[:6]
    created = send(
        client,
        headers,
        pdf_bytes([[("p", f"Printer guide {tag}")]]),
        title=f"Printer guide {tag}",
        brand="DemoBrand",
        department="Repairs",
        effective_date="2026-09-01",
        category="Manuals",
    ).json()
    doc = client.get(f"/api/v1/documents/{created['document_id']}", headers=headers).json()
    assert (doc["brand"], doc["department"], doc["language"], doc["effective_date"]) == (
        "DemoBrand",
        "Repairs",
        "en",
        "2026-09-01",
    )

    changed = client.patch(
        f"/api/v1/documents/{created['document_id']}",
        headers=headers,
        json={"department": "Service desk", "tags": ["Printers", "printers", "setup"], "brand": None},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["department"], body["brand"], body["tags"]) == ("Service desk", None, ["printers", "setup"])
    with tenant_session(api_tenant.org.id, app_engine) as session:
        event = session.scalar(
            select(m.AuditEvent).where(
                m.AuditEvent.target_id == uuid.UUID(created["document_id"]),
                m.AuditEvent.action == "document.metadata_updated",
            )
        )
    assert event is not None and event.details["department"] == ["Repairs", "Service desk"]

    found = client.get(
        "/api/v1/documents",
        headers=headers,
        params={"department": "service desk", "effective_from": "2026-08-01", "effective_to": "2026-09-30"},
    ).json()
    assert [d["id"] for d in found["items"]] == [created["document_id"]]
    assert client.get("/api/v1/documents", headers=headers, params={"q": "service desk"}).json()["total"] >= 1
    facets = client.get("/api/v1/documents/facets", headers=headers).json()
    assert "Service desk" in facets["departments"] and "Manuals" in facets["categories"]


def test_only_managers_edit_metadata_and_index_fields_stay_upload_only(
    client: TestClient, api_tenant: Tenant, app_engine: Engine, hashed: str
) -> None:
    manager = as_role(client, api_tenant, m.Role.MANAGER)
    created = send(client, manager, pdf_bytes([[("p", f"Doc {uuid.uuid4()}")]])).json()
    url = f"/api/v1/documents/{created['document_id']}"
    employee = as_role(client, api_tenant, m.Role.EMPLOYEE)
    assert client.patch(url, headers=employee, json={"brand": "X"}).status_code == 403
    # Product model and audience are copied into the index, so they can't be changed here.
    assert client.patch(url, headers=manager, json={"product_model": "X"}).status_code == 422
    assert client.patch(url, headers=manager, json={"audience": "employee"}).status_code == 422
    assert client.patch(url, headers=manager, json={"language": "english"}).status_code == 422
    other = make_tenant(app_engine, password_hash=hashed)
    stranger = as_role(client, other, m.Role.MANAGER)
    assert client.patch(url, headers=stranger, json={"brand": "X"}).status_code == 404  # another tenant's
