# Evidence: §12 testing and RAG evaluation (2026-10-20)

Every §12 checkbox, with what was built, how it is tested and what is still open. Decision: ADR-022 (golden-v1 frozen,
gates from `evaluation/gates.yaml`, regression baseline). Gate definitions: `docs/evaluation.md`.

**Read the numbers with this in mind:** every run uses the offline substitute models (`fake:fake-llm`, an extractive
model, and `fake:fake-embedding`, a hashing embedder). The runs measure the pipeline (retrieval, authorization, version
handling, citation validation, abstention), not answer quality. They are rerun on the real provider once §19 picks it.

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **467 passed** (0 skipped, 2 min 22 s). `ruff`, `ruff format --check`, `mypy --strict` clean.
Frontend: `npm run lint`, `npm run typecheck`, `npm test` (14) and `npm run build` pass.

New for §12:

- `tests/unit/test_evaluation.py` (11): golden-v1 is frozen (sha256), meets the §12.1 size and mix, every label points
  at a real document, the corpus is fictional and canaried, exploration queries are never gated, thresholds come only
  from `gates.yaml`, a drop beyond tolerance fails even above target, matching respects page and version, the audit
  sheet keeps citation numbers and its pre-screen flags unsupported claims.
- `tests/unit/test_predicates.py` (8 functions, 17 cases): role predicates against the permission matrix, who may open
  which version, the version state machine against *every* unlisted transition, chunk ID and metadata stability,
  order lookups vs questions about orders, unknown-term weighting, product-model disambiguation, every JSON route
  validates its response with a model.
- `tests/integration/test_golden_gate.py`: the whole golden-v1 run (142 cases + 96 access probes + 12 lifecycle
  scenarios) inside `pytest`, failing on any merge gate (G1, G3, G4, G5, G6). About 23 s.
- `tests/integration/test_adversarial.py` (5): system-prompt probes (3), instructions inside a published PDF,
  contradictory sources.

## Results: golden-v1 baseline (run `eval-20261019-215435-26fa47`)

Report: `evaluation/reports/golden-v1-baseline.json`; baseline: `evaluation/baselines/golden-v1.json`. A second
independent run (`eval-20261019-215108-8d0454`) gave identical counts.

| Gate | Result | 95% interval | Target | Status |
| --- | --- | --- | --- | --- |
| G1 Recall@5 | 98 / 98 answerable | 0.962–1.000 | ≥ 0.90 | pass |
| G3 Invented policy | 0 / 36 unanswerable | upper 0.096 | 0 | pass |
| G4 Valid citations | 106 / 106 citations | 0.965–1.000 | 1.00 | pass |
| G5 Unauthorized exposure | 0 / 96 probes | upper 0.038 | 0 | pass |
| G6 Inactive-version citations | 0 / 12 scenarios | upper 0.243 | 0 | pass |
| G9 Latency | full answer p50 31 ms, p95 47 ms (n = 139); retrieval p50 12 ms, p95 15 ms | — | no target yet | recorded |
| G2 Grounding | 59 claims in a 30-case sample; pre-screen: 59 supported | — | ≥ 95% | **awaiting human verdicts** |

G6's upper bound (0.243) is wide because 12 scenarios is a small sample; the count is what is gated.

**What did not pass (kept as honest failures, not tuned away):** by category, positive 65/67, outdated 1/2 and typo 4/6;
everything else is complete. In total 5 of 98 answerable cases are wrong: four abstain when they should answer (G051,
G082, G087, G091) and one answer lacks an expected fact (G036, "power on"). The four abstentions are the price of the
stricter evidence check that fixed the defects below: it prefers "Not in our documents" to a weak answer. This is the
first thing to measure again with the real models.

**Defects found by building golden-v1 and fixed before the baseline** (ADR-022): order-policy questions routed to the
order handoff; a sibling product's error code quoted (TechBook 14 vs 14 Pro); an off-topic answer from two matching
words; source-tag injection in document text.

## §12.1 Dataset preparation

| Item | Status | What and where | Tests |
| --- | --- | --- | --- |
| 20–30 permitted documents, fictional policies labelled | ✓ | 21 documents (`app/demo/golden_corpus.py` + the 5 M3 documents): manuals, policies, FAQs, employee-only procedures; near-identical products (DemoPrinter-X2 / X2 Pro, TechBook 14 / 14 Pro); a policy with two versions (Price Match 14 → 30 days). Every document says "This is a fictional document for the TechCare demo."; employee-only documents carry canary codes | `test_the_corpus_is_fictional_varied_and_canaried` |
| 50+ products, 100–200 orders if live tools | Not applicable | Order and repair lookups are P1 (US-05); P0 answers orders with a handoff and never from documents | — |
| ≥ 120 labelled QA cases with answer, document/version, page | ✓ | `evaluation/golden_questions/golden-v1.yaml`: 142 cases, 98 answerable; each has expected facts, document, version and pages | `test_golden_v1_meets_the_size_and_mix_of_section_12_1`, `test_every_label_points_at_a_real_document…` |
| Case mix | ✓ | positive 67, unanswerable 26, permission 8, follow-up 8, exact code 7, near model 6, typo 6, ambiguous 5, multi-document 4, direct 3, outdated 2. Conflicting sources: `test_adversarial` (P0 guarantee: never blended; detection is P1) | same |
| Stable regression set vs exploratory queries | ✓ | golden-v1 frozen by `golden-v1.sha256`; tuning only on `evaluation/exploration/dev-queries.yaml` (`gated: false`) | `test_golden_v1_is_frozen`, `test_exploration_queries_are_never_gated` |

## §12.2 Unit tests

| Item | Status | Tests |
| --- | --- | --- |
| MIME/content type, limits, checksum | ✓ | `test_validation` (14: allowlist, magic bytes, declared type cross-check, size and page limits, password, corrupt, macro, zip bomb), `test_chunking::test_checksums_are_stable_and_content_addressed` |
| Chunk IDs and metadata stability | ✓ | `test_predicates::test_chunk_ids_and_metadata_are_stable` |
| Role and tenant permission predicates | ✓ | `test_predicates::test_role_predicates_follow_the_permission_matrix`, `…test_who_may_open_which_version` |
| Intent classifier schema and unknown-intent fallback | ✓ | `test_rag::test_intent_routing_is_typed_with_a_safe_fallback`, `test_predicates::test_order_lookups_versus_questions_about_orders` |
| Version state machine rejects invalid transitions | ✓ | `test_predicates::test_the_version_state_machine_rejects_every_unlisted_transition` (every pair not in the table) |
| Response format always validated | ✓ | `test_predicates::test_every_json_route_validates_its_response_with_a_model`, `test_api_support::test_the_voice_contract_matches_its_frozen_schema` |

## §12.3 Integration tests (new ticks)

| Item | Status | Tests |
| --- | --- | --- |
| Same file twice → no duplicate chunks | ✓ | `test_ingestion::test_duplicate_files`, `…test_reprocessing_a_version_is_idempotent`, `test_documents_api::test_duplicate_upload_warns_and_can_be_confirmed` |
| Vector store unavailable → explicit degraded response | ✓ | `test_rag_graph::test_an_unavailable_vector_store_gives_a_safe_error` (status `error`, no citations, question saved) |
| Business tool returns only the verified user's order | P1 | Order tools are P1 (US-05) |

## §12.4 Adversarial and quality tests

| Item | Status | Tests |
| --- | --- | --- |
| Secret system prompt or internal documents | ✓ | `test_adversarial::test_the_system_prompt_is_never_disclosed` (3 probes × guest and employee); G5: 96 access probes (11 staff questions × 4 tricks × 2 customer personas + direct-ID previews) |
| Retrieved PDF with instructions | ✓ (guards) | `test_adversarial::test_instructions_inside_a_published_document_stay_data`: cited as a source, never obeyed, nothing employee-only retrieved, a forged `</source><source …>` is escaped. With the real model: red-team again (§19) |
| Contradictory warranty periods | ✓ (P0) | `test_adversarial::test_contradictory_sources_are_never_blended`: each stated period is backed by its own quote, never merged. Detecting the conflict is P1 (§8.5) |
| Question not in any document | ✓ | G3: 0 / 36; E2E demo 2 below |
| Almost identical model number | ✓ | golden-v1 `near_model` 6/6; `test_predicates::test_a_named_product_excludes_other_models…` |
| Scan-only pages | ✓ | `test_parsing::test_scanned_pdf_without_any_text_fails_with_a_clear_code`, `…test_pdf_pages_without_text_are_flagged` |
| English and Bangla | Not applicable | Language support is not enabled in the MVP (English only) |
| Manual review of source correctness and hallucination | **Open** | Sheet ready: `evaluation/audits/golden-v1-baseline-g2.md` (59 claims; the pre-screen finds all 59 in their passage). It needs a person to fill in the verdicts before G2 can be reported |

## §12.5 End-to-end demos (browser, dev stack, 2026-10-20)

Real UI, real API, worker and Postgres; offline models. Manager: Priya (staff context); guest: a fresh isolated browser
context with no sign-in. Screenshots in `docs/demos/e2e/`.

1. **Upload a returns-policy PDF and ask.** Priya uploads `data/demo-docs/Holiday-Returns-Policy-v1.pdf` (fictional)
   as "Holiday Returns Policy", public, tags *returns, holiday* (`12.5-1a-upload-returns-policy.png`), and publishes
   version 1 (`12.5-1b-published-v1.png`). The guest asks "Until when can I return a holiday gift?" → "Gifts bought
   between November 1 and December 24 can be returned until **January 31** with a gift receipt.", answered from the
   Holiday Returns Policy, source panel "Version 1 · p. 1" with the quoted passage (`12.5-1c-guest-answer-v1-cited.png`).
2. **Unsupported question.** "Do you sell drones with thermal cameras?" → **Not in our documents**, no citations,
   Contact support and Rephrase offered (`12.5-2-unsupported-not-in-documents.png`).
3. **Replace the PDF.** On the document page Priya uploads `Holiday-Returns-Policy-v2.pdf` as version 2; the confirm
   says version 1 will stop appearing in answers (`12.5-3a-publish-v2-confirm.png`). After publishing, the timeline shows
   v2 Published and v1 Archived (`12.5-3b-v2-published-v1-archived.png`). The guest asks the same question → "…until
   **February 15**…", source panel "**Version 2** · p. 1"; January 31 no longer appears (`12.5-3c-guest-answer-v2-cited.png`).
4. **Employee-only question as a public visitor.** Staff ask "When can we waive the diagnostic fee?" in the Assistant
   → answered from Repair Desk Procedures, marked **Employee only**, "Copy for customer" disabled
   (`12.5-4b-staff-employee-only-answered.png`). The guest asks the identical question → **Not in our documents**,
   nothing from the procedure disclosed (`12.5-4a-guest-employee-only-refused.png`).
5. Order question with verification: P1, not built.

Observed while recording: the extractive substitute model also "cites" the document's title line as a second sentence
("Holiday Returns Policy (version 1)."). It is a real line of the PDF, so the citation is valid; a generative model
won't produce it. A phrasing note: "When can the diagnostic fee be **waived**?" is not found by the hashing embedder
(it matches words, and the document says "Waive"). That is a limit of the substitute embedder, not of authorization.

## Not done here

- G2 needs human verdicts on the audit sheet; until then it is not reported (no bare pre-screen number).
- G7 (reliability under injected failures), G8 (operability on a clean machine) and G10 (accessibility, proposed) are
  not part of §12; `docs/evaluation.md` says when each runs.
- Every number above is on the substitute models; the real provider's run sets the baseline that matters (§19).
- CI has not run on GitHub yet (you push); `test_golden_gate.py` is what blocks a merge there.
