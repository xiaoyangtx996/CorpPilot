import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type Message, type PlanningRequest, type PlanningRun, type CollaborationPlan, type ModelSettingsValue, type ReplyRuntime } from './api';

const storageKey = 'corppilot.planning-pending.v1';
type Pending = { source_id: string; payload: PlanningRequest; run_id?: string; rejected?: boolean };
const labels = { queued: '排队中', running: '模型生成中', completed: '提案已生成，待审阅', failed: '失败', cancelled: '已取消', unknown: '结果未知' };
const active = (run: PlanningRun) => run.state === 'queued' || run.state === 'running';
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败';
const isText = (value: unknown) => typeof value === 'string' && !!value.trim() && value.length <= 120;
function decode(raw: string): Pending {
  const row = JSON.parse(raw), p = row?.payload;
  if (!row || typeof row !== 'object' || Array.isArray(row) || Object.keys(row).some(k => !['source_id', 'payload', 'run_id', 'rejected'].includes(k)) || !isText(row.source_id) || row.run_id !== undefined && !isText(row.run_id) || row.rejected !== undefined && typeof row.rejected !== 'boolean' || !p || typeof p !== 'object' || Object.keys(p).sort().join() !== 'agent_id,candidate_ids,request_id,source_message_id' || !isText(p.agent_id) || !isText(p.source_message_id) || !isText(p.request_id) || !Array.isArray(p.candidate_ids) || p.candidate_ids.length < 1 || p.candidate_ids.length > 100 || p.candidate_ids.some((id: unknown) => !isText(id)) || new Set(p.candidate_ids).size !== p.candidate_ids.length) throw new Error('原提案请求格式无效');
  return row;
}
function matches(a: PlanningRequest, b: PlanningRequest) {
  return a.agent_id === b.agent_id && a.source_message_id === b.source_message_id && a.request_id === b.request_id && a.candidate_ids.length === b.candidate_ids.length && a.candidate_ids.every((id, i) => id === b.candidate_ids[i]);
}

export function PlanningPanel({ conversationId, source, onClose, onImport, onRecoverCollaboration }: { conversationId: string; source?: Message; onClose: () => void; onImport: (conversationId: string, plan: CollaborationPlan) => void; onRecoverCollaboration: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), serial = useRef(0), writing = useRef(false), reading = useRef<number | null>(null), pendingRef = useRef<Pending | null>(null);
  const sourceRef = useRef(conversationId);
  const [sourceId, setSourceId] = useState(conversationId), [conversation, setConversation] = useState<Conversation | null>(null), [agents, setAgents] = useState<Agent[]>([]);
  const [pending, setPending] = useState<Pending | null>(null), [current, setCurrent] = useState<PlanningRun | null>(null), [history, setHistory] = useState<PlanningRun[]>([]);
  const [payload, setPayload] = useState<PlanningRequest>(() => ({ request_id: crypto.randomUUID(), source_message_id: source?.id ?? '', agent_id: '', candidate_ids: [] }));
  const [query, setQuery] = useState(''), [confirmed, setConfirmed] = useState(false), [loading, setLoading] = useState(true), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false);
  const [storageError, setStorageError] = useState(''), [readError, setReadError] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [importBlocked, setImportBlocked] = useState(false);
  const [settings, setSettings] = useState<ModelSettingsValue | null>(null), [runtime, setRuntime] = useState<ReplyRuntime | null>(null);
  function adopt(row: PlanningRun, sent: Pending) {
    if (row.conversation_id !== sent.source_id || !matches(row.request_payload, sent.payload) || sent.run_id && sent.run_id !== row.id) { setStorageError('服务端提案与原请求不一致，已锁定原请求，请核查。'); return; }
    const saved = { source_id: sent.source_id, payload: sent.payload, run_id: row.id };
    setCurrent(row); setError('');
    try { sessionStorage.setItem(storageKey, JSON.stringify(saved)); pendingRef.current = saved; setPending(saved); setStorageError(''); }
    catch { setStorageError('无法保存已收到的提案编号，原请求仍保留，请恢复会话存储后核对。'); }
  }
  async function load(id: string) {
    if (writing.current || reading.current !== null) return;
    const version = ++serial.current; reading.current = version; setLoading(true);
    const saved = pendingRef.current;
    const results = await Promise.allSettled([api<Agent[]>('/agents'), api<ModelSettingsValue>('/model-settings'), api<ReplyRuntime>('/runtime'), ...(id ? [api<Conversation>(`/conversations/${id}`), api<PlanningRun[]>(`/conversations/${id}/collaboration-proposals`)] : []), ...(saved?.run_id ? [api<PlanningRun>(`/collaboration-proposals/${saved.run_id}`)] : [])]);
    if (alive.current && version === serial.current) {
      const [people, config, status, convo, records] = results; const errors: string[] = [];
      if (people.status === 'fulfilled') setAgents(people.value as Agent[]); else errors.push(`身份：${failure(people.reason)}`);
      if (config.status === 'fulfilled') setSettings(config.value as ModelSettingsValue); else { setSettings(null); errors.push(`模型设置：${failure(config.reason)}`); }
      if (status.status === 'fulfilled') setRuntime(status.value as ReplyRuntime); else { setRuntime(null); errors.push(`调度：${failure(status.reason)}`); }
      if (id) {
        if (convo.status === 'fulfilled') setConversation(convo.value as Conversation); else { setConversation(null); errors.push(`源会话：${failure(convo.reason)}`); }
        if (records.status === 'fulfilled') {
          const rows = records.value as PlanningRun[]; setHistory(rows); setLoaded(true);
          const found = saved && rows.find(row => row.request_id === saved.payload.request_id);
          if (found && saved && !saved.run_id) adopt(found, saved);
        } else errors.push(`提案历史：${failure(records.reason)}`);
      }
      if (saved?.run_id) { const detail = results.at(-1)!; if (detail.status === 'fulfilled') adopt(detail.value as PlanningRun, saved); else errors.push(`原提案详情：${failure(detail.reason)}`); }
      setReadError(errors.join('；')); setLoading(false);
    }
    if (reading.current === version) reading.current = null;
    if (alive.current && version !== serial.current && reading.current === null && !writing.current) void load(sourceRef.current);
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal(); let id = conversationId;
    try { const raw = sessionStorage.getItem(storageKey), saved = raw ? decode(raw) : null; pendingRef.current = saved; setPending(saved); if (saved) { id = saved.source_id; setPayload(saved.payload); } }
    catch { setStorageError('无法安全读取原提案请求，请恢复会话存储后重新打开；暂不能提交或开始新提案。'); }
    sourceRef.current = id; setSourceId(id); void load(id);
    return () => { alive.current = false; serial.current++; reading.current = null; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  const poll = !!pending && (!current || active(current)) || history.some(active);
  useEffect(() => { if (!poll) return; const timer = window.setInterval(() => void load(sourceId), 1000); return () => window.clearInterval(timer); }, [poll, sourceId]);
  function change(next: PlanningRequest) { setPayload(next); setConfirmed(false); }
  const enabled = agents.filter(agent => agent.enabled);
  const valid = !!payload.source_message_id && !!conversation && !conversation.archived && enabled.some(a => a.id === payload.agent_id && conversation.member_ids.includes(a.id)) && payload.candidate_ids.length > 0 && payload.candidate_ids.length <= 100 && payload.candidate_ids.every(id => enabled.some(a => a.id === id));
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (!saved && (loading || readError || !valid || !confirmed)) return;
    if (saved?.run_id) { void load(saved.source_id); return; }
    const sent = saved ? { source_id: saved.source_id, payload: saved.payload } : { source_id: sourceId, payload };
    try { sessionStorage.setItem(storageKey, JSON.stringify(sent)); } catch { setStorageError('无法保存原提案请求，尚未发送。'); return; }
    pendingRef.current = sent; setPending(sent); writing.current = true; serial.current++; setBusy(true); setConfirmed(false); setError(''); setNotice('');
    try { const row = await api<PlanningRun>(`/conversations/${sent.source_id}/collaboration-proposals`, 'POST', sent.payload); if (alive.current) adopt(row, sent); }
    catch (error) {
      if (alive.current && !saved && error instanceof ApiError && error.status >= 400 && error.status < 500) { try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(storageKey, JSON.stringify(rejected)); pendingRef.current = rejected; setPending(rejected); } catch { setStorageError('无法记录首次拒绝证据，仍须按原请求核对。'); } }
      if (alive.current) setError(`${failure(error)}。原请求已保留，未自动重发。`);
    } finally { writing.current = false; if (alive.current) { setBusy(false); setLoading(false); void load(sent.source_id); } }
  }
  function release(rejected = false) {
    if (writing.current || storageError || (!rejected && (!current || !['completed', 'failed', 'cancelled'].includes(current.state))) || rejected && !pendingRef.current?.rejected) return;
    try { sessionStorage.removeItem(storageKey); pendingRef.current = null; setPending(null); setCurrent(null); setConfirmed(false); setError(''); setNotice(rejected ? '首次请求明确未被接受，保留字段供修改并重新确认。' : '原记录仍保留在服务端历史。新请求须重新选择目标并明确确认。'); setPayload({ ...(rejected ? payload : { agent_id: '', candidate_ids: [], source_message_id: source?.id ?? '' }), request_id: crypto.randomUUID() }); if (!rejected) { serial.current++; reading.current = null; sourceRef.current = conversationId; setSourceId(conversationId); setConversation(null); setHistory([]); setLoaded(false); void load(conversationId); } }
    catch { setStorageError('无法清除当前查看记录，尚未解除锁定。'); }
  }
  async function cancel(row: PlanningRun) {
    if (writing.current) return; writing.current = true; serial.current++; setBusy(true); setError('');
    try { await api(`/runs/${row.id}/cancel`, 'POST', {}); }
    catch (error) { if (alive.current) setError(`取消结果待核对：${failure(error)}。继续读取原提案状态，不会新建请求。`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); setLoading(false); void load(sourceId); } }
  }
  function importPlan(row: PlanningRun) {
    if (!row.proposal || row.state !== 'completed' || busy) return;
    try { if (sessionStorage.getItem('corppilot.collaboration-pending.v1') !== null) { setImportBlocked(true); setError('存在未确认的协作创建请求，不能用模型提案覆盖。请先打开协作恢复入口核对。'); return; } }
    catch { setImportBlocked(true); setError('无法读取协作恢复记录，暂不能导入；请恢复会话存储后核对。'); return; }
    onImport(row.conversation_id, { ...row.proposal, source_message_id: row.source_message_id, coordinator_id: row.agent_id, request_id: crypto.randomUUID() });
  }
  function record(row: PlanningRun) { return <article className="task-card"><header><h3>{labels[row.state]}</h3><small>{new Date(row.created_at).toLocaleString()}</small></header><small>提案 Run {row.id} · 原消息 {row.source_message_id}</small><p>协调人：{agents.find(a => a.id === row.agent_id)?.name ?? row.agent_id} · 模型：{row.model ?? '尚未知'} · 输入/输出 token：{row.usage?.prompt_tokens ?? '未知'} / {row.usage?.completion_tokens ?? '未知'}</p><details><summary>本次冻结候选目录（{row.candidate_snapshot.length} 位）</summary>{row.candidate_snapshot.map(a => <p key={a.id}>{a.name} · {a.template_id} · 技能：{a.skills.join('、') || '未配置'}</p>)}</details>{row.error && <p className="error">{row.error}</p>}{row.state === 'running' && <p>模型调用已开始，不能保证停止；不会自动重试。</p>}{row.state === 'unknown' && <p className="error">结果未知，须核查供应商调用；当前保留原请求，不提供替代调用。</p>}{row.state === 'queued' && <button disabled={busy} onClick={() => void cancel(row)}>取消此提案排队</button>}{row.proposal && <section><h3>{row.proposal.title}</h3><p className="task-source">拟共享摘要：{row.proposal.shared_brief}</p>{row.proposal.tasks.map(t => <article className="task-card" key={t.key}><h4>{t.title}</h4><p>负责人：{row.candidate_snapshot.find(a => a.id === t.agent_id)?.name ?? t.agent_id}</p><p className="task-source">范围：{t.scope}</p><p className="task-source">验收：{t.acceptance}</p><p>前置：{t.depends_on.map(key => row.proposal?.tasks.find(t => t.key === key)?.title ?? key).join('、') || '无'}</p></article>)}<p>这是模型建议，尚未创建项目或任务。导入后检查共享内容、负责人和依赖，再单独确认创建。</p><button disabled={busy || !!storageError} onClick={() => importPlan(row)}>导入协作表单并审阅</button></section>}</article>; }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="planning-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}><header><h2 id="planning-title">模型协作提案</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p>指定一位协调人，根据这条 Owner 消息和你选择的公开身份目录生成计划。会调用模型并可能产生费用；不会读取他人私有历史、创建群聊或启动 CLI。</p><p>源会话：{conversation?.title ?? (sourceId || '未选择')}</p><button disabled={busy || loading} onClick={() => void load(sourceId)}>{loading ? '读取提案中…' : '刷新提案状态'}</button>
    {readError && <p className="error" role="alert">{readError}。读取失败不代表没有提案，已有结果保留。</p>}{storageError && <p className="error" role="alert">{storageError}</p>}{error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{importBlocked && <button disabled={busy} onClick={onRecoverCollaboration}>打开协作计划与恢复</button>}
    {runtime && <p className="muted">模型控制服务：{runtime.running ? '运行中' : '未运行'} · 活动请求 {runtime.active_requests}{runtime.error && ` · ${runtime.error}`}</p>}{settings && (!settings.enabled || !settings.configured || !settings.credential_available) && <p className="muted">模型配置尚未就绪，提交后将排队等待；可在左栏模型设置中配置，也可取消排队。</p>}
    {pending ? <section><h3>已保存原提案请求</h3><small>请求 {pending.payload.request_id} · 原消息 {pending.payload.source_message_id}</small>{current ? record(current) : <p>尚未确认服务端状态，保留原协调人、候选顺序与请求 ID。</p>}<button disabled={busy || loading || !!storageError} onClick={() => void submit()}>{pending.run_id ? '读取原提案（不调用模型）' : '核对原提案请求'}</button>{!pending.run_id && <p>若服务端尚未接收，手动核对可能首次提交这次已确认的模型调用。</p>}{pending.rejected && <button disabled={busy || !!storageError} onClick={() => release(true)}>修改首次未接受的请求</button>}{current && ['completed', 'failed', 'cancelled'].includes(current.state) && <button disabled={busy || !!storageError} onClick={() => release()}>结束查看并开始新提案</button>}</section>
      : payload.source_message_id ? <form onSubmit={event => { event.preventDefault(); void submit(); }}><small>固定目标消息：{payload.source_message_id}</small>{source?.id === payload.source_message_id && <p className="task-source">{source.content}</p>}<fieldset className="task-fields" disabled={busy || loading || !!readError || !!storageError || !conversation || conversation.archived}><label>提案协调人<select required value={payload.agent_id} onChange={event => change({ ...payload, agent_id: event.target.value })}><option value="">选择源会话中启用的 Agent</option>{enabled.filter(a => conversation?.member_ids.includes(a.id)).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label><label>搜索候选身份<input value={query} onChange={event => setQuery(event.target.value)} /></label><fieldset className="member-choices"><legend>明确选择候选身份（{payload.candidate_ids.length}/100）</legend>{agents.filter(a => a.enabled || payload.candidate_ids.includes(a.id)).filter(a => `${a.name} ${a.template_id} ${a.skills.join(' ')}`.toLowerCase().includes(query.toLowerCase())).map(a => <label className="check" key={a.id}><input type="checkbox" checked={payload.candidate_ids.includes(a.id)} disabled={!payload.candidate_ids.includes(a.id) && (!a.enabled || payload.candidate_ids.length >= 100)} onChange={event => change({ ...payload, candidate_ids: event.target.checked ? [...payload.candidate_ids, a.id] : payload.candidate_ids.filter(id => id !== a.id) })} />{a.name}{!a.enabled && ' · 已停用（可取消选择）'} · {a.skills.join('、') || '未配置技能'}</label>)}</fieldset><p>已选：{payload.candidate_ids.map(id => agents.find(a => a.id === id)?.name ?? id).join('、') || '暂无'}</p><label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认让所选协调人调用模型生成提案，仅提供目标消息与上述候选公开信息</label><button className="primary" disabled={!valid || !confirmed}>确认调用模型生成提案</button></fieldset>{conversation?.archived && <p>源会话已归档，不能提交新提案。</p>}</form> : <p>从 Owner 消息下“请 Agent 提出协作计划”选择目标；此入口可恢复原请求并查看当前会话提案。</p>}
    <section><h3>提案历史（最近 100 条及活动请求）</h3>{loaded && !loading && !readError && !history.length && <p>暂无模型协作提案。</p>}{history.filter(row => row.id !== current?.id).map(row => <details key={row.id}><summary>{row.proposal?.title ?? labels[row.state]} · {new Date(row.created_at).toLocaleString()}</summary>{record(row)}</details>)}</section>
  </dialog>;
}
