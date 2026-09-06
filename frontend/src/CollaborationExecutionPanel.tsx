import { useEffect, useRef, useState } from 'react';
import { CheckpointRecovery } from './CheckpointRecovery';
import { api, ApiError, type Agent, type Conversation, type CollaborationReceipt, type Task, type TaskExecution, type TaskDependencyStatus, type CliSettingsValue, type ReplyRuntime, type ProjectExecutionRequest, type ProjectExecutionReceipt, type ProjectExecutionDetail, type ExecutionReconciliationRecord } from './api';

type Pending = { payload: ProjectExecutionRequest; batch_id?: string; rejected?: boolean };
type Entry = { task: Task; runs: TaskExecution[]; dependencies: TaskDependencyStatus };
const active = (run: TaskExecution) => ['queued', 'running', 'stopping'].includes(run.state);
const labels = { queued: '排队中', running: '运行中', stopping: '正在停止', awaiting_review: '执行已结束', failed: '失败', cancelled: '已取消', unknown: '结果未知', superseded: '需求已过期' };
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败';
function same(a: ProjectExecutionRequest, b: ProjectExecutionRequest) { return a.request_id === b.request_id && a.tasks.length === b.tasks.length && a.tasks.every((t, i) => { const x = b.tasks[i]; return t.task_id === x.task_id && t.expected_version === x.expected_version && t.previous_execution_id === x.previous_execution_id && t.reconciliation_note === x.reconciliation_note; }); }
function decode(raw: string, taskIds: string[]): Pending {
  const row = JSON.parse(raw), p = row?.payload;
  const id = (v: unknown) => typeof v === 'string' && /^[0-9a-f-]{36}$/.test(v);
  if (!row || Object.keys(row).some(k => !['payload', 'batch_id', 'rejected'].includes(k)) || row.batch_id !== undefined && !id(row.batch_id) || row.rejected !== undefined && typeof row.rejected !== 'boolean' || !p || Object.keys(p).sort().join() !== 'request_id,tasks' || !id(p.request_id) || !Array.isArray(p.tasks) || !p.tasks.length || p.tasks.length > 16 || p.tasks.some((t: ProjectExecutionRequest['tasks'][number]) => !t || Object.keys(t).sort().join() !== 'expected_version,previous_execution_id,reconciliation_note,task_id' || !taskIds.includes(t.task_id) || !Number.isSafeInteger(t.expected_version) || t.expected_version < 1 || !(t.previous_execution_id === null || id(t.previous_execution_id)) || typeof t.reconciliation_note !== 'string' || t.reconciliation_note.length > 2000 || t.previous_execution_id !== null && !t.reconciliation_note.trim()) || new Set(p.tasks.map((t: ProjectExecutionRequest['tasks'][number]) => t.task_id)).size !== p.tasks.length) throw Error('原批量请求格式无效');
  return row;
}

export function CollaborationExecutionPanel({ plan, onClose, initialBatchId = '' }: { plan: CollaborationReceipt; onClose: () => void; initialBatchId?: string }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), serial = useRef(0), reading = useRef(false), writing = useRef(false), pendingRef = useRef<Pending | null>(null), viewRef = useRef(initialBatchId);
  const key = `corppilot.project-execution-pending.v1.${plan.id}`, base = `/collaboration-plans/${plan.id}/executions`;
  const [pending, setPending] = useState<Pending | null>(null), [detail, setDetail] = useState<ProjectExecutionDetail | null>(null), [history, setHistory] = useState<ProjectExecutionReceipt[]>([]), [entries, setEntries] = useState<Entry[]>([]);
  const [selected, setSelected] = useState<string[]>([]), [notes, setNotes] = useState<Record<string, string>>({}), [confirmed, setConfirmed] = useState(false), [stopConfirmed, setStopConfirmed] = useState(''), [stopUnknown, setStopUnknown] = useState(false);
  const [config, setConfig] = useState<CliSettingsValue | null>(null), [runtime, setRuntime] = useState<ReplyRuntime | null>(null), [project, setProject] = useState<Conversation | null>(null), [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [readError, setReadError] = useState(''), [storageError, setStorageError] = useState('');
  const authority = useRef('');
  const [checkpoint, setCheckpoint] = useState('');
  const [declarations, setDeclarations] = useState<Record<string, ExecutionReconciliationRecord | null>>({});
  const unresolved = (run: TaskExecution) => run.state === 'unknown' && !declarations[run.id];
  function adopt(row: ProjectExecutionReceipt, sent: Pending) {
    if (row.collaboration_id !== plan.id || row.project_conversation_id !== plan.project_conversation_id || !same(row.request_payload, sent.payload) || sent.batch_id && sent.batch_id !== row.id) { setStorageError('批量回执与原请求不一致，原请求继续锁定。'); return; }
    try { const saved = { payload: sent.payload, batch_id: row.id }; sessionStorage.setItem(key, JSON.stringify(saved)); pendingRef.current = saved; setPending(saved); viewRef.current = row.id; setStorageError(''); setError(''); }
    catch { setStorageError('批次编号保存失败，原请求继续保留。'); }
  }
  async function load(tracking = false) {
    if (reading.current || writing.current) return;
    reading.current = true; const token = ++serial.current; setLoading(true);
    try {
      const trackingId = pendingRef.current?.batch_id ?? viewRef.current;
      if (tracking && trackingId) {
        const row = await api<ProjectExecutionDetail>(`/project-executions/${trackingId}`);
        if (!alive.current || token !== serial.current) return;
        if (row.id !== trackingId || row.collaboration_id !== plan.id) throw Error('批次详情与当前协作回执不一致');
        if (pendingRef.current) adopt(row, pendingRef.current);
        setDetail(row);
        return;
      }
      const [records, settings, status, conversation, people, tasks] = await Promise.all([api<ProjectExecutionReceipt[]>(base), api<CliSettingsValue>('/cli-settings'), api<ReplyRuntime>('/cli-runtime'), api<Conversation>(`/conversations/${plan.project_conversation_id}`), api<Agent[]>('/agents'), Promise.all(Object.values(plan.task_ids).map(async id => { const [task, runs, dependencies] = await Promise.all([api<Task>(`/tasks/${id}`), api<TaskExecution[]>(`/tasks/${id}/executions`), api<TaskDependencyStatus>(`/tasks/${id}/dependencies`)]); return { task, runs, dependencies }; }))]);
      const unknowns = tasks.flatMap(entry => entry.runs.filter(run => run.state === 'unknown'));
      const checks = await Promise.all(unknowns.map(async run => [run.id, await api<ExecutionReconciliationRecord | null>(`/executions/${run.id}/reconciliation`)] as const));
      if (!alive.current || token !== serial.current) return;
      setDeclarations(Object.fromEntries(checks));
      const signature = JSON.stringify([tasks.map(e => [e.task.id, e.task.requirement_version, e.task.agent_id, e.runs[0]?.id, e.runs.map(r => r.state)]), conversation.archived, conversation.member_ids, people.map(a => [a.id, a.enabled, a.tools]), settings, status.running, status.error, checks]);
      if (authority.current !== signature) { authority.current = signature; setConfirmed(false); }
      setHistory(records); setConfig(settings); setRuntime(status); setProject(conversation); setAgents(people); setEntries(tasks);
      const saved = pendingRef.current;
      if (saved && !saved.batch_id) { const row = records.find(r => r.request_id === saved.payload.request_id); if (row) adopt(row, saved); }
      const id = pendingRef.current?.batch_id ?? viewRef.current;
      if (id) {
        const row = await api<ProjectExecutionDetail>(`/project-executions/${id}`);
        if (!alive.current || token !== serial.current) return;
        if (row.id !== id || row.collaboration_id !== plan.id) throw Error('批次详情与当前协作回执不一致');
        if (pendingRef.current) adopt(row, pendingRef.current);
        setDetail(row);
        setStopUnknown(sessionStorage.getItem(`corppilot.project-stop-pending.v1.${id}`) !== null);
      }
      setReadError('');
    } catch (error) { if (alive.current && token === serial.current) setReadError(`${failure(error)}。已有结果保留，不能据此判断未入队。`); }
    finally { reading.current = false; if (alive.current) { if (token === serial.current) setLoading(false); else if (!writing.current) void load(); } }
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal();
    try { const raw = sessionStorage.getItem(key), saved = raw ? decode(raw, Object.values(plan.task_ids)) : null; pendingRef.current = saved; setPending(saved); if (saved) viewRef.current = ''; }
    catch { viewRef.current = ''; setStorageError('无法读取原批量请求，请恢复会话存储后重新打开；不能新建请求。'); }
    void load(); return () => { alive.current = false; serial.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  const polling = !!pending && !pending.batch_id || detail?.items.some(item => active(item.execution));
  useEffect(() => { if (!polling) return; const timer = setInterval(() => void load(true), 1500); return () => clearInterval(timer); }, [polling]);
  function blocked(entry: Entry) {
    const person = agents.find(a => a.id === entry.task.agent_id);
    return project?.archived ? '项目已归档' : !person?.enabled || !person.tools.includes('execute') || !project?.member_ids.includes(person.id) ? '负责人不可执行' : entry.runs.some(active) ? '已有活动执行' : entry.runs.some(unresolved) ? '存在未知执行，请先到任务执行中核查' : entry.dependencies.requirement_version !== entry.task.requirement_version ? '需求版本已变化' : '';
  }
  const ready = !loading && !readError && config?.enabled && config.configured && config.credential_available && config.executable_available && config.platform_supported && runtime?.running && !runtime.error;
  const chosen = entries.filter(e => selected.includes(e.task.id));
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (saved?.batch_id) { void load(); return; }
    if (!saved && (!ready || !confirmed || !chosen.length || chosen.some(e => blocked(e) || e.runs[0] && !notes[e.task.id]?.trim()))) return;
    const sent: Pending = { payload: saved?.payload ?? { request_id: crypto.randomUUID(), tasks: chosen.map(e => ({ task_id: e.task.id, expected_version: e.task.requirement_version, previous_execution_id: e.runs[0]?.id ?? null, reconciliation_note: e.runs[0] ? notes[e.task.id].trim() : '' })) } };
    try { sessionStorage.setItem(key, JSON.stringify(sent)); } catch { setStorageError('无法保存完整原请求，尚未发送。'); return; }
    pendingRef.current = sent; setPending(sent); writing.current = true; serial.current++; setBusy(true); setConfirmed(false); setError('');
    try { const row = await api<ProjectExecutionReceipt>(base, 'POST', sent.payload); if (alive.current) adopt(row, sent); }
    catch (error) { if (alive.current) { if (!saved && error instanceof ApiError && error.status >= 400 && error.status < 500) { try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(key, JSON.stringify(rejected)); pendingRef.current = rejected; setPending(rejected); } catch { setStorageError('首次拒绝证据保存失败，原请求继续保留。'); } } setError(`${failure(error)}。原请求已保留，不会自动新建替代批次。`); } }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(); } }
  }
  function release() {
    if (writing.current || storageError || !pending || !(pending.rejected || detail && !detail.items.some(i => active(i.execution) || unresolved(i.execution)))) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setDetail(null); viewRef.current = ''; setSelected([]); setNotes({}); setConfirmed(false); setStopConfirmed(''); setStopUnknown(false); serial.current++; void load(); }
    catch { setStorageError('无法结束原请求查看，继续保持锁定。'); }
  }
  async function stop() {
    if (writing.current || loading || !detail || stopConfirmed !== detail.id || stopUnknown) return;
    const id = detail.id;
    try { sessionStorage.setItem(`corppilot.project-stop-pending.v1.${id}`, 'unconfirmed'); } catch { setStorageError('无法保存停止核对记录，尚未发送。'); return; }
    writing.current = true; serial.current++; setBusy(true); setStopUnknown(true); setStopConfirmed(''); setError('');
    try { const row = await api<ProjectExecutionDetail>(`/project-executions/${id}/stop`, 'POST', { confirm: true }); if (alive.current) { if (row.id !== id || row.collaboration_id !== plan.id) throw Error('停止回执不一致'); setDetail(row); sessionStorage.removeItem(`corppilot.project-stop-pending.v1.${id}`); setStopUnknown(false); } }
    catch (error) { if (alive.current) setError(`${failure(error)}。停止结果待确认，只刷新本批绑定实例，不会自动重发停止。`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(); } }
  }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="batch-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}><header><h2 id="batch-title">批量执行协作项目 · {plan.approved_plan.title}</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p>只选择本协作回执中的 1–16 个任务，按各自当前需求版本一起入队。确认可能调用 CLI 和模型并产生费用；批量创建全有或全无。明确授权的固定首批可自动交接成果，其他批次仍须 Owner 批准前置成果；未选前置任务不会自动执行。</p><small>协作回执 {plan.id}</small><button disabled={busy || loading} onClick={() => void load()}>刷新任务与本批状态</button>
    {readError && <p className="error" role="alert">{readError}</p>}{error && <p className="error" role="alert">{error}</p>}{storageError && <p className="error" role="alert">{storageError}</p>}
    {pending && initialBatchId && pending.batch_id !== initialBatchId && <p>已有另一原批量请求待核对，优先恢复该请求；本次指定批次尚未打开，不会覆盖原请求。</p>}
    {pending ? <section><h3>原批量请求</h3><small>{pending.payload.request_id}</small>{pending.payload.tasks.map(t => <p key={t.task_id}>{entries.find(e => e.task.id === t.task_id)?.task.title ?? t.task_id} · v{t.expected_version} · 前次 {t.previous_execution_id ?? '首次'} · {t.reconciliation_note}</p>)}<button disabled={busy || loading || !!storageError} onClick={() => void submit()}>{pending.batch_id ? '读取原批次（不再入队）' : '核对原批量请求'}</button>{!pending.batch_id && <p>若此前未接收，手动核对可能首次将已确认任务入队。</p>}{(pending.rejected || detail && !detail.items.some(i => active(i.execution) || unresolved(i.execution))) && <button disabled={busy || !!storageError} onClick={release}>{pending.rejected ? '修改首次未接受请求' : '结束查看并准备另一批'}</button>}</section> : <fieldset className="task-fields" disabled={busy || !ready || !!storageError}>
      {entries.map(e => <section key={e.task.id}><label className="check"><input type="checkbox" checked={selected.includes(e.task.id)} disabled={!selected.includes(e.task.id) && !!blocked(e)} onChange={event => { setSelected(event.target.checked ? [...selected, e.task.id] : selected.filter(id => id !== e.task.id)); setConfirmed(false); }} />{e.task.title} · v{e.task.requirement_version} · {agents.find(a => a.id === e.task.agent_id)?.name ?? e.task.agent_id}</label><p>执行范围：{e.task.scope}</p><p>验收标准：{e.task.acceptance}</p><p>{blocked(e) || (e.dependencies.ready ? '前置条件已满足，执行时再次核查' : `入队后等待前置验收：${e.dependencies.blocked_reason}`)}</p>{e.runs[0] && <p>最近执行 {e.runs[0].id} · 第 {e.runs[0].attempt} 次 · {labels[e.runs[0].state]}</p>}{selected.includes(e.task.id) && e.runs[0] && <label>再次执行核查说明<textarea required maxLength={2000} value={notes[e.task.id] ?? ''} onChange={event => { setNotes({ ...notes, [e.task.id]: event.target.value }); setConfirmed(false); }} /></label>}</section>)}
      <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认上述 {selected.length} 个任务的版本、前次结果及副作用，并授权本批 CLI 和模型执行</label><button disabled={!confirmed || !chosen.length || chosen.some(e => !!blocked(e) || !!e.runs[0] && !notes[e.task.id]?.trim())} onClick={() => void submit()}>确认将选定任务一起入队</button>
    </fieldset>}{!pending && !ready && <p>请先成功读取当前任务、已启用 CLI 配置及调度状态；读取失败时不能提交新批次。</p>}
    {detail && <section><h3>本批绑定实例</h3><small>批次 {detail.id}</small>{detail.items.map(item => <article className="task-card" key={item.execution.id}><h4>{item.task.title} · {labels[item.execution.state]}</h4><small>执行 {item.execution.id} · v{item.execution.requirement_version}</small>{item.latest_execution_id !== item.execution.id && <p>此实例不是任务最新执行；本批停止不会操作替代实例。</p>}{item.task.requirement_version !== item.execution.requirement_version && <p>当前需求已变化；本批仍绑定原需求版本。</p>}{item.execution.state === 'unknown' && <p>{declarations[item.execution.id] ? 'Owner 已声明进程停止并核查外部影响；原执行仍为未知。再次执行仍须填写说明并重新确认。' : '未知实例尚未核查或声明未读到，请到任务执行中核查后刷新本页。'}</p>}<p>{item.dependencies.handoff_authorized ? '本实例已获固定前置成果自动交接授权；这不是验收批准。' : '前置成果按原审批规则处理。'}</p><p>Owner 评审：{item.review?.decision === 'approved' ? '已批准' : item.review?.decision === 'rejected' ? '已拒绝' : '尚未批准'}</p>{!item.dependencies.ready && <p>等待前置：{item.dependencies.blocked_reason}</p>}{item.execution.summary && <p>{item.execution.summary}</p>}</article>)}<p>执行结束不等于成果验收通过；请在项目任务中评审成果或核查未知执行。</p>
      {stopUnknown ? <p className="error">停止响应未确认。只刷新本批状态，不自动重发停止；必要时到具体任务核查原实例。</p> : detail.items.some(i => active(i.execution)) && <><label className="check"><input type="checkbox" disabled={busy || loading} checked={stopConfirmed === detail.id} onChange={event => setStopConfirmed(event.target.checked ? detail.id : '')} />我确认只请求停止本批绑定的实例，等待实际退出核实</label><button disabled={busy || loading || stopConfirmed !== detail.id} onClick={() => void stop()}>确认停止本批执行</button></>}
    </section>}
    {detail && <button disabled={busy || loading || !!storageError || !!pending && pending.batch_id !== detail.id} onClick={() => setCheckpoint(detail.id)}>从本批检查点恢复</button>}
    <details><summary>批量执行历史</summary>{history.map(row => <p key={row.id}><button disabled={busy || loading || !!pending} onClick={() => { viewRef.current = row.id; setDetail(null); setStopConfirmed(''); setStopUnknown(false); void load(); }}>查看批次 {row.id}</button> · {row.created_at}</p>)}</details>
    {checkpoint && <CheckpointRecovery key={checkpoint} sourceBatchId={checkpoint} plan={plan} onClose={() => setCheckpoint('')} onOpenBatch={id => {
      try {
        if (pendingRef.current && pendingRef.current.batch_id !== checkpoint) throw Error('另一原批量请求仍待核对，不能切换');
        sessionStorage.removeItem(key); pendingRef.current = null; setPending(null);
        viewRef.current = id; setDetail(null); setStopConfirmed(''); setStopUnknown(false); setCheckpoint(''); void load();
      } catch (e) { setStorageError(failure(e)); }
    }} />}
  </dialog>;
}
