"""G2 grounding audit sheet (docs/evaluation.md §2 G2): a sentence-level review of a random sample of answers.

    python -m app.evaluation.audit --report ../evaluation/reports/<run>.json --sample 30 \\
        --out ../evaluation/audits/<run>-g2.md

Each answered case is split into sentences; each sentence is pre-screened against the passages it cites
(`supported` when the sentence's content words all occur in a cited chunk's text). The pre-screen only orders
the work: G2 counts the *human* verdicts in the last column ("supported claims ÷ all factual claims ≥ 95%"),
never this script's guess. The sample is seeded by the run ID, so the same report always gives the same sheet.
"""

import argparse
import json
import random
import re
from pathlib import Path
from typing import Any

from app.evaluation.gates import wilson
from app.rag.text import content_terms

_CLAIM = re.compile(r"(.*?)((?:\s*\[\d+\])+)", re.DOTALL)  # "Sentence. [1][2]": the markers follow it


def claims(answer: str) -> list[tuple[str, list[int]]]:
    """Sentences of an answer with the citation numbers that follow them (unmarked text has none)."""
    out: list[tuple[str, list[int]]] = []
    end = 0
    for match in _CLAIM.finditer(answer):
        text = match.group(1).strip()
        if text:
            out.append((text, [int(n) for n in re.findall(r"\d+", match.group(2))]))
        end = match.end()
    tail = answer[end:].strip()
    if tail:
        out.append((tail, []))
    return out


def prescreen(sentence: str, refs: list[int], quotes: list[str]) -> str:
    cited = [quotes[r - 1] for r in refs if 0 < r <= len(quotes)]
    if not cited:
        return "no citation"
    words = content_terms(sentence)
    return "supported" if any(words <= content_terms(q) for q in cited) else "check"


def build_sheet(report: dict[str, Any], sample: int) -> str:
    answered = [c for c in report["cases"] if c["answer_status"] == "answered"]
    rng = random.Random(report["run_id"])  # noqa: S311 - a reproducible sample, not a secret
    chosen = sorted(rng.sample(answered, min(sample, len(answered))), key=lambda c: c["case_id"])
    lines = [
        f"# G2 grounding audit: {report['dataset']}, run {report['run_id']}",
        "",
        f"Sample: {len(chosen)} of {len(answered)} answered cases (seeded by the run ID). Models: "
        f"{report['config'].get('llm_model')} / {report['config'].get('embedding_model')}.",
        "",
        "Fill in **Verdict** for every claim: `supported` (the cited passage states it), `unsupported`, or "
        "`partial`. G2 = supported ÷ all claims, target ≥ 95% (gates.yaml). The pre-screen is only a hint.",
        "",
        "| Case | Claim | Cited passage | Pre-screen | Verdict |",
        "| --- | --- | --- | --- | --- |",
    ]
    total = 0
    for case in chosen:
        quotes: list[str] = case.get("passages") or case.get("quotes", [])
        for sentence, refs in claims(case["answer"]):
            total += 1
            cited = " / ".join(quotes[r - 1][:400] for r in refs if 0 < r <= len(quotes)) or "(none)"
            hint = prescreen(sentence, refs, quotes)
            lines.append(f"| {case['case_id']} | {_cell(sentence)} | {_cell(cited)} | {hint} | |")
    lines += ["", f"Claims to review: {total}. Reviewer: ______  Date: ______  Supported: ___ / {total}"]
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


VERDICTS = {"supported", "partial", "unsupported"}


def score(sheet: str) -> dict[str, Any]:
    """G2 from a filled-in sheet: only the reviewer's **Verdict** column counts, never a proposal."""
    lines = [line.strip() for line in sheet.splitlines() if line.startswith("| ")]
    rows = [re.split(r"(?<!\\)\|", line)[1:-1] for line in lines]
    if not rows:
        raise ValueError("no table in the sheet")
    header = [cell.strip() for cell in rows[0]]
    column = header.index("Verdict")
    broken = [row[0].strip() for row in rows[2:] if len(row) != len(header)]
    if broken:  # an unescaped "|" in a cell: counting around it would silently drop claims
        raise ValueError(f"rows with the wrong number of cells: {', '.join(broken)}")
    verdicts = [row[column].strip().strip("`").lower() for row in rows[2:]]
    unknown = sorted({v for v in verdicts if v and v not in VERDICTS})
    if unknown:
        raise ValueError(f"unknown verdicts: {', '.join(unknown)}")
    supported = verdicts.count("supported")
    filled = [v for v in verdicts if v]
    return {
        "claims": len(verdicts),
        "reviewed": len(filled),
        "supported": supported,
        "partial": filled.count("partial"),
        "unsupported": filled.count("unsupported"),
        "ci95": wilson(supported, len(filled)) if filled else None,
        "complete": len(filled) == len(verdicts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--score", type=Path, help="a filled-in sheet: print G2 from its Verdict column")
    args = parser.parse_args()
    if args.score:
        result = score(args.score.read_text(encoding="utf-8"))
        print(json.dumps(result))
        if not result["complete"]:
            raise SystemExit(f"{result['claims'] - result['reviewed']} claims have no verdict yet")
        return
    if not (args.report and args.out):
        parser.error("--report and --out are required to build a sheet")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_sheet(report, args.sample), encoding="utf-8")
    print("audit sheet:", args.out)


if __name__ == "__main__":
    main()
