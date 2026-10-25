"""PostgreSQL backup and a tested restore (blueprint §11.3) for the local compose stack.

    python scripts/db_backup.py backup                 # → data/backups/knowledgeai-<UTC time>.dump
    python scripts/db_backup.py verify <file.dump>     # restore into a scratch database and compare
    python scripts/db_backup.py backup --verify        # both

`verify` restores the dump into `knowledgeai_restore_check`, compares the schema revision and the row count of
every table with the source database, then drops the scratch database. Postgres is the source of truth
(ADR-002): Chroma is rebuilt from it (`python -m app.ingestion.rebuild`) and uploaded originals are backed up
with object storage, so this dump plus the storage bucket is a complete backup. Staging and production use the
managed database's own point-in-time backups instead; the restore check is the same (docs/operations.md).

Runs pg_dump/pg_restore inside the postgres container, so the host needs only Docker. Standard library only.
"""

import argparse
import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = "knowledgeai"  # the schema owner from compose.yaml (a superuser locally, so RLS doesn't hide rows)
SCRATCH = "knowledgeai_restore_check"


def _compose(*args: str, stdin: bytes | None = None) -> bytes:
    command = ["docker", "compose", "exec", "-T", "postgres", *args]
    result = subprocess.run(command, cwd=ROOT, input=stdin, capture_output=True, check=False)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:2])} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _psql(database: str, sql: str) -> str:
    return _compose("psql", "-U", OWNER, "-d", database, "-v", "ON_ERROR_STOP=1", "-At", "-c", sql).decode()


def backup(database: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{database}-{stamp}.dump"
    path.write_bytes(_compose("pg_dump", "-U", OWNER, "-d", database, "--format=custom"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"backup {path} {path.stat().st_size} bytes sha256={digest}")
    return path


def table_counts(database: str) -> dict[str, int]:
    tables = _psql(
        database,
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
    ).split()
    counts: dict[str, int] = {}
    for table in tables:
        # Table names come from the catalog query above, never from input.
        counts[table] = int(_psql(database, f'SELECT count(*) FROM "{table}"').strip())  # noqa: S608
    return counts


def verify(dump: Path, source: str) -> bool:
    _psql("postgres", f"DROP DATABASE IF EXISTS {SCRATCH}")
    _psql("postgres", f"CREATE DATABASE {SCRATCH} OWNER {OWNER}")
    try:
        _compose("pg_restore", "-U", OWNER, "-d", SCRATCH, "--exit-on-error", stdin=dump.read_bytes())
        expected, restored = table_counts(source), table_counts(SCRATCH)
        revision = "SELECT version_num FROM alembic_version"
        same_revision = _psql(source, revision) == _psql(SCRATCH, revision)
        mismatches = {
            t: (expected.get(t), restored.get(t))
            for t in expected.keys() | restored.keys()
            if expected.get(t) != restored.get(t)
        }
        rows = sum(restored.values())
        policies = int(_psql(SCRATCH, "SELECT count(*) FROM pg_policies").strip())
        print(
            f"restore check: {len(restored)} tables, {rows} rows, {policies} RLS policies, "
            f"revision {'matches' if same_revision else 'DIFFERS'}"
        )
        for table, (want, got) in sorted(mismatches.items()):
            print(f"  MISMATCH {table}: source {want}, restored {got}")
        return same_revision and not mismatches and policies > 0
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {SCRATCH}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database", default="knowledgeai")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("backup")
    make.add_argument("--out", type=Path, default=ROOT / "data" / "backups")
    make.add_argument("--verify", action="store_true", help="restore the new dump and compare it")
    check = sub.add_parser("verify")
    check.add_argument("dump", type=Path)
    args = parser.parse_args()
    if args.command == "backup":
        path = backup(args.database, args.out)
        return 0 if not args.verify or verify(path, args.database) else 1
    return 0 if verify(args.dump, args.database) else 1


if __name__ == "__main__":
    sys.exit(main())
