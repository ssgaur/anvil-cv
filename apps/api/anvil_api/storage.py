"""Local-disk storage provider (concrete impl for v0).

S3/Azure/GCS will plug into the same StorageProvider interface in Phase 1.5.

Local-mode `signed_url` builds an HMAC-SHA256-signed URL with an expiry so the
file-serving endpoint (`/api/files/{key}`) can be hit by `<img src>` from the
browser without an Authorization header.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import os
import shutil
import time
from pathlib import Path
from typing import BinaryIO

from anvil_api.config import settings


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _sign(key: str, exp: int) -> str:
    msg = f"{key}|{exp}".encode()
    digest = hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).digest()
    return _b64u(digest)


def verify_file_signature(key: str, exp: int | str | None, sig: str | None) -> bool:
    """Validate the HMAC signature for a file URL.

    Returns False when the sig is missing, expired, or doesn't match.
    """
    if not sig or exp is None:
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    expected = _sign(key, exp_i)
    return hmac.compare_digest(expected, sig)


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
        # Local mode: served via /api/files/{key} with HMAC signature query.
        exp = int(time.time()) + expires_seconds
        sig = _sign(key, exp)
        return f"/api/files/{key}?exp={exp}&sig={sig}"

    def absolute_path(self, key: str) -> str:
        """Local-only helper: useful for ML service which shares the filesystem."""
        return str(self._path(key))


storage = LocalStorage()
