"""§12.2 unit tests: role and tenant permission predicates, the version state machine, chunk ID stability,
intent routing, evidence grading refinements and validated response models."""

import uuid
from typing import Any, get_type_hints

import pytest
from fastapi.routing import APIRoute

from app.core.permissions import Principal
from app.db.models import Audience, Document, DocumentVersion, Intent, Role, VersionStatus
from app.integrations.chroma import ChunkMetadata, chroma_id
from app.main import app
from app.rag import evidence
from app.rag.intent import classify
from app.rag.schemas import Source
from app.rag.text import content_terms
from app.services.versions import ALLOWED_TRANSITIONS, InvalidTransition, can_view_version, transition

ORG = uuid.uuid4()


@pytest.mark.parametrize(
    ("role", "audiences", "manager"),
    [
        (None, {Audience.PUBLIC}, False),
        (Role.CUSTOMER, {Audience.PUBLIC}, False),
        (Role.EMPLOYEE, {Audience.PUBLIC, Audience.EMPLOYEE}, False),
        (Role.MANAGER, {Audience.PUBLIC, Audience.EMPLOYEE}, True),
        (Role.ADMIN, {Audience.PUBLIC, Audience.EMPLOYEE}, True),
    ],
)
def test_role_predicates_follow_the_permission_matrix(
    role: Role | None, audiences: set[Audience], manager: bool
) -> None:
    principal = Principal(ORG, uuid.uuid4() if role else None, role)
    assert set(principal.allowed_audiences) == audiences
    assert principal.can_manage_documents is manager


def _document(audience: str, org: uuid.UUID = ORG, deleted: bool = False) -> Document:
    from app.core.time import utcnow

    return Document(
        id=uuid.uuid4(),
        organization_id=org,
        title="t",
        audience=audience,
        deleted_at=utcnow() if deleted else None,
    )


def _version(status: VersionStatus) -> DocumentVersion:
    return DocumentVersion(id=uuid.uuid4(), status=status.value)


def test_who_may_open_which_version() -> None:
    customer = Principal(ORG)
    employee = Principal(ORG, uuid.uuid4(), Role.EMPLOYEE)
    manager = Principal(ORG, uuid.uuid4(), Role.MANAGER)
    published, draft = _version(VersionStatus.PUBLISHED), _version(VersionStatus.READY)
    assert can_view_version(customer, _document("public"), published)
    assert not can_view_version(customer, _document("employee"), published)
    assert can_view_version(employee, _document("employee"), published)
    assert not can_view_version(employee, _document("public"), draft)  # drafts: managers and admins
    assert can_view_version(manager, _document("public"), draft)
    assert not can_view_version(manager, _document("public", org=uuid.uuid4()), published)  # another tenant
    assert not can_view_version(manager, _document("public", deleted=True), published)


def test_the_version_state_machine_rejects_every_unlisted_transition() -> None:
    for start in VersionStatus:
        for end in VersionStatus:
            version = _version(start)
            if end in ALLOWED_TRANSITIONS[start]:
                transition(version, end)
                assert version.status == end.value
            else:
                with pytest.raises(InvalidTransition):
                    transition(version, end)
    assert ALLOWED_TRANSITIONS[VersionStatus.ARCHIVED] == frozenset()  # archived is final


def test_chunk_ids_and_metadata_are_stable() -> None:
    version = uuid.UUID("01a15413-5e11-77ac-8745-4163ee6a496a")
    assert chroma_id(ORG, version, 3) == f"{ORG}:{version}:3" == chroma_id(ORG, version, 3)
    metadata = ChunkMetadata(
        organization_id=ORG,
        document_id=version,
        document_version_id=version,
        title="T",
        source_type="pdf",
        page_start=1,
        page_end=2,
        audience=Audience.PUBLIC,
    )
    assert metadata.to_chroma() == metadata.to_chroma()
    assert "product_model" not in metadata.to_chroma()  # unset optional fields are omitted, never None


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Can I cancel an order free of charge?", Intent.KNOWLEDGE),
        ("How do I change the delivery address of an order?", Intent.KNOWLEDGE),
        ("Has my order shipped yet?", Intent.ORDER),
        ("When will my order arrive?", Intent.ORDER),
        ("When does my order arrive?", Intent.ORDER),
        ("Why hasn't my order come yet?", Intent.ORDER),
        ("Is delivery free over 50 dollars, and can I still cancel before my order ships?", Intent.KNOWLEDGE),
        ("What's the status of order number 123?", Intent.ORDER),
        ("Where is my order TC-123456?", Intent.ORDER),
        ("what are my recent orders", Intent.ORDER),
        ("Show me my orders", Intent.ORDER),
        ("What did I order last week?", Intent.ORDER),
        ("Can I cancel my order?", Intent.KNOWLEDGE),
        ("How do I return my order?", Intent.KNOWLEDGE),
        ("Do my orders ship for free over $50?", Intent.KNOWLEDGE),
        # ADR-031: one's own repairs and pickups
        ("when can i pick up my products", Intent.ORDER),
        ("how many products do I have", Intent.ORDER),
        ("what was the faults with my product", Intent.ORDER),
        ("Is my laptop fixed yet?", Intent.ORDER),
        ("What did the technician find?", Intent.ORDER),
        ("RP-100001 status?", Intent.ORDER),
        ("What's wrong with my printer? It prints faded pages", Intent.KNOWLEDGE),
        ("Can someone else pick up an order for me?", Intent.KNOWLEDGE),
        ("How long does a laptop repair usually take?", Intent.KNOWLEDGE),
        ("How do I collect points?", Intent.KNOWLEDGE),
    ],
)
def test_order_lookups_versus_questions_about_orders(question: str, intent: Intent) -> None:
    assert classify(question) is intent


def _source(text: str, product: str | None = None, similarity: float = 0.5) -> Source:
    version = uuid.uuid4()
    return Source(
        source_id="C1",
        chroma_id=f"{ORG}:{version}:0",
        document_id=uuid.uuid4(),
        version_id=version,
        title="T",
        page_start=1,
        page_end=1,
        section=None,
        audience="public",
        text=text,
        similarity=similarity,
        product_model=product,
    )


def _grade(question: str, sources: list[Source]) -> Any:
    return evidence.grade(
        question, sources, [s.version_id for s in sources], min_similarity=0.05, min_coverage=0.6, limit=5
    )


def test_words_no_source_mentions_weigh_double() -> None:
    discount = _source("Employees get a 15% staff discount after 90 days of employment.")
    assert not _grade("How many vacation days do employees get?", [discount]).sufficient
    fee = _source("Opened laptops have a 15% restocking fee.")
    assert _grade(
        "What is the restockng fee for an opened laptop?", [fee]
    ).sufficient  # one typo still passes


@pytest.mark.parametrize(
    ("a", "b", "near"),
    [
        ("coverd", "cover", True),  # deletion (against the stem)
        ("expyre", "expire", True),  # substitution
        ("pikcup", "pickup", True),  # neighbours swapped
        ("standrd", "standard", True),  # deletion inside
        ("express", "express", False),  # identical is not an edit
        ("expres", "address", False),  # two edits
        ("carts", "card", False),  # two edits
    ],
)
def test_one_edit_is_a_typo(a: str, b: str, near: bool) -> None:
    assert evidence.one_edit(a, b) is near and evidence.one_edit(b, a) is near


def test_typos_count_as_covered_but_codes_and_short_words_must_match_exactly() -> None:
    gift = _source("TechCare gift cards never expire. Gift cards cannot be exchanged for cash.")
    assert _grade("Do gift cards expyre?", [gift]).sufficient
    assert evidence.mentions({"expire"}, "expyre")
    assert not evidence.mentions({"e-04"}, "e-05")  # error codes: exact only
    assert not evidence.mentions({"h1"}, "h2")
    assert not evidence.mentions({"card"}, "care")  # 4 letters: too short to guess
    assert not evidence.mentions({"x200"}, "x300")
    # A typo of a word that is in the sources doesn't rescue an off-topic question.
    assert not _grade("Do gift cards work for refrigerater parts?", [gift]).sufficient


def test_a_code_no_source_mentions_means_no_evidence() -> None:
    hub = _source("Smart Hub H1 error codes. Code: H-07; Meaning: Firmware update failed.", "Smart Hub H1")
    assert not _grade("What does error H9 mean on the Smart Hub H1?", [hub]).sufficient
    assert _grade("What does error H-07 mean on the Smart Hub H1?", [hub]).sufficient
    fee = _source("Opened laptops have a 15% restocking fee within 30 days.")
    assert _grade(
        "What is the restocking fee for an opened laptop after 45 days?", [fee]
    ).sufficient  # a number
    assert [evidence.is_code(t) for t in ("h9", "e-04", "x2", "45", "usb-c")] == [
        True,
        True,
        True,
        False,
        False,
    ]


def test_a_two_part_question_keeps_the_document_for_each_part() -> None:
    question = "What warranty comes with a screen repair, and how long does Care+ cover accidental damage?"
    repair = _source("Phone Screen Repair Service. Every screen repair comes with a 90 day repair warranty.")
    care = _source("Care+ Protection Plan Terms. Care+ covers accidental damage for 24 months from purchase.")
    gift = _source("Gift Cards FAQ. TechCare gift cards never expire.")
    kept = evidence.complementary(content_terms(question), [care], [gift, repair])
    assert kept == [repair]  # adds the other half of the answer, not an unrelated document
    assert evidence.complementary(content_terms(question), [], [repair]) == []  # never starts one


def test_a_code_inside_a_hyphenated_name_counts_as_mentioned() -> None:
    pro = _source(
        "DemoPrinter-X2 Pro Troubleshooting Guide > Error codes. Code: P-11; Meaning: Paper size mismatch.",
        "DemoPrinter-X2 Pro",
    )
    assert _grade("P-11 on my X2 Pro printer?", [pro]).sufficient  # "x2" is part of "demoprinter-x2"
    assert _grade("What does P11 mean on the DemoPrinter-X2 Pro?", [pro]).sufficient  # "p11" is "p-11"
    assert not _grade("What does P-12 mean on the X2 Pro?", [pro]).sufficient  # a different code still isn't
    assert {"demoprinter", "x2", "demoprinter-x2", "demoprinterx2"} <= evidence.vocabulary("DemoPrinter-X2")


def test_a_named_product_excludes_other_models_but_not_general_documents() -> None:
    x2 = _source("DemoPrinter-X2 error code E-04 means toner cartridge not detected.", "DemoPrinter-X2")
    pro = _source("DemoPrinter-X2 Pro error code E-04 means duplex unit not detected.", "DemoPrinter-X2 Pro")
    policy = _source("Printer error code E-04 repairs on a DemoPrinter-X2 or Pro are covered under warranty.")
    graded = _grade("What does error code E-04 mean on the DemoPrinter-X2?", [x2, pro, policy])
    assert {s.product_model for s in graded.sources} == {"DemoPrinter-X2", None}
    graded_pro = _grade("What does error code E-04 mean on the DemoPrinter-X2 Pro?", [x2, pro, policy])
    assert {s.product_model for s in graded_pro.sources} == {"DemoPrinter-X2 Pro", None}
    assert evidence.mentioned_products(
        "e-04 on the demoprinter-x2?", {"DemoPrinter-X2", "DemoPrinter-X2 Pro"}
    ) == {"demoprinter-x2"}


def test_every_json_route_validates_its_response_with_a_model() -> None:
    # "Pydantic response format always validated" (§12.2): FastAPI validates and filters through these models.
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in {"/api/v1/files/{token}"}:
            continue
        if route.status_code == 204 or getattr(route.response_class, "__name__", "") == "EventSourceResponse":
            continue
        hints = get_type_hints(route.endpoint)
        assert route.response_model is not None and hints.get("return") is not Any, route.path
