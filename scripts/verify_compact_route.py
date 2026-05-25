"""POST /sessions/{session_id}/compact 红绿灯  端到端 mock 验证

跑法:
    conda activate multi-chat
    python scripts/verify_compact_route.py

case:
    1 不存在的 session_id -> 404
    2 session 存在但无 round -> 422
    3 有 round 但全部 reply.state != "done" -> 422
    4 有进行中 round (state=replying) -> 409
    5 正常路径 -> 200 且摘要被写入 mongo

实现要点:
    - 不真调 LLM  patch run_session_summary 返回固定字符串
    - storage 走 mongomock-motor  确保不依赖外部 mongo
    - 不起 uvicorn  直接走 httpx.AsyncClient + ASGITransport 命中 FastAPI app
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

from multichat.core.models import (  # noqa: E402
    AgentRecord,
    ModelCatalogEntry,
    TaskState,
)
from multichat.routes.sessions import router as sessions_router  # noqa: E402
from multichat.storage.mongo import MotorMongoStorage  # noqa: E402

# ============================================================================
# 工具函数
# ============================================================================


async def make_app(storage) -> FastAPI:
    """构造一个最小 FastAPI app  挂上 sessions_router 与 storage state"""
    app = FastAPI()
    app.state.storage = storage
    app.include_router(sessions_router)
    return app


async def make_storage_with_agent(
    *,
    agent_name: str = "agent_x",
    model_id: str = "gpt-4o-mini",
    max_input_tokens: int = 128000,
) -> MotorMongoStorage:
    """建一个 mongomock storage  注入一个可用的 AgentRecord 与 judge_target"""
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "test_db")
    await storage.ensure_indexes()
    rec = AgentRecord(
        name=agent_name,
        display_name="X",
        provider_type="openai_compatible",
        base_url="http://example.com",
        api_key="sk-test",
        model=model_id,
        available_models=[
            ModelCatalogEntry(
                model_id=model_id,
                label=model_id,
                max_input_tokens=max_input_tokens,
            )
        ],
        prompt="you are X",
    )
    await storage._db["agents"].insert_one(rec.model_dump())
    # judge_pointer 走 settings 集合
    await storage.set_judge_target(agent_name)
    return storage


def _expect(cond: bool, msg: str, ctx: str) -> None:
    """断言糖  失败立刻 raise  case 名+ctx 一起打日志"""
    if not cond:
        raise AssertionError(f"[FAIL] {ctx}: {msg}")


# ============================================================================
# case 1: 不存在的 session_id -> 404
# ============================================================================


async def case1_session_not_found() -> None:
    storage = await make_storage_with_agent()
    app = await make_app(storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post("/sessions/not-exist/compact")
    _expect(resp.status_code == 404, f"status={resp.status_code}", "case1")
    body = resp.json()
    _expect("not found" in body.get("detail", ""), f"detail={body}", "case1")
    print("[PASS] case1 不存在 session -> 404")


# ============================================================================
# case 2: session 存在但无 round -> 422
# ============================================================================


async def case2_no_rounds() -> None:
    storage = await make_storage_with_agent()
    sid = await storage.create_session(title="empty")
    app = await make_app(storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(f"/sessions/{sid}/compact")
    _expect(resp.status_code == 422, f"status={resp.status_code}", "case2")
    body = resp.json()
    _expect(
        body.get("detail") == "no completed rounds to summarize",
        f"detail={body}",
        "case2",
    )
    print("[PASS] case2 session 无 round -> 422")


# ============================================================================
# case 3: 有 round 但全部 reply.state != "done" -> 422
# ============================================================================


async def case3_all_rounds_not_done() -> None:
    storage = await make_storage_with_agent()
    sid = await storage.create_session(title="t")
    # 造两个 round  但 reply.state 都是 streaming  不算 done
    for q in ("q1", "q2"):
        tid = await storage.create_round(sid, q, None)
        await storage.update_round_field(
            tid,
            "reply",
            {"agent": "agent_x", "state": "streaming", "content": "..."},
        )
        # state 留默认 pending  case4 才 patch state
        # 为了避开 case4 重叠  这里先把 round state 改成 done  防止被识别成进行中
        await storage.update_round_state(tid, TaskState.DONE)

    app = await make_app(storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(f"/sessions/{sid}/compact")
    _expect(resp.status_code == 422, f"status={resp.status_code}", "case3")
    body = resp.json()
    _expect(
        body.get("detail") == "no completed rounds to summarize",
        f"detail={body}",
        "case3",
    )
    print("[PASS] case3 reply.state != done -> 422")


# ============================================================================
# case 4: 有进行中 round (state=replying) -> 409
# ============================================================================


async def case4_in_progress() -> None:
    storage = await make_storage_with_agent()
    sid = await storage.create_session(title="t")
    tid = await storage.create_round(sid, "q1", None)
    await storage.update_round_state(tid, TaskState.REPLYING)
    # reply 字段不存在或 state 非 done 也无所谓  状态机已是 replying

    app = await make_app(storage)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(f"/sessions/{sid}/compact")
    _expect(resp.status_code == 409, f"status={resp.status_code}", "case4")
    body = resp.json()
    _expect(
        "in-progress" in body.get("detail", ""),
        f"detail={body}",
        "case4",
    )
    print("[PASS] case4 进行中 round -> 409")


# ============================================================================
# case 5: 正常路径 -> 200 且摘要被写入 mongo
# ============================================================================


FIXED_SUMMARY = "### 1. 会话目标\n固定摘要正文用于红绿灯断言"


async def case5_happy_path() -> None:
    storage = await make_storage_with_agent(
        agent_name="agent_x", model_id="gpt-4o-mini", max_input_tokens=128000
    )
    sid = await storage.create_session(title="t")
    # 造两轮已完成对话
    rounds_meta: list[tuple[str, int]] = []
    for q, a in [("hello", "hi there"), ("ping", "pong long answer " * 50)]:
        tid = await storage.create_round(sid, q, None)
        await storage.update_round_field(
            tid,
            "reply",
            {"agent": "agent_x", "state": "done", "content": a},
        )
        await storage.update_round_state(tid, TaskState.DONE)
        rd = await storage.get_round(tid)
        rounds_meta.append((tid, rd.round_index))

    last_round_index = rounds_meta[-1][1]

    app = await make_app(storage)
    transport = ASGITransport(app=app)

    # patch run_session_summary 返回固定文本  避开真实 LLM
    # 注意打 patch 的对象是 routes.sessions 模块里的引用
    with patch(
        "multichat.routes.sessions.run_session_summary",
        new=AsyncMock(return_value=FIXED_SUMMARY),
    ) as mocked:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post(f"/sessions/{sid}/compact")

    _expect(resp.status_code == 200, f"status={resp.status_code} body={resp.text}", "case5")
    body = resp.json()
    _expect(body["summary"] == FIXED_SUMMARY, f"summary mismatch: {body}", "case5")
    _expect(
        body["summary_until_round"] == last_round_index,
        f"summary_until_round={body['summary_until_round']} expect {last_round_index}",
        "case5",
    )
    _expect(body["max_input_tokens"] == 128000, f"max_input_tokens={body}", "case5")
    _expect(body["model_id"] == "gpt-4o-mini", f"model_id={body}", "case5")
    _expect(
        body["used_tokens_before"] > 0,
        f"used_tokens_before={body['used_tokens_before']}",
        "case5",
    )
    _expect(
        body["used_tokens_after"] > 0,
        f"used_tokens_after={body['used_tokens_after']}",
        "case5",
    )
    _expect(
        body["used_tokens_after"] < body["used_tokens_before"],
        f"压缩后未减少 before={body['used_tokens_before']} after={body['used_tokens_after']}",
        "case5",
    )
    _expect(
        isinstance(body["summary_updated_at"], str) and body["summary_updated_at"],
        f"summary_updated_at empty: {body}",
        "case5",
    )
    # 验证 mocked 被调用一次  入参里 history 有 4 条  old_summary 为空
    _expect(mocked.call_count == 1, f"call_count={mocked.call_count}", "case5")
    call_kwargs = mocked.call_args.kwargs
    _expect(
        len(call_kwargs.get("history", [])) == 4,
        f"history len={len(call_kwargs.get('history', []))}",
        "case5",
    )
    _expect(call_kwargs.get("old_summary") == "", "old_summary 应该是空", "case5")

    # 回查 mongo 确认 summary 已写入
    refreshed = await storage.get_session(sid)
    _expect(refreshed is not None, "session 回查为 None", "case5")
    _expect(refreshed.summary == FIXED_SUMMARY, f"db summary={refreshed.summary}", "case5")
    _expect(
        refreshed.summary_until_round == last_round_index,
        f"db summary_until_round={refreshed.summary_until_round}",
        "case5",
    )
    _expect(
        refreshed.summary_updated_at is not None,
        "db summary_updated_at 为空",
        "case5",
    )
    print("[PASS] case5 正常路径 -> 200  摘要写回 mongo")


# ============================================================================
# main
# ============================================================================


async def main() -> None:
    await case1_session_not_found()
    await case2_no_rounds()
    await case3_all_rounds_not_done()
    await case4_in_progress()
    await case5_happy_path()
    print("\nALL GREEN: 5/5 cases passed")


if __name__ == "__main__":
    asyncio.run(main())
