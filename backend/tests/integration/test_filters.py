"""§10.2 assistant filters (product, brand, category, file type): they only narrow what the caller may already
see, and an answer obeys them."""

import uuid

import pytest
from sqlalchemy import update

from app.core.permissions import Principal
from app.db import models as m
from app.db.session import tenant_session
from app.rag import service
from app.services.versions import active_version_ids
from tests.integration.conftest import RagEnv
from tests.integration.test_rag_graph import context

pytestmark = pytest.mark.integration


def titles(env: RagEnv, principal: Principal, **filters: str) -> set[str]:
    with tenant_session(principal.organization_id, env.ctx.engine) as session:
        active = set(active_version_ids(session, principal, None, **filters))
        return {d.title for d in env.documents.values() if d.active_version_id in active}


def test_filters_narrow_and_never_widen(rag_env: RagEnv) -> None:
    guest = Principal(rag_env.tenant.org.id)
    everything = titles(rag_env, guest)
    printer = rag_env.documents["DemoPrinter-X2 Troubleshooting Guide"]
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        session.execute(update(m.Document).where(m.Document.id == printer.id).values(brand="DemoBrand"))

    # Brand keeps general documents (no brand) and drops another brand's.
    assert titles(rag_env, guest, brand="demobrand") == everything
    assert printer.title not in titles(rag_env, guest, brand="OtherBrand")
    # Category and file type are strict.
    assert titles(rag_env, guest, category="Warranty") == {"TechCare Laptop Warranty"}
    assert titles(rag_env, guest, file_type="docx") == {"TechCare Returns Policy"}  # not the staff-only one
    # No filter can reach an employee-only document for a customer.
    assert "Repair Desk Procedures" not in titles(rag_env, guest, file_type="docx", category="")
    assert "Repair Desk Procedures" in titles(rag_env, rag_env.tenant.employee, file_type="docx")
    assert titles(rag_env, guest, file_type="exe") == set()  # unknown types match nothing


def test_an_answer_obeys_the_assistant_filters(rag_env: RagEnv) -> None:
    question = "What does error code E-04 mean on the DemoPrinter-X2?"
    answered = service.ask(context(rag_env), question, None, {"category": "Troubleshooting"})
    filtered = service.ask(context(rag_env), question, None, {"category": "Warranty"})
    assert answered.answer_status.value == "answered"
    assert filtered.answer_status.value == "insufficient_evidence"  # the printer guide was filtered out
    assert uuid.UUID(str(answered.citations[0].document_id)) == (
        rag_env.documents["DemoPrinter-X2 Troubleshooting Guide"].id
    )
