"""Blueprint §6.3 versioning and deletion semantics."""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import Principal
from app.db import models as m
from app.db.session import set_tenant
from app.integrations.chroma import KnowledgeIndex
from app.services import versions as svc
from app.services.storage import LocalStorage
from tests.integration.conftest import Factory, index_version

pytestmark = pytest.mark.integration

READY, PUBLISHED, ARCHIVED = m.VersionStatus.READY, m.VersionStatus.PUBLISHED, m.VersionStatus.ARCHIVED


def manager_of(make: Factory, org: m.Organization) -> Principal:
    return make.user(org, m.Role.MANAGER)[1]


def ready_indexed(
    make: Factory, index: KnowledgeIndex, doc: m.Document, number: int, chunks: int = 2
) -> m.DocumentVersion:
    version = make.version(doc, number=number, status=READY, chunks=chunks)
    index_version(index, doc, version, [f"v{number} chunk {i}" for i in range(chunks)])
    return version


def test_new_uploads_are_not_answerable_until_published(
    db: Session, make: Factory, index: KnowledgeIndex
) -> None:
    org = make.org()
    doc = make.document(org)
    ready_indexed(make, index, doc, 1)
    assert svc.active_version_ids(db, Principal(organization_id=org.id)) == []


def test_publish_switches_the_pointer_archives_the_old_version_and_audits(
    db: Session, make: Factory, index: KnowledgeIndex
) -> None:
    org = make.org()
    manager = manager_of(make, org)
    doc = make.document(org)
    v1, v2 = ready_indexed(make, index, doc, 1), ready_indexed(make, index, doc, 2)

    svc.publish_version(db, index, manager, doc.id, v1.id)
    svc.publish_version(db, index, manager, doc.id, v2.id, request_id="req-42")

    for row in (doc, v1, v2):
        db.refresh(row)
    assert doc.active_version_id == v2.id
    assert (v1.status, v2.status) == (ARCHIVED.value, PUBLISHED.value)
    assert v2.published_at is not None
    assert svc.active_version_ids(db, Principal(organization_id=org.id)) == [v2.id]
    events = db.scalars(select(m.AuditEvent).order_by(m.AuditEvent.created_at, m.AuditEvent.id)).all()
    assert [e.action for e in events] == ["document.version.published"] * 2
    assert events[-1].details["previous_version_id"] == str(v1.id) and events[-1].request_id == "req-42"


def test_publish_refuses_when_chroma_is_missing_chunks(
    db: Session, make: Factory, index: KnowledgeIndex
) -> None:
    org = make.org()
    doc = make.document(org)
    version = make.version(doc, status=READY, chunks=3)
    index_version(index, doc, version, ["only", "two"])  # one chunk never made it into Chroma
    with pytest.raises(svc.VersionNotIndexed, match="Chroma has 2"):
        svc.publish_version(db, index, manager_of(make, org), doc.id, version.id)
    db.refresh(doc)
    assert doc.active_version_id is None


@pytest.mark.parametrize(
    "status", [m.VersionStatus.UPLOADED, m.VersionStatus.PROCESSING, m.VersionStatus.FAILED]
)
def test_only_ready_versions_can_be_published(
    db: Session, make: Factory, index: KnowledgeIndex, status: m.VersionStatus
) -> None:
    org = make.org()
    doc = make.document(org)
    version = make.version(doc, status=status)
    with pytest.raises(svc.InvalidTransition):
        svc.publish_version(db, index, manager_of(make, org), doc.id, version.id)


@pytest.mark.parametrize("role", [None, m.Role.CUSTOMER, m.Role.EMPLOYEE])
def test_only_managers_and_admins_can_publish(
    db: Session, make: Factory, index: KnowledgeIndex, role: m.Role | None
) -> None:
    org = make.org()
    doc = make.document(org)
    version = ready_indexed(make, index, doc, 1)
    with pytest.raises(svc.PermissionDenied):
        svc.publish_version(db, index, Principal(organization_id=org.id, role=role), doc.id, version.id)


def test_state_machine_rejects_invalid_transitions(db: Session, make: Factory) -> None:
    doc = make.document(make.org())
    version = make.version(doc, status=ARCHIVED)
    with pytest.raises(svc.InvalidTransition):
        svc.transition(
            version, PUBLISHED
        )  # archived is terminal; a rollback publishes a *copy* as a new version
    draft = make.version(doc, number=2, status=m.VersionStatus.UPLOADED)
    svc.transition(draft, m.VersionStatus.PROCESSING)
    assert draft.status == "processing"


def test_active_versions_respect_audience_and_deletion(
    db: Session, make: Factory, index: KnowledgeIndex
) -> None:
    org = make.org()
    manager = manager_of(make, org)
    public_doc, internal_doc, deleted_doc = (
        make.document(org, m.Audience.PUBLIC, title="Public"),
        make.document(org, m.Audience.EMPLOYEE, title="Internal"),
        make.document(org, m.Audience.PUBLIC, title="Deleted"),
    )
    ids = {}
    for doc in (public_doc, internal_doc, deleted_doc):
        version = ready_indexed(make, index, doc, 1)
        svc.publish_version(db, index, manager, doc.id, version.id)
        ids[doc.title] = version.id
    admin = make.user(org, m.Role.ADMIN)[1]
    svc.soft_delete_document(db, admin, deleted_doc.id)

    assert svc.active_version_ids(db, Principal(organization_id=org.id)) == [ids["Public"]]
    employee = Principal(organization_id=org.id, role=m.Role.EMPLOYEE)
    assert sorted(svc.active_version_ids(db, employee)) == sorted([ids["Public"], ids["Internal"]])


def test_archived_versions_are_visible_to_document_managers_only(db: Session, make: Factory) -> None:
    org = make.org()
    doc = make.document(org)
    archived = make.version(doc, status=ARCHIVED)
    assert svc.can_view_version(manager_of(make, org), doc, archived)
    assert not svc.can_view_version(Principal(organization_id=org.id, role=m.Role.EMPLOYEE), doc, archived)
    assert not svc.can_view_version(Principal(organization_id=org.id), doc, archived)


def test_duplicate_checksum_is_found_across_documents(db: Session, make: Factory) -> None:
    org = make.org()
    first, second = make.document(org, title="Returns"), make.document(org, title="Returns copy")
    existing = make.version(first)
    found = svc.find_duplicates(db, org.id, existing.checksum)
    assert [v.document_id for v in found] == [first.id]
    assert second.id not in {v.document_id for v in found}


def test_reconcile_reports_drift(db: Session, make: Factory, index: KnowledgeIndex) -> None:
    doc = make.document(make.org())
    version = make.version(doc, status=READY, chunks=3)
    index_version(index, doc, version, ["a", "b"])
    report = svc.reconcile_version(db, index, version)
    assert (report.expected, report.postgres_chunks, report.chroma_chunks, report.consistent) == (
        3,
        3,
        2,
        False,
    )


def test_soft_delete_requires_admin(db: Session, make: Factory) -> None:
    org = make.org()
    doc = make.document(org)
    with pytest.raises(svc.PermissionDenied):
        svc.soft_delete_document(db, manager_of(make, org), doc.id)


def test_purge_removes_vectors_files_and_rows_but_keeps_citation_rows(
    db: Session, make: Factory, index: KnowledgeIndex, tmp_path: Path
) -> None:
    org = make.org()
    admin = make.user(org, m.Role.ADMIN)[1]
    storage = LocalStorage(tmp_path)
    doc = make.document(org)
    version = ready_indexed(make, index, doc, 1)
    storage.put(version.storage_key, b"%PDF-1.7 fictional")
    svc.publish_version(db, index, admin, doc.id, version.id)

    convo = m.Conversation(organization_id=org.id, owner_user_id=admin.user_id, audience="public")
    db.add(convo)
    db.flush()
    msg = m.Message(organization_id=org.id, conversation_id=convo.id, role="assistant", content="…")
    db.add(msg)
    db.flush()
    answer = m.Answer(
        organization_id=org.id, message_id=msg.id, answer_status="answered", grounded=True, request_id="r"
    )
    db.add(answer)
    db.flush()
    db.add(
        m.AnswerCitation(
            organization_id=org.id,
            answer_id=answer.id,
            position=1,
            document_version_id=version.id,
            page_start=1,
            page_end=1,
            quote="Returns policy text",
        )
    )
    db.flush()

    svc.soft_delete_document(db, admin, doc.id)
    svc.purge_document(db, index, storage, doc, admin)

    set_tenant(db, org.id)
    db.expunge_all()
    assert index.count_version(org.id, version.id) == 0
    assert not storage.exists(version.storage_key)
    assert db.scalar(select(func.count()).select_from(m.DocumentVersion)) == 0
    citation = db.scalar(select(m.AnswerCitation))
    assert citation is not None and (citation.document_version_id, citation.quote, citation.position) == (
        None,
        None,
        1,
    )
    actions = db.scalars(select(m.AuditEvent.action)).all()
    assert {"document.deleted", "document.purged"} <= set(actions)
