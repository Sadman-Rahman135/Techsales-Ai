"""Blueprint §6.1 constraints and ADR-011 row-level security, verified on real Postgres as the app role."""

import uuid

import pytest
from alembic import command
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.session import set_tenant
from tests.integration.conftest import Factory, alembic_config, make_tenant, run_alembic, sha256

pytestmark = pytest.mark.integration


def test_version_number_is_unique_per_document(db: Session, make: Factory) -> None:
    doc = make.document(make.org())
    make.version(doc, number=1)
    with pytest.raises(IntegrityError, match="uq_document_versions_document_id_version_number"):
        make.version(doc, number=1)


def test_same_file_twice_on_one_document_is_rejected(db: Session, make: Factory) -> None:
    doc = make.document(make.org())
    checksum = sha256()
    make.version(doc, number=1, checksum=checksum)
    with pytest.raises(IntegrityError, match="uq_document_versions_document_id_checksum"):
        make.version(doc, number=2, checksum=checksum)


def test_only_one_published_version_per_document(db: Session, make: Factory) -> None:
    doc = make.document(make.org())
    published = m.VersionStatus.PUBLISHED
    make.version(doc, number=1, status=published, published_at=func.now())
    with pytest.raises(IntegrityError, match="uq_document_versions_one_published"):
        make.version(doc, number=2, status=published, published_at=func.now())


def test_active_version_must_belong_to_the_same_document(db: Session, make: Factory) -> None:
    org = make.org()
    doc_a, doc_b = make.document(org, title="A"), make.document(org, title="B")
    other_version = make.version(doc_b)
    doc_a.active_version_id = other_version.id
    with pytest.raises(IntegrityError, match="fk_documents_active_version"):
        db.flush()


def test_child_rows_cannot_point_at_another_tenant(db: Session, make: Factory) -> None:
    org_a, org_b = make.org(), make.org()
    doc_a = make.document(org_a)
    set_tenant(db, org_b.id)
    db.add(
        m.DocumentVersion(
            organization_id=org_b.id,
            document_id=doc_a.id,
            version_number=1,
            checksum=sha256(),
            storage_key="x",
            original_filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=1,
        )
    )
    with pytest.raises(IntegrityError, match="fk_document_versions_organization_id_document_id_documents"):
        db.flush()


def test_chroma_id_must_be_deterministic(db: Session, make: Factory) -> None:
    doc = make.document(make.org())
    version = make.version(doc)
    db.add(
        m.DocumentChunk(
            organization_id=doc.organization_id,
            version_id=version.id,
            chunk_index=0,
            chroma_id="random-id",
            page_start=1,
            page_end=1,
            text_checksum=sha256(),
        )
    )
    with pytest.raises(IntegrityError, match="chroma_id_deterministic"):
        db.flush()


def test_answered_status_requires_grounding(db: Session, make: Factory) -> None:
    org = make.org()
    _, principal = make.user(org, m.Role.CUSTOMER)
    set_tenant(db, org.id)
    convo = m.Conversation(organization_id=org.id, owner_user_id=principal.user_id, audience="public")
    db.add(convo)
    db.flush()
    msg = m.Message(organization_id=org.id, conversation_id=convo.id, role="assistant", content="…")
    db.add(msg)
    db.flush()
    db.add(
        m.Answer(
            organization_id=org.id,
            message_id=msg.id,
            answer_status="answered",
            grounded=False,
            request_id="req-1",
        )
    )
    with pytest.raises(IntegrityError, match="grounded_matches_status"):
        db.flush()


# ---------- row-level security (ADR-011) ----------


def test_tests_run_as_the_restricted_app_role(db: Session) -> None:
    """Guard: as a superuser, RLS would be bypassed and the tests below would pass for the wrong reason."""
    row = db.execute(
        text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    assert tuple(row) == ("knowledgeai_app", False, False)


def test_tenant_tables_return_nothing_without_a_tenant(db: Session, make: Factory) -> None:
    make.document(make.org())
    db.execute(text("SELECT set_config('app.organization_id', '', true)"))
    assert db.scalar(select(func.count()).select_from(m.Document)) == 0


def test_each_tenant_sees_only_its_own_rows(db: Session, make: Factory) -> None:
    org_a, org_b = make.org(), make.org()
    make.document(org_a, title="A policy")
    make.document(org_b, title="B policy")
    set_tenant(db, org_a.id)
    assert db.scalars(select(m.Document.title)).all() == ["A policy"]
    set_tenant(db, org_b.id)
    assert db.scalars(select(m.Document.title)).all() == ["B policy"]


def test_cannot_write_rows_for_another_tenant(db: Session, make: Factory) -> None:
    org_a, org_b = make.org(), make.org()
    set_tenant(db, org_a.id)
    db.add(m.Document(organization_id=org_b.id, title="Smuggled", audience="public"))
    with pytest.raises(DBAPIError, match="row-level security"):
        db.flush()


def test_cannot_read_another_tenants_row_even_by_id(db: Session, make: Factory) -> None:
    org_a, org_b = make.org(), make.org()
    doc_b = make.document(org_b)
    db.expunge_all()
    set_tenant(db, org_a.id)
    assert db.get(m.Document, doc_b.id) is None  # §6.1: ownership checked on every fetch, not only lists


# ---------- audit log ----------


def test_audit_events_are_append_only(db: Session, make: Factory) -> None:
    org = make.org()
    set_tenant(db, org.id)
    event = m.AuditEvent(organization_id=org.id, actor_label="system", action="test", target_type="none")
    db.add(event)
    db.flush()
    with pytest.raises(DBAPIError, match="append-only"), db.begin_nested():
        db.execute(update(m.AuditEvent).where(m.AuditEvent.id == event.id).values(action="tampered"))
    with pytest.raises(DBAPIError, match="append-only"), db.begin_nested():
        db.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": event.id})


# ---------- delete behaviour ----------


def _answer_with_citation(db: Session, make: Factory) -> tuple[m.Conversation, m.Answer, m.DocumentVersion]:
    org = make.org()
    doc = make.document(org)
    version = make.version(doc, chunks=1)
    chunk = db.scalar(select(m.DocumentChunk).where(m.DocumentChunk.version_id == version.id))
    assert chunk is not None
    _, principal = make.user(org, m.Role.CUSTOMER)
    set_tenant(db, org.id)
    convo = m.Conversation(organization_id=org.id, owner_user_id=principal.user_id, audience="public")
    db.add(convo)
    db.flush()
    msg = m.Message(organization_id=org.id, conversation_id=convo.id, role="assistant", content="Answer")
    db.add(msg)
    db.flush()
    answer = m.Answer(
        organization_id=org.id, message_id=msg.id, answer_status="answered", grounded=True, request_id="req-1"
    )
    db.add(answer)
    db.flush()
    db.add(
        m.AnswerCitation(
            organization_id=org.id,
            answer_id=answer.id,
            position=1,
            chunk_id=chunk.id,
            document_version_id=version.id,
            page_start=1,
            page_end=1,
            quote="Quoted text",
        )
    )
    db.add(m.AnswerFeedback(organization_id=org.id, answer_id=answer.id, rating="helpful"))
    db.flush()
    return convo, answer, version


def test_deleting_a_conversation_removes_its_content(db: Session, make: Factory) -> None:
    convo, _, _ = _answer_with_citation(db, make)
    db.delete(convo)
    db.flush()
    db.expunge_all()
    for table in (m.Message, m.Answer, m.AnswerCitation, m.AnswerFeedback):
        assert db.scalar(select(func.count()).select_from(table)) == 0, table.__tablename__


def test_deleting_a_source_keeps_the_citation_row_but_drops_the_link(db: Session, make: Factory) -> None:
    _, answer, version = _answer_with_citation(db, make)
    org_id = version.organization_id
    db.execute(update(m.Document).values(active_version_id=None))
    db.execute(text("DELETE FROM documents WHERE id = :id"), {"id": version.document_id})
    db.expunge_all()
    citation = db.scalar(select(m.AnswerCitation).where(m.AnswerCitation.answer_id == answer.id))
    assert citation is not None
    assert (citation.chunk_id, citation.document_version_id, citation.organization_id) == (None, None, org_id)


# ---------- migrations ----------


def test_migrations_round_trip_and_match_the_models(app_engine: object) -> None:
    run_alembic("base", "downgrade")
    run_alembic("head")
    command.check(alembic_config())  # raises if models and migrations have drifted


def test_uuidv7_keys_are_time_ordered(db: Session, make: Factory) -> None:
    first, second = make.org(), make.org()
    assert isinstance(first.id, uuid.UUID) and first.id.version == 7
    assert first.id < second.id


def test_versions_are_immutable_once_uploaded(db: Session, make: Factory) -> None:
    doc = make.document(make.org())
    version = make.version(doc, status=m.VersionStatus.PROCESSING)
    with pytest.raises(DBAPIError, match="immutable"), db.begin_nested():
        db.execute(
            update(m.DocumentVersion).where(m.DocumentVersion.id == version.id).values(checksum=sha256())
        )
    # Lifecycle fields may still change.
    db.execute(update(m.DocumentVersion).where(m.DocumentVersion.id == version.id).values(status="ready"))


def test_the_worker_has_its_own_login_under_the_same_isolation(app_engine: Engine) -> None:
    """§16 "separate service accounts": the ingestion worker's role inherits the app role's privileges, so RLS
    holds for it too, but it is a different login that can be revoked on its own."""
    import os

    from sqlalchemy.engine import make_url

    from app.config import get_settings
    from app.db.session import build_engine, tenant_session

    url = make_url(app_engine.url.render_as_string(hide_password=False)).set(
        username="knowledgeai_worker",
        password=os.environ.get("WORKER_DB_PASSWORD", "knowledgeai-worker-dev-only"),
    )
    worker_engine = build_engine(get_settings(), url.render_as_string(hide_password=False))
    org_a, org_b = make_tenant(app_engine).org, make_tenant(app_engine).org
    with tenant_session(org_a.id, app_engine) as session:
        session.add(m.Document(organization_id=org_a.id, title="Worker-visible", audience="public"))
    try:
        with worker_engine.connect() as connection:
            role = connection.execute(
                text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
            assert tuple(role) == ("knowledgeai_worker", False, False)
            assert (
                connection.scalar(select(func.count()).select_from(m.IngestionJob)) is not None
            )  # claims jobs
            assert connection.scalar(select(func.count()).select_from(m.Document)) == 0  # no tenant: nothing
            with pytest.raises(DBAPIError, match="permission denied"):
                connection.execute(text("CREATE TABLE worker_cannot_do_ddl (id int)"))
        with tenant_session(org_a.id, worker_engine) as session:
            assert session.scalars(select(m.Document.title)).all() == ["Worker-visible"]
        with tenant_session(org_b.id, worker_engine) as session:
            assert session.scalars(select(m.Document.title)).all() == []
    finally:
        worker_engine.dispose()


def test_every_tenant_table_has_forced_row_level_security(app_engine: Engine) -> None:
    """ADR-011 for every table that holds tenant data, including ADR-029's orders and tickets: a new table
    with an organization_id must be added to RLS_TABLES and get the policy in its migration."""
    no_rls = {"memberships", "ingestion_jobs"}  # read before a tenant is known / claimed across tenants
    with app_engine.connect() as connection:
        tenant_tables = set(
            connection.scalars(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'organization_id'"
                )
            )
        )
        protected = {
            row.relname: (row.relrowsecurity, row.relforcerowsecurity)
            for row in connection.execute(
                text("SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relkind = 'r'")
            )
        }
        policies = set(
            connection.scalars(
                text("SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")
            )
        )
    assert tenant_tables - no_rls == set(m.RLS_TABLES)
    assert all(protected[t] == (True, True) for t in m.RLS_TABLES)
    assert set(m.RLS_TABLES) <= policies
