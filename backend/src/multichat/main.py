"""FastAPI 应用工厂 集中配置生命周期事件与路由挂载

启动 lifespan 顺序:
    1. load_settings 读取 yaml
    2. MotorMongoStorage.connect 建立 motor 客户端 并 ensure_indexes
    3. seed_from_yaml 首次注入 agents 与 judge 指针 已存在则跳过
    4. 从 DB 列出 agents 用 settings 构造 DeepAgentRegistry 并 initialize 8 个实例
    5. 把 storage settings registry 挂到 app.state 路由层共享
关闭阶段释放 motor 客户端
deep_agents 没有外部资源 langchain client 内部 httpx 池随进程退出 不需要单独 shutdown
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from .config import Settings, load_settings
from .llm.deep_agents import build_registry
from .routes.agents import router as agents_router
from .storage.mongo import MotorMongoStorage

_logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期钩子 启动时初始化资源 关闭时回收资源"""
    config_path: str | None = getattr(app.state, "_config_path", None)
    settings: Settings = load_settings(config_path)
    storage = await MotorMongoStorage.connect(settings.mongo)
    seeded = await storage.seed_from_yaml(settings)
    _logger.info("seed: %d agents written" % seeded)

    # 从 DB 加载已有 agents 启动 deep_agent 注册表
    # 必须在 seed 之后 否则首次启动时 list_agents 为空会抛 ValueError
    records = await storage.list_agents()
    registry = build_registry(settings)
    await registry.initialize(records)

    app.state.settings = settings
    app.state.storage = storage
    app.state.deep_agents = registry
    try:
        yield
    finally:
        await storage.close()


def create_app(config_path: str | None = None) -> FastAPI:
    """应用工厂 供 uvicorn --factory 调用

    参数 config_path 在 lifespan 中使用 通过 app.state 传递
    """

    app = FastAPI(
        title="multichat-backend",
        version="0.1.0",
        description="多模型协同对话服务 think-then-choose 模式",
        lifespan=_lifespan,
    )
    # 把配置路径暂存到 app.state 让 lifespan 启动时读到
    app.state._config_path = config_path

    @app.get("/")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # 路由挂载 当前仅 M1.C 的 agents/judge CRUD 其余 ask/decide 等 M2 再接
    app.include_router(agents_router)

    return app
