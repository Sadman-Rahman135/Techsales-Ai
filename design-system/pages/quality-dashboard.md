# Quality dashboard: page overrides (§10.4, persona: support manager)

## Layout
- App shell with sidebar. A time-range control (Today · 7 days · 30 days · Custom) sits at the top and is always visible, with an explicit label:
  "Showing 1–30 Sep 2026 · store time (America/Chicago)", using the tenant's configured timezone. The range is stored in the URL.
- Row 1, four KPI tiles: Questions · Answered % · Not-in-documents % · Failed calls. Each shows a value (tabular), a change vs the previous period with an arrow icon **and** a sign (not color alone), and a sparkline.
- Row 2: "Answered vs not in documents over time" (stacked bars by day) and "Top intents" (horizontal bars).
- Row 3: **Unanswered questions** table (the main action surface) · Most-cited documents · Feedback summary.
- Row 4 (P1, ADR-030): "Helpful answers by day" (one `--mark-series` line, table twin) · Export (CSV per dataset for the shown range).
- Row 5: Ingestion failures with a "Retry" action per row.
- The unanswered table and "Corrections to review" link to the review queue (`/admin/reviews`).

## Charts
- Trend → line or stacked bar. Comparison → horizontal bar. No pie charts for more than 3 categories.
- Status series reuse the semantic tokens: answered = `--success`, not-in-documents = `--warning`, failed = `--danger`. Each series also gets a pattern/marker and direct labels.
- Gridlines use `--border`; axes are labelled with units; tooltips work on hover **and** keyboard focus.
- Every chart has a "View as table" toggle and an `aria-label` summary ("Answered rate rose from 71% to 84% over 30 days").
- Loading uses skeleton chart frames. Empty: "No questions in this period." Error: message + Retry.

## Privacy
- The unanswered questions table shows question text only, with PII redacted server-side. Opening the full conversation requires the
  `conversations:read` permission; otherwise the row shows a `Lock` with "Restricted".
- Export (P1, built) respects the same redaction, and each download is audited.
