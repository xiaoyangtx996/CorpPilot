import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type Conversation, type Task, type TaskExecution, type ReplyRun, type ReplyRuntime } from './api';
import { TaskExecutions } from './TaskExecutions';
import { ExecutionUsage } from './ExecutionUsage';

type Page<T> = { items: T[]; total: number; has_more: boolean };
type Activity = { agent_id: string; tasks: Page<Task>; executions: Page<TaskExecution & { task_title: string; conversation_id: string; artifact_count: number; review_decision: 'approved' | 'rejected' | null }>; model_runs: Page<ReplyRun & { kind: 'reply' | 'planning' | 'retrospective' | 'peer_review' }> };
const labels: Record<string, string> = { queued: '排队', running: '运行中', stopping: '等待退出', awaiting_review: '执行已结束', failed: '失败', cancelled: '已取消', unknown: '结果未知', superseded: '需求已过期', completed: '已完成' };
const kinds = { reply: '回复', planning: '规划', retrospective: '复盘', peer_review: '成员评议' };
const failure = (error: unknown) => error instanceof Error ? error.message : '读取失败';
function range<T>(page: Page<T>) { return `显示 ${page.items.length} / ${page.total} 条${page.has_more ? '，仅最近50条' : ''}`; }

export function AgentActivity({ agent, agents, onOpenConversation }: { agent: Agent; agents: Agent[]; onOpenConversation: (row: Conversation) => void }) {
  const version = useRef(0), opening = useRef(false);
  const [value, setValue] = useState<Activity | null>(null);
  const [runtime, setRuntime] = useState<ReplyRuntime | null>(null);
  const [error, setError] = useState(''), [runtimeError, setRuntimeError] = useState('');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<{ task: Task; conversation: Conversation } | null>(null);
  async function load() {
    const request = ++version.current;
    setLoading(true); setError(''); setRuntimeError(''); setValue(null); setRuntime(null);
    const replies = await Promise.allSettled([api<Activity>(`/agents/${agent.id}/activity`), api<ReplyRuntime>('/cli-runtime')]);
    if (request !== version.current) return;
    const activity = replies[0];
    if (activity.status === 'fulfilled') {
      if (activity.value.agent_id !== agent.id) setError('活动回执的身份不一致，请重新读取。');
      else setValue(activity.value);
    } else setError(failure(activity.reason));
    const status = replies[1];
    if (status.status === 'fulfilled') setRuntime(status.value); else setRuntimeError(failure(status.reason));
    setLoading(false);
  }
  useEffect(() => { void load(); return () => { version.current++; }; }, [agent.id]);
  async function open(conversationId: string, taskId?: string) {
    if (opening.current) return;
    opening.current = true; setBusy(true); setError('');
    const request = version.current;
    try {
      const conversation = await api<Conversation>(`/conversations/${conversationId}`);
      if (conversation.id !== conversationId) throw Error('会话关联不一致');
      if (taskId) {
        const task = await api<Task>(`/tasks/${taskId}`);
        if (task.id !== taskId || task.conversation_id !== conversationId) throw Error('任务关联不一致');
        if (request === version.current) setDetail({ task, conversation });
      } else if (request === version.current) onOpenConversation(conversation);
    } catch (error) { if (request === version.current) setError(failure(error)); }
    finally { opening.current = false; if (request === version.current) setBusy(false); }
  }
  return <section aria-label={`${agent.name}的活动`} aria-busy={loading}>
    <h3>任务与实际活动</h3>
    <p className="muted">这是 Owner 的只读观察，不会把其他身份资料发送给 Agent。各类按更新时间显示最近50条，刷新获取当前状态。</p>
    <button disabled={loading || busy} onClick={() => void load()}>{loading ? '读取活动中…' : '刷新 Agent 活动'}</button>
    {error && <p className="error" role="alert">{error}。未确认活动为空。</p>}
    {value && <>
      <details open><summary>当前负责的任务 · {range(value.tasks)}</summary>
        {!value.tasks.items.length && <p>尚无当前负责的任务。</p>}
        {value.tasks.items.map(task => <article className="task-card" key={task.id}>
          <h4>{task.title}</h4><small>当前需求 v{task.requirement_version} · {task.id}</small>
          <details><summary>目标与验收要求</summary><p>{task.scope}</p><p>{task.acceptance}</p></details>
          <button disabled={busy} onClick={() => void open(task.conversation_id, task.id)}>查看该任务全部执行</button>
          <button disabled={busy} onClick={() => void open(task.conversation_id)}>打开所属会话</button>
        </article>)}
      </details>
      <details open><summary>此身份的实际执行 · {range(value.executions)}</summary>
        {!value.executions.items.length && <p>尚无执行记录。</p>}
        {value.executions.items.map(run => <article className="task-card" key={run.id}>
          <h4>{run.task_title} · {labels[run.state] ?? run.state}</h4>
          <small>执行 {run.id} · 第{run.attempt}次 · 当次需求 v{run.requirement_version}</small>
          <p>退出码：{run.exit_code ?? '未观测'} · 已保存成果 {run.artifact_count} 个</p>
          <ExecutionUsage run={run} />
          <p>当次 Owner 验收：{run.review_decision === 'approved' ? '已批准' : run.review_decision === 'rejected' ? '已拒绝' : '未批准'}</p>
          {run.summary && <p>{run.summary}</p>}
          <button disabled={busy} onClick={() => void open(run.conversation_id, run.task_id)}>查看该任务全部执行</button>
        </article>)}
      </details>
      <details><summary>此身份的模型活动 · {range(value.model_runs)}</summary>
        {!value.model_runs.items.length && <p>尚无模型活动记录。</p>}
        {value.model_runs.items.map(run => <article className="task-card" key={run.id}>
          <h4>{kinds[run.kind]} · {labels[run.state] ?? run.state}</h4><small>{run.id}</small>
          <p>模型：{run.model ?? '待回执'} · 输入/输出 token：{run.usage?.prompt_tokens ?? '未知'} / {run.usage?.completion_tokens ?? '未知'}</p>
          {run.error && <p className="error">{run.error}</p>}
          <button disabled={busy} onClick={() => void open(run.conversation_id)}>打开所属会话</button>
        </article>)}
      </details>
      <p className="muted">Token 是用量，费用尚未核算。这里只展示任务要求和活动回执，不是完整模型输入或工具调用明细。</p>
    </>}
    <p>全局 CLI 队列：{runtime ? `${runtime.running ? '控制服务运行中' : '控制服务已关闭'} · 活动实例 ${runtime.active_requests}` : '尚未读回'}</p>
    {runtime?.error && <p className="error">全局调度提示：{runtime.error}</p>}
    {runtimeError && <p className="error" role="alert">全局队列读取失败：{runtimeError}</p>}
    {detail && <TaskExecutions {...detail} agents={agents} onClose={() => setDetail(null)} />}
  </section>;
}
