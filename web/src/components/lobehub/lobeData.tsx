import type { LucideIcon } from 'lucide-react';
import {
  Blocks,
  Bot,
  FilePenLine,
  House,
  ImageIcon,
  LibraryBig,
  ListTodo,
  MessageSquareText,
  Settings2,
  Shapes,
  Sparkles,
  Wrench,
} from 'lucide-react';
import type { WorkbenchView } from '../../state/types';

export interface WorkbenchNavItem {
  key: WorkbenchView;
  label: string;
  icon: LucideIcon;
}

export interface RecommendCardDefinition {
  key: string;
  title: string;
  description: string;
  tag: string;
  icon: LucideIcon;
  actionLabel: string;
  action: 'send' | 'settings' | 'view';
  prompt?: string;
  view?: WorkbenchView;
}

export interface PlaceholderDefinition {
  badge: string;
  title: string;
  description: string;
  highlights: string[];
  primaryActionLabel: string;
  secondaryActionLabel: string;
}

export const PRIMARY_NAV_ITEMS: WorkbenchNavItem[] = [
  { key: 'home', label: '首页', icon: House },
  { key: 'tasks', label: '任务', icon: ListTodo },
  { key: 'page', label: '文稿', icon: FilePenLine },
];

export const FOOTER_NAV_ITEMS: WorkbenchNavItem[] = [
  { key: 'image', label: '生成', icon: Sparkles },
  { key: 'community', label: '社区', icon: Shapes },
  { key: 'resource', label: '资源', icon: LibraryBig },
];

export const HOME_RECOMMEND_GROUPS: RecommendCardDefinition[][] = [
  [
    {
      key: 'multi-agent-chat',
      title: '多 agent 协作问答',
      description: '让多个数字员工同时思考，再从中选择最适合的一条回答。',
      tag: '对话',
      icon: MessageSquareText,
      actionLabel: '立即发问',
      action: 'send',
      prompt: '请从产品、研发、测试三个角度分别分析这个需求的风险和落地方案。',
    },
    {
      key: 'agent-settings',
      title: '管理数字员工',
      description: '统一管理展示名、模型、System Prompt 和 Judge 指向。',
      tag: '助理',
      icon: Bot,
      actionLabel: '打开设置',
      action: 'settings',
    },
    {
      key: 'mcp-settings',
      title: '配置 MCP 工具',
      description: '维护外部工具服务，让 agent 可以调用更丰富的能力。',
      tag: 'MCP',
      icon: Wrench,
      actionLabel: '查看设置',
      action: 'settings',
    },
    {
      key: 'skills-settings',
      title: '维护 Skills',
      description: '调整技能说明和启停状态，让工作流更贴合当前团队场景。',
      tag: 'Skills',
      icon: Settings2,
      actionLabel: '查看设置',
      action: 'settings',
    },
  ],
  [
    {
      key: 'requirements-review',
      title: '需求评审草案',
      description: '快速发起一轮需求评审，让多 agent 给出不同视角的判断。',
      tag: '模板',
      icon: Blocks,
      actionLabel: '发送模板',
      action: 'send',
      prompt: '请作为产品、研发、测试三方一起评审这个需求，并输出分歧点、风险点和建议方案。',
    },
    {
      key: 'chat-entry',
      title: '回到聊天工作台',
      description: '如果你已经有会话，直接回到主聊天页继续当前上下文。',
      tag: '工作台',
      icon: MessageSquareText,
      actionLabel: '进入聊天',
      action: 'view',
      view: 'chat',
    },
    {
      key: 'image-entry',
      title: '查看生成页占位',
      description: '先用和参考界面一致的入口层级承接后续图像和多模态能力。',
      tag: '占位页',
      icon: ImageIcon,
      actionLabel: '打开页面',
      action: 'view',
      view: 'image',
    },
    {
      key: 'resource-entry',
      title: '查看资源页占位',
      description: '先用资源工作台承接后续文件、文稿和知识类能力的扩展入口。',
      tag: '占位页',
      icon: LibraryBig,
      actionLabel: '打开页面',
      action: 'view',
      view: 'resource',
    },
  ],
];

export const PLACEHOLDER_DEFINITIONS: Record<
  Exclude<WorkbenchView, 'home' | 'chat'>,
  PlaceholderDefinition
> = {
  tasks: {
    badge: '任务工作台',
    title: '任务页外壳已对齐，等待真实任务能力接入',
    description: '当前项目真正可用的核心能力还是多 agent 对话、Judge、MCP 和 Skills。这里先按参考界面的视觉层级保留任务入口，后续再接入真实任务流。',
    highlights: ['保留导航层级和入口位置', '可直接回到首页继续发问', '后续可承接任务编排与追踪能力'],
    primaryActionLabel: '返回首页',
    secondaryActionLabel: '打开设置',
  },
  page: {
    badge: '文稿工作台',
    title: '文稿页先做高保真占位，不伪造后端编辑能力',
    description: '现阶段先保留和参考界面一致的页面入口、标题层级和说明卡片。等后端支持文稿存储和编辑后，再把这里接成真实页面。',
    highlights: ['保留参考界面的文稿入口', '说明当前真实能力边界', '未来可承接富文本和知识整理'],
    primaryActionLabel: '返回首页',
    secondaryActionLabel: '打开设置',
  },
  image: {
    badge: '生成工作台',
    title: '生成页已预留位置，后续承接多模态生成能力',
    description: '当前前端优先把和 LobeHub 一致的导航壳、首页和聊天主链路搭好。生成相关能力后续再接入真实模型与任务流。',
    highlights: ['保留生成入口位置', '可以继续沿用当前主题体系', '后续适配图像和视频生成'],
    primaryActionLabel: '返回首页',
    secondaryActionLabel: '打开设置',
  },
  community: {
    badge: '社区工作台',
    title: '社区页先保留视觉和层级，避免做假功能',
    description: '这里先呈现和参考界面一致的工作台入口感，让整体外壳一体化。实际的社区分发、模版市场和共享能力后续单独接入。',
    highlights: ['完整保留社区入口层级', '不伪造尚未存在的后端接口', '后续可承接模版与共享市场'],
    primaryActionLabel: '返回首页',
    secondaryActionLabel: '打开设置',
  },
  resource: {
    badge: '资源工作台',
    title: '资源页先承接工作台入口，后续扩展文件与知识能力',
    description: '当前系统已经有会话、agent、MCP 和 Skills 的基础能力，资源页先保留结构位置，后续适配文稿、文件和知识库资源。',
    highlights: ['统一资源入口位置', '和文稿、生成页面形成一组工作台', '后续可接文件、知识和文稿资产'],
    primaryActionLabel: '返回首页',
    secondaryActionLabel: '打开设置',
  },
};
