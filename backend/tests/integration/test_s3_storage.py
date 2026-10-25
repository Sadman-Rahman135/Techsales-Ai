"""§16 durable object storage: the S3 adapter against a real S3-compatible server (compose `s3`, SeaweedFS),
held to the same contract as local storage."""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import StorageBackend, get_settings
from app.services.storage import LocalStorage, ObjectStorage, S3Storage

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get("S3_TEST_ENDPOINT_URL", "http://127.0.0.1:8333")


@pytest.fixture(scope="module")
def s3() -> Iterator[S3Storage]:
    settings = get_settings().model_copy(
        update={
            "storage_backend": StorageBackend.S3,
            "s3_bucket": f"knowledgeai-test-{uuid.uuid4().hex[:8]}",
            "s3_endpoint_url": ENDPOINT,
            "s3_access_key_id": SecretStr(os.environ.get("S3_TEST_ACCESS_KEY_ID", "knowledgeai-dev")),
            "s3_secret_access_key": SecretStr(
                os.environ.get("S3_TEST_SECRET_ACCESS_KEY", "knowledgeai-dev-only-s3-secret")
            ),
        }
    )
    storage = S3Storage(settings)
    assert storage.ensure_bucket() is True
    assert storage.ensure_bucket() is False  # idempotent
    yield storage
    for obj in storage.list(""):
        storage.delete(obj.key)
    storage._client.delete_bucket(Bucket=settings.s3_bucket)


@pytest.fixture(params=["local", "s3"])
def storage(request: pytest.FixtureRequest, tmp_path: Path, s3: S3Storage) -> ObjectStorage:
    return LocalStorage(tmp_path) if request.param == "local" else s3


def test_both_backends_keep_the_same_contract(storage: ObjectStorage) -> None:
    org = uuid.uuid4().hex
    key = f"{org}/doc/1.pdf"
    storage.put(key, b"%PDF-1.7 fictional")
    assert storage.exists(key) and storage.get(key) == b"%PDF-1.7 fictional"
    storage.put(key, b"replaced")  # a re-run overwrites, never duplicates
    assert storage.get(key) == b"replaced"
    listed = list(storage.list(f"{org}/"))
    assert [o.key for o in listed] == [key] and listed[0].modified_at.tzinfo == UTC
    storage.delete(key)
    storage.delete(key)  # idempotent
    assert not storage.exists(key) and list(storage.list(f"{org}/")) == []
    with pytest.raises(FileNotFoundError):  # what the API and the worker catch
        storage.get(key)


@pytest.mark.parametrize("key", ["../etc/passwd", "org/../../x", "/abs/path", ""])
def test_unsafe_keys_are_refused_before_any_request(storage: ObjectStorage, key: str) -> None:
    with pytest.raises(ValueError):
        storage.put(key, b"x")


def test_the_client_has_timeouts_and_capped_retries(s3: S3Storage) -> None:
    config = s3._client.meta.config  # §11.3: object storage can't hang a request or a job
    settings = get_settings()
    assert config.connect_timeout == settings.s3_connect_timeout_seconds
    assert config.read_timeout == settings.s3_read_timeout_seconds
    assert config.retries["total_max_attempts"] == settings.s3_max_attempts


def test_an_unreachable_store_fails_fast() -> None:
    settings = get_settings().model_copy(
        update={
            "s3_bucket": "nowhere",
            "s3_endpoint_url": "http://127.0.0.1:9",  # nothing listens on the discard port
            "s3_access_key_id": SecretStr("x"),
            "s3_secret_access_key": SecretStr("y"),
            "s3_connect_timeout_seconds": 1.0,
            "s3_max_attempts": 1,
        }
    )
    with pytest.raises(Exception, match=r"(?i)connect"):
        S3Storage(settings).put("org/doc/1", b"x")
