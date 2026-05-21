"""SPA 静态资源挂载

仅在 web/dist 存在时启用 把构建产物挂到根路径 /
开发模式下 web/dist 不存在 不阻塞应用启动 走 vite dev server + proxy

设计要点:
    - 路径基于项目根的相对路径解析 不写绝对路径
    - 必须在所有 include_router 之后调用 才能让 API 路由优先匹配
    - 缺失目录或缺失 index.html 都静默跳过 仅打日志
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

_logger = structlog.get_logger(__name__)


def mount_spa(app: FastAPI, dist_dir: str = "web/dist") -> None:
    """把前端构建产物挂载为根路径

    参数:
        app: FastAPI 应用实例
        dist_dir: 构建产物目录 相对项目根

    生效条件: 目录存在且包含 index.html
    """
    # 项目根定位:
    #   backend/src/multichat/routes/static_spa.py
    #     parents[0] routes
    #     parents[1] multichat
    #     parents[2] src
    #     parents[3] backend
    #     parents[4] 项目根
    project_root = Path(__file__).resolve().parents[4]
    dist_path = project_root / dist_dir
    if not dist_path.exists():
        _logger.info("web 静态产物不存在 跳过挂载", path=str(dist_path))
        return
    if not (dist_path / "index.html").exists():
        _logger.warning("web/dist 缺少 index.html 跳过挂载", path=str(dist_path))
        return
    # html=True 让 GET / 自动返回 index.html 支持 SPA 路由由前端处理
    app.mount("/", StaticFiles(directory=str(dist_path), html=True), name="spa")
    _logger.info("挂载前端 SPA", path=str(dist_path))
