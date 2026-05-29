"""任务编排器 多 agent 并发回答状态机驱动

每个 task 一个后台 asyncio.Task 跑 _run_task_loop  fan-out 到多个 agent reply 子任务

设计要点:
    - 单/多 agent 走同一份编排逻辑  agents 列表长度区分
    - 每个 agent 的 reply 是独立 subtask  彼此 cancel/retry 互不影响
    - state 机简化为 PENDING -> REPLYING -> DONE / CANCELLED
    - 选答 / 重试 / 终止单 agent 都通过外部 API 触发  task 主循环不阻塞等用户输入
    - reply 阶段流式 chunk 写库走节流 减少 mongo 压力
    - 异常分级捕获 单 agent reply 失败不影响兄弟  全局未捕获异常落到 task.unrecoverable

异步对象与事件循环绑定问题(参考全局规范):
    - hub 与各 asyncio.Task / Event 都在 create_task 调用所在 loop 创建
    - 子任务由 asyncio.create_task 拉起 自动在同一 loop 不会跨 loop
    - storage 客户端在 fastapi lifespan 创建 与 task_manager 共享同一 loop
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from ..llm.agent_runner import run_reply
from ..llm.deep_agents import DeepAgentRegistry
from ..llm.summarization import run_session_summary
from ..llm.token_counter import (
    count_history_tokens,
    should_trigger_summary,
    usage_payload,
)
from .errors import extract_retry_after, humanize_llm_error, is_rate_limit_error
from .events import TaskEvent, TaskEventHub
from .mention_parser import parse_single_mention
from .models import TaskState

_logger = structlog.get_logger(__name__)


def _now_iso() -> str:
    """统一带时区 ISO 时间戳 落库与事件 payload 共用"""
    return datetime.now(timezone.utc).isoformat()


# {{TASK_MANAGER_BODY}}


class TaskManager:
    """任务管理器 单例形式由应用工厂注入

    路由层职责:
        - POST /ask 调 create_task 拿 task_id  body 含 agents 与 input_mode
        - GET /sse 调 get_hub 拿 hub 然后桥接到 SSE
        - POST /select_reply 调 select_reply 把用户选定 agent 落库 + 推 reply.selected
        - POST /retry_reply 调 retry_reply 单 agent 重答
        - POST /cancel 调 cancel_task 取消 task 或单 agent reply

    内部状态:
        - _hubs           按 task_id 索引每个任务的 SSE 事件总线
        - _tasks          按 task_id 索引主 _run_task_loop 协程  外部 cancel 走它
        - _reply_subtasks 按 task_id -> agent_name 索引每个 agent reply 子任务
                          单 agent 终止 / 重试只动这一项 不动主 loop
        - _compact_locks  按 session_id 索引摘要互斥锁  防并发跑
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
        self._hubs: dict[str, TaskEventHub] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # task_id -> agent_name -> reply 子任务  retry / cancel 单 agent 时索引
        self._reply_subtasks: dict[str, dict[str, asyncio.Task[None]]] = {}
        # 同 session 摘要互斥锁  防止两次 _run_task_loop 并发跑同一 session 的自动压缩
        self._compact_locks: dict[str, asyncio.Lock] = {}

    # ============================================================ 对外 API
    async def create_task(
        self,
        session_id: str | None,
        user_message: str,
        owner_user_id: str,
        agents: list[str],
        input_mode: Literal["single", "multi"] = "single",
        thinking_enabled: bool = False,
        replace_task_id: str | None = None,
    ) -> str:
        """收到用户消息 创建 round 并启动后台驱动 task

        agents 必须非空 长度 1~4  路由层先校验
        若 session_id 为空 自动新建会话
        thinking_enabled 跟随前端输入框大脑开关  落到 round 顶层  reply 阶段读取
        @ 直呼会被 mention_parser 命中并把 agents 覆盖为 [mention]  转入单 agent 模式
        """
        if not agents:
            raise ValueError("agents 不能为空")
        if input_mode not in ("single", "multi"):
            raise ValueError(f"input_mode 取值非法 {input_mode}")

        # 校验 agents 都是当前用户可见的已注册 agent
        visible_agents = await self._storage.list_agents(owner_user_id=owner_user_id)
        registered = {a.name for a in visible_agents}
        for a in agents:
            if a not in registered:
                raise ValueError(f"未知 agent: {a}")

        # @ 直呼优先级最高  把 agents 覆盖为 [mention]  转单 agent 模式
        mention = parse_single_mention(user_message, list(registered))
        if mention:
            agents = [mention]
            input_mode = "single"

        if replace_task_id:
            round_to_replace = await self._storage.get_round(replace_task_id)
            if round_to_replace is None:
                raise ValueError(f"replace_task_id 不存在 {replace_task_id}")
            if session_id and round_to_replace.session_id != session_id:
                raise ValueError("replace_task_id 不属于当前 session")
            session_id = round_to_replace.session_id
            await self._truncate_history_for_edit(
                session_id=session_id,
                replace_round=round_to_replace,
                new_user_message=user_message,
                new_agents=agents,
                new_input_mode=input_mode,
                new_thinking_enabled=thinking_enabled,
            )
            hub = TaskEventHub(replace_task_id)
            self._hubs[replace_task_id] = hub
            t = asyncio.create_task(
                self._run_task_loop(
                    replace_task_id,
                    session_id,
                    user_message,
                    agents,
                    input_mode,
                    owner_user_id,
                )
            )
            self._tasks[replace_task_id] = t
            return replace_task_id

        if not session_id:
            session_id = await self._storage.create_session(
                title=user_message[:40] or "新会话",
                owner_user_id=owner_user_id,
            )

        task_id = await self._storage.create_round(
            session_id,
            user_message,
            mention,
            agents=agents,
            input_mode=input_mode,
            thinking_enabled=thinking_enabled,
        )

        # hub 必须在当前 loop 创建 才能被同一 loop 上的 publish/subscribe 安全消费
        hub = TaskEventHub(task_id)
        self._hubs[task_id] = hub
        # 后台驱动协程
        t = asyncio.create_task(
            self._run_task_loop(task_id, session_id, user_message, agents, input_mode, owner_user_id)
        )
        self._tasks[task_id] = t
        return task_id

    async def _truncate_history_for_edit(
        self,
        *,
        session_id: str,
        replace_round: Any,
        new_user_message: str,
        new_agents: list[str],
        new_input_mode: Literal["single", "multi"],
        new_thinking_enabled: bool,
    ) -> None:
        """编辑历史消息时裁掉目标 round 后面的全部内容

        说明:
            - 只允许编辑最新一条已完成消息  由路由层先校验
            - 若摘要覆盖到了被编辑 round 或其后续内容  摘要和 context_usage 一并清空
            - 这样新的历史会从当前编辑点重新计算
        """
        round_index = int(getattr(replace_round, "round_index", 0))
        task_id = str(getattr(replace_round, "task_id"))
        session = await self._storage.get_session(session_id)
        summary_until = int(session.summary_until_round or 0) if session is not None else 0
        await self._storage.delete_rounds_after(session_id, round_index)
        if summary_until >= round_index:
            await self._storage.clear_session_summary(session_id)
        else:
            await self._storage.update_session_context_usage(session_id, None)
        # 编辑目标轮次本身也要被重置成新的用户消息和新的回复占位
        await self._storage.update_round_field(task_id, "question", new_user_message)
        await self._storage.update_round_field(task_id, "agents", list(new_agents))
        await self._storage.update_round_field(task_id, "input_mode", new_input_mode)
        await self._storage.update_round_field(task_id, "thinking_enabled", bool(new_thinking_enabled))
        await self._storage.update_round_field(task_id, "selected_reply_agent", None)
        await self._storage.update_round_field(
            task_id,
            "replies",
            {name: {"state": "pending", "content": "", "segments": []} for name in new_agents},
        )
        await self._storage.update_round_state(task_id, TaskState.PENDING)
        await self._storage.update_session_meta(session_id, title=None)

    async def cancel_task(self, task_id: str, scope: str) -> None:
        """取消任务

        scope = "global" 取消整个 task  会顺带终止所有 agent 子任务
        scope = AgentName 仅取消该 agent 的 reply 子任务  其它 agent 继续跑
        """
        if scope == "global":
            t = self._tasks.get(task_id)
            if t is not None and not t.done():
                t.cancel()
            return

        subtasks = self._reply_subtasks.get(task_id) or {}
        sub = subtasks.get(scope)
        if sub is not None and not sub.done():
            sub.cancel()

    async def select_reply(self, task_id: str, agent_name: str) -> None:
        """用户从多 agent 候选中选定一个作为正式回答

        约束:
            - round 不存在抛 KeyError  路由层映射 404
            - agent_name 不在 round.agents 抛 ValueError  路由层映射 409
            - 该 agent reply 状态非 done 抛 ValueError  路由层映射 409
            - round.state 必须是 DONE  否则抛 ValueError 路由层映射 409
              避免还在 streaming 时就被选答  导致后续段更新无主
        """
        round_obj = await self._storage.get_round(task_id)
        if round_obj is None:
            raise KeyError(f"round 不存在 task_id={task_id}")
        if round_obj.state != TaskState.DONE:
            raise ValueError(
                f"round 当前状态 {round_obj.state}  仅 DONE 可选答"
            )

        await self._storage.select_reply(task_id, agent_name)

        hub = self._hubs.get(task_id)
        if hub is not None:
            await hub.publish(
                TaskEvent(
                    type="reply.selected",
                    data={"agent": agent_name},
                )
            )
            # 选答之后  history 拼接才确定  顺带推一次 context.usage 让前端进度条同步
            await self._publish_context_usage(task_id, agent_name, hub)
        else:
            # task 已结束  hub 已 close  仅写库即可  context.usage 等下轮主动拉
            try:
                round_after = await self._storage.get_round(task_id)
                if round_after is not None:
                    await self._publish_context_usage_no_hub(
                        round_after.session_id, agent_name
                    )
            except Exception:
                _logger.exception(
                    "select_reply 后写 context_usage 失败 忽略", task_id=task_id
                )

    async def retry_reply(self, task_id: str, agent_name: str) -> None:
        """重答单个 agent  在 DONE 或 CANCELLED 状态下重启该 agent 的 reply 子任务

        约束:
            - round 不存在抛 KeyError  路由层映射 404
            - round.state 必须是 DONE 或 CANCELLED  避免和正在跑的子任务冲突
            - agent_name 必须在 round.agents 内
            - 用户已选答的 agent 也允许重答  重答完会清空 selected_reply_agent
              逼用户重新确认  避免引用一个已经被覆盖的内容

        实现:
            - 把 replies[agent].state 重置为 pending  推 reply.start 再起子任务
            - 子任务完成后 publish reply.done / reply.error  与首跑路径一致
            - round.state 在子任务进入时切回 REPLYING  全部 agent done 后再切 DONE
        """
        round_obj = await self._storage.get_round(task_id)
        if round_obj is None:
            raise KeyError(f"round 不存在 task_id={task_id}")
        if round_obj.state not in (TaskState.DONE, TaskState.CANCELLED):
            raise ValueError(
                f"round 当前状态 {round_obj.state}  仅 DONE 或 CANCELLED 可重答"
            )
        if agent_name not in (round_obj.agents or []):
            raise ValueError(
                f"agent {agent_name} 不在本轮候选 {round_obj.agents}"
            )

        # 从 session 反查 owner_user_id  用于按用户获取 agent 实例(MCP/Skills)
        session = await self._storage.get_session(round_obj.session_id)
        owner_user_id = (session.owner_user_id if session else None) or "system"

        hub = self._hubs.get(task_id)
        if hub is None:
            # 历史 task 已经清理  重新建 hub  让 SSE 重连后能拿事件
            hub = TaskEventHub(task_id)
            self._hubs[task_id] = hub

        # 重答会让"原选答"失效  清掉 selected_reply_agent  逼前端重新确认
        if round_obj.selected_reply_agent == agent_name:
            await self._storage.update_round_field(
                task_id, "selected_reply_agent", None
            )

        # 重置该 agent reply 占位  状态切 REPLYING
        await self._storage.update_reply_for_agent(
            task_id,
            agent_name,
            {"state": "pending", "content": "", "segments": []},
        )
        await self._set_state(task_id, TaskState.REPLYING)
        await hub.publish(
            TaskEvent(type="task.state", data={"state": "REPLYING"})
        )

        # 准备 history 与首跑一致
        history = await self._build_history(
            round_obj.session_id, current_task_id=task_id
        )

        # 起独立子任务  完成时再校验是否需要把整 round 刷回 DONE
        async def _retry_one() -> None:
            try:
                await self._do_reply_for_agent(
                    task_id, agent_name, round_obj.question, history, hub,
                    owner_user_id,
                )
            finally:
                # 子任务结束  检查整 round 是否所有 agent 都 done  是则刷 DONE
                await self._maybe_finalize_round(task_id, hub)

        sub = asyncio.create_task(_retry_one())
        self._reply_subtasks.setdefault(task_id, {})[agent_name] = sub

    def get_hub(self, task_id: str) -> TaskEventHub | None:
        """提供给 SSE 路由的 hub 查询入口 task 已结束则返回 None"""
        return self._hubs.get(task_id)

    # ============================================================ 后台主循环
    async def _run_task_loop(
        self,
        task_id: str,
        session_id: str,
        user_message: str,
        agents: list[str],
        input_mode: Literal["single", "multi"],
        owner_user_id: str,
    ) -> None:
        """主驱动: PENDING -> REPLYING(并发) -> DONE / CANCELLED

        单 agent 模式: reply 完成后自动写 selected_reply_agent
        多 agent 模式: 全部 reply 终态后切 DONE  等用户调 /select_reply
        """
        hub = self._hubs[task_id]
        try:
            # 请求到来时先静默检查会话上下文 token 是否超阈值
            # 超了就同步触发一次摘要再继续  失败也不阻塞 task  靠 except 捕获不让 reply 卡住
            await self._maybe_auto_compact(session_id)

            history = await self._build_history(session_id, current_task_id=task_id)

            # 切 REPLYING  publish 一次 task.state  推所有 agents 名让前端布局
            await self._set_state(task_id, TaskState.REPLYING)
            await hub.publish(
                TaskEvent(
                    type="task.state",
                    data={"state": "REPLYING", "agents": list(agents)},
                )
            )

            # 并发 fan-out  每个 agent 一个 reply 子任务
            self._reply_subtasks[task_id] = {}
            subtasks: list[asyncio.Task[None]] = []
            for name in agents:
                sub = asyncio.create_task(
                    self._do_reply_for_agent(task_id, name, user_message, history, hub, owner_user_id)
                )
                self._reply_subtasks[task_id][name] = sub
                subtasks.append(sub)

            # gather return_exceptions=True 让单卡失败不传染兄弟
            await asyncio.gather(*subtasks, return_exceptions=True)

            # 全部子任务终态  根据 input_mode 决定是否自动选答
            if input_mode == "single" and len(agents) == 1:
                solo = agents[0]
                # 单 agent 模式只在该 agent reply done 时自动选答
                round_obj = await self._storage.get_round(task_id)
                solo_reply = (round_obj.replies or {}).get(solo, {}) if round_obj else {}
                if solo_reply.get("state") == "done":
                    await self._storage.update_round_field(
                        task_id, "selected_reply_agent", solo
                    )
                    await hub.publish(
                        TaskEvent(
                            type="reply.selected",
                            data={"agent": solo, "auto": True},
                        )
                    )

            await self._set_state(task_id, TaskState.DONE)
            await hub.publish(
                TaskEvent(type="task.state", data={"state": "DONE"})
            )

            # 推 context.usage  优先用选中 agent  否则用第一个 done 的 agent
            usage_agent = await self._pick_usage_agent(task_id)
            if usage_agent is not None:
                await self._publish_context_usage(task_id, usage_agent, hub)

        except asyncio.CancelledError:
            _logger.info("task cancelled by user", task_id=task_id)
            try:
                await self._set_state(task_id, TaskState.CANCELLED)
                # 把所有 streaming/pending 的 reply 也置为 cancelled
                round_obj = await self._storage.get_round(task_id)
                if round_obj is not None:
                    for agent_name, reply in (round_obj.replies or {}).items():
                        rstate = (reply or {}).get("state")
                        if rstate in (None, "pending", "streaming"):
                            await self._storage.update_round_field(
                                task_id, f"replies.{agent_name}.state", "cancelled"
                            )
                await hub.publish(
                    TaskEvent(
                        type="task.state",
                        data={"state": "CANCELLED", "reason": "user_cancel"},
                    )
                )
            except Exception:
                _logger.exception(
                    "cancel 阶段写状态失败 忽略", task_id=task_id
                )
        except Exception as e:
            _logger.exception("task failed unrecoverable", task_id=task_id)
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
            # CANCELLED 状态保留 hub 不关闭 让 SSE 连接保持活跃
            # 这样 retry_reply 时前端可以继续收到事件而不需要重新建立 SSE 连接
            # 只有正常结束(DONE)或不可恢复异常才关闭 hub 并清理索引
            round_obj = await self._storage.get_round(task_id)
            if round_obj is not None and round_obj.state == TaskState.CANCELLED:
                # CANCELLED 只清 task 索引  保留 hub 给后续 retry 用
                self._tasks.pop(task_id, None)
            else:
                await hub.close()
                self._cleanup(task_id)

    async def _maybe_finalize_round(
        self, task_id: str, hub: TaskEventHub
    ) -> None:
        """retry_reply 子任务收尾时调  全 agent 都 done 就刷 round.state DONE 与事件

        重试完成后关闭 hub 并清理索引  否则 hub 会一直占内存
        """
        try:
            round_obj = await self._storage.get_round(task_id)
            if round_obj is None:
                return
            replies = round_obj.replies or {}
            still_running = any(
                (r or {}).get("state") in (None, "pending", "streaming")
                for r in replies.values()
            )
            if still_running:
                return
            await self._set_state(task_id, TaskState.DONE)
            await hub.publish(
                TaskEvent(type="task.state", data={"state": "DONE"})
            )
            # 重试成功 round 到 DONE 后 关闭 hub 清理索引  释放内存
            await hub.close()
            self._cleanup(task_id)
        except Exception:
            _logger.exception(
                "_maybe_finalize_round 失败 忽略", task_id=task_id
            )

    async def _pick_usage_agent(self, task_id: str) -> str | None:
        """挑用作 context.usage 计算的 agent  选中优先  否则第一个 done"""
        round_obj = await self._storage.get_round(task_id)
        if round_obj is None:
            return None
        if round_obj.selected_reply_agent:
            return round_obj.selected_reply_agent
        for name, reply in (round_obj.replies or {}).items():
            if (reply or {}).get("state") == "done":
                return name
        return None

    # ============================================================ reply 子任务
    async def _do_reply_for_agent(
        self,
        task_id: str,
        agent_name: str,
        user_message: str,
        history: list[dict[str, Any]],
        hub: TaskEventHub,
        owner_user_id: str,
    ) -> None:
        """单个 agent 的 reply 流式回答  写 replies.<agent>.* 字段

        节流策略: 把 LLM 吐的小 chunk 缓冲在内存 buf 中
        每 reply_flush_interval_ms 一次或回复结束时把 buf 一次性 append 到 mongo
        既减少写库次数 又保证最终内容完整

        段时间线持久化:
            按时间顺序维护 replies.<agent>.segments
            chunk 累积到 current_text_buf  tool_call / tool_result 到来时
            先把当前文本封成 text 段 push 到 segments_buf 再 push tool 段并整组写库
            reply.done 终态前把残余 text 封段  最后把 segments 一并落到 reply 终态文档里
        """
        await hub.publish(TaskEvent(type="reply.start", data={"agent": agent_name}))
        # 取一次 round.thinking_enabled  本轮 reply 是否走深度思考
        try:
            round_obj_for_thinking = await self._storage.get_round(task_id)
            thinking_enabled = bool(
                getattr(round_obj_for_thinking, "thinking_enabled", False)
            )
        except Exception:
            thinking_enabled = False

        await self._storage.update_reply_for_agent(
            task_id,
            agent_name,
            {
                "state": "streaming",
                "content": "",
                "started_at": _now_iso(),
                "segments": [],
            },
        )

        flush_buf: list[str] = []
        # segments_buf  按时间顺序的段时间线  最终覆盖写到 replies.<agent>.segments
        segments_buf: list[dict[str, Any]] = []
        current_text_buf: list[str] = []
        current_thinking_buf: list[str] = []
        loop = asyncio.get_event_loop()
        last_flush_ts = loop.time()
        flush_interval_s = self._settings.runtime.reply_flush_interval_ms / 1000.0

        def _flush_text_segment() -> bool:
            """把 current_text_buf 里的文本封成一个 text 段 push 到 segments_buf"""
            if not current_text_buf:
                return False
            text = "".join(current_text_buf)
            current_text_buf.clear()
            if not text:
                return False
            if segments_buf and segments_buf[-1].get("type") == "text":
                segments_buf[-1]["content"] = (
                    segments_buf[-1].get("content", "") + text
                )
            else:
                segments_buf.append({"type": "text", "content": text})
            return True

        def _flush_thinking_segment() -> bool:
            """把 current_thinking_buf 里的 reasoning 封成一个 thinking 段"""
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
            if ev.type == "reply.thinking":
                think_chunk = ev.data.get("chunk", "") or ""
                if think_chunk:
                    current_thinking_buf.append(think_chunk)
                now = loop.time()
                if now - last_flush_ts >= flush_interval_s and current_thinking_buf:
                    if _flush_thinking_segment():
                        try:
                            await self._storage.update_reply_segments_for_agent(
                                task_id, agent_name, segments_buf
                            )
                        except Exception:
                            _logger.exception(
                                "reply.thinking 段持久化失败 忽略",
                                task_id=task_id,
                                agent=agent_name,
                            )
                    last_flush_ts = now
                return

            # reply.chunk 单独走节流写库
            if ev.type == "reply.chunk":
                # 文本到来前  把 reasoning 累积先封段  保证段顺序 thinking 在 text 之前
                if current_thinking_buf:
                    if _flush_thinking_segment():
                        try:
                            await self._storage.update_reply_segments_for_agent(
                                task_id, agent_name, segments_buf
                            )
                        except Exception:
                            _logger.exception(
                                "reply.thinking 段持久化失败 忽略",
                                task_id=task_id,
                                agent=agent_name,
                            )
                chunk_text = ev.data.get("chunk", "") or ""
                if chunk_text:
                    flush_buf.append(chunk_text)
                    current_text_buf.append(chunk_text)
                now = loop.time()
                if now - last_flush_ts >= flush_interval_s and flush_buf:
                    await self._storage.append_reply_chunk_for_agent(
                        task_id, agent_name, "".join(flush_buf)
                    )
                    flush_buf.clear()
                    last_flush_ts = now
                return

            # 工具调用事件  按时间顺序封段  先把累积 reasoning / text 都封段  再 push tool 段
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
                    await self._storage.update_reply_segments_for_agent(
                        task_id, agent_name, segments_buf
                    )
                except Exception:
                    _logger.exception(
                        "reply.tool_call 段持久化失败 忽略",
                        task_id=task_id,
                        agent=agent_name,
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
                    await self._storage.update_reply_segments_for_agent(
                        task_id, agent_name, segments_buf
                    )
                except Exception:
                    _logger.exception(
                        "reply.tool_result 段持久化失败 忽略",
                        task_id=task_id,
                        agent=agent_name,
                    )
                return

        # 429 限流重试参数
        max_retries = self._settings.runtime.rate_limit_max_retries
        base_delay = self._settings.runtime.rate_limit_retry_delay_s
        max_delay = self._settings.runtime.rate_limit_retry_max_delay_s
        full_text: str = ""

        for attempt in range(max_retries + 1):
            try:
                full_text = await run_reply(
                    agent_name=agent_name,
                    user_message=user_message,
                    history=history,
                    registry=self._registry,
                    on_event=on_event,
                    thinking_enabled=thinking_enabled,
                    owner_user_id=owner_user_id,
                )
                # 成功 跳出重试循环
                break
            except asyncio.CancelledError:
                # 取消不重试 先刷盘已有内容再上抛
                _flush_thinking_segment()
                _flush_text_segment()
                try:
                    if flush_buf:
                        await self._storage.append_reply_chunk_for_agent(
                            task_id, agent_name, "".join(flush_buf)
                        )
                        flush_buf.clear()
                    if segments_buf:
                        await self._storage.update_reply_segments_for_agent(
                            task_id, agent_name, segments_buf
                        )
                    partial_text = "".join(current_text_buf)
                    await self._storage.update_reply_for_agent(
                        task_id,
                        agent_name,
                        {
                            "state": "cancelled",
                            "content": partial_text,
                            "segments": segments_buf,
                            "finished_at": _now_iso(),
                        },
                    )
                except Exception:
                    _logger.exception(
                        "cancel 刷盘失败 忽略",
                        task_id=task_id,
                        agent=agent_name,
                    )
                await hub.publish(
                    TaskEvent(
                        type="reply.error",
                        data={"agent": agent_name, "error": "cancelled"},
                    )
                )
                raise
            except Exception as e:
                # 判断是否为限流错误且仍有重试机会
                if is_rate_limit_error(e) and attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    retry_after_s = extract_retry_after(e)
                    if retry_after_s is not None:
                        delay = max(delay, retry_after_s)
                    _logger.info(
                        "agent 限流 自动重试",
                        task_id=task_id,
                        agent=agent_name,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay_s=delay,
                    )
                    # 推送 SSE 事件告知前端正在重试
                    await hub.publish(
                        TaskEvent(
                            type="reply.retry",
                            data={
                                "agent": agent_name,
                                "attempt": attempt + 1,
                                "max_retries": max_retries,
                                "delay_s": delay,
                                "reason": "rate_limit",
                            },
                        )
                    )
                    # 重置 reply 状态清空已刷内容 重新开始
                    await self._storage.update_reply_for_agent(
                        task_id,
                        agent_name,
                        {
                            "state": "streaming",
                            "content": "",
                            "segments": [],
                            "started_at": _now_iso(),
                        },
                    )
                    # 清空本地 buffer
                    flush_buf.clear()
                    segments_buf.clear()
                    current_text_buf.clear()
                    current_thinking_buf.clear()

                    await asyncio.sleep(delay)
                    continue

                # 非限流或重试次数耗尽 先刷盘已有内容再标记 failed
                friendly = humanize_llm_error(e)
                _logger.warning(
                    "agent reply 失败",
                    task_id=task_id,
                    agent=agent_name,
                    raw_error=str(e),
                    friendly=friendly,
                )
                _flush_thinking_segment()
                _flush_text_segment()
                try:
                    if flush_buf:
                        await self._storage.append_reply_chunk_for_agent(
                            task_id, agent_name, "".join(flush_buf)
                        )
                        flush_buf.clear()
                    if segments_buf:
                        await self._storage.update_reply_segments_for_agent(
                            task_id, agent_name, segments_buf
                        )
                    partial_text = "".join(current_text_buf)
                    await self._storage.update_reply_for_agent(
                        task_id,
                        agent_name,
                        {
                            "state": "failed",
                            "content": partial_text,
                            "segments": segments_buf,
                            "finished_at": _now_iso(),
                            "error": friendly,
                        },
                    )
                except Exception:
                    _logger.exception(
                        "reply 失败写状态二次报错 忽略",
                        task_id=task_id,
                        agent=agent_name,
                    )
                await hub.publish(
                    TaskEvent(
                        type="reply.error",
                        data={"agent": agent_name, "error": friendly},
                    )
                )
                # failed 是终态 不继续循环
                return

        # ---- 重试循环结束 如果成功走到这里 full_text 已拿到 ----
        # 兜底刷新剩余 chunk 防止丢
        if flush_buf:
            await self._storage.append_reply_chunk_for_agent(
                task_id, agent_name, "".join(flush_buf)
            )
            flush_buf.clear()
        # reply 完成前把尾部残余 reasoning / text 封段  保证 segments 是完整时间线
        _flush_thinking_segment()
        _flush_text_segment()
        finished_at_iso = _now_iso()
        await self._storage.update_reply_for_agent(
            task_id,
            agent_name,
            {
                "state": "done",
                "content": full_text,
                "started_at": _now_iso(),
                "finished_at": finished_at_iso,
                "segments": segments_buf,
            },
        )
        await hub.publish(
            TaskEvent(
                type="reply.done",
                data={
                    "agent": agent_name,
                    "content": full_text,
                    "finished_at": finished_at_iso,
                },
            )
        )

    # ============================================================ 状态机辅助
    async def _set_state(self, task_id: str, state: TaskState) -> None:
        """集中刷状态 + updated_at"""
        await self._storage.update_round_state(task_id, state)

    async def _build_history(
        self, session_id: str, current_task_id: str
    ) -> list[dict[str, Any]]:
        """取最近 N 轮 round 转成 langchain 友好格式

        约束:
            - 当前正在跑的 task 不进 history
            - 有选答且完成的 round  用选答的回复
            - 无选答的 round(被取消/失败等)  有内容的回复也纳入上下文  选内容最长的
            - 完全无内容(还没开始流式就取消)的 round 跳过
            - 取最近 history_max_rounds 轮
            - 若 session 已生成摘要 在最前面注入一条 system message 承载摘要
              并跳过 round_index <= summary_until_round 的旧轮次

        摘要注入策略:
            - 摘要文本以 "[会话摘要]\\n..." 开头  让 LLM 一眼识别这是浓缩历史
            - 摘要消息不进 history_max_rounds 计数  独立占一条
            - agent_runner._build_messages 必须识别 role=system 转 SystemMessage
        """
        session = await self._storage.get_session(session_id)
        summary_text = (session.summary or "") if session is not None else ""
        summary_until = (
            int(session.summary_until_round) if session is not None else 0
        )

        all_rounds = await self._storage.list_rounds(session_id)
        # (round, effective_agent) 列表  effective_agent 是最终用于 history 的 agent 名
        usable: list[tuple[Any, str]] = []
        for r in all_rounds:
            if r.task_id == current_task_id:
                continue
            eff_agent, eff_content = TaskManager._effective_reply_for_round(r)
            if eff_agent and eff_content:
                usable.append((r, eff_agent))

        # 已被摘要覆盖的 round 不再单独喂  避免与摘要内容重复占 token
        if summary_text:
            usable = [pair for pair in usable if pair[0].round_index > summary_until]

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
        for r, eff_agent in usable:
            out.append({"role": "user", "content": r.question})
            eff_reply = (r.replies or {}).get(eff_agent) or {}
            out.append(
                {
                    "role": "assistant",
                    "content": eff_reply.get("content", "") or "",
                    "agent": eff_agent,
                }
            )
        return out

    def _cleanup(self, task_id: str) -> None:
        """task 主循环结束后释放索引 防止内存堆积 hub 已 close"""
        self._hubs.pop(task_id, None)
        self._tasks.pop(task_id, None)
        # reply_subtasks 不强制 pop  retry_reply 起的子任务可能还在跑
        # 子任务自身完成后由 GC 回收 不会泄露
        # 这里仅清掉已经全部 done 的字典占位 减少长会话累计的 dict 体积
        sub_map = self._reply_subtasks.get(task_id)
        if sub_map is not None and all(t.done() for t in sub_map.values()):
            self._reply_subtasks.pop(task_id, None)

    # ============================================================ 上下文压缩与用量
    @staticmethod
    def _effective_reply_for_round(r: Any) -> tuple[str, str]:
        """取一个 round 中用于上下文的有效回复  返回 (agent_name, content)

        有选答且完成的 → 用选答
        无选答(被取消/失败等) → 用内容最长的回复
        无任何内容 → 返回 ("", "")
        """
        picked = r.selected_reply_agent
        if picked:
            picked_reply = (r.replies or {}).get(picked) or {}
            if picked_reply.get("state") == "done":
                return (picked, picked_reply.get("content", "") or "")
        # 无选答  找内容最长的回复
        best_agent = ""
        best_len = -1
        best_content = ""
        for agent_name, reply in (r.replies or {}).items():
            content = (reply or {}).get("content", "") or ""
            if len(content) > best_len:
                best_agent = agent_name
                best_len = len(content)
                best_content = content
        return (best_agent, best_content)

    def _resolve_compaction_agent_name(
        self,
        done_rounds: list[Any],
    ) -> str | None:
        """挑选用作摘要 / 用量评估的 agent 名

        策略:
            1 优先取最近一轮有效回复的 agent 名  通常就是用户期望的"当前模型"
            2 不在 registry 时回退到 compaction agent 指针
            3 都拿不到返回 None  上层判断不可压缩
        """
        registered = set(self._registry.names())
        for r in reversed(done_rounds):
            eff_agent, _ = TaskManager._effective_reply_for_round(r)
            if eff_agent in registered:
                return eff_agent
                return cand
        return None

    async def _maybe_auto_compact(self, session_id: str) -> None:
        """请求到来时静默检查并按需压缩  失败不阻塞主流"""
        try:
            lock = self._compact_locks.setdefault(session_id, asyncio.Lock())
            async with lock:
                session = await self._storage.get_session(session_id)
                if session is None:
                    return

                all_rounds = await self._storage.list_rounds(session_id)
                # 有有效回复内容的 round 都纳入(包括被取消/失败的)
                usable_rounds = []
                for r in all_rounds:
                    eff_agent, eff_content = TaskManager._effective_reply_for_round(r)
                    if eff_agent and eff_content:
                        usable_rounds.append(r)
                if not usable_rounds:
                    return

                summary = session.summary or ""
                summary_until = int(session.summary_until_round or 0)
                uncovered = [r for r in usable_rounds if r.round_index > summary_until]
                if not uncovered:
                    return

                agent_name = self._resolve_compaction_agent_name(usable_rounds)
                if agent_name is None:
                    try:
                        compaction_agent = await self._storage.get_compaction_agent_target()
                        if compaction_agent in self._registry.names():
                            agent_name = compaction_agent
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

                history_dicts: list[dict[str, Any]] = []
                if summary:
                    history_dicts.append(
                        {"role": "system", "content": f"[会话摘要]\n{summary}"}
                    )
                for r in uncovered:
                    _, eff_content = TaskManager._effective_reply_for_round(r)
                    history_dicts.append({"role": "user", "content": r.question})
                    history_dicts.append({"role": "assistant", "content": eff_content})

                used = count_history_tokens(history_dicts)
                if not should_trigger_summary(used, max_tokens):
                    return

                comp_history: list[dict[str, Any]] = []
                for r in uncovered:
                    _, eff_content = TaskManager._effective_reply_for_round(r)
                    comp_history.append({"role": "user", "content": r.question})
                    comp_history.append({"role": "assistant", "content": eff_content})
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
            _logger.exception("auto_compact 失败 忽略", session_id=session_id)

    async def _publish_context_usage(
        self, task_id: str, agent_name: str, hub: TaskEventHub
    ) -> None:
        """每轮 reply 完成后推一次 context.usage  让前端进度条同步"""
        try:
            round_obj = await self._storage.get_round(task_id)
            if round_obj is None:
                return
            session_id = round_obj.session_id
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

    async def _publish_context_usage_no_hub(
        self, session_id: str, agent_name: str
    ) -> None:
        """select_reply 时 hub 已 close 的情况  仅写库不推流"""
        try:
            history_session = await self._storage.get_session(session_id)
            if history_session is None:
                return
            # 简化做法 直接复用 _build_history 拿到含本轮的 history
            # current_task_id 给空串  list_rounds 返回的全部 done round 都进来
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
            await self._storage.update_session_context_usage(session_id, payload)
        except Exception:
            _logger.exception(
                "publish context.usage_no_hub 失败 忽略",
                session_id=session_id,
            )
