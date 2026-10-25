"""Gate definitions and reporting rules (docs/evaluation.md §2-§3), read from `evaluation/gates.yaml`.

Thresholds live only in gates.yaml. Ratios carry a 95% Wilson interval; zero-count gates carry the upper bound
of their rate. A gate is `pass`, `fail`, `not_run` or `no_target`, and a run on a dataset other than the gated
one (e.g. exploration queries) is reported but marked `gated: false`.
"""

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml

from app.config import REPO_ROOT

GATES_FILE = REPO_ROOT / "evaluation" / "gates.yaml"
BASELINES = REPO_ROOT / "evaluation" / "baselines"


def wilson(successes: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3)]


def load_gates(path: Path = GATES_FILE) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {"dataset": raw["dataset"], "gates": {g["id"]: g for g in raw["gates"]}}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _passes(value: float, comparator: str, target: float) -> bool:
    return {">=": value >= target, "==": value == target, "<=": value <= target}[comparator]


def ratio_gate(
    gate_id: str, metric: str, hits: int, n: int, failing: list[dict[str, Any]], spec: dict[str, Any]
) -> dict[str, Any]:
    value = hits / n if n else 0.0
    target = float(spec["target"])
    small = n < int(spec.get("min_sample", 0))
    return {
        "id": gate_id,
        "metric": metric,
        "status": "not_run"
        if n == 0
        else ("pass" if _passes(round(value, 6), spec["comparator"], target) else "fail"),
        "value": round(value, 3),
        "numerator": hits,
        "denominator": n,
        "ci95": wilson(hits, n),
        "target": target,
        "comparator": spec["comparator"],
        "below_min_sample": small,
        "failing_cases": failing,
    }


def count_gate(
    gate_id: str, metric: str, count: int, n: int, failing: list[dict[str, Any]], spec: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "metric": metric,
        "status": "not_run" if n == 0 else ("pass" if count == int(spec["target"]) else "fail"),
        "value": count,
        "denominator": n,
        "upper95": wilson(count, n)[1],  # with 0 failures in n: the true rate is below this, not zero
        "target": int(spec["target"]),
        "comparator": spec["comparator"],
        "below_min_sample": n < int(spec.get("min_sample", 0)),
        "failing_cases": failing,
    }


def baseline_path(dataset: str) -> Path:
    return BASELINES / f"{dataset}.json"


def apply_regression(dataset: str, gates: list[dict[str, Any]], specs: dict[str, Any]) -> None:
    """Merge rule (docs/evaluation.md §3): a drop beyond a gate's tolerance vs the baseline fails it even when
    the target is still met. The baseline is the last accepted run, committed under evaluation/baselines/."""
    path = baseline_path(dataset)
    if not path.exists():
        return
    baseline = json.loads(path.read_text(encoding="utf-8"))
    for gate in gates:
        spec = specs.get(gate["id"], {})
        tolerance = spec.get("regression_tolerance")
        previous = baseline.get(gate["id"])
        gate["baseline"] = previous
        if tolerance is not None and previous is not None and gate["value"] < previous - float(tolerance):
            gate["status"] = "fail"
            gate["regression"] = f"dropped from {previous} to {gate['value']} (tolerance {tolerance})"


def write_baseline(report: dict[str, Any]) -> Path:
    BASELINES.mkdir(parents=True, exist_ok=True)
    path = baseline_path(report["dataset"])
    values = {g["id"]: g["value"] for g in report["gates"] if g["status"] != "not_run"}
    path.write_text(
        json.dumps({"run_id": report["run_id"], "commit": report.get("commit"), **values}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
