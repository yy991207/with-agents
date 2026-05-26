"""多 agent 并发回答红绿灯  端到端 mock 验证

跑法
    conda activate multi-chat
    python scripts/verify_multi_agent_flow.py

case
    1 storage.create_round 接 agents + input_mode  落库与 replies 占位正确
    2 _do_reply_for_agent 跑完  replies.<agent>.segments 顺序与字段完整
    3 multi 模式 fan-out 多 agent 并发  全部 done 后 task.state DONE  无自动选答
    4 select_reply 校验 + 推 reply.selected 事件
    5 single 模式 reply 完成自动写 selected_reply_agent
    6 上一轮 multi 未选答时再开新轮 抛 ValueError 等价  路由层会变 409
    7 cancel 单 agent  其它 agent 不受影响
    8 retry_reply 重置某 agent 子任务
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from mongomock_motor import AsyncMongoMockClient  # noqa: E402

from multichat.core.events import TaskEvent  # noqa: E402
from multichat.core.models import TaskState  # noqa: E402
from multichat.core.task_manager import TaskManager  # noqa: E402
from multichat.storage.mongo import MotorMongoStorage  # noqa: E402


# ============================================================ 公共 helpers
def make_settings():
    s = MagicMock()
    s.runtime.history_max_rounds = 10
    s.runtime.http_timeout_seconds = 60
    # 节流间隔设大一点  让 chunk 都进 segments_buf 走封段路径
    s.runtime.reply_flush_interval_ms = 999999
    return s


def make_registry(names=("A", "B", "C", "D")):
    r = MagicMock()
    r.names = MagicMock(return_value=list(names))
    return r


async def make_storage() -> MotorMongoStorage:
    """建一个内存 mongo 实例"""
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "test_db")
    await storage.ensure_indexes()
    return storage


class CollectingHub:
    """事件总线 mock  把 publish 进来的事件全部收下"""

    def __init__(self) -> None:
        self.events: list[TaskEvent] = []

    async def publish(self, ev: TaskEvent) -> None:
        self.events.append(ev)

    async def close(self) -> None:
        pass


# ============================================================ 用例
async def case1_create_round_with_agents() -> None:
    """create_round 接 agents + input_mode  replies 占位填齐"""
    storage = await make_storage()
    sid = await storage.create_session(title="t")

    tid = await storage.create_round(
        sid,
        "Q1",
        None,
        agents=["A", "B"],
        input_mode="multi",
    )
    rounds = await storage.list_rounds(sid)
    assert len(rounds) == 1
    r = rounds[0]
    assert r.agents == ["A", "B"], r.agents
    assert r.input_mode == "multi"
    assert set(r.replies.keys()) == {"A", "B"}, r.replies
    for agent_name, reply in r.replies.items():
        assert reply.get("state") == "pending", (agent_name, reply)
        assert reply.get("content") == ""
        assert reply.get("segments") == []
    assert r.selected_reply_agent is None

    # single 模式 校验
    tid2 = await storage.create_round(
        sid, "Q2", None, agents=["A"], input_mode="single"
    )
    rounds = await storage.list_rounds(sid)
    last = rounds[-1]
    assert last.task_id == tid2
    assert last.agents == ["A"]
    assert last.input_mode == "single"
    print("case 1 PASS  create_round 占位 OK")


async def case2_do_reply_for_agent_segments() -> None:
    """_do_reply_for_agent 跑完  segments 顺序与字段完整"""
    storage = await make_storage()
    sid = await storage.create_session(title="t")
    tid = await storage.create_round(
        sid, "Q", None, agents=["A"], input_mode="single"
    )
    tm = TaskManager(storage, make_registry(), make_settings())

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled=False):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "你好"}))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "我帮你"}))
        await on_event(TaskEvent(type="reply.tool_call", data={"agent": agent_name, "tool": "Read", "input": "{file:'a'}"}))
        await on_event(TaskEvent(type="reply.tool_result", data={"agent": agent_name, "tool": "Read", "result": "abc"}))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "完成。"}))
        return "你好我帮你完成。"

    hub = CollectingHub()
    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        await tm._do_reply_for_agent(
            task_id=tid, agent_name="A", user_message="Q", history=[], hub=hub  # type: ignore[arg-type]
        )

    r = (await storage.list_rounds(sid))[0]
    reply = r.replies["A"]
    assert reply["state"] == "done", reply["state"]
    assert reply["content"] == "你好我帮你完成。", reply["content"]
    types = [s["type"] for s in reply["segments"]]
    assert types == ["text", "tool_call", "tool_result", "text"], types
    print(f"case 2 PASS  replies['A'].segments 共 {len(reply['segments'])} 段")


async def case3_multi_fanout_no_auto_select() -> None:
    """multi 模式 fan-out  全部 done 后 task.state DONE  不自动选答"""
    storage = await make_storage()
    sid = await storage.create_session(title="t")
    tm = TaskManager(storage, make_registry(("A", "B")), make_settings())

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled=False):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": f"hi-{agent_name}"}))
        return f"hi-{agent_name}"

    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        tid = await tm.create_task(
            sid, "Q", agents=["A", "B"], input_mode="multi", thinking_enabled=False
        )
        # 等主任务结束
        await asyncio.wait_for(tm._tasks[tid], timeout=5.0)

    r = (await storage.list_rounds(sid))[0]
    assert r.state == TaskState.DONE
    assert r.replies["A"]["state"] == "done"
    assert r.replies["B"]["state"] == "done"
    # multi 模式不自动选答
    assert r.selected_reply_agent is None, r.selected_reply_agent
    print("case 3 PASS  multi fan-out 全部 done  无自动选答")


async def case4_select_reply() -> None:
    """select_reply 校验  推 reply.selected 事件"""
    storage = await make_storage()
    sid = await storage.create_session(title="t")
    tm = TaskManager(storage, make_registry(("A", "B")), make_settings())

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled=False):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": f"hi-{agent_name}"}))
        return f"hi-{agent_name}"

    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        tid = await tm.create_task(sid, "Q", agents=["A", "B"], input_mode="multi")
        await asyncio.wait_for(tm._tasks[tid], timeout=5.0)

    # task 已结束 hub 已 close  select_reply 走 _publish_context_usage_no_hub 分支
    tm._publish_context_usage_no_hub = AsyncMock()  # type: ignore[method-assign]

    # 校验  agent 不在候选
    try:
        await tm.select_reply(tid, "X")
    except ValueError:
        pass
    else:
        raise AssertionError("select_reply 未拦截不在候选的 agent")

    # 正常选 A
    await tm.select_reply(tid, "A")
    r = (await storage.list_rounds(sid))[0]
    assert r.selected_reply_agent == "A", r.selected_reply_agent
    print("case 4 PASS  select_reply 校验 + 选答落库 OK")


async def case5_single_auto_select() -> None:
    """single 模式 reply 完成自动写 selected_reply_agent"""
    storage = await make_storage()
    sid = await storage.create_session(title="t")
    tm = TaskManager(storage, make_registry(("A",)), make_settings())

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled=False):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "hello"}))
        return "hello"

    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        tid = await tm.create_task(sid, "Q", agents=["A"], input_mode="single")
        await asyncio.wait_for(tm._tasks[tid], timeout=5.0)

    r = (await storage.list_rounds(sid))[0]
    assert r.state == TaskState.DONE
    assert r.selected_reply_agent == "A", r.selected_reply_agent
    print("case 5 PASS  single 模式自动选答 OK")


async def case6_unselected_blocks_next_round() -> None:
    """multi 上一轮未选答  下一轮发提问 ask 路由会拦  这里直接验校验函数"""
    from multichat.routes.ask import _ensure_last_round_selected

    storage = await make_storage()
    sid = await storage.create_session(title="t")
    tm = TaskManager(storage, make_registry(("A", "B")), make_settings())

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled=False):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": f"hi-{agent_name}"}))
        return f"hi-{agent_name}"

    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        tid = await tm.create_task(sid, "Q1", agents=["A", "B"], input_mode="multi")
        await asyncio.wait_for(tm._tasks[tid], timeout=5.0)

    # 未选答时校验函数应抛 HTTPException
    from fastapi import HTTPException
    try:
        await _ensure_last_round_selected(storage, sid)
    except HTTPException as e:
        assert e.status_code == 409
    else:
        raise AssertionError("未选答时校验函数没拦")

    # 选答后再校验  应通过
    tm._publish_context_usage_no_hub = AsyncMock()  # type: ignore[method-assign]
    await tm.select_reply(tid, "A")
    await _ensure_last_round_selected(storage, sid)  # 不抛 = 通过
    print("case 6 PASS  上一轮未选答时拦截下一轮  选答后放行")


async def case7_cancel_single_agent() -> None:
    """cancel 单 agent  其它 agent 不受影响

    用 fake_run_reply 让 A 故意慢一些 给 cancel 留窗口期
    """
    storage = await make_storage()
    sid = await storage.create_session(title="t")
    tm = TaskManager(storage, make_registry(("A", "B")), make_settings())

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled=False):
        if agent_name == "A":
            try:
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                # 这一份 fake 让 task_manager 自己捕到 CancelledError 并写 reply.state cancelled
                raise
        else:
            await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "fast"}))
            return "fast"
        return ""

    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    async def _kick() -> None:
        # 等 A 子任务挂上后 取消它
        await asyncio.sleep(0.05)
        await tm.cancel_task(tid_holder["tid"], "A")

    tid_holder: dict[str, str] = {}
    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        tid = await tm.create_task(sid, "Q", agents=["A", "B"], input_mode="multi")
        tid_holder["tid"] = tid
        kicker = asyncio.create_task(_kick())
        await asyncio.wait_for(tm._tasks[tid], timeout=5.0)
        await kicker

    r = (await storage.list_rounds(sid))[0]
    assert r.replies["A"]["state"] == "cancelled", r.replies["A"]
    assert r.replies["B"]["state"] == "done", r.replies["B"]
    assert r.state == TaskState.DONE  # 整 round 仍走到 DONE
    print("case 7 PASS  cancel 单 agent  其它 agent 不受影响")


async def case8_retry_reply() -> None:
    """retry_reply 重置某 agent  完成后 round 回 DONE"""
    storage = await make_storage()
    sid = await storage.create_session(title="t")
    tm = TaskManager(storage, make_registry(("A", "B")), make_settings())

    call_count = {"A": 0}

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled=False):
        if agent_name == "A":
            call_count["A"] += 1
            text = "first" if call_count["A"] == 1 else "retried"
            await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": text}))
            return text
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "B-resp"}))
        return "B-resp"

    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        tid = await tm.create_task(sid, "Q", agents=["A", "B"], input_mode="multi")
        await asyncio.wait_for(tm._tasks[tid], timeout=5.0)
        # 重答 A
        await tm.retry_reply(tid, "A")
        # 等 retry 子任务收尾
        sub = tm._reply_subtasks.get(tid, {}).get("A")
        if sub is not None:
            await asyncio.wait_for(sub, timeout=5.0)

    r = (await storage.list_rounds(sid))[0]
    assert r.replies["A"]["content"] == "retried", r.replies["A"]["content"]
    assert r.replies["B"]["content"] == "B-resp", r.replies["B"]["content"]
    assert r.state == TaskState.DONE
    assert call_count["A"] == 2
    print("case 8 PASS  retry_reply 重置 A  完成后 round DONE")


async def main() -> None:
    await case1_create_round_with_agents()
    await case2_do_reply_for_agent_segments()
    await case3_multi_fanout_no_auto_select()
    await case4_select_reply()
    await case5_single_auto_select()
    await case6_unselected_blocks_next_round()
    await case7_cancel_single_agent()
    await case8_retry_reply()
    print()
    print("全部用例通过")


if __name__ == "__main__":
    asyncio.run(main())
