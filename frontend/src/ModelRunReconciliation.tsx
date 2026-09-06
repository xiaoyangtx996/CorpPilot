import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type ModelUnknownRun, type ModelReconciliationRequest, type ModelReconciliationRecord } from './api';

const storageKey = 'corppilot.model-reconciliation-pending.v1';
type Pending = { run_id: string; payload: ModelReconciliationRequest; rejected?: boolean };
const message = (e: unknown) => e instanceof Error ? e.message : '请求失败';
function matches(row: ModelReconciliationRecord, sent: Pending) { return row.run_id === sent.run_id && (Object.keys(sent.payload) as (keyof ModelReconciliationRequest)[]).every(k => row[k] === sent.payload[k]); }
function decode(raw: string): Pending {
  const row = JSON.parse(raw), p = row?.payload;
  if (!row || Object.keys(row).some(k => !['run_id', 'payload', 'rejected'].includes(k)) || typeof row.run_id !== 'string' || !/^[0-9a-f-]{36}$/.test(row.run_id) || row.rejected !== undefined && typeof row.rejected !== 'boolean' || !p || Object.keys(p).sort().join() !== 'attempt,local_request_stopped,note,provider_effects_checked,request_id,requirement_version' || typeof p.request_id !== 'string' || !/^[0-9a-f-]{36}$/.test(p.request_id) || !Number.isSafeInteger(p.attempt) || p.attempt < 1 || !Number.isSafeInteger(p.requirement_version) || p.requirement_version < 1 || p.local_request_stopped !== true || p.provider_effects_checked !== true || typeof p.note !== 'string' || !p.note.trim() || p.note.length > 2000) throw Error('原核查请求格式无效');
  return row;
}
export function ModelRunReconciliation({ runId = '', onClose, onVerified }: { runId?: string; onClose: () => void; onVerified?: (record: ModelReconciliationRecord) => void }) {
  const dialog = useRef<HTMLDialogElement>(null), alive = useRef(false), serial = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null);
  const storageReadable = useRef(false);
  const [selected, setSelected] = useState(runId), [run, setRun] = useState<ModelUnknownRun | null>(null), [rows, setRows] = useState<ModelUnknownRun[]>([]), [record, setRecord] = useState<ModelReconciliationRecord | null>(null), [pending, setPending] = useState<Pending | null>(null);
  const [conversation, setConversation] = useState(''), [agent, setAgent] = useState(''), [localStopped, setLocalStopped] = useState(false), [providerChecked, setProviderChecked] = useState(false), [note, setNote] = useState('');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [readError, setReadError] = useState(''), [storageError, setStorageError] = useState('');
  function accept(row: ModelReconciliationRecord, sent?: Pending) {
    if (!storageReadable.current) return;
    if (sent && !matches(row, sent)) return;
    try { if (sent) { sessionStorage.removeItem(storageKey); pendingRef.current = null; setPending(null); } setError(''); onVerified?.(row); }
    catch { setStorageError('无法清除待确认请求，仍保留原内容。'); }
  }
  async function load(id: string) {
    if (writing.current) return;
    const version = ++serial.current; setLoading(true); setReadError('');
    const results = await Promise.allSettled([api<ModelUnknownRun[]>('/model-run-reconciliations/pending'), ...(id ? [api<ModelUnknownRun>(`/runs/${id}`), api<ModelReconciliationRecord | null>(`/runs/${id}/reconciliation`)] : [])]);
    if (!alive.current || version !== serial.current) return;
    const errors: string[] = [];
    if (results[0].status === 'fulfilled') setRows(results[0].value as ModelUnknownRun[]); else errors.push(message(results[0].reason));
    if (id) {
      const detail = results[1], receipt = results[2];
      if (detail.status === 'fulfilled') {
        const value = detail.value as ModelUnknownRun;
        const listed = results[0].status === 'fulfilled' ? (results[0].value as ModelUnknownRun[]).find(r => r.id === id) : null;
        setRun({ ...value, ...listed });
        const metadata = await Promise.allSettled([api<Conversation>(`/conversations/${value.conversation_id}`), api<Agent>(`/agents/${value.agent_id}`)]);
        if (!alive.current || version !== serial.current) return;
        setConversation(metadata[0].status === 'fulfilled' ? metadata[0].value.title : value.conversation_id); setAgent(metadata[1].status === 'fulfilled' ? metadata[1].value.name : value.agent_id);
      } else { setRun(null); errors.push(message(detail.reason)); }
      if (receipt.status === 'fulfilled') {
        const value = receipt.value as ModelReconciliationRecord | null;
        if (value && value.run_id !== id) { setRecord(null); errors.push('声明与原 Run 不一致'); }
        else { setRecord(value); if (value) { const sent = pendingRef.current; if (!sent || matches(value, sent)) accept(value, sent ?? undefined); } }
      } else { setRecord(null); errors.push(message(receipt.reason)); }
    }
    setReadError(errors.join('；')); setLoading(false);
  }
  useEffect(() => {
    alive.current = true; const previous = document.activeElement; dialog.current?.showModal(); let id = runId;
    try { const raw = sessionStorage.getItem(storageKey), saved = raw ? decode(raw) : null; pendingRef.current = saved; setPending(saved); storageReadable.current = true; if (saved) { id = saved.run_id; setNote(saved.payload.note); } }
    catch { storageReadable.current = false; setStorageError('无法安全恢复原模型核查请求，请恢复会话存储后重新打开。'); }
    setSelected(id); void load(id); return () => { alive.current = false; serial.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (!saved && (!run || loading || readError || record || run.state !== 'unknown' || !localStopped || !providerChecked || !note.trim())) return;
    const sent: Pending = { run_id: saved?.run_id ?? run!.id, payload: saved?.payload ?? { request_id: crypto.randomUUID(), attempt: run!.attempt, requirement_version: run!.requirement_version, local_request_stopped: true, provider_effects_checked: true, note: note.trim() } };
    try { sessionStorage.setItem(storageKey, JSON.stringify(sent)); } catch { setStorageError('无法保存完整原请求，尚未发送。'); return; }
    pendingRef.current = sent; setPending(sent); writing.current = true; serial.current++; setBusy(true); setError('');
    try { await api<ModelReconciliationRecord>(`/runs/${sent.run_id}/reconciliation`, 'POST', sent.payload); }
    catch (error) { if (alive.current) { if (!saved && error instanceof ApiError && error.status >= 400 && error.status < 500) { try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(storageKey, JSON.stringify(rejected)); pendingRef.current = rejected; setPending(rejected); } catch { setStorageError('首次拒绝证据保存失败，原请求仍锁定。'); } } setError(`${message(error)}。原请求保留，不自动重试。`); } }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(sent.run_id); } }
  }
  function editRejected() {
    if (busy || !pendingRef.current?.rejected || storageError) return;
    try { sessionStorage.removeItem(storageKey); pendingRef.current = null; setPending(null); setLocalStopped(false); setProviderChecked(false); setError(''); void load(selected); }
    catch { setStorageError('无法解除首次拒绝请求锁定。'); }
  }
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="model-check-title" onCancel={event => { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }}><header><h2 id="model-check-title">模型调用核查</h2><button disabled={busy} onClick={onClose}>关闭</button></header><p>这是 Owner 对本地请求、供应商结果及费用的人工声明，不是供应商已停止或零费用的机器证明。声明本身不新建调用；此前已授权且排队的同来源请求可能继续执行，原 unknown 不会重发，新请求须另行确认。</p><button disabled={busy || loading} onClick={() => void load(selected)}>刷新原调用与核查记录</button>
    {error && <p className="error" role="alert">{error}</p>}{readError && <p className="error" role="alert">{readError}。读取失败时不能确认已核查。</p>}{storageError && <p className="error" role="alert">{storageError}</p>}
    {pending ? <section><h3>原核查请求待确认</h3><p>Run {pending.run_id} · 请求 {pending.payload.request_id}</p><p>{pending.payload.note}</p><button disabled={busy || !!storageError} onClick={() => void submit()}>同键核对原声明</button>{pending.rejected && <button disabled={busy || !!storageError} onClick={editRejected}>修改首次未接受的声明</button>}{record && !matches(record, pending) && <section><p>同一 Run 已有另一份不可覆盖声明，此记录不表示原请求成功。</p><p>{record.note}</p><small>{record.request_id} · {record.reconciled_at}</small><button disabled={busy || loading || !!readError || !!storageError} onClick={() => { const row = record; try { sessionStorage.removeItem(storageKey); pendingRef.current = null; setPending(null); onVerified?.(row); } catch { setStorageError('不能清除原请求，继续保持锁定。'); } }}>接受已有声明并结束原请求等待</button></section>}</section> : <section><h3>待核查模型调用</h3>{!loading && !readError && !rows.length && <p>暂无待核查模型调用。</p>}{rows.map(row => <p key={row.id}><button disabled={busy || loading} onClick={() => { setSelected(row.id); setRun(null); setRecord(null); setLocalStopped(false); setProviderChecked(false); setNote(''); void load(row.id); }}>{row.kind === 'peer_review' ? 'Agent评议' : row.kind === 'planning' ? '协作提案' : row.kind === 'retrospective' ? '记忆复盘' : '普通回复'} · {row.id}</button></p>)}</section>}
    {run && <section><h3>{run.kind === 'peer_review' ? 'Agent评议' : run.kind === 'planning' ? '协作提案' : run.kind === 'retrospective' ? '记忆复盘' : run.kind === 'reply' ? '普通回复' : '模型请求'} · {run.state}</h3><p>会话：{conversation} · Agent：{agent}</p><small>Run {run.id} · attempt {run.attempt} · v{run.requirement_version}{run.scope_id && ` · ${run.scope} ${run.scope_id}`}</small><p>模型 {run.model ?? '未知'} · 输入/输出 token {run.usage?.prompt_tokens ?? '未知'} / {run.usage?.completion_tokens ?? '未知'}</p>{run.error && <p>{run.error}</p>}{record && !pending ? <section><h3>已读回不可变核查声明</h3><p>{record.note}</p><small>{record.reconciled_at}</small><p>原调用仍为unknown；关闭后需明确结束原请求查看，并重新选择和确认才能再次调用。</p></section> : !pending && <form onSubmit={event => { event.preventDefault(); void submit(); }}><fieldset className="task-fields" disabled={busy || loading || !!readError || !!storageError || run.state !== 'unknown'}><label className="check"><input type="checkbox" checked={localStopped} onChange={event => setLocalStopped(event.target.checked)} />我已核查本地请求及其子进程已停止</label><label className="check"><input type="checkbox" checked={providerChecked} onChange={event => setProviderChecked(event.target.checked)} />我已核查供应商侧结果、可能继续执行的影响和费用</label><label>核查依据<textarea required maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></label><button disabled={!localStopped || !providerChecked || !note.trim()}>确认保存人工核查声明</button></fieldset></form>}</section>}
  </dialog>;
}
