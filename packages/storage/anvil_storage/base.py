from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class StorageProvider(ABC):
    """Provider-agnostic blob storage interface.

    All paths are bucket-relative keys, e.g. "datasets/12/images/abc.jpg".
    """

    @abstractmethod
    def put(self, key: str, data: bytes | BinaryIO, content_type: str | None = None) -> str:
        """Upload bytes/stream. Returns storage URI (e.g. s3://bucket/key)."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Fetch object bytes."""

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def signed_url(self, key: str, expires_seconds: int = 3600) -> str:
        """Pre-signed URL for direct browser download/upload."""
