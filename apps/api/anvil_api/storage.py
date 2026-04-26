"""Local-disk storage provider (concrete impl for v0).

S3/Azure/GCS will plug into the same StorageProvider interface in Phase 1.5.
"""
from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path
from typing import BinaryIO

from anvil_api.config import settings


class LocalStorage:
    """Minimal local-disk impl matching anvil_storage.StorageProvider shape."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # prevent path traversal
        clean = key.lstrip("/")
        if ".." in Path(clean).parts:
            raise ValueError(f"unsafe key: {key}")
        p = self.root / clean
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, key: str, data: bytes | BinaryIO, content_type: str | None = None) -> str:
        p = self._path(key)
        if isinstance(data, (bytes, bytearray)):
            p.write_bytes(bytes(data))
        else:
            with open(p, "wb") as f:
                shutil.copyfileobj(data, f)
        return f"local://{key}"

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def delete(self, key: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._path(key))

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def signed_url(self, key: str, expires_seconds: int = 3600) -> str:
        # Local mode: served via /api/files/{key}
        return f"/api/files/{key}"

    def absolute_path(self, key: str) -> str:
        """Local-only helper: useful for ML service which shares the filesystem."""
        return str(self._path(key))


storage = LocalStorage()
