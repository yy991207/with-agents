"""FastAPI 应用工厂 集中配置生命周期事件与路由挂载

当前为 M1 骨架阶段 仅暴露一个健康检查路由 占位 真实业务路由在后续模块逐步接入
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期钩子 启动时初始化资源 关闭时回收资源

    后续会在这里创建 MongoStorage 连接 deepagents 实例池 TaskManager 等共享对象
    """
    # 启动阶段占位:加载配置 初始化存储与 LLM 资源
    yield
    # 关闭阶段占位:释放连接 取消后台任务


def create_app(config_path: str | None = None) -> FastAPI:
    """应用工厂 供 uvicorn --factory 调用

    参数:
        config_path: 可选的配置文件路径 缺省时按 multichat.config 默认搜索规则加载
    """

    app = FastAPI(
        title="multichat-backend",
        version="0.1.0",
        description="多模型协同对话服务 think-then-choose 模式",
        lifespan=_lifespan,
    )

    # 这里仅挂一个健康检查路由 真实业务路由会在 routes 子模块逐步接入
    @app.get("/")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # config_path 暂时占位 后续在 multichat.config 中实际消费
    _ = config_path

    return app
