## What and why

<!-- One or two sentences. Link the plan issue: "Part of #123" -->

**Priority:** <!-- P0 (MVP) / P1 (production) / P2 (stretch) -->
**Plan section:** <!-- e.g. docs/IMPLEMENTATION_PLAN.md §7.1 -->

## Feature evidence (required, blueprint §0)

A feature is not done just because its UI exists. All three are required before a plan checkbox is ticked.

- [ ] **Automated tests** added or updated: <!-- test file paths -->
- [ ] **Manual test** performed. Steps and result:
  1.
- [ ] **Screenshot or short demo** attached below, or saved under `docs/demos/` (no real secrets or PII visible)

## Safety checklist

- [ ] No usable secrets, real customer data, or `.env` contents in code, logs, or screenshots
- [ ] Access control: tenant and role filters are applied *before* any context reaches the LLM (if relevant)
- [ ] Project 1 still runs without Project 2 (ADR-001): no new dependency on the voice project
- [ ] API changes are reflected in OpenAPI and `docs/api-contract.md` (if relevant)
- [ ] New dependencies are pinned and recorded in `docs/dependency-versions.md` (if any)

<!-- Release PRs only: confirm every item in the Definition of Done (IMPLEMENTATION_PLAN.md §18). -->
