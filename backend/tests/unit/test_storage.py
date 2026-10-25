from pathlib import Path

import pytest

from app.services.storage import LocalStorage


def test_put_exists_delete(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put("org/doc/1", b"data")
    assert storage.exists("org/doc/1")
    storage.delete("org/doc/1")
    assert not storage.exists("org/doc/1")
    storage.delete("org/doc/1")  # idempotent


@pytest.mark.parametrize("key", ["../etc/passwd", "org/../../x", "/abs/path", "C:/windows", ""])
def test_keys_cannot_escape_the_storage_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put(key, b"x")
