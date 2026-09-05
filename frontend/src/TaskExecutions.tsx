import { useEffect, useRef, useState } from 'react';
import { ExecutionReview } from './ExecutionReview';
import { api, ApiError, type Agent, type Conversation, type Task, type TaskExecution, type ExecutionRequest, type CliSettingsValue, type ReplyRuntime } from './api';

const labels = { queued: '排队中', running: '运行中', stopping: '正在停止', awaiting_review: '执行已结束', failed: '失败', cancelled: '已取消', unknown: '结果未知', superseded: '需求已过期' };
const active = (run: TaskExecution) => ['queued', 'running', 'stopping'].includes(run.state);
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败';
function decode(raw: string): ExecutionRequest {
  const row = JSON.parse(raw);
  if (row && typeof row === 'object' && Object.hasOwn(row, 'rejected')) { if (typeof row.rejected !== 'boolean') throw new Error('拒绝记录格式无效'); delete row.rejected; }
  if (!row || typeof row !== 'object' || Object.keys(row).sort().join() !== 'expected_version,previous_execution_id,reconciliation_note,request_id' || typeof row.request_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(row.request_id) || !Number.isSafeInteger(row.expected_version) || row.expected_version < 1 || typeof row.reconciliation_note !== 'string' || row.reconciliation_note.length > 2000 || !(row.previous_execution_id === null || typeof row.previous_execution_id === 'string' && !!row.previous_execution_id)) throw new Error('待确认记录格式无效');
  return row;
}

export function TaskExecutions({ task, conversation, agents, onClose }: { task: Task; conversation: Conversation; agents: Agent[]; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const alive = useRef(false), reading = useRef<number | null>(null), writing = useRef(false), serial = useRef(0);
  const key = `corppilot.execution-pending.v1.${task.id}`;
  const [runs, setRuns] = useState<TaskExecution[]>([]);
  const [config, setConfig] = useState<CliSettingsValue | null>(null);
  const [runtime, setRuntime] = useState<ReplyRuntime | null>(null);
  const [pending, setPending] = useState<ExecutionRequest | null>(null);
  const [rejected, setRejected] = useState(false);
  const pendingRef = useRef<ExecutionRequest | null>(null);
  const [storageError, setStorageError] = useState('');
  const [loading, setLoading] = useState(true), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [readError, setReadError] = useState(''), [notice, setNotice] = useState('');
  const [note, setNote] = useState('');
  const [confirmed, setConfirmed] = useState('');
  function restore() {
    try { const raw = sessionStorage.getItem(key); const saved = raw === null ? null : decode(raw); pendingRef.current = saved; setPending(saved); setRejected(raw !== null && JSON.parse(raw).rejected === true); setStorageError(''); }
    catch { setStorageError('无法安全读取待确认执行记录。当前禁止新建执行，请恢复浏览器会话存储后重新读取。'); }
  }
  function discardRejected() {
    if (!rejected || writing.current) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setRejected(false); setStorageError(''); setError(''); setConfirmed(''); setNotice('已撤销首次提交时被明确拒绝的请求。请刷新任务或修正配置后重新确认。'); }
    catch { setStorageError('无法清除未接受请求，尚未解除锁定。'); }
  }
  function reconcile(rows: TaskExecution[]) {
    const sent = pendingRef.current;
    if (!sent) return;
    const match = rows.find(row => row.request_id === sent.request_id);
    if (!match) return;
    if (match.requirement_version !== sent.expected_version || match.previous_execution_id !== sent.previous_execution_id || match.reconciliation_note !== sent.reconciliation_note.trim()) { setStorageError('原请求与服务端记录不一致，禁止新建执行，请核查。'); return; }
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setStorageError(''); setNotice('已核对原请求，未重复创建执行。'); }
    catch { setStorageError('未能清除待确认记录；保留原请求，禁止新建执行。'); }
  }
  async function load() {
    if (reading.current !== null || writing.current) return;
    const request = ++serial.current; reading.current = request; setLoading(true);
    const results = await Promise.allSettled([api<TaskExecution[]>(`/tasks/${task.id}/executions`), api<CliSettingsValue>('/cli-settings'), api<ReplyRuntime>('/cli-runtime')]);
    if (alive.current && request === serial.current) {
      const [records, settings, status] = results;
      const errors: string[] = [];
      if (records.status === 'fulfilled') { setRuns(records.value); setLoaded(true); reconcile(records.value); } else errors.push(`执行历史：${failure(records.reason)}`);
      if (settings.status === 'fulfilled') setConfig(settings.value); else { setConfig(null); errors.push(`CLI 配置：${failure(settings.reason)}`); }
      if (status.status === 'fulfilled') setRuntime(status.value); else { setRuntime(null); errors.push(`调度状态：${failure(status.reason)}`); }
      setReadError(errors.join('；')); setLoading(false);
    }
    if (reading.current === request) reading.current = null;
    if (alive.current && request !== serial.current && reading.current === null && !writing.current) void load();
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal(); restore(); void load();
    return () => { alive.current = false; serial.current++; reading.current = null; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  const polling = runs.some(active) || !!pending;
  useEffect(() => { if (!polling) return; const timer = window.setInterval(() => void load(), 1000); return () => window.clearInterval(timer); }, [polling]);
  const agent = agents.find(row => row.id === task.agent_id);
  const latest = runs[0];
  const confirmationKey = `${task.requirement_version}:${latest?.id ?? 'first'}`;
  const blocked = !loaded || loading || !!readError ? '请先成功读取执行历史、配置和调度状态。'
    : conversation.archived ? '会话已归档，不能新建执行。'
    : !agent?.enabled || !conversation.member_ids.includes(task.agent_id) ? '负责人未启用或已不在会话中。'
    : !agent.tools.includes('execute') ? '负责人缺少 execute 工具权限，请编辑身份工具范围。'
    : runs.some(row => row.state === 'unknown') ? '存在结果未知的执行，须先核实实例及副作用，不能重试。'
    : runs.some(active) ? '已有活动执行，请等待或请求停止。'
    : !config?.enabled || !config.configured || !config.credential_available || !config.executable_available || !config.platform_supported ? 'CLI 尚未就绪，请在左侧 CLI 设置中配置、检查并启用。'
    : !runtime?.running || runtime.error ? `CLI 调度暂不可用：${runtime?.error || '控制服务未运行'}` : '';
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (!saved && (blocked || confirmed !== confirmationKey || latest && !note.trim())) return;
    const sent = saved ?? { request_id: crypto.randomUUID(), expected_version: task.requirement_version, reconciliation_note: latest ? note.trim() : '', previous_execution_id: latest?.id ?? null };
    try { sessionStorage.setItem(key, JSON.stringify(sent)); }
    catch { setStorageError('浏览器无法保存待确认请求，尚未发送。请恢复会话存储后重新读取。'); return; }
    pendingRef.current = sent; setPending(sent); setRejected(false); writing.current = true; serial.current++; setBusy(true); setError(''); setNotice(''); setConfirmed('');
    try {
      const run = await api<TaskExecution>(`/tasks/${task.id}/executions`, 'POST', sent);
      if (!alive.current) return;
      setRuns(current => [run, ...current.filter(row => row.id !== run.id)].sort((a, b) => b.attempt - a.attempt)); reconcile([run]); setNote('');
    } catch (error) {
      if (alive.current && !saved && error instanceof ApiError && error.status >= 400 && error.status < 500) {
        try { sessionStorage.setItem(key, JSON.stringify({ ...sent, rejected: true })); setRejected(true); }
        catch { setStorageError('无法记录服务端拒绝结果，仍保留原请求等待核对。'); }
      }
      if (alive.current) setError(`${error instanceof ApiError && error.status === 409 ? '需求版本发生变化，请关闭窗口并刷新任务；原请求仍保留，不会自动改用新版本。' : failure(error)} 原请求已保留，请核对原请求；不会自动创建替代执行。`);
    } finally { writing.current = false; if (alive.current) { setBusy(false); setLoading(false); void load(); } }
  }
  async function cancel(run: TaskExecution) {
    if (writing.current) return;
    writing.current = true; serial.current++; setBusy(true); setError('');
    try { const updated = await api<TaskExecution>(`/executions/${run.id}/cancel`, 'POST', {}); if (alive.current) setRuns(current => current.map(row => row.id === updated.id ? updated : row)); }
    catch (error) { if (alive.current) setError(`停止请求结果待核对：${failure(error)}。刷新状态或再次请求停止，不会创建新执行。`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); setLoading(false); void load(); } }
  }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="execution-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header><h2 id="execution-title">任务执行 · {task.title}</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p>当前任务需求 v{task.requirement_version} · 负责人：{agent?.name ?? task.agent_id}</p>
    <p className="muted">确认执行会调用配置的 CLI 和模型，可能产生费用。工作区按执行隔离；退出成功仅进入待评审，不代表成果已通过验收。</p>
    <button disabled={loading || busy} onClick={() => void load()}>{loading ? '读取执行中…' : '刷新执行状态'}</button>
    {runtime && <p className="muted">CLI 控制服务：{runtime.running ? '运行中' : '未运行'} · 活动请求 {runtime.active_requests}{runtime.error && ` · ${runtime.error}`}</p>}
    {readError && <p className="error" role="alert">{readError}。已有状态可能过期。</p>}
    {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {storageError && <p className="error" role="alert">{storageError}<button disabled={busy} onClick={() => { restore(); void load(); }}>重新读取待确认执行</button></p>}
    {pending ? <section><p role="status">执行请求待确认 · 需求 v{pending.expected_version}。沿用原请求 ID 和原内容核对；不会自动换版本或重新执行。若服务端从未接收，点击核对可能首次启动这次已确认的执行。</p><small>{pending.request_id}</small><button disabled={busy || !!storageError} onClick={() => void submit()}>{busy ? '核对中…' : '核对原执行请求'}</button>{rejected && <><p>首次提交已被服务端明确拒绝，未接受执行；可撤销该请求后修正配置或刷新任务。曾经结果未知的请求不提供此操作。</p><button disabled={busy || !!storageError} onClick={discardRejected}>撤销未接受请求</button></>}</section>
      : <form onSubmit={event => { event.preventDefault(); void submit(); }}><fieldset className="task-fields" disabled={busy || !!blocked || !!storageError}>
        {latest && <label>前次结果与副作用核查说明<textarea required maxLength={2000} value={note} onChange={event => setNote(event.target.value)} placeholder="记录已核查的结果、副作用和再次执行的理由" /></label>}
        <label className="check"><input type="checkbox" checked={confirmed === confirmationKey} onChange={event => setConfirmed(event.target.checked ? confirmationKey : '')} />我确认按需求 v{task.requirement_version} 调用 CLI 和模型执行{latest ? `，并已核对第 ${latest.attempt} 次执行` : ''}</label>
        <button className="primary" disabled={confirmed !== confirmationKey || !!latest && !note.trim()}>{latest ? '确认再次执行任务' : '确认执行任务'}</button>
      </fieldset>{blocked && <p className="muted">{blocked}</p>}</form>}
    {loaded && !runs.length && <p className="muted">暂无执行记录，尚未启动任务。</p>}
    {runs.map(run => <article className="task-card" key={run.id}><header><h3>第 {run.attempt} 次执行 · 需求 v{run.requirement_version}</h3><span>{labels[run.state]}</span></header><small>执行 ID：{run.id}</small><p>退出码：{run.exit_code ?? '尚未确认'} · {new Date(run.created_at).toLocaleString()}</p>{run.summary && <p className="task-source">{run.summary}</p>}{run.reconciliation_note && <p className="task-source">执行前核查：{run.reconciliation_note}</p>}{run.state === 'unknown' && <p className="error">实例和副作用未核实，不能启动替代执行。</p>}{run.state === 'stopping' && <p role="status">停止请求已记录，等待实际执行实例退出确认。</p>}{['queued', 'running'].includes(run.state) && <button disabled={busy} onClick={() => void cancel(run)}>{run.state === 'queued' ? '取消排队执行' : '请求停止执行'}</button>}<ExecutionReview run={run} /></article>)}
  </dialog>;
}
