"""最小可用认证路由

当前目标：
    - 注册时创建租户和该租户下第一个用户
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
    """注册请求体 最小只包含租户和首个用户信息"""

    tenant_name: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    """登录请求体"""

    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class AuthUserView(BaseModel):
    """登录态对外视图"""

    tenant_id: str
    tenant_name: str
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


async def _build_auth_view(storage, tenant_id: str, user_id: str) -> AuthUserView:
    """拼装登录态返回结构"""
    tenant = await storage.get_tenant_by_id(tenant_id)
    user = await storage.get_user_by_id(user_id)
    if tenant is None or user is None:
        raise HTTPException(401, "登录态无效")
    return AuthUserView(
        tenant_id=tenant.tenant_id,
        tenant_name=tenant.tenant_name,
        user_id=user.user_id,
        username=user.username,
    )


@router.post("/register", response_model=AuthUserView, status_code=201)
async def register(body: RegisterRequest, request: Request) -> AuthUserView:
    """注册租户和首个用户"""
    storage = request.app.state.storage
    settings = request.app.state.settings

    tenant = await storage.get_tenant_by_name(body.tenant_name)
    if tenant is None:
        tenant = await storage.create_tenant(body.tenant_name)
    password_hash = hash_password(body.password, settings.auth.password_pepper)
    existing_user = await storage.get_user_by_username(tenant.tenant_id, body.username)
    if existing_user is not None:
        raise HTTPException(409, f"username 已存在 tenant_name={body.tenant_name} username={body.username}")
    role = "owner" if await storage.get_user_count_by_tenant(tenant.tenant_id) == 0 else "member"
    user = await storage.create_user(tenant.tenant_id, body.username, password_hash, role=role)
    return AuthUserView(
        tenant_id=tenant.tenant_id,
        tenant_name=tenant.tenant_name,
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
    tenant = await storage.get_tenant_by_id(user.tenant_id)
    if tenant is None:
        raise HTTPException(401, "登录态数据异常")
    if not verify_password(body.password, settings.auth.password_pepper, user.password_hash):
        raise HTTPException(401, "账号或密码错误")

    session = await storage.create_auth_session(
        tenant.tenant_id,
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
        tenant_id=tenant.tenant_id,
        tenant_name=tenant.tenant_name,
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
    return await _build_auth_view(storage, session.tenant_id, session.user_id)


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
