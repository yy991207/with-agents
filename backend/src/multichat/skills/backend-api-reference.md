---
name: backend-api-reference
description: 后端服务 API 接口参考手册 模型调用后端接口时使用此 Skill 作为操作指南
---

# 后端服务 API 接口参考手册

你拥有两个工具可以调用后端服务接口:

- **backend_info**: 查询后端服务运行地址 返回 base_url/host/port
- **http_request**: 发起 HTTP 请求 支持 GET/POST/PUT/DELETE 方法

## 获取后端地址

调用前先用 backend_info 查看后端地址:

```
backend_info()
```

返回: {"base_url": "http://127.0.0.1:8002", "host": "127.0.0.1", "port": 8002}

## 调用方式

http_request 支持两种 url 格式:
- **相对路径(推荐)**: `/api/agents` — 自动拼接后端地址并注入鉴权 更简洁
- **完整 URL**: 用 backend_info 返回的 base_url 拼接 如 `http://127.0.0.1:8002/api/agents`

工具参数:
- `url`: 相对路径或完整 URL
- `method`: GET / POST / PUT / DELETE
- `body`: JSON 字符串 用于 POST/PUT 请求体
- `headers`: 额外请求头 JSON 字符串 可选
- `timeout_s`: 超时秒数 默认 10

鉴权自动注入 无需手动传 cookie 或 token

## 会话管理

### GET /sessions — 列出会话
```
http_request(url="/sessions")
```
响应: 按 updated_at 降序的会话列表 query 参数 `limit` 默认 50

### DELETE /sessions/{session_id} — 删除会话
```
http_request(url="/sessions/{session_id}", method="DELETE")
```
返回 204

### POST /sessions/batch-delete — 批量删除
```
http_request(url="/sessions/batch-delete", method="POST", body='{"session_ids": ["id1", "id2"]}')
```
响应: {"deleted": N, "skipped": N, "errors": [...]}

### GET /history/{session_id} — 查看会话历史
```
http_request(url="/history/{session_id}")
```
响应: {"session": {...}, "rounds": [...]}

### POST /sessions/{session_id}/compact — 压缩会话
```
http_request(url="/sessions/{session_id}/compact", method="POST")
```
响应: {"summary": "...", "used_tokens_before": N, "used_tokens_after": N}

## Agent 管理

### GET /api/agents — 列出 agent
```
http_request(url="/api/agents")
```
响应: {"agents": [...], "compaction_agent_target": "name"}

### POST /api/agents — 创建 agent
```
http_request(
  url="/api/agents",
  method="POST",
  body='{
    "display_name": "助手名称",
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-xxx",
    "model": "gpt-4o",
    "prompt": "你是xxx",
    "provider_type": "openai_compatible",
    "available_models": [{"model_id": "gpt-4o", "label": "GPT-4o", "max_input_tokens": 128000}]
  }'
)
```
响应: AgentView (含自动生成的 name)

### PUT /api/agents/{name} — 更新 agent
```
http_request(
  url="/api/agents/{name}",
  method="PUT",
  body='{"display_name": "新名称", "prompt": "新提示词"}'
)
```
至少改一项 响应: {"name": "...", "version": N, "reloaded": true}

### DELETE /api/agents/{name} — 删除 agent
```
http_request(url="/api/agents/{name}", method="DELETE")
```
返回 204 compaction agent 不允许删

### POST /api/models/discover — 发现可用模型
```
http_request(
  url="/api/models/discover",
  method="POST",
  body='{"base_url": "https://api.openai.com/v1", "api_key": "sk-xxx"}'
)
```
响应: {"models": [{"model_id": "...", "label": "...", "max_input_tokens": N}]}

### POST /api/agents/{name}/models/discover — 用已有 agent 凭据发现模型
```
http_request(url="/api/agents/{name}/models/discover", method="POST", body='{}')
```
body 可选传 base_url/api_key 覆盖 不传则使用 DB 中保存的值

### POST /api/agents/{name}/revert — 回滚到历史版本
```
http_request(url="/api/agents/{name}/revert", method="POST", body='{"target_version": 3}')
```

### 头像上传/获取/删除
```
http_request(url="/api/agents/{name}/avatar")         # 获取
http_request(url="/api/agents/{name}/avatar", method="DELETE")  # 删除
```
上传需要 multipart/form-data http_request 工具暂不支持文件上传

## Skills 管理

### GET /api/skills — 列出 skills
```
http_request(url="/api/skills")
```
响应: {"skills": [{"name": "...", "description": "...", "content": "...", "enabled": true}]}

### POST /api/skills — 创建 skill
```
http_request(
  url="/api/skills",
  method="POST",
  body='{"name": "skill_name", "description": "简介", "content": "完整内容", "enabled": true}'
)
```
同名已存在返回 409

### PUT /api/skills/{name} — 更新 skill
```
http_request(
  url="/api/skills/{name}",
  method="PUT",
  body='{"description": "新简介", "content": "新内容", "enabled": true}'
)
```
不存在返回 404

### PUT /api/skills/{name}/toggle — 启停 skill
```
http_request(url="/api/skills/{name}/toggle", method="PUT", body='{"enabled": false}')
```

### DELETE /api/skills/{name} — 删除 skill
```
http_request(url="/api/skills/{name}", method="DELETE")
```
返回 204

### POST /api/skills/reload — 重载让变更生效
```
http_request(url="/api/skills/reload", method="POST")
```
响应: {"reloaded": N}
创建/更新/删除 skill 后必须调用此接口让变更生效

### GET /api/skills/marketplace — 浏览 skill 市场
```
http_request(url="/api/skills/marketplace")
```

### POST /api/skills/marketplace/import — 从市场导入
```
http_request(url="/api/skills/marketplace/import", method="POST", body='{"names": ["skill1", "skill2"]}')
```
响应: {"results": [{"name": "...", "status": "ok|skipped|error"}]}

## MCP 管理

### GET /api/mcp/servers — 列出 MCP 服务器
```
http_request(url="/api/mcp/servers")
```

### POST /api/mcp/servers — 新增 MCP 服务器
```
http_request(
  url="/api/mcp/servers",
  method="POST",
  body='{"name": "my-mcp", "transport": "stdio", "command": "npx", "args": ["-y", "@some/mcp"], "env": {"KEY": "val"}, "disabled": false}'
)
```

### PUT /api/mcp/servers/{name} — 更新 MCP 服务器
### PUT /api/mcp/servers/{name}/toggle — 启停
### DELETE /api/mcp/servers/{name} — 删除
### POST /api/mcp/reload — 重载让 MCP 变更生效

## 对话核心

### POST /ask — 发起对话
```
http_request(
  url="/ask",
  method="POST",
  body='{"session_id": "xxx", "user_message": "你好", "agents": ["agent_name"], "input_mode": "single"}'
)
```
响应: {"session_id": "...", "task_id": "...", "created_at": "..."}
此后需通过 SSE 流接收回复 此接口仅创建任务

### POST /cancel — 取消对话
```
http_request(url="/cancel", method="POST", body='{"task_id": "xxx", "scope": "global"}')
```
scope: "global" 取消全部 或指定 agent name 只取消单个

### POST /retry_reply — 重答
```
http_request(url="/retry_reply", method="POST", body='{"task_id": "xxx", "agent": "agent_name"}')
```

### POST /select_reply — 多 agent 选答
```
http_request(url="/select_reply", method="POST", body='{"task_id": "xxx", "agent": "选中的agent_name"}')
```

## 错误码

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 204 | 操作成功无返回内容(DELETE) |
| 401 | 未登录/鉴权失败 |
| 404 | 资源不存在 |
| 409 | 冲突(重复创建/状态不允许) |
| 422 | 参数校验失败 |

## 操作规范

1. 调用前先用 backend_info() 确认后端地址 或直接用相对路径调用更方便
2. POST/PUT 请求的 body 必须是合法 JSON 字符串
3. 创建/更新/删除 skill 或 MCP 后必须调用 reload 让变更生效
4. 不要盲目调用接口 先想清楚目的
5. 相对路径 `/api/xxx` 会自动拼接后端地址和鉴权 优先使用这种方式