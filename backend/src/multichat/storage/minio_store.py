"""MinIO 对象存储实现

当前阶段提供真实可用实现，用于头像和后续 skill 文件存储。
"""

from __future__ import annotations

import hashlib
from io import BytesIO

from minio import Minio

from .object_store import ObjectStore, StoredObject


class MinioObjectStore(ObjectStore):
    """MinIO 对象存储实现"""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        self._bucket = bucket
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def _ensure_bucket(self) -> None:
        """确保 bucket 存在，不存在则创建"""
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def put_bytes(self, object_key: str, payload: bytes, mime_type: str) -> dict:
        self._ensure_bucket()
        digest = hashlib.sha256(payload).hexdigest()
        self._client.put_object(
            self._bucket,
            object_key,
            BytesIO(payload),
            len(payload),
            content_type=mime_type,
        )
        return {
            "object_key": object_key,
            "mime_type": mime_type,
            "size": len(payload),
            "sha256": digest,
        }

    async def get_bytes(self, object_key: str) -> StoredObject:
        response = self._client.get_object(self._bucket, object_key)
        try:
            content = response.read()
        finally:
            response.close()
            response.release_conn()
        stat = self._client.stat_object(self._bucket, object_key)
        return StoredObject(
            object_key=object_key,
            content=content,
            mime_type=stat.content_type or "application/octet-stream",
            size=int(stat.size or len(content)),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def delete(self, object_key: str) -> None:
        self._client.remove_object(self._bucket, object_key)
