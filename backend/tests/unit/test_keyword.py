"""Reciprocal rank fusion and the keyword query (ADR-028); the search itself is tested against Postgres."""

from app.integrations.chroma import RetrievedChunk
from app.rag import keyword


def hit(chroma_id: str, text: str | None = None, distance: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(chroma_id, text or f"text of {chroma_id}", {}, distance)


def test_fusion_rewards_agreement_and_brings_in_keyword_only_hits() -> None:
    vector = [hit("a"), hit("b"), hit("c")]
    fused = keyword.fuse(vector, ["c", "k"], [hit("k")], k=60, limit=10)
    # "c" is in both lists, so it beats "a" (vector rank 1 only). The keyword-only "k" (keyword rank 2) ties
    # with "b" (vector rank 2); ties go by distance, then ID.
    assert [h.chroma_id for h in fused] == ["c", "a", "b", "k"]


def test_fusion_drops_near_duplicates_and_keeps_the_limit() -> None:
    vector = [hit("a", "Standard delivery takes 3 to 5 business days."), hit("b")]
    copy = hit("x", "Standard delivery takes 3 to 5 business days. ")  # same text in another document
    fused = keyword.fuse(vector, ["x", "y"], [copy, hit("y")], k=60, limit=2)
    assert [h.chroma_id for h in fused] == ["a", "b"]  # "x" repeats "a" word for word, so it is skipped


def test_keyword_ids_the_caller_cannot_fetch_are_ignored() -> None:
    # An ID the Principal-filtered fetch didn't return (another audience, an old version) never gets in.
    fused = keyword.fuse([hit("a")], ["secret", "a"], [], k=60, limit=10)
    assert [h.chroma_id for h in fused] == ["a"]


def test_a_question_without_content_words_has_no_keyword_query() -> None:
    assert keyword.keyword_query("what is it?") is None
    assert keyword.keyword_query("E-04 on the X2") is not None


def test_keyword_search_runs_only_for_questions_naming_a_code() -> None:
    assert keyword.names_a_code("P-11 on my X2 Pro printer?")
    assert keyword.names_a_code("hub shows H-01")
    assert not keyword.names_a_code("When must the demo devices be turned on at opening?")  # golden-v1 G128
    assert not keyword.names_a_code("Is delivery free over 50 dollars?")  # a number alone is not a code
