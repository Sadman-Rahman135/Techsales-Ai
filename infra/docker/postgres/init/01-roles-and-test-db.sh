#!/bin/sh
# Runs once when the Postgres volume is first created (docker-entrypoint-initdb.d), and is safe to re-run.
# - knowledgeai_app: the role the API connects as. Not a superuser and no BYPASSRLS, so row-level security
#   applies to it (ADR-011). The owner role ($POSTGRES_USER) is used only for migrations.
# - knowledgeai_worker: the ingestion worker's own login (§16 "separate service accounts"). It inherits the
#   app role's privileges, so the same RLS applies, but it has its own password and can be revoked on its own.
#   Local development may keep using knowledgeai_app for both; the compose `app` profile uses both roles.
# - knowledgeai_test: a separate database for the test suite.
# Managed databases get the same roles from infra/deploy/postgres/roles.sql.
set -eu

APP_ROLE=knowledgeai_app
APP_PASSWORD="${APP_DB_PASSWORD:-knowledgeai-app-dev-only}"
WORKER_ROLE=knowledgeai_worker
WORKER_PASSWORD="${WORKER_DB_PASSWORD:-knowledgeai-worker-dev-only}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$APP_ROLE') THEN
    CREATE ROLE $APP_ROLE LOGIN PASSWORD '$APP_PASSWORD' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$WORKER_ROLE') THEN
    CREATE ROLE $WORKER_ROLE LOGIN PASSWORD '$WORKER_PASSWORD' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
\$\$;
GRANT $APP_ROLE TO $WORKER_ROLE;
SELECT 'CREATE DATABASE knowledgeai_test OWNER $POSTGRES_USER'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'knowledgeai_test')\gexec
EOSQL

for db in "$POSTGRES_DB" knowledgeai_test; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<EOSQL
GRANT CONNECT ON DATABASE $db TO $APP_ROLE;
GRANT USAGE ON SCHEMA public TO $APP_ROLE;
-- Tables created later by migrations (as $POSTGRES_USER) are usable by the app role automatically.
ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO $APP_ROLE;
ALTER DEFAULT PRIVILEGES FOR ROLE $POSTGRES_USER IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO $APP_ROLE;
EOSQL
done
