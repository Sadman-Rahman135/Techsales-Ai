# P1 retrieval upgrades (plan §8.5, §2.2, §10.2): evidence

Date: 2026-10-20. Decision record: ADR-028 in [architecture.md](../architecture.md). Everything below runs on the local
models (ADR-026) on the RTX 4050, unless it says "offline models" (the deterministic substitutes CI uses).

## 1. What was built

| Plan item | What it does | Where |
| --- | --- | --- |
| §8.5 Metadata-filter product model/brand | The assistant filters by product model, brand, category and file type; filters only narrow what the caller may already see | `app/services/versions.py` `active_version_ids`, `components/chat/chat-app.tsx` |
| §8.5 Lexical/BM25 for part numbers and error codes | Postgres full-text search over chunk text, for questions that name a code or model number | `app/rag/keyword.py`, migration `d9e42a3ec454` |
| §8.5 Fuse lexical/vector + optional reranker | Reciprocal rank fusion (k = 60); local bge-reranker-v2-m3 orders candidates and relaxes the word-coverage rule for high scores | `app/rag/keyword.py`, `app/rag/rerank.py`, compose service `reranker` |
| §8.5 Near-duplicate chunks and version conflicts | Fusion drops chunks with the same text; versions: only the active one is ever searched (ADR-003) | `keyword.fuse`, `active_version_ids` |
| §8.5 Conversational rewriting with short history | A follow-up that fails alone is retried with the previous question (one turn), deterministically | `app/rag/graph.py` (`rewrite_query`, `is_follow_up`) |
| §8.5 Multi-document questions | A second document that answers the rest of a two-part question is kept and cited | `app/rag/evidence.py` `complementary` |
| §8.5 / §7.3 Benchmark chunking, top-k, reranker | §4 below | `data/benchmark_retrieval.sh` (local script), reports in `evaluation/reports/` |
| §2.2 Search across metadata | Brand, department, language and effective date on documents; list filters; managers edit them (audited) | migration `6f67937742dd`, `PATCH /documents/{id}`, documents list "More filters" |

Found and fixed on the way:
- **Wrapped table cells.** The PDF parser kept a wrapped table cell's line break, which split one row in two. The
  demo PDF builder wrote long cells past the cell edge, so the index held "…leave it on for 15 mi". Both are fixed,
  with a test.
- **Order routing.** "…can I still cancel before my order ships?" was sent to the order-lookup reply. Order intent
  now needs status phrasing.

## 2. Automated tests

| Test | Proves |
| --- | --- |
| `test_keyword_search::test_an_exact_code_is_found_by_keyword` | A code finds its table row; fetched hits carry a real cosine distance |
| `test_keyword_search::test_employee_only_text_is_never_found_for_a_customer` | Keyword search obeys the audience filter (staff find the same words) |
| `test_keyword_search::test_another_tenant_finds_and_fetches_nothing_even_with_these_version_ids` | RLS and the Principal-filtered fetch both block another tenant |
| `test_keyword_search::test_the_worker_stores_every_chunks_text` | Every chunk the worker writes has its text |
| `test_keyword` (5 unit tests) | Fusion order, near-duplicates, unfetchable IDs ignored, keyword search only for codes |
| `test_rerank` (4 unit tests) | Reranker client, order and fallback when it fails, the relaxed rule, config checks |
| `test_rag_graph::test_a_follow_up_that_points_back_is_answered_with_the_previous_question` | "How do I fix it?" after an E-04 question is answered from the printer guide, using the one retry |
| `test_rag_graph::test_a_standalone_question_in_a_conversation_is_not_mixed_with_the_previous_one` | A new topic in the same conversation never uses the previous question |
| `test_predicates::test_a_two_part_question_keeps_the_document_for_each_part` | Two-part questions keep both documents, not unrelated ones |
| `test_predicates::test_a_code_inside_a_hyphenated_name_counts_as_mentioned` | "X2" in "DemoPrinter-X2", "P11" = "P-11", and a different code still refused |
| `test_predicates::test_order_lookups_versus_questions_about_orders` | Status phrasing → order lookup; policy questions stay with the documents |
| `test_filters::test_filters_narrow_and_never_widen` | Brand keeps general documents, category and file type are strict, no filter reaches employee-only text for a customer |
| `test_filters::test_an_answer_obeys_the_assistant_filters` | The same question answered with the right category, refused with the wrong one |
| `test_documents_api::test_metadata_is_set_at_upload_edited_with_an_audit_and_searchable` | Upload metadata, PATCH with audit (old and new values), list filters, facets |
| `test_documents_api::test_only_managers_edit_metadata_and_index_fields_stay_upload_only` | 403 for employees, 422 for product model/audience/bad language, 404 for another tenant |
| `test_parsing::test_a_pdf_table_cell_that_wraps_stays_one_value` | A wrapped cell stays one value in one row |
| `test_evaluation::test_golden_v2_is_frozen_and_only_relabels_golden_v1` | golden-v2 has v1's questions in v1's order; exactly 8 expectations changed |
| `test_golden_gate::test_golden_v1_merge_gates` (offline models) | All golden-v1 gates still pass in CI (it caught keyword search answering G128, now fixed) |

Whole backend suite: 536 passed (lint, format and mypy clean).

## 3. Measurements on the local models

**Exploration set** (`evaluation/exploration/dev-queries.yaml`, 49 cases: 29 answerable, 12 must-refuse, 8 open).
This is where everything was tuned:

| Variant | Right answers | Refusals | Invented | Full answer p50 / p95 | Report |
| --- | --- | --- | --- | --- | --- |
| Before (vector only), 41 cases | 16 / 29 | 4 / 4 | 0 | 0.95 / 2.3 s | `exploration-local-before.json` |
| A: hybrid + code and intent fixes | 20 / 29 | 12 / 12 | 0 | 1.7 / 5.9 s | `exploration-local-A.json` |
| B: A + reranker order | 20 / 29 | 12 / 12 | 0 | 1.6 / 2.1 s | `exploration-local-B.json` |
| C: B + relaxed rule (score ≥ 1.5, coverage ≥ 0.4) | 23 / 29 | 12 / 12 | 0 | 1.8 / 3.1 s | `exploration-local-C.json` |
| **Final** (C + follow-up wording + two-part questions + codes-only keyword) | **24 / 29** | **12 / 12** | **0** | 1.7 / 2.4 s | `exploration-local-p1.json` |

Why the reranker isn't a stand-alone threshold:
- Unanswerable questions reached embedding similarity 0.79 and reranker score 2.63 ("Which error code means low ink
  on the DemoPrinter-X2?" sits close to the toner text).
- Answerable questions went as low as 0.59 and −3.55.
- No threshold separates them, so the word-coverage gate stays, and the reranker only relaxes it.

Remaining misses:
- **Refused:** X22 "how many days does a normal parcel take", X25, X26. Everyday paraphrases that share few words
  with the policy text; refused, never invented.
- **Incomplete:** X17 gives the code's meaning but not the fix.
- **Scorer:** X32 is right, but the scorer wants "90 day" and the answer says "90-day".

**Golden sets** (checked once, never tuned on):

| Run | G1 retrieval | G3 invented | G4 citations | G5 access | G6 versions | Right answers | Refusals | p50 / p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| golden-v1 before (`golden-v1-local-tables.json`) | 98/98 | 0/36 | 103/103 | 0/96 | 0/12 | 88/98 | 36/36 | 2.0 / 3.8 s |
| golden-v1 after (`golden-v1-local-p1.json`) | 98/98 | 1/36 | 105/105 | 0/96 | 0/12 | 89/98 | 35/36 | 1.9 / 3.0 s |
| golden-v2 after (`golden-v2-local-p1.json`, core) | 100/100 | 0/34 | 105/105 | — | — | 97/100 | 34/34 | 1.9 / 3.4 s |

- **golden-v1's G3 = 1 is G135:** "And how long does it take?", after a phone screen repair question. It is now
  answered "Screen repairs for most phones are completed the same day." [1], which is right. golden-v1 labelled it
  "must refuse" because P0 had no conversation history. golden-v2 corrects that label, nothing else about the
  question. golden-v1 stays frozen and, with the offline models, still passes every gate in CI.
- **golden-v2 misses:**
  - G049 leaves out why to back up the phone.
  - G068 says "the paper tray is empty" where v2 expects "Paper tray empty", a paraphrase v2 doesn't list; it stays
    frozen, so this waits for a v3.
  - G139 is a follow-up that is still refused (the safe direction).

## 4. Benchmark: chunk size, candidates, context (exploration, final setup)

| Setting | Right answers | Refusals | p50 / p95 |
| --- | --- | --- | --- |
| Chunks 300 / 450 / 50 (target / max / overlap) | 23 / 29 | 12 / 12 | 1.75 / 3.3 s |
| **Chunks 500 / 700 / 75 (default)** | 23 / 29 | 12 / 12 | 1.71 / 2.5 s |
| Chunks 700 / 900 / 100 | 23 / 29 | 12 / 12 | 1.73 / 2.4 s |
| 5 candidates | 23 / 29 | 12 / 12 | 1.70 / 2.3 s |
| 20 candidates | 23 / 29 | 12 / 12 | 1.79 / 2.5 s |
| 3 context chunks | 23 / 29 | 12 / 12 | 1.68 / 2.3 s |

- **No setting changes the result.** Chunks follow headings, and every section of this corpus is short: 53 tokens
  on average, 106 at most. So all three chunk sizes produce the same chunks. The defaults stay; long real manuals
  are what would tell them apart.
- **Run-to-run variation is about one case** (24 in the final run, 23 here) with the same settings.
- **How it was run:** `data/benchmark_retrieval.sh`, each chunking variant in its own organization and collection.
  The container worker was paused for the last run: it claims jobs across tenants and doesn't have files uploaded
  from the host.

## 5. Manual tests (browser, containers, local models)

1. **Assistant filters** ([p1-01](p1-01-assistant-filters-no-results.png), dark mode):
   - Category = Warranty, then "What does error code E-04 mean on the DemoPrinter-X2?".
   - Result: "Not in our documents", plus "No results with these filters: Warranty." and **Search without filters**.
   - Clicking it re-asks without filters: "Error code E-04 on the DemoPrinter-X2 means 'Toner cartridge not
     detected'." [1] DemoPrinter-X2 Troubleshooting Guide · p. 2.
2. **Edit details** ([p1-02](p1-02-edit-document-details.png)):
   - On the printer guide, set brand DemoBrand, department Service desk, effective date 2026-09-01, tags.
   - "Details saved." The header shows them, and the Activity tab records the change.
3. **Metadata search** ([p1-03](p1-03-documents-metadata-filters-light.png), light mode):
   - Brand DemoBrand with effective dates 2026-08-01 to 2026-12-31 gives 1 document.
   - The first layout put "More filters" inside the toolbar row and squeezed the search box; it was moved under the row.
4. **Upload dialog:** the new fields (brand, department, effective date, language) appear, with English as the default.
