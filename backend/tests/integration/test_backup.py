"""§11.3 "PostgreSQL backups and tested restore procedure": back up the test database and restore it."""

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine

from tests.integration.conftest import TEST_DB, make_tenant


def _compose_postgres_running() -> bool:
    if shutil.which("docker") is None:
        return False
    found = subprocess.run(
        ["docker", "compose", "ps", "-q", "postgres"],  # noqa: S607
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    return found.returncode == 0 and bool(found.stdout.strip())


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "db_backup.py"

pytestmark = [
    pytest.mark.integration,
    # CI runs Postgres as a service container, not through compose; the script needs the compose service.
    pytest.mark.skipif(not _compose_postgres_running(), reason="runs pg_dump inside the compose container"),
]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("db_backup", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_backup_restores_with_every_row_and_policy(app_engine: Engine, tmp_path: Path) -> None:
    make_tenant(app_engine)  # committed rows across several tables
    db_backup = _script()
    dump = db_backup.backup(TEST_DB, tmp_path)
    assert dump.stat().st_size > 0
    assert db_backup.verify(dump, TEST_DB) is True
    counts = db_backup.table_counts(TEST_DB)
    assert counts["users"] >= 4 and counts["memberships"] >= 4


def test_a_damaged_backup_fails_the_check(app_engine: Engine, tmp_path: Path) -> None:
    db_backup = _script()
    damaged = tmp_path / "damaged.dump"
    damaged.write_bytes(db_backup.backup(TEST_DB, tmp_path).read_bytes()[:2048])
    with pytest.raises(RuntimeError, match="pg_restore"):
        db_backup.verify(damaged, TEST_DB)
