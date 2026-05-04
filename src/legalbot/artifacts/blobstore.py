"""BlobStore abstraction (local FS default, S3 stub). Shared by artifacts + attachments."""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path

from legalbot.core.config import get_settings


class BlobStore(ABC):
    """Async blob put/get/delete keyed by string reference."""

    @abstractmethod
    async def put(self, key: str, data: bytes, *, mime: str | None = None) -> str:
        """Persist bytes and return the canonical ref (ok to equal `key`)."""

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...


class LocalBlobStore(BlobStore):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # Keys are forward-slash separated logical paths; safe join.
        clean = key.replace("..", "_").lstrip("/")
        p = self.root / clean
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def put(self, key: str, data: bytes, *, mime: str | None = None) -> str:
        path = self._resolve(key)
        await asyncio.to_thread(path.write_bytes, data)
        return key

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            await asyncio.to_thread(os.remove, path)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._resolve(key).exists)


class S3BlobStore(BlobStore):  # pragma: no cover — stub for prod
    """Stub. Wire up aiobotocore / boto3 in prod."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    async def put(self, key: str, data: bytes, *, mime: str | None = None) -> str:
        raise NotImplementedError("S3BlobStore is a stub; configure boto3 in prod")

    async def get(self, key: str) -> bytes:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def exists(self, key: str) -> bool:
        raise NotImplementedError


_store: BlobStore | None = None


def get_blob_store() -> BlobStore:
    global _store
    if _store is None:
        settings = get_settings()
        if settings.BLOB_STORE_KIND == "s3":
            if not settings.BLOB_STORE_S3_BUCKET:
                raise RuntimeError("BLOB_STORE_S3_BUCKET must be set when BLOB_STORE_KIND='s3'")
            _store = S3BlobStore(settings.BLOB_STORE_S3_BUCKET)
        else:
            _store = LocalBlobStore(settings.BLOB_STORE_PATH)
    return _store


def reset_blob_store() -> None:
    global _store
    _store = None
