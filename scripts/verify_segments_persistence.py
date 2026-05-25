"""reply.segments 时间线持久化红绿灯  端到端 mock 验证 #22 #23 #24

跑法
    conda activate multi-chat
    python scripts/verify_segments_persistence.py

case
    1 storage.update_reply_segments 能直接覆盖写 reply.segments
    2 _do_reply 在 chunk + tool_call + tool_result + chunk + tool_call + chunk 序列下
      最终 reply.segments 顺序与字段完整  且 reply.content 与拼接文本一致
    3 chunk 之间夹的 tool_call 可以正确把当前 text 段封段后再追加 tool_call 段
      不出现 tool 之前的 text 与 tool 之后的 text 合并丢顺序
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from mongomock_motor import AsyncMongoMockClient  # noqa: E402

from multichat.core.events import TaskEvent  # noqa: E402
from multichat.core.task_manager import TaskManager  # noqa: E402
from multichat.storage.mongo import MotorMongoStorage  # noqa: E402


def make_settings():
    s = MagicMock()
    s.runtime.history_max_rounds = 10
    s.runtime.http_timeout_seconds = 60
    # 节流间隔设大一点  让 chunk 都进 segments_buf 走封段路径  避免被 append_reply_chunk 抢节流
    s.runtime.reply_flush_interval_ms = 999999
    return s


def make_registry(names=("A",)):
    r = MagicMock()
    r.names = MagicMock(return_value=list(names))
    return r


async def make_storage_with_round() -> tuple[MotorMongoStorage, str, str]:
    """建一个内存 mongo  插一个 session + 一个 round  返回 storage / sid / tid"""
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "test_db")
    await storage.ensure_indexes()
    sid = await storage.create_session(title="t")
    tid = await storage.create_round(sid, "Q1", None)
    return storage, sid, tid


async def case1_update_reply_segments():
    """直接调 storage.update_reply_segments  能整组覆盖写 reply.segments"""
    storage, sid, tid = await make_storage_with_round()
    # 先把 reply 父对象建好  模拟 _do_reply 第一次 update_round_field 的效果
    await storage.update_round_field(
        tid,
        "reply",
        {"agent": "A", "state": "streaming", "content": "", "segments": []},
    )
    segs = [
        {"type": "text", "content": "你好"},
        {"type": "tool_call", "tool": "Read", "input": "{...}"},
        {"type": "tool_result", "tool": "Read", "result": "ok"},
    ]
    await storage.update_reply_segments(tid, segs)
    rounds = await storage.list_rounds(sid)
    reply = rounds[0].reply
    assert reply is not None, reply
    got = reply.get("segments")
    assert got == segs, got
    print("case 1 PASS  update_reply_segments 整组覆盖写")


async def case2_do_reply_segments_full_pipeline():
    """跑一遍 _do_reply  断言 segments 顺序 + 字段完整 + content 拼接正确"""
    storage, sid, tid = await make_storage_with_round()
    tm = TaskManager(storage, make_registry(), make_settings())

    # 假 hub  事件全收住
    published: list[TaskEvent] = []

    class FakeHub:
        async def publish(self, ev: TaskEvent) -> None:
            published.append(ev)

    # mock run_reply  按指定顺序触发 chunk / tool_call / tool_result 事件
    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "你好"}))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "我帮你"}))
        await on_event(TaskEvent(
            type="reply.tool_call",
            data={"agent": agent_name, "tool": "Read", "input": "{file:'a'}"},
        ))
        await on_event(TaskEvent(
            type="reply.tool_result",
            data={"agent": agent_name, "tool": "Read", "result": "abc"},
        ))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "读完了"}))
        await on_event(TaskEvent(
            type="reply.tool_call",
            data={"agent": agent_name, "tool": "Bash", "input": "ls"},
        ))
        await on_event(TaskEvent(
            type="reply.tool_result",
            data={"agent": agent_name, "tool": "Bash", "result": "file1\nfile2"},
        ))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "完成。"}))
        return "你好我帮你读完了完成。"

    # 把 _publish_context_usage 短路掉  这一步走真实代码会查 storage.get_agent  本测试不关心
    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        await tm._do_reply(
            task_id=tid,
            agent_name="A",
            user_message="Q1",
            history=[],
            hub=FakeHub(),
        )

    rounds = await storage.list_rounds(sid)
    reply = rounds[0].reply
    assert reply is not None
    assert reply["state"] == "done", reply["state"]
    assert reply["content"] == "你好我帮你读完了完成。", reply["content"]

    segs = reply["segments"]
    # 期望:  text("你好我帮你") / tool_call(Read) / tool_result(Read) / text("读完了") /
    #        tool_call(Bash) / tool_result(Bash) / text("完成。")
    expected_types = [
        "text", "tool_call", "tool_result", "text", "tool_call", "tool_result", "text",
    ]
    got_types = [s["type"] for s in segs]
    assert got_types == expected_types, got_types

    assert segs[0]["content"] == "你好我帮你", segs[0]
    assert segs[1] == {"type": "tool_call", "tool": "Read", "input": "{file:'a'}"}, segs[1]
    assert segs[2] == {"type": "tool_result", "tool": "Read", "result": "abc"}, segs[2]
    assert segs[3]["content"] == "读完了", segs[3]
    assert segs[4] == {"type": "tool_call", "tool": "Bash", "input": "ls"}, segs[4]
    assert segs[5] == {"type": "tool_result", "tool": "Bash", "result": "file1\nfile2"}, segs[5]
    assert segs[6]["content"] == "完成。", segs[6]
    print(f"case 2 PASS  segments 顺序与字段完整  共 {len(segs)} 段")


async def case3_text_around_tools_not_merged():
    """tool_call 前后的 text 必须各自独立成段  不能被合并"""
    storage, sid, tid = await make_storage_with_round()
    tm = TaskManager(storage, make_registry(), make_settings())

    class FakeHub:
        async def publish(self, ev: TaskEvent) -> None:
            pass

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "前"}))
        await on_event(TaskEvent(
            type="reply.tool_call",
            data={"agent": agent_name, "tool": "T", "input": "i"},
        ))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "后"}))
        return "前后"

    tm._publish_context_usage = AsyncMock()  # type: ignore[method-assign]

    with patch("multichat.core.task_manager.run_reply", new=fake_run_reply):
        await tm._do_reply(
            task_id=tid, agent_name="A", user_message="Q", history=[], hub=FakeHub(),
        )

    rounds = await storage.list_rounds(sid)
    segs = rounds[0].reply["segments"]
    assert [s["type"] for s in segs] == ["text", "tool_call", "text"], segs
    assert segs[0]["content"] == "前", segs[0]
    assert segs[2]["content"] == "后", segs[2]
    print("case 3 PASS  tool 前后的 text 各自独立成段")


async def main():
    await case1_update_reply_segments()
    await case2_do_reply_segments_full_pipeline()
    await case3_text_around_tools_not_merged()
    print()
    print("全部用例通过")


if __name__ == "__main__":
    asyncio.run(main())
