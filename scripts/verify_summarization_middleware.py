"""红绿灯验证脚本 检查 deepagents create_deep_agent 默认是否挂了 SummarizationMiddleware

执行方式
    conda activate multi-chat
    python scripts/verify_summarization_middleware.py

验证两件事
    1 静态层 检查 create_deep_agent 返回的 graph 中间件栈里有没有 SummarizationMiddleware
    2 行为层 给 reply 实例硬塞一段超长 fake history 真的发一次请求 看是不是触发了摘要
       触发标志 graph state 里出现 _summarization_event 字段或者 token 数明显被压下去

为了不污染正式 tests 目录 这里独立成一个 scripts 脚本 便于一次性核查
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 让脚本能 import backend 的 src 包
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepagents import create_deep_agent  # noqa: E402
from deepagents.middleware.summarization import (  # noqa: E402
    SummarizationMiddleware,
    _DeepAgentsSummarizationMiddleware,
)
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

# 直接读项目 config.yaml 拿凭据 不走环境变量 避免污染 base
import yaml  # noqa: E402

CFG_PATH = ROOT / "config.yaml"


def load_creds() -> tuple[str, str]:
    """从 config.yaml 读 key 与 base_url"""
    with CFG_PATH.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg["key"], cfg["base_url"]


def make_long_history(rounds: int = 60) -> list:
    """造一段假对话 每条 1000 字以上 撑满 token 触发摘要

    rounds=60 大约 12 万字 约 5~6 万 token 大模型也得摘
    """
    msgs: list = []
    filler = "这是一段用来撑长上下文的填充文本 内容无关紧要 只是为了把 token 数顶上去 让摘要中间件被触发 " * 30
    for i in range(rounds):
        msgs.append(HumanMessage(content=f"第 {i} 轮用户提问 {filler}"))
        msgs.append(AIMessage(content=f"第 {i} 轮 AI 回答 {filler}"))
    return msgs


def list_middleware(graph) -> list[str]:
    """从 compiled graph 里把所有 middleware 类名抠出来

    langgraph 编译后 middleware 一般挂在 graph 的私有属性上
    这里宽松点 直接 dir 翻一圈 找带 middleware 的 attr
    """
    found: list[str] = []
    # langchain.agents.middleware 编译后的图 一般有 .nodes 或 .builder
    for attr in ("middleware", "_middleware", "middlewares"):
        v = getattr(graph, attr, None)
        if v:
            for m in v:
                found.append(type(m).__name__)
    # builder 方式
    builder = getattr(graph, "builder", None)
    if builder is not None:
        for attr in ("middleware", "_middleware", "middlewares"):
            v = getattr(builder, attr, None)
            if v:
                for m in v:
                    found.append(type(m).__name__)
    return found


async def main() -> None:
    key, base_url = load_creds()

    # 用便宜的 qwen-turbo 跑摘要 主对话也走它 省钱
    model = ChatOpenAI(
        model="qwen-turbo",
        api_key=key,
        base_url=base_url,
        timeout=60,
        max_retries=1,
    )

    print("=" * 60)
    print("步骤 1 静态层 检查 middleware 栈")
    print("=" * 60)
    graph = create_deep_agent(
        model=model,
        tools=[],
        system_prompt="你是一个测试助手 简短回答即可",
        name="verify-agent",
    )
    mw_names = list_middleware(graph)
    print(f"发现的 middleware 类名 共 {len(mw_names)} 个")
    for n in mw_names:
        print(f"  - {n}")

    has_sum = any(
        n in ("SummarizationMiddleware", "_DeepAgentsSummarizationMiddleware")
        for n in mw_names
    )
    print()
    print(f"SummarizationMiddleware 是否在栈中  {'是' if has_sum else '否'}")

    print()
    print("=" * 60)
    print("步骤 2 行为层 喂超长 history 看是否触发摘要")
    print("=" * 60)
    fake_history = make_long_history(rounds=60)
    fake_history.append(HumanMessage(content="一句话告诉我刚才你做了什么"))
    print(f"投喂消息数  {len(fake_history)} 条")

    try:
        state = await graph.ainvoke(
            {"messages": fake_history},
            config={"recursion_limit": 50},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"调用失败  {type(exc).__name__}: {exc}")
        return

    print(f"返回 state 的 keys  {list(state.keys()) if isinstance(state, dict) else type(state)}")

    # 看摘要事件
    sum_event = None
    if isinstance(state, dict):
        sum_event = state.get("_summarization_event") or state.get("summarization_event")

    final_msgs = state.get("messages", []) if isinstance(state, dict) else []
    print(f"返回 messages 数  {len(final_msgs)}")
    print(f"_summarization_event 是否出现  {'是' if sum_event else '否'}")
    if sum_event:
        print(f"  事件内容  {str(sum_event)[:300]}")

    # 最后一条 AI 文本
    last_ai = None
    for m in reversed(final_msgs):
        kind = getattr(m, "type", None) or getattr(m, "role", None)
        if kind in ("ai", "assistant"):
            last_ai = m
            break
    if last_ai is not None:
        content = getattr(last_ai, "content", "")
        if isinstance(content, list):
            content = "".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        print(f"末尾 AI 回复前 200 字  {str(content)[:200]}")

    print()
    print("=" * 60)
    print("结论")
    print("=" * 60)
    if has_sum and sum_event:
        print("绿灯  默认就挂了 SummarizationMiddleware 而且确实被触发了 不需要额外接入")
    elif has_sum and not sum_event:
        print("黄灯  middleware 在栈里但本次 history 没到摘要阈值 可以放更长 history 再试")
    else:
        print("红灯  默认未挂 SummarizationMiddleware 需要在 _build_one 里显式传 middleware=[...]")


if __name__ == "__main__":
    asyncio.run(main())
