# Evidence: §14 repository structure and §15 milestone checklist (2026-10-20)

What was built for §14 and the open §15 items, how it is tested, what was run by hand, and what is still open.
Decisions: ADR-023 (release images, networks, liveness, lock) and ADR-024 (evidence grading: typos and codes).

## Automated tests

`cd backend; .venv/Scripts/python -m pytest` → **482 passed** (0 skipped, 1 min 54 s). `ruff`, `ruff format --check`, `mypy --strict`
clean; `python -m app.cli.contracts` matches. Frontend `lint`, `typecheck`, `npm test` (14) and `build` pass. CI gains an
`images` job that builds both Dockerfiles.

| New test | Proves |
| --- | --- |
| `test_repository::test_every_path_in_the_repository_map_exists` | `docs/repository.md` lists only paths that exist (§14) |
| `test_repository::test_tests_cited_as_evidence_exist` | Every "file::test" reference cited in the threat model, API contract and the §11–§15 evidence pages is a real test |
| `test_api_support::test_the_committed_openapi_document_matches_the_routes` | `docs/contracts/openapi.json` is regenerated with every API change |
| `test_health::test_liveness_answers_even_when_every_component_is_down` | `/health/live` stays 200 while `/health` is 503 |
| `test_predicates::test_one_edit_is_a_typo` (7 cases), `…test_typos_count_as_covered_but_codes_and_short_words_must_match_exactly`, `…test_a_code_no_source_mentions_means_no_evidence` | ADR-024 |
| `test_evaluation::test_the_load_run_parses_server_sent_events_and_summarizes_by_phase` | The G9 load client's SSE parsing, percentiles and error counting |

## §14 Repository structure

| Plan §14 item | Status | Where |
| --- | --- | --- |
| Documented layout | ✓ | `docs/repository.md`: every folder and key file, plus a plan-§14 → repo table explaining each difference (tested) |
| `docs/api-contract.md` | ✓ new | Auth modes, every route with its roles, error codes, the SSE events, sample requests. All samples were run against the container stack: sign-in, `/me`, document list, upload → job → publish (202 → done → 200), guest JSON answer, guest stream ("Not in our documents"), 401 body, internal API refused from outside its networks (403) |
| `docs/threat-model.md` | ✓ new | Assets, actors, STRIDE threats by boundary, each control with the test that proves it; open and accepted risks |
| OpenAPI in the repo | ✓ new | `docs/contracts/openapi.json` (24 paths, 28 operations), written and checked by `python -m app.cli.contracts` |
| `infra/` and Dockerfiles | ✓ | `backend/Dockerfile`, `frontend/Dockerfile`, compose profile `app` (below); `infra/deploy` waits for §16 |
| `sample-data/` | Differs, by decision | Fictional documents are generated from code (`app/demo/`, `scripts/make_demo_docs.py`), so no binaries are committed |
| Orders/tickets services | P1 | Not built (US-05) |

## §15 Milestones: what changed

### M7: containerize services

- **Images.** `backend/Dockerfile` (multi-stage, runtime dependencies from the lock, non-root uid 10001; one image for
  api, worker and migrations) and `frontend/Dockerfile` (Next.js standalone, non-root). Both build; CI builds them too.
- **Compose profile `app`.** `docker compose --profile app up -d --build` runs migrate (exits 0) → api (healthy) and
  worker → web (healthy). `GET /api/v1/health` on the container API: postgres, chroma, storage, worker and queue all ok.
- **Networks.** `web` is only on `app`; Postgres and Chroma only on `data`. From inside the web container: `api`
  resolves, `postgres` and `chroma` don't (ENOTFOUND). This makes architecture §1's rule true locally.
- **End to end in containers** (browser, 1280×800): Priya signs in on port 13000, uploads "Battery Recycling Drop-off"
  (txt), it processes through the container worker into the `uploads` volume, she publishes it
  (`15-02-containers-published.png`); a guest asks how to bring a swollen battery and gets a cited answer whose source
  panel opens the original from the volume (`15-01-containers-guest-answer.png`).
- **Lock.** `scripts/lock_backend.py` regenerated `backend/constraints.txt` as Windows dev venv ∪ Linux image: one new
  pin (`uvloop==0.23.0; sys_platform != "win32"`), every shared pin identical. `pip-audit` on the new lock: nothing new
  (the five ignored entries are the documented chromadb advisories, ADR-020).
- **A clock bug the full test run exposed, fixed:** the request deadline and `answers.latency_ms` used
  `time.monotonic()`, which Windows advances only every ~15.6 ms. Once the pipeline got faster, a whole request could
  fit in one tick, so a 1 µs deadline never tripped (`test_a_request_past_its_deadline_errors_instead_of_hanging`
  failed) and latencies were rounded to 15.6 ms steps. Both now use `time.perf_counter()` (5 of 5 reruns pass). The
  golden runner's G9 latencies before this fix were quantized that way on Windows; the load test's numbers come from
  the client's `perf_counter` and are unaffected. Linux containers were never affected.
- **Bugs found and fixed while containerizing:** an empty `NEXT_PUBLIC_MAX_UPLOAD_MB` build arg would have read as
  0 MB (args now default to the code's values; the upload dialog shows "up to 20 MB" in the container); the API had no
  dependency-free liveness route, so a worker outage would have marked the API unhealthy (`/health/live` added).

### M7: load, security and backup tests

**Load (G9)**, run against a separate API container answering for the evaluation tenant, guest budget raised
(`evaluation/reports/load-m7.json`; offline models; API, worker and data services in Docker on one Windows laptop):

| Phase | Requests | Errors | Retrieval p50 / p95 | First token p50 / p95 | Full answer p50 / p95 | Throughput |
| --- | --- | --- | --- | --- | --- | --- |
| Sequential | 200 | 0 | 9 / 10 ms | 24 / 26 ms | 37 / 47 ms | — |
| 20 concurrent | 100 | 0 | 119 / 232 ms | 472 / 659 ms | 782 / 907 ms | 24.7 req/s |

Within the proposed SLO (docs/evaluation.md: retrieval p95 ≤ 800 ms, first token p95 ≤ 3 s, full p95 ≤ 8 s). With the
real provider the model call will dominate; under concurrency the single API process queues requests (sync handlers in
the thread pool), which is what the concurrent numbers show. Scaling is more API replicas, not code.

**Cost.** Per answered question: 437 prompt tokens, 102 completion tokens, 12 embedding tokens, 1 model call
(estimated by the substitute model from the real prompt). Cost per question = 0.437 × input price + 0.102 × output
price + 0.012 × embedding price (USD per 1k tokens); the prices come with the provider decision (§19).

**Security:** `docs/threat-model.md` (every control linked to a test), `pip-audit` clean apart from the accepted
chromadb advisories, gitleaks and all pre-commit hooks pass, the network split checked by hand (above).
**Backup/restore:** unchanged since §11 (`scripts/db_backup.py backup --verify`, tested).

### M7: fix top evaluation regressions (ADR-024)

Found and measured on new exploration questions, never tuned on golden-v1 (ADR-022):

| Change | Exploration (`dev-queries.yaml`) | golden-v1 (regression check) |
| --- | --- | --- |
| One-edit typos count as the word | typo questions 1 / 6 → 5 / 6 | typo cases 4 / 6 → 6 / 6 (both were known failures: disclosed as seen) |
| A code no source mentions = no evidence | "What does error H9 mean?" was answered with the H-07 row → refused (G3 1 / 2 → 0 / 2) | no change; all gates pass |

golden-v1 after the change (`evaluation/reports/golden-v1-m7.json`): G1 98 / 98, G3 0 / 36, G4 109 / 109, G5 0 / 96,
G6 0 / 12; 95 of 98 answerable cases fully right (was 93). Still wrong: G036 and X14 (the substitute model quotes the
wrong sentence: "power on"), G051 ("color" vs "turn white": no shared word) and G082. These need a real model or
synonyms, not more rules.

### M7: README, diagrams, decisions, demo video

- `README.md` rewritten: purpose, screenshots, features, architecture diagram and flow, setup (containers and dev),
  configuration, scripts, testing, deployment, and what isn't built.
- Diagrams: README architecture (mermaid) and `docs/architecture.md` §1–§3; topology §7 updated for the containers.
- Decisions: ADR-023, ADR-024.
- **Demo video:** `docs/demos/15-demo-walkthrough.mp4` (88 s, 1280×800, no narration), recorded on the container
  stack: a guest asks and opens the cited source; "Not in our documents" for an uncovered question; an employee-only
  question refused to the guest; Priya signs in, uploads and publishes version 2 (version 1 archived); the assistant
  now cites version 2; an employee-only answer marked *Employee only*; Quality and Operations.
- **A bug the recording caught, fixed:** after publishing a new version, the document page kept calling the
  previously selected version "the published version" (the label came from a preview fetched before publishing).
  It now comes from the refreshed document; checked by publishing "Gift wrapping" v2 while viewing v1
  (`15-03-preview-label-after-publish.png`).

## Still open in §15 (not ticked)

| Item | Why |
| --- | --- |
| M1 "Finalize personas, business policies, …" | Depends on open §19 decisions (who publishes and sees analytics, anonymous chat) that are the owner's to make |
| M1 CI, M5 gate | The workflow exists and every check passes locally; it needs its first run on GitHub (you push) |
| M4 gate | Needs a person who doesn't code: session script in `docs/demos/m4-usability-test.md` |
| M6 order data, order lookup, tickets | P1 (scope.md §2, US-05): not built |
| M7 staging, TLS, managed secrets | Deployment (§16): the images and the config guards are ready, the infrastructure isn't |
| M7 gate (full DoD) and the 3–5 minute narrated video | §18 items: the narrated presentation needs a person; the 88 s walkthrough is its raw material |
