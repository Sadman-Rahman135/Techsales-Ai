-- Database roles for a managed or self-hosted PostgreSQL 18 (docs/deployment.md §3). Run once as the database
-- owner (the role that runs migrations), before the first `alembic upgrade head`:
--
--   psql "$MIGRATION_DATABASE_URL" -v app_password="…" -v worker_password="…" -f infra/deploy/postgres/roles.sql
--
-- Passwords come from the secret store; never commit them. Same roles as the local init script
-- (infra/docker/postgres/init/01-roles-and-test-db.sh), without the test database.
--   knowledgeai_app     the API. Not a superuser, no BYPASSRLS: row-level security applies (ADR-011).
--   knowledgeai_worker  the ingestion worker: its own login, the app role's privileges (§16 separate accounts).
-- The owner role is used only for migrations. Some providers don't allow NOBYPASSRLS for the owner; that's fine,
-- the owner never serves traffic.

\set ON_ERROR_STOP on

SELECT format('CREATE ROLE knowledgeai_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
              :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'knowledgeai_app')\gexec

SELECT format('CREATE ROLE knowledgeai_worker LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
              :'worker_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'knowledgeai_worker')\gexec

GRANT knowledgeai_app TO knowledgeai_worker;

SELECT format('GRANT CONNECT ON DATABASE %I TO knowledgeai_app', current_database())\gexec
GRANT USAGE ON SCHEMA public TO knowledgeai_app;
-- Tables and sequences created later by migrations (as this owner) are usable by the app role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO knowledgeai_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO knowledgeai_app;

-- Rotating a password later: ALTER ROLE knowledgeai_worker PASSWORD '…'; then restart the worker.
-- Cutting a service off: ALTER ROLE knowledgeai_worker NOLOGIN; (the API keeps working).
