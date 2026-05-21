"""SSE 协议封装 把 TaskEventHub 的事件流写成 text/event-stream

设计要点
    - sse-starlette 的 EventSourceResponse 接受异步迭代器 内部已处理
      text/event-stream header 与 chunked 编码 上层只管 yield {event,data} dict
    - 订阅 hub 后 先把历史事件以一条 type=snapshot 的复合帧推给前端
      这样新订阅者(刷新页面 重连等)能在不依赖增量推送的情况下重建当前状态
    - 后续从 queue 持续吐增量事件 直到收到 None 表示流关闭
    - keepalive 用 sse-starlette 自带 ping 机制 每 15 秒推一个注释帧
      防 Nginx/反向代理因空闲超时主动断连

异步对象与事件循环绑定问题(参考全局规范)
    - hub 内部 queue 必须在使用它的 loop 中创建
    - 此处仅作消费 不跨线程 push 谁创建 queue 谁消费
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

import structlog
from sse_starlette.sse import EventSourceResponse

if TYPE_CHECKING:
    # 真实 TaskEvent / TaskEventHub 由 M2 在 core/events.py 中实装
    # 路由层 SSE 仅依赖鸭子类型 这里仅给类型注解使用
    from .events import TaskEvent, TaskEventHub  # noqa: F401

_logger = structlog.get_logger(__name__)


def _format_event(event: "TaskEvent") -> dict:
    """把 TaskEvent 转成 sse_starlette 接受的 dict 形式

    sse_starlette 会读 event 字段写入 SSE event 行 data 字段写入 SSE data 行
    """
    return {
        "event": event.type,
        "data": json.dumps(event.data, ensure_ascii=False),
    }


async def stream_hub(hub: "TaskEventHub") -> AsyncIterator[dict]:
    """订阅 hub 先吐 snapshot 帧再吐增量

    snapshot 帧把所有历史事件压成一条 type=snapshot 的事件
    data 结构 {"events": [event_dict, ...]} event_dict 即 TaskEvent.to_dict()
    前端收到 snapshot 后应清空本地 task 状态再按 events 顺序回放
    """
    history, queue = await hub.subscribe()
    try:
        # 1. snapshot 单帧 把已有事件批量交给前端
        snap_event = {
            "event": "snapshot",
            "data": json.dumps(
                {"events": [e.to_dict() for e in history]},
                ensure_ascii=False,
            ),
        }
        yield snap_event

        # 2. 后续增量事件 直到收到 None 表示 hub 关闭
        while True:
            event = await queue.get()
            if event is None:
                break
            yield _format_event(event)
    finally:
        # 不论正常关闭还是客户端断开 都要从 hub 注销 释放队列资源
        await hub.unsubscribe(queue)


def build_sse_response(hub: "TaskEventHub") -> EventSourceResponse:
    """供路由直接 return 的 SSE 响应 内部带 keepalive

    ping=15 表示 sse-starlette 每 15 秒发一个注释帧维持长连接
    生产环境若反向代理空闲超时更短 需要相应调小这里的间隔
    """
    return EventSourceResponse(
        stream_hub(hub),
        ping=15,
    )
