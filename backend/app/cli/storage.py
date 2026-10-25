"""Object storage housekeeping for local S3-compatible stores (compose `s3`, the deployment rehearsal).

    python -m app.cli.storage --ensure-bucket   # create S3_BUCKET if missing; then write, read, delete

In staging and production the bucket, its policy and lifecycle rules are created by infrastructure
(infra/deploy/s3/, docs/deployment.md); this command refuses to create buckets there.
"""

import argparse
import secrets

from app.config import AppEnv, StorageBackend, get_settings
from app.services.storage import S3Storage


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ensure-bucket", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.storage_backend is not StorageBackend.S3:
        raise SystemExit("STORAGE_BACKEND is not s3: nothing to do")
    storage = S3Storage(settings)
    if args.ensure_bucket:
        if settings.app_env in (AppEnv.STAGING, AppEnv.PRODUCTION):
            raise SystemExit("buckets are created by infrastructure in staging/production, not by the app")
        print(f"bucket {settings.s3_bucket}: {'created' if storage.ensure_bucket() else 'exists'}")
    key = f"healthcheck/{secrets.token_hex(8)}"
    storage.put(key, b"ok")
    if storage.get(key) != b"ok":
        raise SystemExit(f"bucket {settings.s3_bucket}: read back something else than was written")
    storage.delete(key)
    print(f"bucket {settings.s3_bucket}: write, read and delete work")


if __name__ == "__main__":
    main()
