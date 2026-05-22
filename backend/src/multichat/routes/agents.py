"""agents 配置 CRUD API 前端 SettingsDrawer 使用

GET    /api/agents                 列出全部 agent 配置 + 当前 judge 指针
POST   /api/agents                 新增 agent  自动生成内部稳定 name
PUT    /api/agents/{name}          局部更新某个 agent 内部触发热替换
DELETE /api/agents/{name}          删除 agent  judge target 不允许删
PUT    /api/judge                  更新 judge 指针(选哪个 agent 当裁判)
GET    /api/agents/{name}/history  列出 agent 历史版本
POST   /api/agents/{name}/revert   回滚到指定历史版本

设计原则
    - agent 数量自由 可加可减 形成"团队管理"
    - name 是内部稳定 ID 不可变 用于 round 引用
    - display_name 给前端展示 可改
    - PUT 的写入顺序: DB upsert 成功后再触发 registry.reload
      reload 失败必须回滚 DB 否则会出现 DB 与内存版本不一致
    - 乐观锁可选 expected_version 不传则默认强制覆盖
    - judge 指针不需要热替换 LLM 调用时按名字现取
    - api_key 接口返回前端时由 _mask_key mask 仅展示首3末4位
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.models import ModelCatalogEntry

router = APIRouter(prefix="/api", tags=["agents"])

_logger = structlog.get_logger(__name__)


def _mask_key(k: str) -> str:
    """把 api_key 转成 mask 形式  仅展示首 3 位 + 末 4 位

    长度过短直接返回 *** 防止暴露原文
    """
    if not k or len(k) < 8:
        return "***"
    return f"{k[:3]}...{k[-4:]}"


class ModelView(BaseModel):
    """agent.available_models 中的一项 对外视图"""

    model_id: str
    label: str = ""


class AgentView(BaseModel):
    """对外的 agent 视图 完整数字员工字段  api_key 已 mask"""

    name: str
    display_name: str
    provider_type: str
    base_url: str
    api_key: str
    model: str
    available_models: list[ModelView]
    prompt: str
    version: int
    updated_at: str


class AgentsListResponse(BaseModel):
    """GET /api/agents 响应 agents 数组 + 当前 judge 指针"""

    agents: list[AgentView]
    judge_target: str


class CreateAgentRequest(BaseModel):
    """POST /api/agents 请求体  name 由后端自动生成"""

    display_name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=8)
    api_key: str = Field(min_length=4)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=5)
    available_models: list[ModelView] = Field(default_factory=list)
    provider_type: str = "openai_compatible"


class UpdateAgentRequest(BaseModel):
    """PUT /api/agents/{name} 请求体  至少一项非空"""

    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    available_models: list[ModelView] | None = None
    prompt: str | None = None
    provider_type: str | None = None
    expected_version: int | None = None


class UpdateAgentResponse(BaseModel):
    """PUT /api/agents/{name} 响应 包含新 version 与是否成功热替换"""

    name: str
    version: int
    reloaded: bool


class UpdateJudgeRequest(BaseModel):
    """PUT /api/judge 请求体 target 必须是已知 agent 内部 name"""

    target: str


class AgentHistoryItem(BaseModel):
    """历史版本展示用 view  字段直接来自 agent_history 集合"""

    name: str
    display_name: str
    base_url: str
    api_key: str
    model: str
    available_models: list[ModelView]
    prompt: str
    provider_type: str
    version: int
    archived_at: str
    archived_reason: str


class RevertAgentRequest(BaseModel):
    """POST /api/agents/{name}/revert 请求体 指定要回滚到的历史版本号"""

    target_version: int


def _to_view(record) -> AgentView:
    """AgentRecord -> 前端 AgentView  api_key 走 mask"""
    return AgentView(
        name=record.name,
        display_name=record.display_name or record.name,
        provider_type=record.provider_type,
        base_url=record.base_url,
        api_key=_mask_key(record.api_key),
        model=record.model,
        available_models=[
            ModelView(model_id=m.model_id, label=(m.label or m.model_id))
            for m in record.available_models
        ],
        prompt=record.prompt,
        version=record.version,
        updated_at=record.updated_at.isoformat()
        if hasattr(record.updated_at, "isoformat")
        else str(record.updated_at),
    )


def _models_payload(models: list[ModelView] | None) -> list[ModelCatalogEntry] | None:
    """把对外 ModelView 列表转成内部 ModelCatalogEntry 列表  None 透传"""
    if models is None:
        return None
    return [ModelCatalogEntry(model_id=m.model_id, label=m.label) for m in models]


@router.get("/agents", response_model=AgentsListResponse)
async def list_agents(request: Request) -> AgentsListResponse:
    """列出全部 agent 配置与当前 judge 指针 给前端 SettingsDrawer 初始化用"""
    storage = request.app.state.storage
    records = await storage.list_agents()
    try:
        judge = await storage.get_judge_target()
    except KeyError:
        # 极端情况 settings 集合为空 暴露空字符串 让前端先选一个再设置
        judge = ""
    return AgentsListResponse(
        agents=[_to_view(r) for r in records],
        judge_target=judge,
    )


@router.post("/agents", response_model=AgentView, status_code=201)
async def create_agent(body: CreateAgentRequest, request: Request) -> AgentView:
    """新建 agent  name 由后端生成 形如 agent_<8位hex>  display_name 由用户决定

    新建即热注册 让后续对话立即可见
    """
    storage = request.app.state.storage
    registry = request.app.state.deep_agents
    try:
        record = await storage.create_agent(
            name=None,
            display_name=body.display_name,
            base_url=body.base_url,
            api_key=body.api_key,
            model=body.model,
            prompt=body.prompt,
            available_models=_models_payload(body.available_models) or [],
            provider_type=body.provider_type,
        )
    except ValueError as exc:
        # 极小概率 uuid 碰撞 让前端重试
        raise HTTPException(409, str(exc)) from exc
    # 热替换/新增到 registry  失败回滚 DB 让两边一致
    try:
        await registry.reload(record)
    except Exception as exc:
        _logger.error("create_agent reload 失败 回滚", name=record.name, err=str(exc))
        try:
            await storage.delete_agent(record.name)
        except Exception:
            _logger.exception("回滚 delete_agent 失败 忽略", name=record.name)
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc
    return _to_view(record)


@router.delete("/agents/{name}", status_code=204)
async def delete_agent(name: str, request: Request) -> None:
    """删除 agent  若该 name 是当前 judge target 返回 409  不存在返回 404"""
    storage = request.app.state.storage
    registry = request.app.state.deep_agents
    try:
        await storage.delete_agent(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    registry.unregister(name)


@router.put("/agents/{name}", response_model=UpdateAgentResponse)
async def update_agent(
    name: str, body: UpdateAgentRequest, request: Request
) -> UpdateAgentResponse:
    """局部更新 agent 任意字段 成功后立即热替换 deep_agent 实例

    流程
        1. 取出现有 agent 不存在直接 404
        2. 校验乐观锁与入参合法性 不通过抛 4xx
        3. 写 DB upsert 拿到新 version
        4. 调 registry.reload 触发 build 新实例并 swap
        5. reload 失败则回滚 DB 把旧值写回 返回 500
    """
    storage = request.app.state.storage
    registry = request.app.state.deep_agents

    existing = await storage.get_agent(name)
    if existing is None:
        raise HTTPException(404, f"agent not found: {name}")

    if body.expected_version is not None and body.expected_version != existing.version:
        raise HTTPException(
            409,
            f"version conflict client expected {body.expected_version} server has {existing.version}",
        )

    # 至少改一项  expected_version 不算实质改动
    has_change = any(
        v is not None
        for v in (
            body.display_name,
            body.base_url,
            body.api_key,
            body.model,
            body.available_models,
            body.prompt,
            body.provider_type,
        )
    )
    if not has_change:
        raise HTTPException(
            400,
            "at least one of display_name/base_url/api_key/model/available_models/prompt/provider_type must be provided",
        )

    # 长度兜底 仅校验本次主动传入的字段 防止只改其它字段时被旧值长度卡住
    if body.display_name is not None and not body.display_name.strip():
        raise HTTPException(400, "display_name must not be empty")
    if body.base_url is not None and len(body.base_url.strip()) < 8:
        raise HTTPException(400, "base_url too short")
    if body.api_key is not None and len(body.api_key.strip()) < 4:
        raise HTTPException(400, "api_key too short")
    if body.model is not None and not body.model.strip():
        raise HTTPException(400, "model must not be empty")
    if body.prompt is not None and len(body.prompt.strip()) < 5:
        raise HTTPException(400, "prompt too short")

    new_record = await storage.upsert_agent(
        name,
        display_name=body.display_name,
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        available_models=_models_payload(body.available_models),
        prompt=body.prompt,
        provider_type=body.provider_type,
    )

    # 热替换 失败回滚 DB 重新写回旧值 让 DB 与内存一致
    try:
        await registry.reload(new_record)
        reloaded = True
    except Exception as exc:
        _logger.error("deep_agent reload 失败", name=name, err=str(exc))
        # 回滚 此处 upsert 仍会让 version 再 +1 但内容是旧值
        # 这是有意为之 让前端通过 version 跳变知道发生过失败回滚
        await storage.upsert_agent(
            name,
            display_name=existing.display_name,
            base_url=existing.base_url,
            api_key=existing.api_key,
            model=existing.model,
            available_models=[
                ModelCatalogEntry(model_id=m.model_id, label=m.label)
                for m in existing.available_models
            ],
            prompt=existing.prompt,
            provider_type=existing.provider_type,
        )
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc

    return UpdateAgentResponse(
        name=new_record.name, version=new_record.version, reloaded=reloaded
    )


@router.put("/judge", status_code=204)
async def update_judge(body: UpdateJudgeRequest, request: Request) -> None:
    """更新 judge 指针 target 必须是已知 agent 之一 否则 400"""
    storage = request.app.state.storage
    try:
        await storage.set_judge_target(body.target)
    except KeyError as exc:
        raise HTTPException(400, f"unknown agent: {body.target}") from exc


# ============================================================ 配置历史/回滚
@router.get("/agents/{name}/history", response_model=list[AgentHistoryItem])
async def list_agent_history(
    name: str, request: Request, limit: int = 20
) -> list[AgentHistoryItem]:
    """列出指定 agent 的历史版本 按 version 降序"""
    storage = request.app.state.storage
    if await storage.get_agent(name) is None:
        raise HTTPException(404, f"agent not found: {name}")
    history = await storage.list_agent_history(name, limit=limit)
    out: list[AgentHistoryItem] = []
    for h in history:
        archived_at = h.get("archived_at")
        archived_iso = (
            archived_at.isoformat()
            if hasattr(archived_at, "isoformat")
            else str(archived_at or "")
        )
        out.append(
            AgentHistoryItem(
                name=h["name"],
                display_name=h.get("display_name") or h["name"],
                base_url=h.get("base_url", ""),
                api_key=_mask_key(h.get("api_key", "")),
                model=h.get("model", ""),
                available_models=[
                    ModelView(
                        model_id=str(m.get("model_id", "")),
                        label=str(m.get("label", "")) or str(m.get("model_id", "")),
                    )
                    for m in (h.get("available_models") or [])
                ],
                prompt=h.get("prompt", ""),
                provider_type=h.get("provider_type", "openai_compatible"),
                version=int(h["version"]),
                archived_at=archived_iso,
                archived_reason=str(h.get("archived_reason", "upsert")),
            )
        )
    return out


@router.post("/agents/{name}/revert", response_model=UpdateAgentResponse)
async def revert_agent(
    name: str, body: RevertAgentRequest, request: Request
) -> UpdateAgentResponse:
    """把 agent 回滚到指定历史版本 内部走 upsert 让 version 继续 +1"""
    storage = request.app.state.storage
    registry = request.app.state.deep_agents

    current = await storage.get_agent(name)
    if current is None:
        raise HTTPException(404, f"agent not found: {name}")
    target = await storage.get_agent_history(name, body.target_version)
    if target is None:
        raise HTTPException(
            404, f"history not found: {name} v{body.target_version}"
        )

    # 把历史版本里的字段全量打回当前 agent 形成新版本
    target_models = [
        ModelCatalogEntry(
            model_id=str(m.get("model_id", "")), label=str(m.get("label", ""))
        )
        for m in (target.get("available_models") or [])
    ]
    new_record = await storage.upsert_agent(
        name,
        display_name=str(target.get("display_name") or target.get("name", "")),
        base_url=str(target.get("base_url", current.base_url)),
        api_key=str(target.get("api_key", current.api_key)),
        model=str(target.get("model", current.model)),
        available_models=target_models,
        prompt=str(target.get("prompt", current.prompt)),
        provider_type=str(target.get("provider_type", current.provider_type)),
    )
    try:
        await registry.reload(new_record)
        reloaded = True
    except Exception as exc:
        _logger.error("revert reload 失败 回滚到原值", name=name, err=str(exc))
        await storage.upsert_agent(
            name,
            display_name=current.display_name,
            base_url=current.base_url,
            api_key=current.api_key,
            model=current.model,
            available_models=[
                ModelCatalogEntry(model_id=m.model_id, label=m.label)
                for m in current.available_models
            ],
            prompt=current.prompt,
            provider_type=current.provider_type,
        )
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc
    return UpdateAgentResponse(
        name=new_record.name, version=new_record.version, reloaded=reloaded
    )
