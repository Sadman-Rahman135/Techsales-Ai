# Evidence: §6 data model (2026-10-18)

## Automated tests
`cd backend; .venv/Scripts/python -m pytest` → **74 passed** (49 new for §6), all against real Postgres 18 and Chroma 1.5.9.
The integration suite runs as the restricted `knowledgeai_app` role, and a guard test fails if it ever runs with RLS bypass.

| Plan item | Test(s) |
| --- | --- |
| §6.1 unique `(document_id, version_number)` | `test_schema::test_version_number_is_unique_per_document` |
| §6.1 ownership checked on every fetch | `test_schema::test_cannot_read_another_tenants_row_even_by_id`, `…each_tenant_sees_only_its_own_rows`, `…tenant_tables_return_nothing_without_a_tenant`, `…cannot_write_rows_for_another_tenant` |
| §6.1 no shared Chroma IDs across tenants | `test_chroma_index::test_ids_are_deterministic`, `test_schema::test_chroma_id_must_be_deterministic`, `test_chroma_index::test_other_tenants_chunks_are_never_returned` |
| §6.1 FKs and delete behaviour explicit | `test_schema::test_child_rows_cannot_point_at_another_tenant`, `…active_version_must_belong_to_the_same_document`, `…deleting_a_source_keeps_the_citation_row_but_drops_the_link` |
| §6.1 conversation deletion clears content | `test_schema::test_deleting_a_conversation_removes_its_content` |
| §6.2 strategy chosen and tested | all of `test_chroma_index.py` (ADR-010) |
| §6.3 new uploads not searchable | `test_versions::test_new_uploads_are_not_answerable_until_published` |
| §6.3 immutable versions | `test_schema::test_versions_are_immutable_once_uploaded` |
| §6.3 verify chunk count before activating | `test_versions::test_publish_refuses_when_chroma_is_missing_chunks`, `…only_ready_versions_can_be_published` |
| §6.3 switch pointer only after success | `test_versions::test_publish_switches_the_pointer_archives_the_old_version_and_audits` |
| §6.3 exclude unpublished/old versions + post-filter | `test_chroma_index::test_only_active_versions_are_searched`, `…public_principal_never_sees_employee_chunks`, `test_versions::test_active_versions_respect_audience_and_deletion` |
| §6.3 archived versions: managers/admins only | `test_versions::test_archived_versions_are_visible_to_document_managers_only` |
| §6.3 delete vectors, objects, references | `test_versions::test_purge_removes_vectors_files_and_rows_but_keeps_citation_rows`, `test_chroma_index::test_delete_version_removes_its_vectors`, `tests/unit/test_storage.py` |
| §6.3 duplicate checksum handling | `test_schema::test_same_file_twice_on_one_document_is_rejected`, `test_versions::test_duplicate_checksum_is_found_across_documents` |
| §6.3 reconciliation check | `test_versions::test_reconcile_reports_drift` |
| Migrations | `test_schema::test_migrations_round_trip_and_match_the_models` (downgrade → upgrade → `alembic check`) |

Static checks: `ruff check`, `ruff format --check`, `mypy --strict` (30 files): all pass.

## Manual tests
| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 1 | `alembic upgrade head` → `downgrade base` → `upgrade head` → `alembic check` on the dev DB | Clean round trip, no drift | ✓ |
| 2 | Inspect `pg_class` for RLS flags | RLS enabled + forced on the 9 tenant tables only | ✓ |
| 3 | `psql` as `knowledgeai_app`: insert a document for tenant A, then read with no tenant / tenant B | 0 rows both times | ✓ ([transcript](6-data-model-psql.txt)) |
| 4 | Same session: insert a document for tenant A while scoped to tenant B | `new row violates row-level security policy` | ✓ |
| 5 | Same session: `UPDATE audit_events` | `audit_events is append-only (UPDATE blocked)` | ✓ |
| 6 | Roll back, count organizations | 0 (nothing persisted) | ✓ |

No UI for this section; the psql transcript is the demo.

## Found and fixed while building
- Autogenerate put the circular `documents → document_versions` FK inside `CREATE TABLE documents`, before the target table
  existed, and the downgrade dropped tables in an order that FK blocked. Both were moved to explicit steps.
- The Docker image's default user is a superuser with BYPASSRLS, so RLS would have been silently inert. A separate app role fixes it.
- Version immutability (§6.3) wasn't enforced at first; a trigger now blocks changes to a version's file, number and owner.
