"""GET /sessions 列出最近 N 个 session 用于侧边栏

无路径参数 query 可选 limit 控制条数 默认 50

DELETE /sessions/{session_id} 删除 session 与其下所有 rounds
    - 204 删除成功
    - 404 session 不存在
    - 409 session 下还有进行中 round 不允许删除

POST /sessions/batch-delete 批量删除会话
    请求体 { session_ids: string[] }
    - 200 { deleted, skipped, errors[] } 逐条返回结果，不因单条失败而全回滚
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="", tags=["history"])


class BatchDeleteRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=200)


class BatchDeleteResult(BaseModel):
    deleted: int
    skipped: int
    errors: list[str]


@router.get("/sessions")
async def list_sessions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    """列出最近会话 给前端左侧栏初始化用 按 updated_at 降序"""
    storage = request.app.state.storage
    metas = await storage.list_sessions(limit=limit)
    return [m.model_dump(mode="json") for m in metas]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request) -> None:
    """删除指定 session 及其下所有 rounds

    错误码:
        404 session 不存在
        409 session 下还有进行中的 round 提示用户先取消或等其完成
    """
    storage = request.app.state.storage
    try:
        await storage.delete_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
    except ValueError as e:
        # 进行中 round 阻塞删除 返回 409 让前端提示用户
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/sessions/batch-delete", response_model=BatchDeleteResult)
async def batch_delete_sessions(
    body: BatchDeleteRequest,
    request: Request,
) -> BatchDeleteResult:
    """批量删除会话 逐条执行，单条失败不阻塞其他条"""
    storage = request.app.state.storage
    deleted = 0
    skipped = 0
    errors: list[str] = []
    for sid in body.session_ids:
        try:
            await storage.delete_session(sid)
            deleted += 1
        except KeyError:
            # session 不存在，跳过
            skipped += 1
        except ValueError as e:
            # 进行中的 round 阻塞删除
            errors.append(f"{sid}: {e}")
            skipped += 1
    return BatchDeleteResult(deleted=deleted, skipped=skipped, errors=errors)
