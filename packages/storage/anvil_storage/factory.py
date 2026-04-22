from __future__ import annotations

import os

from anvil_storage.base import StorageProvider


def get_storage(provider: str | None = None, bucket: str | None = None) -> StorageProvider:
    """Factory: instantiate a storage provider from env or args.

    Concrete impls land in Phase 1 (local + s3/minio first).
    """
    provider = provider or os.getenv("STORAGE_PROVIDER", "local")
    bucket = bucket or os.getenv("STORAGE_BUCKET", "anvil")
    raise NotImplementedError(
        f"storage provider '{provider}' (bucket={bucket}) not yet implemented; "
        "concrete backends land in Phase 1"
    )
