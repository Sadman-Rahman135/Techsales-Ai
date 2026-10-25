"""§12.1 dataset preparation and the evaluation tooling: golden-v1 is big and varied enough, frozen, labelled
against the real corpus; gates follow docs/evaluation.md; the audit sheet splits claims correctly."""

from collections import Counter
from pathlib import Path

import pytest
import yaml

from app.config import REPO_ROOT
from app.demo.golden_corpus import CANARIES, DOCUMENTS, FICTIONAL, NEW_DOCUMENTS
from app.evaluation import audit
from app.evaluation.gates import apply_regression, count_gate, load_gates, ratio_gate, sha256_of
from app.evaluation.runner import Case, Source, load_dataset, matches
from app.ingestion.parsing import parse_document

GOLDEN = REPO_ROOT / "evaluation" / "golden_questions" / "golden-v1.yaml"
FROZEN = GOLDEN.with_suffix(".sha256")
EXPLORATION = REPO_ROOT / "evaluation" / "exploration" / "dev-queries.yaml"


def test_golden_v1_is_frozen() -> None:
    # Any edit to golden-v1 must become golden-v2 (docs/evaluation.md §1): old results stay comparable.
    assert FROZEN.read_text().split()[0] == sha256_of(GOLDEN)


def test_golden_v2_is_frozen_and_only_relabels_golden_v1() -> None:
    v2 = GOLDEN.with_name("golden-v2.yaml")
    assert v2.with_suffix(".sha256").read_text().split()[0] == sha256_of(v2)
    _, _, old = load_dataset(GOLDEN)
    name, _, new = load_dataset(v2)
    assert name == "golden-v2" and all(c.normalized for c in new)
    assert [(c.id, c.question, c.persona, c.conversation) for c in old] == [
        (c.id, c.question, c.persona, c.conversation) for c in new
    ]  # same questions, same order: only expectations changed
    changed = {a.id for a, b in zip(old, new, strict=True) if (a.expect, a.facts) != (b.expect, b.facts)}
    assert changed == {"G025", "G028", "G069", "G070", "G076", "G135", "G136", "G139"}


def test_facts_accept_listed_phrasings_and_normalized_spacing() -> None:
    from app.evaluation.runner import has_fact

    assert has_fact("The fee is $79.", ("79 dollar", "$79"))
    assert not has_fact("The fee is 79 euros.", ("79 dollar", "$79"))
    assert not has_fact("comes with a 90-day warranty", "90 day")  # golden-v1: exact phrase only
    assert has_fact("comes with a 90-day warranty", "90 day", normalized=True)
    assert has_fact("turn them on by 8:45 am", "8:45am", normalized=True)


def test_golden_v1_meets_the_size_and_mix_of_section_12_1() -> None:
    dataset, k, cases = load_dataset(GOLDEN)
    assert (dataset, k) == ("golden-v1", 5) and len(cases) >= 120
    expect = Counter(c.expect for c in cases)
    category = Counter(c.category for c in cases)
    assert sum(1 for c in cases if c.expect == "answered" and c.sources) >= 80
    assert sum(1 for c in cases if c.category == "unanswerable") >= 25
    assert category["permission"] + category["follow_up"] >= 15
    for needed in (
        "positive",
        "unanswerable",
        "outdated",
        "multi_document",
        "ambiguous",
        "typo",
        "exact_code",
        "permission",
        "follow_up",
        "near_model",
    ):
        assert category[needed] > 0, needed
    assert expect["insufficient_evidence"] >= 25
    assert len({c.id for c in cases}) == len(cases)


def test_every_label_points_at_a_real_document_and_conversations_are_ordered() -> None:
    _, _, cases = load_dataset(GOLDEN)
    titles = {d.title for d in DOCUMENTS}
    for case in cases:
        for source in case.sources:
            assert source.title in titles, (case.id, source.title)
        if case.expect == "answered":
            assert case.sources or case.category == "multi_document", case.id
    threads = Counter(c.conversation for c in cases if c.conversation)
    assert threads and all(n >= 2 for n in threads.values())  # a follow-up always has something to follow


def test_the_corpus_is_fictional_varied_and_canaried() -> None:
    assert 20 <= len(DOCUMENTS) <= 30
    assert {d.filename.rsplit(".", 1)[1] for d in DOCUMENTS} == {"pdf", "docx", "txt"}
    for document in NEW_DOCUMENTS:  # what the parser extracts, so compressed PDF and Word files count too
        text = _text_of(document.filename, document.build())
        assert FICTIONAL in text, document.title
        if document.title in CANARIES:
            assert document.audience.value == "employee" and CANARIES[document.title] in text
    assert any(d.revisions for d in DOCUMENTS)  # an outdated version to test against


def _text_of(filename: str, data: bytes) -> str:
    kind = filename.rsplit(".", 1)[1]
    parsed = parse_document(data, kind)  # type: ignore[arg-type]
    return " ".join(segment.text for segment in parsed.segments)


def test_exploration_queries_are_never_gated() -> None:
    dataset, _, cases = load_dataset(EXPLORATION)
    assert dataset != load_gates()["dataset"] and cases


# ---------- gates ----------


def test_gate_thresholds_come_from_gates_yaml() -> None:
    specs = load_gates()["gates"]
    g1 = ratio_gate("G1", "recall_at_5", 72, 80, [], specs["G1"])
    assert (g1["status"], g1["value"], g1["ci95"]) == ("pass", 0.9, [0.815, 0.948])
    assert ratio_gate("G1", "recall_at_5", 71, 80, [], specs["G1"])["status"] == "fail"
    g5 = count_gate("G5", "unauthorized_exposure_count", 0, 60, [], specs["G5"])
    assert g5["status"] == "pass" and g5["upper95"] == pytest.approx(0.06, abs=0.001)
    assert count_gate("G5", "x", 0, 0, [], specs["G5"])["status"] == "not_run"  # didn't run is never a pass
    assert count_gate("G5", "x", 0, 10, [], specs["G5"])["below_min_sample"] is True


def test_a_drop_beyond_the_tolerance_fails_even_above_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.evaluation import gates

    monkeypatch.setattr(gates, "BASELINES", tmp_path)
    (tmp_path / "golden-v1.json").write_text('{"G1": 1.0}')
    specs = load_gates()["gates"]
    run = [ratio_gate("G1", "recall_at_5", 95, 100, [], specs["G1"])]
    apply_regression("golden-v1", run, specs)
    assert run[0]["status"] == "fail" and "dropped from 1.0" in run[0]["regression"]
    ok = [ratio_gate("G1", "recall_at_5", 98, 100, [], specs["G1"])]
    apply_regression("golden-v1", ok, specs)
    assert ok[0]["status"] == "pass"


def test_matching_respects_page_and_version() -> None:
    case = Case("X", "customer", "q", sources=(Source("Price Match Policy", 1, 2),))
    assert matches(("Price Match Policy", 1, 1, 2), case)
    assert not matches(("Price Match Policy", 1, 1, 1), case)  # the outdated version doesn't count
    assert not matches(("Price Match Policy", 2, 2, 2), case)


# ---------- G2 audit sheet ----------


def test_claims_keep_their_citation_numbers() -> None:
    assert audit.claims("Turn it off. [1] Open the door. [1][2] No source here.") == [
        ("Turn it off.", [1]),
        ("Open the door.", [1, 2]),
        ("No source here.", []),
    ]


def test_prescreen_flags_claims_the_passage_does_not_contain() -> None:
    passage = "Laptops are covered for 12 months from the date of purchase."
    assert audit.prescreen("Laptops are covered for 12 months.", [1], [passage]) == "supported"
    assert audit.prescreen("Laptops are covered for 36 months.", [1], [passage]) == "check"
    assert audit.prescreen("Laptops are covered.", [], [passage]) == "no citation"


def test_g2_counts_only_the_reviewers_verdicts() -> None:
    sheet = (
        "| Case | Claim | Cited passage | Pre-screen | Proposed | Verdict |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| G1 | A \\| B. | A \\| B | supported | supported | supported |\n"
        "| G2 | C. | C | check | supported | partial |\n"
        "| G3 | D. | D | supported | supported | |\n"
    )
    result = audit.score(sheet)
    assert result["claims"] == 3 and result["reviewed"] == 2 and not result["complete"]
    assert result["supported"] == 1 and result["partial"] == 1  # the proposal column never counts
    with pytest.raises(ValueError, match="unknown verdicts"):
        audit.score(sheet.replace("| partial |", "| maybe |"))
    with pytest.raises(ValueError, match="wrong number of cells"):
        audit.score(sheet.replace("C. | C |", "C. | C | C |"))


def test_dataset_files_parse_as_yaml() -> None:
    for path in (GOLDEN, EXPLORATION, REPO_ROOT / "evaluation" / "datasets" / "m3-handpicked.yaml"):
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]


def test_the_load_run_parses_server_sent_events_and_summarizes_by_phase() -> None:
    from app.evaluation import load

    stream = [
        "event: meta",
        'data: {"request_id": "req-1"}',
        "",
        "event: delta",
        'data: {"text": "Hi"}',
        "",
        "event: final",
        'data: {"answer_status": "answered"}',
        "",
    ]
    assert [e for e, _ in load.events(iter(stream))] == ["meta", "delta", "final"]
    samples = [
        load.Sample("sequential", f"G{i}", 200, "answered", f"r{i}", first_token_ms=10 * i, full_ms=20 * i)
        for i in range(1, 11)
    ] + [load.Sample("sequential", "G99", 429, "http_error")]
    summary = load.summarize(samples, wall_seconds=2.0)
    assert summary["errors"] == 1 and summary["http_status"] == {"200": 10, "429": 1}
    assert summary["full_ms"] == {"p50": 110, "p95": 191, "n": 10} and summary["throughput_rps"] == 5.5
    assert all(c.persona == "customer" and c.conversation is None for c in load.questions())
