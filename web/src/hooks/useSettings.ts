// Settings 抽屉 hook:封装拉取、保存、judge 切换、provider profile CRUD 的副作用与 dispatch
// 通过 antd message.* 上报错误与成功 把网络细节挡在外面
import { useCallback } from 'react';
import { message } from 'antd';
import {
  createProfile,
  deleteProfile,
  getAgents,
  listProfiles,
  updateAgent,
  updateJudge,
  updateProfile,
} from '../api/http';
import { convertAgentView } from '../state/converters';
import { useChat } from '../state/ChatContext';
import type {
  CreateProfileRequest,
  UpdateProfileRequest,
} from '../state/types';

// 抽出错误信息的人话描述,优先用 Error.message,其次回退到字符串化
function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// 从 Error.message 中提取 HTTP 状态码  失败返回 null
function parseHttpStatus(err: unknown): number | null {
  if (!(err instanceof Error)) return null;
  const m = /HTTP (\d{3}):/.exec(err.message);
  return m ? Number(m[1]) : null;
}

export function useSettings() {
  const { state, dispatch } = useChat();

  // 内部:统一拉取 profile 列表 失败给 message 提示
  const loadProfiles = useCallback(async (): Promise<void> => {
    dispatch({ type: 'settings.profiles.loading.start' });
    try {
      const profiles = await listProfiles();
      dispatch({ type: 'settings.profiles.loaded', profiles });
    } catch (e) {
      const msg = describeError(e);
      dispatch({ type: 'settings.error', message: msg });
      message.error(`加载 provider 配置失败:${msg}`);
    }
  }, [dispatch]);

  // 打开抽屉:并发拉 agents + profiles
  const openDrawer = useCallback(async (): Promise<void> => {
    dispatch({ type: 'settings.open' });
    dispatch({ type: 'settings.loading.start' });
    dispatch({ type: 'settings.profiles.loading.start' });
    try {
      // 注意:agents 与 profiles 互不依赖 用 Promise.allSettled 一起拉
      const [agentsRes, profilesRes] = await Promise.allSettled([
        getAgents(),
        listProfiles(),
      ]);

      if (agentsRes.status === 'fulfilled') {
        const data = agentsRes.value;
        // 后端返回 snake_case  这里统一过 convertAgentView 转 camelCase
        const agents = (data.agents ?? []).map((a) => convertAgentView(a));
        dispatch({
          type: 'settings.loaded',
          agents,
          judgeTarget: data.judge_target,
        });
      } else {
        const msg = describeError(agentsRes.reason);
        dispatch({ type: 'settings.error', message: msg });
        message.error(`加载 agent 配置失败:${msg}`);
      }

      if (profilesRes.status === 'fulfilled') {
        dispatch({ type: 'settings.profiles.loaded', profiles: profilesRes.value });
      } else {
        const msg = describeError(profilesRes.reason);
        dispatch({ type: 'settings.error', message: msg });
        message.error(`加载 provider 配置失败:${msg}`);
      }
    } catch (e) {
      const msg = describeError(e);
      dispatch({ type: 'settings.error', message: msg });
      message.error(`加载配置失败:${msg}`);
    }
  }, [dispatch]);

  // 关闭抽屉
  const closeDrawer = useCallback((): void => {
    dispatch({ type: 'settings.close' });
  }, [dispatch]);

  // 编辑表单字段:仅写本地草稿,不发请求
  const updateDraft = useCallback(
    (name: string, field: 'model' | 'prompt', value: string): void => {
      dispatch({ type: 'settings.draft.update', name, field, value });
    },
    [dispatch],
  );

  // 切换 agent 关联的 provider profile:仅写本地草稿
  const setAgentProfile = useCallback(
    (agentName: string, profileName: string): void => {
      dispatch({ type: 'settings.draft.profile', name: agentName, profileName });
    },
    [dispatch],
  );

  // 重置某个 agent 的草稿到当前服务端版本
  // 实现方式:再次拉一次列表覆盖 drafts;调用面较窄,直接复用 openDrawer 路径
  const reset = useCallback(
    async (name: string): Promise<void> => {
      try {
        const data = await getAgents();
        const agents = (data.agents ?? []).map((a) => convertAgentView(a));
        const target = agents.find((a) => a.name === name);
        if (!target) {
          message.error(`未找到 agent:${name}`);
          return;
        }
        // 借 settings.loaded 整体刷一遍,逻辑简单一致
        dispatch({
          type: 'settings.loaded',
          agents,
          judgeTarget: data.judge_target,
        });
        message.success(`${name} 已重置到 v${target.version}`);
      } catch (e) {
        const msg = describeError(e);
        dispatch({ type: 'settings.error', message: msg });
        message.error(`重置失败:${msg}`);
      }
    },
    [dispatch],
  );

  // 保存某个 agent 的修改  把 model / prompt / profile_name 一并 PUT
  const save = useCallback(
    async (name: string): Promise<void> => {
      const draft = state.settings.drafts[name];
      if (!draft) return;
      if (!draft.dirty) {
        // 没改动就不调接口,避免无谓的版本碰撞
        return;
      }
      // 简单前置校验:对齐后端 400 规则
      if (!draft.model.trim()) {
        message.error('模型 ID 不能为空');
        return;
      }
      if (draft.prompt.trim().length < 5) {
        message.error('System Prompt 至少 5 个字');
        return;
      }
      if (!draft.profileName.trim()) {
        message.error('请先选择 API 提供商');
        return;
      }
      dispatch({ type: 'settings.saving.start' });
      try {
        const resp = await updateAgent(name, {
          model: draft.model,
          prompt: draft.prompt,
          profile_name: draft.profileName,
          expected_version: draft.version,
        });
        dispatch({ type: 'settings.saved', name, version: resp.version });
        message.success(`已保存 当前版本 v${resp.version}`);
      } catch (e) {
        const msg = describeError(e);
        dispatch({ type: 'settings.error', message: msg });
        message.error(`保存失败:${msg}`);
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
        message.success(`Judge 已切换到 ${target}`);
      } catch (e) {
        const msg = describeError(e);
        dispatch({ type: 'settings.error', message: msg });
        message.error(`切换 Judge 失败:${msg}`);
      }
    },
    [dispatch],
  );

  // 新建 provider profile  api_key 必须是明文
  const createNewProfile = useCallback(
    async (body: CreateProfileRequest): Promise<boolean> => {
      try {
        const profile = await createProfile(body);
        dispatch({ type: 'settings.profiles.upserted', profile });
        message.success(`已新建 provider:${profile.name}`);
        return true;
      } catch (e) {
        const status = parseHttpStatus(e);
        const msg = describeError(e);
        if (status === 409) {
          message.warning('已存在同名 provider 请换一个名称');
        } else {
          message.error(`新建失败:${msg}`);
        }
        return false;
      }
    },
    [dispatch],
  );

  // 更新 provider profile  fields.api_key 不传则保留旧值 传空字符串当清空
  const saveProfile = useCallback(
    async (name: string, fields: UpdateProfileRequest): Promise<boolean> => {
      try {
        const profile = await updateProfile(name, fields);
        dispatch({ type: 'settings.profiles.upserted', profile });
        message.success(`provider 已保存:${name}`);
        return true;
      } catch (e) {
        const status = parseHttpStatus(e);
        const msg = describeError(e);
        if (status === 404) {
          message.warning('该 provider 已不存在 请刷新');
        } else {
          message.error(`保存 provider 失败:${msg}`);
        }
        return false;
      }
    },
    [dispatch],
  );

  // 删除 provider profile  409 时给出引用提示
  const removeProfile = useCallback(
    async (name: string): Promise<boolean> => {
      try {
        await deleteProfile(name);
        dispatch({ type: 'settings.profiles.deleted', name });
        message.success(`已删除 provider:${name}`);
        return true;
      } catch (e) {
        const status = parseHttpStatus(e);
        const msg = describeError(e);
        if (status === 409) {
          message.warning('此 provider 还被 agent 引用 请先切换 agent 的 provider');
        } else if (status === 404) {
          message.warning('该 provider 已不存在');
          dispatch({ type: 'settings.profiles.deleted', name });
        } else {
          message.error(`删除失败:${msg}`);
        }
        return false;
      }
    },
    [dispatch],
  );

  return {
    state: state.settings,
    openDrawer,
    closeDrawer,
    updateDraft,
    setAgentProfile,
    reset,
    save,
    setJudge,
    loadProfiles,
    createNewProfile,
    saveProfile,
    removeProfile,
  };
}
