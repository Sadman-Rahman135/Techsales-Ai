"""Blueprint §6.2 and ADR-004: deterministic IDs, idempotent upserts, and queries that can only return
chunks the principal may see from active versions."""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.permissions import Principal
from app.db import models as m
from app.integrations.chroma import ChunkMetadata, ChunkRecord, KnowledgeIndex, chroma_id
from tests.integration.conftest import Factory, embed, index_version

pytestmark = pytest.mark.integration


def test_ids_are_deterministic() -> None:
    org, version = uuid.uuid4(), uuid.uuid4()
    assert chroma_id(org, version, 3) == f"{org}:{version}:3"
    with pytest.raises(ValueError):
        chroma_id(org, version, -1)


def test_reindexing_a_version_overwrites_instead_of_duplicating(index: KnowledgeIndex, make: Factory) -> None:
    doc = make.document(make.org())
    version = make.version(doc)
    index_version(index, doc, version, ["a", "b", "c"])
    index_version(index, doc, version, ["a", "b", "c"])  # e.g. a retried job (G7)
    assert index.count_version(doc.organization_id, version.id) == 3


def test_public_principal_never_sees_employee_chunks(index: KnowledgeIndex, make: Factory) -> None:
    org = make.org()
    public_doc, internal_doc = make.document(org, m.Audience.PUBLIC), make.document(org, m.Audience.EMPLOYEE)
    public_v, internal_v = make.version(public_doc), make.version(internal_doc)
    index_version(index, public_doc, public_v, ["Returns within 30 days."])
    index_version(index, internal_doc, internal_v, ["CANARY-EMP-7Q2 internal procedure."])
    active = [public_v.id, internal_v.id]

    customer = Principal(organization_id=org.id)
    employee = Principal(organization_id=org.id, user_id=uuid.uuid4(), role=m.Role.EMPLOYEE)
    customer_texts = [hit.text for hit in index.query(customer, embed(0), active, n_results=10)]
    employee_texts = [hit.text for hit in index.query(employee, embed(0), active, n_results=10)]

    assert customer_texts == ["Returns within 30 days."]
    assert not any("CANARY" in t for t in customer_texts)
    assert sorted(employee_texts) == ["CANARY-EMP-7Q2 internal procedure.", "Returns within 30 days."]


def test_other_tenants_chunks_are_never_returned(index: KnowledgeIndex, make: Factory) -> None:
    org_a, org_b = make.org(), make.org()
    doc_a, doc_b = make.document(org_a), make.document(org_b)
    v_a, v_b = make.version(doc_a), make.version(doc_b)
    index_version(index, doc_a, v_a, ["Org A policy"])
    index_version(index, doc_b, v_b, ["Org B policy"])
    # Even if org B's version ID leaked into the active list, the tenant filter still excludes it.
    hits = index.query(Principal(organization_id=org_a.id), embed(0), [v_a.id, v_b.id], n_results=10)
    assert [h.text for h in hits] == ["Org A policy"]


def test_only_active_versions_are_searched(index: KnowledgeIndex, make: Factory) -> None:
    doc = make.document(make.org())
    old, new = make.version(doc, number=1), make.version(doc, number=2)
    index_version(index, doc, old, ["Old: returns within 14 days"])
    index_version(index, doc, new, ["New: returns within 30 days"])
    hits = index.query(Principal(organization_id=doc.organization_id), embed(0), [new.id], n_results=10)
    assert [h.text for h in hits] == ["New: returns within 30 days"]


def test_nothing_published_means_no_query_at_all(index: KnowledgeIndex, make: Factory) -> None:
    doc = make.document(make.org())
    index_version(index, doc, make.version(doc), ["text"])
    assert index.query(Principal(organization_id=doc.organization_id), embed(0), [], n_results=5) == []


def test_delete_version_removes_its_vectors(index: KnowledgeIndex, make: Factory) -> None:
    doc = make.document(make.org())
    version = make.version(doc)
    index_version(index, doc, version, ["a", "b"])
    index.delete_version(doc.organization_id, version.id)
    assert index.count_version(doc.organization_id, version.id) == 0


def test_rejects_chunks_for_a_different_version_or_dimension(
    index: KnowledgeIndex, make: Factory, db: Session
) -> None:
    doc = make.document(make.org())
    version, other = make.version(doc, number=1), make.version(doc, number=2)
    meta = ChunkMetadata(
        organization_id=doc.organization_id,
        document_id=doc.id,
        document_version_id=other.id,
        title="T",
        source_type="pdf",
        page_start=1,
        page_end=1,
        audience=m.Audience.PUBLIC,
    )
    with pytest.raises(ValueError, match="does not match"):
        index.upsert_version(doc.organization_id, version.id, [ChunkRecord(0, "x", embed(0), meta)])
    good_meta = meta.model_copy(update={"document_version_id": version.id})
    with pytest.raises(ValueError, match="dims"):
        index.upsert_version(doc.organization_id, version.id, [ChunkRecord(0, "x", [0.1, 0.2], good_meta)])


def test_metadata_is_scalar_only_and_strict() -> None:
    meta = ChunkMetadata(
        organization_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        title="Manual",
        source_type="pdf",
        page_start=7,
        page_end=8,
        audience=m.Audience.PUBLIC,
    )
    stored = meta.to_chroma()
    assert all(isinstance(v, str | int) for v in stored.values())
    assert "section" not in stored  # None is omitted, Chroma rejects it
    with pytest.raises(ValueError):
        ChunkMetadata.model_validate(
            {**meta.model_dump(), "permissions": ["admin"]}
        )  # extra fields forbidden
