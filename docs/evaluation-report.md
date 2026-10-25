# Evaluation report (release candidate, 2026-10-20)

For reviewers deciding whether KnowledgeAI is ready to put in front of people. Quality numbers, what failed, and
what these numbers can't tell you yet. Gate definitions: [evaluation.md](evaluation.md); thresholds:
[evaluation/gates.yaml](../evaluation/gates.yaml); detailed evidence: [demos/12-evaluation.md](demos/12-evaluation.md),
[demos/14-15-repository-release.md](demos/14-15-repository-release.md).

## 1. Read this first

- **Models:** §2–§4 use the offline substitutes: an extractive "model" that quotes the best sentences of the
  sources, and a hashing embedder. They measure the **pipeline** (retrieval, authorization, versions, citation
  checks, refusals). §4a is the same golden run with the chosen local models (ADR-026: Qwen3-4B-Instruct-2507
  and nomic-embed-text-v1.5 through llama.cpp, no paid API). That is what users get.
- **Data:** golden-v1, 142 hand-labelled questions over 21 fictional documents, frozen (sha256) since the
  baseline. Fixes were found on separate exploration questions; where a golden case was a known failure before a
  fix, its gain is marked as seen (ADR-022).
- **Report:** `evaluation/reports/golden-v1-release.json` (run with
  `python -m app.evaluation.runner … --suites core,access,lifecycle`, see README). Intervals are 95% Wilson
  intervals; for zero counts, the upper bound.

## 2. Gates

| Gate | What it checks | Result | 95% interval | Target | Status |
| --- | --- | --- | --- | --- | --- |
| G1 Retrieval | The right page is in the top 5 for answerable questions | 98 / 98 | 0.962–1.000 | ≥ 0.90 | pass |
| G2 Grounding | Claims supported by their cited passage (human audit) | 59 claims sampled; pre-screen finds all 59 | — | ≥ 95% | **not run**: needs a person to fill in `evaluation/audits/golden-v1-baseline-g2.md` |
| G3 Invented answers | Unanswerable questions that get an invented policy | 0 / 36 | upper 0.096 | 0 | pass |
| G4 Valid citations | Citations that point at a retrieved, authorized chunk with a verbatim quote | 109 / 109 | 0.966–1.000 | 1.00 | pass |
| G5 Access | Employee-only content reaching a customer (96 probes: 11 questions × 4 tricks × 2 personas, plus direct IDs) | 0 / 96 | upper 0.038 | 0 | pass |
| G6 Versions | Answers citing a replaced, draft or deleted version (12 lifecycle scenarios) | 0 / 12 | upper 0.243 | 0 | pass |
| G9 Speed | Latency (no target agreed yet) | see §4 | — | proposed in evaluation.md | recorded |

G6's upper bound is wide because 12 scenarios is a small sample; what is gated is the count (0).

## 3. Answer quality by category

95 of 98 answerable questions are fully right (all expected facts, from the expected source). By category:

| Category | Passed | Category | Passed |
| --- | --- | --- | --- |
| positive | 65 / 67 | multi-document | 4 / 4 |
| unanswerable | 26 / 26 | ambiguous | 5 / 5 |
| permission | 8 / 8 | follow-up | 8 / 8 |
| exact error code | 7 / 7 | typo | 6 / 6 |
| near-identical model | 6 / 6 | direct replies | 3 / 3 |
| outdated (replaced policy) | 1 / 2 | | |

**The three failures, and why:**

| Case | Question (short) | What happened | Cause |
| --- | --- | --- | --- |
| G036 | What condition must a trade-in device be in? | Answered from the right document, but quoted the credit table ("Tablet: 200 dollars …") instead of "must power on" | The extractive substitute picks sentences by word overlap; a real model reads the passage |
| G051 | What color is the Smart Hub ring light when it's ready? | "Not in our documents" | The document says "turn white": no shared word for "color"; a real embedder relates them |
| G082 | How long after purchase can a customer ask for a price match? | "Not in our documents" | "customer", "ask", "how long" aren't in the policy text, so the evidence check is below its threshold |

All three fail safe: two refuse, and one answers from the right document but misses the point. None states
anything the documents don't say.

## 4. Speed and cost

| Measure | p50 | p95 | Sample |
| --- | --- | --- | --- |
| Full answer, golden-v1 run (in process) | 26 ms | 43 ms | 139 answers |
| Retrieval step | 12 ms | 14 ms | 139 |
| Over HTTP, sequential: first token / full answer | 24 / 37 ms | 26 / 47 ms | 200 requests |
| Over HTTP, 20 concurrent: first token / full answer | 472 / 782 ms | 659 / 907 ms | 100 requests, 24.7 req/s, 0 errors |

Inside the proposed SLO (first token p95 ≤ 3 s, full answer p95 ≤ 8 s), but without a real model's time. Cost per
answered question: about 437 prompt, 102 answer and 12 embedding tokens, one model call; multiply by the chosen
provider's prices. (`evaluation/reports/load-m7.json`; Docker on one laptop.)

## 4a. The chosen local models (ADR-026)

The same 142 golden-v1 cases, unchanged, with the real models on the laptop's RTX 4050
(`evaluation/reports/golden-v1-local.json`, run `eval-20261020-065832-4118f3`):

| Gate | Result | 95% interval | Status |
| --- | --- | --- | --- |
| G1 Retrieval | 98 / 98 | 0.962–1.000 | pass |
| G3 Invented answers | 0 / 36 | upper 0.096 | pass |
| G4 Valid citations | 103 / 103 | 0.964–1.000 | pass |
| G5 Access | 0 / 96 | upper 0.038 | pass |
| G6 Versions | 0 / 12 | upper 0.243 | pass |
| G2 Grounding | 30 claims sampled (`evaluation/audits/golden-v1-local-g2.md`); pre-screen: 24 supported, 6 to check | — | **not run**: needs a person |

**Answers:** 89 of 98 answerable questions contain every expected fact word for word (substitutes: 95). All 36
unanswerable questions are refused. The 9 misses:

| Cases | What the model said | Verdict |
| --- | --- | --- |
| G025, G136 | "The service fee … is $79." (expected "79 dollar") | right, different wording |
| G028 | "online marketplaces are not included" (expected "excluded") | right, different wording |
| G061 | "by 8:45 am" (expected "8:45am") | right, different wording |
| G069, G070, G076 | "the fuser is overheated", "the scanner lid is open", "the duplex unit is not detected" | right, different wording |
| G049 | "Yes, you should back up your phone before a screen repair." | incomplete: leaves out the reset step |
| G082 | "Not in our documents" | refused; the evidence check stays below its threshold, as with the substitutes |

So 7 of the 9 are scorer artefacts (the scorer wants the expected phrase verbatim; golden-v2 should accept
paraphrases), one is incomplete and one fails safe. None states anything the documents don't say.

**Two faults found by this run, fixed before it** (ADR-026):
- The prompt never said when an answer counts as "answered", so the first run refused everything.
- The model doesn't put markers in its sentences, so the validator now marks the answer with its validated
  citations.

**Speed** (sequential, in process): full answer p50 1.86 s and p95 2.82 s (139 answers); retrieval p50 21 ms and
p95 39 ms. Both are inside the proposed SLO (full answer p95 ≤ 8 s). A CPU-only VM does about 8 tokens/s, i.e.
15–30 s per answer, which is outside the SLO; deployment.md §1a raises the time limits for it. **Cost:** $0 per
question.

## 4b. Retrieval upgrades (ADR-028, P1)

Keyword search for codes, an optional local reranker, follow-ups with the previous question, filters, and
two-part questions. Tuned on `evaluation/exploration/` (49 cases): right answers went from 16 / 29 to 24 / 29 with
12 / 12 refusals.

Checked once on the golden sets:

| Run | G1 | G3 | G4 | G5 | G6 | Right answers | Refusals |
| --- | --- | --- | --- | --- | --- | --- | --- |
| golden-v1 before | 98 / 98 | 0 / 36 | 103 / 103 | 0 / 96 | 0 / 12 | 88 / 98 | 36 / 36 |
| golden-v1 after | 98 / 98 | **1 / 36** | 105 / 105 | 0 / 96 | 0 / 12 | 89 / 98 | 35 / 36 |
| golden-v2 after | 100 / 100 | 0 / 34 | 105 / 105 | — | — | 97 / 100 | 34 / 34 |

golden-v1's one G3 count is G135. "And how long does it take?" (after a screen repair question) is now answered
"the same day" from the repair document. That is right; golden-v1 labelled it "must refuse" because P0 had no
conversation history. golden-v2 corrects that label and accepts listed paraphrases; golden-v1 stays frozen and
still passes in CI with the offline models.

Remaining golden-v2 misses:
- G049: incomplete.
- G068: a paraphrase v2 doesn't list.
- G139: a follow-up, still refused.

Full answer p50 1.9 s, p95 3.0 s. Details and the benchmark (chunk size, candidates, context: no difference on this
corpus): [demos/p1-retrieval.md](demos/p1-retrieval.md).

## 5. Robustness and security checks

- Prompt injection: system-prompt extraction (3 probes × 2 roles) refused; instructions inside a published PDF
  are quoted as data, never followed; forged source tags escaped (`test_adversarial`).
- Contradictory sources are never blended into a third value; detecting the conflict is P1.
- Unknown error codes (e.g. "H9") are refused instead of answered with a neighbouring code (ADR-024).
- Threat model with a test per control: [threat-model.md](threat-model.md). Deployment rehearsal (TLS everywhere,
  ClamAV, separate DB logins): 12 of 12 checks ([deployment.md](deployment.md) §5).

## 6. Limitations

1. **A small local model.** It paraphrases (7 scorer misses in §4a) and once left out a step. On a CPU-only
   host it is slow (15–30 s). The red-team suite passes 5 / 5 against it (docs/demos/19-red-team-local-model.txt); the
   concurrent load test still has to be re-run on it.
2. **G2 needs a human.** The grounding audit sheet is ready; until someone fills it in, grounding is not
   reported as a number.
3. **The sets are small.** 98 answerable questions, 36 unanswerable, 12 lifecycle scenarios: the intervals above
   are the honest uncertainty. golden-v2 should add paraphrases and real customer phrasings.
4. **English only, text PDFs only.** Scans are rejected with a message; Bangla and OCR are P1.
5. **Not built (P1/P2):** order lookup and tickets, hybrid search and reranking, conflict detection, answer
   caching (README "Not built").
