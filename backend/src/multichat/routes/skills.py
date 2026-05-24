"""Skills 配置 API  安装/配置/启停 skill

GET    /api/skills                     返回所有 skill 列表
POST   /api/skills                     新增一个 skill（name 不可重复）
POST   /api/skills/reload              重载所有 agent 使 skills 变更生效
GET    /api/skills/marketplace         浏览 Agent Skills Hub 市场中的可用 skill
POST   /api/skills/marketplace/import  从市场一键导入 skill（按名称批量）
GET    /api/skills/marketplace/{name}  预览市场中某个 skill 的完整内容
PUT    /api/skills/{name}              修改单个 skill
DELETE /api/skills/{name}              删除单个 skill
PUT    /api/skills/{name}/toggle       快捷启停开关

数据存储: settings 集合 skills_config 文档 skills 数组
每次操作都是原子化的数组元素变更 不依赖全量覆盖

市场数据来源: GitHub agent-skills-hub/agent-skills-hub 仓库 (790+ skills)
GitHub API 无鉴权限速 60 次/小时 后端对目录列表做了内存缓存(5 分钟 TTL)
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/skills", tags=["skills"])

_SKILLS_CONFIG_DOC_ID = "skills_config"

# Agent Skills Hub GitHub 仓库信息
_MARKET_REPO_OWNER = "agent-skills-hub"
_MARKET_REPO_NAME = "agent-skills-hub"
_MARKET_BRANCH = "main"
_MARKET_SKILLS_DIR = "skills"
_MARKET_RAW_BASE = f"https://raw.githubusercontent.com/{_MARKET_REPO_OWNER}/{_MARKET_REPO_NAME}/{_MARKET_BRANCH}/{_MARKET_SKILLS_DIR}"

# 目录列表缓存 避免频繁调用 GitHub API
_market_cache: Optional[dict] = None  # {"ts": float, "items": list[MarketSkillSummary]}


class SkillItem(BaseModel):
    """单个 skill 的配置 字段对齐 models.SkillConfig"""

    name: str
    description: str = ""
    content: str
    enabled: bool = True


class SkillUpdate(BaseModel):
    """PUT /api/skills/{name} 请求体  全量覆盖除 name 外的字段"""

    description: str = ""
    content: str
    enabled: bool = True


class SkillToggle(BaseModel):
    """PUT /api/skills/{name}/toggle 请求体"""

    enabled: bool


class SkillsListResponse(BaseModel):
    """GET /api/skills 响应"""

    skills: list[SkillItem]


class MarketSkillSummary(BaseModel):
    """市场中单个 skill 的摘要信息 不包含完整 content"""

    name: str
    description: str = ""


class MarketListResponse(BaseModel):
    """GET /api/skills/marketplace 响应"""

    skills: list[MarketSkillSummary]
    total: int


class MarketImportRequest(BaseModel):
    """POST /api/skills/marketplace/import 请求体"""

    names: list[str]  # 要导入的 skill 名称列表


class MarketImportResult(BaseModel):
    """单个 skill 的导入结果"""

    name: str
    status: str  # "ok" | "skipped"（重复）| "error"
    message: str = ""


class MarketImportResponse(BaseModel):
    """POST /api/skills/marketplace/import 响应"""

    results: list[MarketImportResult]


def _skills_collection(storage):
    """返回 settings 集合 封装访问路径"""
    return storage._db["settings"]


async def _ensure_doc(storage):
    """确保 skills_config 文档存在 不存在则创建空文档"""
    col = _skills_collection(storage)
    doc = await col.find_one({"_id": _SKILLS_CONFIG_DOC_ID})
    if doc is None:
        await col.insert_one({"_id": _SKILLS_CONFIG_DOC_ID, "skills": []})
        doc = {"_id": _SKILLS_CONFIG_DOC_ID, "skills": []}
    return doc


# ====== 固定路径路由必须在带参数路由之前定义 ======

@router.get("", response_model=SkillsListResponse)
async def list_skills(request: Request) -> SkillsListResponse:
    """列出所有已安装的 skill"""
    doc = await _ensure_doc(request.app.state.storage)
    skills = doc.get("skills", [])
    if not isinstance(skills, list):
        return SkillsListResponse(skills=[])
    return SkillsListResponse(skills=[SkillItem.model_validate(s) for s in skills])


@router.post("", response_model=SkillItem)
async def create_skill(body: SkillItem, request: Request) -> SkillItem:
    """新增一个 skill  name 不可重复"""
    storage = request.app.state.storage
    col = _skills_collection(storage)
    doc = await _ensure_doc(storage)

    existing = [s for s in doc.get("skills", []) if isinstance(s, dict) and s.get("name") == body.name]
    if existing:
        raise HTTPException(status_code=409, detail=f"skill 已存在 name={body.name}")

    new_item = body.model_dump(mode="json")
    await col.update_one(
        {"_id": _SKILLS_CONFIG_DOC_ID},
        {"$push": {"skills": new_item}},
    )
    return body


@router.post("/reload")
async def reload_agents(request: Request) -> dict:
    """重载所有 agent 使 skills 变更生效

    调用 DeepAgentRegistry.reload_all() 重新从 DB 读取 agent 配置并构建实例
    新构建的实例会带上最新的 skills 内容
    """
    registry = request.app.state.deep_agents
    count = await registry.reload_all()
    return {"reloaded": count}


@router.put("/{name}", response_model=SkillItem)
async def update_skill(name: str, body: SkillUpdate, request: Request) -> SkillItem:
    """修改单个 skill  全量覆盖除 name 外的字段  name 不存在抛 404"""
    storage = request.app.state.storage
    col = _skills_collection(storage)
    doc = await _ensure_doc(storage)

    skills = doc.get("skills", [])
    if not isinstance(skills, list):
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    updated = None
    for i, s in enumerate(skills):
        if isinstance(s, dict) and s.get("name") == name:
            updated = SkillItem(name=name, description=body.description, content=body.content, enabled=body.enabled)
            skills[i] = updated.model_dump(mode="json")
            break
    else:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    await col.update_one(
        {"_id": _SKILLS_CONFIG_DOC_ID},
        {"$set": {"skills": skills}},
    )
    return updated


@router.put("/{name}/toggle", response_model=SkillItem)
async def toggle_skill(name: str, body: SkillToggle, request: Request) -> SkillItem:
    """快捷启停开关  只改 enabled 字段  name 不存在抛 404"""
    storage = request.app.state.storage
    col = _skills_collection(storage)
    doc = await _ensure_doc(storage)

    skills = doc.get("skills", [])
    if not isinstance(skills, list):
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    updated = None
    for i, s in enumerate(skills):
        if isinstance(s, dict) and s.get("name") == name:
            s["enabled"] = body.enabled
            skills[i] = s
            updated = SkillItem(name=name, description=s.get("description", ""), content=s.get("content", ""), enabled=body.enabled)
            break
    else:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    await col.update_one(
        {"_id": _SKILLS_CONFIG_DOC_ID},
        {"$set": {"skills": skills}},
    )
    return updated


@router.delete("/{name}", status_code=204)
async def delete_skill(name: str, request: Request) -> None:
    """删除单个 skill  name 不存在抛 404"""
    storage = request.app.state.storage
    col = _skills_collection(storage)
    doc = await _ensure_doc(storage)

    skills = doc.get("skills", [])
    if not isinstance(skills, list):
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    found = any(isinstance(s, dict) and s.get("name") == name for s in skills)
    if not found:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    await col.update_one(
        {"_id": _SKILLS_CONFIG_DOC_ID},
        {"$pull": {"skills": {"name": name}}},
    )