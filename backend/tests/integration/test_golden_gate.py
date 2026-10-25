"""golden-v1 merge gates (blueprint §12, docs/evaluation.md §2): the frozen 142-case set, the generated
access probes and the lifecycle scenarios, scored by the same runner as `python -m app.evaluation.runner`.
Every gate that blocks a merge must pass, with its minimum sample, and G1 may not drop past its tolerance vs
the baseline.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.config import REPO_ROOT
from app.evaluation.gates import apply_regression, load_gates
from app.evaluation.lifecycle import run_lifecycle
from app.evaluation.probes import run_access_probes
from app.evaluation.runner import Personas, evaluate
from app.rag.llm import FakeChatModel
from tests.integration.conftest import RagEnv

pytestmark = pytest.mark.integration

GOLDEN = REPO_ROOT / "evaluation" / "golden_questions" / "golden-v1.yaml"


def test_golden_v1_merge_gates(golden_env: RagEnv, tmp_path: Path) -> None:
    tenant = golden_env.tenant
    assert tenant.employee.user_id and tenant.customer.user_id
    personas = Personas(tenant.org.id, tenant.employee.user_id, tenant.customer.user_id)
    deps: dict[str, Any] = {
        "engine": golden_env.ctx.engine,
        "index": golden_env.ctx.index,
        "embedder": golden_env.ctx.embedder,
        "llm": FakeChatModel(),
        "settings": golden_env.ctx.settings,
    }
    report = evaluate(GOLDEN, personas, **deps)
    report["gates"].append(run_access_probes(personas, report["run_id"], **deps))
    report["gates"].append(
        run_lifecycle(golden_env.ctx, tenant.manager, tenant.admin, personas, report["run_id"], **deps)
    )
    specs = load_gates()["gates"]
    apply_regression("golden-v1", report["gates"], specs)
    (tmp_path / "golden.json").write_text(json.dumps(report, indent=2))

    assert report["gated"] is True
    for gate in report["gates"]:
        if "merge" in specs[gate["id"]]["blocking"]:
            assert gate["status"] == "pass", json.dumps(gate, indent=2)[:3000]
            assert not gate["below_min_sample"], gate["id"]
    gates = {g["id"]: g for g in report["gates"]}
    assert gates["G1"]["denominator"] >= 80 and gates["G3"]["denominator"] >= 25
    assert gates["G5"]["denominator"] >= 60 and gates["G6"]["denominator"] >= 12
    assert report["latency"]["status"] == "no_target" and report["latency"]["full_answer_ms"]["n"] > 100
