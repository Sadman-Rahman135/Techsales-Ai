"""Blueprint §9 over HTTP: conventions on every route, the role matrix, authorized short-lived previews."""

import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.ratelimit import limiter
from app.config import Settings, get_settings
from app.core import links
from app.core.permissions import Principal
from app.core.security import issue_token
from app.db.session import get_engine, tenant_session
from app.demo.builders import pdf_bytes
from app.demo.corpus import _wait_for
from app.ingestion.embeddings import get_embedder
from app.ingestion.validation import DOCX_MIME, validate_upload
from app.integrations.chroma import get_knowledge_index
from app.main import app
from app.rag.llm import FakeChatModel, get_chat_model
from app.services import documents as docs
from app.services.storage import get_storage
from app.services.versions import soft_delete_document
from tests.integration.conftest import RagEnv

pytestmark = pytest.mark.integration

PUBLIC_OPERATIONS = {
    ("get", "/api/v1/health"),
    ("get", "/api/v1/health/live"),
    ("post", "/api/v1/auth/login"),
    ("get", "/api/v1/files/{token}"),
}


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
    app.dependency_overrides[get_storage] = lambda: rag_env.ctx.storage
    app.dependency_overrides[get_chat_model] = FakeChatModel
    app.dependency_overrides[get_settings] = lambda: settings
    limiter.reset()
    yield TestClient(app, client=("127.0.0.1", 50000))  # inside INTERNAL_API_ALLOWED_NETWORKS
    app.dependency_overrides.clear()


def bearer(settings: Settings, principal: Principal) -> dict[str, str]:
    assert principal.user_id is not None
    return {
        "Authorization": f"Bearer {issue_token(settings, principal.user_id, principal.organization_id)[0]}"
    }


def session() -> dict[str, str]:
    return {"X-Session-Ref": secrets.token_urlsafe(24)}


def operations() -> list[tuple[str, str]]:
    return [(method, path) for path, ops in app.openapi()["paths"].items() for method in ops]


def concrete(path: str) -> str:
    return path.replace("{token}", "not-a-token").replace("{", "").replace("}", "")


def fill(path: str) -> str:
    return "/".join(str(uuid.uuid4()) if part.startswith("{") else part for part in path.split("/"))


# ---------- conventions (§9 intro, §9.3) ----------


def test_every_route_is_versioned_and_documents_the_error_shape() -> None:
    for method, path in operations():
        assert path.startswith("/api/v1/"), path
        if path.startswith("/api/v1/health"):
            continue  # readiness: 503 carries the health report itself; liveness has no errors
        responses = app.openapi()["paths"][path][method]["responses"]
        for code in ("401", "403", "404", "422"):
            schema = responses[code]["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, (method, path, code)


@pytest.mark.parametrize(("method", "path"), operations())
def test_every_protected_route_refuses_anonymous_callers(client: TestClient, method: str, path: str) -> None:
    if (method, path) in PUBLIC_OPERATIONS:
        return
    response = client.request(method.upper(), fill(path), json={})
    assert response.status_code == 401, (method, path, response.text)
    body = response.json()["error"]
    assert body["code"] == "UNAUTHENTICATED" and body["request_id"] == response.headers["X-Request-ID"]


def test_malformed_json_is_400_and_invalid_json_is_422(client: TestClient) -> None:
    malformed = client.post(
        "/api/v1/chat/ask",
        content=b'{"question": ',
        headers={**session(), "Content-Type": "application/json"},
    )
    assert malformed.status_code == 400 and malformed.json()["error"]["code"] == "MALFORMED_REQUEST"
    invalid = client.post("/api/v1/chat/ask", json={"question": 42}, headers=session())
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------- role matrix (product-vision.md §3; enforced in every handler) ----------

ROLES = ("admin", "manager", "employee", "customer")
MATRIX: list[tuple[str, str, set[str]]] = [  # (method, path template, roles allowed)
    ("get", "/api/v1/documents", set(ROLES)),
    ("post", "/api/v1/documents", {"admin", "manager"}),
    ("post", "/api/v1/documents/{doc}/versions", {"admin", "manager"}),
    ("post", "/api/v1/documents/{doc}/publish", {"admin", "manager"}),
    ("delete", "/api/v1/documents/{doc}", {"admin"}),
    ("get", "/api/v1/ingestion-jobs/{job}", {"admin", "manager"}),
    ("post", "/api/v1/ingestion-jobs/{job}/cancel", {"admin", "manager"}),
    ("post", "/api/v1/ingestion-jobs/{job}/retry", {"admin", "manager"}),
    ("get", "/api/v1/admin/analytics", {"admin", "manager"}),
    ("post", "/api/v1/chat/ask", set(ROLES)),
    ("get", "/api/v1/conversations", set(ROLES)),
    ("delete", "/api/v1/conversations", set(ROLES)),
    ("delete", "/api/v1/conversations/{doc}", set(ROLES)),
    ("get", "/api/v1/documents/facets", set(ROLES)),
    ("get", "/api/v1/documents/{doc}/versions/{job}/chunks", {"admin", "manager"}),
    ("get", "/api/v1/documents/{doc}/activity", {"admin", "manager"}),
]


@pytest.mark.parametrize(("method", "template", "allowed"), MATRIX)
def test_role_matrix(
    client: TestClient, rag_env: RagEnv, settings: Settings, method: str, template: str, allowed: set[str]
) -> None:
    path = template.replace("{doc}", str(uuid.uuid4())).replace("{job}", str(uuid.uuid4()))
    body: dict[str, Any] = {"question": "hi"} if path.endswith("/chat/ask") else {}
    for role in ROLES:
        principal: Principal = getattr(rag_env.tenant, role)
        status = client.request(
            method.upper(), path, headers=bearer(settings, principal), json=body
        ).status_code
        if role in allowed:
            assert status not in (401, 403), (role, method, path, status)  # may be 404/422 for the dummy IDs
        else:
            assert status == 403, (role, method, path, status)


def test_user_tokens_cannot_call_the_service_api(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    body = {"question": "hi", "conversation_id": "call-1"}
    for role in ROLES:
        headers = bearer(settings, getattr(rag_env.tenant, role))
        assert client.post("/api/v1/internal/knowledge/answer", json=body, headers=headers).status_code == 401


# ---------- preview (§9, §9.1 preview_url, §11.1 short-lived scoped links) ----------


def test_citation_preview_links_open_the_cited_file(client: TestClient, rag_env: RagEnv) -> None:
    caller = session()
    answer = client.post(
        "/api/v1/chat/ask",
        json={"question": "How do I clear a paper jam in the DemoPrinter-X2?"},
        headers=caller,
    ).json()
    citation = answer["citations"][0]
    preview = client.get(citation["preview_url"], headers=caller)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["page"], body["is_active_version"], body["mime_type"]) == (2, True, "application/pdf")
    assert body["url"].startswith("/api/v1/files/") and body["url"].endswith("#page=2")

    file = client.get(body["url"].split("#")[0])
    assert file.status_code == 200 and file.content.startswith(b"%PDF-")
    assert file.headers["content-type"] == "application/pdf"
    assert file.headers["content-disposition"].startswith("inline;")
    assert file.headers["cache-control"] == "private, no-store"
    assert file.headers["x-content-type-options"] == "nosniff"
    assert file.headers["referrer-policy"] == "no-referrer"
    assert "sandbox" in file.headers["content-security-policy"]


def test_text_files_are_served_as_plain_text_and_word_files_download(
    client: TestClient, rag_env: RagEnv
) -> None:
    for title, content_type, disposition in [
        ("TechCare Store Hours and Contact", "text/plain; charset=utf-8", "inline;"),
        (
            "TechCare Returns Policy",
            DOCX_MIME,
            "attachment;",
        ),
    ]:
        document = rag_env.documents[title]
        preview = client.get(f"/api/v1/documents/{document.id}/preview", headers=session()).json()
        file = client.get(preview["url"])
        assert file.headers["content-type"] == content_type and file.headers[
            "content-disposition"
        ].startswith(disposition)


def test_preview_respects_audience_and_draft_rules(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    internal = rag_env.documents["Repair Desk Procedures"]
    assert client.get(f"/api/v1/documents/{internal.id}/preview", headers=session()).status_code == 404
    customer = bearer(settings, rag_env.tenant.customer)
    assert client.get(f"/api/v1/documents/{internal.id}/preview", headers=customer).status_code == 404
    staff = bearer(settings, rag_env.tenant.employee)
    assert client.get(f"/api/v1/documents/{internal.id}/preview", headers=staff).status_code == 200

    warranty = rag_env.documents["TechCare Laptop Warranty"]
    data = pdf_bytes([[("h1", "TechCare Laptop Warranty"), ("p", f"Draft {uuid.uuid4()}")]])
    validated = validate_upload("draft.pdf", None, data, rag_env.ctx.settings)
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        draft = docs.create_upload(
            db,
            rag_env.ctx.storage,
            rag_env.ctx.settings,
            rag_env.tenant.manager,
            validated,
            document_id=warranty.id,
        )
    _wait_for(rag_env.ctx, draft.job.id, 60)
    url = f"/api/v1/documents/{warranty.id}/preview?version_id={draft.version.id}"
    assert client.get(url, headers=staff).status_code == 404  # drafts: managers and admins only
    manager = client.get(url, headers=bearer(settings, rag_env.tenant.manager))
    assert manager.status_code == 200 and manager.json()["is_active_version"] is False


def test_preview_ids_cannot_be_probed_or_mixed(client: TestClient, rag_env: RagEnv) -> None:
    warranty, hours = (
        rag_env.documents["TechCare Laptop Warranty"],
        rag_env.documents["TechCare Store Hours and Contact"],
    )
    assert client.get(f"/api/v1/documents/{uuid.uuid4()}/preview", headers=session()).status_code == 404
    mixed = f"/api/v1/documents/{warranty.id}/preview?version_id={hours.active_version_id}"
    assert client.get(mixed, headers=session()).status_code == 404  # a version of another document
    too_far = client.get(f"/api/v1/documents/{warranty.id}/preview?page=99", headers=session())
    assert too_far.status_code == 422 and too_far.json()["error"]["code"] == "PAGE_OUT_OF_RANGE"


def test_file_links_expire_and_are_revoked_by_deletion(
    client: TestClient, rag_env: RagEnv, settings: Settings
) -> None:
    hours = rag_env.documents["TechCare Store Hours and Contact"]
    assert hours.active_version_id is not None
    expired, _ = links.sign(
        settings.model_copy(update={"preview_link_seconds": -5}),
        rag_env.tenant.org.id,
        hours.active_version_id,
    )
    assert client.get(f"/api/v1/files/{expired}").status_code == 404
    assert client.get("/api/v1/files/garbage").status_code == 404
    session_token = bearer(settings, rag_env.tenant.admin)["Authorization"].split()[1]
    assert client.get(f"/api/v1/files/{session_token}").status_code == 404

    data = b"# Loyalty\nTechCare points never expire.\n"
    validated = validate_upload("loyalty.txt", None, data, rag_env.ctx.settings)
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        upload = docs.create_upload(
            db, rag_env.ctx.storage, rag_env.ctx.settings, rag_env.tenant.manager, validated
        )
    _wait_for(rag_env.ctx, upload.job.id, 60)
    url = client.get(
        f"/api/v1/documents/{upload.document.id}/preview?version_id={upload.version.id}",
        headers=bearer(settings, rag_env.tenant.manager),
    ).json()["url"]
    assert client.get(url).status_code == 200
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as db:
        soft_delete_document(db, rag_env.tenant.admin, upload.document.id)
    assert client.get(url).status_code == 404  # still unexpired, but the document is gone
