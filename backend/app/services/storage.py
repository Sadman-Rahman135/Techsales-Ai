"""Original-file storage: a local directory in development, S3 (or an S3-compatible store) when deployed.
Staging and production refuse local storage (app/config.py). Both adapters share one interface and one key
check, and both raise `FileNotFoundError` for a missing object, so callers never know which one they have."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings, StorageBackend, get_settings

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]*$")


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    modified_at: datetime


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def list(self, prefix: str) -> Iterator[StoredObject]: ...


def check_key(key: str) -> str:
    # Keys are generated server-side ("{org}/{document}/{version}.ext"); still, never trust them blindly.
    if not _SAFE_KEY.fullmatch(key) or ".." in key.split("/"):
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / check_key(key)).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError(f"storage key escapes the storage root: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".partial")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: a reader never sees half a file

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> Iterator[StoredObject]:
        base = self._path(prefix.rstrip("/")) if prefix else self._root
        if not base.is_dir():
            return
        for path in base.rglob("*"):
            if path.is_file() and not path.name.endswith(".partial"):
                yield StoredObject(
                    key=path.relative_to(self._root).as_posix(),
                    modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                )


class S3Storage:
    """Objects in one bucket under the same keys. Encryption at rest, versioning and lifecycle rules are
    bucket settings (infra/deploy/s3/), so the app sends plain requests and works with any S3-compatible
    store."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required for STORAGE_BACKEND=s3")
        self._bucket = settings.s3_bucket
        self._client = client or _s3_client(settings)

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=check_key(key), Body=data)  # atomic per object

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=check_key(key))
        except self._client.exceptions.NoSuchKey as exc:
            raise FileNotFoundError(key) from exc
        body: bytes = response["Body"].read()
        return body

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=check_key(key))  # no error if it's already gone

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=check_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def ensure_bucket(self) -> bool:
        """Create the bucket if it's missing; True if it was created. For local S3-compatible stores and the
        rehearsal only: real buckets, with their policies, come from infrastructure (docs/deployment.md)."""
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
            return False
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)
            return True

    def list(self, prefix: str) -> Iterator[StoredObject]:
        pages = self._client.get_paginator("list_objects_v2").paginate(
            Bucket=self._bucket, Prefix=check_key(prefix) if prefix else ""
        )
        for page in pages:
            for item in page.get("Contents", []):
                yield StoredObject(key=item["Key"], modified_at=item["LastModified"].astimezone(UTC))


def _s3_client(settings: Settings) -> Any:
    import boto3
    from botocore.config import Config

    config = Config(
        connect_timeout=settings.s3_connect_timeout_seconds,
        read_timeout=settings.s3_read_timeout_seconds,
        retries={"total_max_attempts": settings.s3_max_attempts, "mode": "standard"},  # incl. the first try
        # S3-compatible stores usually need path-style URLs; AWS works with either.
        s3={"addressing_style": "path" if settings.s3_endpoint_url else "auto"},
    )
    keys = (settings.s3_access_key_id, settings.s3_secret_access_key)
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=keys[0].get_secret_value() if keys[0] else None,
        aws_secret_access_key=keys[1].get_secret_value() if keys[1] else None,
        config=config,
    )


@lru_cache
def get_storage() -> ObjectStorage:
    settings = get_settings()
    if settings.storage_backend is StorageBackend.LOCAL:
        return LocalStorage(settings.storage_local_dir)
    return S3Storage(settings)
