"""SPA 静态资源挂载骨架

仅在生产模式下启用 把 web/dist 打包产物挂载到根路径 /
开发模式下前端由 vite dev server 单独启动 通过反向代理或同源策略访问后端

注意:
    - 路径基于项目根的相对路径解析 不写绝对路径
    - 若 web/dist 不存在则跳过挂载 不阻塞应用启动
"""

from __future__ import annotations

from typing import Any


def mount_spa(app: Any, dist_dir: str = "web/dist") -> None:
    """把前端构建产物挂载为根路径

    参数:
        app: FastAPI 应用实例
        dist_dir: 构建产物目录 相对项目根
    """

    raise NotImplementedError("M2 实施")
