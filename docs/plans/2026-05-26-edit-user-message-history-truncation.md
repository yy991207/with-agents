# 编辑用户消息历史回退 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 支持用户在历史消息上编辑后重新发送，并把该消息之后的对话与被压缩进摘要的后续上下文一起裁掉。

**Architecture:** 前端在用户气泡上提供 hover 编辑入口，进入编辑态后复用底部输入框重新发送。后端通过 `/ask` 增加“从指定 round 重新开始”的参数，先裁掉目标 round 及其后续记录，再创建新的 round。若摘要覆盖范围碰到编辑点，则回退或清空摘要与上下文快照，保证后续上下文从编辑后的当前消息重新计算。

**Tech Stack:** React + TypeScript + Ant Design, FastAPI + Pydantic + MongoDB, Vitest / pytest

---

### Task 1: 后端截断与摘要回退

**Files:**
- Modify: `backend/src/multichat/routes/ask.py`
- Modify: `backend/src/multichat/core/task_manager.py`
- Modify: `backend/src/multichat/storage/base.py`
- Modify: `backend/src/multichat/storage/mongo.py`
- Test: `backend/tests/test_edit_message_truncation.py`

**Step 1: Write the failing test**

```python
async def test_edit_send_truncates_tail_and_updates_summary():
    ...
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_edit_message_truncation.py -q`

Expected: FAIL because `replace_task_id` 还不存在，且截断逻辑未实现。

**Step 3: Write minimal implementation**

实现 `/ask` 编辑参数、删除指定 round 之后历史、按摘要覆盖范围回退 summary / context_usage。

**Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_edit_message_truncation.py -q`

Expected: PASS

### Task 2: 前端 hover 编辑与重发确认

**Files:**
- Modify: `web/src/components/UserBubble.tsx`
- Modify: `web/src/components/Timeline.tsx`
- Modify: `web/src/components/ChatInput.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/hooks/useChatTask.ts`
- Modify: `web/src/api/http.ts`

**Step 1: Write the failing test**

```ts
test('editing a prior message resets the tail rounds', () => {
  ...
});
```

**Step 2: Run test to verify it fails**

Run: `cd web && npm test -- --run path/to/test`

Expected: FAIL because edit state / confirm / resend flow not wired。

**Step 3: Write minimal implementation**

实现 hover 编辑图标、编辑态输入框、确认丢失历史提示、发送时携带 replace task id。

**Step 4: Run test to verify it passes**

Run: `cd web && npm test -- --run path/to/test`

Expected: PASS

