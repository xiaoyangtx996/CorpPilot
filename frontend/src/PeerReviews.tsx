import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type Message, type PeerReviewRequest, type PeerReviewRun, type ModelReconciliationRecord } from './api';
import { ModelRunReconciliation } from './ModelRunReconciliation';

const storageKey = 'corppilot.peer-review-pending.v1';
type Pending = { conversation_id: string; source: Message; payload: PeerReviewRequest; run_id?: string; rejected?: boolean };
const failure = (e: unknown) => e instanceof Error ? e.message : '请求失败';
const text = (v: unknown) => typeof v === 'string' && !!v.trim() && v.length <= 120;
const labels = { queued: '排队中', running: '评议生成中', completed: '评议已发布', failed: '失败', cancelled: '已取消', unknown: '结果未知' };
const active = (r: PeerReviewRun) => r.state === 'queued' || r.state === 'running';
function decode(raw: string): Pending {
  const row = JSON.parse(raw), p = row?.payload, s = row?.source;
  if (!row || Object.keys(row).some(k => !['conversation_id', 'source', 'payload', 'run_id', 'rejected'].includes(k)) || !text(row.conversation_id) || row.run_id !== undefined && !text(row.run_id) || row.rejected !== undefined && typeof row.rejected !== 'boolean' || !p || Object.keys(p).sort().join() !== 'agent_id,confirm,request_id,source_message_id' || !text(p.agent_id) || !text(p.request_id) || !text(p.source_message_id) || p.confirm !== true || !s || s.id !== p.source_message_id || s.conversation_id !== row.conversation_id || s.sender_kind !== 'agent' || !text(s.sender_id) || s.sender_id === p.agent_id || typeof s.content !== 'string' || !s.content.trim() || s.content.length > 16000) throw Error('原评议请求格式无效');
  return row;
}
function matches(row: PeerReviewRun, saved: Pending) {
  const p = row.request_payload, q = saved.payload;
  return row.conversation_id === saved.conversation_id && row.source_message_id === q.source_message_id && row.agent_id === q.agent_id && row.request_id === q.request_id && !!p && p.confirm === true && p.agent_id === q.agent_id && p.source_message_id === q.source_message_id && p.request_id === q.request_id && (!saved.run_id || saved.run_id === row.id) && !!row.source_run_id;
}

export function PeerReviews({ conversationId, source, onClose }: { conversationId: string; source?: Message; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), writing = useRef(false), reading = useRef(false), serial = useRef(0), savedRef = useRef<Pending | null>(null), storageReadable = useRef(true);
  const contextRef = useRef(conversationId);
  const [context, setContext] = useState(conversationId), [conversation, setConversation] = useState<Conversation | null>(null), [conversations, setConversations] = useState<Conversation[]>([]), [agents, setAgents] = useState<Agent[]>([]);
  const [chosen, setChosen] = useState<Message | undefined>(source), [target, setTarget] = useState(''), [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null), [current, setCurrent] = useState<PeerReviewRun | null>(null), [history, setHistory] = useState<PeerReviewRun[]>([]);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [readError, setReadError] = useState(''), [error, setError] = useState(''), [storageError, setStorageError] = useState('');
  const [checkId, setCheckId] = useState(''), [checked, setChecked] = useState<Record<string, ModelReconciliationRecord>>({});
  function adopt(row: PeerReviewRun, saved: Pending, verified = true) {
    if (!storageReadable.current) return;
    if (!matches(row, saved)) { storageReadable.current = false; setStorageError('服务端回执与原来源或完整请求不一致，已锁定，请核查。'); return; }
    const next = { conversation_id: saved.conversation_id, source: saved.source, payload: saved.payload, run_id: row.id };
    try { sessionStorage.setItem(storageKey, JSON.stringify(next)); savedRef.current = next; setPending(next); if (verified) setCurrent(row); setError(''); }
    catch { storageReadable.current = false; setStorageError('无法保存已受理编号，原请求保留，请恢复会话存储后重新打开。'); }
  }
  async function load(id: string) {
    if (reading.current || writing.current) return;
    reading.current = true; const version = ++serial.current; const saved = savedRef.current; setLoading(true);
    try {
      const [people, groups] = await Promise.all([api<Agent[]>('/agents'), api<Conversation[]>('/conversations')]);
      const group = groups.find(c => c.id === id) ?? null;
      const rows = group && group.type !== 'dm' ? await api<PeerReviewRun[]>(`/conversations/${id}/peer-reviews`) : [];
      const found = saved && !saved.run_id ? rows.find(r => r.request_id === saved.payload.request_id) : undefined;
      if (found && saved && !matches(found, saved)) throw Error('历史回执与原请求不一致，原请求继续保留');
      const identity = saved?.run_id ?? found?.id;
      const detail = identity ? await api<PeerReviewRun>(`/peer-reviews/${identity}`) : undefined;
      if (!alive.current || version !== serial.current) return;
      setAgents(people); setConversations(groups.filter(c => c.type !== 'dm')); setConversation(group); setHistory(rows); setReadError('');
      if (saved && detail) adopt(detail, saved);
    } catch (e) { if (alive.current && version === serial.current) setReadError(failure(e)); }
    finally { reading.current = false; if (alive.current) { if (version === serial.current) setLoading(false); else if (!writing.current) void load(contextRef.current); } }
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal(); let id = conversationId;
    try { const raw = sessionStorage.getItem(storageKey); if (raw) { const saved = decode(raw); savedRef.current = saved; setPending(saved); setChosen(saved.source); setTarget(saved.payload.agent_id); id = saved.conversation_id; } }
    catch { storageReadable.current = false; setStorageError('无法安全读取原评议请求，暂不能提交或释放；请恢复会话存储后重新打开。'); }
    contextRef.current = id; setContext(id); void load(id);
    return () => { alive.current = false; serial.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  const polling = !!pending && (!current || active(current)) || history.some(active);
  useEffect(() => { if (!polling) return; const timer = window.setInterval(() => void load(contextRef.current), 1500); return () => clearInterval(timer); }, [polling]);
  const valid = !!chosen && chosen.conversation_id === context && chosen.sender_kind === 'agent' && !!conversation && conversation.type !== 'dm' && !conversation.archived && agents.some(a => a.id === chosen.sender_id && a.enabled && conversation.member_ids.includes(a.id)) && agents.some(a => a.id === target && a.id !== chosen.sender_id && a.enabled && conversation.member_ids.includes(a.id));
  async function submit() {
    if (writing.current || !storageReadable.current) return;
    const old = savedRef.current;
    if (old?.run_id) { void load(old.conversation_id); return; }
    if (!old && (!valid || !confirmed || loading || readError || !chosen)) return;
    const sent: Pending = old ? { conversation_id: old.conversation_id, source: old.source, payload: old.payload } : { conversation_id: context, source: chosen!, payload: { request_id: crypto.randomUUID(), source_message_id: chosen!.id, agent_id: target, confirm: true } };
    try { sessionStorage.setItem(storageKey, JSON.stringify(sent)); } catch { storageReadable.current = false; setStorageError('无法保存完整原请求，尚未发送。'); return; }
    savedRef.current = sent; setPending(sent); writing.current = true; serial.current++; setBusy(true); setConfirmed(false); setError('');
    try { const row = await api<PeerReviewRun>(`/conversations/${sent.conversation_id}/peer-reviews`, 'POST', sent.payload); if (alive.current) adopt(row, sent, false); }
    catch (e) {
      if (alive.current && !old && e instanceof ApiError && e.status >= 400 && e.status < 500) {
        try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(storageKey, JSON.stringify(rejected)); savedRef.current = rejected; setPending(rejected); }
        catch { storageReadable.current = false; setStorageError('无法记录首次拒绝证据，保留原请求。'); }
      }
      if (alive.current) setError(`${failure(e)}。原请求保留，未自动重发。`);
    } finally { writing.current = false; if (alive.current) { setBusy(false); setLoading(false); void load(sent.conversation_id); } }
  }
  async function release(rejected = false) {
    const saved = savedRef.current, row = current;
    if (!saved || writing.current || !storageReadable.current || (rejected ? !saved.rejected || !!saved.run_id : !row || !matches(row, saved) || !(['completed', 'failed', 'cancelled'].includes(row.state) || row.state === 'unknown' && checked[row.id]))) return;
    writing.current = true; serial.current++; setBusy(true); setError('');
    try {
      if (!rejected && row?.state === 'unknown') {
        const receipt = await api<ModelReconciliationRecord | null>(`/runs/${row.id}/reconciliation`);
        if (!receipt || receipt.run_id !== row.id || receipt.local_request_stopped !== true || receipt.provider_effects_checked !== true) throw Error('未读回原Run核查声明');
      }
      if (!alive.current || savedRef.current !== saved) return;
      sessionStorage.removeItem(storageKey); savedRef.current = null; setPending(null); setCurrent(null); setConfirmed(false); setTarget('');
      if (!rejected) setChosen(undefined);
    } catch (e) { if (alive.current) { if (row?.state === 'unknown') setChecked(values => { const next = { ...values }; delete next[row.id]; return next; }); setError(`${failure(e)}。原请求继续保留，尚未释放。`); } }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(contextRef.current); } }
  }
  async function cancel(row: PeerReviewRun) {
    if (writing.current) return; writing.current = true; serial.current++; setBusy(true);
    try { await api(`/runs/${row.id}/cancel`, 'POST', {}); }
    catch (e) { if (alive.current) setError(`取消结果待读取核对：${failure(e)}`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(contextRef.current); } }
  }
  function record(row: PeerReviewRun) { return <article className="task-card"><h3>{labels[row.state]}</h3><p>评议者：{agents.find(a => a.id === row.agent_id)?.name ?? row.agent_id}</p><small>Run {row.id} · 来源消息 {row.source_message_id} · 来源Run {row.source_run_id}</small><p>模型 {row.model ?? '未知'} · 输入/输出 token：{row.usage?.prompt_tokens ?? '未知'} / {row.usage?.completion_tokens ?? '未知'}</p>{row.error && <p className="error">{row.error}</p>}{row.state === 'completed' && <p>评议已发布到原群，请在消息历史点击刷新查看。消息编号：{row.reply_message_id}</p>}{row.state === 'queued' && <button disabled={busy} onClick={() => void cancel(row)}>取消此次排队</button>}{row.state === 'running' && <p>调用已开始，不能保证停止；不会自动重试。</p>}{row.state === 'unknown' && <><p>原结果未知，不能替代调用；请核查本地请求及供应商结果与费用。</p><button disabled={busy} onClick={() => setCheckId(row.id)}>核查此模型调用</button>{checked[row.id] && <p>已读回人工核查声明，原Run仍未知。</p>}</>}</article>; }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="peer-title" onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}><header><h2 id="peer-title">Agent评议与恢复</h2><button autoFocus disabled={busy} onClick={onClose}>关闭</button></header>
    <p>只分享选定这一条Agent消息，不带其他聊天历史或原Owner目标；调用一次模型可能产生费用，不会自动续轮、建群或执行CLI。</p>
    <p>源会话：{conversation?.title ?? (context || '未选择')}</p><button disabled={busy || loading} onClick={() => void load(contextRef.current)}>{loading ? '读取评议中…' : '刷新评议状态'}</button>
    {readError && <p className="error" role="alert">{readError}。读取失败不表示没有请求，已有记录保留。</p>}{storageError && <p className="error" role="alert">{storageError}</p>}{error && <p className="error" role="alert">{error}</p>}
    {chosen && <section><h3>选定源消息全文</h3><p>作者：{agents.find(a => a.id === chosen.sender_id)?.name ?? chosen.sender_id}</p><small>{chosen.id}</small><p className="task-source">{chosen.content}</p></section>}
    {pending ? <section><h3>已冻结原评议请求</h3><p>目标：{agents.find(a => a.id === pending.payload.agent_id)?.name ?? pending.payload.agent_id} · 请求 {pending.payload.request_id}</p>{current ? record(current) : <p>服务端受理状态尚未确认，完整原请求已保留。</p>}<button disabled={busy || loading || !!storageError} onClick={() => void submit()}>{pending.run_id ? '读取原评议（不调用模型）' : '同键核对原评议请求'}</button>{!pending.run_id && <p>若尚未受理，手动核对可能首次提交这次已确认的调用。</p>}{pending.rejected && <button disabled={busy || !!storageError} onClick={() => void release(true)}>修改首次未接受的请求</button>}{current && (['completed', 'failed', 'cancelled'].includes(current.state) || current.state === 'unknown' && checked[current.id]) && <button disabled={busy || loading || !!storageError} onClick={() => void release()}>结束原请求并重新选取</button>}</section>
      : chosen ? <form onSubmit={e => { e.preventDefault(); void submit(); }}><fieldset disabled={busy || loading || !!readError || !!storageError}><label>评议成员<select value={target} onChange={e => { setTarget(e.target.value); setConfirmed(false); }}><option value="">选择另一位同群启用成员</option>{agents.filter(a => a.id !== chosen.sender_id && conversation?.member_ids.includes(a.id) && (a.enabled || a.id === target)).map(a => <option key={a.id} value={a.id} disabled={!a.enabled}>{a.name}{!a.enabled && ' · 已停用'}</option>)}</select></label><label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我确认仅分享此条全文，请所选成员调用一次模型评议</label><button className="primary" disabled={!valid || !confirmed}>确认调用模型评议</button></fieldset></form>
        : <section><p>暂无待提交来源。请关闭此窗口，在董事群或项目群Agent消息下点击“请另一成员评议”。</p><label>查看群聊评议历史<select disabled={busy || loading || !!storageError} value={conversations.some(c => c.id === context) ? context : ''} onChange={e => { const id = e.target.value; serial.current++; contextRef.current = id; setContext(id); setConversation(null); setHistory([]); void load(id); }}><option value="">选择群聊</option>{conversations.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label></section>}
    <section><h3>当前源会话评议历史</h3>{!loading && !readError && !history.length && <p>暂无评议记录。</p>}{history.filter(r => r.id !== current?.id).map(r => <details key={r.id}><summary>{labels[r.state]} · {new Date(r.created_at).toLocaleString()}</summary>{record(r)}</details>)}</section>
    {checkId && <ModelRunReconciliation runId={checkId} onClose={() => setCheckId('')} onVerified={row => setChecked(values => ({ ...values, [row.run_id]: row }))} />}
  </dialog>;
}
