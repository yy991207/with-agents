// Settings 抽屉 hook:封装拉取、保存、judge 切换的副作用与 dispatch
// 通过中文提示 message.* 上报错误与成功 把网络细节挡在外面
import { useCallback } from 'react';
import { message } from 'antd';
import { getAgents, updateAgent, updateJudge } from '../api/http';
import { useChat } from '../state/ChatContext';

// 抽出错误信息的人话描述,优先用 Error.message,其次回退到字符串化
function describeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

export function useSettings() {
  const { state, dispatch } = useChat();

  // 打开抽屉:同时去后端拉一份最新配置
  const openDrawer = useCallback(async (): Promise<void> => {
    dispatch({ type: 'settings.open' });
    dispatch({ type: 'settings.loading.start' });
    try {
      const data = await getAgents();
      dispatch({
        type: 'settings.loaded',
        agents: data.agents,
        judgeTarget: data.judge_target,
      });
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

  // 重置某个 agent 的草稿到当前服务端版本
  // 实现方式:再次拉一次列表覆盖 drafts;调用面较窄,直接复用 openDrawer 路径
  const reset = useCallback(
    async (name: string): Promise<void> => {
      try {
        const data = await getAgents();
        // 找到目标 agent,重新写一遍 draft
        const target = data.agents.find((a) => a.name === name);
        if (!target) {
          message.error(`未找到 agent:${name}`);
          return;
        }
        // 借 settings.loaded 整体刷一遍,逻辑简单一致
        dispatch({
          type: 'settings.loaded',
          agents: data.agents,
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

  // 保存某个 agent 的修改
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
      dispatch({ type: 'settings.saving.start' });
      try {
        const resp = await updateAgent(name, {
          model: draft.model,
          prompt: draft.prompt,
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

  return {
    state: state.settings,
    openDrawer,
    closeDrawer,
    updateDraft,
    reset,
    save,
    setJudge,
  };
}
