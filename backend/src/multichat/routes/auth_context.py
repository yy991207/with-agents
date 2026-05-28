"""认证上下文依赖

负责把 cookie session 解析成当前请求身份，供后续路由统一复用。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request

from ..core.models import RequestIdentity


def _is_session_expired(session) -> bool:
    """判断登录态是否过期"""
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


async def get_current_identity(request: Request) -> RequestIdentity:
    """从 cookie session 中解析当前身份，不存在或失效直接 401"""
    settings = request.app.state.settings
    storage = request.app.state.storage
    session_id = request.cookies.get(settings.auth.session_cookie_name)
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

    return RequestIdentity(
        user_id=user.user_id,
        username=user.username,
    )