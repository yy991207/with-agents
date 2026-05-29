"""Skills 配置 API  安装/配置/启停 skill

GET    /api/skills                     列出当前用户的 skill
POST   /api/skills                     新增一个 skill（name 不可重复）
POST   /api/skills/reload              重载所有 agent 使 skills 变更生效
GET    /api/skills/marketplace         浏览 Agent Skills Hub 市场中的可用 skill
POST   /api/skills/marketplace/import  从市场一键导入 skill（按名称批量）
PUT    /api/skills/{name}              修改单个 skill
DELETE /api/skills/{name}              删除单个 skill（同步清理 MinIO 文件）
PUT    /api/skills/{name}/toggle       快捷启停开关
POST   /api/skills/{name}/files        上传 skill 文件包（multipart 多文件）
GET    /api/skills/{name}/files        列出 skill 的附带文件列表
GET    /api/skills/{name}/files/{path} 下载 skill 单个文件
DELETE /api/skills/{name}/files/{path} 删除 skill 单个文件

数据隔离: 每个 Skill 严格属于创建用户 owner_user_id 过滤
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import time
from typing import Optional

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth_context import get_current_identity
from ..core.models import RequestIdentity, SkillConfig, SkillFileMeta

router = APIRouter(prefix="/api/skills", tags=["skills"])

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
    files: list[SkillFileMeta] = []


class SkillUpdate(BaseModel):
    """PUT /api/skills/{name} 请求体"""

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
    """市场中单个 skill 的摘要信息"""

    name: str
    description: str = ""


class MarketListResponse(BaseModel):
    """GET /api/skills/marketplace 响应"""

    skills: list[MarketSkillSummary]
    total: int


class MarketImportRequest(BaseModel):
    """POST /api/skills/marketplace/import 请求体"""

    names: list[str]


class MarketImportResult(BaseModel):
    """单个 skill 的导入结果"""

    name: str
    status: str  # "ok" | "skipped" | "error"
    message: str = ""


class MarketImportResponse(BaseModel):
    """POST /api/skills/marketplace/import 响应"""

    results: list[MarketImportResult]


class SkillFileResponse(BaseModel):
    """单个 skill 文件的元数据响应"""

    path: str
    object_key: str
    size: int
    sha256: str


class SkillFilesListResponse(BaseModel):
    """GET /api/skills/{name}/files 响应"""

    files: list[SkillFileResponse]


# 文件相对路径校验规则: 禁止路径穿越、绝对路径、空路径
_PATH_DANGEROUS_RE = re.compile(r"(\.\.)|(^/)|(\\)")


def _validate_relative_path(path: str) -> None:
    """校验文件相对路径合法性 不合法抛 HTTPException"""
    if not path:
        raise HTTPException(status_code=422, detail="文件路径不能为空")
    if _PATH_DANGEROUS_RE.search(path):
        raise HTTPException(status_code=422, detail=f"文件路径不合法 禁止包含 .. / 绝对路径或反斜杠: {path}")


def _skill_to_item(s: SkillConfig) -> SkillItem:
    """SkillConfig → SkillItem"""
    return SkillItem(
        name=s.name,
        description=s.description,
        content=s.content,
        enabled=s.enabled,
        files=s.files,
    )


# ====== 固定路径路由必须在带参数路由之前定义 ======

@router.get("", response_model=SkillsListResponse)
async def list_skills(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> SkillsListResponse:
    """列出当前用户的所有 skill"""
    storage = request.app.state.storage
    skills = await storage.list_skills(owner_user_id=identity.user_id)
    return SkillsListResponse(skills=[_skill_to_item(s) for s in skills])


@router.post("", response_model=SkillItem)
async def create_skill(
    body: SkillItem,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> SkillItem:
    """新增一个 skill  同名抛 409"""
    storage = request.app.state.storage
    existing = await storage.get_skill(body.name, owner_user_id=identity.user_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"skill 已存在 name={body.name}")

    skill = SkillConfig(
        name=body.name,
        description=body.description,
        content=body.content,
        enabled=body.enabled,
        owner_user_id=identity.user_id,
    )
    await storage.upsert_skill(skill, owner_user_id=identity.user_id)
    return body


@router.post("/reload")
async def reload_agents(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> dict:
    """重载当前用户的 agent 实例使 skills 变更生效"""
    registry = request.app.state.deep_agents
    count = await registry.reload_all(owner_user_id=identity.user_id)
    return {"reloaded": count}


@router.put("/{name}", response_model=SkillItem)
async def update_skill(
    name: str,
    body: SkillUpdate,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> SkillItem:
    """修改单个 skill  全量覆盖  不存在抛 404"""
    storage = request.app.state.storage
    existing = await storage.get_skill(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    skill = SkillConfig(
        name=name,
        description=body.description,
        content=body.content,
        enabled=body.enabled,
        owner_user_id=identity.user_id,
    )
    await storage.upsert_skill(skill, owner_user_id=identity.user_id)
    return _skill_to_item(skill)


@router.put("/{name}/toggle", response_model=SkillItem)
async def toggle_skill(
    name: str,
    body: SkillToggle,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> SkillItem:
    """快捷启停开关  只改 enabled 字段"""
    storage = request.app.state.storage
    existing = await storage.get_skill(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    existing.enabled = body.enabled
    await storage.upsert_skill(existing, owner_user_id=identity.user_id)
    return _skill_to_item(existing)


@router.delete("/{name}", status_code=204)
async def delete_skill(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> None:
    """删除单个 skill  同步清理 MinIO 中的附带文件"""
    storage = request.app.state.storage
    object_store = request.app.state.object_store

    # 先获取文件 object_key 列表 用于清理对象存储
    file_keys = await storage.get_skill_file_keys(name, owner_user_id=identity.user_id)
    for key in file_keys:
        try:
            await object_store.delete(key)
        except Exception:
            # 对象存储删除失败不影响 skill 删除 忽略
            pass

    try:
        await storage.delete_skill(name, owner_user_id=identity.user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ====== Skill 文件管理端点 ======

@router.post("/{name}/files", response_model=SkillItem)
async def upload_skill_files(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> SkillItem:
    """上传 skill 文件包  multipart 多文件 + paths 字段指定各文件相对路径

    前端 FormData 格式:
        files: 多个 UploadFile（与 paths 一一对应）
        paths: 多个字符串（每个文件的相对路径 如 SKILL.md / scripts/fetch.py）

    文件存入 MinIO object_key 格式: users/{uid}/skills/{name}/{path}
    保留原始目录结构不改变相对路径 确保模型执行脚本时能正确找到文件
    """
    storage = request.app.state.storage
    object_store = request.app.state.object_store

    # 校验 skill 存在且属于当前用户
    existing = await storage.get_skill(name, owner_user_id=identity.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    # 手动解析 multipart form data
    form = await request.form()
    upload_files = form.getlist("files")
    paths = form.getlist("paths")

    if not upload_files:
        raise HTTPException(status_code=422, detail="未选择文件")

    if len(upload_files) != len(paths):
        raise HTTPException(
            status_code=422,
            detail=f"文件数({len(upload_files)})与路径数({len(paths)})不匹配",
        )

    # 遍历上传文件 存入 MinIO 并构建元数据列表
    new_file_metas: list[SkillFileMeta] = list(existing.files)  # 保留已有文件
    existing_paths = {f.path for f in existing.files}  # 已有文件的路径集合

    for upload_file, rel_path in zip(upload_files, paths):
        rel_path_str = str(rel_path).strip()
        _validate_relative_path(rel_path_str)

        # 同路径文件覆盖: 先删除旧 MinIO 对象
        if rel_path_str in existing_paths:
            old_object_key = f"users/{identity.user_id}/skills/{name}/{rel_path_str}"
            try:
                await object_store.delete(old_object_key)
            except Exception:
                pass
            # 从 new_file_metas 中移除旧记录
            new_file_metas = [f for f in new_file_metas if f.path != rel_path_str]

        # 读取文件内容
        payload = await upload_file.read()
        if not payload:
            raise HTTPException(status_code=422, detail=f"文件为空: {rel_path_str}")

        # 构造 object_key 保留原始相对路径
        object_key = f"users/{identity.user_id}/skills/{name}/{rel_path_str}"

        # 计算 SHA256
        sha256_hash = hashlib.sha256(payload).hexdigest()

        # 写入 MinIO 根据扩展名推断 mime_type
        ext = os.path.splitext(rel_path_str)[1].lower()
        mime_map = {
            ".py": "text/x-python",
            ".md": "text/markdown",
            ".json": "application/json",
            ".txt": "text/plain",
            ".yaml": "text/yaml",
            ".yml": "text/yaml",
            ".csv": "text/csv",
            ".html": "text/html",
            ".js": "text/javascript",
            ".ts": "text/typescript",
            ".sh": "text/x-shellscript",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        await object_store.put_bytes(object_key, payload, mime_type)

        file_meta = SkillFileMeta(
            path=rel_path_str,
            object_key=object_key,
            size=len(payload),
            sha256=sha256_hash,
        )
        new_file_metas.append(file_meta)

    # 更新 SkillConfig 的 files 字段
    existing.files = new_file_metas
    updated = await storage.upsert_skill(existing, owner_user_id=identity.user_id)
    return _skill_to_item(updated)


@router.get("/{name}/files", response_model=SkillFilesListResponse)
async def list_skill_files(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> SkillFilesListResponse:
    """列出 skill 的附带文件列表"""
    storage = request.app.state.storage
    skill = await storage.get_skill(name, owner_user_id=identity.user_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    return SkillFilesListResponse(
        files=[
            SkillFileResponse(path=f.path, object_key=f.object_key, size=f.size, sha256=f.sha256)
            for f in skill.files
        ]
    )


@router.get("/{name}/files/{path:path}")
async def download_skill_file(
    name: str,
    path: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> Response:
    """下载 skill 中的单个文件"""
    storage = request.app.state.storage
    object_store = request.app.state.object_store

    skill = await storage.get_skill(name, owner_user_id=identity.user_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    # 在 files 列表中查找对应文件
    file_meta = None
    for f in skill.files:
        if f.path == path:
            file_meta = f
            break

    if file_meta is None:
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

    # 从 MinIO 读取文件内容
    stored = await object_store.get_bytes(file_meta.object_key)
    return Response(content=stored.content, media_type=stored.mime_type)


@router.delete("/{name}/files/{path:path}", status_code=204)
async def delete_skill_file(
    name: str,
    path: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> None:
    """删除 skill 中的单个文件  同步清理 MinIO"""
    storage = request.app.state.storage
    object_store = request.app.state.object_store

    skill = await storage.get_skill(name, owner_user_id=identity.user_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}")

    # 在 files 列表中查找并移除
    file_meta = None
    remaining_files: list[SkillFileMeta] = []
    for f in skill.files:
        if f.path == path:
            file_meta = f
        else:
            remaining_files.append(f)

    if file_meta is None:
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

    # 删除 MinIO 对象
    try:
        await object_store.delete(file_meta.object_key)
    except Exception:
        pass  # 对象存储删除失败不影响数据库更新

    # 更新 SkillConfig 的 files 列表
    skill.files = remaining_files
    await storage.upsert_skill(skill, owner_user_id=identity.user_id)


# ====== Marketplace 路由（仅读 GitHub 无数据库操作） ======

@router.get("/marketplace", response_model=MarketListResponse)
async def list_marketplace(
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
) -> MarketListResponse:
    """浏览 Agent Skills Hub 市场中的可用 skill"""
    global _market_cache
    now = time.time()
    if _market_cache is None or now - _market_cache["ts"] > 300:
        items = await _fetch_market_listing()
        _market_cache = {"ts": now, "items": items}
    items = _market_cache["items"]
    start = (page - 1) * per_page
    page_items = items[start : start + per_page]
    return MarketListResponse(skills=page_items, total=len(items))


@router.get("/marketplace/{name}")
async def get_marketplace_skill(
    name: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> dict:
    """预览市场中某个 skill 的完整内容"""
    url = f"{_MARKET_RAW_BASE}/{name}/SKILL.md"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.text
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"skill 不存在 name={name}: {e}")

    # 解析 frontmatter
    meta, body = _parse_frontmatter(content)
    return {"name": name, "description": meta.get("description", ""), "content": body}


@router.post("/marketplace/import", response_model=MarketImportResponse)
async def import_marketplace_skills(
    body: MarketImportRequest,
    request: Request,
    identity: RequestIdentity = Depends(get_current_identity),
) -> MarketImportResponse:
    """从市场一键导入 skill 到当前用户"""
    storage = request.app.state.storage
    results: list[MarketImportResult] = []

    for skill_name in body.names:
        # 检查是否已存在
        existing = await storage.get_skill(skill_name, owner_user_id=identity.user_id)
        if existing is not None:
            results.append(MarketImportResult(name=skill_name, status="skipped", message="skill 已存在"))
            continue

        # 从 GitHub 拉取内容
        url = f"{_MARKET_RAW_BASE}/{skill_name}/SKILL.md"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content = resp.text
        except Exception as e:
            results.append(MarketImportResult(name=skill_name, status="error", message=str(e)))
            continue

        meta, body_text = _parse_frontmatter(content)
        skill = SkillConfig(
            name=skill_name,
            description=meta.get("description", ""),
            content=body_text,
            enabled=True,
            owner_user_id=identity.user_id,
        )
        await storage.upsert_skill(skill, owner_user_id=identity.user_id)
        results.append(MarketImportResult(name=skill_name, status="ok"))

    return MarketImportResponse(results=results)


# ====== Marketplace 辅助 ======

async def _fetch_market_listing() -> list[MarketSkillSummary]:
    """从 GitHub API 拉取目录列表"""
    api_url = f"https://api.github.com/repos/{_MARKET_REPO_OWNER}/{_MARKET_REPO_NAME}/contents/{_MARKET_SKILLS_DIR}?ref={_MARKET_BRANCH}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logging.getLogger(__name__).warning("GitHub API 目录列表请求失败")
        return []

    items: list[MarketSkillSummary] = []
    for entry in data:
        if not isinstance(entry, dict) or entry.get("type") != "dir":
            continue
        items.append(MarketSkillSummary(name=entry.get("name", ""), description=""))
    return items


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 SKILL.md 的 YAML frontmatter 返回 (meta, body)"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    body = parts[2].strip()
    return meta, body