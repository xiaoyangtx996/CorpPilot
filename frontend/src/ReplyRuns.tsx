import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type Conversation, type Message, type ReplyRun, type ReplyRuntime } from './api';

const labels = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', unknown: '结果未知' };
const terminal = (run: ReplyRun) => !['queued', 'running'].includes(run.state);
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败';

export function ReplyRuns({ conversation, agents, source, requests, onCompleted, onSettings }: {
  conversation: Conversation; agents: Agent[]; source: Message | null; requests: Record<string, string>;
  onCompleted: () => void; onSettings: () => void;
}) {
  const [runs, setRuns] = useState<ReplyRun[]>([]);
  const latestRuns = useRef<ReplyRun[]>([]);
  latestRuns.current = runs;
  const [agentId, setAgentId] = useState('');
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [readError, setReadError] = useState('');
  const [runtime, setRuntime] = useState<ReplyRuntime | null>(null);
  const [runtimeError, setRuntimeError] = useState('');
  const active = useRef(true);
  const reading = useRef(false);
  const writing = useRef(false);
  const knownCompleted = useRef(new Set<string>());
  const complete = useRef(onCompleted);
  complete.current = onCompleted;
  const panel = useRef<HTMLElement>(null);
  const members = agents.filter(agent => agent.enabled && conversation.member_ids.includes(agent.id));
  const pending = runs.some(run => !terminal(run));
  useEffect(() => {
    setAgentId(''); setError('');
    if (source) { panel.current?.scrollIntoView({ block: 'nearest' }); panel.current?.querySelector('select')?.focus(); }
  }, [source?.id]);
  function merge(incoming: ReplyRun[]) {
    if (!active.current) return;
    setRuns(current => {
      const values = new Map(current.map(run => [run.id, run]));
      for (const run of incoming) {
        const previous = values.get(run.id);
        // Terminal runs cannot return to queued/running when an older request arrives late.
        if (!previous || (!terminal(previous) && (terminal(run) || (run.updated_at >= previous.updated_at && !(previous.state === 'running' && run.state === 'queued'))))) values.set(run.id, run);
      }
      return [...values.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
    });
    for (const run of incoming) {
      if (run.state === 'completed' && !knownCompleted.current.has(run.id)) {
        knownCompleted.current.add(run.id); complete.current();
      }
    }
  }
  async function load() {
    if (reading.current) return;
    reading.current = true; setLoading(true);
    const results = await Promise.allSettled([
      api<ReplyRun[]>(`/conversations/${conversation.id}/runs`), api<ReplyRuntime>('/runtime'),
    ]);
    if (active.current) {
      const [runResult, runtimeResult] = results;
      if (runResult.status === 'fulfilled') {
        const incomingIds = new Set(runResult.value.map(run => run.id));
        const missing = latestRuns.current.filter(run => !terminal(run) && !incomingIds.has(run.id));
        merge(runResult.value); setLoaded(true); setReadError('');
        if (missing.length) {
          const recovered = await Promise.allSettled(missing.map(run => api<ReplyRun>(`/runs/${run.id}`)));
          if (!active.current) { reading.current = false; return; }
          const failures: string[] = [];
          recovered.forEach((result, index) => {
            if (result.status === 'fulfilled') merge([result.value]);
            else failures.push(`${missing[index].id}：${failure(result.reason)}`);
          });
          if (failures.length) setReadError(`部分活动 Run 的独立查询失败，保留上次状态并继续轮询。${failures.join('；')}`);
        }
      }
      else setReadError(failure(runResult.reason));
      if (runtimeResult.status === 'fulfilled') { setRuntime(runtimeResult.value); setRuntimeError(''); }
      else setRuntimeError(failure(runtimeResult.reason));
      setLoading(false);
    }
    reading.current = false;
  }
  useEffect(() => { active.current = true; void load(); return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => { void load(); }, 1000);
    return () => window.clearInterval(timer);
  }, [pending]);
  async function submit() {
    if (!source || !loaded || writing.current || conversation.archived || !members.some(agent => agent.id === agentId)) return;
    const key = `${source.id}:${agentId}`;
    const previous = runs.find(run => run.source_message_id === source.id && run.agent_id === agentId);
    requests[key] ??= previous?.request_id ?? `reply:${source.id}:${agentId}`;
    writing.current = true; setBusy(true); setError('');
    try {
      const run = previous ? await api<ReplyRun>(`/runs/${previous.id}`)
        : await api<ReplyRun>(`/conversations/${conversation.id}/runs`, 'POST', { agent_id: agentId, source_message_id: source.id, request_id: requests[key] });
      merge([run]);
    } catch (error) { if (active.current) setError(`${failure(error)}。未自动重试；再次点击将沿用同一请求 ID 查询或创建原请求。`); }
    finally { writing.current = false; if (active.current) setBusy(false); }
  }
  async function cancel(run: ReplyRun) {
    if (writing.current || run.state !== 'queued') return;
    writing.current = true; setBusy(true); setError('');
    try { merge([await api<ReplyRun>(`/runs/${run.id}/cancel`, 'POST', {})]); }
    catch (error) { if (active.current) setError(failure(error)); }
    finally { writing.current = false; if (active.current) { setBusy(false); void load(); } }
  }
  const existing = source ? runs.find(run => run.source_message_id === source.id && run.agent_id === agentId) : undefined;
  return <section ref={panel} className="reply-runs" aria-label="Agent 回复运行记录">
    <header><h2>Agent 回复</h2><button type="button" disabled={loading} onClick={() => void load()}>{loading ? '读取中…' : '刷新运行记录'}</button></header>
    {source ? <div className="reply-request"><p>回复这条 Owner 消息：<span>{source.content}</span></p><label>选择一位启用成员<select value={agentId} disabled={busy || conversation.archived} onChange={event => { setAgentId(event.target.value); setError(''); }}><option value="">请选择 Agent</option>{members.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <p className="muted">确认后仅该 Agent 调用已配置模型回复一次，可能产生服务商费用。不会自动让其他成员继续回复。</p>
      {!members.length && <p className="muted">暂无启用成员，请先调整成员或启用身份。</p>}
      <button type="button" className="primary" disabled={busy || !loaded || !agentId || conversation.archived || !members.some(agent => agent.id === agentId)} onClick={() => void submit()}>{busy ? '提交中…' : existing ? '查询原回复请求（不新建）' : (source && requests[`${source.id}:${agentId}`]) ? '重试提交（同一请求）' : '确认调用模型回复'}</button>
    </div> : <p className="muted">在一条已保存的 Owner 消息下点击“请 Agent 回复”，再选择成员并确认。</p>}
    {conversation.archived && <p className="archive-notice">会话已归档，不能发起新回复。</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {readError && <p className="error" role="alert">运行记录读取失败：{readError}。已有状态可能过期，请手动刷新。</p>}
    {runtimeError && <p className="error" role="alert">调度状态读取失败：{runtimeError}</p>}
    {runtime && <p className="muted">控制服务：{runtime.running ? '运行中' : '未运行'} · 当前请求 {runtime.active_requests}{runtime.error ? ` · ${runtime.error}` : ''}</p>}
    <button type="button" className="text-button" onClick={onSettings}>打开模型设置</button>
    {loaded && !runs.length && <p className="muted">暂无回复运行记录</p>}
    <div className="run-list">{runs.map(run => <article className="run-record" key={run.id}><header><strong>{agents.find(agent => agent.id === run.agent_id)?.name ?? 'Agent'}</strong><span>{labels[run.state]} · {run.state}</span></header>
      <p className="muted">模型：{run.model ?? '尚未知'} · 输入 token：{run.usage?.prompt_tokens ?? '未知'} · 输出 token：{run.usage?.completion_tokens ?? '未知'}</p>
      <small>Run {run.id}</small>
      {run.error && <p className="error">{run.error}</p>}
      {run.state === 'unknown' && <p className="muted">实际结果未知，未自动重发；请核实服务商记录，避免重复调用。</p>}
      {run.state === 'failed' && <p className="muted">本次失败，不会自动重试。</p>}
      {run.state === 'running' && <p className="muted">模型请求已开始，无法保证停止，因此不提供取消。</p>}
      {run.state === 'queued' && <button type="button" disabled={busy} onClick={() => void cancel(run)}>取消排队</button>}
    </article>)}</div>
  </section>;
}
