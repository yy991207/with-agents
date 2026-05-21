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
    """对外的 agent 视图 与 AgentRecord 一致 但 updated_at 序列化为 ISO 字符串"""

    name: str
    model: str
    prompt: str
    version: int
    updated_at: str


class AgentsListResponse(BaseModel):
    """GET /api/agents 响应 agents 数组 + 当前 judge 指针"""

    agents: list[AgentView]
    judge_target: str


class UpdateAgentRequest(BaseModel):
    """PUT /api/agents/{name} 请求体 model/prompt 至少二选一"""

    model: str | None = None
    prompt: str | None = None
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
    if body.model is None and body.prompt is None:
        raise HTTPException(400, "at least one of model/prompt must be provided")

    # 长度兜底 prompt 至少 5 字 model 不能为空字符串
    new_model = body.model if body.model is not None else existing.model
    new_prompt = body.prompt if body.prompt is not None else existing.prompt
    if not new_model.strip():
        raise HTTPException(400, "model must not be empty")
    if len(new_prompt.strip()) < 5:
        raise HTTPException(400, "prompt too short")

    # 写 DB upsert_agent 内部 version +1 同时 updated_at 刷新
    new_record = await storage.upsert_agent(name, new_model, new_prompt)

    # 热替换 失败回滚 DB 重新写回旧值 让 DB 与内存一致
    try:
        await registry.reload(new_record)
        reloaded = True
    except Exception as exc:
        _logger.error("deep_agent reload 失败", name=name, err=str(exc))
        # 回滚 此处 upsert 仍会让 version 再 +1 但 model/prompt 是旧值
        # 这是有意为之 让前端通过 version 跳变知道发生过失败回滚
        await storage.upsert_agent(name, existing.model, existing.prompt)
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
