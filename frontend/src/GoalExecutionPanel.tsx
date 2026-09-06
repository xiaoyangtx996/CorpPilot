import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type Message, type GoalExecutionRequest, type GoalExecutionReceipt, type CollaborationPlan } from './api';
import { CollaborationExecutionPanel } from './CollaborationExecutionPanel';
import { ModelRunReconciliation } from './ModelRunReconciliation';

const pendingKey = 'corppilot.goal-execution-pending.v1', stopKey = 'corppilot.goal-stop-pending.v1';
type Pending = { source_id: string; payload: GoalExecutionRequest; goal_id?: string; planning_run_id?: string; launch_request_id?: string; launch_id?: string; batch_id?: string; stop_requested?: boolean; rejected?: boolean };
const fields = (v: unknown, names: string) => !!v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).sort().join() === names;
const id = (v: unknown, max = 200): v is string => typeof v === 'string' && !!v && v === v.trim() && v.length <= max;
const failure = (e: unknown) => e instanceof Error ? e.message : '请求失败';
function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) {
    const x = a as Record<string, unknown>, y = b as Record<string, unknown>;
    return Object.keys(x).length === Object.keys(y).length && Object.keys(x).every(k => Object.hasOwn(y, k) && same(x[k], y[k]));
  }
  return false;
}
function decode(raw: string): Pending {
  const row = JSON.parse(raw), p = row?.payload;
  if (!row || typeof row !== 'object' || Array.isArray(row) || Object.keys(row).some(k => !['source_id', 'payload', 'goal_id', 'planning_run_id', 'launch_request_id', 'launch_id', 'batch_id', 'stop_requested', 'rejected'].includes(k)) || !id(row.source_id)
    || !fields(p, 'agent_id,candidate_ids,confirm_execution,confirm_handoff,max_tasks,request_id,shared_brief,source_message_id')
    || !id(p.request_id, 120) || !id(p.source_message_id) || !id(p.agent_id) || !id(p.shared_brief, 16000)
    || !Array.isArray(p.candidate_ids) || !p.candidate_ids.length || p.candidate_ids.length > 100 || p.candidate_ids.some((v: unknown) => !id(v)) || new Set(p.candidate_ids).size !== p.candidate_ids.length || !p.candidate_ids.includes(p.agent_id)
    || !Number.isSafeInteger(p.max_tasks) || p.max_tasks < 1 || p.max_tasks > 16 || p.confirm_execution !== true || typeof p.confirm_handoff !== 'boolean'
    || ['goal_id', 'planning_run_id', 'launch_request_id', 'launch_id', 'batch_id'].some(k => row[k] !== undefined && !id(row[k], 120))
    || ['stop_requested', 'rejected'].some(k => row[k] !== undefined && typeof row[k] !== 'boolean')
    || new TextEncoder().encode(JSON.stringify(p)).length > 65536) throw Error('原目标授权格式无效，不能替换或重新发送');
  return row;
}
function matches(row: GoalExecutionReceipt, saved: Pending) {
  try {
    decode(JSON.stringify({ source_id: row.source_conversation_id, payload: row.request_payload }));
    const p = saved.payload, r = row.planning;
    if (![row.id, row.planning_run_id, row.launch_request_id].every(v => id(v, 120)) || row.source_conversation_id !== saved.source_id || row.request_id !== p.request_id || !same(row.request_payload, p)
      || typeof row.stop_requested !== 'boolean' || saved.stop_requested && !row.stop_requested
      || !['planning', 'ready_to_launch', 'launched', 'failed', 'unknown', 'cancelled', 'stop_requested', 'stopped'].includes(row.state)
      || saved.goal_id && saved.goal_id !== row.id || saved.planning_run_id && saved.planning_run_id !== row.planning_run_id || saved.launch_request_id && saved.launch_request_id !== row.launch_request_id || saved.launch_id && saved.launch_id !== row.launch_id
      || !r || r.id !== row.planning_run_id || r.conversation_id !== saved.source_id || r.source_message_id !== p.source_message_id || r.agent_id !== p.agent_id || !id(r.request_id, 120)
      || !['queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'].includes(r.state)
      || !same(r.request_payload, { agent_id: p.agent_id, source_message_id: p.source_message_id, request_id: r.request_id, candidate_ids: p.candidate_ids })
      || !Array.isArray(r.candidate_snapshot) || !same(r.candidate_snapshot.map(a => a.id), p.candidate_ids)) return false;
    if (!row.launch) return row.launch === null && row.launch_id === null && row.batch === null && !saved.batch_id;
    const l = row.launch, c = l.collaboration, b = l.batch, detail = row.batch, proposal = r.proposal;
    if (!proposal || r.state !== 'completed' || !Array.isArray(proposal.tasks) || !proposal.tasks.length || proposal.tasks.length > p.max_tasks || new Set(proposal.tasks.map(t => t.key)).size !== proposal.tasks.length || proposal.tasks.some(t => !p.candidate_ids.includes(t.agent_id))) return false;
    const plan: CollaborationPlan = { ...proposal, shared_brief: p.shared_brief, request_id: row.launch_request_id, source_message_id: p.source_message_id, coordinator_id: p.agent_id };
    if (![l.id, c.id, c.project_conversation_id, c.shared_message_id, b.id, b.request_id].every(v => id(v, 120)) || l.id !== row.launch_id || l.source_conversation_id !== saved.source_id || l.request_id !== row.launch_request_id
      || !same(l.request_payload, { plan, confirm_execution: true, confirm_handoff: p.confirm_handoff }) || !same(c.approved_plan, plan) || c.source_conversation_id !== saved.source_id || c.source_message_id !== p.source_message_id || c.request_id !== row.launch_request_id || c.coordinator_id !== p.agent_id
      || b.collaboration_id !== c.id || b.project_conversation_id !== c.project_conversation_id || b.request_payload.request_id !== b.request_id || saved.batch_id && saved.batch_id !== b.id || !detail) return false;
    const members = [...new Set([p.agent_id, ...plan.tasks.map(t => t.agent_id)])];
    if (!Array.isArray(c.member_ids) || c.member_ids.length !== members.length || new Set(c.member_ids).size !== members.length || c.member_ids.some(v => !members.includes(v))
      || !c.task_ids || !same(Object.keys(c.task_ids).sort(), plan.tasks.map(t => t.key).sort()) || Object.values(c.task_ids).some(v => !id(v, 120)) || new Set(Object.values(c.task_ids)).size !== plan.tasks.length
      || !Array.isArray(b.tasks) || b.tasks.length !== plan.tasks.length || !Array.isArray(b.request_payload.tasks) || b.request_payload.tasks.length !== plan.tasks.length || new Set(b.tasks.map(t => t.task_id)).size !== plan.tasks.length || new Set(b.tasks.map(t => t.execution_id)).size !== plan.tasks.length || new Set(b.tasks.map(t => t.request_id)).size !== plan.tasks.length) return false;
    const { items, ...snapshot } = detail;
    return same(snapshot, b) && Array.isArray(items) && items.length === b.tasks.length && plan.tasks.every((task, i) => {
      const binding = b.tasks[i], request = b.request_payload.tasks[i], item = items[i];
      return id(binding.execution_id, 120) && id(binding.request_id, 120) && binding.task_id === c.task_ids[task.key]
        && same(request, { task_id: binding.task_id, expected_version: 1, previous_execution_id: null, reconciliation_note: '' })
        && item.task.id === binding.task_id && item.task.conversation_id === c.project_conversation_id && item.execution.id === binding.execution_id && item.execution.task_id === binding.task_id && item.execution.request_id === binding.request_id && item.execution.requirement_version === 1 && item.execution.attempt === 1 && item.execution.agent_id === task.agent_id;
    });
  } catch { return false; }
}
function anchor(row: GoalExecutionReceipt): Pending {
  return { source_id: row.source_conversation_id, payload: row.request_payload, goal_id: row.id, planning_run_id: row.planning_run_id, launch_request_id: row.launch_request_id,
    ...(row.launch_id ? { launch_id: row.launch_id } : {}), ...(row.batch ? { batch_id: row.batch.id } : {}), stop_requested: row.stop_requested };
}
const stage = { planning: '秘书正在规划', ready_to_launch: '计划已生成，正在衔接执行', launched: '项目已启动', failed: '执行条件未满足', unknown: '结果待核查', cancelled: '规划已取消', stop_requested: '已请求停止，仍需核查', stopped: '目标授权已停止' };
const runState = { queued: '排队', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', unknown: '未知', stopping: '等待退出', awaiting_review: '等待验收', superseded: '需求已过期' };

export function GoalExecutionPanel({ conversationId, source, onClose, onOpenProject }: { conversationId: string; source?: Message; onClose: () => void; onOpenProject: (conversation: Conversation) => void }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), serial = useRef(0), reading = useRef(false), writing = useRef(false), readable = useRef(true), polling = useRef(false);
  const pendingRef = useRef<Pending | null>(null), stopRef = useRef<Pending | null>(null), selected = useRef<Pending | null>(null), sourceRef = useRef(conversationId);
  const [pending, setPending] = useState<Pending | null>(null), [stopPending, setStopPending] = useState<Pending | null>(null), [current, setCurrent] = useState<GoalExecutionReceipt | null>(null), [verified, setVerified] = useState(false);
  const [history, setHistory] = useState<GoalExecutionReceipt[]>([]), [agents, setAgents] = useState<Agent[]>([]), [conversation, setConversation] = useState<Conversation | null>(null);
  const [payload, setPayload] = useState<GoalExecutionRequest>({ request_id: crypto.randomUUID(), source_message_id: source?.id ?? '', agent_id: '', candidate_ids: [], shared_brief: '', max_tasks: 4, confirm_execution: true, confirm_handoff: false });
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [confirmed, setConfirmed] = useState(false), [stopConfirmed, setStopConfirmed] = useState('');
  const [error, setError] = useState(''), [readError, setReadError] = useState(''), [storageError, setStorageError] = useState(''), [notice, setNotice] = useState('');
  const [batch, setBatch] = useState<GoalExecutionReceipt | null>(null), [checkId, setCheckId] = useState('');
  function lock(message: string) { readable.current = false; setStorageError(message); setVerified(false); }
  function adopt(row: GoalExecutionReceipt, expected: Pending, independent: boolean) {
    if (!matches(row, expected)) { lock('目标回执与原授权、规划或固定批次不一致；原请求保留，不能释放。'); return; }
    if (stopRef.current && !matches(row, stopRef.current)) { lock('停止回执与原停止授权不一致；恢复记录保留，不能释放。'); return; }
    const next = anchor(row);
    try {
      if (pendingRef.current && pendingRef.current.payload.request_id === expected.payload.request_id) { sessionStorage.setItem(pendingKey, JSON.stringify(next)); pendingRef.current = next; setPending(next); }
      if (independent && stopRef.current?.goal_id === row.id && row.stop_requested) { sessionStorage.removeItem(stopKey); stopRef.current = null; setStopPending(null); }
    } catch { lock('无法保存目标恢复记录，请恢复会话存储后重新打开。'); return; }
    selected.current = next; setCurrent(row); setVerified(independent); setError('');
    polling.current = ['queued', 'running'].includes(row.planning.state) || row.state === 'ready_to_launch' || !!row.batch?.items.some(item => ['queued', 'running', 'stopping'].includes(item.execution.state));
  }
  async function load() {
    if (reading.current || writing.current) return;
    reading.current = true; const version = ++serial.current; setLoading(true); setReadError(''); setVerified(false);
    try {
      const sid = sourceRef.current;
      const result = await Promise.allSettled([api<Agent[]>('/agents'), ...(sid ? [api<Conversation>(`/conversations/${sid}`), api<GoalExecutionReceipt[]>(`/conversations/${sid}/goal-executions`)] : [])]);
      if (!alive.current || version !== serial.current) return;
      const errors: string[] = [];
      if (result[0].status === 'fulfilled') setAgents(result[0].value as Agent[]); else errors.push(failure(result[0].reason));
      let rows: GoalExecutionReceipt[] = [];
      if (sid) {
        if (result[1].status === 'fulfilled') setConversation(result[1].value as Conversation); else errors.push(failure(result[1].reason));
        if (result[2].status === 'fulfilled') {
          rows = result[2].value as GoalExecutionReceipt[];
          if (!Array.isArray(rows) || rows.some(row => !matches(row, { source_id: sid, payload: row.request_payload }))) throw Error('目标历史回执格式或授权关联无效');
          setHistory(rows);
        } else errors.push(failure(result[2].reason));
      }
      const saved = pendingRef.current ?? stopRef.current ?? selected.current;
      if (saved) {
        const found = saved.goal_id ?? rows.find(row => row.request_id === saved.payload.request_id)?.id;
        if (found) {
          const row = await api<GoalExecutionReceipt>(`/goal-executions/${found}`);
          if (!alive.current || version !== serial.current) return;
          adopt(row, saved, true);
        }
      }
      if (errors.length) { setReadError(errors.join('；')); setVerified(false); }
    } catch (e) { if (alive.current && version === serial.current) { setReadError(failure(e)); setVerified(false); } }
    finally { if (version === serial.current) { reading.current = false; if (alive.current) setLoading(false); } }
  }
  useEffect(() => {
    alive.current = true; reading.current = false; const previous = document.activeElement; dialog.current?.showModal();
    try {
      const raw = sessionStorage.getItem(pendingKey), stopping = sessionStorage.getItem(stopKey);
      const saved = raw ? decode(raw) : null, stopped = stopping ? decode(stopping) : null;
      if (stopped && !stopped.goal_id) throw Error('原停止记录缺少目标编号');
      pendingRef.current = saved; setPending(saved); stopRef.current = stopped; setStopPending(stopped);
      if (saved && stopped && saved.goal_id !== stopped.goal_id) throw Error('存在两个不同目标的恢复记录，两者均保留，请先核对原记录');
      const restore = saved ?? stopped;
      if (restore) { sourceRef.current = restore.source_id; setPayload(restore.payload); selected.current = restore; }
    } catch (e) { lock(`${failure(e)}。恢复存储前不能新建或停止。`); }
    void load();
    const timer = window.setInterval(() => {
      const row = selected.current;
      if (!document.hidden && row && readable.current && (polling.current || !!stopRef.current)) void load();
    }, 3000);
    return () => { alive.current = false; serial.current++; window.clearInterval(timer); if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  function change(next: GoalExecutionRequest) { setPayload(next); setConfirmed(false); }
  let valid = false;
  try { decode(JSON.stringify({ source_id: sourceRef.current, payload })); valid = !!conversation && !conversation.archived && conversation.member_ids.includes(payload.agent_id) && payload.candidate_ids.every(v => agents.some(a => a.id === v && a.enabled)); } catch { /* Incomplete draft. */ }
  async function submit() {
    if (writing.current || reading.current || !readable.current) return;
    const saved = pendingRef.current;
    if (saved?.goal_id) { void load(); return; }
    if (!saved && (!valid || !confirmed || readError || current || stopRef.current)) return;
    const sent = saved ? { source_id: saved.source_id, payload: saved.payload } : { source_id: sourceRef.current, payload: structuredClone(payload) };
    try {
      if (!saved && sessionStorage.getItem(pendingKey) !== null) { lock('已有原目标请求待恢复，不能覆盖。'); return; }
      decode(JSON.stringify(sent)); sessionStorage.setItem(pendingKey, JSON.stringify(sent));
    } catch (e) { lock(`${failure(e)}。尚未发送。`); return; }
    pendingRef.current = sent; setPending(sent); writing.current = true; setBusy(true); setError(''); setConfirmed(false); setVerified(false);
    try { const row = await api<GoalExecutionReceipt>(`/conversations/${sent.source_id}/goal-executions`, 'POST', sent.payload); if (alive.current) adopt(row, sent, false); }
    catch (e) {
      if (alive.current) {
        if (!saved && e instanceof ApiError && e.status >= 400 && e.status < 500) {
          try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(pendingKey, JSON.stringify(rejected)); pendingRef.current = rejected; setPending(rejected); } catch { lock('无法保存首次拒绝记录，原请求保留。'); }
        }
        setError(`${failure(e)}。原授权和请求编号已保留。`);
      }
    } finally { writing.current = false; if (alive.current) { setBusy(false); void load(); } }
  }
  function editRejected() {
    const saved = pendingRef.current;
    if (busy || !readable.current || !saved?.rejected || saved.goal_id) return;
    try { sessionStorage.removeItem(pendingKey); pendingRef.current = null; setPending(null); setPayload({ ...saved.payload, request_id: crypto.randomUUID() }); selected.current = null; setCurrent(null); setConfirmed(false); }
    catch { lock('无法清除首次拒绝记录，原授权保留。'); }
  }
  function finish() {
    if (busy || loading || readError || !readable.current || !verified || !pendingRef.current || !current || !matches(current, pendingRef.current)) return;
    try { sessionStorage.removeItem(pendingKey); pendingRef.current = null; setPending(null); setNotice('已独立核验目标授权。任务状态继续保留在此处和目标历史；没有新增规划或执行。'); }
    catch { lock('无法结束原目标请求核对，记录保留。'); }
  }
  async function stop() {
    if (busy || loading || !readable.current || !verified || !current || stopConfirmed !== current.id || stopRef.current) return;
    const saved = anchor(current);
    try { if (sessionStorage.getItem(stopKey) !== null) throw Error('另有停止请求待恢复'); sessionStorage.setItem(stopKey, JSON.stringify(saved)); }
    catch (e) { lock(`${failure(e)}。尚未发送停止。`); return; }
    stopRef.current = saved; setStopPending(saved); writing.current = true; setBusy(true); setStopConfirmed(''); setVerified(false);
    try { await api(`/goal-executions/${saved.goal_id}/stop`, 'POST', { confirm: true }); }
    catch (e) { if (alive.current) setError(`停止响应未确认：${failure(e)}。仅刷新此目标，不自动重发。`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(); } }
  }
  async function openProject(row: GoalExecutionReceipt) {
    if (!row.launch || !verified || !readable.current) return;
    setBusy(true);
    try { const project = await api<Conversation>(`/conversations/${row.launch.collaboration.project_conversation_id}`); if (project.id !== row.launch.collaboration.project_conversation_id || project.type !== 'project') throw Error('项目关联不一致'); onOpenProject(project); onClose(); }
    catch (e) { setError(failure(e)); } finally { setBusy(false); }
  }
  function view(row: GoalExecutionReceipt) { if (busy || loading || pendingRef.current || stopRef.current) return; selected.current = anchor(row); setCurrent(null); setStopConfirmed(''); setVerified(false); void load(); }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="goal-title" onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}><header><h2 id="goal-title">目标执行与恢复</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p>明确授权秘书根据共享摘要规划一次，自动创建项目并启动固定首批任务。可能调用模型和CLI并产生费用；任务上限不是费用上限。仅共享下方摘要，原消息及私聊历史不会自动复制。最终成果由你验收。</p>
    <p>源会话：{conversation?.title ?? sourceRef.current}</p><button disabled={busy || loading} onClick={() => void load()}>{loading ? '读取目标中…' : '刷新目标状态'}</button>
    {error && <p role="alert" className="error">{error}</p>}{readError && <p role="alert" className="error">{readError}。已有目标不因读取失败而消失；原请求保留。</p>}{storageError && <p role="alert" className="error">{storageError}</p>}{notice && <p role="status">{notice}</p>}
    {pending && <section><h3>原目标授权待核对</h3><small>请求 {pending.payload.request_id} · 原消息 {pending.payload.source_message_id}</small><p className="task-source">冻结共享摘要：{pending.payload.shared_brief}</p><p>协调人 {agents.find(a => a.id === pending.payload.agent_id)?.name ?? pending.payload.agent_id} · 候选 {pending.payload.candidate_ids.length} 位 · 最多 {pending.payload.max_tasks} 项任务 · {pending.payload.confirm_handoff ? '授权固定首批成果自动交接' : '前置成果逐项由Owner批准'}</p><button disabled={busy || loading || !!storageError} onClick={() => void submit()}>{pending.goal_id ? '读取原目标回执（不再规划）' : '同键核对原目标授权'}</button>{pending.rejected && <button disabled={busy || !!storageError} onClick={editRejected}>修改首次未接受的目标授权</button>}{verified && current && <button disabled={busy || loading || !!readError || !!storageError} onClick={finish}>确认已读回并结束原请求核对</button>}<p>同键核对可能首次提交原授权；已知目标编号后只读取，不自动重规划。</p></section>}
    {!pending && !current && !stopPending && payload.source_message_id && <form onSubmit={e => { e.preventDefault(); void submit(); }}><fieldset className="task-fields" disabled={busy || loading || !!storageError || !!readError || !conversation || conversation.archived}>
      {source?.id === payload.source_message_id && <details><summary>原目标消息（仅供你参考）</summary><p className="task-source">{source.content}</p></details>}
      <label>明确共享给秘书与项目的摘要<textarea required maxLength={16000} value={payload.shared_brief} onChange={e => change({ ...payload, shared_brief: e.target.value })} /></label>
      <label>目标协调人<select required value={payload.agent_id} onChange={e => change({ ...payload, agent_id: e.target.value })}><option value="">选择源会话中的协调人</option>{agents.filter(a => a.enabled && conversation?.member_ids.includes(a.id)).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <fieldset className="member-choices"><legend>明确授权候选身份（含协调人，{payload.candidate_ids.length}/100）</legend>{agents.filter(a => a.enabled || payload.candidate_ids.includes(a.id)).map(a => <label className="check" key={a.id}><input type="checkbox" checked={payload.candidate_ids.includes(a.id)} disabled={!payload.candidate_ids.includes(a.id) && (!a.enabled || payload.candidate_ids.length >= 100)} onChange={e => change({ ...payload, candidate_ids: e.target.checked ? [...payload.candidate_ids, a.id] : payload.candidate_ids.filter(v => v !== a.id) })} />{a.name}{!a.enabled && ' · 已停用'}</label>)}</fieldset>
      <label>最多任务数<input type="number" min={1} max={16} step={1} required value={payload.max_tasks} onChange={e => change({ ...payload, max_tasks: Number(e.target.value) })} /></label>
      <label className="check"><input type="checkbox" checked={payload.confirm_handoff} onChange={e => change({ ...payload, confirm_handoff: e.target.checked })} />授权固定首批成果按依赖自动交接，无需中间逐项审批</label>
      <label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我确认共享上述摘要和候选公开身份，授权规划一次、在任务上限内创建并启动项目，并按上述交接选择执行；可能产生模型及CLI费用，最终成果由我验收</label><button className="primary" disabled={!valid || !confirmed}>授权秘书组织并执行</button>
    </fieldset></form>}
    {!pending && !current && !payload.source_message_id && <p>从 Owner 消息下“交给秘书组织并执行”开始。本入口也可恢复原请求和查看历史。</p>}
    {current && <section><h3>{stage[current.state]}</h3><small>目标 {current.id} · 规划 {current.planning_run_id}</small><p>规划实际状态：{runState[current.planning.state]} · {current.planning.model ?? '模型信息待回执'}；输入/输出token：{current.planning.usage?.prompt_tokens ?? '未知'} / {current.planning.usage?.completion_tokens ?? '未知'}</p>{current.error && <p className="error">{current.error}</p>}{current.planning.error && <p className="error">{current.planning.error}</p>}
      {current.state === 'unknown' && <p>结果未知，仅核对现有目标；不会自动再规划或换一个任务执行。</p>}{current.planning.state === 'unknown' && <button disabled={busy} onClick={() => setCheckId(current.planning_run_id)}>核查原模型调用</button>}
      {current.launch && <><p>项目：{current.launch.collaboration.approved_plan.title} · {current.launch.collaboration.project_conversation_id}</p><small>启动 {current.launch_id} · 固定批次 {current.batch?.id}</small><button disabled={busy || loading || !verified || !!storageError} onClick={() => void openProject(current)}>打开此目标的项目群</button><button disabled={busy || loading || !verified || !!storageError} onClick={() => setBatch(current)}>查看此目标固定批次</button></>}
      {current.batch?.items.map(item => <article className="task-card" key={item.execution.id}><h4>{item.task.title} · {runState[item.execution.state]}</h4><small>执行 {item.execution.id}</small><p>Owner验收：{item.review?.decision === 'approved' ? '已批准' : item.review?.decision === 'rejected' ? '已拒绝' : '尚未批准'}</p>{!item.dependencies.ready && <p>{item.dependencies.blocked_reason}</p>}</article>)}
      {stopPending ? <p role="status">原停止请求待独立读取核对，仅刷新目标状态，不自动重发停止。</p> : !current.stop_requested && <><label className="check"><input type="checkbox" disabled={busy || loading || !verified || !!storageError} checked={stopConfirmed === current.id} onChange={e => setStopConfirmed(e.target.checked ? current.id : '')} />我确认停止此目标的后续调度，并请求停止其固定批次；运行中或未知实例仍需核查</label><button disabled={busy || loading || !verified || !!storageError || stopConfirmed !== current.id} onClick={() => void stop()}>确认停止此目标</button></>}
      {current.stop_requested && <p>停止授权已持久保存。模型或执行仍运行、等待退出或未知时，不代表已经停止。</p>}
    </section>}
    <section><h3>目标执行历史（最近100条及活动目标）</h3>{history.map(row => <p key={row.id}><button disabled={busy || loading || !!pending || !!stopPending || !!storageError} onClick={() => view(row)}>{row.launch?.collaboration.approved_plan.title ?? row.request_payload.shared_brief.slice(0, 45)} · {stage[row.state]}</button><small> · {row.id}</small></p>)}</section>
    {batch?.launch && <CollaborationExecutionPanel key={batch.batch?.id} plan={batch.launch.collaboration} initialBatchId={batch.batch?.id} onClose={() => setBatch(null)} />}
    {checkId && <ModelRunReconciliation runId={checkId} onClose={() => setCheckId('')} onVerified={() => { void load(); }} />}
  </dialog>;
}
