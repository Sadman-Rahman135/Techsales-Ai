"""§8.5 hybrid retrieval (ADR-028): keyword search finds exact codes and stays inside the caller's tenant,
audience and active versions, exactly like the vector search."""

import uuid

import pytest
from sqlalchemy import Engine, func, select

from app.core.permissions import Principal
from app.db import models as m
from app.db.session import tenant_session
from app.demo.corpus import EMPLOYEE_CANARY
from app.rag import keyword
from app.services.versions import active_version_ids
from tests.integration.conftest import RagEnv, make_tenant

pytestmark = pytest.mark.integration


def keyword_hits(env: RagEnv, principal: Principal, question: str) -> tuple[list[uuid.UUID], list[str]]:
    with tenant_session(principal.organization_id, env.ctx.engine) as session:
        active = active_version_ids(session, principal, None)
        return active, keyword.search(session, question, active, 10)


def test_an_exact_code_is_found_by_keyword(rag_env: RagEnv) -> None:
    guest = Principal(rag_env.tenant.org.id)
    active, ids = keyword_hits(rag_env, guest, "printer shows E-07")
    vector = rag_env.ctx.embedder.embed_queries(["printer shows E-07"]).vectors[0]
    hits = rag_env.ctx.index.fetch(guest, ids, vector, active)
    assert hits and "Fuser overheated" in hits[0].text  # the row with the code ranks first
    assert all(0.0 <= h.distance <= 2.0 for h in hits)  # a real cosine distance, graded like any candidate


def test_employee_only_text_is_never_found_for_a_customer(rag_env: RagEnv) -> None:
    _, for_guest = keyword_hits(rag_env, Principal(rag_env.tenant.org.id), EMPLOYEE_CANARY)
    _, for_customer = keyword_hits(rag_env, rag_env.tenant.customer, EMPLOYEE_CANARY)
    _, for_employee = keyword_hits(rag_env, rag_env.tenant.employee, EMPLOYEE_CANARY)
    assert for_guest == [] and for_customer == []
    # The same words are found for staff: the audience filter, not the query, made the difference.
    assert for_employee


def test_another_tenant_finds_and_fetches_nothing_even_with_these_version_ids(
    rag_env: RagEnv, app_engine: Engine
) -> None:
    owner = Principal(rag_env.tenant.org.id)
    active, ids = keyword_hits(rag_env, owner, "E-07")
    assert ids
    stranger = Principal(make_tenant(app_engine).org.id)
    with tenant_session(stranger.organization_id, app_engine) as session:
        assert keyword.search(session, "E-07", active, 10) == []  # row-level security
    vector = rag_env.ctx.embedder.embed_queries(["E-07"]).vectors[0]
    assert rag_env.ctx.index.fetch(stranger, ids, vector, active) == []  # the Chroma filter and re-check


def test_the_worker_stores_every_chunks_text(rag_env: RagEnv) -> None:
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        missing = session.scalar(
            select(func.count()).select_from(m.DocumentChunk).where(m.DocumentChunk.text.is_(None))
        )
        total = session.scalar(select(func.count()).select_from(m.DocumentChunk))
    assert total and missing == 0
