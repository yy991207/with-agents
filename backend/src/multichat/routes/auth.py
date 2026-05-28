"""最小可用认证路由

当前目标：
    - 注册时创建用户，自动登录
    - 登录后通过 httpOnly cookie 建立 session
    - 提供 me / logout 让前端识别当前登录状态
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..core.auth import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    """注册请求体 最小化 只包含用户名和密码"""

    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """登录请求体"""

    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class AuthUserView(BaseModel):
    """登录态对外视图"""

    user_id: str
    username: str


def _session_cookie_name(settings) -> str:
    """读取 session cookie 名称"""
    return settings.auth.session_cookie_name


def _is_session_expired(session) -> bool:
    """判断登录态是否过期"""
    expires_at = session.expires_at
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


@router.post("/register", response_model=AuthUserView, status_code=201)
async def register(body: RegisterRequest, request: Request, response: Response) -> AuthUserView:
    """注册用户 自动创建 session 并写 cookie"""
    storage = request.app.state.storage
    settings = request.app.state.settings

    existing_user = await storage.get_user_by_username(body.username)
    if existing_user is not None:
        raise HTTPException(409, f"username 已存在 username={body.username}")

    password_hash = hash_password(body.password, settings.auth.password_pepper)
    user = await storage.create_user(body.username, password_hash)

    session = await storage.create_auth_session(
        user.user_id,
        expires_in_hours=settings.auth.session_ttl_hours,
    )
    response.set_cookie(
        key=_session_cookie_name(settings),
        value=session.session_id,
        httponly=True,
        samesite="lax",
        secure=settings.auth.session_cookie_secure,
        max_age=settings.auth.session_ttl_hours * 3600,
        path="/",
    )
    return AuthUserView(
        user_id=user.user_id,
        username=user.username,
    )


@router.post("/login", response_model=AuthUserView)
async def login(body: LoginRequest, request: Request, response: Response) -> AuthUserView:
    """账号密码登录 成功后写 httpOnly cookie"""
    storage = request.app.state.storage
    settings = request.app.state.settings

    user = await storage.get_user_by_username(body.username)
    if user is None:
        raise HTTPException(401, "账号或密码错误")
    if not verify_password(body.password, settings.auth.password_pepper, user.password_hash):
        raise HTTPException(401, "账号或密码错误")

    session = await storage.create_auth_session(
        user.user_id,
        expires_in_hours=settings.auth.session_ttl_hours,
    )
    response.set_cookie(
        key=_session_cookie_name(settings),
        value=session.session_id,
        httponly=True,
        samesite="lax",
        secure=settings.auth.session_cookie_secure,
        max_age=settings.auth.session_ttl_hours * 3600,
        path="/",
    )
    return AuthUserView(
        user_id=user.user_id,
        username=user.username,
    )


@router.get("/me", response_model=AuthUserView)
async def me(request: Request) -> AuthUserView:
    """读取当前登录态"""
    settings = request.app.state.settings
    storage = request.app.state.storage
    session_id = request.cookies.get(_session_cookie_name(settings))
    if not session_id:
        raise HTTPException(401, "未登录")
    session = await storage.get_auth_session(session_id)
    if session is None:
        raise HTTPException(401, "未登录")
    if _is_session_expired(session):
        await storage.delete_auth_session(session.session_id)
        raise HTTPException(401, "登录态已过期")
    user = await storage.get_user_by_id(session.user_id)
    if user is None:
        raise HTTPException(401, "登录态无效")
    return AuthUserView(
        user_id=user.user_id,
        username=user.username,
    )


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    """退出登录 删除服务端 session 并清 cookie"""
    settings = request.app.state.settings
    storage = request.app.state.storage
    session_id = request.cookies.get(_session_cookie_name(settings))
    if session_id:
        await storage.delete_auth_session(session_id)
    response.delete_cookie(
        key=_session_cookie_name(settings),
        path="/",
    )
    return {"ok": True}