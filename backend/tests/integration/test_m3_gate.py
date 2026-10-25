"""Milestone M3 gate (plan §15, docs/evaluation.md §5): the 15 hand-picked questions are answered from the
right source and the no-answer probes abstain, scored by the same runner that produces evaluation reports."""

import json
from pathlib import Path

import pytest

from app.evaluation.runner import Personas, evaluate
from app.rag.llm import FakeChatModel
from tests.integration.conftest import RagEnv

pytestmark = pytest.mark.integration

DATASET = Path(__file__).resolve().parents[3] / "evaluation" / "datasets" / "m3-handpicked.yaml"


def test_m3_gate_passes(rag_env: RagEnv, tmp_path: Path) -> None:
    assert rag_env.tenant.employee.user_id is not None
    report = evaluate(
        DATASET,
        Personas(rag_env.tenant.org.id, rag_env.tenant.employee.user_id),
        engine=rag_env.ctx.engine,
        index=rag_env.ctx.index,
        embedder=rag_env.ctx.embedder,
        llm=FakeChatModel(),
        settings=rag_env.ctx.settings,
    )
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2))
    failing = [c for c in report["cases"] if not c["passed"]]
    assert failing == [], json.dumps(failing, indent=2)

    gates = {g["id"]: g for g in report["gates"]}
    assert gates["G1"]["status"] == "pass" and gates["G1"]["denominator"] == 15
    assert gates["G3"]["status"] == "pass" and gates["G3"]["value"] == 0 and gates["G3"]["denominator"] == 6
    assert gates["G4"]["status"] == "pass" and gates["G4"]["numerator"] == gates["G4"]["denominator"] > 0
    assert report["m3_gate"]["status"] == "pass"
    assert gates["G1"]["ci95"][0] > 0.7  # reported with its interval, never as a bare percentage
