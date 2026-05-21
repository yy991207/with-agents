# 多模型协同对话 - think-then-choose 模式设计文档

- **日期**:2026-05-20
- **作者**:jinwyang
- **状态**:已确认 待实施
- **范围**:`backend/src/multichat/` 多模型对话服务

---

## 0. M0 验证结论(2026-05-21)

在动手实施之前 先做了一次最小验证 用 `/Users/yang/multi-chat-verify/verify_deepagents.py`
对 4 个 dashscope 模型跑了 deepagents 0.6.3 的最小用例

| Agent | Model | think (50 字快答) | reply (规划+工具) |
|---|---|:-:|:-:|
| DeepSeek | deepseek-v4-pro | PASS 3.3s 38 字 | PASS 11.4s |
| GLM | glm-5.1 | PASS 3.9s 33 字 | PASS 10.3s |
| Kimi | kimi-k2.6 | PASS 1.4s 35 字 | PASS 5.1s |
| Qwen | qwen3.6-plus | PASS 1.9s 38 字 | PASS 12.9s |

关键结论:
- 4 个模型 function calling 全部可用 都能在 deepagents 虚拟文件系统中正确完成
  write_file 与 read_file 调用
- 用同一个 deepagents 实例 仅靠 system_prompt 约束就能跑成 50 字快答模式
  不需要额外旁路 这验证了 8 实例预创建方案中"think 用轻量 deep agent"的可行性
- deepagents 0.6.x 虚拟文件系统 key 带 `/` 前缀 值是 dict 含 content/encoding 等字段
  做 wrapper 时要照这个 schema 访问
- 工作版本组合: deepagents 0.6.3 + langchain 1.3.1 + langchain-openai 1.2.1
  + langgraph 1.2.0 + Python 3.11 已固化到 backend/pyproject.toml
- conda 环境 `multi-chat` 已建好 全程使用此环境

未来如果 deepagents 升大版本 这次验证结论需要重做

---

## 1. 背景与目标

### 1.1 现状

(本节为历史背景 描述脱离前的 autogen 仓库 `agentchat_fastapi` sample 现状)
旧服务采用 autogen 的 `RoundRobinGroupChat`,4 个 LLM(DeepSeek、GLM、Kimi、Qwen)按固定顺序轮流发言。问题:

- **死板**:每轮 4 个 agent 必发言一次,无论是否有话可说
- **失控**:用户不能选择"我只想听 A 和 B 的回答"
- **无差异化**:agent 之间没有任何区分机制,体验等同于"4 倍长度的单 agent 回答"

### 1.2 目标

把控制权交还给用户,做一个**think-then-choose**模式的多模型对话:

1. 用户提问后,4 个模型**并行做 50 字 think**,展示各自的回答理由
2. 用户看 think 卡片后,**手动决策**让谁来正式回答(或让 AI 帮选)
3. 仅被选中的 agent 进入正式回答流程,**其他 3 个 agent 不消耗正文 token**
4. 支持 **@ 直呼**:用户 @ 单个 agent 时跳过 think 阶段,直接由该 agent 回答

### 1.3 非目标

明确**不做**的事(避免设计膨胀):

- 不做 LLM 之间的群聊互动(抢占锁、互相 @、补刀等)
- 不做多 @ 同时呼叫
- 不做用户多选 agent 同时回答
- 不做 think 阶段的硬业务超时(只用 HTTP 层的网络超时)
- 不引入消息队列中间件(Redis、Kafka 等)
- 不引入 Celery 等任务队列

---

## 2. 核心概念

### 2.1 名词表

| 术语 | 含义 |
|---|---|
| **Round** | 一轮对话:用户提问 → think → 决策 → agent 回答 |
| **Think** | 50 字以内的发言理由,所有未被 @ 排除的 agent 并行产出 |
| **Decision** | 用户(或 AI)从 think 结果中选出"由谁来正式回答" |
| **Reply** | 被选中 agent 的正式回答(完整长度,流式输出) |
| **Session** | 一组连续 round 的集合,对应一个对话上下文,有唯一 `session_id` |
| **Task** | 服务端为每次用户提问创建的可恢复任务,有唯一 `task_id`,状态存 MongoDB |

### 2.2 一轮对话的状态机

```
用户提问
  │
  ├─ 含 @AgentName ──→ [Reply 阶段] ──→ 完成
  │
  └─ 不含 @ ──→ [Think 阶段(并行 4 路)]
                      │
                      ├─ 任一 agent 失败/取消/完成 → 单卡更新
                      │
                      └─ 全部收敛(完成 + 失败 + 取消 = 4)
                              │
                              ↓
                      [Decision 阶段:决策卡可交互]
                              │
                              ├─ 用户选 agent
                              ├─ 用户点"重新 think" → 回到 Think
                              └─ 用户点"帮我选" → 用裁判 LLM 选一个
                                      │
                                      ↓
                              [Reply 阶段:被选中 agent 流式回答]
                                      │
                                      ↓
                                    完成
```

### 2.3 任务状态(MongoDB 中存的字段)

每个 task 有一个完整生命周期状态,服务端为之负责:

```
PENDING        刚创建
THINKING       4 个 agent 并行 think 中
THINK_DONE     全部 think 收敛 等用户决策
DECIDED        用户已决策 进入 reply
REPLYING       被选中 agent 正式回答中
DONE           本轮完成
CANCELLED      用户全局停止
```

---

## 3. 整体架构

### 3.1 分层架构图

```
┌──────────────────────────────────────────────────────────────┐
│           浏览器 SPA(React 18 + antd 5 + TypeScript)         │
│  ─────────────────────────────────────────────────────────   │
│  Vite 构建产物 由 FastAPI StaticFiles 挂载                    │
│   - 输入框 + 全局 Stop 按钮(图 1)                            │
│   - think 卡片 4 列并行渲染 单卡可暂停/重试                  │
│   - 决策卡:4 个 agent + "重新 think" + "帮我选" + 取消      │
│   - 完整时间线视图(E2)折叠 chip 展开历史 think              │
│   - SSE 集成:@microsoft/fetch-event-source                  │
│   - 状态管理:useReducer + Context(无额外依赖)              │
└──────┬─────────────────┬───────────────────┬─────────────────┘
       │ POST 用户消息   │ SSE 接收流        │ POST 控制指令
       │                 │ (think/reply 流)  │ (cancel/decide)
       ▼                 ▼                   ▼
┌──────────────────────────────────────────────────────────────┐
│              FastAPI 服务(单进程 单 event loop)              │
│  ─────────────────────────────────────────────────────────   │
│                                                              │
│  ┌──────────────┐    ┌────────────────┐  ┌─────────────────┐ │
│  │   Routes     │    │  TaskManager   │  │ MongoStorage    │ │
│  │              │    │                │  │                 │ │
│  │ POST /ask    │───▶│ 创建/控制 task │◀─┤ task / round /  │ │
│  │ POST /decide │    │ 编排 think →   │  │ session 持久化  │ │
│  │ POST /cancel │    │ decide → reply │  │ MongoDB 客户端  │ │
│  │ GET  /sse    │◀───│ 推送事件到 SSE │  └─────────────────┘ │
│  │ GET  /history│    └────────┬───────┘                      │
│  └──────────────┘             │                              │
│                               ▼                              │
│           ┌───────────────────────────────────┐              │
│           │       AgentRunner(纯函数)         │              │
│           │  - run_think(agent, msgs) → str   │              │
│           │  - run_reply(agent, msgs) → stream│              │
│           │  - run_judge(thinks) → agent_name │              │
│           └───────────┬───────────────────────┘              │
│                       │                                      │
│                       ▼                                      │
│           ┌───────────────────────────────────┐              │
│           │     OpenAI Chat Client(autogen) │              │
│           │     按 config.yaml 创建 4 + 1    │              │
│           │     (4 agent + 1 judge 复用       │              │
│           │      4 个之一 见 6.3)             │              │
│           └───────────────────────────────────┘              │
└──────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   MongoDB 实例      │
                    │   db: multi_chat    │
                    │   collections:      │
                    │   - sessions        │
                    │   - rounds          │
                    │   - agents (含 4    │
                    │       agent + judge │
                    │       指针 见 §7.4) │
                    │   - tasks(可选 用于 │
                    │        恢复中断 task│
                    └─────────────────────┘
```

### 3.2 关键设计决策

**决策 1:无中心调度器,但有任务编排器**

之前讨论的"抢占锁/事件总线"方案彻底放弃。现在每轮的流程是**确定性顺序**(think → decide → reply),不需要 actor model。`TaskManager` 是个**编排器**(orchestrator),按状态机推进任务,不是"指派谁说话"的调度器——因为没有"谁说话"的不确定性,用户在决定。

**决策 2:每轮一个独立 task,状态全在 MongoDB**

每次用户提问创建一个新 task,task 的所有阶段(think 进度、生成内容、决策结果、reply 进度)持续写 MongoDB。**这是抗刷新的基础**——前端断线重连只需 `task_id`,服务端从 MongoDB 续推。

**决策 3:SSE + HTTP/2 通信,不用 WebSocket**

理由:
- think-then-choose 模式下,client → server 都是请求-响应类(发消息、做决策、取消),用普通 POST 即可
- server → client 才是流式(think 文本流、reply 文本流),正好是 SSE 的强项
- SSE 比 WebSocket 简单:无需保持双向连接、无心跳协议、断线重连用 EventSource 内置机制
- HTTP/2 下 SSE 不受 6 连接限制

**决策 4:think 用非流式,reply 用流式**

- think 仅 50 字,等齐返回再推前端 UI 刷新成本低,**用非流式**(LLM API 直接返回完整文本),实现简单
- reply 字数不可控,**必须流式**保证打字机体验

**决策 5:超时只在 HTTP 层**

按你确认的:不在业务层加硬超时,只在 `OpenAIChatCompletionClient` 的底层 `httpx.AsyncClient` 上配置网络 timeout。超时表现为 LLM 调用抛异常,业务层捕获后把对应 think 卡片标记为"失败"。

**决策 6:服务端任务可恢复,但不做主动续推**

服务端为每个 task 持续写 MongoDB,但**不主动**重连推送。前端刷新后:
1. 用 `session_id` 拉历史(已完成 task 的快照)
2. 检测到最新 task 状态非 DONE/CANCELLED → 重新建 SSE 拉那个 task 的当前状态 + 后续增量
3. SSE handler 读 MongoDB 拼当前快照,发完一帧"snapshot"事件,然后挂接当前正在跑的 task 的事件流(in-memory 广播)继续推增量

这样实现简单,且**单进程多 worker 不冲突**——每个 task 只在创建它的那个进程里跑,前端刷新后必须重连到同一个进程才能续推。**不解决跨进程恢复**(需要那种就要引入 task queue 服务,过度工程)。

**决策 7:agents 配置存 MongoDB 不存 yaml**

之前的设计把 4 个 agent 的 model 和 prompt 写在 `config.yaml` 里 前端只能看不能改。本轮收到产品需求 想让用户在前端 SettingsDrawer 直接调整 prompt 和切换模型 实时生效 因此把 agents 配置整体下沉到 MongoDB。

- **动机**:前端要能直接改 model 和 prompt 不用改文件 不用重启服务
- **落地**:`config.yaml` 的 `agents` 段和 `judge` 段降级为**首次启动种子默认值** 启动时若 DB 中 agents collection 为空则从 yaml 注入 之后运行时一律以 DB 为准 yaml 不再被读取
- **热替换**:前端 PUT 后 后端在 storage 层把新文档写进 DB(version + 1) 然后**原子重建**该 agent 对应的 think + reply 两个 deep_agent 实例 加锁 swap 进 `app.state.deep_agents` 字典 整个过程不重启服务
- **代价**:启动顺序多一步 seed 必须 `连 mongo → ensure_indexes → seed_from_yaml → build deep_agents` DB 不可达直接拒绝服务(早暴露) 排查问题时配置真实值要去查 DB 而不是源码

并发安全的边界(具体流程见 §5.8):replace 瞬间正在跑的 reply 用的是旧实例(已 closure 引用) 不会被打断 之后的新 reply 才用新实例 不做"取消已跑 reply 让它用新版"的强一致语义。

---

## 4. 组件清单与文件组织

```
multi-chat/
├── config.yaml                            # 项目根配置 增加 judge agent 字段 .gitignore 排除
├── config.example.yaml                    # 配置模板 入仓
├── run.sh                                 # 一键启动脚本 含 mongo / dev / build / start 子命令
├── docker-compose.yml                     # 仅起本地 mongo
├── backend/
│   ├── pyproject.toml                     # 含 deepagents / langchain / motor 等依赖
│   └── src/multichat/
│       ├── __init__.py
│       ├── main.py                        # 入口 create_app() 工厂 不再用 RoundRobinGroupChat
│       ├── core/
│       │   ├── __init__.py
│       │   ├── models.py                  # Pydantic 数据模型 Task Round Think Reply 等
│       │   ├── task_manager.py            # 任务编排核心 状态机推进
│       │   ├── agent_runner.py            # 纯函数封装 LLM 调用 think reply judge
│       │   ├── mention_parser.py          # @ 解析(沿用之前讨论的)
│       │   ├── storage.py                 # MongoStorage 抽象接口 + Mongo 实现
│       │   └── sse.py                     # SSE 事件推送辅助
│       └── routes/
│           ├── __init__.py
│           ├── ask.py                     # POST /ask 创建任务
│           ├── decide.py                  # POST /decide 用户决策
│           ├── cancel.py                  # POST /cancel 全局或单卡取消
│           ├── stream.py                  # GET /sse/{task_id} SSE 流
│           └── history.py                 # GET /history/{session_id}
├── web/                                   # 前端独立工程(React + antd + Vite + TS)
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html                         # Vite 入口模板
│   ├── src/
│   │   ├── main.tsx                       # 应用入口 ConfigProvider 主题
│   │   ├── App.tsx                        # 顶层布局
│   │   ├── api/                           # 后端 HTTP/SSE 客户端
│   │   │   ├── http.ts                    # axios/fetch 封装 ask/decide/cancel
│   │   │   └── sse.ts                     # @microsoft/fetch-event-source 封装
│   │   ├── state/                         # useReducer + Context
│   │   │   ├── ChatContext.tsx
│   │   │   ├── reducer.ts
│   │   │   └── types.ts
│   │   ├── components/
│   │   │   ├── ChatInput.tsx              # 输入框 + 全局 Stop 按钮
│   │   │   ├── Timeline.tsx               # E2 完整时间线
│   │   │   ├── UserBubble.tsx             # 用户气泡
│   │   │   ├── ThinkPanel.tsx             # 4 列 think 卡片容器
│   │   │   ├── ThinkCard.tsx              # 单个 think 卡片(含暂停/重试)
│   │   │   ├── ThinkCardChip.tsx          # 折叠态 chip
│   │   │   ├── DecisionCard.tsx           # 决策卡(4 agent + 重 think + 帮我选)
│   │   │   ├── ReplyBubble.tsx            # 正式回答气泡(流式)
│   │   │   └── SessionDrawer.tsx          # 侧边抽屉:历史 session 列表
│   │   ├── hooks/
│   │   │   ├── useChatTask.ts             # 封装 ask → SSE → reducer dispatch
│   │   │   └── useSession.ts              # session 切换/历史加载
│   │   ├── theme/
│   │   │   └── tokens.ts                  # antd ConfigProvider tokens
│   │   └── styles/
│   │       └── global.css
│   ├── public/                            # 静态资产 直接复制到 dist
│   └── dist/                              # build 产物 不入 git
├── docs/specs/                            # 设计文档 含本文件
├── doc/                                   # 工作记录 按日期归档
├── scripts/                               # 辅助脚本 check_env 等
└── backend/tests/
    ├── test_mention_parser.py
    ├── test_task_manager.py
    ├── test_agent_runner.py
    └── test_e2e_flow.py                   # 端到端集成测试
```

### 4.1 后端各组件职责与边界

#### `core/models.py`
**职责**:数据模型定义(Pydantic 或 dataclass),供其他模块共享。
**包含**:
- `TaskState` 枚举(PENDING/THINKING/THINK_DONE/DECIDED/REPLYING/DONE/CANCELLED)
- `ThinkResult`(单个 agent 的 think 结果,含状态:pending/done/failed/cancelled)
- `Round`(一轮对话:user_message, thinks[], decision, reply)
- `Session`(对话 session,含 rounds[])
- SSE 事件类型(`SSEEvent` 联合类型,见 §5.3)

#### `core/task_manager.py`
**职责**:任务编排,推进状态机。
**接口**:
- `async create_task(session_id, user_message) -> task_id` 创建任务
- `async run_task(task_id)` 在后台 task 中跑完整 think → decide(等待用户) → reply 流程
- `async submit_decision(task_id, agent_name | "regenerate" | "auto")` 用户决策
- `async cancel_task(task_id, scope: global | agent_name)` 取消
- 内部维护 `_in_memory_subscribers: dict[task_id, list[asyncio.Queue]]` 给 SSE 用

#### `core/agent_runner.py`
**职责**:封装单次 LLM 调用,**纯函数无状态**。
**接口**:
- `async run_think(agent_name, history) -> str` 50 字理由 非流式
- `async run_reply(agent_name, history) -> AsyncIterator[str]` 完整回答 流式
- `async run_judge(judge_agent_name, thinks: list[ThinkResult]) -> str` 帮我选 返回 agent_name

每个调用使用对应 agent 的 model_client(根据 config.yaml 创建)。

#### `core/storage.py`
**职责**:MongoDB CRUD 操作,提供抽象接口。
**接口**:
- `async create_session() -> session_id`
- `async append_round(session_id, round)`
- `async update_round_field(session_id, round_id, path, value)` 用 dot path 更新
- `async load_session(session_id) -> Session`
- `async list_sessions() -> list[SessionMeta]`

抽象成接口的目的:测试时可注入 in-memory mock。

#### `core/mention_parser.py`
**职责**:从用户消息解析 @ 目标。
**接口**:
- `parse_single_mention(text, known_agents) -> str | None` 严格单 @,多 @ 返回 None(降级到 think 流程)

#### `core/sse.py`
**职责**:SSE 协议封装。
**接口**:
- `SSEStream` 类:封装 `text/event-stream` 响应格式
- `format_event(event_type, data)` 序列化

#### `routes/*`
**职责**:HTTP 路由薄壳,只做参数校验 + 调用 TaskManager。**不**包含业务逻辑。

### 4.2 前端架构(React + antd 5 + Vite + TypeScript)

**技术栈钉死**:
- **React 18**(`useReducer` + `useContext` 即可,不引 Redux/Zustand)
- **antd 5.x**(组件库,默认 CSS-in-JS)
- **TypeScript**(强制,所有 `.tsx`/`.ts`,关闭 `noImplicitAny=false`)
- **Vite 5+**(dev server / build)
- **`@microsoft/fetch-event-source`**(SSE 客户端,支持 POST 触发流和重连策略)
- **不引 react-router**(单页面,session 切换走状态)
- **不引 dayjs locale 之外的日期库**(antd 5 默认 dayjs)

#### 组件清单与职责

| 组件 | 文件 | 职责 |
|---|---|---|
| `App` | `App.tsx` | 顶层布局:`Layout` + `Sider`(SessionDrawer) + `Content`(Timeline + ChatInput) |
| `ChatInput` | `components/ChatInput.tsx` | 输入框、发送按钮、**全局 Stop 按钮**(图 1 同款圆形 stop 图标),内部根据 task 状态切换"发送"/"停止" |
| `Timeline` | `components/Timeline.tsx` | E2 完整时间线,渲染所有历史 round + 当前活跃 round |
| `UserBubble` | `components/UserBubble.tsx` | 用户消息气泡(右侧绿色) |
| `ThinkPanel` | `components/ThinkPanel.tsx` | 当前活跃 round 的 think 容器:4 列卡片活跃态,完成后变成 `ThinkCardChip` 折叠 |
| `ThinkCard` | `components/ThinkCard.tsx` | 单个 think 卡片活跃态,流式渲染 50 字理由,角标显示 `pending/done/failed/cancelled`,卡片操作区暂停按钮 + 失败/取消时显示重试按钮 |
| `ThinkCardChip` | `components/ThinkCardChip.tsx` | 历史 round 的 think 折叠态,默认显示"4 个 agent 的思考 ▼",点击展开看完整内容 |
| `DecisionCard` | `components/DecisionCard.tsx` | 4 个 agent 头像 + 选择按钮,加上"重新 think"和"帮我选"两个独立按钮,失败的 agent 灰色不可选 |
| `ReplyBubble` | `components/ReplyBubble.tsx` | 被选中 agent 的正式回答气泡(左侧 + agent 颜色),markdown 渲染 + 流式打字光标,中断时显示"已停止"角标 |
| `SessionDrawer` | `components/SessionDrawer.tsx` | 侧边抽屉:历史 session 列表,点击切换 |

#### 状态管理(useReducer + Context)

`state/types.ts` 定义全局状态形状:

```typescript
type ChatState = {
  sessionId: string | null;
  sessions: SessionMeta[];          // 侧边栏列表
  rounds: RoundView[];              // 当前 session 的所有 round
  activeTaskId: string | null;      // 进行中 task 的 id
  taskState: TaskState;             // PENDING/THINKING/THINK_DONE/DECIDED/REPLYING/DONE/CANCELLED
  sseStatus: 'idle' | 'open' | 'closed' | 'reconnecting';
  globalLoading: boolean;
};
```

`state/reducer.ts` 处理动作:

```typescript
type Action =
  | { type: 'session.select'; sessionId: string }
  | { type: 'session.list.loaded'; sessions: SessionMeta[] }
  | { type: 'rounds.loaded'; rounds: RoundView[] }
  | { type: 'task.created'; taskId: string }
  | { type: 'sse.event'; event: SSEEvent }   // 把 SSE 帧映射到 state 变更
  | { type: 'sse.status'; status: ... }
  | ...
```

`ChatContext.tsx` 提供 `useChat()` hook,任意组件可读 state、dispatch action。

#### SSE 集成(`api/sse.ts`)

封装 `@microsoft/fetch-event-source`,统一处理:
- `event:` 字段映射到 reducer 的 action type
- 自动重连(库内置)
- 关闭时主动 abort,避免 React StrictMode 双调用的内存泄漏
- 重连时优先发 `snapshot` 帧重建状态

```typescript
// api/sse.ts(伪代码)
export function openTaskStream(taskId: string, dispatch: Dispatch<Action>) {
  const ctrl = new AbortController();
  fetchEventSource(`/sse/${taskId}`, {
    signal: ctrl.signal,
    onopen: async (resp) => { dispatch({ type: 'sse.status', status: 'open' }); },
    onmessage: (msg) => {
      const event = { type: msg.event, data: JSON.parse(msg.data) } as SSEEvent;
      dispatch({ type: 'sse.event', event });
      if (event.type === 'task.state' && ['DONE','CANCELLED'].includes(event.data.state)) {
        ctrl.abort();
      }
    },
    onerror: (err) => { dispatch({ type: 'sse.status', status: 'reconnecting' }); /* 库自动重连 */ },
    onclose: () => { dispatch({ type: 'sse.status', status: 'closed' }); }
  });
  return () => ctrl.abort();
}
```

#### antd 主题与样式

- 在 `main.tsx` 用 `ConfigProvider` 注入主题 token,4 个 agent 各分配一个**主色**(对应原 HTML 里的颜色块)
- 4 个 agent 颜色 token 集中定义在 `theme/tokens.ts`,不散落在组件里
- 组件外观全用 antd 提供的(`Card`、`Button`、`Drawer`、`Avatar`、`Tag` 等),自定义 CSS 仅用于布局微调

#### 抗刷新流程的前端实现

1. 启动时读 `localStorage.getItem('session_id')` 和 `localStorage.getItem('active_task_id')`
2. 若 active_task_id 存在,先 `GET /history/{session_id}` 拉历史,再 `openTaskStream(task_id)`
3. SSE 首帧若是 `snapshot`,reducer 用 snapshot 重建当前 round 的所有 think/reply 状态
4. SSE 收到 `task.unrecoverable` 时,弹 `Modal` 让用户决定"丢弃此 task 重新提问"还是"保留历史"

#### 构建与部署

- **dev**:`cd web && npm run dev` 起 Vite dev server(默认 5173 端口),通过 Vite proxy 把 `/ask`、`/decide`、`/cancel`、`/sse`、`/history` 等转给 FastAPI(8002)。开发期前后端分离启动,HMR 流畅
- **build**:`cd web && npm run build` 产 `web/dist/`
- **prod**:FastAPI 启动时 `app.mount("/", StaticFiles(directory="web/dist", html=True))`,所有静态资源由后端 serve,**单端口部署**
- **`run.sh` 扩展**:`./run.sh dev` 同时起 vite + uvicorn 两个进程,`./run.sh start` 走生产模式(只启 uvicorn,假设 dist 已 build)

---

## 5. 数据流详解

### 5.1 普通流程(无 @,完整 think → decide → reply)

```
═══════════════════════════════════════════════════════════════
[T=0]  USER 在前端输入"周末北京去哪玩"点发送

       前端:
       1. POST /ask {session_id, user_message}
          → 服务端返回 {task_id}
       2. 立即建立 SSE: GET /sse/{task_id}

       服务端:
       1. TaskManager.create_task() 创建 task,状态 PENDING
       2. 写 MongoDB:rounds.insert({task_id, user_message, state: PENDING})
       3. asyncio.create_task(run_task(task_id)) 后台跑

═══════════════════════════════════════════════════════════════
[T=0.1] TaskManager.run_task 开始执行
       1. 状态 PENDING → THINKING,写 Mongo
       2. SSE 推送 {type: "task.state", state: "THINKING"}
       3. asyncio.gather 并发 4 个 think:
          for agent in [DS, GLM, Kimi, Qwen]:
              create_task(_run_one_think(agent))

[T=0.1] 每个 _run_one_think 内部:
       a. SSE 推送 {type: "think.start", agent: "DS"}
       b. await agent_runner.run_think(...) (httpx 网络超时 10s)
       c. 成功 → SSE 推 {type: "think.done", agent, content}
                Mongo update rounds.thinks.DS = {state: done, content}
          失败 → SSE 推 {type: "think.failed", agent, error}
                Mongo update rounds.thinks.DS = {state: failed, error}
          被取消 → SSE 推 {type: "think.cancelled", agent}

[T=2.5] 4 个 think 全部收敛(完成 / 失败 / 取消)
       1. 状态 THINKING → THINK_DONE,写 Mongo
       2. SSE 推 {type: "task.state", state: "THINK_DONE",
                  available_agents: ["DS","GLM","Kimi"]}  // Qwen 失败排除
       3. TaskManager 进入 await self._decision_event 等用户决策

═══════════════════════════════════════════════════════════════
[T=10] USER 在前端点 GLM 的卡片

       前端:POST /decide {task_id, choice: "GLM"}

       服务端:
       1. TaskManager.submit_decision(task_id, "GLM")
       2. 写 Mongo rounds.decision = {choice: GLM, decided_at: ...}
       3. set self._decision_event → run_task 醒来
       4. 状态 THINK_DONE → DECIDED → REPLYING,写 Mongo
       5. SSE 推 {type: "task.state", state: "REPLYING", agent: "GLM"}

═══════════════════════════════════════════════════════════════
[T=10.1] TaskManager 调 agent_runner.run_reply("GLM", history)

[T=10.5~T=15] 流式 chunk 持续推送
       SSE 推 {type: "reply.chunk", agent: "GLM", chunk: "..."}
       同时 Mongo update rounds.reply.partial += chunk
       (写 Mongo 节流:每 N 个 chunk 合并写一次,避免高频 IO)

[T=15] reply 完成
       1. SSE 推 {type: "reply.done", agent, content}
       2. Mongo update rounds.reply = {state: done, content, finished_at}
       3. 状态 REPLYING → DONE,写 Mongo
       4. SSE 推 {type: "task.state", state: "DONE"}
       5. 关闭 SSE 连接(或继续保持等下一轮)
═══════════════════════════════════════════════════════════════
```

### 5.2 @ 直呼流程(跳过 think)

```
[T=0] USER 输入 "@DeepSeek 你怎么看"

      服务端 POST /ask:
      1. mention_parser 解析出单 @ "DeepSeek"
      2. 创建 task 状态直接置 DECIDED
         decision = {choice: "DeepSeek", decided_at: now, reason: "user_mention"}
      3. 写 Mongo:rounds.thinks 全部置 {state: skipped}
      4. 后台 task 直接跑 run_reply,不走 think 阶段

[T=0.1~T=N] 后续与 §5.1 的 reply 阶段相同
```

**多 @ 处理**:解析到 ≥2 个有效 @,降级为完整 think 流程(不报错,因为用户意图模糊,还是让用户挑)。

**未知 @ 处理**:`@deepsek`(拼错)解析不到任何已知 agent,降级为完整 think 流程。

### 5.3 单卡取消(think 阶段)

```
[场景] think 阶段 GLM 已完成 Kimi 还在转圈 用户嫌 Kimi 慢点了 Kimi 卡片的暂停按钮

       前端:POST /cancel {task_id, scope: "Kimi"}

       服务端:
       1. TaskManager 找到 _run_one_think(Kimi) 的 asyncio.Task
       2. task.cancel() 触发 CancelledError
       3. _run_one_think 在 except CancelledError 里 publish:
          SSE {type: "think.cancelled", agent: "Kimi"}
          Mongo update rounds.thinks.Kimi.state = cancelled
       4. 这 1 个被取消不影响其他 3 个,继续等收敛

[T=后] 4 个 think 收敛 进入决策卡 Kimi 灰色不可选
       但卡片上显示"已取消 [重试]"按钮 用户点击触发:
       POST /retry-think {task_id, agent: "Kimi"}
       服务端重建 _run_one_think(Kimi) task 卡片状态回 pending → done
```

### 5.4 全局停止(任意阶段)

```
[场景] 用户点输入框右侧大圆按钮(图 1)无论当前是 think 还是 reply 一刀切

       前端:POST /cancel {task_id, scope: "global"}

       服务端 TaskManager.cancel_task(task_id, "global"):
       1. 取消所有进行中的子 task(think 各路 / reply)
       2. 状态 → CANCELLED 写 Mongo
       3. SSE 推 {type: "task.state", state: "CANCELLED"}
       4. 关闭 SSE 连接

       已生成的部分内容保留在 Mongo 里(不擦除),前端时间线显示"用户已停止"
```

### 5.5 重新 think

```
[场景] think 全部完成 用户对所有理由都不满 点决策卡上"重新 think"

       前端:POST /decide {task_id, choice: "regenerate"}

       服务端:
       1. 把当前 thinks 整体快照存到 rounds.think_history[] 里(审计)
       2. 重置 thinks 字段,状态退回 THINKING
       3. SSE 推 {type: "task.state", state: "THINKING", regenerate_count: N}
       4. 重新跑 4 个 _run_one_think
```

### 5.6 帮我选

```
[场景] think 全部完成 用户点"帮我选"

       前端:POST /decide {task_id, choice: "auto"}

       服务端:
       1. SSE 推 {type: "judge.start"}
       2. await agent_runner.run_judge(judge_agent, thinks)
          judge_agent 用 config.yaml 中 judge 字段指定的 agent
          (从现有 4 个里选,默认复用最便宜那个)
          prompt 让 judge 看 4 个 think 内容选一个最适合回答的
       3. judge 返回 agent_name
       4. SSE 推 {type: "judge.done", chosen: "..."}
       5. 走正常 decision → reply 流程,但 decision.reason = "auto_judge"
```

### 5.7 抗刷新流程

```
[场景] 用户提问后页面刷新 当时 task 处于 REPLYING

       前端启动:
       1. 从 localStorage 读 session_id 和最后的 task_id
       2. GET /history/{session_id} 拉历史 渲染 E2 时间线
       3. 检测最新 round 状态非 DONE → GET /sse/{task_id}

       服务端 SSE handler:
       1. 从 Mongo 读 task 当前快照
       2. 发一帧 {type: "snapshot", data: <当前完整状态>}
          (前端用此重建 think 卡片 + 已生成 reply 部分)
       3. 检查 _in_memory_subscribers[task_id] 是否还在
          a. 在 → 把当前连接加入 subscribers,继续推增量
          b. 不在(进程重启或不同 worker)→ 发 {type: "task.unrecoverable"}
             前端弹提示"该任务无法恢复 是否重新提问"
```

### 5.8 配置变更流程(agents 热替换)

```
[场景] 用户在前端 SettingsDrawer 改了 GLM 的 prompt 点保存

       前端: PUT /api/agents/GLM { prompt: "新提示词" }

       服务端:
       1. storage.upsert_agent("GLM", model=旧, prompt=新) → version +1 → 拿到新记录
       2. 后台异步:重建 GLM 的 think + reply 两个 deep_agent 实例
       3. 加锁 swap app.state.deep_agents["GLM-think"] 和 ["GLM-reply"]
       4. 返回 { ok, version } 给前端
       5. 前端弹 toast "已生效 当前版本 N"

       并发安全:
       - 替换瞬间正在跑的 reply 用的是旧实例(已 closure 引用) 不受影响
       - 之后的新 reply 用新实例
       - 不做"取消已跑 reply 让它用新版"
```

首版只支持改 model 和 prompt 不支持新增/删除 agent(4 个固定)。判定 agent 名是否存在用 §7.4 中 `agents` collection 的 name 唯一索引兜底。

---

## 6. 接口契约

### 6.1 HTTP 路由

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/ask` | `{session_id?, user_message}` | `{session_id, task_id}` |
| `POST` | `/decide` | `{task_id, choice: "DS"\|"GLM"\|"Kimi"\|"Qwen"\|"regenerate"\|"auto"}` | `{ok: true}` |
| `POST` | `/cancel` | `{task_id, scope: "global"\|<agent_name>}` | `{ok: true}` |
| `POST` | `/retry-think` | `{task_id, agent: <name>}` | `{ok: true}` |
| `GET`  | `/sse/{task_id}` | - | `text/event-stream` |
| `GET`  | `/history/{session_id}` | - | `{session, rounds: []}` |
| `GET`  | `/sessions` | - | `[{session_id, last_message, updated_at}]` |
| `GET`  | `/api/agents` | - | 列出所有 agent 配置(返回数组,不含 key) |
| `PUT`  | `/api/agents/{name}` | `{model?, prompt?}` | 更新某个 agent,服务端原子热替换实例,返回 `{ok, version}` |
| `GET`  | `/` 及 `/assets/*` | - | 由 `StaticFiles(directory="web/dist", html=True)` 挂载,提供 React SPA 入口与构建产物 |

### 6.2 SSE 事件 schema

每帧格式 `event: <type>\ndata: <json>\n\n`

| 事件 type | data 字段 | 含义 |
|---|---|---|
| `snapshot` | `<完整 task 快照>` | 抗刷新重连首帧 |
| `task.state` | `{state, ...meta}` | 状态变更 |
| `think.start` | `{agent}` | 单个 agent 开始 think |
| `think.done` | `{agent, content}` | 单个 agent think 完成 |
| `think.failed` | `{agent, error}` | 单个 agent think 失败 |
| `think.cancelled` | `{agent}` | 单个 agent think 被取消 |
| `judge.start` | `{}` | 帮我选开始 |
| `judge.done` | `{chosen}` | 帮我选完成 |
| `reply.start` | `{agent}` | 正式回答开始 |
| `reply.chunk` | `{agent, chunk}` | 流式 token |
| `reply.done` | `{agent, content}` | 正式回答完成 |
| `task.unrecoverable` | `{reason}` | 重连失败 |

### 6.3 config.yaml 扩展

> **重要**:从本轮起 `agents` 段和 `judge` 段降级为**首次启动种子默认值**(seed) 服务**运行时不再读取这两段** 一律以 MongoDB `agents` collection 为准。yaml 仅在 DB 中 agents 为空时被读一次注入 DB 之后再改 yaml 不生效(必须通过 `PUT /api/agents/{name}` 改 DB)。

```yaml
key: ...
base_url: ...
# agents 段:首次启动种子默认值 运行时不读 以 DB 为准
agents:
  DeepSeek: { model: ..., prompt: ... }
  GLM:      { model: ..., prompt: ... }
  Kimi:     { model: ..., prompt: ... }
  Qwen:     { model: ..., prompt: ... }
# judge 段:首次启动种子默认值 运行时不读 以 DB 为准
# 用于"帮我选"的裁判 agent 必须是 agents 段中已存在的某一个名字
judge:
  agent: GLM   # 默认复用 GLM
  prompt: >-
    你是一个公正的对话调度器。下面是 4 个 AI 助手对用户问题给出的发言意愿(50 字)。
    请只输出你认为最适合回答用户问题的助手名字,不要解释。
    可选:DeepSeek / GLM / Kimi / Qwen
```

启动时校验:
- yaml 中 `judge.agent` 必须在 `agents` 段中存在(seed 自洽)
- 若 DB 已有 agents 数据 yaml 中两段忽略 不再校验

---

## 7. MongoDB schema

### 7.1 数据库与集合

- 数据库:`multi_chat`
- 集合:`sessions`、`rounds`、`agents`(以及可能的 `settings`,具体存储方式见实现章节)

理由:
- `sessions` 文档小,查询频繁(列出对话列表)
- `rounds` 文档大(含完整 think + reply 内容),按 session 拉,但**不全量加载到 sessions 文档里**——MongoDB 单文档 16MB 限制下,长会话容易撑爆
- `agents` 持久化运行时配置(model + prompt) 取代 yaml 中静态 agents 段(yaml 降级为种子默认值 见 §3.2 决策 7 与 §6.3)

### 7.2 sessions 集合

```js
{
  _id: ObjectId,
  session_id: "uuid",          // 业务主键
  title: "周末北京去哪玩",      // 第一条用户消息截断 (用户体验)
  created_at: ISODate,
  updated_at: ISODate,
  round_count: 5
}
```

**索引**:`{session_id: 1}` 唯一,`{updated_at: -1}` 用于"最近会话"列表。

### 7.3 rounds 集合

```js
{
  _id: ObjectId,
  task_id: "uuid",             // 业务主键 创建时分配
  session_id: "uuid",          // 关联 session
  round_index: 0,              // 在 session 中的序号
  state: "DONE",               // PENDING/THINKING/THINK_DONE/DECIDED/REPLYING/DONE/CANCELLED
  user_message: "周末北京去哪玩?",
  user_mention: null,          // 单 @ 时填 agent 名 否则 null
  thinks: {
    DeepSeek: {
      state: "done",           // pending/done/failed/cancelled/skipped
      content: "我擅长深度推理...",
      error: null,
      started_at: ISODate,
      finished_at: ISODate
    },
    GLM: { ... },
    Kimi: { ... },
    Qwen: { ... }
  },
  think_history: [             // 重新 think 时把上一轮 thinks 整体快照 push 进来
    { thinks: {...}, regenerated_at: ISODate }
  ],
  decision: {
    choice: "GLM",             // agent 名 / "regenerate" / "auto"
    reason: "user_pick",       // user_pick / user_mention / auto_judge / regenerate
    decided_at: ISODate,
    judge_pick: null           // 帮我选时 judge 选的目标
  },
  reply: {
    state: "done",
    agent: "GLM",
    content: "中山公园有花展...",
    error: null,
    started_at: ISODate,
    finished_at: ISODate
  },
  created_at: ISODate,
  updated_at: ISODate
}
```

**索引**:
- `{task_id: 1}` 唯一
- `{session_id: 1, round_index: 1}` 用于按 session 顺序拉历史
- `{session_id: 1, updated_at: -1}` 用于增量同步

### 7.4 agents 集合

持久化 4 个 agent 的运行时配置 替代 yaml 中静态 agents 段。前端通过 `PUT /api/agents/{name}` 改完后服务端原子热替换 deep_agent 实例(见 §5.8)。

```js
{
  _id: ObjectId,
  name: "DeepSeek",   // 唯一 业务主键
  model: "deepseek-v4-pro",
  prompt: "你是一个深度思考型AI助手...",
  kind: "agent",      // "agent" 或 "judge_target"
  version: 1,         // 改一次 +1 前端用来比对
  updated_at: ISODate
}
```

**索引**:`{name: 1}` 唯一。

**首次启动 seed 流程**:
1. 启动时连 mongo → ensure_indexes
2. count agents collection 文档数 若为 0 → 读 `config.yaml` 的 `agents` 段 + `judge` 段 一次性 insertMany 进 DB(每条初始 version = 1)
3. 之后任何运行时配置改动一律走 PUT API 不再回头看 yaml

**关于 judge 指针的存储**:
"哪个 agent 当裁判"的存储方式由实施阶段决定 候选两种:
- 方案 A:在 `agents` 集合中追加一条 `name="__judge__"` `kind="judge_target"` 的特殊文档 value 字段指向某个 agent 名
- 方案 B:新增 `settings` 集合存通用键值对 judge 指针为其中一条

无论选哪种 都不影响 §6.1 路由表和 §5.8 热替换流程。

### 7.5 写入策略

- **节流**:reply 流式 chunk 不每个都写 Mongo,**累积 200ms 或 500 字符** flush 一次
- **最终写入**:think.done / reply.done 等关键节点必落盘
- **批量更新**:用 `$set` 精确更新嵌套字段(如 `thinks.DeepSeek.state`),避免整文档替换
- **失败处理**:Mongo 写入失败不阻塞 SSE 推送(用户体验优先),记错误日志,任务结束时再补一次完整快照

### 7.6 历史会话上下文裁剪

`run_reply` 时给 LLM 的 history 不能直接全量塞——长会话会把 context 撑爆:

- **取近 N 轮**:N 默认 10,可配置
- **每轮只取 user_message + reply.content**,**think 内容不进 history**(它是给用户看的元数据,不是对话本身)
- **超长 reply 截断**:单轮 reply 超过 K tokens 时,仅取前 + 后各一段

---

## 8. 错误与边界处理

| 场景 | 行为 |
|---|---|
| 4 个 think 全部失败 | task 状态置 THINK_DONE,决策卡只显示"重新 think"和"取消" |
| think 阶段 LLM 返回超 50 字 | 保留前 60 字截断(给 10 字弹性) |
| 用户 decide 时 task 已 CANCELLED | 返回 409 Conflict |
| 用户 decide 时 task 状态非 THINK_DONE | 返回 409 Conflict |
| 同一 task 被并发 decide(双击) | TaskManager 内部用 `asyncio.Lock` 保证只接受第一个 |
| MongoDB 短暂不可用 | 任务在内存继续跑,SSE 正常推,定期重试落盘 |
| MongoDB 持续不可用 | 启动时连接失败直接拒绝服务(早暴露) |
| 用户 @ 不存在 agent | 降级为完整 think 流程(不报错) |
| 用户多 @ | 降级为完整 think 流程 |
| reply 阶段 LLM 中途断流 | 保留已生成部分,SSE 推 reply.error,task 置 DONE 但 reply.state = failed |
| SSE 客户端断开 | 服务端 task 不停,继续写 Mongo,可重连 |
| 服务进程重启时有未完成 task | 启动时扫 Mongo state ∈ {THINKING, REPLYING} 的 task 全部置 CANCELLED 并标记 reason=server_restart |

---

## 9. 测试策略

### 9.1 单元测试

- `test_mention_parser.py`:正常单 @ / 大小写 / 别名 / 多 @ 降级 / 未知 @ 降级
- `test_storage.py`:用 mongomock 跑 CRUD 路径
- `test_agent_runner.py`:mock httpx,验证 prompt 拼装、超时处理、流式分块解析

### 9.2 集成测试(无真 LLM)

`tests/test_e2e_flow.py`:`pytest-asyncio` + `mongomock` + 假 agent_runner(返回固定文本)

走完整流程:
- 测 1:正常 ask → think 收齐 → decide → reply → done
- 测 2:@ 直呼 → 跳过 think 直接 reply
- 测 3:think 阶段单卡 cancel
- 测 4:全局 cancel
- 测 5:重新 think
- 测 6:帮我选
- 测 7:抗刷新 重新 GET /sse/{task_id} 收到 snapshot

### 9.3 红绿灯节点

按你 CLAUDE.md 的要求,关键节点跑红绿灯测试:

1. **Mongo schema 落地后** 先单独跑 storage 测试
2. **TaskManager 编排逻辑** 用假 agent_runner 跑 e2e
3. **真 LLM 接通后** 用 dashscope key 跑一次手动验证(单 round)
4. **前端联调** 起 dev server,浏览器跑一遍 think → decide → reply

---

## 10. 迁移与回滚

### 10.1 旧 JSON 文件

按你的决策,**直接抛弃** `team_state.json` 和 `team_history.json`,不写迁移脚本。新方案启动时:

- 这两个文件保留在 disk 上但不读取
- 第一次 ask 创建新 session,从此用 Mongo

### 10.2 代码迁移路径

新代码托管在独立仓库 `multi-chat/`,**不再修改原 autogen 仓库中的 `app_team.py`**。新仓库里只实现 think-then-choose 模式,无需保留 RoundRobinGroupChat 的兼容分支:

- 旧 sample 留在 autogen 仓库 `python/samples/agentchat_fastapi/` 中归档保存 不再演进
- 新仓库 `backend/src/multichat/main.py` 直接以 think-then-choose 为唯一形态实装
- 不再使用 `MULTICHAT_MODE` 环境变量切换 旧实现的回滚通过回退到 autogen 仓库的对应 commit 完成

理由:**新项目独立 减少历史包袱**——不在新仓库里维护两套并行实现。回滚需求改由"切回 autogen 旧 sample"承担。

### 10.3 启动依赖

新增**后端依赖**(`requirements.txt` 或 `pyproject.toml`):

```
motor>=3.3        # Mongo 异步驱动
mongomock-motor   # 测试用
```

新增**前端依赖**(`web/package.json`):

```json
{
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "antd": "^5.20.0",
    "@microsoft/fetch-event-source": "^2.0.1",
    "react-markdown": "^9.0.0"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "vite": "^5.4.0",
    "@vitejs/plugin-react": "^4.3.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0"
  }
}
```

**Node 工具链要求**:Node 18+ / npm 9+(Vite 5 的最低要求)。

`run.sh` 增加启动前 Mongo 连通性检查:

```bash
# 启动前 ping Mongo
${PYTHON_BIN} -c "
import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
asyncio.run(AsyncIOMotorClient(os.environ.get('MONGO_URI', 'mongodb://localhost:27017'))
    .admin.command('ping'))
" || { echo 'MongoDB 不可达 启动中止'; exit 1; }
```

`config.yaml` 新增字段(可选,有默认):

```yaml
mongo:
  uri: mongodb://localhost:27017
  db: multi_chat
runtime:
  history_max_rounds: 10        # reply 时 history 取最近 N 轮
  reply_flush_interval_ms: 200  # Mongo 写入节流
  http_timeout_seconds: 10      # LLM HTTP 超时
```

**启动序列必须严格按此顺序**:`连 mongo → ensure_indexes → seed_from_yaml → build deep_agents`。顺序错了启动失败:索引没建好就 seed 会出现重复 name 的脏数据;seed 没跑完就 build deep_agents 会读到空的 agents collection 拿不到配置。

`run.sh` 增加前端相关子命令:

| 子命令 | 行为 |
|---|---|
| `./run.sh dev` | 双进程 dev:`vite` 起 5173 端口 + `uvicorn --reload` 起 8002,Vite proxy `/ask`/`/sse`/... 到 8002 |
| `./run.sh build` | `cd web && npm install && npm run build` 产 `web/dist/` |
| `./run.sh start` | 生产模式:仅启 uvicorn(假设 `web/dist/` 已存在),没有则报错提示先 `./run.sh build` |
| `./run.sh stop` / `restart` / `log` / `status` | 沿用,只针对 uvicorn 进程 |

前端 dev 模式不进入 `.run.pid` 管理,Ctrl+C 自行结束。生产模式 `start` 严格管 uvicorn 进程。

---

## 11. 风险与待观察项

| 风险 | 影响 | 缓解 |
|---|---|---|
| think 50 字模型不听话(写 200 字) | UI 排版破坏 | 截断 + system prompt 强制 |
| 用户全选困难症 → "帮我选"被滥用 | judge LLM 调用激增 | 加每分钟限频(配置项) |
| Mongo 单 round 文档随 reply 变长 | 接近 16MB 上限 | reply 超长时落 GridFS(后续扩展) |
| SSE 在某些代理/Nginx 后被缓冲 | 流式断断续续 | 部署文档明确要求 `proxy_buffering off` |
| MongoDB 写入抖动影响 SSE 推送 | 体验卡顿 | 写入异步化,SSE 不等 Mongo 确认 |
| 单进程 task 持有内存 → 不能多 worker 横向扩展 | 并发上限受限 | 当前规模够用,真要扩需引入外部任务队列 |
| 抗刷新仅限单进程 | 多 worker 部署后失效 | 文档说明 + 同 client → 同 worker 的 sticky 路由 |
| 用户连续提问导致旧 task 未完成被覆盖 | 时间线乱序 | UI 锁:旧 task 未 DONE 前发送按钮 disabled |
| Vite dev server 与 FastAPI 跨端口的 CORS / cookie | 开发期登录态缺失 | dev 模式用 Vite proxy 把所有 API 转给 8002,前端永远 same-origin 调用 |
| antd 5 默认 CSS-in-JS 体积偏大 | 首屏稍慢 | 先不优化;若实测慢,后续按需引入 `babel-plugin-import` 或切到 antd 的静态主题 |
| React StrictMode 下 SSE 双连接 | 开发期重复请求,服务端 task 多副本 | `useEffect` 返回 cleanup 主动 abort,生产模式不受影响 |
| 用户改坏 prompt 把 agent 搞瘫 | reply 报错 | 前端编辑器加最小长度校验 + 后端保留版本历史允许回滚(M5 待做) |

---

## 12. 实施顺序建议

> 进度提示:**M0 已完成(2026-05-21)**,验证结论见本文 §0。当前下一步是 M1。

按依赖关系拆分 5 个里程碑,每个里程碑独立可测:

1. **M1 基础设施 + agents 持久化**:MongoDB 连通、`storage.py` 含 sessions/rounds/agents CRUD、`config.py` 实装、首次启动 seed_from_yaml 注入 4 agent + judge 指针、`GET /api/agents` 与 `PUT /api/agents/{name}` 路由、deep_agent 实例热替换 swap、前端 SettingsDrawer 抽屉用于编辑 4 个 agent 配置、`run.sh` 改造
2. **M2 核心流程**:`models.py` + `task_manager.py` + `agent_runner.py` + 假 LLM 的 e2e 测试通过
3. **M3 路由与 SSE**:`routes/*` + `sse.py`,curl/httpie 能完整跑通流程
4. **M4 前端**:在 `web/` 下用 Vite 脚手架建 React+TS+antd 工程
   - 4.1 工程初始化:`npm create vite@latest web -- --template react-ts`,装 antd / fetch-event-source / react-markdown
   - 4.2 基础布局:`App.tsx` + `Layout` + `SessionDrawer` + `ChatInput`,通了 dev server 能 ping 后端
   - 4.3 状态层:`ChatContext` + `reducer` + `types`,所有 SSE 事件能正确映射到 state
   - 4.4 时间线与气泡:`Timeline` + `UserBubble` + `ReplyBubble`(流式打字)
   - 4.5 think + decision:`ThinkPanel` + `ThinkCard` + `ThinkCardChip` + `DecisionCard`
   - 4.6 边界态:取消按钮、超时失败、重试按钮、抗刷新 snapshot 处理
   - 4.7 主题与样式:`ConfigProvider` + 4 agent 颜色 token + 紫色渐变禁用(按用户规范)
   - 4.8 build 产物挂到 FastAPI,`./run.sh start` 单端口生产模式跑通
5. **M5 抗刷新 + 真 LLM 联调 + 文档收尾**

每个里程碑结束写一份 `工作记录 - YYYY 年 MMDD.md` 到 `doc/`。

---

## 13. 开放问题(留待 plan 阶段决议)

下列细节我故意没在设计稿里钉死,等 writing-plans 阶段再具体化:

1. SSE 心跳 / keepalive 间隔
2. judge 模型的具体 prompt 措辞与失败兜底(judge 也失败时降级随机选)
3. 前端"帮我选"按钮防抖
4. think 超时阈值的具体值(由 http_timeout 决定,但 UI 上要不要显示倒计时)
5. session_id 生成是 UUIDv4 还是 ULID(影响排序)
6. 用户能否手动管理 session(删除 / 改标题)——非首版必备
7. 前端是否做 i18n(默认中文硬编码,后续若需多语言再上 antd locale)
8. 前端 markdown 是否支持代码高亮(react-markdown + rehype-highlight,默认不开,实施时按需)
9. 前端测试方案(Vitest + React Testing Library 或先不做单测,e2e 用后端真 SSE 跑)
10. agents 配置历史版本回滚(改坏了能恢复) M5 阶段实现

这些不影响整体架构,实现时按需要选最简单的做法即可。

---

**设计稿完毕。** 下一步:用户审核此 spec → 进入 writing-plans 阶段产出实施计划。




