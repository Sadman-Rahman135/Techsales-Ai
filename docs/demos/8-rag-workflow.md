# Evidence: §8 LangGraph RAG workflow and milestone M3 (2026-10-19)

Stack under test: Postgres 18.6 + Chroma 1.5.9 (compose), FastAPI API, ingestion worker, LangGraph 1.2.14.
**Models:** the deterministic offline substitutes (§5.1): `fake-llm` (extractive: answers only with sentences from the sources)
and `fake-embedding@hash-v2` (feature hashing of stemmed content words, 384 dims). The real provider is an open §19 decision;
the OpenAI adapter is implemented and unit-tested against a mocked endpoint. **The gate numbers below measure the pipeline
(retrieval, grading, citation validation, abstention, authorization) with substitute models, not real answer quality.**

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **301 passed** (77 new: 76 for §8, 1 for the index rebuild). `ruff check`,
`ruff format --check` and `mypy --strict` (79 files) pass.

| Requirement | Test(s) |
| --- | --- |
| §8.1 graph: every node, bounded path (recursion limit 12) | `test_rag::test_the_graph_compiles_with_every_node_and_a_bounded_path`, `…test_other_routes` |
| §8.3.1 validate: tenant and audience from the server, never the body | `test_chat_api::test_staff_see_employee_answers_customers_never_do`, `…test_callers_need_a_session_or_a_token`, `…test_invalid_questions_get_422`, `test_rag_graph::test_direct_replies_never_touch_retrieval` (empty question) |
| §8.3.2 typed intent with safe fallback | `test_rag::test_intent_routing_is_typed_with_a_safe_fallback` (9 cases) |
| §8.3.3 authorized retrieval | `test_rag_graph::test_employee_only_content_never_reaches_the_public`, `…test_another_organization_never_gets_these_documents`, `…test_an_unpublished_new_version_is_never_cited`, `…test_a_published_update_is_the_only_version_cited`, `…test_a_deleted_document_is_no_longer_found` |
| §8.3.4 evidence grading (not a security decision) | `test_rag::test_sources_must_cover_most_question_terms`, `…test_topic_adjacent_sources_are_not_evidence`, `…test_inactive_versions_and_weak_similarity_never_count` |
| §8.3.5 at most one rewrite | `test_rag::test_routes_after_grading_allow_exactly_one_rewrite`, `test_rag_graph::test_unknown_topics_rewrite_once_then_abstain` |
| §8.3.6 grounded generation | `test_rag_graph::test_a_grounded_answer_is_persisted_with_its_trace`, `test_rag::test_fake_model_answers_extractively_with_citations` |
| §8.3.7 citation validation (G4) | `test_rag::test_invalid_answers_are_rejected` (unknown IDs, no citations, invented URL/page, empty), `…test_invented_quotes_are_replaced…`, `…test_employee_sources_can_never_be_cited_to_the_public`, `test_rag_graph::test_invented_citations_are_never_shown` |
| §8.3.8 business tool (P0: safe handoff) | `test_rag_graph::test_direct_replies_never_touch_retrieval[order]` |
| §8.3.9 abstain | `test_rag_graph::test_unknown_topics_rewrite_once_then_abstain`, `…test_unparseable_output_abstains` |
| §8.3.10 persist trace (IDs, not text) | `test_rag_graph::test_a_grounded_answer_is_persisted_with_its_trace` (path, retrieved IDs, timings, request/trace IDs, chunk-linked citations) |
| §8.4 prompt contract + strict output with safe fallback | `test_rag::test_system_prompt_states_the_required_rules`, `…test_rendered_sources_are_labelled_escaped_and_budgeted`, `…test_openai_adapter_requests_strict_json_and_parses_it`, `…test_openai_output_that_does_not_parse_means_abstain`, `…test_openai_provider_failures_raise_chat_model_error` |
| Provider / vector store / deadline failures | `test_rag_graph::test_provider_failure_is_an_error_answer_and_the_question_is_saved`, `…test_an_unavailable_vector_store_gives_a_safe_error`, `…test_a_request_past_its_deadline_errors_instead_of_hanging` |
| SSE: provisional text only after grading; one authoritative `final` | `test_rag_graph::test_stream_sends_progress_then_one_authoritative_final`, `…test_abstaining_streams_never_send_provisional_text`, `test_chat_api::test_the_stream_sends_meta_status_delta_then_final`, `…test_refusals_happen_before_the_stream_starts` |
| Conversations + feedback (§9) | `test_chat_api::test_conversations_are_private_to_their_owner`, `…test_signed_in_users_own_their_conversations`, `…test_feedback_on_your_own_answer` |
| Rate limits (§3 budget) | `test_chat_api::test_rate_limited_callers_get_429_with_retry_after`, `test_rag::test_rate_limiter_is_a_sliding_window` |
| Voice contract (§9.2) | `test_chat_api::test_voice_contract` (401 without/with a wrong token, 403 for `audience=employee`, speech summary, abstention copy), `…test_a_voice_call_keeps_one_conversation` |
| Direct IDs | `test_chat_api::test_customers_cannot_open_employee_documents_by_id` |
| **M3 gate** | `test_m3_gate::test_m3_gate_passes` (fresh corpus, same runner as the report below) |
| Index rebuild (§7.3, ADR-013) | `test_ingestion::test_rebuild_reindexes_live_versions_into_a_new_collection` |

## M3 gate report (dev stack, `docs/demos/8-m3-gate-report.json`)

`python -m app.evaluation.runner --dataset ../evaluation/datasets/m3-handpicked.yaml --out ../docs/demos/8-m3-gate-report.json`
on the running dev stack (demo corpus uploaded and published through the real ingestion path with `--load-corpus`).

| Gate | Result | Count | 95% interval |
| --- | --- | --- | --- |
| G1 recall@5 (expected document **and** page in the top 5 retrieved) | pass | 15 / 15 | 0.80 to 1.00 (Wilson) |
| G3 invented answers on no-answer + permission probes | pass | 0 / 6 | upper bound 0.39 |
| G4 citations that point at a retrieved, authorized, published chunk | pass | 16 / 16 | 0.81 to 1.00 |
| M3: answered from the expected source **and** stating the expected fact | pass | 15 / 15 answered, 6 / 6 abstained | |

The samples are small by design (this is the M3 smoke gate); golden-v1 (120 cases, §11) replaces them.
The employee-only document carries the canary `CANARY-EMP-7Q2`; it appeared in no customer answer or quote.

## Manual test (running API, `docs/demos/8-api-transcript.txt`)

| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 1 | Anonymous: "Why is my DemoPrinter-X2 printing faded pages?" | `answered`, grounded, cites the troubleshooting guide p. 1 | ✓ |
| 2 | Follow-up in the same conversation | Same `conversation_id`, 4 messages | ✓ |
| 3 | "Do you sell electric bikes?" | `insufficient_evidence`, no citations, content-guide copy | ✓ |
| 4 | "Where is my order TC-123456?" | `direct` handoff (order lookup is P1), not grounded | ✓ |
| 5 | Customer asks the employee-only question | `insufficient_evidence`; no fee, no canary | ✓ |
| 6 | No session and no token | 401 in the §9.3 shape | ✓ |
| 7 | SSE `/chat/ask/stream` "What does error code E-04 mean?" | `meta → status(searching) → delta → status(checking) → final`; E-04 row first | ✓ |
| 8 | Conversation list/detail, then the same ID from another session | Own history with citations; other session 404 | ✓ |
| 9 | Feedback `not_helpful` with a note | 201 | ✓ |
| 10 | Signed-in employee asks the employee-only question | `answered` from Repair Desk Procedures | ✓ |
| 11 | Voice endpoint with the service token / `audience=employee` / no token | 200 with a two-sentence `speech_summary` / 403 / 401 | ✓ |
| 12 | 11 questions in a minute from one anonymous session | 10 × 200, then 429 `RATE_LIMITED`, `Retry-After: 60` | ✓ |
| 13 | OpenAPI docs | `chat` and `internal` route groups documented | ✓ `8-01-openapi-chat.png` |
| 14 | Embedder changed (v1 → v2): the API refuses the old index; `python -m app.ingestion.rebuild --drop-existing` | `IndexMismatch`, then `versions=6 chunks=20 rows_rewritten=0 failed=0` | ✓ |

## Found and fixed while building

- The extractive model ignored section headings ("How to claim"), so it abstained on a question its source answered. Headings
  now give their sentences context.
- The stemmer turned "jammed" into "jamm"; doubled consonants are now undone, so "paper jam" matches.
- `MIN_TERM_COVERAGE` 0.5 let "What is the warranty on drones?" answer from the laptop warranty; 0.6 abstains (G3).
- The fake embedder hashed stopwords, so "restocking fee" ranked below unrelated chunks (G1 14/15). Hashing stemmed content
  words fixed it (15/15). Its model ID now carries the algorithm version, and the index refused the old vectors until rebuilt,
  which is exactly ADR-013 working.
- When a source was cited twice, the validator kept the last quote rather than the most relevant one.
- Ties between table rows were broken alphabetically (E-07 before E-04); exact codes and model numbers now win ties.
- The streaming endpoint checked rate limits and conversation ownership inside the generator, after the 200 headers; these
  checks moved into a dependency, so refusals are proper 401/404/429 JSON.
- The voice route was listed under both `chat` and `internal` in OpenAPI; it has its own router now.

## Not done here

- Real LLM and embedding provider (§19). Prompt-injection and system-prompt red-team tests (§12) only mean something with a
  real model; the prompt contract and source wrapping are in place and unit-tested.
- ~~Customer chat UI with clickable citations and the source panel (§10.1, M4)~~: done in §10, see `10-ui.md`.
- P1 retrieval improvements (§8.5): hybrid BM25, reranking, history-aware rewriting, conflict detection.
- Active-version cache (P1 optimization); network restrictions for the internal API (deployment, M7).
