"""provider_profiles CRUD API 前端 SettingsDrawer 配置 base_url + api_key + 模型池

GET    /api/profiles          列出全部 profile  api_key 字段 mask
GET    /api/profiles/{name}   获取一条 同样 mask
POST   /api/profiles          新建 body 不传 version  同名 409
PUT    /api/profiles/{name}   局部更新 仅传需要改的字段
DELETE /api/profiles/{name}   删除 仍被 agent 引用返 409

设计原则:
    - api_key 入库明文 接口返回前端时仅展示末 4 位 防止泄漏
    - profile 与 agent 解耦  4 个 agent 可分别引用不同 profile
    - 修改 profile 后  所有引用此 profile 的 agent 的 deep_agent 实例都需要 reload
      避免内存里的 ChatOpenAI 仍持有旧 base_url / api_key
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.models import ModelCatalogEntry, ProviderProfile

router = APIRouter(prefix="/api", tags=["profiles"])

_logger = structlog.get_logger(__name__)


class ModelView(BaseModel):
    """profile 内一条可选模型的对外视图"""

    model_id: str
    label: str = ""


class ProfileView(BaseModel):
    """对外的 profile 视图  api_key 已被 mask 仅展示末 4 位"""

    name: str
    provider_type: str
    base_url: str
    api_key: str
    models: list[ModelView]
    version: int
    updated_at: str


class CreateProfileRequest(BaseModel):
    """POST /api/profiles 请求体"""

    name: str = Field(min_length=1, max_length=64)
    provider_type: str = "openai_compatible"
    base_url: str = Field(min_length=8)
    api_key: str = Field(min_length=4)
    models: list[ModelView] = Field(default_factory=list)


class UpdateProfileRequest(BaseModel):
    """PUT /api/profiles/{name} 请求体  至少一项 base_url/api_key/models 不为 None"""

    base_url: str | None = None
    api_key: str | None = None
    models: list[ModelView] | None = None


def _mask_key(k: str) -> str:
    """把 api_key 转成 mask 形式  仅展示首 3 位 + 末 4 位

    长度过短直接返回 *** 防止暴露原文
    """
    if not k or len(k) < 8:
        return "***"
    return f"{k[:3]}...{k[-4:]}"


def _to_view(p: ProviderProfile) -> ProfileView:
    """ProviderProfile -> 前端 ProfileView  api_key 走 mask"""
    return ProfileView(
        name=p.name,
        provider_type=p.provider_type,
        base_url=p.base_url,
        api_key=_mask_key(p.api_key),
        models=[ModelView(model_id=m.model_id, label=(m.label or m.model_id)) for m in p.models],
        version=p.version,
        updated_at=p.updated_at.isoformat() if hasattr(p.updated_at, "isoformat") else str(p.updated_at),
    )


@router.get("/profiles", response_model=list[ProfileView])
async def list_profiles(request: Request) -> list[ProfileView]:
    """列出全部 profile  api_key 已 mask"""
    storage = request.app.state.storage
    profiles = await storage.list_profiles()
    return [_to_view(p) for p in profiles]


@router.get("/profiles/{name}", response_model=ProfileView)
async def get_profile(name: str, request: Request) -> ProfileView:
    """单条 profile  api_key 已 mask"""
    storage = request.app.state.storage
    p = await storage.get_profile(name)
    if p is None:
        raise HTTPException(404, f"profile not found: {name}")
    return _to_view(p)


@router.post("/profiles", response_model=ProfileView, status_code=201)
async def create_profile(body: CreateProfileRequest, request: Request) -> ProfileView:
    """新建 profile  同名已存在返 409"""
    storage = request.app.state.storage
    new_p = ProviderProfile(
        name=body.name,
        provider_type=body.provider_type,
        base_url=body.base_url,
        api_key=body.api_key,
        models=[ModelCatalogEntry(model_id=m.model_id, label=m.label) for m in body.models],
    )
    try:
        created = await storage.create_profile(new_p)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    # 新 profile 加入不影响已有 agent 的实例 不需要 reload
    return _to_view(created)


@router.put("/profiles/{name}", response_model=ProfileView)
async def update_profile(
    name: str, body: UpdateProfileRequest, request: Request
) -> ProfileView:
    """局部更新 profile  并把所有引用它的 agent 实例 reload"""
    storage = request.app.state.storage
    registry = request.app.state.deep_agents

    if await storage.get_profile(name) is None:
        raise HTTPException(404, f"profile not found: {name}")
    if body.base_url is None and body.api_key is None and body.models is None:
        raise HTTPException(400, "至少传一项 base_url/api_key/models")

    # ModelView -> ModelCatalogEntry  storage 层接收两种都行 这里统一
    models_payload: list[ModelCatalogEntry] | None = None
    if body.models is not None:
        models_payload = [
            ModelCatalogEntry(model_id=m.model_id, label=m.label) for m in body.models
        ]

    updated = await storage.update_profile(
        name,
        base_url=body.base_url,
        api_key=body.api_key,
        models=models_payload,
    )

    # 修改 profile 后 所有引用此 profile 的 agent 的 deep_agent 实例需要 reload
    # 单个 agent 的 reload 失败不阻塞其它 agent  仅记日志 让前端看到 200 即视为更新成功
    affected = [r for r in await storage.list_agents() if r.profile_name == name]
    for r in affected:
        try:
            await registry.reload(r, updated)
        except Exception as exc:
            _logger.error(
                "profile 更新后 reload 失败",
                profile=name,
                agent=r.name,
                err=str(exc),
            )

    return _to_view(updated)


@router.delete("/profiles/{name}", status_code=204)
async def delete_profile(name: str, request: Request) -> None:
    """删除 profile  仍被 agent 引用返 409 不存在返 404"""
    storage = request.app.state.storage
    if await storage.get_profile(name) is None:
        raise HTTPException(404, f"profile not found: {name}")
    try:
        await storage.delete_profile(name)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
