"""Blueprint §8 end to end on real Postgres + Chroma: the graph over the published demo corpus."""

import dataclasses
import hashlib
import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import func, select

from app.core.permissions import Principal
from app.db import models as m
from app.db.session import tenant_session
from app.demo.builders import pdf_bytes
from app.demo.corpus import EMPLOYEE_CANARY, _wait_for
from app.ingestion.validation import validate_upload
from app.rag import prompts, service
from app.rag.graph import RAGContext
from app.rag.llm import ChatModel, ChatModelError, FakeChatModel
from app.rag.schemas import AskResult, CitedSource, GeneratedAnswer, Source
from app.rag.usage import TokenUsage
from app.services import documents as docs
from tests.integration.conftest import RagEnv

pytestmark = pytest.mark.integration


def context(
    env: RagEnv, principal: Principal | None = None, llm: ChatModel | None = None, **kw: object
) -> RAGContext:
    principal = principal or Principal(env.tenant.org.id)
    return RAGContext(
        principal=principal,
        engine=env.ctx.engine,
        index=env.ctx.index,
        embedder=env.ctx.embedder,
        llm=llm or FakeChatModel(),
        settings=kw.pop("settings", env.ctx.settings),  # type: ignore[arg-type]
        request_id=f"req-{uuid.uuid4().hex}",
        trace_id=f"trace-{uuid.uuid4().hex}",
        session_ref=None if principal.user_id else hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        **kw,  # type: ignore[arg-type]
    )


def ask(env: RagEnv, question: str, **kw: object) -> AskResult:
    return service.ask(context(env, **kw), question)  # type: ignore[arg-type]


def stored(env: RagEnv, result: AskResult) -> tuple[m.Answer, list[m.AnswerCitation], list[m.Message]]:
    with tenant_session(env.tenant.org.id, env.ctx.engine) as session:
        answer = session.get_one(m.Answer, result.answer_id)
        cites = list(session.scalars(select(m.AnswerCitation).where(m.AnswerCitation.answer_id == answer.id)))
        messages = list(
            session.scalars(
                select(m.Message)
                .where(m.Message.conversation_id == result.conversation_id)
                .order_by(m.Message.created_at)
            )
        )
        return answer, cites, messages


# ---------- the grounded answer ----------


def test_a_grounded_answer_is_persisted_with_its_trace(rag_env: RagEnv) -> None:
    result = ask(rag_env, "How long are TechCare laptops covered by the warranty?")
    assert (result.answer_status, result.grounded) == (m.AnswerStatus.ANSWERED, True)
    assert "12 months" in result.answer and "[1]" in result.answer
    citation = result.citations[0]
    assert (citation.title, citation.page_start) == ("TechCare Laptop Warranty", 1)
    assert citation.quote in "Laptops are covered for 12 months from the date of purchase."

    answer, cites, messages = stored(rag_env, result)
    assert [msg.role for msg in messages] == ["user", "assistant"]
    assert messages[0].content == "How long are TechCare laptops covered by the warranty?"
    assert messages[1].model_id == "fake-llm"
    assert (answer.intent, answer.retrieval_attempts, answer.request_id) == (
        "knowledge",
        1,
        result.request_id,
    )
    assert answer.graph_path == [
        "validate_request",
        "classify_intent",
        "retrieve_documents",
        "grade_evidence",
        "generate_answer",
        "check_citations",
        "persist",
    ]
    assert len(answer.retrieved_chunk_ids) >= 5 and citation.chunk_id in answer.retrieved_chunk_ids
    assert set(answer.timings) >= {"retrieve_documents", "generate_answer"}
    assert cites[0].chunk_id is not None and cites[0].quote == citation.quote and cites[0].page_start == 1


def test_a_follow_up_continues_the_same_conversation(rag_env: RagEnv) -> None:
    first = ask(rag_env, "What are the store hours on Sunday?")
    ctx = context(rag_env, conversation_id=first.conversation_id)
    second = service.ask(ctx, "How can I contact support by phone?")
    assert second.conversation_id == first.conversation_id
    assert len(stored(rag_env, second)[2]) == 4


def test_a_follow_up_that_points_back_is_answered_with_the_previous_question(rag_env: RagEnv) -> None:
    first = ask(rag_env, "What does error code E-04 mean on the DemoPrinter-X2?")
    assert first.answer_status.value == "answered"
    alone = ask(rag_env, "How do I fix it?")  # a new conversation: nothing to point back to
    assert alone.answer_status.value == "insufficient_evidence"

    follow = service.ask(context(rag_env, conversation_id=first.conversation_id), "How do I fix it?")
    assert follow.answer_status.value == "answered", follow.answer
    assert "cartridge" in follow.answer.lower()  # E-04's "Reinstall the cartridge until it clicks"
    assert {c.title for c in follow.citations} == {"DemoPrinter-X2 Troubleshooting Guide"}
    answer, _, _ = stored(rag_env, follow)
    assert "rewrite_query" in answer.graph_path and answer.retrieval_attempts == 2  # it used the one retry


def test_a_standalone_question_in_a_conversation_is_not_mixed_with_the_previous_one(rag_env: RagEnv) -> None:
    first = ask(rag_env, "What does error code E-04 mean on the DemoPrinter-X2?")
    second = service.ask(
        context(rag_env, conversation_id=first.conversation_id), "What are the store hours on Sunday?"
    )
    assert second.answer_status.value == "answered"
    assert {c.title for c in second.citations} == {"TechCare Store Hours and Contact"}
    answer, _, _ = stored(rag_env, second)
    assert "rewrite_query" not in answer.graph_path  # answered on its own; the previous turn was never used


# ---------- other routes ----------


@pytest.mark.parametrize(
    ("question", "intent", "reply"),
    [
        ("Hello!", "smalltalk", prompts.GREETING),
        ("Can I talk to a real person?", "support", prompts.SUPPORT_HANDOFF),
        ("Where is my order TC-123456?", "order", prompts.ORDER_VERIFY),  # the chat then shows the card
        ("   ", None, prompts.EMPTY_QUESTION),
    ],
)
def test_direct_replies_never_touch_retrieval(
    rag_env: RagEnv, question: str, intent: str | None, reply: str
) -> None:
    result = ask(rag_env, question)
    assert (result.answer_status, result.grounded, result.citations, result.answer) == (
        m.AnswerStatus.DIRECT,
        False,
        [],
        reply,
    )
    answer = stored(rag_env, result)[0]
    assert (
        answer.intent == intent
        and answer.retrieved_chunk_ids == []
        and "retrieve_documents" not in answer.graph_path
    )


def test_unknown_topics_rewrite_once_then_abstain(rag_env: RagEnv) -> None:
    result = ask(rag_env, "Do you sell electric bikes?")
    assert (result.answer_status, result.citations, result.answer) == (
        m.AnswerStatus.INSUFFICIENT_EVIDENCE,
        [],
        prompts.NOT_IN_DOCUMENTS,
    )
    answer = stored(rag_env, result)[0]
    assert answer.retrieval_attempts == 2 and answer.graph_path.count("rewrite_query") == 1
    assert answer.graph_path[-2:] == ["abstain", "persist"] and answer.grounded is False


# ---------- authorization (G5, G6) ----------


def test_employee_only_content_never_reaches_the_public(rag_env: RagEnv) -> None:
    for question in ["When can we waive the diagnostic fee?", f"What does {EMPLOYEE_CANARY} refer to?"]:
        public = ask(rag_env, question)
        assert public.answer_status is m.AnswerStatus.INSUFFICIENT_EVIDENCE
        assert EMPLOYEE_CANARY not in public.answer and "49 dollars" not in public.answer
        signed_in_customer = ask(rag_env, question, principal=rag_env.tenant.customer)
        assert signed_in_customer.answer_status is m.AnswerStatus.INSUFFICIENT_EVIDENCE
    staff = ask(rag_env, "When can we waive the diagnostic fee?", principal=rag_env.tenant.employee)
    assert staff.answer_status is m.AnswerStatus.ANSWERED
    assert staff.citations[0].title == "Repair Desk Procedures"


def test_an_unpublished_new_version_is_never_cited(rag_env: RagEnv) -> None:
    warranty = rag_env.documents["TechCare Laptop Warranty"]
    v2 = pdf_bytes(
        [
            [
                ("h1", "TechCare Laptop Warranty"),
                ("h2", "Coverage period"),
                ("p", "Laptops are covered for 36 months from the date of purchase."),
            ]
        ]
    )
    validated = validate_upload("warranty-v2.pdf", None, v2, rag_env.ctx.settings)
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        upload = docs.create_upload(
            session,
            rag_env.ctx.storage,
            rag_env.ctx.settings,
            rag_env.tenant.manager,
            validated,
            document_id=warranty.id,
        )
    _wait_for(rag_env.ctx, upload.job.id, 60)  # v2 is ready (indexed in Chroma) but not published
    result = ask(rag_env, "How long are TechCare laptops covered by the warranty?")
    assert "12 months" in result.answer and "36 months" not in result.answer
    assert all(c.version_id != upload.version.id for c in result.citations)


# ---------- failures ----------


class BrokenModel(FakeChatModel):
    def generate_answer(
        self, question: str, sources: Sequence[Source], audience: str, *, usage: TokenUsage | None = None
    ) -> GeneratedAnswer | None:
        raise ChatModelError("APITimeoutError")


class UnparseableModel(FakeChatModel):
    def generate_answer(
        self, question: str, sources: Sequence[Source], audience: str, *, usage: TokenUsage | None = None
    ) -> GeneratedAnswer | None:
        return None


class InventingModel(FakeChatModel):
    def generate_answer(
        self, question: str, sources: Sequence[Source], audience: str, *, usage: TokenUsage | None = None
    ) -> GeneratedAnswer | None:
        return GeneratedAnswer(
            answer="Laptops are covered for 99 years [S9].",
            answer_status="answered",
            citations=[CitedSource(source_id="S9", quote="99 years")],
            suggested_followups=[],
        )


def test_provider_failure_is_an_error_answer_and_the_question_is_saved(rag_env: RagEnv) -> None:
    result = ask(rag_env, "How long are TechCare laptops covered by the warranty?", llm=BrokenModel())
    assert (result.answer_status, result.answer, result.citations) == (
        m.AnswerStatus.ERROR,
        prompts.PROVIDER_FAILURE,
        [],
    )
    messages = stored(rag_env, result)[2]
    assert messages[0].content.startswith("How long are TechCare laptops")


def test_unparseable_output_abstains(rag_env: RagEnv) -> None:
    result = ask(rag_env, "How long are TechCare laptops covered by the warranty?", llm=UnparseableModel())
    assert result.answer_status is m.AnswerStatus.INSUFFICIENT_EVIDENCE and result.citations == []


def test_invented_citations_are_never_shown(rag_env: RagEnv) -> None:
    result = ask(rag_env, "How long are TechCare laptops covered by the warranty?", llm=InventingModel())
    assert result.answer_status is m.AnswerStatus.INSUFFICIENT_EVIDENCE
    assert "99 years" not in result.answer and result.citations == []
    assert "check_citations" in stored(rag_env, result)[0].graph_path


def test_a_request_past_its_deadline_errors_instead_of_hanging(rag_env: RagEnv) -> None:
    settings = rag_env.ctx.settings.model_copy(update={"request_deadline_seconds": 0.000001})
    result = ask(rag_env, "How long are TechCare laptops covered by the warranty?", settings=settings)
    assert result.answer_status is m.AnswerStatus.ERROR


# ---------- streaming (ADR-006) ----------


def test_stream_sends_progress_then_one_authoritative_final(rag_env: RagEnv) -> None:
    events = list(service.ask_stream(context(rag_env), "How do I clear a paper jam in the DemoPrinter-X2?"))
    names = [name for name, _ in events]
    assert names == ["status", "delta", "status", "final"]
    assert [p["stage"] for n, p in events if n == "status"] == ["searching", "checking"]
    final = events[-1][1]
    assert isinstance(final, AskResult) and final.answer_status is m.AnswerStatus.ANSWERED
    assert events[1][1]["text"].replace("[S1]", "[1]") == final.answer  # provisional text, then validated


def test_abstaining_streams_never_send_provisional_text(rag_env: RagEnv) -> None:
    names = [name for name, _ in service.ask_stream(context(rag_env), "Can I pay with cryptocurrency?")]
    assert "delta" not in names and names[-1] == "final"


def test_conversations_belong_to_their_session(rag_env: RagEnv) -> None:
    result = ask(rag_env, "What are the store hours on Sunday?")
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        conversation = session.get_one(m.Conversation, result.conversation_id)
        assert conversation.owner_user_id is None and conversation.session_ref is not None
        assert conversation.audience == "public"
        count = session.scalar(
            select(func.count())
            .select_from(m.AnswerCitation)
            .where(m.AnswerCitation.answer_id == result.answer_id)
        )
    assert count == len(result.citations)


# ---------- lifecycle and isolation (§12.2 integration list) ----------


def _publish(env: RagEnv, data: bytes, filename: str, **kw: object) -> docs.UploadResult:
    from app.services.versions import publish_version

    validated = validate_upload(filename, None, data, env.ctx.settings)
    with tenant_session(env.tenant.org.id, env.ctx.engine) as session:
        upload = docs.create_upload(
            session,
            env.ctx.storage,
            env.ctx.settings,
            env.tenant.manager,
            validated,
            allow_duplicate=True,
            **kw,  # type: ignore[arg-type]
        )
    _wait_for(env.ctx, upload.job.id, 60)
    with tenant_session(env.tenant.org.id, env.ctx.engine) as session:
        publish_version(session, env.ctx.index, env.tenant.manager, upload.document.id, upload.version.id)
    return upload


def test_a_published_update_is_the_only_version_cited(rag_env: RagEnv) -> None:
    hours = rag_env.documents["TechCare Store Hours and Contact"]
    v2 = b"# Store hours\nTechCare stores are open on Sunday from 10am to 7pm from November.\n"
    upload = _publish(rag_env, v2, "store-hours-v2.txt", document_id=hours.id)
    result = ask(rag_env, "What are the store hours on Sunday?")
    assert "10am" in result.answer and "11am" not in result.answer
    assert {c.version_id for c in result.citations} == {upload.version.id}


def test_a_deleted_document_is_no_longer_found(rag_env: RagEnv) -> None:
    from app.services.versions import soft_delete_document

    upload = _publish(
        rag_env, b"# Gift cards\nTechCare gift cards never expire and work in every store.\n", "gift.txt"
    )
    assert ask(rag_env, "Do TechCare gift cards expire?").answer_status is m.AnswerStatus.ANSWERED
    with tenant_session(rag_env.tenant.org.id, rag_env.ctx.engine) as session:
        soft_delete_document(session, rag_env.tenant.admin, upload.document.id)
    result = ask(rag_env, "Do TechCare gift cards expire?")
    assert result.answer_status is m.AnswerStatus.INSUFFICIENT_EVIDENCE and result.citations == []


def test_another_organization_never_gets_these_documents(rag_env: RagEnv) -> None:
    from tests.integration.conftest import make_tenant

    other = make_tenant(rag_env.ctx.engine)
    for principal in (Principal(other.org.id), other.employee):
        result = ask(rag_env, "How long are TechCare laptops covered by the warranty?", principal=principal)
        assert result.answer_status is m.AnswerStatus.INSUFFICIENT_EVIDENCE and result.citations == []


class BrokenIndex:
    """Stands in for Chroma being down."""

    def query(self, *args: object, **kwargs: object) -> list[object]:
        raise ConnectionError("chroma unreachable")


def test_an_unavailable_vector_store_gives_a_safe_error(rag_env: RagEnv) -> None:
    ctx = context(rag_env)
    broken = dataclasses.replace(ctx, index=BrokenIndex())  # type: ignore[arg-type]
    result = service.ask(broken, "How long are TechCare laptops covered by the warranty?")
    assert (result.answer_status, result.answer, result.citations) == (
        m.AnswerStatus.ERROR,
        prompts.PROVIDER_FAILURE,
        [],
    )
    assert "chroma" not in result.answer.lower()
