"""agents 配置 CRUD API 前端 SettingsDrawer 使用

GET    /api/agents                 列出全部 agent 配置 + 当前 compaction agent 指针
POST   /api/agents                 新增 agent  自动生成内部稳定 name
PUT    /api/agents/{name}          局部更新某个 agent 内部触发热替换
DELETE /api/agents/{name}          删除 agent  compaction agent target 不允许删
PUT    /api/compaction-agent       更新 compaction agent 指针(选哪个 agent 做压缩)
GET    /api/agents/{name}/history  列出 agent 历史版本
POST   /api/agents/{name}/revert   回滚到指定历史版本

设计原则
    - agent 数量自由 可加可减 形成"团队管理"
    - name 是内部稳定 ID 不可变 用于 round 引用
    - display_name 给前端展示 可改
    - PUT 的写入顺序: DB upsert 成功后再触发 registry.reload
      reload 失败必须回滚 DB 否则会出现 DB 与内存版本不一致
    - 乐观锁可选 expected_version 不传则默认强制覆盖
    - compaction agent 指针不需要热替换 LLM 调用时按名字现取
    - api_key 接口返回前端时直接返回原文 (前端要求"全量显示"以便用户核对/复制)
      _mask_key 函数保留备用 需要回退时 把 _to_view / list_history_agents 里的
      record.api_key / h.get("api_key", "") 改回 _mask_key(...) 即可
"""

from __future__ import annotations

import uuid

import structlog
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .auth_context import get_current_identity
from ..core.models import AgentAvatarRef, ModelCatalogEntry, RequestIdentity

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
    """agent.available_models 中的一项 对外视图

    max_input_tokens 是该模型的最大输入 token 窗口  会话总 token 超过此值 80% 时
    触发自动摘要压缩  必填字段 不传走默认 200000 兜底
    discover 接口返回时也会补默认 让前端拿到一组合法值  用户保存前可在表单里改
    """

    model_id: str
    label: str = ""
    max_input_tokens: int = Field(default=200000, gt=0)


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
    avatar: AgentAvatarRef | None = None
    # 头像 data URL 形如 data:image/png;base64,xxx 没设过为 None
    avatar_data_url: str | None = None


class AgentsListResponse(BaseModel):
    """GET /api/agents 响应 agents 数组 + 当前 compaction agent 指针"""

    agents: list[AgentView]
    compaction_agent_target: str


class CreateAgentRequest(BaseModel):
    """POST /api/agents 请求体  name 由后端自动生成

    传 copy_key_from 时复用已有 agent 的 key，api_key 可以省略。
    """

    display_name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=8)
    api_key: str | None = Field(default=None, min_length=4)
    model: str = Field(min_length=1)
    prompt: str = Field(min_length=5)
    available_models: list[ModelView] = Field(default_factory=list)
    provider_type: str = "openai_compatible"
    copy_key_from: str | None = None


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


class UpdateCompactionAgentRequest(BaseModel):
    """PUT /api/compaction-agent 请求体 target 必须是已知 agent 内部 name"""

    target: str


class DiscoverModelsRequest(BaseModel):
    """POST /api/models/discover 请求体

    base_url 使用 OpenAI 兼容接口根路径 如 https://api.openai.com/v1。
    后端会统一拼接 /models 并用 api_key 作为 Bearer Token 请求。
    """

    base_url: str = Field(min_length=8)
    api_key: str = Field(min_length=4)
    provider_type: str = "openai_compatible"


class DiscoverAgentModelsRequest(BaseModel):
    """POST /api/agents/{name}/models/discover 请求体

    编辑已有 agent 时前端拿不到明文 api_key，所以 api_key 可不传。
    未传时后端使用数据库里保存的 key，只做模型发现，不直接保存配置。
    """

    base_url: str | None = Field(default=None, min_length=8)
    api_key: str | None = Field(default=None, min_length=4)
    provider_type: str | None = None


class DiscoverModelsResponse(BaseModel):
    """模型发现响应 前端直接用于 Select options 与 agent.available_models"""

    models: list[ModelView]


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
    """AgentRecord -> 前端 AgentView

    注意: 这里 api_key 直接返回原文, 不再走 _mask_key.
    原因: 前端要求在配置面板"全量显示"已保存的 Key, 方便用户核对/复制.
    安全提示: 任何人通过浏览器 DevTools 都能看到原文, 仅适用于单用户/受信网络场景.
    需要回退时, 把下面的 record.api_key 改回 _mask_key(record.api_key) 即可.
    """
    return AgentView(
        name=record.name,
        display_name=record.display_name or record.name,
        provider_type=record.provider_type,
        base_url=record.base_url,
        api_key=record.api_key,
        model=record.model,
        available_models=[
            ModelView(
                model_id=m.model_id,
                label=(m.label or m.model_id),
                max_input_tokens=m.max_input_tokens,
            )
            for m in record.available_models
        ],
        prompt=record.prompt,
        version=record.version,
        updated_at=record.updated_at.isoformat()
        if hasattr(record.updated_at, "isoformat")
        else str(record.updated_at),
        avatar=getattr(record, "avatar", None),
        avatar_data_url=getattr(record, "avatar_data_url", None),
    )


def _models_payload(models: list[ModelView] | None) -> list[ModelCatalogEntry] | None:
    """把对外 ModelView 列表转成内部 ModelCatalogEntry 列表  None 透传

    max_input_tokens 直接透传  ModelCatalogEntry 自带 gt=0 校验
    若前端传 0 或负数会在此处的 model_validate 抛 422 由 FastAPI 兜回去
    """
    if models is None:
        return None
    return [
        ModelCatalogEntry(
            model_id=m.model_id,
            label=m.label,
            max_input_tokens=m.max_input_tokens,
        )
        for m in models
    ]


def _models_url(base_url: str) -> str:
    """按 OpenAI 兼容协议把 base_url 规范化为 models 接口地址"""
    return f"{base_url.strip().rstrip('/')}/models"


def _parse_models_payload(payload: object) -> list[ModelView]:
    """解析 OpenAI 兼容 /models 返回体 只保留可展示的模型 id

    通用协议里模型列表在 data 数组中 每项使用 id 字段。这里不依赖具体厂商字段，
    避免把某个服务商的私有返回结构写死到业务代码里。
    """
    if not isinstance(payload, dict):
        raise ValueError("models response must be a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("models response missing data list")

    models: list[ModelView] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(ModelView(model_id=model_id, label=model_id))
    return models


async def _discover_openai_compatible_models(
    *, base_url: str, api_key: str, provider_type: str
) -> list[ModelView]:
    """调用 OpenAI 兼容 /models 接口并做统一错误映射"""
    if provider_type != "openai_compatible":
        raise HTTPException(400, "only openai_compatible provider is supported")

    url = _models_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        models = _parse_models_payload(resp.json())
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            detail = "API Key 无效或没有模型列表权限"
        elif status == 404:
            detail = "模型列表接口不存在 请确认 Base URL 是否为 OpenAI 兼容根路径"
        elif status == 429:
            detail = "模型列表请求被限流 请稍后再试"
        else:
            detail = f"模型列表请求失败 provider 返回 {status}"
        raise HTTPException(400, detail) from exc
    except httpx.RequestError as exc:
        raise HTTPException(400, f"模型列表请求失败 请检查 Base URL: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(400, f"模型列表响应格式不正确: {exc}") from exc

    if not models:
        raise HTTPException(400, "未获取到可用模型 请检查 Base URL 与 API Key")
    return models


@router.get("/agents", response_model=AgentsListResponse)
async def list_agents(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> AgentsListResponse:
    """列出当前用户可见的 agent 配置与当前 compaction agent 指针 给前端 SettingsDrawer 初始化用"""
    storage = request.app.state.storage
    records = await storage.list_agents(owner_user_id=identity.user_id)
    try:
        compaction_agent = await storage.get_compaction_agent_target()
    except KeyError:
        # 极端情况 settings 集合为空 暴露空字符串 让前端先选一个再设置
        compaction_agent = ""
    return AgentsListResponse(
        agents=[_to_view(r) for r in records],
        compaction_agent_target=compaction_agent,
    )


@router.post("/models/discover", response_model=DiscoverModelsResponse)
async def discover_models(body: DiscoverModelsRequest) -> DiscoverModelsResponse:
    """按 OpenAI 兼容协议动态获取可用模型列表

    这里由后端代理请求第三方 provider，避免前端直接把 api_key 暴露给浏览器跨域请求。
    当前只支持 openai_compatible，后续如果要扩展其它协议再单独加 provider_type 分支。
    """
    models = await _discover_openai_compatible_models(
        base_url=body.base_url,
        api_key=body.api_key,
        provider_type=body.provider_type,
    )
    return DiscoverModelsResponse(models=models)


@router.post("/agents/{name}/models/discover", response_model=DiscoverModelsResponse)
async def discover_agent_models(
    name: str,
    body: DiscoverAgentModelsRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> DiscoverModelsResponse:
    """用已有 agent 的凭证动态获取模型列表

    如果前端刚改了 base_url 或 api_key，可在 body 中带上临时值；否则使用 DB 中当前值。
    这个接口只读取模型，不修改 agent，避免"查询模型"动作隐式保存配置。
    """
    storage = request.app.state.storage
    existing = await storage.get_agent(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(404, f"agent not found: {name}")

    base_url = body.base_url or existing.base_url
    api_key = body.api_key or existing.api_key
    provider_type = body.provider_type or existing.provider_type
    models = await _discover_openai_compatible_models(
        base_url=base_url,
        api_key=api_key,
        provider_type=provider_type,
    )
    return DiscoverModelsResponse(models=models)


@router.post("/agents", response_model=AgentView, status_code=201)
async def create_agent(
    body: CreateAgentRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> AgentView:
    """新建 agent  name 由后端生成 形如 agent_<8位hex>  display_name 由用户决定

    新建即热注册 让后续对话立即可见
    传 copy_key_from 时从已有 agent 复制 api_key
    """
    storage = request.app.state.storage
    registry = request.app.state.deep_agents

    # 从已有 agent 复制 key
    api_key = body.api_key
    if body.copy_key_from:
        existing = await storage.get_agent(body.copy_key_from, owner_user_id=identity.user_id)
        if existing is None:
            raise HTTPException(400, f"copy_key_from agent not found: {body.copy_key_from}")
        api_key = existing.api_key
    if not api_key:
        raise HTTPException(400, "api_key is required when copy_key_from is not set")

    try:
        record = await storage.create_agent(
            name=None,
            display_name=body.display_name,
            base_url=body.base_url,
            api_key=api_key,
            model=body.model,
            prompt=body.prompt,
            available_models=_models_payload(body.available_models) or [],
            provider_type=body.provider_type,
            owner_user_id=identity.user_id,
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
            await storage.delete_agent(record.name, owner_user_id=identity.user_id)
        except Exception:
            _logger.exception("回滚 delete_agent 失败 忽略", name=record.name)
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc
    return _to_view(record)


@router.delete("/agents/{name}", status_code=204)
async def delete_agent(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> None:
    """删除 agent  若该 name 是当前 compaction agent target 返回 409  不存在返回 404"""
    storage = request.app.state.storage
    registry = request.app.state.deep_agents
    try:
        await storage.delete_agent(name, owner_user_id=identity.user_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    registry.unregister(name)


@router.put("/agents/{name}", response_model=UpdateAgentResponse)
async def update_agent(
    name: str,
    body: UpdateAgentRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
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

    existing = await storage.get_agent(name, owner_user_id=identity.user_id)
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
        owner_user_id=identity.user_id,
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
                ModelCatalogEntry(
                    model_id=m.model_id,
                    label=m.label,
                    max_input_tokens=m.max_input_tokens,
                )
                for m in existing.available_models
            ],
            prompt=existing.prompt,
            provider_type=existing.provider_type,
            owner_user_id=identity.user_id,
        )
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc

    return UpdateAgentResponse(
        name=new_record.name, version=new_record.version, reloaded=reloaded
    )


@router.put("/compaction-agent", status_code=204)
async def update_compaction_agent(body: UpdateCompactionAgentRequest, request: Request) -> None:
    """更新 compaction agent 指针 target 必须是已知 agent 之一 否则 400"""
    storage = request.app.state.storage
    try:
        await storage.set_compaction_agent_target(body.target)
    except KeyError as exc:
        raise HTTPException(400, f"unknown agent: {body.target}") from exc


# ============================================================ 配置历史/回滚
@router.get("/agents/{name}/history", response_model=list[AgentHistoryItem])
async def list_agent_history(
    name: str,
    request: Request,
    limit: int = 20,
    identity: RequestIdentity = Depends(get_current_identity),
) -> list[AgentHistoryItem]:
    """列出指定 agent 的历史版本 按 version 降序"""
    storage = request.app.state.storage
    if await storage.get_agent(name, owner_user_id=identity.user_id) is None:
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
                # 历史记录的 api_key 也按"全量显示"策略返回原文  与 _to_view 保持一致
                # 回退方式: 改回 _mask_key(h.get("api_key", ""))
                api_key=h.get("api_key", ""),
                model=h.get("model", ""),
                available_models=[
                    ModelView(
                        model_id=str(m.get("model_id", "")),
                        label=str(m.get("label", "")) or str(m.get("model_id", "")),
                        max_input_tokens=int(m.get("max_input_tokens") or 200000),
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
    name: str,
    body: RevertAgentRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> UpdateAgentResponse:
    """把 agent 回滚到指定历史版本 内部走 upsert 让 version 继续 +1"""
    storage = request.app.state.storage
    registry = request.app.state.deep_agents

    current = await storage.get_agent(name, owner_user_id=identity.user_id)
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
            model_id=str(m.get("model_id", "")),
            label=str(m.get("label", "")),
            max_input_tokens=int(m.get("max_input_tokens") or 200000),
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
        owner_user_id=identity.user_id,
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
                ModelCatalogEntry(
                    model_id=m.model_id,
                    label=m.label,
                    max_input_tokens=m.max_input_tokens,
                )
                for m in current.available_models
            ],
            prompt=current.prompt,
            provider_type=current.provider_type,
            owner_user_id=identity.user_id,
        )
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc
    return UpdateAgentResponse(
        name=new_record.name, version=new_record.version, reloaded=reloaded
    )


# ============================================================ 头像上传 / 删除
# 设计要点
#   - 头像直接以 data URL 形式 base64 内联存进 agent doc 不引入对象存储
#   - 体积上限 2MB 防止把 mongo doc 撑过 16MB 硬限制
#   - 仅接受 png/jpeg/webp/gif 四种主流格式 其它一律 415
#   - 头像变更不触发 deep_agent 热替换 它不影响 LLM 行为
#   - 头像变更不进 agent_history  它是展示数据  和 prompt/model 不同
_AVATAR_MAX_BYTES = 2 * 1024 * 1024
_ALLOWED_IMAGE_MIMES: dict[str, str] = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
}


@router.post("/agents/{name}/avatar", response_model=AgentView)
async def upload_agent_avatar(
    name: str,
    request: Request,
    file: UploadFile = File(...),
    identity: RequestIdentity = Depends(get_current_identity),
) -> AgentView:
    """上传 agent 头像  返回更新后的 AgentView

    错误码
        - 404 agent 不存在
        - 413 图片超过 2MB
        - 415 mime 不在白名单
    """
    storage = request.app.state.storage
    existing = await storage.get_agent(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(404, f"agent not found: {name}")

    # 校验 mime  浏览器有时给的 mime 不规范  做一层归一化
    raw_mime = (file.content_type or "").lower().split(";")[0].strip()
    canon_mime = _ALLOWED_IMAGE_MIMES.get(raw_mime)
    if canon_mime is None:
        raise HTTPException(
            415,
            f"unsupported image type: {raw_mime or 'unknown'} 仅支持 png/jpeg/webp/gif",
        )

    # 读全部字节做大小校验  超过上限直接 413
    payload = await file.read()
    if len(payload) > _AVATAR_MAX_BYTES:
        raise HTTPException(
            413,
            f"image too large: {len(payload)} bytes 上限 {_AVATAR_MAX_BYTES} bytes (2MB)",
        )
    if not payload:
        raise HTTPException(400, "empty file")

    object_store = request.app.state.object_store
    ext = canon_mime.split("/")[-1]
    object_key = (
        f"users/{existing.owner_user_id}/"
        f"avatars/{name}/{uuid.uuid4().hex}.{ext}"
    )
    avatar_meta = await object_store.put_bytes(object_key, payload, canon_mime)

    try:
        record = await storage.set_agent_avatar(name, avatar_meta)
    except KeyError as exc:
        # 极小概率上面查到了 但写时 agent 已被删  当 404 兜底
        raise HTTPException(404, str(exc)) from exc
    _logger.info("agent avatar uploaded", name=name, mime=canon_mime, bytes=len(payload))
    return _to_view(record)


@router.get("/agents/{name}/avatar")
async def get_agent_avatar(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> Response:
    """返回 agent 头像原始二进制 给前端 img src 直接展示"""
    storage = request.app.state.storage
    object_store = request.app.state.object_store
    record = await storage.get_agent(name, owner_user_id=identity.user_id)
    if record is None:
        raise HTTPException(404, f"agent not found: {name}")
    if record.avatar is None:
        raise HTTPException(404, f"avatar not found: {name}")

    try:
        stored = await object_store.get_bytes(record.avatar.object_key)
    except KeyError as exc:
        raise HTTPException(404, f"avatar object not found: {name}") from exc
    return Response(content=stored.content, media_type=stored.mime_type)


@router.delete("/agents/{name}/avatar", response_model=AgentView)
async def delete_agent_avatar(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> AgentView:
    """清除 agent 头像  返回更新后的 AgentView (avatar_data_url=None)"""
    storage = request.app.state.storage
    object_store = request.app.state.object_store
    existing = await storage.get_agent(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(404, f"agent not found: {name}")
    if existing.avatar is not None:
        try:
            await object_store.delete(existing.avatar.object_key)
        except KeyError:
            # 对象已不存在时忽略 继续删 mongo 引用
            pass
    try:
        record = await storage.clear_agent_avatar(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    _logger.info("agent avatar cleared", name=name)
    return _to_view(record)
