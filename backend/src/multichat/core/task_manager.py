"""任务编排器 推进 think-then-choose 状态机

每个 task 一个后台 asyncio.Task 跑 _run_task_loop
SSE 通过 TaskEventHub 桥接 路由层只看 hub 不直接拿 task

设计要点:
    - hub/task/decision_event 都按 task_id 索引
    - 状态机在 task 内串行推进 不会出现并发改 round 的情况
    - 单 think 子任务允许独立 cancel 不影响兄弟 think
    - reply 阶段流式 chunk 写库走节流 减少 mongo 压力
    - 异常分级捕获 think 单卡失败不影响整体 全局未捕获异常落到 task.unrecoverable
异步对象与事件循环绑定问题(参考全局规范):
    - hub 与各 asyncio.Task / Event 都在 create_task 调用所在 loop 创建
    - 子任务由 asyncio.create_task 拉起 自动在同一 loop 不会跨 loop
    - storage 客户端在 fastapi lifespan 创建 与 task_manager 共享同一 loop
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from ..llm.agent_runner import run_judge, run_reply, run_think
from ..llm.deep_agents import DeepAgentRegistry
from ..llm.summarization import run_session_summary
from ..llm.token_counter import (
    count_history_tokens,
    should_trigger_summary,
    usage_payload,
)
from .errors import humanize_llm_error
from .events import TaskEvent, TaskEventHub
from .mention_parser import parse_single_mention
from .models import TaskState

_logger = structlog.get_logger(__name__)


def _now_iso() -> str:
    """统一带时区 ISO 时间戳 落库与事件 payload 共用"""
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    """任务管理器 单例形式由应用工厂注入

    路由层职责:
        - POST /ask 调 create_task 拿 task_id
        - GET /sse 调 get_hub 拿 hub 然后桥接到 SSE
        - POST /decide 调 submit_decision 唤醒等待中的 task
        - POST /cancel 调 cancel_task 取消 task 或单 agent
        - POST /retry-think 调 retry_think 当前抛 NotImplementedError
    """

    def __init__(
        self,
        storage: Any,
        registry: DeepAgentRegistry,
        settings: Any,
    ) -> None:
        self._storage = storage
        self._registry = registry
        self._settings = settings
        # 进行中 task 的状态字典 全部按 task_id 索引
        self._hubs: dict[str, TaskEventHub] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # 等待用户决策时的 event 与结果暂存
        self._decision_events: dict[str, asyncio.Event] = {}
        self._decision_results: dict[str, str] = {}
        # 单 think 子任务索引 用于 cancel scope=AgentName
        self._think_subtasks: dict[str, dict[str, asyncio.Task[None]]] = {}
        # 同 session 摘要互斥锁  按 session_id 索引
        # 防止两次 _run_task_loop 并发触发同一 session 的自动压缩 跑两次冗余 LLM
        # 与 routes/sessions.py compact 路由不共享锁  那条路径靠 mongo $set 原子覆盖兜底
        self._compact_locks: dict[str, asyncio.Lock] = {}

    # ============================================================ 对外 API
    async def create_task(
        self,
        session_id: str | None,
        user_message: str,
        thinking_enabled: bool = False,
    ) -> str:
        """收到用户消息 创建 round 并启动后台驱动 task

        若 session_id 为空 自动新建会话
        thinking_enabled 跟随前端输入框大脑开关  落到 round 顶层  reply 阶段读取
        返回 task_id 路由层立刻拿去订阅 SSE
        """
        if not session_id:
            session_id = await self._storage.create_session(
                title=user_message[:40] or "新会话"
            )
        # 解析 @mention 命中则跳过 think 直接 reply
        agent_names = self._registry.names()
        mention = parse_single_mention(user_message, agent_names)
        task_id = await self._storage.create_round(
            session_id, user_message, mention, thinking_enabled=thinking_enabled
        )

        # hub 必须在当前 loop 创建 才能被同一 loop 上的 publish/subscribe 安全消费
        hub = TaskEventHub(task_id)
        self._hubs[task_id] = hub
        # 后台驱动协程
        t = asyncio.create_task(
            self._run_task_loop(task_id, session_id, user_message, mention)
        )
        self._tasks[task_id] = t
        return task_id

    async def submit_decision(self, task_id: str, choice: str) -> None:
        """用户决策入口 choice 取值 agent_name | 'regenerate' | 'auto'"""
        ev = self._decision_events.get(task_id)
        if ev is None:
            # task 不在等决策 或 task_id 未知
            raise KeyError(f"task not awaiting decision: {task_id}")
        self._decision_results[task_id] = choice
        ev.set()

    async def cancel_task(self, task_id: str, scope: str) -> None:
        """取消任务

        scope = "global" 取消整个 task
        scope = AgentName 仅取消该 agent 的 think 子任务
        其余 scope 当成 agent 名走相同逻辑 不存在则静默 路由层已校验
        """
        if scope == "global":
            t = self._tasks.get(task_id)
            if t is not None and not t.done():
                t.cancel()
            return

        subtasks = self._think_subtasks.get(task_id) or {}
        sub = subtasks.get(scope)
        if sub is not None and not sub.done():
            sub.cancel()

    async def retry_think(self, task_id: str, agent_name: str) -> None:
        """单卡 think 重试 在 THINK_DONE 状态下重启某 agent 的 think 子任务

        约束
            - task 必须仍在活动 hub 中 否则抛 KeyError(路由层 -> 404)
            - task 当前 state 必须是 THINK_DONE 否则抛 ValueError(路由层 -> 409)
            - agent_name 必须是已注册 agent 否则抛 ValueError(路由层 -> 409)

        实现思路
            主循环处于 _wait_decision 阻塞 不会 await 新协程
            这里 fire-and-forget 起一个 _retry_one_think 子任务
            子任务复用 storage update + hub publish 两条侧边路径
            完成后 publish task.state THINK_DONE 让前端 available_agents 同步刷新
        """
        # 校验 hub 还在
        if task_id not in self._hubs:
            raise KeyError(f"task not active: {task_id}")

        # 校验状态在 THINK_DONE 仅此状态允许 retry
        round_obj = await self._storage.get_round(task_id)
        if round_obj is None or round_obj.state != TaskState.THINK_DONE:
            current = round_obj.state if round_obj else None
            raise ValueError(
                f"task must be in THINK_DONE state to retry  current: {current}"
            )

        # 校验 agent 名 防误传
        if agent_name not in self._registry.names():
            raise ValueError(f"unknown agent: {agent_name}")

        # 起独立子任务 不阻塞路由响应 子任务自身完整捕获异常
        asyncio.create_task(
            self._retry_one_think(task_id, agent_name, round_obj.question)
        )

    def get_hub(self, task_id: str) -> TaskEventHub | None:
        """提供给 SSE 路由的 hub 查询入口 task 已结束则返回 None"""
        return self._hubs.get(task_id)

    # ============================================================ 后台主循环
    async def _run_task_loop(
        self,
        task_id: str,
        session_id: str,
        user_message: str,
        mention: str | None,
    ) -> None:
        """整体编排 think → decide → reply

        @ 直呼跳过 think 直接进 reply
        regenerate 后允许再决策一次 多次 regenerate 也支持
        异常情况 unrecoverable 仍写入 SSE 与 round 状态以便前端回灌
        """
        hub = self._hubs[task_id]
        try:
            # 请求到来时先静默检查会话上下文 token 是否超阈值
            # 超了就同步触发一次摘要再继续  失败也不阻塞 task  靠 except 捕获不让 reply 卡住
            await self._maybe_auto_compact(session_id)

            history = await self._build_history(session_id, current_task_id=task_id)

            if mention:
                # @ 直呼路径 跳过 think 直接 reply
                await self._mark_thinks_skipped(task_id)
                await self._storage.update_round_field(
                    task_id,
                    "decision",
                    {
                        "choice": mention,
                        "reason": "user_mention",
                        "decided_at": _now_iso(),
                    },
                )
                await self._set_state(task_id, TaskState.DECIDED)
                await hub.publish(
                    TaskEvent(
                        type="task.state",
                        data={
                            "state": "DECIDED",
                            "agent": mention,
                            "reason": "user_mention",
                        },
                    )
                )
                await self._do_reply(task_id, mention, user_message, history, hub)
                return

            # 只有一个 agent 时跳过 think+决策 直通 reply
            agent_names = list(self._registry.names())
            if len(agent_names) == 1:
                solo = agent_names[0]
                await self._mark_thinks_skipped(task_id)
                await self._storage.update_round_field(
                    task_id,
                    "decision",
                    {
                        "choice": solo,
                        "reason": "solo_agent",
                        "decided_at": _now_iso(),
                    },
                )
                await self._set_state(task_id, TaskState.DECIDED)
                await hub.publish(
                    TaskEvent(
                        type="task.state",
                        data={
                            "state": "DECIDED",
                            "agent": solo,
                            "reason": "solo_agent",
                        },
                    )
                )
                await self._do_reply(task_id, solo, user_message, history, hub)
                return

            # 正常 think 阶段
            await self._set_state(task_id, TaskState.THINKING)
            await hub.publish(
                TaskEvent(type="task.state", data={"state": "THINKING"})
            )
            think_results = await self._run_thinks(task_id, user_message, history, hub)

            await self._set_state(task_id, TaskState.THINK_DONE)
            available = [n for n, r in think_results.items() if r["state"] == "done"]
            await hub.publish(
                TaskEvent(
                    type="task.state",
                    data={"state": "THINK_DONE", "available_agents": available},
                )
            )

            # 等用户决策 支持多轮 regenerate
            choice = await self._wait_decision(task_id)
            while choice == "regenerate":
                await self._archive_thinks(task_id)
                await self._set_state(task_id, TaskState.THINKING)
                await hub.publish(
                    TaskEvent(
                        type="task.state",
                        data={"state": "THINKING", "regenerate": True},
                    )
                )
                think_results = await self._run_thinks(
                    task_id, user_message, history, hub
                )
                await self._set_state(task_id, TaskState.THINK_DONE)
                available = [
                    n for n, r in think_results.items() if r["state"] == "done"
                ]
                await hub.publish(
                    TaskEvent(
                        type="task.state",
                        data={
                            "state": "THINK_DONE",
                            "available_agents": available,
                        },
                    )
                )
                choice = await self._wait_decision(task_id)

            # auto 走 judge 反推 agent
            if choice == "auto":
                judge_target = await self._storage.get_judge_target()
                await hub.publish(
                    TaskEvent(
                        type="judge.start", data={"judge_agent": judge_target}
                    )
                )
                successful_thinks = {
                    n: r["content"]
                    for n, r in think_results.items()
                    if r["state"] == "done" and isinstance(r.get("content"), str)
                }
                if not successful_thinks:
                    raise RuntimeError("auto 决策时无可用 think 结果")
                chosen = await run_judge(
                    judge_agent_name=judge_target,
                    user_message=user_message,
                    thinks=successful_thinks,
                    registry=self._registry,
                    timeout_s=self._settings.runtime.http_timeout_seconds,
                )
                await hub.publish(
                    TaskEvent(type="judge.done", data={"chosen": chosen})
                )
                choice = chosen
                reason = "auto_judge"
            else:
                reason = "user_pick"

            # 落库 decision + DECIDED 状态
            await self._storage.update_round_field(
                task_id,
                "decision",
                {
                    "choice": choice,
                    "reason": reason,
                    "decided_at": _now_iso(),
                },
            )
            await self._set_state(task_id, TaskState.DECIDED)
            await hub.publish(
                TaskEvent(
                    type="task.state",
                    data={"state": "DECIDED", "agent": choice, "reason": reason},
                )
            )

            # reply 阶段
            await self._do_reply(task_id, choice, user_message, history, hub)

        except asyncio.CancelledError:
            _logger.info("task cancelled by user", task_id=task_id)
            try:
                await self._set_state(task_id, TaskState.CANCELLED)
                # 把 reply 也标为 cancelled 落库 防止刷新页面后回复显示"回答中"
                await self._storage.update_round_field(task_id, "reply.state", "cancelled")
                await hub.publish(
                    TaskEvent(
                        type="task.state",
                        data={"state": "CANCELLED", "reason": "user_cancel"},
                    )
                )
            except Exception:
                # cancel 阶段不抛出 避免遮蔽原 CancelledError
                _logger.exception(
                    "cancel 阶段写状态失败 忽略", task_id=task_id
                )
            # 这里不再 raise 让 finally 段做 cleanup
        except Exception as e:
            _logger.exception("task failed unrecoverable", task_id=task_id)
            # 顶层兜底也走 humanize 让 task.unrecoverable 事件中的 error 可读
            friendly = humanize_llm_error(e)
            try:
                await self._set_state(task_id, TaskState.CANCELLED)
                await hub.publish(
                    TaskEvent(
                        type="task.unrecoverable",
                        data={"error": friendly},
                    )
                )
                await hub.publish(
                    TaskEvent(
                        type="task.state",
                        data={"state": "CANCELLED", "reason": f"error: {friendly}"},
                    )
                )
            except Exception:
                _logger.exception(
                    "unrecoverable 阶段写状态失败 忽略", task_id=task_id
                )
        finally:
            await hub.close()
            self._cleanup(task_id)

    # ============================================================ think 阶段
    async def _run_thinks(
        self,
        task_id: str,
        user_message: str,
        history: list[dict[str, Any]],
        hub: TaskEventHub,
    ) -> dict[str, dict[str, Any]]:
        """4 路并行 think 收集结果

        每个 agent 一个 subtask 独立捕获错误 互不影响
        允许通过 cancel_task(task_id, agent_name) 单独取消
        """
        agent_names = list(self._registry.names())
        results: dict[str, dict[str, Any]] = {
            n: {"state": "pending"} for n in agent_names
        }
        # 初始化整体 thinks 字段 让前端能一开始就拿到 4 个 pending 占位
        await self._storage.update_round_field(
            task_id, "thinks", {n: {"state": "pending"} for n in agent_names}
        )

        async def _one(name: str) -> None:
            await hub.publish(TaskEvent(type="think.start", data={"agent": name}))
            try:
                content = await run_think(
                    agent_name=name,
                    user_message=user_message,
                    history=history,
                    registry=self._registry,
                    timeout_s=self._settings.runtime.http_timeout_seconds,
                )
                # 截断到 80 字 留点缓冲应对 LLM 偶尔超字数
                if len(content) > 80:
                    content = content[:80]
                results[name] = {"state": "done", "content": content}
                await self._storage.update_round_field(
                    task_id,
                    f"thinks.{name}",
                    {"state": "done", "content": content},
                )
                await hub.publish(
                    TaskEvent(
                        type="think.done",
                        data={"agent": name, "content": content},
                    )
                )
            except asyncio.CancelledError:
                results[name] = {"state": "cancelled"}
                try:
                    await self._storage.update_round_field(
                        task_id, f"thinks.{name}", {"state": "cancelled"}
                    )
                    await hub.publish(
                        TaskEvent(type="think.cancelled", data={"agent": name})
                    )
                except Exception:
                    _logger.exception(
                        "think.cancelled 写状态失败 忽略",
                        task_id=task_id,
                        agent=name,
                    )
                raise
            except Exception as e:
                # 把底层 httpx/超时/限流等英文异常转成中文用户提示
                # raw 用 logger 留底 落库与事件 payload 用 friendly 展示
                friendly = humanize_llm_error(e)
                _logger.warning(
                    "think 失败",
                    task_id=task_id,
                    agent=name,
                    raw_error=str(e),
                    friendly=friendly,
                )
                results[name] = {"state": "failed", "error": friendly}
                await self._storage.update_round_field(
                    task_id,
                    f"thinks.{name}",
                    {"state": "failed", "error": friendly},
                )
                await hub.publish(
                    TaskEvent(
                        type="think.failed",
                        data={"agent": name, "error": friendly},
                    )
                )

        subtasks: dict[str, asyncio.Task[None]] = {
            name: asyncio.create_task(_one(name)) for name in agent_names
        }
        self._think_subtasks[task_id] = subtasks
        # gather return_exceptions=True 让单卡 cancel/失败不传染兄弟
        await asyncio.gather(*subtasks.values(), return_exceptions=True)
        self._think_subtasks.pop(task_id, None)
        return results

    # ============================================================ 单 agent retry
    async def _retry_one_think(
        self,
        task_id: str,
        agent_name: str,
        user_message: str,
    ) -> None:
        """重启单个 agent 的 think 子任务 fire-and-forget 由 retry_think 拉起

        与 _run_thinks._one 行为一致 但区别在于
            - 不进 4 路 gather 单独跑一次
            - 完成后再 publish 一次 task.state THINK_DONE 让前端刷新 available_agents
            - storage 与 hub 不存在时静默退出 防 cleanup 后再触发空指针
        """
        hub = self._hubs.get(task_id)
        if hub is None:
            return

        # 重置该 agent 字段为 pending 让前端先看到 spinner
        try:
            await self._storage.update_round_field(
                task_id, f"thinks.{agent_name}", {"state": "pending"}
            )
        except Exception:
            _logger.exception(
                "retry_think 重置 pending 失败 忽略",
                task_id=task_id,
                agent=agent_name,
            )
            return

        await hub.publish(TaskEvent(type="think.start", data={"agent": agent_name}))

        # 拼上下文 与首次 think 一致
        round_obj = await self._storage.get_round(task_id)
        if round_obj is None:
            return
        history = await self._build_history(
            round_obj.session_id, current_task_id=task_id
        )

        try:
            content = await run_think(
                agent_name=agent_name,
                user_message=user_message,
                history=history,
                registry=self._registry,
                timeout_s=self._settings.runtime.http_timeout_seconds,
            )
            if len(content) > 80:
                content = content[:80]
            await self._storage.update_round_field(
                task_id, f"thinks.{agent_name}", {"state": "done", "content": content}
            )
            await hub.publish(
                TaskEvent(
                    type="think.done",
                    data={"agent": agent_name, "content": content},
                )
            )
        except asyncio.CancelledError:
            await self._storage.update_round_field(
                task_id, f"thinks.{agent_name}", {"state": "cancelled"}
            )
            await hub.publish(
                TaskEvent(type="think.cancelled", data={"agent": agent_name})
            )
            raise
        except Exception as e:
            err = str(e) or e.__class__.__name__
            await self._storage.update_round_field(
                task_id, f"thinks.{agent_name}", {"state": "failed", "error": err}
            )
            await hub.publish(
                TaskEvent(
                    type="think.failed",
                    data={"agent": agent_name, "error": err},
                )
            )

        # 不论成功失败 重新计算 available_agents 让前端可选项与最新一致
        round_obj = await self._storage.get_round(task_id)
        if round_obj is None:
            return
        available = [
            n
            for n, t in (round_obj.thinks or {}).items()
            if isinstance(t, dict) and t.get("state") == "done"
        ]
        await hub.publish(
            TaskEvent(
                type="task.state",
                data={"state": "THINK_DONE", "available_agents": available},
            )
        )

    # ============================================================ reply 阶段
    async def _do_reply(
        self,
        task_id: str,
        agent_name: str,
        user_message: str,
        history: list[dict[str, Any]],
        hub: TaskEventHub,
    ) -> None:
        """REPLYING → DONE 流式回复

        节流策略: 把 LLM 吐的小 chunk 缓冲在内存 buf 中
        每 reply_flush_interval_ms 一次或回复结束时把 buf 一次性 append 到 mongo
        既减少写库次数 又保证最终内容完整

        段时间线持久化:
            除了节流追加 reply_content 之外  还按时间顺序维护 reply.segments
            chunk 累积到 current_text_buf  tool_call / tool_result 到来时
            先把当前文本封成 text 段 push 到 segments_buf 再 push tool 段并整组写库
            reply.done 终态前把残余 text 封段  最后把 segments 一并落到 reply 终态文档里
            这样刷新页面 / 切回旧会话时  可以从 mongo 完整还原文本与工具调用的交错顺序
        """
        await self._set_state(task_id, TaskState.REPLYING)
        await hub.publish(TaskEvent(type="reply.start", data={"agent": agent_name}))
        # 取一次 round.thinking_enabled  本轮 reply 是否走深度思考  存储入参
        # 失败兜底 false  不阻塞主流程
        try:
            round_obj_for_thinking = await self._storage.get_round(task_id)
            thinking_enabled = bool(
                getattr(round_obj_for_thinking, "thinking_enabled", False)
            )
        except Exception:
            thinking_enabled = False
        await self._storage.update_round_field(
            task_id,
            "reply",
            {
                "agent": agent_name,
                "state": "streaming",
                "content": "",
                "started_at": _now_iso(),
                # 初始化 segments 为空数组  后续按时间顺序追加  避免 update_reply_segments
                # 在 reply 还没建出来的瞬间写到不存在的父对象上
                "segments": [],
            },
        )

        flush_buf: list[str] = []
        # segments_buf  按时间顺序的段时间线  最终覆盖写到 reply.segments
        # current_text_buf  当前还没封段的文本累积  遇到 tool 事件或 reply 结束时封段
        # current_thinking_buf  当前还没封段的 reasoning 累积  遇到 chunk / tool / reply.done 时封段
        segments_buf: list[dict[str, Any]] = []
        current_text_buf: list[str] = []
        current_thinking_buf: list[str] = []
        loop = asyncio.get_event_loop()
        last_flush_ts = loop.time()
        flush_interval_s = self._settings.runtime.reply_flush_interval_ms / 1000.0

        def _flush_text_segment() -> bool:
            """把 current_text_buf 里的文本封成一个 text 段 push 到 segments_buf

            空文本不封段  返回是否真的产生了新段  调用方据此决定要不要写库
            """
            if not current_text_buf:
                return False
            text = "".join(current_text_buf)
            current_text_buf.clear()
            if not text:
                return False
            # 与最后一个段同为 text 时  直接合并  避免 LLM 切片粒度过细产出空文本段
            if segments_buf and segments_buf[-1].get("type") == "text":
                segments_buf[-1]["content"] = (
                    segments_buf[-1].get("content", "") + text
                )
            else:
                segments_buf.append({"type": "text", "content": text})
            return True

        def _flush_thinking_segment() -> bool:
            """把 current_thinking_buf 里的 reasoning 封成一个 thinking 段

            与 text 段对称  与最后一个段同为 thinking 时合并
            空 reasoning 不封段
            """
            if not current_thinking_buf:
                return False
            text = "".join(current_thinking_buf)
            current_thinking_buf.clear()
            if not text:
                return False
            if segments_buf and segments_buf[-1].get("type") == "thinking":
                segments_buf[-1]["content"] = (
                    segments_buf[-1].get("content", "") + text
                )
            else:
                segments_buf.append({"type": "thinking", "content": text})
            return True

        async def on_event(ev: TaskEvent) -> None:
            # 闭包内统一声明 nonlocal  避免在不同 if 分支重复声明导致 SyntaxError
            nonlocal last_flush_ts
            # 先把事件原样推到 hub 给前端流式
            await hub.publish(ev)

            # reply.thinking 是 reasoning 流  累积到 current_thinking_buf  封段策略与 text 对称
            # 节流上同 text  reasoning 比正文先到  不参与 reply.content 拼接
            if ev.type == "reply.thinking":
                think_chunk = ev.data.get("chunk", "") or ""
                if think_chunk:
                    current_thinking_buf.append(think_chunk)
                # 节流写库  reasoning 封段后整组写一次
                now = loop.time()
                if now - last_flush_ts >= flush_interval_s and current_thinking_buf:
                    if _flush_thinking_segment():
                        try:
                            await self._storage.update_reply_segments(
                                task_id, segments_buf
                            )
                        except Exception:
                            _logger.exception(
                                "reply.thinking 段持久化失败 忽略", task_id=task_id
                            )
                    last_flush_ts = now
                return

            # reply.chunk 单独走节流写库  同时累积进 current_text_buf 等待封段
            if ev.type == "reply.chunk":
                # 文本到来前  把 reasoning 累积先封段  保证段顺序 thinking 在 text 之前
                if current_thinking_buf:
                    if _flush_thinking_segment():
                        try:
                            await self._storage.update_reply_segments(
                                task_id, segments_buf
                            )
                        except Exception:
                            _logger.exception(
                                "reply.thinking 段持久化失败 忽略", task_id=task_id
                            )
                chunk_text = ev.data.get("chunk", "") or ""
                if chunk_text:
                    flush_buf.append(chunk_text)
                    current_text_buf.append(chunk_text)
                now = loop.time()
                if now - last_flush_ts >= flush_interval_s and flush_buf:
                    await self._storage.append_reply_chunk(
                        task_id, "".join(flush_buf)
                    )
                    flush_buf.clear()
                    last_flush_ts = now
                return

            # 工具调用事件  按时间顺序封段  先把累积 reasoning / text 都封段  再 push tool 段
            # 写库整组覆盖一次 让刷新后能完整还原顺序
            if ev.type == "reply.tool_call":
                _flush_thinking_segment()
                _flush_text_segment()
                tool = ev.data.get("tool", "") or ""
                tool_input = ev.data.get("input", "") or ""
                segments_buf.append(
                    {
                        "type": "tool_call",
                        "tool": tool,
                        "input": tool_input,
                    }
                )
                try:
                    await self._storage.update_reply_segments(task_id, segments_buf)
                except Exception:
                    # 段写库失败不阻塞 reply 主流程  日志保留即可
                    _logger.exception(
                        "reply.tool_call 段持久化失败 忽略", task_id=task_id
                    )
                return

            if ev.type == "reply.tool_result":
                _flush_thinking_segment()
                _flush_text_segment()
                tool = ev.data.get("tool", "") or ""
                tool_result = ev.data.get("result", "") or ""
                segments_buf.append(
                    {
                        "type": "tool_result",
                        "tool": tool,
                        "result": tool_result,
                    }
                )
                try:
                    await self._storage.update_reply_segments(task_id, segments_buf)
                except Exception:
                    _logger.exception(
                        "reply.tool_result 段持久化失败 忽略", task_id=task_id
                    )
                return

        try:
            full_text = await run_reply(
                agent_name=agent_name,
                user_message=user_message,
                history=history,
                registry=self._registry,
                on_event=on_event,
                # reply 通常更长 这里给 6 倍 timeout 还是有上限不会无限等
                timeout_s=self._settings.runtime.http_timeout_seconds * 6,
                thinking_enabled=thinking_enabled,
            )
            # 兜底刷新剩余 chunk 防止丢
            if flush_buf:
                await self._storage.append_reply_chunk(
                    task_id, "".join(flush_buf)
                )
                flush_buf.clear()
            # reply 完成前把尾部残余 reasoning / text 封段  保证 segments 是完整时间线
            _flush_thinking_segment()
            _flush_text_segment()
            # 写入 reply.done 终态 保证 content 与最终一致  segments 同步落
            finished_at_iso = _now_iso()
            await self._storage.update_round_field(
                task_id,
                "reply",
                {
                    "agent": agent_name,
                    "state": "done",
                    "content": full_text,
                    "started_at": _now_iso(),
                    "finished_at": finished_at_iso,
                    "segments": segments_buf,
                },
            )
            await self._set_state(task_id, TaskState.DONE)
            await hub.publish(
                TaskEvent(
                    type="reply.done",
                    data={
                        "agent": agent_name,
                        "content": full_text,
                        # 前端流式刚结束就要显示时间  不传就得等下次 history 刷新
                        "finished_at": finished_at_iso,
                    },
                )
            )
            # reply 完成后立刻推一次 context.usage  让前端进度条与摘要状态同步刷新
            # 失败被 except 吞  不阻塞主流  task.state DONE 仍照常发
            await self._publish_context_usage(task_id, agent_name, hub)
            await hub.publish(
                TaskEvent(type="task.state", data={"state": "DONE"})
            )
        except asyncio.CancelledError:
            # reply 阶段被全局取消 由外层 _run_task_loop except 段处理
            raise
        except Exception as e:
            # reply 阶段失败 同样走 humanize 把英文转中文 让前端可读
            friendly = humanize_llm_error(e)
            _logger.warning(
                "reply 失败", task_id=task_id, raw_error=str(e), friendly=friendly
            )
            try:
                await self._storage.update_round_field(
                    task_id, "reply.state", "failed"
                )
                await self._storage.update_round_field(
                    task_id, "reply.error", friendly
                )
            except Exception:
                _logger.exception(
                    "reply 失败写状态二次报错 忽略", task_id=task_id
                )
            # reply 失败仍标 DONE 让前端能看到 reply.state=failed 而不是被卡死在 REPLYING
            await self._set_state(task_id, TaskState.DONE)
            await hub.publish(
                TaskEvent(
                    type="reply.error",
                    data={"agent": agent_name, "error": friendly},
                )
            )
            await hub.publish(
                TaskEvent(type="task.state", data={"state": "DONE"})
            )

    # ============================================================ 决策等待
    async def _wait_decision(self, task_id: str) -> str:
        """阻塞等待用户决策 期间允许被外部 cancel"""
        ev = self._decision_events.setdefault(task_id, asyncio.Event())
        await ev.wait()
        choice = self._decision_results.pop(task_id, "")
        # 重置 event 让 regenerate 后能再次等下一次决策
        ev.clear()
        return choice

    # ============================================================ 状态机辅助
    async def _set_state(self, task_id: str, state: TaskState) -> None:
        """集中刷状态 + updated_at"""
        await self._storage.update_round_state(task_id, state)

    async def _archive_thinks(self, task_id: str) -> None:
        """regenerate 时把当前 thinks 推入 think_history 然后重置"""
        round_obj = await self._storage.get_round(task_id)
        if round_obj is None:
            return
        history_list: list[dict[str, Any]] = list(round_obj.think_history or [])
        history_list.append(
            {
                "thinks": dict(round_obj.thinks or {}),
                "regenerated_at": _now_iso(),
            }
        )
        await self._storage.update_round_field(
            task_id, "think_history", history_list
        )
        await self._storage.update_round_field(
            task_id,
            "thinks",
            {n: {"state": "pending"} for n in self._registry.names()},
        )

    async def _mark_thinks_skipped(self, task_id: str) -> None:
        """@ 直呼场景 把 4 个 think 全部置为 skipped 让前端可视化清晰"""
        await self._storage.update_round_field(
            task_id,
            "thinks",
            {n: {"state": "skipped"} for n in self._registry.names()},
        )

    async def _build_history(
        self, session_id: str, current_task_id: str
    ) -> list[dict[str, Any]]:
        """取最近 N 轮已完成 round 转成 langchain 友好格式

        约束:
            - 当前正在跑的 task 不进 history
            - reply 状态必须 done 否则跳过 防止把空回复喂回 LLM
            - 取最近 history_max_rounds 轮
            - 若 session 已生成摘要 在最前面注入一条 system message 承载摘要
              并跳过 round_index <= summary_until_round 的旧轮次 避免重复喂

        摘要注入策略:
            - 摘要文本以 "[会话摘要]\n..." 开头  让 LLM 一眼识别这是浓缩历史
            - 摘要消息不进 history_max_rounds 计数  独立占一条
            - agent_runner._build_messages 必须识别 role=system 转 SystemMessage
              否则摘要会被当成噪声丢弃
        """
        # 先拿 session 看看有没有摘要
        session = await self._storage.get_session(session_id)
        summary_text = (session.summary or "") if session is not None else ""
        summary_until = (
            int(session.summary_until_round) if session is not None else 0
        )

        all_rounds = await self._storage.list_rounds(session_id)
        usable = [
            r
            for r in all_rounds
            if r.task_id != current_task_id
            and r.reply
            and r.reply.get("state") == "done"
        ]
        # 已被摘要覆盖的 round 不再单独喂  避免与摘要内容重复占 token
        if summary_text:
            usable = [r for r in usable if r.round_index > summary_until]

        n = self._settings.runtime.history_max_rounds
        if n > 0:
            usable = usable[-n:]

        out: list[dict[str, Any]] = []
        if summary_text:
            # 摘要在最前 用 system 角色  agent_runner 会转成 SystemMessage
            out.append(
                {
                    "role": "system",
                    "content": f"[会话摘要]\n{summary_text}",
                    "is_summary": True,
                }
            )
        for r in usable:
            out.append({"role": "user", "content": r.question})
            reply_dict = r.reply or {}
            out.append(
                {
                    "role": "assistant",
                    "content": reply_dict.get("content", "") or "",
                    "agent": reply_dict.get("agent"),
                }
            )
        return out

    def _cleanup(self, task_id: str) -> None:
        """task 结束后释放索引 防止内存堆积 hub 已 close"""
        self._hubs.pop(task_id, None)
        self._tasks.pop(task_id, None)
        self._decision_events.pop(task_id, None)
        self._decision_results.pop(task_id, None)
        self._think_subtasks.pop(task_id, None)

    # ============================================================ 上下文压缩与用量
    def _resolve_compaction_agent_name(
        self,
        done_rounds: list[Any],
    ) -> str | None:
        """挑选用作摘要 / 用量评估的 agent 名

        策略:
            1 优先取最近一轮 reply 的 agent  通常就是用户期望的"当前模型"
            2 不在 registry 时回退到 judge 指针
            3 都拿不到返回 None  上层判断不可压缩

        说明:
            judge 指针由 storage.get_judge_target 提供  缺失时静默忽略不抛
            registry.names 反映当前已注册 agent 防止取到刚被删除的 agent
        """
        registered = set(self._registry.names())
        for r in reversed(done_rounds):
            reply_dict = getattr(r, "reply", None) or {}
            cand = reply_dict.get("agent")
            if isinstance(cand, str) and cand in registered:
                return cand
        return None  # 调用方再尝试 judge 指针

    async def _maybe_auto_compact(self, session_id: str) -> None:
        """请求到来时静默检查并按需压缩  失败不阻塞主流

        触发条件:
            未摘要部分(round_index > summary_until_round)的 token 估算 >= 80% 阈值

        互斥:
            self._compact_locks 按 session_id 索引  防止同 session 并发跑两次摘要
            与 routes/sessions.py compact 路由不共享锁  那边靠 mongo $set 原子覆盖兜底

        agent 选择:
            优先最近一轮 reply.agent  fallback judge 指针  都拿不到则跳过

        失败处理:
            任何异常 一律 except + log 不向上抛  保证 think/reply 主流程不被影响
        """
        try:
            lock = self._compact_locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                session = await self._storage.get_session(session_id)
                if session is None:
                    return

                all_rounds = await self._storage.list_rounds(session_id)
                done_rounds = [
                    r
                    for r in all_rounds
                    if r.reply and r.reply.get("state") == "done"
                ]
                if not done_rounds:
                    return

                summary = session.summary or ""
                summary_until = int(session.summary_until_round or 0)
                # 未被覆盖的轮次  这部分才参与 token 估算
                uncovered = [r for r in done_rounds if r.round_index > summary_until]
                if not uncovered:
                    return

                agent_name = self._resolve_compaction_agent_name(done_rounds)
                if agent_name is None:
                    try:
                        judge = await self._storage.get_judge_target()
                        if judge in self._registry.names():
                            agent_name = judge
                    except KeyError:
                        agent_name = None
                if agent_name is None:
                    return

                record = await self._storage.get_agent(agent_name)
                if record is None:
                    return
                max_tokens = next(
                    (
                        m.max_input_tokens
                        for m in record.available_models
                        if m.model_id == record.model
                    ),
                    200000,
                )

                # 拼"未摘要部分"的 history dict  含旧摘要前缀  口径与 _build_history 一致
                history_dicts: list[dict[str, Any]] = []
                if summary:
                    history_dicts.append(
                        {"role": "system", "content": f"[会话摘要]\n{summary}"}
                    )
                for r in uncovered:
                    history_dicts.append({"role": "user", "content": r.question})
                    history_dicts.append(
                        {
                            "role": "assistant",
                            "content": (r.reply or {}).get("content", "") or "",
                        }
                    )

                used = count_history_tokens(history_dicts)
                if not should_trigger_summary(used, max_tokens):
                    return  # 没到 80% 阈值不动

                # 触发摘要  传给 run_session_summary 的 history 不带 system 摘要前缀
                # 因为 summarization 内部会把 old_summary 单独排版  避免重复注入
                comp_history: list[dict[str, Any]] = []
                for r in uncovered:
                    comp_history.append({"role": "user", "content": r.question})
                    comp_history.append(
                        {
                            "role": "assistant",
                            "content": (r.reply or {}).get("content", "") or "",
                        }
                    )
                _logger.info(
                    "auto_compact 触发  开始 LLM 摘要",
                    session_id=session_id,
                    used_tokens=used,
                    max_tokens=max_tokens,
                    agent=agent_name,
                    uncovered_rounds=len(uncovered),
                )
                new_summary = await run_session_summary(
                    history=comp_history,
                    old_summary=summary,
                    agent_record=record,
                    timeout_s=120.0,
                )
                new_until = int(uncovered[-1].round_index)
                await self._storage.update_session_summary(
                    session_id,
                    summary=new_summary,
                    summary_until_round=new_until,
                )
                _logger.info(
                    "auto_compact 完成",
                    session_id=session_id,
                    summary_until=new_until,
                    new_summary_len=len(new_summary),
                )
        except Exception:
            # 摘要失败不影响后续 think/reply  只 log 不向上抛
            _logger.exception("auto_compact 失败 忽略", session_id=session_id)

    async def _publish_context_usage(
        self, task_id: str, agent_name: str, hub: TaskEventHub
    ) -> None:
        """每轮 reply 完成后推一次 context.usage  让前端进度条同步

        字段约定见 multichat.llm.token_counter.usage_payload  与前端 ContextUsage 对齐
        agent_name 通常是这一轮 reply 用的 agent  用它的 max_input_tokens 算阈值
        失败 except 吞掉 不阻塞 task 主流

        持久化:
            payload 同时写到 sessions.context_usage 字段  让前端刷新 / 切会话后
            通过 GET /history/{id} 拿到上一次的状态  无需等下一轮 reply 才显示进度条
            写库失败 except 吞掉  不影响 SSE 推流
        """
        try:
            round_obj = await self._storage.get_round(task_id)
            if round_obj is None:
                return
            session_id = round_obj.session_id
            # _build_history 默认会过滤 current_task_id  这里我们要算"含本轮"
            # 故传一个空 task_id  让 list_rounds 返回的全部 done round 都进来
            history = await self._build_history(session_id, current_task_id="")

            record = await self._storage.get_agent(agent_name)
            if record is None:
                return
            max_tokens = next(
                (
                    m.max_input_tokens
                    for m in record.available_models
                    if m.model_id == record.model
                ),
                200000,
            )
            used = count_history_tokens(history)
            payload = usage_payload(used, max_tokens, model_id=record.model)
            await hub.publish(TaskEvent(type="context.usage", data=payload))
            # 写库快照  失败仅 log 不阻塞流
            try:
                await self._storage.update_session_context_usage(session_id, payload)
            except Exception:
                _logger.exception(
                    "update_session_context_usage 失败 不阻塞流",
                    session_id=session_id,
                )
        except Exception:
            _logger.exception(
                "publish context.usage 失败 不阻塞流", task_id=task_id
            )
