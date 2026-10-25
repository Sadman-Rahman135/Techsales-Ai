# P1 admin extras (plan §2.2): review queue, exports, feedback trend, multi-organization isolation

Date: 2026-10-20. Decision record: ADR-030 in [architecture.md](../architecture.md). The browser checks ran against the
containers (`docker compose --profile app up -d --build`) with demo data; everything shown is fictional.

## 1. What was built

| Plan item | What it does | Where |
| --- | --- | --- |
| §2.2 Administrative review workflows | `/admin/reviews` lists every question the documents didn't cover and every answer rated not helpful. A manager resolves it (optionally naming the document that now answers it) or dismisses it, with a note; resolved items can be reopened. Every decision is audited | `app/services/reviews.py`, `app/api/reviews.py`, `components/admin/reviews-view.tsx`, migration `804ee344031b` |
| §2.2 Data export | CSV downloads of questions, feedback, review decisions and tickets for the dashboard's date range, and of every document. Redacted like the dashboard; every download audited | `app/services/exports.py`, `app/api/exports.py`, Quality "Export" panel, Documents "Export CSV" |
| §10.4 Feedback trends (scope.md P1) | "Helpful answers by day" on Quality: one line, a table twin, gaps on days without ratings | `GET /admin/analytics` `feedback_daily`, `components/quality/charts.tsx` `ShareLine` |
| §2.2 Multiple organizations with independently tested access isolation | One test sweeps every tenant-scoped route with another organization's real IDs | `tests/integration/test_multi_org.py` |

## 2. Automated tests

| Test | Proves |
| --- | --- |
| `test_reviews_exports::test_unanswered_and_not_helpful_answers_wait_in_the_queue_redacted` | Both kinds of item are queued with their reason; emails and phone numbers are redacted; the reason filter works |
| `…test_a_manager_resolves_dismisses_and_reopens_and_every_decision_is_audited` | Resolve with a document, dismiss, reopen; counts move; `review.resolved/dismissed/reopened` audit events |
| `…test_only_queued_answers_and_live_documents_can_be_decided` | An answered, unrated answer is 404; a made-up document is 422 |
| `…test_reviews_are_for_this_tenants_managers_and_admins` | Employees and customers 403, guests 401, another tenant's manager sees nothing and gets 404 |
| `…test_exports_are_redacted_csv_and_audited` | All five exports are CSV with a BOM and a file name; redaction holds; a question starting with `=` exports as `'=`; each download writes `export.downloaded` with a row count |
| `…test_exports_are_for_managers_and_admins_with_a_valid_range` | Employees 403; unknown dataset 422; a backwards range is `INVALID_RANGE` |
| `…test_the_dashboard_has_feedback_by_day` | `feedback_daily` has one entry per day and adds up to the feedback totals |
| `test_exports` (3 unit tests) | Formula cells are quoted, numbers are not; times in the tenant's timezone; quoting and the BOM |
| `test_multi_org::test_another_organizations_ids_lead_nowhere` | 16 routes (documents, versions, jobs, previews, conversations, feedback, tickets, drafts, reviews) answer 404 to another organization's IDs |
| `test_multi_org::test_another_organizations_lists_counts_and_exports_are_empty` | Lists, facets, analytics, the review queue and every export show none of the other organization's IDs or text |
| `test_multi_org::test_another_organizations_customers_get_no_answers_from_its_documents` | Another organization's customer and employee get "Not in our documents" for a question this one answers |
| `test_schema::test_every_tenant_table_has_forced_row_level_security` | `answer_reviews` forces RLS like every tenant table |
| `lib/reviews.test.ts` (4) | Export links go through the BFF with the range; the dataset list matches the API |

## 3. Manual test (browser)

Run on the release containers with the development data (organization `techcare`: 209 questions, 50 not in
documents, 3 rated not helpful), signed in as the demo manager.

1. **Review** in the sidebar (managers and admins only) opens the queue: 52 to review. Filtering by "Rated not
   helpful" leaves 3. Each card shows its reasons as icon + text, the feedback reason and note, and "What the
   assistant said" expands the answer ([p1-10](p1-10-review-queue-dark.jpg), dark mode).
2. **Resolve** on "Why is my DemoPrinter-X2 printing faded pages?" opens the dialog. Pick "DemoPrinter-X2
   Troubleshooting Guide", add a note, then **Mark resolved** ([p1-11](p1-11-review-resolve-dialog.jpg)).
3. The counts move to 51 to review / 1 resolved. The Resolved tab shows who resolved it, when, the linked document
   and the note, with **Reopen** ([p1-12](p1-12-review-resolved.jpg)).
4. **Quality** (30 days, light mode) has "Helpful answers by day" with its table toggle, and the **Export** panel
   for the range. "Review all not-helpful answers" links to the filtered queue
   ([p1-13](p1-13-quality-trend-export-light.jpg)). The development data has only three ratings, all not
   helpful, so the line is a single point at 0%.
5. All five exports download through the BFF (HTTP 200, CSV, file name per dataset). The questions file has 209
   rows, matching the database. The reviews file holds step 2's decision in store time (`-05:00`). Each download
   wrote an `export.downloaded` audit event with its row count.
6. **Documents** has **Export CSV** next to Upload ([p1-14](p1-14-documents-export.jpg)).

Not yet done: the full MASTER.md checklist at 375 / 768 px and 200% zoom for the review screen and the two new
Quality panels (the browser window couldn't be resized in this run).

## 4. Known limits

- The review queue shows the newest 50 items per filter; there is no paging beyond that yet.
- Redaction is pattern-based (emails, phones, order numbers, cards, IDs). A person's name in a question still shows
  and exports, the same as on the dashboard (data-model §8: name detection is P1).
- The document picker in "Resolve" lists the first 100 documents by title.
