"""对象存储抽象

设计意图：
    - Mongo 只存文件元数据，不直接存头像或 skill 文件本体
    - 文件本体统一通过对象存储接口读写，便于后续接 MinIO / S3
    - 测试阶段先用内存实现，避免真实网络依赖
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class StoredObject(BaseModel):
    """对象存储读取结果"""

    object_key: str
    content: bytes
    mime_type: str
    size: int = Field(..., ge=0)
    sha256: str


@runtime_checkable
class ObjectStore(Protocol):
    """对象存储协议"""

    async def put_bytes(self, object_key: str, payload: bytes, mime_type: str) -> dict: ...

    async def get_bytes(self, object_key: str) -> StoredObject: ...

    async def delete(self, object_key: str) -> None: ...


class InMemoryObjectStore:
    """测试用内存对象存储实现"""

    def __init__(self) -> None:
        self._objects: dict[str, StoredObject] = {}

    async def put_bytes(self, object_key: str, payload: bytes, mime_type: str) -> dict:
        digest = hashlib.sha256(payload).hexdigest()
        stored = StoredObject(
            object_key=object_key,
            content=payload,
            mime_type=mime_type,
            size=len(payload),
            sha256=digest,
        )
        self._objects[object_key] = stored
        return {
            "object_key": object_key,
            "mime_type": mime_type,
            "size": len(payload),
            "sha256": digest,
        }

    async def get_bytes(self, object_key: str) -> StoredObject:
        if object_key not in self._objects:
            raise KeyError(f"object 不存在 key={object_key}")
        return self._objects[object_key]

    async def delete(self, object_key: str) -> None:
        if object_key not in self._objects:
            raise KeyError(f"object 不存在 key={object_key}")
        del self._objects[object_key]
