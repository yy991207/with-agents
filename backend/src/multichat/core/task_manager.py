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

    # ============================================================ 对外 API
    async def create_task(
        self,
        session_id: str | None,
        user_message: str,
    ) -> str:
        """收到用户消息 创建 round 并启动后台驱动 task

        若 session_id 为空 自动新建会话
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
            session_id, user_message, mention
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
        """
        await self._set_state(task_id, TaskState.REPLYING)
        await hub.publish(TaskEvent(type="reply.start", data={"agent": agent_name}))
        await self._storage.update_round_field(
            task_id,
            "reply",
            {
                "agent": agent_name,
                "state": "streaming",
                "content": "",
                "started_at": _now_iso(),
            },
        )

        flush_buf: list[str] = []
        loop = asyncio.get_event_loop()
        last_flush_ts = loop.time()
        flush_interval_s = self._settings.runtime.reply_flush_interval_ms / 1000.0

        async def on_event(ev: TaskEvent) -> None:
            # 先把事件原样推到 hub 给前端流式
            await hub.publish(ev)
            # reply.chunk 单独走节流写库
            if ev.type == "reply.chunk":
                nonlocal last_flush_ts
                chunk_text = ev.data.get("chunk", "") or ""
                if chunk_text:
                    flush_buf.append(chunk_text)
                now = loop.time()
                if now - last_flush_ts >= flush_interval_s and flush_buf:
                    await self._storage.append_reply_chunk(
                        task_id, "".join(flush_buf)
                    )
                    flush_buf.clear()
                    last_flush_ts = now

        try:
            full_text = await run_reply(
                agent_name=agent_name,
                user_message=user_message,
                history=history,
                registry=self._registry,
                on_event=on_event,
                # reply 通常更长 这里给 6 倍 timeout 还是有上限不会无限等
                timeout_s=self._settings.runtime.http_timeout_seconds * 6,
            )
            # 兜底刷新剩余 chunk 防止丢
            if flush_buf:
                await self._storage.append_reply_chunk(
                    task_id, "".join(flush_buf)
                )
                flush_buf.clear()
            # 写入 reply.done 终态 保证 content 与最终一致
            await self._storage.update_round_field(
                task_id,
                "reply",
                {
                    "agent": agent_name,
                    "state": "done",
                    "content": full_text,
                    "started_at": _now_iso(),
                    "finished_at": _now_iso(),
                },
            )
            await self._set_state(task_id, TaskState.DONE)
            await hub.publish(
                TaskEvent(
                    type="reply.done",
                    data={"agent": agent_name, "content": full_text},
                )
            )
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
        """
        all_rounds = await self._storage.list_rounds(session_id)
        usable = [
            r
            for r in all_rounds
            if r.task_id != current_task_id
            and r.reply
            and r.reply.get("state") == "done"
        ]
        n = self._settings.runtime.history_max_rounds
        if n > 0:
            usable = usable[-n:]
        out: list[dict[str, Any]] = []
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
