"""冒烟测试 仅做 import 级验证

create_app 真正启动需要连 mongo 不在单测里跑 集成验证留给 e2e/手工启动
"""

from __future__ import annotations


def test_import_package() -> None:
    """import multichat 不报错 且暴露版本号"""
    import multichat

    assert multichat.__version__ == "0.1.0"


def test_create_app_module_importable() -> None:
    """multichat.main 模块可 import create_app 是 callable

    注意 这里不调用 create_app 也不进入 lifespan 因为 startup 会真实连 mongo
    集成验证由 e2e 或手工 docker-compose 起 mongo 后跑
    """
    from multichat.main import create_app

    assert callable(create_app)


def test_create_app_registers_auth_routes() -> None:
    """应用工厂应显式挂上 /api/auth 路由  否则前端刷新会直接 404"""
    from multichat.main import create_app

    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/api/auth/me" in paths
    assert "/api/auth/login" in paths
