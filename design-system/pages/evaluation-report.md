# Evaluation report: page overrides (§3 gates, personas: support manager, developer)

Route: `/admin/quality/evaluation` (a tab next to the quality dashboard) and `/admin/quality/evaluation/[runId]`.
Reads `evaluation/reports/<run_id>.json` + `evaluation/gates.yaml`. Spec: [docs/evaluation.md](../../docs/evaluation.md).

## Hierarchy (top → bottom)
1. **Run header:** run ID, commit, dataset version, date (tenant timezone), model config (collapsed), and a run selector
   (one filter row, above everything). Next to it, a **verdict banner** with icon + text: "7 of 10 gates pass · 2 fail · 1 not run".
   The banner is the hero; no hero number.
2. **Failing gates first**, then not-run / no-target, then passing. Order within a group follows G1→G10.
3. **Trends:** small multiples (one chart per ratio gate) with the last N runs.
4. **Failing cases** table for the selected gate (case ID, question excerpt, expected vs got, trace link).

## Gate row (the core component), by form
| Gate kind | Form | Content |
| --- | --- | --- |
| Ratio (G1, G2, G4) | **Meter**: track `--mark-track`, fill `--mark-pass` or `--mark-fail`, 2px target marker `--mark-target` with a "Target 90%" label | Value as text `73 / 80 · 91.3%`, `95% CI 83–96%`, delta vs main (signed, with arrow icon) |
| Zero-tolerance (G3, G5, G6, G7) | **Stat** (no chart: the number is the chart) | `0 leaks in 64 probes` + `< 4.7% at 95% confidence` |
| Pass/fail check (G8, G10) | **Stat** | `Pass · 22 min` or `Fail · step 4 of 9` |
| No target yet (G9) | **Stat** with neutral `CircleDashed` | `p95 full answer 4.6 s` + "SLO not agreed yet" |

Every row: status badge (icon + "Pass" / "Fail" / "Not run" / "No target"), gate name, plain-language one-liner
("Finds the right page for answerable questions"), "Blocks: merge · release" chips, chevron to expand the failing cases.
Rows are buttons (≥ 44px tall, keyboard-expandable, `aria-expanded`).

## Trend small multiples
- One line per chart (`--mark-series`), 2px, an end-dot with the latest value direct-labelled, and a target reference as a
  1px solid `--fg-muted` hairline labelled "Target". No legend box (single series; the title names it).
- Y-axis clamped to a sensible window (e.g. 70–100%) with that range **stated in the axis labels**, so differences aren't exaggerated without notice.
- Crosshair + tooltip on hover **and** keyboard focus (arrow keys move between runs). Every chart has a "Table" toggle.

## States
- No runs yet: "No evaluation runs yet. Runs appear here after `make eval` or CI on main."
- A run with missing gates: those gates show "Not run". Never inferred as a pass.
- Loading: keep the previous render at reduced opacity (no skeleton flash on refetch).
