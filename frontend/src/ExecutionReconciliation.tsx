import { useEffect, useRef, useState } from 'react';
import { api, type TaskExecution, type ExecutionReconciliationRequest, type ExecutionReconciliationRecord, type Task, type Agent } from './api';

const key = 'corppilot.reconciliation-pending.v1';
type Pending = { execution_id: string; payload: ExecutionReconciliationRequest };
const failure = (error: unknown) => error instanceof Error ? error.message : '读取失败';
function decode(raw: string): Pending {
  const value = JSON.parse(raw), p = value?.payload;
  if (!value || Object.keys(value).sort().join() !== 'execution_id,payload' || typeof value.execution_id !== 'string' || !/^[a-zA-Z0-9-]+$/.test(value.execution_id) || !p || Object.keys(p).sort().join() !== 'attempt,external_effects_checked,note,process_stopped,request_id,requirement_version' || typeof p.request_id !== 'string' || !p.request_id.trim() || p.request_id.length > 120 || !Number.isSafeInteger(p.attempt) || p.attempt < 1 || !Number.isSafeInteger(p.requirement_version) || p.requirement_version < 1 || p.process_stopped !== true || p.external_effects_checked !== true || typeof p.note !== 'string' || !p.note.trim() || p.note.length > 2000) throw new Error('待确认记录格式无效');
  return value;
}
function matches(row: ExecutionReconciliationRecord, sent: Pending) {
  return row.execution_id === sent.execution_id && (Object.keys(sent.payload) as (keyof ExecutionReconciliationRequest)[]).every(field => row[field] === sent.payload[field]);
}

export function ExecutionReconciliation({ executionId = '', onClose }: { executionId?: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), sequence = useRef(0), writing = useRef(false);
  const pendingRef = useRef<Pending | null>(null);
  const [pending, setPending] = useState<Pending | null>(null), [storageError, setStorageError] = useState('');
  const [selected, setSelected] = useState(executionId), [rows, setRows] = useState<TaskExecution[]>([]), [listLoaded, setListLoaded] = useState(false);
  const [taskNames, setTaskNames] = useState<Record<string, string>>({}), [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [run, setRun] = useState<TaskExecution | null>(null), [record, setRecord] = useState<ExecutionReconciliationRecord | null>(null), [task, setTask] = useState<Task | null>(null), [agent, setAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [readError, setReadError] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [stopped, setStopped] = useState(false), [checked, setChecked] = useState(false), [note, setNote] = useState('');
  function confirm(row: ExecutionReconciliationRecord, sent: Pending) {
    if (!matches(row, sent)) { setError('服务端核查记录与原请求不一致；原请求继续保留，请核查，不会覆盖已有声明。'); return; }
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setStorageError(''); setError(''); setRecord(row); setNotice('已核对并保存 Owner 声明；原执行仍为结果未知，退出码未改写。'); }
    catch { setStorageError('无法清除待确认记录，原请求继续保留。'); }
  }
  function acknowledgeExisting() {
    const sent = pendingRef.current;
    if (busy || loading || readError || !sent || !record || record.execution_id !== sent.execution_id || matches(record, sent)) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setStorageError(''); setError(''); setNotice('已确认服务端已有另一条不可覆盖的 Owner 声明，并结束本地待确认。未将本地原请求标为已接受。'); }
    catch { setStorageError('无法清除待确认记录，原请求继续保留。'); }
  }
  async function load(identity: string) {
    const version = ++sequence.current; setLoading(true); setReadError('');
    const results = await Promise.allSettled([api<TaskExecution[]>('/execution-reconciliations/pending'), ...(identity ? [api<TaskExecution>(`/executions/${identity}`), api<ExecutionReconciliationRecord | null>(`/executions/${identity}/reconciliation`)] : [])]);
    if (!alive.current || version !== sequence.current) return;
    const errors: string[] = [], [list, detail, declaration] = results;
    if (list.status === 'fulfilled') {
      const values = list.value as TaskExecution[]; setRows(values); setListLoaded(true);
      const taskIds = [...new Set(values.map(row => row.task_id))], agentIds = [...new Set(values.map(row => row.agent_id))];
      const [tasks, agents] = await Promise.all([Promise.allSettled(taskIds.map(id => api<Task>(`/tasks/${id}`))), Promise.allSettled(agentIds.map(id => api<Agent>(`/agents/${id}`)))]);
      if (!alive.current || version !== sequence.current) return;
      const titles: Record<string, string> = {}, names: Record<string, string> = {};
      tasks.forEach((result, index) => { if (result.status === 'fulfilled') titles[taskIds[index]] = result.value.title; });
      agents.forEach((result, index) => { if (result.status === 'fulfilled') names[agentIds[index]] = result.value.name; });
      setTaskNames(titles); setAgentNames(names);
    } else errors.push(`待核查列表：${failure(list.reason)}`);
    if (identity) {
      if (detail.status === 'fulfilled') {
        const value = detail.value as TaskExecution; setRun(value);
        const metadata = await Promise.allSettled([api<Task>(`/tasks/${value.task_id}`), api<Agent>(`/agents/${value.agent_id}`)]);
        if (!alive.current || version !== sequence.current) return;
        setTask(metadata[0].status === 'fulfilled' ? metadata[0].value : null); setAgent(metadata[1].status === 'fulfilled' ? metadata[1].value : null);
      } else { setRun(null); errors.push(`原执行：${failure(detail.reason)}`); }
      if (declaration.status === 'fulfilled') { const value = declaration.value as ExecutionReconciliationRecord | null; setRecord(value); if (value && pendingRef.current?.execution_id === identity) confirm(value, pendingRef.current); }
      else { errors.push(`Owner 核查记录：${failure(declaration.reason)}；已显示的声明来自此前成功读取或保存，未能重新读取`); }
    }
    setReadError(errors.join('；')); setLoading(false);
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal();
    let identity = executionId;
    try { const raw = sessionStorage.getItem(key); const value = raw ? decode(raw) : null; pendingRef.current = value; setPending(value); if (value) identity = value.execution_id; }
    catch { setStorageError('无法安全读取待确认核查请求；请恢复会话存储后重新打开窗口，暂不允许提交。'); }
    setSelected(identity); void load(identity);
    return () => { alive.current = false; sequence.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  function select(identity: string) { setSelected(identity); setRun(null); setRecord(null); setTask(null); setAgent(null); setStopped(false); setChecked(false); setNote(''); setError(''); setNotice(''); void load(identity); }
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (!saved && (!run || loading || readError || record || run.state !== 'unknown' || !stopped || !checked || !note.trim())) return;
    const sent: Pending = saved ?? { execution_id: run!.id, payload: { request_id: crypto.randomUUID(), attempt: run!.attempt, requirement_version: run!.requirement_version, process_stopped: true, external_effects_checked: true, note: note.trim() } };
    try { sessionStorage.setItem(key, JSON.stringify(sent)); } catch { setStorageError('无法保存原核查请求，尚未发送。'); return; }
    pendingRef.current = sent; setPending(sent); writing.current = true; sequence.current++; setBusy(true); setError(''); setNotice('');
    try { const value = await api<ExecutionReconciliationRecord>(`/executions/${sent.execution_id}/reconciliation`, 'POST', sent.payload); if (alive.current) confirm(value, sent); }
    catch (error) { if (alive.current) setError(`${failure(error)}。原请求已保留，可按原内容核对；不会改用当前任务版本或覆盖已有声明。`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(sent.execution_id); } }
  }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="reconciliation-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header><h2 id="reconciliation-title">未知执行核查</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p>这是 Owner 对进程及外部影响的人工核查声明，不会停止进程，也不构成机器退出成功或成果验收证据。</p>
    <p className="error">保存最后一条未核查声明后，调度可能恢复所有此前已授权的排队 CLI 任务，并调用模型产生费用。此操作本身不会新建执行。</p>
    <button disabled={loading || busy} onClick={() => void load(selected)}>{loading ? '读取核查状态中…' : '刷新核查状态'}</button>
    {readError && <p className="error" role="alert">{readError}。读取失败不代表没有待核查执行。</p>}{storageError && <p className="error" role="alert">{storageError}</p>}{error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {pending ? <section><h3>原核查请求待确认</h3><p>执行 ID：{pending.execution_id} · 第 {pending.payload.attempt} 次 · 原需求 v{pending.payload.requirement_version}</p><small>请求 ID：{pending.payload.request_id}</small><p className="task-source">{pending.payload.note}</p><p>已确认进程停止、外部影响已核查。核对会使用原请求；如果服务端尚未接收，可能首次保存声明并恢复已授权队列。</p><button disabled={busy || !!storageError} onClick={() => void submit()}>{busy ? '核对中…' : '核对原核查请求'}</button>{record && !matches(record, pending) && <section><h4>服务端已有另一条不可覆盖的声明</h4><p className="task-source">{record.note}</p><small>请求 {record.request_id} · {record.reconciled_at}</small><p>此记录不等于原请求被接受。确认已有声明后，可以结束本地等待并继续核查其他执行。</p><button disabled={busy || loading || !!readError || !!storageError} onClick={acknowledgeExisting}>确认已有核查并结束本地待确认</button></section>}</section> : <section><h3>待核查执行（最多显示 100 条）</h3>{listLoaded && !loading && !readError && !rows.length && <p>暂无待核查执行。</p>}{rows.map(row => <button key={row.id} disabled={busy || loading} aria-pressed={row.id === selected} onClick={() => select(row.id)}><strong>{taskNames[row.task_id] ?? `任务 ${row.task_id}`}</strong> · {agentNames[row.agent_id] ?? `Agent ${row.agent_id}`}<br /><small>执行 {row.id} · 第 {row.attempt} 次 · v{row.requirement_version}</small></button>)}</section>}
    {run && <article className="task-card"><h3>{task?.title ?? `任务 ${run.task_id}`}</h3><p>负责人：{agent?.name ?? run.agent_id}{agent && !agent.enabled ? ' · 已停用' : ''}</p><small>执行 ID：{run.id}</small><p>第 {run.attempt} 次 · 原需求 v{run.requirement_version} · {run.state === 'unknown' ? '结果未知' : run.state} · 退出码：{run.exit_code ?? '尚未确认'}</p>{run.summary && <p className="task-source">{run.summary}</p>}
      {record ? <section><h3>Owner 已核查</h3><p>声明：进程已停止；外部影响已核查。</p><p className="task-source">{record.note}</p><small>{record.reconciled_at} · 请求 {record.request_id}</small><p>此声明不改变原执行结果；再次执行仍须在任务中单独确认。</p></section> : !pending && <form onSubmit={event => { event.preventDefault(); void submit(); }}><fieldset className="task-fields" disabled={loading || busy || !!readError || !!storageError || run.state !== 'unknown'}><label className="check"><input type="checkbox" checked={stopped} onChange={event => setStopped(event.target.checked)} />我已核实原执行及其子进程均已停止</label><label className="check"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />我已核查文件、服务及其他外部影响</label><label>核查依据<textarea required maxLength={2000} value={note} onChange={event => setNote(event.target.value)} placeholder="记录核查方式、实际结果及已处理的外部影响" /></label><button className="primary" disabled={!stopped || !checked || !note.trim()}>保存 Owner 核查声明</button></fieldset></form>}
    </article>}
  </dialog>;
}
