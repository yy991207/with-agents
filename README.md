# multi-chat

## 项目简介

multi-chat 是一个面向 4 个大模型并行协作的对话服务，从 autogen 仓库的 `agentchat_fastapi` sample 演化独立而来。后端使用 FastAPI 单端口部署，前端 React 18 + antd 5 + Vite，对话状态持久化到 MongoDB。

核心交互叫 think-then-choose：用户提问后，4 个模型先并行产出 50 字以内的发言意愿（think 阶段），用户在前端看完 4 张 think 卡片后再决定让谁来正式回答（decide 阶段），仅被选中的那个模型进入完整流式回答（reply 阶段）。这样省 token、可控、并且把"选谁说话"的主动权交还给用户。同时支持 `@AgentName` 直呼跳过 think 阶段。

## 技术栈

| 模块 | 选型 |
|---|---|
| 后端框架 | FastAPI + uvicorn（单进程单 event loop，端口 8002） |
| LLM 编排 | deepagents 0.6.3 + langchain 1.3.1 + langchain-openai 1.2.1 + langgraph 1.2.0 |
| 前端框架 | React 18 + antd 5 + TypeScript + Vite 5 |
| SSE 客户端 | @microsoft/fetch-event-source |
| 数据存储 | MongoDB 7（本地或 docker compose 起） |
| LLM 凭证 | dashscope OpenAI 兼容模式（4 个 agent + 1 个 judge 复用） |
| Python | 3.11，conda 环境名 `multi-chat` |

## 快速开始

### 先决条件

- Python 3.11 已安装，建议用 conda 隔离：环境名 `multi-chat`
- Node 18+ 与 npm 9+
- MongoDB 7：本地原生安装或用本仓库的 `docker-compose.yml` 起一个
- 一份 dashscope API key（写到 `config.yaml` 的 `key` 字段）

### 后端启动

```bash
conda activate multi-chat
cd backend
pip install -e ".[dev]"
uvicorn multichat.main:create_app --factory --reload --port 8002
```

后端启动前需保证 MongoDB 已运行（`./run.sh mongo` 或本机已装 mongod）。

### 前端启动（开发模式）

```bash
cd web
npm install
npm run dev
```

然后访问 http://localhost:5173。Vite dev server 会把 `/ask`、`/decide`、`/cancel`、`/sse`、`/history` 等接口反向代理到后端 8002，前端永远 same-origin 调用，免 CORS。

## 配置

仓库内只有 `config.example.yaml` 模板，真实配置 `config.yaml` 已被 `.gitignore` 排除：

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入真实 dashscope key
```

`config.yaml` 含 4 个 agent 的 model + prompt、judge 选择、MongoDB 连接、运行期参数。每个段都加了中文注释，改一个 agent 不需要跨段对照。

## 一键启动

仓库根目录提供 `run.sh`，常用子命令：

| 命令 | 说明 |
|---|---|
| `./run.sh mongo` | 用 docker compose 起本地 MongoDB |
| `./run.sh dev` | 双进程开发模式：vite dev server + uvicorn --reload |
| `./run.sh build` | 前端构建产 web/dist |
| `./run.sh start` | 生产模式后台起 uvicorn 单端口（需先 build） |
| `./run.sh stop` / `restart` | 停 / 重启后端 |
| `./run.sh status` / `log` | 查看状态 / 跟踪日志 |

环境检查脚本：`./scripts/check_env.sh`，启动前用它快速验证 Python、Node、MongoDB、config.yaml 都到位。

## 项目结构

```
multi-chat/
├── backend/                    # FastAPI 后端 由并行任务实装
│   ├── pyproject.toml
│   └── src/multichat/
├── web/                        # React 前端 由并行任务实装
│   ├── package.json
│   └── src/
├── docs/specs/                 # 设计文档 think-then-choose spec 等
├── doc/                        # 工作记录 按日期归档
├── scripts/                    # 辅助脚本 check_env 等
├── docker/mongo-data/          # 本地 mongo 数据卷 不入仓
├── config.example.yaml         # 配置模板
├── docker-compose.yml          # 仅 MongoDB 容器
├── run.sh                      # 一键启动脚本
├── .gitignore
└── README.md
```

## 状态

- M0 验证：已完成（2026-05-21），4 个 dashscope 模型 think+reply 全 PASS，详见 `docs/specs/2026-05-20-think-then-choose-design.md` §0
- M1 基础设施：进行中（MongoDB 连通 + storage.py + config.py 实装）

## 文档

- 设计文档：`docs/specs/2026-05-20-think-then-choose-design.md`
- 开发工作记录：`doc/工作记录 - YYYY 年 MMDD.md`
