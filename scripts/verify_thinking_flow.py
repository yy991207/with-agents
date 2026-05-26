"""thinking 模式红绿灯  端到端 mock 验证 #31~#36

跑法
    conda activate multi-chat
    python scripts/verify_thinking_flow.py

case
    1 storage.create_round 接 thinking_enabled 落到 round 顶层字段
    2 ChatOpenAI 在 thinking_enabled=True 时拿到 model_kwargs={"extra_body":...}
    3 _chunk_to_reasoning 能从 chunk.additional_kwargs.reasoning_content 取 reasoning
    4 _do_reply 处理 reply.thinking 流  最终 segments 顺序: thinking → text  reply.content 不含 reasoning
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from mongomock_motor import AsyncMongoMockClient  # noqa: E402

from multichat.core.events import TaskEvent  # noqa: E402
from multichat.core.task_manager import TaskManager  # noqa: E402
from multichat.llm.agent_runner import _chunk_to_reasoning  # noqa: E402
from multichat.storage.mongo import MotorMongoStorage  # noqa: E402


def make_settings():
    s = MagicMock()
    s.runtime.history_max_rounds = 10
    s.runtime.http_timeout_seconds = 60
    # 节流大点  让 thinking / text 都能进段缓冲再封段
    s.runtime.reply_flush_interval_ms = 999999
    return s


def make_registry(names=("A",)):
    r = MagicMock()
    r.names = MagicMock(return_value=list(names))
    return r


async def make_storage_with_session_round(thinking_enabled: bool = False):
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "test_db")
    await storage.ensure_indexes()
    sid = await storage.create_session(title="t")
    tid = await storage.create_round(
        sid, "Q1", None, thinking_enabled=thinking_enabled
    )
    return storage, sid, tid


async def case1_create_round_thinking_enabled():
    """create_round 透传 thinking_enabled  并落到 round 顶层"""
    storage, sid, _tid = await make_storage_with_session_round(thinking_enabled=True)
    rounds = await storage.list_rounds(sid)
    assert len(rounds) == 1
    assert rounds[0].thinking_enabled is True, rounds[0]
    # 默认 false 路径
    storage2, sid2, _tid2 = await make_storage_with_session_round(
        thinking_enabled=False
    )
    rs2 = await storage2.list_rounds(sid2)
    print(
        "case 1 PASS  create_round 透传 thinking_enabled  "
        f"开启:{rounds[0].thinking_enabled}  默认:{rs2[0].thinking_enabled}"
    )


async def case2_chatopenai_model_kwargs_thinking():
    """_build_one 在 thinking_enabled=True 时给 ChatOpenAI 注入 extra_body"""
    from multichat.core.models import AgentRecord, ModelCatalogEntry
    from multichat.llm import deep_agents as da

    rec = AgentRecord(
        name="A",
        display_name="A",
        base_url="http://x",
        api_key="sk-test",
        model="m1",
        prompt="abcde",
        available_models=[
            ModelCatalogEntry(model_id="m1", label="M1", max_input_tokens=200000)
        ],
    )

    captured = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeAgent:
        pass

    async def _go(thinking: bool):
        captured.clear()
        # 现在 _build_one 用 ReasoningChatOpenAI 替代 ChatOpenAI  patch 那个名字
        with patch.object(da, "ReasoningChatOpenAI", _FakeChatOpenAI), patch.object(
            da, "create_deep_agent", return_value=_FakeAgent()
        ):
            await da._build_one(
                rec, "reply", make_settings(), thinking_enabled=thinking
            )
        return dict(captured)

    cap_on = await _go(True)
    cap_off = await _go(False)

    # 顶层 extra_body 路径  开启时存在  关闭时不存在
    assert cap_on.get("extra_body") == {
        "thinking": {"type": "enabled"}
    }, cap_on.get("extra_body")
    assert "extra_body" not in cap_off, cap_off
    print("case 2 PASS  ChatOpenAI extra_body 注入正确  开启:有  关闭:无")


def case3_chunk_to_reasoning():
    """_chunk_to_reasoning 从 chunk.additional_kwargs 各候选 key 取 reasoning"""
    chunk1 = SimpleNamespace(additional_kwargs={"reasoning_content": "我在思考..."})
    assert _chunk_to_reasoning(chunk1) == "我在思考...", chunk1
    chunk2 = SimpleNamespace(additional_kwargs={"reasoning": "thinking..."})
    assert _chunk_to_reasoning(chunk2) == "thinking...", chunk2
    chunk3 = SimpleNamespace(additional_kwargs={})
    assert _chunk_to_reasoning(chunk3) == ""
    chunk4 = SimpleNamespace(additional_kwargs=None)
    assert _chunk_to_reasoning(chunk4) == ""
    chunk5 = None
    assert _chunk_to_reasoning(chunk5) == ""
    # 优先 reasoning_content
    chunk6 = SimpleNamespace(
        additional_kwargs={"reasoning_content": "win", "reasoning": "lose"}
    )
    assert _chunk_to_reasoning(chunk6) == "win"
    print("case 3 PASS  _chunk_to_reasoning 各候选 key 正确取值")


async def case4_do_reply_thinking_segments():
    """_do_reply 走完一遍  thinking + text 段顺序与字段完整  content 不含 reasoning"""
    storage, sid, tid = await make_storage_with_session_round(thinking_enabled=True)
    tm = TaskManager(storage, make_registry(), make_settings())

    class FakeHub:
        async def publish(self, ev: TaskEvent) -> None:
            pass

    async def fake_run_reply(*, agent_name, user_message, history, registry, on_event, timeout_s, thinking_enabled):
        # reasoning model 通常先吐 reasoning  后吐正文
        await on_event(TaskEvent(type="reply.thinking", data={"agent": agent_name, "chunk": "我先看一下用户问"}))
        await on_event(TaskEvent(type="reply.thinking", data={"agent": agent_name, "chunk": "什么"}))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "你好"}))
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "这是答案"}))
        return "你好这是答案"

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
    # reply.content 不应该含 reasoning  那是 reasoning_content 走另外一条路
    assert reply["content"] == "你好这是答案", reply["content"]
    segs = reply["segments"]
    types = [s["type"] for s in segs]
    assert types == ["thinking", "text"], types
    assert segs[0]["content"] == "我先看一下用户问什么", segs[0]
    assert segs[1]["content"] == "你好这是答案", segs[1]
    print(f"case 4 PASS  _do_reply thinking + text 段顺序与字段完整  thinking={segs[0]['content'][:8]}…")


async def main():
    await case1_create_round_thinking_enabled()
    await case2_chatopenai_model_kwargs_thinking()
    case3_chunk_to_reasoning()
    await case4_do_reply_thinking_segments()
    print()
    print("全部用例通过")


if __name__ == "__main__":
    asyncio.run(main())
