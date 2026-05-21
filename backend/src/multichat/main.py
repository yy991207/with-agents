"""FastAPI 应用工厂 集中配置生命周期事件与路由挂载

启动 lifespan 顺序:
    1. load_settings 读取 yaml
    2. MotorMongoStorage.connect 建立 motor 客户端 并 ensure_indexes
    3. seed_from_yaml 首次注入 agents 与 judge 指针 已存在则跳过
    4. 从 DB 列出 agents 用 settings 构造 DeepAgentRegistry 并 initialize 8 个实例
    5. 实例化 TaskManager(storage registry settings) 挂到 app.state
    6. 把 storage settings registry task_manager 挂到 app.state 路由层共享
关闭阶段释放 motor 客户端
deep_agents 没有外部资源 langchain client 内部 httpx 池随进程退出 不需要单独 shutdown
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, load_settings
from .core.task_manager import TaskManager
from .llm.deep_agents import build_registry
from .routes.agents import router as agents_router
from .routes.ask import router as ask_router
from .routes.cancel import router as cancel_router
from .routes.decide import router as decide_router
from .routes.history import router as history_router
from .routes.retry_think import router as retry_think_router
from .routes.sessions import router as sessions_router
from .routes.stream import router as stream_router
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

    # M2 任务管理器 注入 storage registry settings 三件套
    # 此处签名以 M2 实装的 TaskManager(storage registry settings) 为准
    # 若 M2 当前骨架仍为零参 启动时会抛 TypeError 这是预期 整合阶段统一对齐
    task_manager = TaskManager(storage, registry, settings)

    app.state.settings = settings
    app.state.storage = storage
    app.state.deep_agents = registry
    app.state.task_manager = task_manager
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

    # 跨域中间件 必须在 app 创建后立即添加 不能放进 lifespan
    # 这里为了兼容前端 dev 模式(5173/5175 等任意端口) 写死宽容策略
    # allow_credentials 设为 False 是因为 allow_origins=["*"] 与 credentials=True 互斥
    # 真生产环境再收紧成可配置白名单
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # 路由挂载
    # M1: agents/judge CRUD
    app.include_router(agents_router)
    # M3: 对话核心 + 历史
    app.include_router(ask_router)
    app.include_router(decide_router)
    app.include_router(cancel_router)
    app.include_router(retry_think_router)
    app.include_router(stream_router)
    app.include_router(history_router)
    app.include_router(sessions_router)

    return app
