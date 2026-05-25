// Settings 抽屉 hook:封装拉取、保存、judge 切换、agent CRUD 的副作用与 dispatch
// 通过 antd message.* 上报错误与成功 把网络细节挡在外面
import { useCallback } from 'react';
import { message } from 'antd';
import {
  createAgent as apiCreateAgent,
  deleteAgent as apiDeleteAgent,
  deleteAgentAvatar,
  getAgent,
  getAgents,
  updateAgent,
  updateJudge,
  uploadAgentAvatar,
} from '../api/http';
import { convertAgentView } from '../state/converters';
import { useChat } from '../state/ChatContext';
import type {
  AgentEditDraft,
  CreateAgentRequest,
  UpdateAgentRequest,
} from '../state/types';

// 抽出错误信息的人话描述
function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// 从 Error.message 中提取 HTTP 状态码 失败返回 null
function parseHttpStatus(err: unknown): number | null {
  if (!(err instanceof Error)) return null;
  const m = /HTTP (\d{3}):/.exec(err.message);
  return m ? Number(m[1]) : null;
}

export function useSettings() {
  const { state, dispatch } = useChat();

  // 打开抽屉:拉 agents
  const openDrawer = useCallback(async (): Promise<void> => {
    dispatch({ type: 'settings.open' });
    dispatch({ type: 'settings.loading.start' });
    try {
      const data = await getAgents();
      const agents = (data.agents ?? []).map((a) => convertAgentView(a));
      dispatch({
        type: 'settings.loaded',
        agents,
        judgeTarget: data.judge_target,
      });
    } catch (e) {
      const msg = describeError(e);
      dispatch({ type: 'settings.error', message: msg });
      message.error(`加载 agent 配置失败:${msg}`);
    }
  }, [dispatch]);

  // 关闭抽屉
  const closeDrawer = useCallback((): void => {
    dispatch({ type: 'settings.close' });
  }, [dispatch]);

  // 切换当前 active 的 agent tab
  const switchTab = useCallback(
    (agentName: string): void => {
      dispatch({ type: 'settings.agent.tab.switch', name: agentName });
    },
    [dispatch],
  );

  // 编辑表单字段:仅写本地草稿 不发请求
  // 上层把 partial draft 直接传进来 reducer 自己 merge
  const setDraftField = useCallback(
    (agentName: string, patch: Partial<AgentEditDraft>): void => {
      dispatch({ type: 'settings.draft.field', agentName, patch });
    },
    [dispatch],
  );

  // 重置某个 agent 的草稿到当前服务端版本
  const reset = useCallback(
    async (name: string): Promise<void> => {
      try {
        const fresh = await getAgent(name);
        // 借 settings.saved 用最新 agent 重置该 draft
        dispatch({ type: 'settings.saved', agent: fresh });
        message.success(`${fresh.display_name || fresh.name} 已重置到 v${fresh.version}`);
      } catch (e) {
        const msg = describeError(e);
        dispatch({ type: 'settings.error', message: msg });
        message.error(`重置失败:${msg}`);
      }
    },
    [dispatch],
  );

  // 保存某个 agent 的修改
  const save = useCallback(
    async (name: string): Promise<void> => {
      const draft = state.settings.drafts[name];
      if (!draft) return;
      if (!draft.dirty) {
        // 没改动就不调接口 避免无谓的版本碰撞
        return;
      }
      // 简单前置校验:对齐后端规则
      if (!draft.displayName.trim()) {
        message.error('显示名不能为空');
        return;
      }
      if (draft.baseUrl.trim().length < 8) {
        message.error('Base URL 至少 8 个字符');
        return;
      }
      if (!draft.model.trim()) {
        message.error('模型 ID 不能为空');
        return;
      }
      if (draft.prompt.trim().length < 5) {
        message.error('System Prompt 至少 5 个字');
        return;
      }
      // 仅当用户真的改过 api_key 才校验长度并写入 body
      if (draft.apiKeyDirty && draft.apiKey.length < 4) {
        message.error('API Key 至少 4 个字符');
        return;
      }
      dispatch({ type: 'settings.saving.start' });
      try {
        const body: UpdateAgentRequest = {
          display_name: draft.displayName.trim(),
          base_url: draft.baseUrl.trim(),
          model: draft.model.trim(),
          available_models: draft.availableModels,
          prompt: draft.prompt,
          provider_type: draft.providerType,
          expected_version: draft.version,
        };
        if (draft.apiKeyDirty) {
          body.api_key = draft.apiKey;
        }
        const updated = await updateAgent(name, body);
        // PUT 返回 UpdateAgentResponse 只含 name/version/reloaded，
        // 不含 base_url/api_key/model 等完整字段。
        // 后端没有 GET /api/agents/{name} 接口，改用列表接口拿完整数据。
        const list = await getAgents();
        const full = (list.agents ?? []).find((a) => a.name === name);
        if (full) {
          dispatch({ type: 'settings.saved', agent: full });
          message.success(`已保存 当前版本 v${full.version}`);
        } else {
          // 极小概率找不到，降级兜底：用 draft 现有值 + 新 version 更新
          dispatch({
            type: 'settings.saved',
            agent: {
              name: draft.name,
              display_name: draft.displayName,
              provider_type: draft.providerType,
              base_url: draft.baseUrl,
              api_key: draft.apiKeyMask,
              model: draft.model,
              available_models: draft.availableModels,
              prompt: draft.prompt,
              version: updated.version,
              updated_at: new Date().toISOString(),
              avatar_data_url: draft.avatarDataUrl,
            },
          });
          message.success(`已保存 当前版本 v${updated.version}`);
        }
      } catch (e) {
        const status = parseHttpStatus(e);
        const msg = describeError(e);
        if (status === 409) {
          message.warning('版本冲突 已被他人改动 请重置后再编辑');
        } else if (status === 404) {
          message.warning('该 agent 已不存在 请刷新');
        } else {
          message.error(`保存失败:${msg}`);
        }
        dispatch({ type: 'settings.error', message: msg });
      }
    },
    [state.settings.drafts, dispatch],
  );

  // 切换 judge 指针
  const setJudge = useCallback(
    async (target: string): Promise<void> => {
      try {
        await updateJudge(target);
        dispatch({ type: 'settings.judge.set', target });
        const draft = state.settings.drafts[target];
        const label = draft?.displayName || target;
        message.success(`Judge 已切换到 ${label}`);
      } catch (e) {
        const msg = describeError(e);
        dispatch({ type: 'settings.error', message: msg });
        message.error(`切换 Judge 失败:${msg}`);
      }
    },
    [dispatch, state.settings.drafts],
  );

  // 创建一个新 agent
  const createAgent = useCallback(
    async (body: CreateAgentRequest): Promise<boolean> => {
      try {
        const agent = await apiCreateAgent(body);
        dispatch({ type: 'settings.agent.created', agent });
        message.success(`已新增数字员工:${agent.display_name || agent.name}`);
        return true;
      } catch (e) {
        const status = parseHttpStatus(e);
        const msg = describeError(e);
        if (status === 409) {
          message.warning('已存在同名 agent 请换一个名称');
        } else {
          message.error(`新增失败:${msg}`);
        }
        return false;
      }
    },
    [dispatch],
  );

  // 删除 agent 409 时给出 judge 引用提示
  const removeAgent = useCallback(
    async (name: string): Promise<boolean> => {
      try {
        await apiDeleteAgent(name);
        dispatch({ type: 'settings.agent.deleted', name });
        message.success(`已删除 agent:${name}`);
        return true;
      } catch (e) {
        const status = parseHttpStatus(e);
        const msg = describeError(e);
        if (status === 409) {
          message.warning('该 agent 是当前 Judge 请先切换 Judge 再删除');
        } else if (status === 404) {
          message.warning('该 agent 已不存在');
          dispatch({ type: 'settings.agent.deleted', name });
        } else {
          message.error(`删除失败:${msg}`);
        }
        return false;
      }
    },
    [dispatch],
  );

  // 上传头像  独立路径  不走 save 不影响 dirty
  // 后端 413 = 文件过大  415 = 格式不支持  404 = agent 不存在
  const uploadAvatar = useCallback(
    async (name: string, file: File): Promise<boolean> => {
      try {
        const updated = await uploadAgentAvatar(name, file);
        dispatch({
          type: 'settings.agent.avatar.set',
          agentName: name,
          avatarDataUrl: updated.avatar_data_url,
        });
        message.success('头像已更新');
        return true;
      } catch (e) {
        const status = parseHttpStatus(e);
        const msg = describeError(e);
        if (status === 413) {
          message.error('头像不能超过 2MB');
        } else if (status === 415) {
          message.error('仅支持 PNG / JPEG / WebP / GIF 格式');
        } else if (status === 404) {
          message.warning('该 agent 已不存在 请刷新');
        } else {
          message.error(`头像上传失败:${msg}`);
        }
        return false;
      }
    },
    [dispatch],
  );

  // 删除头像 走独立路径不影响 dirty
  const removeAvatar = useCallback(
    async (name: string): Promise<boolean> => {
      try {
        await deleteAgentAvatar(name);
        dispatch({
          type: 'settings.agent.avatar.set',
          agentName: name,
          avatarDataUrl: null,
        });
        message.success('头像已移除');
        return true;
      } catch (e) {
        const msg = describeError(e);
        message.error(`头像移除失败:${msg}`);
        return false;
      }
    },
    [dispatch],
  );

  return {
    state: state.settings,
    openDrawer,
    closeDrawer,
    switchTab,
    setDraftField,
    reset,
    save,
    setJudge,
    createAgent,
    removeAgent,
    uploadAvatar,
    removeAvatar,
  };
}
