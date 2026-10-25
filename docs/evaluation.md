# KnowledgeAI evaluation and acceptance gates (blueprint §3, detailed)

Makes every row of [IMPLEMENTATION_PLAN.md §3](IMPLEMENTATION_PLAN.md#3-success-criteria--acceptance-gates) precisely measurable:
what is counted, on which data, by which test, when it runs, and what blocks a merge or a release.
Machine-readable thresholds live in [`evaluation/gates.yaml`](../evaluation/gates.yaml). The report UI is specified in
[`design-system/pages/evaluation-report.md`](../design-system/pages/evaluation-report.md).

> These are **targets for a controlled project evaluation, not performance claims** (§3). Every reported number carries
> its sample size and dataset version. A result is never quoted without them.

## 1. Datasets

| Set | Contents | Minimum size | Source |
| --- | --- | --- | --- |
| `golden-v1` (regression) | Labeled QA cases: question, persona, expected answer facts, relevant `document_id`/`version_id`/page(s) | **120 cases** (§12.1): ≥ 80 answerable, ≥ 25 unanswerable, ≥ 15 permission/follow-up | `evaluation/golden_questions/` |
| `negative-access` | Persona × audience × tenant probes, including prompt tricks and direct-ID requests (§12.3, §12.4) | ≥ 60 probes | Generated from the permission matrix in `product-vision.md` |
| `lifecycle` | Document v(n) → v(n+1) publish, archive, delete scenarios | ≥ 12 scenarios | Integration fixtures |
| `fault-injection` | Failures injected at parse, chunk, embed, upsert, commit | Every stage × 2 retries | Integration fixtures |
| `exploration` | Free-form dev queries | Unbounded | **Never** used for gate numbers |

Rules: `golden-v1` is frozen once M3 starts. Changes create `golden-v2`, and old results stay comparable to their own version.
`golden-v2` exists since 2026-10-20 (ADR-028): the same 142 questions with label corrections only.
- The two follow-ups expect answers, now that follow-ups use the previous question.
- Listed paraphrases are accepted.
- `scoring: normalized` ignores case, hyphens and spacing.

golden-v1 still gates CI with the offline models; golden-v2 measures the local models.
All content is fictional TechCare data (§18). **Canary strings** (unique tokens such as `CANARY-EMP-7Q2`) are planted in every
employee-only document, so a leak can be detected mechanically in any output.

## 2. Gate specifications

`Blocking` says what a failure stops: **merge** (CI on every PR touching backend/RAG code), **release** (M7 / Definition of Done),
or **report** (tracked, not blocking).

| # | Gate | Metric (exact definition) | Target | Data | Blocking |
| --- | --- | --- | --- | --- | --- |
| G1 | **Retrieval** | Recall@5 = answerable cases where ≥ 1 labeled relevant chunk (same document **and** active version, overlapping page) is in the top-5 retrieved, ÷ answerable cases | **≥ 90%** | `golden-v1` answerable (n ≥ 80) | merge (no drop > 3 pts vs main), release (≥ 90%) |
| G2 | **Grounding** | Supported claims ÷ all factual claims, in a manual sentence-level audit of a random sample of answered cases. An LLM judge may pre-screen but never replaces the human audit | **≥ 95%** | ≥ 30 answers per audit | release |
| G3 | **Abstention** | Count of unanswerable cases where the answer states a policy/fact not present in any document. `answer_status` must be `insufficient_evidence` | **0** | `golden-v1` unanswerable (n ≥ 25) | merge |
| G4 | **Citation validity** | Citations whose `chunk_id` ∈ (retrieved set ∩ chunks authorized for the caller) ÷ all citations | **100%** | Every eval + integration run | merge |
| G5 | **Access control** | Count of probes where an unauthorized chunk appears in retrieved context, citations, answer text (canary match) or `preview_url` | **0** | `negative-access` (n ≥ 60) | merge |
| G6 | **Document lifecycle** | Count of answers citing a non-active version after publish/archive/delete | **0** | `lifecycle` (n ≥ 12) | merge |
| G7 | **Reliability** | Count of duplicate or orphaned chunks after an injected failure + retry (Chroma IDs vs Postgres chunk table) | **0** | `fault-injection` | merge |
| G8 | **Operability** | A clean machine reaches "services healthy + sign-in works" by following only the README | **Pass**, ≤ 30 min | Fresh VM / container, run by someone other than the author | release |
| G9 | **Performance** | p50/p95 of retrieval latency, time to first token, full response; hardware and provider stated | **Record now; SLO to agree** (proposal below) | 200 sequential + 20 concurrent requests from `golden-v1` | report → release once agreed |
| G10 | **Accessibility** *(proposed, not in §3)* | Serious/critical axe-core violations on every P0 route in light + dark, plus a manual keyboard pass | **0** | Playwright + `@axe-core/playwright` | merge |

### Proposed performance SLO (G9), for agreement at M5
| Measure | p50 | p95 | Notes |
| --- | --- | --- | --- |
| Retrieval (embed + vector search + filters) | ≤ 300 ms | ≤ 800 ms | Local Chroma, one org |
| Time to first streamed token | ≤ 1.5 s | ≤ 3 s | What the customer actually perceives (§10.1 streaming) |
| Full answer | ≤ 4 s | ≤ 8 s | Includes evidence grading; rewrite path counted |

These are a starting proposal sized to the UI's "Searching…" state. Record the provider, model and hardware with every run.

**Latest run** (2026-10-20, `python -m app.evaluation.load`, report `evaluation/reports/load-m7.json`; offline substitute
models; API, worker and data services in Docker on one laptop): 200 sequential requests, retrieval p50/p95 9/10 ms,
first token 24/26 ms, full answer 37/47 ms; 100 requests at 20 concurrent, retrieval 119/232 ms, first token
472/659 ms, full answer 782/907 ms, 24.7 req/s; 0 errors. Inside the proposal, but the model's time isn't in it. The local model's sequential full answer is p50 1.9 s and
p95 2.8 s on the RTX 4050 (evaluation-report.md §4a); the concurrent load test is still to be re-run on it. How to run: `docs/operations.md` §11.

## 3. Reporting rules

- **Counts before percentages:** "72 / 80 (90.0%)", never "90%" alone.
- **Uncertainty:** ratio gates show a 95% Wilson interval. With n = 80, an observed 90% is really [81.5%, 94.8%].
  Zero-count gates show the rule-of-three bound: 0 leaks in 60 probes means the true rate is < 5% with 95% confidence, *not* "no leaks possible".
- **Gate status:** `pass` (meets target) · `fail` (misses target) · `not_run` (no result for this commit) · `no_target` (G9 until agreed).
  A gate that didn't run is **not** a pass.
- **Regression:** each run is compared with the last run on `main`. A drop beyond the gate's tolerance fails the merge even if the target is still met.
- **Failures are linked:** every failing case lists its `case_id`, the request/trace IDs and the offending chunk IDs, so a reviewer can open it.

## 4. Run artifacts

Each run writes `evaluation/reports/<run_id>.json`. The report UI reads only this file:

```json
{
  "run_id": "eval-2026-11-20-a1b2c3",
  "commit": "a1b2c3d",
  "dataset": "golden-v1",
  "started_at": "2026-11-20T15:04:00Z",
  "config": {"llm_model": "<configured>", "embedding_model": "<configured>", "top_k": 5, "hardware": "<describe>"},
  "gates": [
    {"id": "G1", "status": "pass", "value": 0.913, "numerator": 73, "denominator": 80,
     "ci95": [0.83, 0.96], "target": 0.90, "comparator": ">=", "baseline": 0.90, "failing_cases": []},
    {"id": "G5", "status": "pass", "value": 0, "denominator": 64, "upper95": 0.047, "target": 0, "comparator": "==", "failing_cases": []}
  ]
}
```

## 5. When each gate first becomes enforceable

| Milestone | Gates switched on |
| --- | --- |
| M1 | G8 (draft README run) |
| M2 | G7 |
| M3 | G1, G3, G4 (M3 gate: 15 hand-picked questions, §15): `evaluation/datasets/m3-handpicked.yaml`, run with
`python -m app.evaluation.runner --dataset ../evaluation/datasets/m3-handpicked.yaml [--load-corpus] --out <report>` |
| M4 | G10 |
| M5 | G2, G5, G6; G9 SLO agreed |
| M7 | All gates at full targets on the release candidate = Definition of Done (§18) |
