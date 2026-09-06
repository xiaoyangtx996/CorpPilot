import { useEffect, useRef, useState } from 'react';
import { ModelRunReconciliation } from './ModelRunReconciliation';
import { api, type ModelReconciliationRecord, type Agent, type Conversation, type Message, type ReplyRun, type ReplyRuntime } from './api';

const labels = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', unknown: '结果未知' };
const terminal = (run: ReplyRun) => !['queued', 'running'].includes(run.state);
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败';

export function ReplyRuns({ conversation, agents, source, requests, onCompleted, onSettings }: {
  conversation: Conversation; agents: Agent[]; source: Message | null; requests: Record<string, string>;
  onCompleted: () => void; onSettings: () => void;
}) {
  const [checkId, setCheckId] = useState(''), [checkedRuns, setCheckedRuns] = useState<Record<string, ModelReconciliationRecord>>({}), [storageError, setStorageError] = useState('');
  const [, refreshRequest] = useState(0);
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
  useEffect(() => {
    if (!source || !agentId) return;
    const key = `${source.id}:${agentId}`;
    try {
      const raw = sessionStorage.getItem(`corppilot.reply-renewed.v1.${conversation.id}.${key}`);
      if (raw) { const saved = JSON.parse(raw); if (saved.source_message_id !== source.id || saved.agent_id !== agentId || typeof saved.request_id !== 'string' || !/^[0-9a-f-]{36}$/.test(saved.request_id)) throw Error(); requests[key] = saved.request_id; refreshRequest(n => n + 1); }
      setStorageError('');
    } catch { setStorageError('无法恢复原新回复请求，暂不能调用模型。'); }
  }, [source?.id, agentId]);
  async function prepareNew(run: ReplyRun) {
    if (writing.current || !checkedRuns[run.id]) return;
    writing.current = true; setBusy(true);
    try {
      const receipt = await api<ModelReconciliationRecord | null>(`/runs/${run.id}/reconciliation`);
      if (!active.current) return;
      if (!receipt || receipt.run_id !== run.id || !receipt.local_request_stopped || !receipt.provider_effects_checked) throw Error('尚未读回原调用核查声明');
      const key = `${run.source_message_id}:${run.agent_id}`, payload = { source_message_id: run.source_message_id, agent_id: run.agent_id, request_id: crypto.randomUUID() };
      const stored = sessionStorage.getItem(`corppilot.reply-renewed.v1.${conversation.id}.${key}`);
      const storedId = stored ? JSON.parse(stored).request_id : null;
      if (requests[key] && requests[key] !== run.request_id || storedId && storedId !== run.request_id || latestRuns.current.find(row => row.source_message_id === run.source_message_id && row.agent_id === run.agent_id)?.id !== run.id) throw Error('已有另一回复请求，请先在原消息下核对，不能覆盖');
      sessionStorage.setItem(`corppilot.reply-renewed.v1.${conversation.id}.${key}`, JSON.stringify(payload));
      requests[key] = payload.request_id; refreshRequest(n => n + 1); setError('已准备新的请求 ID；请在原 Owner 消息下选择同一成员，再明确确认调用模型。尚未发送。');
    } catch (error) { if (active.current) setError(failure(error)); }
    finally { writing.current = false; if (active.current) setBusy(false); }
  }
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
    if (!source || !loaded || writing.current || storageError || conversation.archived || !members.some(agent => agent.id === agentId)) return;
    const key = `${source.id}:${agentId}`;
    const previous = runs.find(run => run.source_message_id === source.id && run.agent_id === agentId && (!requests[key] || run.request_id === requests[key]));
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
  const existing = source ? runs.find(run => run.source_message_id === source.id && run.agent_id === agentId && (!requests[`${source.id}:${agentId}`] || run.request_id === requests[`${source.id}:${agentId}`])) : undefined;
  return <section ref={panel} className="reply-runs" aria-label="Agent 回复运行记录">
    <header><h2>Agent 回复</h2><button type="button" disabled={loading} onClick={() => void load()}>{loading ? '读取中…' : '刷新运行记录'}</button></header>
    {source ? <div className="reply-request"><p>回复这条 Owner 消息：<span>{source.content}</span></p><label>选择一位启用成员<select value={agentId} disabled={busy || conversation.archived} onChange={event => { setAgentId(event.target.value); setError(''); }}><option value="">请选择 Agent</option>{members.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <p className="muted">确认后仅该 Agent 调用已配置模型回复一次，可能产生服务商费用。不会自动让其他成员继续回复。</p>
      {!members.length && <p className="muted">暂无启用成员，请先调整成员或启用身份。</p>}
      <button type="button" className="primary" disabled={busy || !!storageError || !loaded || !agentId || conversation.archived || !members.some(agent => agent.id === agentId)} onClick={() => void submit()}>{busy ? '提交中…' : existing ? '查询原回复请求（不新建）' : (source && requests[`${source.id}:${agentId}`]) ? '确认或核对回复（同一请求 ID）' : '确认调用模型回复'}</button>
    </div> : <p className="muted">在一条已保存的 Owner 消息下点击“请 Agent 回复”，再选择成员并确认。</p>}
    {conversation.archived && <p className="archive-notice">会话已归档，不能发起新回复。</p>}
    {error && <p className="error" role="alert">{error}</p>}{storageError && <p className="error" role="alert">{storageError}</p>}
    {readError && <p className="error" role="alert">运行记录读取失败：{readError}。已有状态可能过期，请手动刷新。</p>}
    {runtimeError && <p className="error" role="alert">调度状态读取失败：{runtimeError}</p>}
    {runtime && <p className="muted">控制服务：{runtime.running ? '运行中' : '未运行'} · 当前请求 {runtime.active_requests}{runtime.error ? ` · ${runtime.error}` : ''}</p>}
    <button type="button" className="text-button" onClick={onSettings}>打开模型设置</button>
    {loaded && !runs.length && <p className="muted">暂无回复运行记录</p>}
    <div className="run-list">{runs.map(run => <article className="run-record" key={run.id}><header><strong>{agents.find(agent => agent.id === run.agent_id)?.name ?? 'Agent'}</strong><span>{labels[run.state]} · {run.state}</span></header>
      <p className="muted">模型：{run.model ?? '尚未知'} · 输入 token：{run.usage?.prompt_tokens ?? '未知'} · 输出 token：{run.usage?.completion_tokens ?? '未知'}</p>
      <small>Run {run.id}</small>
      {run.error && <p className="error">{run.error}</p>}
      {run.state === 'unknown' && <section><p>实际结果未知，未自动重发；请核查本地请求、供应商结果及费用。</p><button disabled={busy} onClick={() => setCheckId(run.id)}>核查此模型调用</button>{checkedRuns[run.id] && <button disabled={busy || !!storageError} onClick={() => void prepareNew(run)}>结束原请求查看并准备新回复</button>}</section>}
      {run.state === 'failed' && <p className="muted">本次失败，不会自动重试。</p>}
      {run.state === 'running' && <p className="muted">模型请求已开始，无法保证停止，因此不提供取消。</p>}
      {run.state === 'queued' && <button type="button" disabled={busy} onClick={() => void cancel(run)}>取消排队</button>}
    </article>)}</div>
    {checkId && <ModelRunReconciliation runId={checkId} onClose={() => setCheckId('')} onVerified={row => setCheckedRuns(current => ({ ...current, [row.run_id]: row }))} />}
  </section>;
}
