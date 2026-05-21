"""冒烟测试 验证包能正常 import 应用工厂可调用"""

from __future__ import annotations


def test_import_package() -> None:
    """import multichat 不报错 且暴露版本号"""
    import multichat

    assert multichat.__version__ == "0.1.0"


def test_create_app_returns_fastapi() -> None:
    """create_app 不传参时能正确返回 FastAPI 实例"""
    from fastapi import FastAPI

    from multichat.main import create_app

    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "multichat-backend"
