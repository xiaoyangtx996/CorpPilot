import { useEffect, useRef, useState } from 'react';
import { CollaborationExecutionPanel } from './CollaborationExecutionPanel';
import { api, ApiError, type Agent, type Conversation, type Message, type CollaborationPlan, type CollaborationTask, type CollaborationReceipt, type ProjectLaunchRequest, type ProjectLaunchReceipt } from './api';

const launchKey = 'corppilot.project-launch-pending.v1';
const batchPrefix = 'corppilot.project-execution-pending.v1.';
type LaunchPending = { source_id: string; operation: 'launch'; payload: ProjectLaunchRequest; launch_id?: string; rejected?: boolean };
const storageKey = 'corppilot.collaboration-pending.v1';
type Pending = { source_id: string; plan: CollaborationPlan; rejected?: boolean };
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败';
const newTask = (): CollaborationTask => ({ key: `t${crypto.randomUUID().replaceAll('-', '').slice(0, 24)}`, title: '', scope: '', acceptance: '', agent_id: '', depends_on: [] });
const fields = (value: unknown, keys: string) => !!value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join() === keys;
const text = (value: unknown, max: number) => typeof value === 'string' && !!value.trim() && value.length <= max;
function decode(raw: string): Pending {
  const value = JSON.parse(raw), p = value?.plan;
  if (!fields(value, value?.rejected === undefined ? 'plan,source_id' : 'plan,rejected,source_id') || value.rejected !== undefined && typeof value.rejected !== 'boolean' || !text(value.source_id, 120) || !fields(p, 'coordinator_id,request_id,shared_brief,source_message_id,tasks,title') || !text(p.request_id, 120) || !text(p.source_message_id, 120) || !text(p.coordinator_id, 120) || !text(p.title, 120) || !text(p.shared_brief, 16000) || !Array.isArray(p.tasks) || !p.tasks.length || p.tasks.length > 16 || p.tasks.some((t: CollaborationTask) => !fields(t, 'acceptance,agent_id,depends_on,key,scope,title') || typeof t.key !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(t.key) || !text(t.title, 200) || !text(t.scope, 16000) || !text(t.acceptance, 16000) || !text(t.agent_id, 120) || !Array.isArray(t.depends_on) || t.depends_on.some(k => typeof k !== 'string'))) throw new Error('原协作请求格式无效');
  const keys = new Set(p.tasks.map((t: CollaborationTask) => t.key));
  if (keys.size !== p.tasks.length || p.tasks.some((t: CollaborationTask) => t.depends_on.length > 16 || new Set(t.depends_on).size !== t.depends_on.length || t.depends_on.some(k => !keys.has(k))) || cyclic(p.tasks) || new TextEncoder().encode(JSON.stringify(p)).length > 65536) throw new Error('原协作请求依赖或大小无效');
  return value;
}
function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => same(v, b[i]));
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) { const x = a as Record<string, unknown>, y = b as Record<string, unknown>; return Object.keys(x).length === Object.keys(y).length && Object.keys(x).every(k => Object.hasOwn(y, k) && same(x[k], y[k])); }
  return false;
}
function cyclic(tasks: CollaborationTask[]): boolean {
  const done = new Set<string>(), visiting = new Set<string>(), graph = new Map(tasks.map(t => [t.key, t.depends_on]));
  function visit(key: string): boolean { if (visiting.has(key)) return true; if (done.has(key)) return false; visiting.add(key); if ((graph.get(key) ?? []).some(visit)) return true; visiting.delete(key); done.add(key); return false; }
  return tasks.some(t => visit(t.key));
}

function decodeLaunch(raw: string): LaunchPending {
  const row = JSON.parse(raw);
  if (!row || typeof row !== 'object' || Array.isArray(row) || Object.keys(row).some(k => !['source_id', 'operation', 'payload', 'launch_id', 'rejected'].includes(k)) || row.operation !== 'launch' || row.launch_id !== undefined && !text(row.launch_id, 120) || row.rejected !== undefined && typeof row.rejected !== 'boolean' || !(fields(row.payload, 'confirm_execution,plan') || fields(row.payload, 'confirm_execution,confirm_handoff,plan') && typeof row.payload.confirm_handoff === 'boolean') || row.payload.confirm_execution !== true) throw Error('原启动请求格式无效');
  decode(JSON.stringify({ source_id: row.source_id, plan: row.payload.plan }));
  if (new TextEncoder().encode(JSON.stringify(row.payload)).length > 65536) throw Error('启动请求超过64KiB');
  return row;
}
function matchesLaunch(row: ProjectLaunchReceipt, sent: LaunchPending) {
  if (!row || typeof row !== 'object') return false;
  const p = sent.payload.plan, c = row.collaboration, b = row.batch;
  const id = (value: unknown) => text(value, 120) && typeof value === 'string' && value === value.trim();
  const members = [...new Set([p.coordinator_id, ...p.tasks.map(t => t.agent_id)])];
  if (!c || !b || !b.request_payload || !c.task_ids || Array.isArray(c.task_ids) || typeof c.task_ids !== 'object' || !Array.isArray(c.member_ids) || !Array.isArray(b.tasks) || !Array.isArray(b.request_payload.tasks)) return false;
  if (![row.id, row.source_conversation_id, row.request_id, c.id, c.source_conversation_id, c.source_message_id, c.request_id, c.project_conversation_id, c.shared_message_id, c.coordinator_id, b.id, b.collaboration_id, b.project_conversation_id, b.request_id, b.request_payload.request_id].every(id)) return false;
  if (c.coordinator_id !== p.coordinator_id || c.member_ids.length !== members.length || new Set(c.member_ids).size !== members.length || c.member_ids.some(member => !id(member) || !members.includes(member))) return false;
  if (Object.keys(c.task_ids).length !== p.tasks.length || Object.values(c.task_ids).some(value => !id(value)) || new Set(Object.values(c.task_ids)).size !== p.tasks.length || b.tasks.length !== p.tasks.length || b.request_payload.tasks.length !== p.tasks.length) return false;
  if (b.tasks.some(item => !item || !id(item.task_id) || !id(item.execution_id) || !id(item.request_id)) || new Set(b.tasks.map(t => t.task_id)).size !== p.tasks.length || new Set(b.tasks.map(t => t.execution_id)).size !== p.tasks.length || new Set(b.tasks.map(t => t.request_id)).size !== p.tasks.length) return false;
  return row.source_conversation_id === sent.source_id && row.request_id === p.request_id && (!sent.launch_id || row.id === sent.launch_id) && same(row.request_payload, sent.payload) && c.source_conversation_id === sent.source_id && c.source_message_id === p.source_message_id && c.request_id === p.request_id && same(c.approved_plan, p) && b.collaboration_id === c.id && b.project_conversation_id === c.project_conversation_id && b.request_id === b.request_payload.request_id && p.tasks.every((t, i) => {
    const item = b.tasks[i], request = b.request_payload.tasks[i];
    return !!request && item.task_id === c.task_ids[t.key] && request.task_id === item.task_id && request.expected_version === 1 && request.previous_execution_id === null && request.reconciliation_note === '';
  });
}
function pendingBatches() { return Array.from({ length: sessionStorage.length }, (_, i) => sessionStorage.key(i)).filter((k): k is string => !!k && k.startsWith(batchPrefix)).map(k => k.slice(batchPrefix.length)); }

export function CollaborationPanel({ conversationId, source, initialPlan, onClose, onOpenProject }: { conversationId: string; source?: Message; initialPlan?: CollaborationPlan; onClose: () => void; onOpenProject: (conversation: Conversation) => void }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), serial = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null);
  const launchRef = useRef<LaunchPending | null>(null), readable = useRef(true);
  const [launchPending, setLaunchPending] = useState<LaunchPending | null>(null), [launchReceipt, setLaunchReceipt] = useState<ProjectLaunchReceipt | null>(null), [launchHistory, setLaunchHistory] = useState<ProjectLaunchReceipt[]>([]);
  const [operation, setOperation] = useState<'create' | 'launch'>('create'), [blockedBatches, setBlockedBatches] = useState<string[]>([]);
  const [handoff, setHandoff] = useState(false);
  const [sourceId, setSourceId] = useState(conversationId), [conversation, setConversation] = useState<Conversation | null>(null), [agents, setAgents] = useState<Agent[]>([]);
  const [plan, setPlan] = useState<CollaborationPlan>(() => initialPlan ? structuredClone({ ...initialPlan, request_id: crypto.randomUUID() }) : ({ request_id: crypto.randomUUID(), source_message_id: source?.id ?? '', title: '', shared_brief: '', coordinator_id: '', tasks: [newTask()] }));
  const [pending, setPending] = useState<Pending | null>(null), [storageError, setStorageError] = useState(''), [receipt, setReceipt] = useState<CollaborationReceipt | null>(null);
  const [initialBatchId, setInitialBatchId] = useState('');
  const [batchPlan, setBatchPlan] = useState<CollaborationReceipt | null>(null), [origin, setOrigin] = useState<CollaborationReceipt | null>(null);
  const [history, setHistory] = useState<CollaborationReceipt[]>([]), [loaded, setLoaded] = useState(false), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [confirmed, setConfirmed] = useState(false);
  const [readError, setReadError] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  function confirm(row: CollaborationReceipt, sent: Pending) {
    if (!readable.current) return;
    if (row.source_conversation_id !== sent.source_id || row.request_id !== sent.plan.request_id || !same(row.approved_plan, sent.plan)) { setError('回执与原协作计划不一致，原请求继续保留，不能创建替代项目。'); return; }
    setReceipt(row);
    try { sessionStorage.removeItem(storageKey); pendingRef.current = null; setPending(null); setStorageError(''); setError(''); setNotice('已核对原协作请求，项目群及任务只创建一次；本次创建未调用模型或启动执行。'); }
    catch { setStorageError('无法清除待确认请求，请恢复会话存储后按原请求核对。'); }
  }
  function adoptLaunch(row: ProjectLaunchReceipt, sent: LaunchPending, verified: boolean) {
    if (!readable.current) return;
    if (!matchesLaunch(row, sent)) { readable.current = false; setStorageError('启动回执与原计划、操作或固定批次不一致，原请求已锁定。'); return; }
    const next: LaunchPending = { source_id: sent.source_id, operation: 'launch', payload: sent.payload, launch_id: row.id };
    try { sessionStorage.setItem(launchKey, JSON.stringify(next)); launchRef.current = next; setLaunchPending(next); if (verified) setLaunchReceipt(row); setError(''); }
    catch { readable.current = false; setStorageError('不能保存启动编号，原请求保留，请恢复存储后重新打开。'); }
  }
  async function load(id: string) {
    if (writing.current) return;
    const version = ++serial.current; setLoading(true); setReadError('');
    const results = await Promise.allSettled([api<Agent[]>('/agents'), ...(id ? [api<Conversation>(`/conversations/${id}`), api<CollaborationReceipt[]>(`/conversations/${id}/collaboration-plans`), api<ProjectLaunchReceipt[]>(`/conversations/${id}/project-launches`)] : [])]);
    if (!alive.current || version !== serial.current) return;
    const errors: string[] = [], [people, convo, records, launches] = results;
    if (people.status === 'fulfilled') setAgents(people.value as Agent[]); else errors.push(`Agent：${failure(people.reason)}`);
    if (id) {
      setOrigin(null);
      if (convo.status === 'fulfilled' && (convo.value as Conversation).type === 'project') {
        try {
          const originValue = await api<CollaborationReceipt | null>(`/conversations/${id}/origin-plan`);
          if (!alive.current || version !== serial.current) return;
          setOrigin(originValue);
        } catch (error) {
          if (!alive.current || version !== serial.current) return;
          errors.push(`项目来源回执：${failure(error)}`);
        }
      }
      if (convo.status === 'fulfilled') setConversation(convo.value as Conversation); else { setConversation(null); errors.push(`源会话：${failure(convo.reason)}`); }
      if (records.status === 'fulfilled') { const values = records.value as CollaborationReceipt[]; setHistory(values); setLoaded(true); const sent = pendingRef.current; const row = sent && values.find(v => v.request_id === sent.plan.request_id); if (row && sent) confirm(row, sent); }
      else errors.push(`协作历史：${failure(records.reason)}`);
      if (launches.status === 'fulfilled') {
        const rows = launches.value as ProjectLaunchReceipt[]; setLaunchHistory(rows);
        const sent = launchRef.current;
        if (sent && sent.source_id === id) {
          const found = rows.find(r => r.request_id === sent.payload.plan.request_id), identity = sent.launch_id ?? found?.id;
          if (identity) try {
            const detail = await api<ProjectLaunchReceipt>(`/project-launches/${identity}`);
            if (!alive.current || version !== serial.current) return;
            adoptLaunch(detail, sent, true);
          } catch (e) { if (!alive.current || version !== serial.current) return; errors.push(`启动详情：${failure(e)}`); }
        }
      } else errors.push(`启动历史：${failure(launches.reason)}`);
    }
    try { setBlockedBatches(pendingBatches()); } catch { readable.current = false; setStorageError('无法读取待执行请求，不能启动新项目。'); }
    setReadError(errors.join('；')); setLoading(false);
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal(); let id = conversationId;
    try { const raw = sessionStorage.getItem(storageKey); const saved = raw ? decode(raw) : null; pendingRef.current = saved; setPending(saved); if (saved) { id = saved.source_id; setPlan(saved.plan); if (initialPlan) setNotice('已有待确认协作请求，导入未覆盖原计划。模型提案仍保留在提案历史，请先完成当前恢复后重新导入。'); } }
    catch { readable.current = false; setStorageError('无法安全读取原协作请求，请恢复会话存储后重新打开；暂不允许创建新项目。'); }
    try { const raw = sessionStorage.getItem(launchKey); if (raw) { const saved = decodeLaunch(raw); if (pendingRef.current) setNotice('另有待核对启动请求，先核对当前仅创建请求，再关闭并重新打开恢复启动；两者均保留。'); else { launchRef.current = saved; setLaunchPending(saved); setOperation('launch'); setHandoff(saved.payload.confirm_handoff === true); setPlan(saved.payload.plan); id = saved.source_id; if (initialPlan) setNotice('已恢复原启动请求，导入提案未覆盖原计划。'); } } }
    catch { readable.current = false; setStorageError('无法安全读取原启动请求，不能新建或释放，请恢复会话存储后重新打开。'); }
    setSourceId(id); void load(id);
    return () => { alive.current = false; serial.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  function change(next: CollaborationPlan) { setPlan(next); setConfirmed(false); }
  function updateTask(key: string, patch: Partial<CollaborationTask>) { change({ ...plan, tasks: plan.tasks.map(t => t.key === key ? { ...t, ...patch } : t) }); }
  const enabled = agents.filter(a => a.enabled), members = [...new Set([plan.coordinator_id, ...plan.tasks.map(t => t.agent_id)].filter(Boolean))];
  const cycle = cyclic(plan.tasks), oversized = new TextEncoder().encode(JSON.stringify(operation === 'launch' ? { plan, confirm_execution: true, ...(handoff ? { confirm_handoff: true } : {}) } : plan)).length > 65536;
  const invalid = !plan.source_message_id || !plan.title.trim() || !plan.shared_brief.trim() || !conversation || conversation.archived || !enabled.some(a => a.id === plan.coordinator_id && conversation.member_ids.includes(a.id)) || plan.tasks.some(t => !t.title.trim() || !t.scope.trim() || !t.acceptance.trim() || !enabled.some(a => a.id === t.agent_id)) || cycle || oversized;
  async function submit() {
    if (launchRef.current || operation === 'launch') { await submitLaunch(); return; }
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (!saved && !newRequestAllowed()) return;
    if (!saved && (loading || readError || invalid || !confirmed || receipt)) return;
    const sent: Pending = saved ? { source_id: saved.source_id, plan: saved.plan } : { source_id: sourceId, plan };
    try { sessionStorage.setItem(storageKey, JSON.stringify(sent)); } catch { setStorageError('无法保存原协作请求，尚未发送。'); return; }
    pendingRef.current = sent; setPending(sent); writing.current = true; serial.current++; setBusy(true); setError(''); setNotice(''); setConfirmed(false);
    try { const value = await api<CollaborationReceipt>(`/conversations/${sent.source_id}/collaboration-plans`, 'POST', sent.plan); if (alive.current) confirm(value, sent); }
    catch (error) {
      if (alive.current && !saved && error instanceof ApiError && error.status >= 400 && error.status < 500) { try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(storageKey, JSON.stringify(rejected)); pendingRef.current = rejected; setPending(rejected); } catch { setStorageError('无法保存明确拒绝证据，仍须保留原请求。'); } }
      if (alive.current) setError(`${failure(error)}。原计划及请求 ID 已保留，核对不会自动新建替代项目。`);
    } finally { writing.current = false; if (alive.current) { setBusy(false); void load(sent.source_id); } }
  }
  function newRequestAllowed() {
    try {
      const batches = pendingBatches(); setBlockedBatches(batches);
      if (sessionStorage.getItem(storageKey) || sessionStorage.getItem(launchKey)) { setError('已有未核对的创建或启动请求，请先恢复原请求，不能创建替代项目。'); return false; }
      return true;
    } catch { readable.current = false; setStorageError('无法核对原请求存储，尚未提交。'); return false; }
  }
  async function submitLaunch() {
    if (writing.current || !readable.current || storageError) return;
    const saved = launchRef.current;
    if (saved?.launch_id) { void load(saved.source_id); return; }
    if (!saved && (pendingRef.current || loading || readError || invalid || !confirmed || receipt || !newRequestAllowed())) return;
    const sent: LaunchPending = saved ? { source_id: saved.source_id, operation: 'launch', payload: saved.payload } : { source_id: sourceId, operation: 'launch', payload: { plan: structuredClone(plan), confirm_execution: true, ...(handoff ? { confirm_handoff: true } : {}) } };
    try { decodeLaunch(JSON.stringify(sent)); sessionStorage.setItem(launchKey, JSON.stringify(sent)); } catch (e) { setStorageError(`${failure(e)}。尚未发送。`); return; }
    launchRef.current = sent; setLaunchPending(sent); writing.current = true; serial.current++; setBusy(true); setError(''); setNotice(''); setConfirmed(false);
    try { const value = await api<ProjectLaunchReceipt>(`/conversations/${sent.source_id}/project-launches`, 'POST', sent.payload); if (alive.current) adoptLaunch(value, sent, false); }
    catch (e) {
      if (alive.current && !saved && e instanceof ApiError && e.status >= 400 && e.status < 500) try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(launchKey, JSON.stringify(rejected)); launchRef.current = rejected; setLaunchPending(rejected); } catch { readable.current = false; setStorageError('无法保存首次拒绝证据，原启动请求保留。'); }
      if (alive.current) setError(`${failure(e)}。完整启动请求已保留，不会自动重发。`);
    } finally { writing.current = false; if (alive.current) { setBusy(false); void load(sent.source_id); } }
  }
  function editLaunchRejected() {
    const saved = launchRef.current; if (!saved?.rejected || saved.launch_id || busy || !readable.current) return;
    try { sessionStorage.removeItem(launchKey); launchRef.current = null; setLaunchPending(null); setLaunchReceipt(null); setPlan({ ...saved.payload.plan, request_id: crypto.randomUUID() }); setConfirmed(false); setError(''); setNotice('首次启动明确未接受，完整计划保留供修改；须重新确认。'); }
    catch { readable.current = false; setStorageError('无法清除明确拒绝记录，仍保持锁定。'); }
  }
  function finishLaunch() {
    const saved = launchRef.current;
    if (busy || loading || readError || !readable.current || !saved || !launchReceipt || !matchesLaunch(launchReceipt, saved)) return;
    try { sessionStorage.removeItem(launchKey); launchRef.current = null; setLaunchPending(null); setReceipt(launchReceipt.collaboration); setConfirmed(false); setNotice('已独立读回项目和固定首批回执，任务已获执行授权；入队不等于执行成功，最终成果仍需Owner验收。'); }
    catch { readable.current = false; setStorageError('无法结束原请求核对，继续保留。'); }
  }
  function showBatch(row: CollaborationReceipt, batchId = '') { setInitialBatchId(batchId); setBatchPlan(row); }
  async function recoverBatch(id: string) {
    if (writing.current) return; writing.current = true; serial.current++; setBusy(true);
    try { const row = await api<CollaborationReceipt>(`/collaboration-plans/${id}`); if (alive.current) showBatch(row); }
    catch (e) { if (alive.current) setError(`读取原批次所属计划失败：${failure(e)}`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); setLoading(false); } }
  }
  function editRejected() {
    const saved = pendingRef.current; if (!saved?.rejected || busy) return;
    try { sessionStorage.removeItem(storageKey); pendingRef.current = null; setPending(null); setPlan({ ...saved.plan, request_id: crypto.randomUUID() }); setReceipt(null); setConfirmed(false); setError(''); setNotice('首次请求明确未被接受，保留完整计划供修改；下次提交前需要重新确认。'); }
    catch { setStorageError('无法清除未接受请求，暂不允许修改。'); }
  }
  async function openProject(row: CollaborationReceipt) {
    if (writing.current) return; writing.current = true; setBusy(true); setError('');
    try { const value = await api<Conversation>(`/conversations/${row.project_conversation_id}`); if (alive.current) { onOpenProject(value); onClose(); } }
    catch (error) { if (alive.current) setError(`打开项目失败：${failure(error)}。原回执仍保留。`); }
    finally { writing.current = false; if (alive.current) setBusy(false); }
  }
  function summary(value: CollaborationPlan) { return <section><h3>{value.title || '待命名项目'}</h3><p className="task-source">共享摘要：{value.shared_brief || '尚未填写'}</p><p>协调人：{agents.find(a => a.id === value.coordinator_id)?.name ?? value.coordinator_id}</p>{value.tasks.map((t, i) => <article key={t.key} className="task-card"><h4>{i + 1}. {t.title || '未命名任务'} · {agents.find(a => a.id === t.agent_id)?.name ?? t.agent_id}</h4><p className="task-source">范围：{t.scope}</p><p className="task-source">验收：{t.acceptance}</p><p>前置：{t.depends_on.map(k => value.tasks.find(v => v.key === k)?.title ?? k).join('、') || '无'}</p></article>)}</section>; }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="collaboration-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}><header><h2 id="collaboration-title">组建协作项目</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    {initialPlan && !pending && !launchPending && <p role="status">已导入模型提案为可编辑草稿；请核对摘要、负责人及依赖，重新勾选确认后才会创建项目。模型生成步骤已发生；下方可选仅创建项目，或明确授权创建并启动全部任务。</p>}
    <p>从 Owner 消息组织项目群与任务。仅共享你明确填写的摘要；源会话历史不会自动复制。仅创建项目不启动执行；选择批准并启动时，将授权全部任务进入CLI队列，可能调用模型并产生费用。可选择逐项审批前置成果，或明确授权本批成果自动交接；最终成果仍须Owner验收。</p>
    <p>源会话：{conversation?.title ?? (sourceId || '未选择会话')}{conversation?.archived ? ' · 已归档' : ''}</p><button disabled={busy || loading} onClick={() => void load(sourceId)}>{loading ? '读取协作状态中…' : '刷新协作状态'}</button>
    {readError && <p role="alert" className="error">{readError}。读取失败不代表没有项目；已显示回执保留。</p>}{error && <p role="alert" className="error">{error}</p>}{storageError && <p role="alert" className="error">{storageError}</p>}{notice && <p role="status">{notice}</p>}
    {blockedBatches.length > 0 && <section><p className="error">另有批量执行请求可恢复；不妨碍新项目启动。打开同项目控制页时优先恢复该项目原请求，不会覆盖。</p>{blockedBatches.map(id => <button key={id} disabled={busy} onClick={() => void recoverBatch(id)}>恢复原批量执行 · {id}</button>)}</section>}
    {launchPending ? <section><h3>原创建并启动请求待核对</h3><small>源会话 {launchPending.source_id} · 请求 {launchPending.payload.plan.request_id} · 操作：批准并启动全部任务</small>{summary(launchPending.payload.plan)}<p>原交接授权：{launchPending.payload.confirm_handoff ? '仅本批固定任务自动交接，最终成果待Owner验收' : '前置成果逐项由Owner批准'}</p><p>完整计划已冻结。若尚未受理，同键核对可能首次创建项目并启动已授权的全部任务；可能产生CLI和模型费用。</p><button disabled={busy || loading || !!storageError} onClick={() => void submitLaunch()}>{launchPending.launch_id ? '读取原启动回执（不再启动）' : '同键核对原启动请求'}</button>{launchPending.rejected && <button disabled={busy || !!storageError} onClick={editLaunchRejected}>修改首次未接受的启动计划</button>}{launchReceipt ? <><p>已独立GET核对项目 {launchReceipt.collaboration.project_conversation_id} 和固定批次 {launchReceipt.batch.id}，包含 {launchReceipt.batch.tasks.length} 个首次执行；入队不等于成功。</p><button disabled={busy || loading || !!readError || !!storageError} onClick={finishLaunch}>确认已读回并结束原请求核对</button><button disabled={busy || !!storageError} onClick={() => showBatch(launchReceipt.collaboration, launchReceipt.batch.id)}>查看已启动批次与停止控制</button></> : <p>尚未独立读回完整组合回执，原请求保留，不能新建替代项目。</p>}</section>
      : pending ? <section><h3>原协作请求待确认</h3><small>源会话 {pending.source_id} · 原消息 {pending.plan.source_message_id} · 请求 {pending.plan.request_id}</small>{summary(pending.plan)}<p>若服务端尚未收到，核对可能首次创建这次已确认的项目群及任务；不会调用模型或 CLI。</p><button disabled={busy || !!storageError} onClick={() => void submit()}>{busy ? '核对中…' : '核对原协作请求'}</button>{pending.rejected && <button disabled={busy || !!storageError} onClick={editRejected}>修改首次未接受的计划</button>}</section>
      : receipt ? <section><h3>已保存协作回执</h3><small>回执 {receipt.id} · {receipt.created_at}</small>{summary(receipt.approved_plan)}<button disabled={busy} onClick={() => void openProject(receipt)}>打开已创建的项目群</button><button disabled={busy} onClick={() => showBatch(receipt, launchReceipt?.collaboration.id === receipt.id ? launchReceipt.batch.id : '')}>{launchReceipt && launchReceipt.collaboration.id === receipt.id ? '查看已启动批次与停止控制' : '批量执行本协作项目'}</button>{launchReceipt && launchReceipt.collaboration.id === receipt.id && <p>固定首批 {launchReceipt.batch.id}；打开控制页将定位此批次，已有原批量请求则优先恢复。</p>}</section>
      : plan.source_message_id ? <form onSubmit={event => { event.preventDefault(); void submit(); }}><small>原消息 ID：{plan.source_message_id}</small>{source?.id === plan.source_message_id && <details><summary>查看原 Owner 消息（不会自动共享）</summary><p className="task-source">{source.content}</p></details>}<fieldset className="task-fields" disabled={busy || loading || !!readError || !!storageError || !conversation || conversation.archived}>
        <label>项目群名称<input required maxLength={120} value={plan.title} onChange={event => change({ ...plan, title: event.target.value })} /></label><label>明确共享给项目成员的摘要<textarea required maxLength={16000} value={plan.shared_brief} onChange={event => change({ ...plan, shared_brief: event.target.value })} placeholder="主动填写可以共享的需求；不要包含不应共享的私聊内容" /></label>
        <label>协调人<select required value={plan.coordinator_id} onChange={event => change({ ...plan, coordinator_id: event.target.value })}><option value="">选择源会话中已启用的 Agent</option>{enabled.filter(a => conversation?.member_ids.includes(a.id)).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        {plan.tasks.map((t, index) => <section className="task-card" key={t.key}><h3>协作任务 {index + 1}</h3><label>任务 {index + 1} 标题<input required maxLength={120} value={t.title} onChange={event => updateTask(t.key, { title: event.target.value })} /></label><label>任务 {index + 1} 范围<textarea required maxLength={16000} value={t.scope} onChange={event => updateTask(t.key, { scope: event.target.value })} /></label><label>任务 {index + 1} 验收标准<textarea required maxLength={16000} value={t.acceptance} onChange={event => updateTask(t.key, { acceptance: event.target.value })} /></label><label>任务 {index + 1} 负责人<select required value={t.agent_id} onChange={event => updateTask(t.key, { agent_id: event.target.value })}><option value="">选择已启用的 Agent</option>{enabled.map(a => <option key={a.id} value={a.id}>{a.name} · 技能：{a.skills.join('、') || '未配置'}</option>)}</select></label><fieldset className="member-choices"><legend>任务 {index + 1} 前置任务</legend>{plan.tasks.filter(v => v.key !== t.key).map(v => <label className="check" key={v.key}><input type="checkbox" checked={t.depends_on.includes(v.key)} onChange={event => updateTask(t.key, { depends_on: event.target.checked ? [...t.depends_on, v.key] : t.depends_on.filter(k => k !== v.key) })} />{v.title || `任务 ${plan.tasks.indexOf(v) + 1}（待命名）`}</label>)}</fieldset><button type="button" disabled={plan.tasks.length === 1} onClick={() => change({ ...plan, tasks: plan.tasks.filter(v => v.key !== t.key).map(v => ({ ...v, depends_on: v.depends_on.filter(k => k !== t.key) })) })}>删除任务 {index + 1}</button></section>)}
        <button type="button" disabled={plan.tasks.length >= 16} onClick={() => change({ ...plan, tasks: [...plan.tasks, newTask()] })}>添加协作任务</button>{cycle && <p className="error" role="alert">前置任务形成循环，请先调整依赖。</p>}{oversized && <p className="error" role="alert">计划超过 64KiB，请缩短摘要或任务内容后再提交。</p>}
        <section><h3>提交前确认</h3><label>本次操作<select value={operation} onChange={event => { setOperation(event.target.value as 'create' | 'launch'); setConfirmed(false); }}><option value="create">仅创建项目（不启动执行）</option><option value="launch">批准完整计划并启动全部任务</option></select></label>{operation === 'launch' && <label className="check"><input type="checkbox" checked={handoff} onChange={event => { setHandoff(event.target.checked); setConfirmed(false); }} />授权本批按依赖自动交接已保存成果，无需逐项审批；最终成果由我验收</label>}<p>将加入项目群：{members.map(id => agents.find(a => a.id === id)?.name ?? id).join('、') || '尚未选择'}</p>{summary(plan)}<label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{operation === 'launch' ? (handoff ? '我批准上述完整计划及本批自动交接授权；固定首批任务可按依赖继续执行，可能调用CLI和模型并产生费用；拒绝或修改前置成果将阻断后继，最终成果由我验收' : '我批准上述完整计划、共享摘要、全部任务范围与验收及依赖，并授权创建项目后将全部任务首次入队；可能调用CLI和模型并产生费用，前置成果仍须Owner批准') : '我确认向上述成员共享摘要，并按此计划创建项目群、任务及依赖'}</label></section><button className="primary" disabled={invalid || !confirmed}>{operation === 'launch' ? '批准并启动全部任务' : '确认创建协作项目'}</button>
      </fieldset>{conversation?.archived && <p className="muted">源会话已归档，不能创建新项目；已有回执和原请求仍可核对。</p>}</form> : <p>从任意 Owner 消息下的“组建协作项目”开始编排；本入口也可恢复待确认请求并查看当前会话历史。</p>}
    {origin && <section><h3>当前项目的创建回执</h3><p>{origin.approved_plan.title} · {origin.id}</p><button disabled={busy} onClick={() => showBatch(origin)}>批量执行当前项目任务</button></section>}
    <section><h3>已批准的协作历史（最近 100 条）</h3>{loaded && !loading && !readError && !history.length && <p>暂无已批准协作计划。</p>}{history.map(row => <details key={row.id}><summary>{row.approved_plan.title} · {new Date(row.created_at).toLocaleString()}</summary><p>以下为当时批准的计划；项目重命名或任务修订不会改写此回执。</p>{summary(row.approved_plan)}<small>回执 {row.id} · 项目 {row.project_conversation_id}</small><button disabled={busy} onClick={() => void openProject(row)}>打开此项目群</button><button disabled={busy} onClick={() => showBatch(row)}>批量执行本协作项目</button></details>)}</section>
    <section><h3>创建并启动历史</h3>{loaded && !loading && !readError && !launchHistory.length && <p>暂无创建并启动记录。</p>}{launchHistory.map(row => <details key={row.id}><summary>{row.collaboration.approved_plan.title} · {row.created_at}</summary>{summary(row.request_payload.plan)}<p>交接授权：{row.request_payload.confirm_handoff ? '仅本批固定任务自动交接，最终成果待Owner验收' : '前置成果逐项由Owner批准'}</p><p>启动回执 {row.id} · 固定首批 {row.batch.id}；入队不等于完成。打开控制页将定位此编号，已有原批量请求则优先恢复。</p><button disabled={busy} onClick={() => void openProject(row.collaboration)}>打开此项目群</button><button disabled={busy} onClick={() => showBatch(row.collaboration, row.batch.id)}>查看已启动批次与停止控制</button></details>)}</section>
    {batchPlan && <CollaborationExecutionPanel key={`${batchPlan.id}.${initialBatchId}`} plan={batchPlan} initialBatchId={initialBatchId} onClose={() => setBatchPlan(null)} />}
  </dialog>;
}
