"""task_manager 摘要相关红绿灯  端到端 mock 验证 #11 #14 #15

跑法
    conda activate multi-chat
    python scripts/verify_task_manager_summary.py

case
    1 _build_history 没有摘要时不注入 system 消息
    2 _build_history 有摘要时注入 system 消息  且过滤 round_index <= summary_until_round
    3 _publish_context_usage 推 context.usage 事件  字段齐全
    4 _maybe_auto_compact 未到阈值时不调 LLM
    5 _maybe_auto_compact 超阈值时触发摘要  写回 storage
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
from multichat.core.models import (  # noqa: E402
    AgentRecord,
    ModelCatalogEntry,
)
from multichat.storage.mongo import MotorMongoStorage  # noqa: E402


def make_settings():
    s = MagicMock()
    s.runtime.history_max_rounds = 10
    s.runtime.http_timeout_seconds = 60
    s.runtime.reply_flush_interval_ms = 200
    return s


def make_registry(names=("A", "B")):
    r = MagicMock()
    r.names = MagicMock(return_value=list(names))
    return r


async def setup_storage_with_session_and_rounds(rounds_data: list[dict]):
    """工具: 建一个 mongomock storage  注入一个 session + 若干 round  返回 (storage, session_id)

    rounds_data 形如:
        [{"question":"q1","reply_content":"a1","reply_state":"done","reply_agent":"A"}, ...]
    """
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "test_db")
    await storage.ensure_indexes()
    sid = await storage.create_session(title="t")
    for d in rounds_data:
        tid = await storage.create_round(sid, d["question"], None)
        await storage.update_round_field(
            tid,
            "reply",
            {
                "agent": d.get("reply_agent", "A"),
                "state": d.get("reply_state", "done"),
                "content": d.get("reply_content", ""),
            },
        )
    return storage, sid


async def case1_build_history_no_summary():
    storage, sid = await setup_storage_with_session_and_rounds([
        {"question": "Q1", "reply_content": "A1"},
        {"question": "Q2", "reply_content": "A2"},
    ])
    tm = TaskManager(storage, make_registry(), make_settings())
    history = await tm._build_history(sid, current_task_id="")
    # 没摘要 不应有 system 条目
    roles = [h["role"] for h in history]
    assert "system" not in roles, roles
    assert roles == ["user", "assistant", "user", "assistant"], roles
    print("case 1 PASS  无摘要不注入 system")


async def case2_build_history_with_summary():
    storage, sid = await setup_storage_with_session_and_rounds([
        {"question": "Q1", "reply_content": "A1"},
        {"question": "Q2", "reply_content": "A2"},
        {"question": "Q3", "reply_content": "A3"},
    ])
    # 写一份摘要  覆盖到 round_index=1
    await storage.update_session_summary(sid, summary="旧摘要正文", summary_until_round=1)

    tm = TaskManager(storage, make_registry(), make_settings())
    history = await tm._build_history(sid, current_task_id="")
    # 第一个应当是 system 摘要
    assert history[0]["role"] == "system", history[0]
    assert "旧摘要正文" in history[0]["content"], history[0]
    # round_index<=1 的 Q1/Q2 必须被过滤  只剩 Q3 那对
    user_msgs = [h for h in history if h["role"] == "user"]
    assert len(user_msgs) == 1, user_msgs
    assert user_msgs[0]["content"] == "Q3", user_msgs
    print(f"case 2 PASS  注入 system 摘要 + 过滤已覆盖 round  剩 {len(history)} 条")


async def case3_publish_context_usage():
    storage, sid = await setup_storage_with_session_and_rounds([
        {"question": "Q1", "reply_content": "A1", "reply_agent": "A"},
    ])
    # 模拟 storage.get_agent 返回一个有 max_input_tokens 的 record
    rec = AgentRecord(
        name="A", display_name="A", base_url="x", api_key="k", model="m1",
        prompt="abcde",
        available_models=[ModelCatalogEntry(model_id="m1", label="M1", max_input_tokens=200000)],
    )
    storage.get_agent = AsyncMock(return_value=rec)

    tm = TaskManager(storage, make_registry(), make_settings())
    # 拿 task_id
    rounds = await storage.list_rounds(sid)
    tid = rounds[0].task_id

    # 假 hub  收 published 事件
    published = []

    class FakeHub:
        async def publish(self, ev):
            published.append(ev)

    await tm._publish_context_usage(tid, "A", FakeHub())
    assert len(published) == 1, published
    ev = published[0]
    assert ev.type == "context.usage", ev
    for k in ("used_tokens", "threshold_tokens", "max_input_tokens", "ratio", "model_id"):
        assert k in ev.data, (k, ev.data)
    assert ev.data["max_input_tokens"] == 200000
    assert ev.data["model_id"] == "m1"
    print(f"case 3 PASS  推 context.usage  used={ev.data['used_tokens']} ratio={ev.data['ratio']}")


async def case4_auto_compact_not_triggered():
    storage, sid = await setup_storage_with_session_and_rounds([
        {"question": "短", "reply_content": "短", "reply_agent": "A"},
    ])
    rec = AgentRecord(
        name="A", display_name="A", base_url="x", api_key="k", model="m1",
        prompt="abcde",
        available_models=[ModelCatalogEntry(model_id="m1", label="M1", max_input_tokens=200000)],
    )
    storage.get_agent = AsyncMock(return_value=rec)
    storage.get_judge_target = AsyncMock(return_value="A")

    tm = TaskManager(storage, make_registry(), make_settings())

    with patch("multichat.core.task_manager.run_session_summary") as mock_run:
        mock_run.return_value = "(不应被调用)"
        await tm._maybe_auto_compact(sid)
        assert not mock_run.called, "未到阈值不应调 LLM"

    s = await storage.get_session(sid)
    assert s.summary == "", s
    print("case 4 PASS  未到阈值不调 LLM")


async def case5_auto_compact_triggered():
    # 造一段超长内容  让 token 超过 200000 × 80%
    long_text = "撑长上下文文本 " * 30000  # ~30 万字符  约 7.5 万 token
    storage, sid = await setup_storage_with_session_and_rounds([
        {"question": long_text, "reply_content": long_text, "reply_agent": "A"},
        {"question": long_text, "reply_content": long_text, "reply_agent": "A"},
    ])
    rec = AgentRecord(
        name="A", display_name="A", base_url="x", api_key="k", model="m1",
        prompt="abcde",
        # 设小窗口让阈值容易触发  10 万 token 窗口 阈值 8 万
        available_models=[ModelCatalogEntry(model_id="m1", label="M1", max_input_tokens=100000)],
    )
    storage.get_agent = AsyncMock(return_value=rec)
    storage.get_judge_target = AsyncMock(return_value="A")

    tm = TaskManager(storage, make_registry(), make_settings())

    fake_summary = "新摘要内容"
    with patch("multichat.core.task_manager.run_session_summary", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = fake_summary
        await tm._maybe_auto_compact(sid)
        assert mock_run.called, "应触发 LLM"

    s = await storage.get_session(sid)
    assert s.summary == fake_summary, s.summary
    assert s.summary_until_round >= 1, s.summary_until_round
    print(f"case 5 PASS  触发 LLM 摘要  summary_until_round={s.summary_until_round}")


async def main():
    await case1_build_history_no_summary()
    await case2_build_history_with_summary()
    await case3_publish_context_usage()
    await case4_auto_compact_not_triggered()
    await case5_auto_compact_triggered()
    print()
    print("全部用例通过")


if __name__ == "__main__":
    asyncio.run(main())
