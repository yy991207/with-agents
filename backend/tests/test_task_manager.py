"""TaskManager 单元测试

策略:
    - 通过 monkeypatch 把 task_manager 模块里的 run_think / run_reply / run_judge
      替换成 fake 协程 不发任何网络请求
    - storage 用 mongomock-motor 真实跑通 update_round_field 与 list_rounds
    - DeepAgentRegistry 真实 build 但 ChatOpenAI 仅在 ainvoke 时才触网 这里我们没 ainvoke
    - SSE 事件流通过 TaskEventHub.subscribe() 直接消费 验证状态机推送的事件序列

覆盖场景(对齐 spec §4 状态机):
    1. test_full_flow         全流程 ask → 4 think → user pick → reply → DONE
    2. test_mention_skip_think  @直呼 跳过 think 直接 reply
    3. test_think_one_failed_others_continue  单卡 think 失败 其他成功 available 不含失败者
    4. test_regenerate         decide regenerate 后 thinks 重置 think_history 推一条
    5. test_auto_judge         decide auto 触发 judge → reply
    6. test_global_cancel      cancel scope=global → 状态 CANCELLED 事件正确发出
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest
from mongomock_motor import AsyncMongoMockClient

from multichat.config import (
    AgentConfig,
    JudgeConfig,
    MongoConfig,
    RuntimeConfig,
    Settings,
)
from multichat.core import task_manager as tm_module
from multichat.core.events import TaskEvent, TaskEventHub
from multichat.core.models import TaskState
from multichat.core.task_manager import TaskManager
from multichat.llm.deep_agents import build_registry
from multichat.storage.mongo import MotorMongoStorage


# ----------------------------------------------------------------- 公共 fixture
def _settings() -> Settings:
    """构造测试 settings 4 agent + GLM 任 judge"""
    return Settings(
        key="sk-test-tail",
        base_url="https://example.com/v1",
        agents={
            "DeepSeek": AgentConfig(model="deepseek-test", prompt="深度思考"),
            "GLM": AgentConfig(model="glm-test", prompt="活泼"),
            "Kimi": AgentConfig(model="kimi-test", prompt="温柔"),
            "Qwen": AgentConfig(model="qwen-test", prompt="百科"),
        },
        judge=JudgeConfig(agent="GLM", prompt="你是裁判"),
        mongo=MongoConfig(uri="mongodb://localhost:27017", db="multi_chat_test"),
        runtime=RuntimeConfig(
            history_max_rounds=10,
            reply_flush_interval_ms=10,  # 测试中故意调小让节流写至少触发一次
            http_timeout_seconds=5,
        ),
    )


@pytest.fixture
async def env() -> AsyncIterator[tuple[TaskManager, MotorMongoStorage, TaskEventHub | None]]:
    """构造 TaskManager + storage + registry 已 seed 4 个 agent"""
    settings = _settings()
    client = AsyncMongoMockClient()
    storage = MotorMongoStorage.from_client(client, "multi_chat_test")
    await storage.ensure_indexes()
    await storage.seed_from_yaml(settings)

    registry = build_registry(settings)
    records = await storage.list_agents()
    await registry.initialize(records)

    manager = TaskManager(storage=storage, registry=registry, settings=settings)
    yield manager, storage, None
    await storage.close()


# ---------------------------------------------------------------- 工具函数
async def _drain_events(
    hub: TaskEventHub,
    *,
    stop_types: set[str],
    max_events: int = 200,
    timeout_s: float = 3.0,
) -> list[TaskEvent]:
    """订阅 hub 直到收到 stop_types 中任一 type 或 hub 关流

    返回收集到的全部事件(history + live)
    """
    history, queue = await hub.subscribe()
    collected: list[TaskEvent] = list(history)
    # 检查 history 是否已有终止事件
    if any(ev.type in stop_types for ev in collected):
        await hub.unsubscribe(queue)
        return collected
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if ev is None:
            break
        collected.append(ev)
        if ev.type in stop_types:
            break
        if len(collected) > max_events:
            break
    await hub.unsubscribe(queue)
    return collected


async def _wait_state(
    storage: MotorMongoStorage, task_id: str, expected: TaskState, timeout_s: float = 3.0
) -> None:
    """轮询 round.state 直到等于 expected 超时则抛 TimeoutError"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        r = await storage.get_round(task_id)
        if r is not None and r.state == expected:
            return
        await asyncio.sleep(0.02)
    r = await storage.get_round(task_id)
    raise AssertionError(
        f"等不到状态 {expected} 当前={r.state if r else None}"
    )


async def _wait_subscribers(hub: TaskEventHub, timeout_s: float = 1.0) -> None:
    """等 hub 至少有一个订阅者 防 publish 时点早于 subscribe"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        # 直接读私有 list 这是测试代码 接受耦合
        if hub._subscribers:  # noqa: SLF001
            return
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------- 用例
@pytest.mark.asyncio
async def test_full_flow(env: tuple[TaskManager, MotorMongoStorage, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """全流程 ask → 4 think 全成功 → 选 GLM → reply → DONE"""
    manager, storage, _ = env

    async def fake_think(agent_name, user_message, history, registry, timeout_s):
        return f"我是 {agent_name} 我可以回答"

    async def fake_reply(agent_name, user_message, history, registry, on_event, timeout_s):
        # 流式吐 3 个 chunk
        for chunk in ["你好", " 这是", " 回答"]:
            await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": chunk}))
        return "你好 这是 回答"

    monkeypatch.setattr(tm_module, "run_think", fake_think)
    monkeypatch.setattr(tm_module, "run_reply", fake_reply)

    task_id = await manager.create_task(None, "解释一下量子隧穿")
    hub = manager.get_hub(task_id)
    assert hub is not None

    # 等到 THINK_DONE 后提交决策
    await _wait_state(storage, task_id, TaskState.THINK_DONE)
    await manager.submit_decision(task_id, "GLM")

    # 等待后台 task 完成
    bg = manager._tasks.get(task_id)  # noqa: SLF001
    if bg is not None:
        await asyncio.wait_for(bg, timeout=3)

    # 一次性订阅拿全部 history(此时 hub 已 close)
    history, queue = await hub.subscribe()
    await hub.unsubscribe(queue)
    events = list(history)

    types = [e.type for e in events]
    # 4 路 think.start + 4 路 think.done
    assert types.count("think.start") == 4
    assert types.count("think.done") == 4
    # reply.start / reply.chunk x3 / reply.done 各至少出现一次
    assert types.count("reply.start") == 1
    assert types.count("reply.chunk") >= 3
    assert types.count("reply.done") == 1

    # 数据库终态校验
    r = await storage.get_round(task_id)
    assert r is not None
    assert r.state == TaskState.DONE
    assert r.decision is not None
    assert r.decision.get("choice") == "GLM"
    assert r.reply is not None
    assert r.reply.get("state") == "done"
    assert r.reply.get("content") == "你好 这是 回答"
    # thinks 4 个全部 done
    assert all(v.get("state") == "done" for v in (r.thinks or {}).values())


@pytest.mark.asyncio
async def test_mention_skip_think(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """@Kimi 直呼 跳过 think 直接 reply 4 个 think 全 skipped"""
    manager, storage, _ = env

    async def fake_think(*args, **kwargs):  # 不应被调用
        raise AssertionError("@直呼路径不应触发 run_think")

    async def fake_reply(agent_name, user_message, history, registry, on_event, timeout_s):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "好的"}))
        return "好的"

    monkeypatch.setattr(tm_module, "run_think", fake_think)
    monkeypatch.setattr(tm_module, "run_reply", fake_reply)

    task_id = await manager.create_task(None, "@Kimi 帮我看下")
    hub = manager.get_hub(task_id)
    assert hub is not None

    # 等 task 跑完
    bg = manager._tasks[task_id]  # noqa: SLF001
    await asyncio.wait_for(bg, timeout=3)

    r = await storage.get_round(task_id)
    assert r is not None
    assert r.state == TaskState.DONE
    assert r.decision is not None
    assert r.decision.get("choice") == "Kimi"
    assert r.decision.get("reason") == "user_mention"
    # 4 个 think 全 skipped
    assert all(v.get("state") == "skipped" for v in (r.thinks or {}).values())
    # reply 内容
    assert r.reply is not None
    assert r.reply.get("content") == "好的"


@pytest.mark.asyncio
async def test_think_one_failed_others_continue(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """GLM think 抛异常 其他 3 个 ok available_agents 不含 GLM"""
    manager, storage, _ = env

    async def fake_think(agent_name, user_message, history, registry, timeout_s):
        if agent_name == "GLM":
            raise RuntimeError("模拟 think 失败")
        return f"{agent_name} OK"

    async def fake_reply(agent_name, user_message, history, registry, on_event, timeout_s):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "ok"}))
        return "ok"

    monkeypatch.setattr(tm_module, "run_think", fake_think)
    monkeypatch.setattr(tm_module, "run_reply", fake_reply)

    task_id = await manager.create_task(None, "你好")
    hub = manager.get_hub(task_id)
    assert hub is not None

    await _wait_state(storage, task_id, TaskState.THINK_DONE)

    # snapshot 截至 THINK_DONE 看 available_agents
    history, queue = await hub.subscribe()
    await hub.unsubscribe(queue)
    think_done_state_evs = [
        e for e in history
        if e.type == "task.state" and e.data.get("state") == "THINK_DONE"
    ]
    assert think_done_state_evs, "未收到 THINK_DONE state 事件"
    available = think_done_state_evs[-1].data.get("available_agents") or []
    assert "GLM" not in available
    assert set(available) == {"DeepSeek", "Kimi", "Qwen"}

    # 也校验 think.failed 事件存在
    failed = [e for e in history if e.type == "think.failed"]
    assert len(failed) == 1
    assert failed[0].data["agent"] == "GLM"

    # 用户从可用项中挑一个 让流程继续到 DONE 再校验 round.thinks.GLM 失败状态
    await manager.submit_decision(task_id, "Kimi")
    bg = manager._tasks[task_id]  # noqa: SLF001
    await asyncio.wait_for(bg, timeout=3)

    r = await storage.get_round(task_id)
    assert r is not None
    assert (r.thinks or {}).get("GLM", {}).get("state") == "failed"
    assert (r.thinks or {}).get("Kimi", {}).get("state") == "done"


@pytest.mark.asyncio
async def test_regenerate(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """submit_decision('regenerate') 后 thinks 重置 think_history 推一条 然后再 pick"""
    manager, storage, _ = env

    call_idx = {"n": 0}

    async def fake_think(agent_name, user_message, history, registry, timeout_s):
        call_idx["n"] += 1
        return f"{agent_name}-r{call_idx['n']}"

    async def fake_reply(agent_name, user_message, history, registry, on_event, timeout_s):
        return "done"

    monkeypatch.setattr(tm_module, "run_think", fake_think)
    monkeypatch.setattr(tm_module, "run_reply", fake_reply)

    task_id = await manager.create_task(None, "再来一次")
    hub = manager.get_hub(task_id)
    assert hub is not None

    # 第一次 THINK_DONE 后 regenerate
    await _wait_state(storage, task_id, TaskState.THINK_DONE)
    await manager.submit_decision(task_id, "regenerate")

    # 第二次 THINK_DONE 等到 然后 pick
    # 因为状态先变 THINKING 再 THINK_DONE 这里要分两步等
    # 简化做法: 等 think_history 至少出现一条 再等 THINK_DONE
    await asyncio.sleep(0.05)
    deadline = asyncio.get_event_loop().time() + 3
    while asyncio.get_event_loop().time() < deadline:
        r = await storage.get_round(task_id)
        if r is not None and r.think_history and len(r.think_history) >= 1:
            break
        await asyncio.sleep(0.02)
    r_mid = await storage.get_round(task_id)
    assert r_mid is not None and r_mid.think_history and len(r_mid.think_history) == 1
    # 再等到第二轮 THINK_DONE
    await _wait_state(storage, task_id, TaskState.THINK_DONE)
    await manager.submit_decision(task_id, "DeepSeek")

    bg = manager._tasks[task_id]  # noqa: SLF001
    await asyncio.wait_for(bg, timeout=3)

    r = await storage.get_round(task_id)
    assert r is not None
    assert r.state == TaskState.DONE
    assert r.decision is not None and r.decision.get("choice") == "DeepSeek"
    # think_history 留有一条 第二轮 thinks 是新内容
    assert r.think_history and len(r.think_history) == 1
    # 第二轮 think 内容包含 r5/r6/r7/r8 等(因 call_idx 累计)
    new_thinks = r.thinks or {}
    for name, info in new_thinks.items():
        # 第二轮调用从第 5 次开始
        assert info.get("state") == "done"
        assert info.get("content", "").startswith(name + "-r")


@pytest.mark.asyncio
async def test_auto_judge(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """auto 决策 触发 run_judge → reply"""
    manager, storage, _ = env

    async def fake_think(agent_name, user_message, history, registry, timeout_s):
        return f"{agent_name} 行"

    async def fake_judge(judge_agent_name, user_message, thinks, registry, timeout_s):
        return "Qwen"

    async def fake_reply(agent_name, user_message, history, registry, on_event, timeout_s):
        await on_event(TaskEvent(type="reply.chunk", data={"agent": agent_name, "chunk": "答"}))
        return "答"

    monkeypatch.setattr(tm_module, "run_think", fake_think)
    monkeypatch.setattr(tm_module, "run_reply", fake_reply)
    monkeypatch.setattr(tm_module, "run_judge", fake_judge)

    task_id = await manager.create_task(None, "问个问题")
    hub = manager.get_hub(task_id)
    assert hub is not None

    await _wait_state(storage, task_id, TaskState.THINK_DONE)
    await manager.submit_decision(task_id, "auto")

    bg = manager._tasks[task_id]  # noqa: SLF001
    await asyncio.wait_for(bg, timeout=3)

    history, queue = await hub.subscribe()
    await hub.unsubscribe(queue)
    types = [e.type for e in history]
    assert "judge.start" in types
    assert "judge.done" in types

    r = await storage.get_round(task_id)
    assert r is not None
    assert r.state == TaskState.DONE
    assert r.decision is not None
    assert r.decision.get("choice") == "Qwen"
    assert r.decision.get("reason") == "auto_judge"


@pytest.mark.asyncio
async def test_global_cancel(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """cancel scope=global 状态机走入 CANCELLED"""
    manager, storage, _ = env

    # 让 think 协程长时间挂着 给 cancel 一个机会
    async def fake_think(agent_name, user_message, history, registry, timeout_s):
        await asyncio.sleep(2.0)
        return "x"

    async def fake_reply(*args, **kwargs):
        return ""

    monkeypatch.setattr(tm_module, "run_think", fake_think)
    monkeypatch.setattr(tm_module, "run_reply", fake_reply)

    task_id = await manager.create_task(None, "请问")
    hub = manager.get_hub(task_id)
    assert hub is not None

    # 等 task.state THINKING 出来再发 cancel
    await asyncio.sleep(0.05)
    await manager.cancel_task(task_id, "global")

    bg = manager._tasks.get(task_id)  # noqa: SLF001
    if bg is not None:
        # _run_task_loop 在 finally 里会消化 CancelledError 不会抛到这里
        await asyncio.wait_for(bg, timeout=3)

    r = await storage.get_round(task_id)
    assert r is not None
    assert r.state == TaskState.CANCELLED
