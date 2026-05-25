import { Button } from 'antd';
import {
  ArrowRight,
  ChevronRight,
  CircleHelp,
  Clock,
  FilePenLine,
  ListTodo,
  MessageSquareText,
  Search,
  SlidersHorizontal,
  Sparkles,
  User,
} from 'lucide-react';
import { Flexbox } from 'react-layout-kit';
import type { WorkbenchView } from '../../state/types';
import LobeRecommendCard from './LobeRecommendCard';

export interface LobeTaskViewProps {
  onOpenChat: () => void;
  onNavigate: (view: WorkbenchView) => void;
}

export default function LobeTaskView({
  onOpenChat,
  onNavigate,
}: LobeTaskViewProps) {
  return (
    <Flexbox width={'100%'} height={'100%'} style={{ overflowY: 'auto' }}>
      <Flexbox width={'100%'} align="center" style={{ padding: '24px 0 16vh' }}>
        <div style={{ width: 'min(960px, 100%)', paddingInline: 16 }}>
          <Flexbox gap={28}>
            {/* 顶部导航：全部任务 + 切换按钮 */}
            <Flexbox horizontal align="center" justify="space-between" gap={12} width={'100%'}>
              <div
                style={{
                  color: 'rgba(15, 23, 42, 0.92)',
                  fontSize: 14,
                  fontWeight: 600,
                }}
              >
                全部任务
              </div>
              <Flexbox horizontal gap={6}>
                <Button shape="circle" size="small" type="text" icon={<Search size={14} />} />
                <Button shape="circle" size="small" type="text" icon={<SlidersHorizontal size={14} />} />
              </Flexbox>
            </Flexbox>

            {/* 标题区 */}
            <Flexbox gap={6}>
              <h1
                style={{
                  color: 'rgba(15, 23, 42, 0.92)',
                  fontSize: 28,
                  fontWeight: 700,
                  margin: 0,
                  lineHeight: 1.25,
                }}
              >
                今天想搞定点什么？
              </h1>
              <p
                style={{
                  color: 'rgba(51, 65, 85, 0.72)',
                  fontSize: 14,
                  margin: 0,
                  lineHeight: 1.6,
                }}
              >
                当前项目真实的问答和配置能力已经就位，任务编排和自动化功能后续接入。
              </p>
            </Flexbox>

            {/* 输入区卡片 */}
            <div
              style={{
                background: '#fff',
                border: '1px solid #e5e7eb',
                borderRadius: 18,
                boxShadow: '0 10px 24px rgba(15, 23, 42, 0.05)',
                padding: 16,
              }}
            >
              <Flexbox gap={12}>
                {/* 输入内容区 */}
                <div
                  style={{
                    color: 'rgba(71, 85, 105, 0.56)',
                    fontSize: 14,
                    lineHeight: 1.7,
                    minHeight: 46,
                    padding: '6px 0',
                  }}
                >
                  添加描述…
                </div>

                {/* 底部工具栏 */}
                <Flexbox horizontal align="center" justify="space-between" gap={12} width={'100%'}>
                  <Flexbox horizontal align="center" gap={8}>
                    {/* 优先级入口 */}
                    <div
                      style={{
                        alignItems: 'center',
                        border: '1px solid #e5e7eb',
                        borderRadius: 999,
                        color: 'rgba(71, 85, 105, 0.72)',
                        display: 'inline-flex',
                        fontSize: 13,
                        gap: 6,
                        height: 30,
                        padding: '0 10px',
                        cursor: 'not-allowed',
                        opacity: 0.6,
                      }}
                    >
                      <CircleHelp size={14} />
                      <span>无优先级</span>
                      <ChevronRight size={12} />
                    </div>
                    {/* 负责人入口 */}
                    <div
                      style={{
                        alignItems: 'center',
                        border: '1px solid #e5e7eb',
                        borderRadius: 999,
                        color: 'rgba(71, 85, 105, 0.72)',
                        display: 'inline-flex',
                        fontSize: 13,
                        gap: 6,
                        height: 30,
                        padding: '0 10px',
                        cursor: 'not-allowed',
                        opacity: 0.6,
                      }}
                    >
                      <User size={14} />
                      <span>负责人</span>
                      <ChevronRight size={12} />
                    </div>
                  </Flexbox>
                  <Button type="primary" shape="round" disabled>
                    创建任务
                  </Button>
                </Flexbox>
              </Flexbox>
            </div>

            {/* 模板区：标题 + 换一批 */}
            <Flexbox gap={12}>
              <Flexbox horizontal align="center" justify="space-between" gap={8} width={'100%'}>
                <div style={{ color: 'rgba(71, 85, 105, 0.72)', fontSize: 12 }}>
                  为你推荐的模板
                </div>
                <Button type="text" size="small" onClick={onNavigate.bind(null, 'tasks')}>
                  换一批
                </Button>
              </Flexbox>

              <div
                style={{
                  display: 'grid',
                  gap: 12,
                  gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))',
                }}
              >
                <LobeRecommendCard
                  title="迭代复盘周报"
                  description="每周五下班前帮你拉本周迭代数据：完成率、逾期项、新增 Bug"
                  tag="模板"
                  actionLabel="添加任务"
                  icon={Clock}
                  onAction={() => onOpenChat()}
                />
                <LobeRecommendCard
                  title="站会简报"
                  description="每天站会前 15 分钟帮你拉一份进度简报：今日重点、阻塞项、昨日完成"
                  tag="模板"
                  actionLabel="添加任务"
                  icon={ListTodo}
                  onAction={() => onOpenChat()}
                />
                <LobeRecommendCard
                  title="用户访谈排期"
                  description="每周一帮你梳理本周访谈：谁、什么时候、问题列表准备好没"
                  tag="模板"
                  actionLabel="添加任务"
                  icon={MessageSquareText}
                  onAction={() => onOpenChat()}
                />
                <LobeRecommendCard
                  title="竞品更新追踪"
                  description="告诉我 3-5 个竞品，每天看他们的更新日志、新功能、官网变化"
                  tag="模板"
                  actionLabel="添加任务"
                  icon={Search}
                  onAction={() => onOpenChat()}
                />
                <LobeRecommendCard
                  title="核心指标日报"
                  description="告诉我要看的指标（DAU、留存、转化），每天早上自动同步变化"
                  tag="模板"
                  actionLabel="添加任务"
                  icon={Sparkles}
                  onAction={() => onOpenChat()}
                />
                <LobeRecommendCard
                  title="PRD 评审提醒"
                  description="每周五盘点本周该评审的 PRD 和决策项，别让文档压在草稿箱"
                  tag="模板"
                  actionLabel="添加任务"
                  icon={FilePenLine}
                  onAction={() => onOpenChat()}
                />
              </div>
            </Flexbox>

            {/* 底部回到聊天入口 */}
            <Button
              type="text"
              icon={<ArrowRight size={14} />}
              onClick={onOpenChat}
              style={{ alignSelf: 'flex-start' }}
            >
              打开聊天工作台
            </Button>
          </Flexbox>
        </div>
      </Flexbox>
    </Flexbox>
  );
}