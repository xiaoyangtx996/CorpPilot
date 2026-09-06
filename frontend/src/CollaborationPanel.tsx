import { useEffect, useRef, useState } from 'react';
import { CollaborationExecutionPanel } from './CollaborationExecutionPanel';
import { api, ApiError, type Agent, type Conversation, type Message, type CollaborationPlan, type CollaborationTask, type CollaborationReceipt } from './api';

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

export function CollaborationPanel({ conversationId, source, initialPlan, onClose, onOpenProject }: { conversationId: string; source?: Message; initialPlan?: CollaborationPlan; onClose: () => void; onOpenProject: (conversation: Conversation) => void }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), serial = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null);
  const [sourceId, setSourceId] = useState(conversationId), [conversation, setConversation] = useState<Conversation | null>(null), [agents, setAgents] = useState<Agent[]>([]);
  const [plan, setPlan] = useState<CollaborationPlan>(() => initialPlan ? structuredClone({ ...initialPlan, request_id: crypto.randomUUID() }) : ({ request_id: crypto.randomUUID(), source_message_id: source?.id ?? '', title: '', shared_brief: '', coordinator_id: '', tasks: [newTask()] }));
  const [pending, setPending] = useState<Pending | null>(null), [storageError, setStorageError] = useState(''), [receipt, setReceipt] = useState<CollaborationReceipt | null>(null);
  const [batchPlan, setBatchPlan] = useState<CollaborationReceipt | null>(null), [origin, setOrigin] = useState<CollaborationReceipt | null>(null);
  const [history, setHistory] = useState<CollaborationReceipt[]>([]), [loaded, setLoaded] = useState(false), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [confirmed, setConfirmed] = useState(false);
  const [readError, setReadError] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  function confirm(row: CollaborationReceipt, sent: Pending) {
    if (row.source_conversation_id !== sent.source_id || row.request_id !== sent.plan.request_id || !same(row.approved_plan, sent.plan)) { setError('回执与原协作计划不一致，原请求继续保留，不能创建替代项目。'); return; }
    setReceipt(row);
    try { sessionStorage.removeItem(storageKey); pendingRef.current = null; setPending(null); setStorageError(''); setError(''); setNotice('已核对原协作请求，项目群及任务只创建一次；本次创建未调用模型或启动执行。'); }
    catch { setStorageError('无法清除待确认请求，请恢复会话存储后按原请求核对。'); }
  }
  async function load(id: string) {
    const version = ++serial.current; setLoading(true); setReadError('');
    const results = await Promise.allSettled([api<Agent[]>('/agents'), ...(id ? [api<Conversation>(`/conversations/${id}`), api<CollaborationReceipt[]>(`/conversations/${id}/collaboration-plans`)] : [])]);
    if (!alive.current || version !== serial.current) return;
    const errors: string[] = [], [people, convo, records] = results;
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
    }
    setReadError(errors.join('；')); setLoading(false);
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal(); let id = conversationId;
    try { const raw = sessionStorage.getItem(storageKey); const saved = raw ? decode(raw) : null; pendingRef.current = saved; setPending(saved); if (saved) { id = saved.source_id; setPlan(saved.plan); if (initialPlan) setNotice('已有待确认协作请求，导入未覆盖原计划。模型提案仍保留在提案历史，请先完成当前恢复后重新导入。'); } }
    catch { setStorageError('无法安全读取原协作请求，请恢复会话存储后重新打开；暂不允许创建新项目。'); }
    setSourceId(id); void load(id);
    return () => { alive.current = false; serial.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  function change(next: CollaborationPlan) { setPlan(next); setConfirmed(false); }
  function updateTask(key: string, patch: Partial<CollaborationTask>) { change({ ...plan, tasks: plan.tasks.map(t => t.key === key ? { ...t, ...patch } : t) }); }
  const enabled = agents.filter(a => a.enabled), members = [...new Set([plan.coordinator_id, ...plan.tasks.map(t => t.agent_id)].filter(Boolean))];
  const cycle = cyclic(plan.tasks), oversized = new TextEncoder().encode(JSON.stringify(plan)).length > 65536;
  const invalid = !plan.source_message_id || !plan.title.trim() || !plan.shared_brief.trim() || !conversation || conversation.archived || !enabled.some(a => a.id === plan.coordinator_id && conversation.member_ids.includes(a.id)) || plan.tasks.some(t => !t.title.trim() || !t.scope.trim() || !t.acceptance.trim() || !enabled.some(a => a.id === t.agent_id)) || cycle || oversized;
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
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
    {initialPlan && !pending && <p role="status">已导入模型提案为可编辑草稿；请核对摘要、负责人及依赖，重新勾选确认后才会创建项目。模型生成步骤已发生，本次建群不调用模型。</p>}
    <p>从 Owner 消息组织项目群与任务。仅共享你明确填写的摘要；源会话历史不会自动复制。创建后仍需另行授权模型回复与 CLI 执行。</p>
    <p>源会话：{conversation?.title ?? (sourceId || '未选择会话')}{conversation?.archived ? ' · 已归档' : ''}</p><button disabled={busy || loading} onClick={() => void load(sourceId)}>{loading ? '读取协作状态中…' : '刷新协作状态'}</button>
    {readError && <p role="alert" className="error">{readError}。读取失败不代表没有项目；已显示回执保留。</p>}{error && <p role="alert" className="error">{error}</p>}{storageError && <p role="alert" className="error">{storageError}</p>}{notice && <p role="status">{notice}</p>}
    {pending ? <section><h3>原协作请求待确认</h3><small>源会话 {pending.source_id} · 原消息 {pending.plan.source_message_id} · 请求 {pending.plan.request_id}</small>{summary(pending.plan)}<p>若服务端尚未收到，核对可能首次创建这次已确认的项目群及任务；不会调用模型或 CLI。</p><button disabled={busy || !!storageError} onClick={() => void submit()}>{busy ? '核对中…' : '核对原协作请求'}</button>{pending.rejected && <button disabled={busy || !!storageError} onClick={editRejected}>修改首次未接受的计划</button>}</section>
      : receipt ? <section><h3>已保存协作回执</h3><small>回执 {receipt.id} · {receipt.created_at}</small>{summary(receipt.approved_plan)}<button disabled={busy} onClick={() => void openProject(receipt)}>打开已创建的项目群</button><button disabled={busy} onClick={() => setBatchPlan(receipt)}>批量执行本协作项目</button></section>
      : plan.source_message_id ? <form onSubmit={event => { event.preventDefault(); void submit(); }}><small>原消息 ID：{plan.source_message_id}</small>{source?.id === plan.source_message_id && <details><summary>查看原 Owner 消息（不会自动共享）</summary><p className="task-source">{source.content}</p></details>}<fieldset className="task-fields" disabled={busy || loading || !!readError || !!storageError || !conversation || conversation.archived}>
        <label>项目群名称<input required maxLength={120} value={plan.title} onChange={event => change({ ...plan, title: event.target.value })} /></label><label>明确共享给项目成员的摘要<textarea required maxLength={16000} value={plan.shared_brief} onChange={event => change({ ...plan, shared_brief: event.target.value })} placeholder="主动填写可以共享的需求；不要包含不应共享的私聊内容" /></label>
        <label>协调人<select required value={plan.coordinator_id} onChange={event => change({ ...plan, coordinator_id: event.target.value })}><option value="">选择源会话中已启用的 Agent</option>{enabled.filter(a => conversation?.member_ids.includes(a.id)).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
        {plan.tasks.map((t, index) => <section className="task-card" key={t.key}><h3>协作任务 {index + 1}</h3><label>任务 {index + 1} 标题<input required maxLength={120} value={t.title} onChange={event => updateTask(t.key, { title: event.target.value })} /></label><label>任务 {index + 1} 范围<textarea required maxLength={16000} value={t.scope} onChange={event => updateTask(t.key, { scope: event.target.value })} /></label><label>任务 {index + 1} 验收标准<textarea required maxLength={16000} value={t.acceptance} onChange={event => updateTask(t.key, { acceptance: event.target.value })} /></label><label>任务 {index + 1} 负责人<select required value={t.agent_id} onChange={event => updateTask(t.key, { agent_id: event.target.value })}><option value="">选择已启用的 Agent</option>{enabled.map(a => <option key={a.id} value={a.id}>{a.name} · 技能：{a.skills.join('、') || '未配置'}</option>)}</select></label><fieldset className="member-choices"><legend>任务 {index + 1} 前置任务</legend>{plan.tasks.filter(v => v.key !== t.key).map(v => <label className="check" key={v.key}><input type="checkbox" checked={t.depends_on.includes(v.key)} onChange={event => updateTask(t.key, { depends_on: event.target.checked ? [...t.depends_on, v.key] : t.depends_on.filter(k => k !== v.key) })} />{v.title || `任务 ${plan.tasks.indexOf(v) + 1}（待命名）`}</label>)}</fieldset><button type="button" disabled={plan.tasks.length === 1} onClick={() => change({ ...plan, tasks: plan.tasks.filter(v => v.key !== t.key).map(v => ({ ...v, depends_on: v.depends_on.filter(k => k !== t.key) })) })}>删除任务 {index + 1}</button></section>)}
        <button type="button" disabled={plan.tasks.length >= 16} onClick={() => change({ ...plan, tasks: [...plan.tasks, newTask()] })}>添加协作任务</button>{cycle && <p className="error" role="alert">前置任务形成循环，请先调整依赖。</p>}{oversized && <p className="error" role="alert">计划超过 64KiB，请缩短摘要或任务内容后再提交。</p>}
        <section><h3>提交前确认</h3><p>将加入项目群：{members.map(id => agents.find(a => a.id === id)?.name ?? id).join('、') || '尚未选择'}</p>{summary(plan)}<label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认向上述成员共享摘要，并按此计划创建项目群、任务及依赖</label></section><button className="primary" disabled={invalid || !confirmed}>确认创建协作项目</button>
      </fieldset>{conversation?.archived && <p className="muted">源会话已归档，不能创建新项目；已有回执和原请求仍可核对。</p>}</form> : <p>从任意 Owner 消息下的“组建协作项目”开始编排；本入口也可恢复待确认请求并查看当前会话历史。</p>}
    {origin && <section><h3>当前项目的创建回执</h3><p>{origin.approved_plan.title} · {origin.id}</p><button disabled={busy} onClick={() => setBatchPlan(origin)}>批量执行当前项目任务</button></section>}
    <section><h3>已批准的协作历史（最近 100 条）</h3>{loaded && !loading && !readError && !history.length && <p>暂无已批准协作计划。</p>}{history.map(row => <details key={row.id}><summary>{row.approved_plan.title} · {new Date(row.created_at).toLocaleString()}</summary><p>以下为当时批准的计划；项目重命名或任务修订不会改写此回执。</p>{summary(row.approved_plan)}<small>回执 {row.id} · 项目 {row.project_conversation_id}</small><button disabled={busy} onClick={() => void openProject(row)}>打开此项目群</button><button disabled={busy} onClick={() => setBatchPlan(row)}>批量执行本协作项目</button></details>)}</section>
    {batchPlan && <CollaborationExecutionPanel key={batchPlan.id} plan={batchPlan} onClose={() => setBatchPlan(null)} />}
  </dialog>;
}
