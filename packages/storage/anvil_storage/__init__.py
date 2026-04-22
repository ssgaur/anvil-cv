"""Pluggable storage abstraction for Anvil.

Backends:
  - local      : filesystem
  - s3         : AWS S3 (or any S3-compatible: MinIO, R2, Backblaze)
  - minio      : alias for s3 with MinIO defaults
  - azure      : Azure Blob Storage
  - gcs        : Google Cloud Storage
"""
from anvil_storage.base import StorageProvider
from anvil_storage.factory import get_storage

__all__ = ["StorageProvider", "get_storage"]
