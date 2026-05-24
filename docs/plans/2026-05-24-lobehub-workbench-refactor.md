# Multi-Chat LobeHub Workbench Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把当前 `web` 前端重构成贴近 LobeHub 的工作台外壳与首页结构，同时保留现有多 agent 会话、SSE 重连、历史恢复、配置管理能力。

**Architecture:** 不搬动现有业务状态和接口协议，继续沿用 `ChatContext`、`useSession`、`useSettings`、`request()`、SSE worker 这套主链路；只在其外层新增一层很薄的“工作台 UI 状态”和新的页面壳组件。依赖上不直接追最新 `@lobehub/ui`，而是使用与当前 React 18 / Ant Design 5 兼容的 `@lobehub/ui@1.171.0` 体系，先完成导航壳、首页、最近、助理、占位页，再把聊天区和设置区外观换成 LobeHub 风格。

**Tech Stack:** React 18、TypeScript、Vite、Ant Design 5、`@lobehub/ui@1.171.0`、`@lobehub/icons@1.x`、`antd-style`、现有 SSE/HTTP hooks。

---

## 实施前必须知道的约束

1. 当前项目不能直接上 `@lobehub/ui@5.x`。它要求 React 19 + Ant Design 6，会和 `web/package.json` 当前依赖冲突。
2. 本次只做“LobeHub 工作台壳层 + 现有业务能力映射”。
3. `任务 / 文稿 / 社区 / 资源 / 生成` 先做高保真占位页，不伪造后端能力。
4. `SettingsDrawer.tsx` 里的 agent / judge / MCP / skills 现有能力一个都不能丢，只能换壳。
5. `App.tsx` 里的启动恢复、历史回放、SSE 重连逻辑是高风险区，只允许最小改动。

---

### Task 1: 锁定兼容依赖并建立样式上下文

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/src/main.tsx`
- Modify: `web/src/theme/tokens.ts`

**Step 1: 先把依赖版本写死到兼容组合**

在 `web/package.json` 新增以下依赖，禁止升级 React 或 antd 主版本：

```json
{
  "dependencies": {
    "@lobehub/ui": "1.171.0",
    "@lobehub/icons": "^1.94.0",
    "antd-style": "^3.7.1",
    "react-layout-kit": "^1.9.1",
    "lucide-react": "^0.484.0"
  }
}
```

**Step 2: 安装依赖并生成锁文件**

Run: `cd web && npm install`

Expected: 安装成功，不升级 `react` 到 19，不升级 `antd` 到 6。

**Step 3: 在 `web/src/main.tsx` 接入样式提供器**

最小实现目标：保持当前 `ConfigProvider` 和 `ChatProvider` 顺序不乱，在外层加 `ThemeProvider`（或 `StyleProvider`）给 Lobe 组件提供 CSS 变量上下文。

示例结构：

```tsx
<React.StrictMode>
  <ThemeProvider themeMode="light">
    <ConfigProvider theme={themeConfig}>
      <ChatProvider>
        <App />
      </ChatProvider>
    </ConfigProvider>
  </ThemeProvider>
</React.StrictMode>
```

**Step 4: 收紧主题 token，保证风格统一**

在 `web/src/theme/tokens.ts`：
- 保留当前蓝色主色；
- 补充背景、边框、文本层级 token；
- 不要引入紫色渐变；
- 给后续首页卡片、主容器、输入区留出统一圆角和阴影变量。

**Step 5: 运行基础校验**

Run: `cd web && npm run type-check`

Expected: PASS。

**Step 6: Commit**

```bash
git add web/package.json web/package-lock.json web/src/main.tsx web/src/theme/tokens.ts
git commit -m "feat: add compatible lobehub ui dependencies"
```

---

### Task 2: 增加工作台壳层 UI 状态，不碰业务状态机

**Files:**
- Modify: `web/src/state/types.ts`
- Modify: `web/src/state/reducer.ts`

**Step 1: 先定义新的页面壳状态类型**

在 `web/src/state/types.ts` 新增：

```ts
export type WorkbenchView =
  | 'home'
  | 'chat'
  | 'tasks'
  | 'page'
  | 'image'
  | 'community'
  | 'resource';

export interface WorkbenchState {
  activeView: WorkbenchView;
  sidebarCollapsed: boolean;
  recentExpanded: boolean;
  agentsExpanded: boolean;
  recommendPage: number;
}
```

并把它挂到 `ChatState`。

**Step 2: 新增 reducer action**

在 `ChatAction` 里加：

```ts
| { type: 'ui.view.set'; view: WorkbenchView }
| { type: 'ui.sidebar.toggle'; collapsed?: boolean }
| { type: 'ui.section.toggle'; section: 'recent' | 'agents' }
| { type: 'ui.recommend.rotate' }
```

**Step 3: 给初始状态补默认值**

在 `web/src/state/reducer.ts` 的 `initialState` 里加：

```ts
workbench: {
  activeView: 'home',
  sidebarCollapsed: false,
  recentExpanded: true,
  agentsExpanded: true,
  recommendPage: 0,
}
```

**Step 4: 实现 reducer 分支，并保持业务联动最小**

要求：
- `session.switch` 到具体会话时，自动切 `activeView='chat'`；
- `task.created` 时自动切 `activeView='chat'`；
- `session.deleted` 删除当前会话后，如果没有剩余会话和轮次，可退回 `home`；
- 其他业务状态机逻辑不改。

**Step 5: 运行类型检查验证新增状态没有破坏现有调用**

Run: `cd web && npm run type-check`

Expected: PASS；如果报 `ChatState` 缺字段，就继续补齐引用。

**Step 6: Commit**

```bash
git add web/src/state/types.ts web/src/state/reducer.ts
git commit -m "feat: add workbench ui state"
```

---

### Task 3: 搭出 LobeHub 风格外壳和导航侧栏

**Files:**
- Create: `web/src/components/lobehub/LobeWorkbenchShell.tsx`
- Create: `web/src/components/lobehub/LobeSidebar.tsx`
- Create: `web/src/components/lobehub/LobeNavItem.tsx`
- Create: `web/src/components/lobehub/LobeSectionList.tsx`
- Create: `web/src/components/lobehub/lobeData.tsx`
- Modify: `web/src/styles/global.css`

**Step 1: 先创建导航元数据文件**

在 `web/src/components/lobehub/lobeData.tsx` 里写死工作台导航：
- 顶部：搜索、首页；
- 中段：最近、助理；
- 底部：生成、社区、资源、帮助中心；
- 扩展入口：任务、文稿做为主导航项。

推荐用常量数组描述：

```ts
export const PRIMARY_NAV = [...];
export const SECONDARY_NAV = [...];
export const PLACEHOLDER_COPY = {...};
```

**Step 2: 写 `LobeNavItem.tsx` 和 `LobeSectionList.tsx`**

要求：
- 外观贴近参考界面；
- 高度、左右留白、图标位、hover 态、active 态统一；
- 允许右侧挂操作按钮或数量；
- 中文注释说明“这里只管理展示，不处理业务请求”。

**Step 3: 写 `LobeSidebar.tsx`**

功能要求：
- 用户头部区；
- 折叠按钮；
- 搜索入口（首版只做静态触发，不接搜索逻辑）；
- 首页 / 任务 / 文稿 / 最近 / 助理 / 生成 / 社区 / 资源 / 帮助中心；
- 最近分组消费 `useSession()` 返回的 `sessions`；
- 助理分组消费 `useChat().state.settings.drafts`；
- 点击最近会话时调用 `switchSession()`；
- 点击“创建助理”时打开 `SettingsDrawer` 并切到 agent 配置。

**Step 4: 用 `LobeWorkbenchShell.tsx` 包出整体外壳**

结构目标：

```tsx
<Flexbox horizontal width={'100%'} height={'100%'}>
  <LobeSidebar />
  <main className="workbench-main-shell">...</main>
</Flexbox>
```

主内容区需要：
- 外层 8px 页面边距；
- 内层圆角大容器；
- 边框和背景分层接近参考界面。

**Step 5: 在 `web/src/styles/global.css` 补壳层样式**

只补壳层公共类：
- `body`、`#root` 背景层级；
- `workbench-main-shell`；
- `lobe-placeholder-page`；
- `reply-cursor-blink` 动画不要重复定义。

**Step 6: 运行类型检查**

Run: `cd web && npm run type-check`

Expected: 允许暂时因为 `App.tsx` 还没接入而出现“未使用导出”警告；不能有类型错误。

**Step 7: Commit**

```bash
git add web/src/components/lobehub web/src/styles/global.css
git commit -m "feat: add lobehub workbench shell"
```

---

### Task 4: 首页欢迎区、推荐卡片和占位页落地

**Files:**
- Create: `web/src/components/lobehub/LobeHomeView.tsx`
- Create: `web/src/components/lobehub/LobeRecommendCard.tsx`
- Create: `web/src/components/lobehub/LobePlaceholderView.tsx`
- Modify: `web/src/components/lobehub/lobeData.tsx`

**Step 1: 写首页欢迎区**

欢迎区结构必须贴近参考界面：
- 顶部一行：头像 + 助理名 + 切换箭头；
- 下方两行欢迎文案；
- 整体间距紧凑，不做夸张 Hero 区。

建议 props：

```ts
interface LobeHomeViewProps {
  onSendPreset: (message: string) => void;
  onOpenSettings: () => void;
  onOpenView: (view: WorkbenchView) => void;
}
```

**Step 2: 写横幅区和主输入承载区**

首页要有一条轻横幅，文案映射当前真实能力：
- 多 agent 协作；
- Judge；
- MCP；
- Skills。

输入区本体后面接 `ChatInput`，但首页外面要先包一层 LobeHub 风格容器。

**Step 3: 写推荐卡片区**

卡片固定两列布局，每张卡结构：
- 图标
- 标题
- 次级说明
- 标签
- 主操作按钮

首版卡片建议：
- 开始一次多 agent 对话
- 管理数字员工
- 配置 MCP 工具
- 维护 Skills
- 查看最近会话
- 进入资源占位页

**Step 4: 写占位页组件**

`LobePlaceholderView.tsx` 用统一模板承接：
- 任务
- 文稿
- 生成
- 社区
- 资源

每页都要有：
- 标题、副标题；
- 2~3 张说明卡；
- 底部真实操作按钮，如“返回首页”“打开设置”“查看最近会话”。

**Step 5: 跑一次类型检查**

Run: `cd web && npm run type-check`

Expected: PASS。

**Step 6: Commit**

```bash
git add web/src/components/lobehub/LobeHomeView.tsx web/src/components/lobehub/LobeRecommendCard.tsx web/src/components/lobehub/LobePlaceholderView.tsx web/src/components/lobehub/lobeData.tsx
git commit -m "feat: add lobehub style home and placeholder views"
```

---

### Task 5: 用新壳接管 `App.tsx`，但保留启动恢复和 SSE 逻辑

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/hooks/useSession.ts`
- Modify: `web/src/hooks/useSettings.ts`

**Step 1: 先把 `App.tsx` 的布局替换成新壳，不动启动逻辑**

保留这些块原样：
- `bootstrappedRef`
- `loadPersisted()` 恢复逻辑
- `getHistory()`
- `getAgents()`
- `openTaskStream()`
- `handleChoose()` / `handleRetry()` / `handleRetryReply()`

只替换 `return` 部分，把旧 `Layout/Sider/Header/Content` 改成：

```tsx
<LobeWorkbenchShell
  sidebar={...}
  content={...}
  settingsDrawer={<SettingsDrawer />}
/>
```

**Step 2: 根据 `state.workbench.activeView` 切内容区**

规则：
- `home`：显示 `LobeHomeView`
- `chat`：显示聊天主页面
- 其他：显示 `LobePlaceholderView`

**Step 3: 首页输入和推荐操作要能真实驱动会话**

要求：
- 首页输入发送后直接复用 `send()`；
- 点击推荐卡片时，给 `send()` 传预置问题；
- 点击最近会话时切 `chat`；
- 点击首页时若当前没有任务可回 `home`。

**Step 4: 微调 `useSession()` / `useSettings()` 暴露能力**

如果当前接口不够用，只补最小能力，例如：
- 首页或侧栏切换视图时需要外部可控；
- “创建助理”可能需要一个直接打开设置并切到 agent tab 的方法。

不要重写已有 hook，只加最少辅助方法。

**Step 5: 跑类型检查**

Run: `cd web && npm run type-check`

Expected: PASS。

**Step 6: 手动跑启动页联调**

Run: `cd web && npm run dev`

Expected:
- 首屏能看到新壳；
- 无历史时显示首页；
- 发送问题后切到聊天页；
- 刷新后历史仍能恢复。

**Step 7: Commit**

```bash
git add web/src/App.tsx web/src/hooks/useSession.ts web/src/hooks/useSettings.ts
git commit -m "feat: integrate lobehub workbench into app shell"
```

---

### Task 6: 聊天区换壳，但不改交互协议

**Files:**
- Create: `web/src/components/lobehub/LobeChatView.tsx`
- Modify: `web/src/components/ChatInput.tsx`
- Modify: `web/src/components/Timeline.tsx`
- Modify: `web/src/components/ReplyBubble.tsx`
- Modify: `web/src/components/DecisionCard.tsx`
- Modify: `web/src/components/ThinkPanel.tsx`
- Modify: `web/src/components/ThinkCard.tsx`
- Modify: `web/src/components/ThinkCardChip.tsx`

**Step 1: 新建 `LobeChatView.tsx` 作为聊天页容器**

它只负责：
- 顶部内边距和内容宽度；
- 空态时显示接近首页的过渡视图；
- 有会话时承载 `Timeline`；
- 底部固定 `ChatInput`。

**Step 2: 重写 `ChatInput.tsx` 外观**

保留原 props：

```ts
onSend: (message: string) => void | Promise<void>
onStop?: () => void | Promise<void>
```

只换 UI：
- 圆角输入卡；
- 底部左右工具栏；
- 模型入口先做静态展示；
- 停止按钮和发送按钮逻辑保持不变。

**Step 3: 把 `Timeline.tsx` 改成 Lobe 风格内容宽度**

要求：
- 空态不要再用 antd `Empty` 默认样式；
- 聊天页居中内容宽度控制在接近参考界面的 `min(960px, 100%)`；
- 用户消息、think、决策、回答的间距更像对话流，而不是管理后台卡片堆叠。

**Step 4: 逐个调整消息和思考组件容器样式**

不改数据契约，只改容器：
- `ReplyBubble.tsx`
- `DecisionCard.tsx`
- `ThinkPanel.tsx`
- `ThinkCard.tsx`
- `ThinkCardChip.tsx`

重点：
- 边框更轻；
- 状态标签更紧凑；
- 颜色继续走现有 `getAgentColor()`；
- 工具调用段落更像 LobeHub 的辅助信息块。

**Step 5: 运行构建和类型检查**

Run:
- `cd web && npm run type-check`
- `cd web && npm run build`

Expected: 全部 PASS。

**Step 6: Commit**

```bash
git add web/src/components/lobehub/LobeChatView.tsx web/src/components/ChatInput.tsx web/src/components/Timeline.tsx web/src/components/ReplyBubble.tsx web/src/components/DecisionCard.tsx web/src/components/ThinkPanel.tsx web/src/components/ThinkCard.tsx web/src/components/ThinkCardChip.tsx
git commit -m "feat: restyle chat flow with lobehub workbench ui"
```

---

### Task 7: 设置区改成更像 LobeHub 的侧栏交互，但能力不减

**Files:**
- Modify: `web/src/components/SettingsDrawer.tsx`
- Modify: `web/src/components/McpManagePanel.tsx`
- Modify: `web/src/components/McpSettingsPanel.tsx`
- Modify: `web/src/components/SkillsPanel.tsx`

**Step 1: 先统一设置抽屉外壳**

目标：
- 左侧保持菜单，但视觉更像参考界面的面板选择器；
- 右侧正文区改成 section block，而不是传统后台大表单；
- agent / judge / MCP / skills 的已有字段和按钮全部保留。

**Step 2: 优先改 agent 和 judge 区域层级**

要求：
- 当前选中 agent 顶部信息做成“标签 + 版本 + 内部 ID”摘要区；
- 保存 / 重置按钮固定在可见区域；
- 新建助理入口更靠近参考界面的“创建助理”。

**Step 3: MCP / Skills 保留功能，只改容器**

首版只做：
- 标题层级统一；
- 表单分组；
- 列表与详情之间的留白、边框、说明文案统一。

**Step 4: 跑类型检查**

Run: `cd web && npm run type-check`

Expected: PASS。

**Step 5: 手动验证设置能力**

Run: `cd web && npm run dev`

人工检查：
- 打开设置
- 切换 agent
- 修改显示名并保存
- 切换 Judge
- 浏览 MCP / Skills 面板

**Step 6: Commit**

```bash
git add web/src/components/SettingsDrawer.tsx web/src/components/McpManagePanel.tsx web/src/components/McpSettingsPanel.tsx web/src/components/SkillsPanel.tsx
git commit -m "feat: restyle settings surfaces for lobehub workbench"
```

---

### Task 8: 文档、工作记录和最终验证

**Files:**
- Modify: `doc/工作记录 - 2026 年 0524.md`

**Step 1: 纠正工作记录中的不实内容**

当前这份记录已经提前写了“已完成”的内容，实施结束后要按真实结果重写，至少包含：
- 改动背景
- 兼容性判断（为什么选 `@lobehub/ui@1.171.0`）
- 新增文件
- 修改文件
- 保留不动的业务逻辑
- 异常处理方式
- 影响范围
- 测试情况

**Step 2: 运行最终验证命令**

Run:
- `cd web && npm run type-check`
- `cd web && npm run build`

Expected: 全部 PASS。

**Step 3: 做一轮人工回归**

必须检查：
1. 首次进入无历史时显示首页；
2. 首页输入问题后切到聊天页；
3. 最近会话可切换；
4. 刷新后历史能恢复；
5. 活跃任务刷新后 SSE 能继续接；
6. Think → Decision → Reply 链路完整；
7. 设置抽屉四个面板都能打开；
8. 占位页入口都能正常切换。

**Step 4: Commit**

```bash
git add doc/工作记录\ -\ 2026\ 年\ 0524.md
git commit -m "docs: update lobehub refactor work log"
```

---

## 最终验收命令

```bash
cd web && npm run type-check
cd web && npm run build
```

## 最终人工验收清单

- 首页欢迎区层级接近参考界面
- 推荐卡片为双列，层级清晰
- 侧边栏包含：首页 / 任务 / 文稿 / 最近 / 助理 / 生成 / 社区 / 资源 / 帮助中心
- 最近和助理分组可展开收起
- 聊天页保持现有业务能力
- 设置区保留 agent / judge / MCP / skills 全部能力
- 不出现紫色渐变
- 不引入绝对路径

## 风险清单

1. `@lobehub/ui` 老版本 API 可能与 `/Users/yang/lobehub` 当前主仓风格不完全一致，优先复刻结构和交互，不强求内部实现同源。
2. 侧边栏、输入区、消息区可能出现样式冲突，优先保持功能可用，再做视觉精修。
3. `doc/工作记录 - 2026 年 0524.md` 当前内容与真实代码状态不一致，最后必须回写。
