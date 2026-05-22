"""agents 配置 CRUD API 前端 SettingsDrawer 使用

GET  /api/agents        列出 4 个 agent 配置 + 当前 judge 指针
PUT  /api/agents/{name} 更新某个 agent 的 model 或 prompt 内部触发热替换
PUT  /api/judge         更新 judge 指针(选哪个 agent 当裁判)

设计原则
    - agents 不允许新增/删除 PUT 仅命中已存在 name 否则 404
    - PUT 的写入顺序: DB upsert 成功后再触发 registry.reload
      reload 失败必须回滚 DB 否则会出现 DB 与内存版本不一致
    - 乐观锁可选 expected_version 不传则默认强制覆盖
    - judge 指针不需要热替换 LLM 调用时按名字现取
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["agents"])

_logger = structlog.get_logger(__name__)


class AgentView(BaseModel):
    """对外的 agent 视图 与 AgentRecord 一致 但 updated_at 序列化为 ISO 字符串

    profile_name 暴露给前端展示当前引用哪个 provider profile
    """

    name: str
    profile_name: str
    model: str
    prompt: str
    version: int
    updated_at: str


class AgentsListResponse(BaseModel):
    """GET /api/agents 响应 agents 数组 + 当前 judge 指针"""

    agents: list[AgentView]
    judge_target: str


class UpdateAgentRequest(BaseModel):
    """PUT /api/agents/{name} 请求体 model/prompt/profile_name 至少传一项"""

    model: str | None = None
    prompt: str | None = None
    profile_name: str | None = None
    # 可选乐观锁 不传则强制覆盖 传了则要求与服务端版本一致
    expected_version: int | None = None


class UpdateAgentResponse(BaseModel):
    """PUT /api/agents/{name} 响应 包含新 version 与是否成功热替换"""

    name: str
    version: int
    reloaded: bool


class UpdateJudgeRequest(BaseModel):
    """PUT /api/judge 请求体 target 必须是 4 个已知 agent 名之一"""

    target: str


class AgentHistoryItem(BaseModel):
    """历史版本展示用 view  字段直接来自 agent_history 集合"""

    name: str
    model: str
    prompt: str
    version: int
    archived_at: str
    archived_reason: str


class RevertAgentRequest(BaseModel):
    """POST /api/agents/{name}/revert 请求体 指定要回滚到的历史版本号"""

    target_version: int


@router.get("/agents", response_model=AgentsListResponse)
async def list_agents(request: Request) -> AgentsListResponse:
    """列出全部 agent 配置与当前 judge 指针 给前端 SettingsDrawer 初始化用"""
    storage = request.app.state.storage
    records = await storage.list_agents()
    judge = await storage.get_judge_target()
    return AgentsListResponse(
        agents=[
            AgentView(
                name=r.name,
                profile_name=r.profile_name,
                model=r.model,
                prompt=r.prompt,
                version=r.version,
                updated_at=r.updated_at.isoformat(),
            )
            for r in records
        ],
        judge_target=judge,
    )


@router.put("/agents/{name}", response_model=UpdateAgentResponse)
async def update_agent(
    name: str, body: UpdateAgentRequest, request: Request
) -> UpdateAgentResponse:
    """更新指定 agent 的 model 或 prompt 成功后立即热替换 deep_agent 实例

    流程
        1. 取出现有 agent 不存在直接 404
        2. 校验乐观锁与入参合法性 不通过抛 4xx
        3. 写 DB upsert 拿到新 version
        4. 调 registry.reload 触发 build 新实例并 swap
        5. reload 失败则回滚 DB 把 model/prompt 写回旧值 返回 500
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

    # 至少改一项
    if body.model is None and body.prompt is None and body.profile_name is None:
        raise HTTPException(400, "at least one of model/prompt/profile_name must be provided")

    # 长度兜底 仅校验本次主动传入的字段 防止只改 profile_name 时被旧值长度卡住
    new_model = body.model if body.model is not None else existing.model
    new_prompt = body.prompt if body.prompt is not None else existing.prompt
    if body.model is not None and not new_model.strip():
        raise HTTPException(400, "model must not be empty")
    if body.prompt is not None and len(new_prompt.strip()) < 5:
        raise HTTPException(400, "prompt too short")

    # 校验 profile_name 必须存在  避免运行时 reload 找不到 profile 抛 KeyError
    new_profile_name = body.profile_name if body.profile_name is not None else existing.profile_name
    target_profile = await storage.get_profile(new_profile_name)
    if target_profile is None:
        raise HTTPException(400, f"unknown profile: {new_profile_name}")

    # 写 DB upsert_agent 内部 version +1 同时 updated_at 刷新
    new_record = await storage.upsert_agent(
        name, new_model, new_prompt, profile_name=new_profile_name
    )

    # 热替换 失败回滚 DB 重新写回旧值 让 DB 与内存一致
    try:
        await registry.reload(new_record, target_profile)
        reloaded = True
    except Exception as exc:
        _logger.error("deep_agent reload 失败", name=name, err=str(exc))
        # 回滚 此处 upsert 仍会让 version 再 +1 但 model/prompt 是旧值
        # 这是有意为之 让前端通过 version 跳变知道发生过失败回滚
        await storage.upsert_agent(
            name, existing.model, existing.prompt, profile_name=existing.profile_name
        )
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc

    return UpdateAgentResponse(
        name=new_record.name, version=new_record.version, reloaded=reloaded
    )


@router.put("/judge", status_code=204)
async def update_judge(body: UpdateJudgeRequest, request: Request) -> None:
    """更新 judge 指针 target 必须是 4 个已知 agent 之一 否则 400

    judge 指针不需要热替换 LLM 调用时按名字现取即可
    """
    storage = request.app.state.storage
    existing = await storage.get_agent(body.target)
    if existing is None:
        raise HTTPException(400, f"unknown agent: {body.target}")
    await storage.set_judge_target(body.target)


# ============================================================ 配置历史/回滚
@router.get("/agents/{name}/history", response_model=list[AgentHistoryItem])
async def list_agent_history(
    name: str, request: Request, limit: int = 20
) -> list[AgentHistoryItem]:
    """列出指定 agent 的历史版本 按 version 降序

    错误码:
        404 agent 不存在(连当前都没 必然没历史)
    返回:
        列表项含 name model prompt version archived_at archived_reason
        前端可据此预览每个历史版本的 prompt/model 再选择 revert
    """
    storage = request.app.state.storage
    if await storage.get_agent(name) is None:
        raise HTTPException(404, f"agent not found: {name}")
    history = await storage.list_agent_history(name, limit=limit)
    out: list[AgentHistoryItem] = []
    for h in history:
        archived_at = h.get("archived_at")
        archived_iso = (
            archived_at.isoformat() if hasattr(archived_at, "isoformat") else str(archived_at or "")
        )
        out.append(
            AgentHistoryItem(
                name=h["name"],
                model=h["model"],
                prompt=h["prompt"],
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
    """把 agent 回滚到指定历史版本 内部走 upsert 让 version 继续 +1

    流程:
        1. 取目标历史版本 不存在抛 404
        2. 当前 agent 不存在抛 404
        3. 调 upsert_agent(target.model, target.prompt) 自然把当前值再归档一条
           此处 archived_reason 仍记 upsert  revert 语义由前端通过新 version 的来源推断
        4. 调 registry.reload 失败则回滚 重新写回旧当前值

    返回新 version 与 reloaded 标志 与 update_agent 行为对齐
    """
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

    # 历史版本如有 profile_name 字段则一并恢复 否则保留当前值
    target_profile_name = str(target.get("profile_name", current.profile_name))
    target_profile = await storage.get_profile(target_profile_name)
    if target_profile is None:
        # 历史 profile 已被删 回滚到当前 profile 防止 reload 时找不到
        target_profile_name = current.profile_name
        target_profile = await storage.get_profile(target_profile_name)
    if target_profile is None:
        raise HTTPException(500, f"无法定位 profile {target_profile_name} 请先创建")

    new_record = await storage.upsert_agent(
        name,
        str(target["model"]),
        str(target["prompt"]),
        profile_name=target_profile_name,
    )
    try:
        await registry.reload(new_record, target_profile)
        reloaded = True
    except Exception as exc:
        _logger.error("revert reload 失败 回滚到原值", name=name, err=str(exc))
        # reload 失败 把当前值再写回 让 DB 与内存一致
        # 这里复用 upsert 仍会让 version 再 +1 但 model/prompt 是回滚前的旧值
        await storage.upsert_agent(
            name, current.model, current.prompt, profile_name=current.profile_name
        )
        raise HTTPException(500, f"reload failed reverted: {exc}") from exc
    return UpdateAgentResponse(
        name=new_record.name, version=new_record.version, reloaded=reloaded
    )
