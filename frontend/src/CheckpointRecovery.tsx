import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type CheckpointPreview, type CheckpointRequest, type CheckpointReceipt, type CheckpointDetail, type CollaborationReceipt } from './api';

type Pending = { source_batch_id: string; payload: CheckpointRequest; preview: CheckpointPreview; recovery_id?: string; batch_id?: string; receipt?: CheckpointReceipt; rejected?: boolean };
const id = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const canonical = (v: unknown): string => JSON.stringify(v, (_, value) => value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value);
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
const failure = (e: unknown) => e instanceof Error ? e.message : '读取失败';
function immutable(row: CheckpointReceipt): CheckpointReceipt {
  const batch = row.batch as CheckpointDetail['batch'];
  return { ...row, batch: { id: batch.id, collaboration_id: batch.collaboration_id, project_conversation_id: batch.project_conversation_id, request_id: batch.request_id, request_payload: batch.request_payload, tasks: batch.tasks, created_at: batch.created_at } };
}
async function validPreview(value: CheckpointPreview, source: string, plan: CollaborationReceipt) {
  if (!value || Object.keys(value).sort().join() !== 'blockers,fingerprint,snapshot,source_batch_id' || value.source_batch_id !== source || value.snapshot?.source_batch_id !== source || value.snapshot.collaboration_id !== plan.id || value.snapshot.project_conversation_id !== plan.project_conversation_id || !Array.isArray(value.blockers) || value.blockers.some(x => typeof x !== 'string') || !Array.isArray(value.snapshot.nodes) || value.snapshot.nodes.length > 1000) throw Error('检查点身份或结构无效');
  const seen = new Set<string>();
  for (const node of value.snapshot.nodes) {
    if (!id(node.task_id) || seen.has(node.task_id) || !Number.isSafeInteger(node.requirement_version) || node.requirement_version < 1 || !['reuse', 'retry', 'blocked'].includes(node.action) || typeof node.external !== 'boolean' || !Array.isArray(node.dependencies) || node.dependencies.some(x => !id(x)) || !Array.isArray(node.artifacts) || node.artifacts.some(x => !id(x.id) || typeof x.path !== 'string' || !Number.isSafeInteger(x.size) || x.size < 0 || !/^[0-9a-f]{64}$/.test(x.sha256))) throw Error('检查点节点无效');
    if (node.action !== 'blocked' && (!id(node.execution_id) || node.latest_execution_id !== node.execution_id || node.execution_requirement_version !== node.requirement_version || !Number.isSafeInteger(node.attempt) || (node.attempt ?? 0) < 1)) throw Error('检查点实例无效');
    if (node.action === 'retry' && (node.external || !Object.values(plan.task_ids).includes(node.task_id))) throw Error('不能重新执行外部任务');
    seen.add(node.task_id);
  }
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(value.snapshot))))).map(x => x.toString(16).padStart(2, '0')).join('');
  if (hash !== value.fingerprint) throw Error('检查点指纹不一致');
}
function match(row: CheckpointReceipt, sent: Pending, details = false) {
  const snapshot = sent.preview.snapshot, retry = snapshot.nodes.filter(n => n.action === 'retry');
  if (!row || !id(row.id) || !id(row.batch_id) || row.source_batch_id !== sent.source_batch_id || row.request_id !== sent.payload.request_id || !same(row.request_payload, sent.payload) || row.checkpoint_fingerprint !== sent.payload.checkpoint_fingerprint || !same(row.snapshot, snapshot) || sent.recovery_id && row.id !== sent.recovery_id || sent.batch_id && row.batch_id !== sent.batch_id) throw Error('恢复回执与原授权不一致，原请求继续锁定');
  const batch = row.batch;
  if (batch?.id !== row.batch_id || batch.collaboration_id !== snapshot.collaboration_id || batch.project_conversation_id !== snapshot.project_conversation_id || !id(batch.request_id) || batch.request_payload?.request_id !== batch.request_id || !Array.isArray(batch.tasks) || batch.tasks.length !== retry.length || !Array.isArray(batch.request_payload.tasks) || batch.request_payload.tasks.length !== retry.length) throw Error('恢复批次关联不一致');
  if (new Set(batch.tasks.map(x => x.task_id)).size !== retry.length || new Set(batch.tasks.map(x => x.execution_id)).size !== retry.length || new Set(batch.request_payload.tasks.map(x => x.task_id)).size !== retry.length) throw Error('恢复批次任务重复');
  for (const node of retry) {
    const binding = batch.tasks.find(x => x.task_id === node.task_id), payload = batch.request_payload.tasks.find(x => x.task_id === node.task_id);
    if (!binding || !id(binding.execution_id) || binding.execution_id === node.execution_id || !id(binding.request_id) || !payload || !same(payload, { task_id: node.task_id, expected_version: node.requirement_version, previous_execution_id: node.execution_id, reconciliation_note: sent.payload.reconciliation_note.trim() })) throw Error('恢复批次未精确匹配重试节点');
    if (details) {
      const items = (row as CheckpointDetail).batch.items;
      if (!Array.isArray(items) || items.length !== retry.length || new Set(items.map(x => x.execution.id)).size !== retry.length) throw Error('恢复实例详情不完整');
      const item = items.find(x => x.execution.id === binding.execution_id), run = item?.execution;
      if (!item || item.task.id !== node.task_id || item.task.conversation_id !== snapshot.project_conversation_id || !run || run.task_id !== node.task_id || run.agent_id !== node.agent_id || run.requirement_version !== node.requirement_version || run.attempt !== (node.attempt ?? 0) + 1 || run.previous_execution_id !== node.execution_id || run.request_id !== binding.request_id) throw Error('恢复实例详情与原检查点不一致');
    }
  }
  if (sent.receipt && !same(immutable(row), sent.receipt)) throw Error('已知恢复关联被替换，原请求继续锁定');
}

export function CheckpointRecovery({ sourceBatchId, plan, onClose, onOpenBatch }: { sourceBatchId: string; plan: CollaborationReceipt; onClose: () => void; onOpenBatch: (id: string) => void }) {
  const key = `corppilot.checkpoint-recovery-pending.v1.${sourceBatchId}`, base = `/project-executions/${sourceBatchId}/checkpoint-recoveries`;
  const dialog = useRef<HTMLDialogElement>(null), version = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null);
  const [preview, setPreview] = useState<CheckpointPreview | null>(null), [pending, setPending] = useState<Pending | null>(null), [history, setHistory] = useState<CheckpointReceipt[]>([]), [receipt, setReceipt] = useState<CheckpointDetail | null>(null);
  const [note, setNote] = useState(''), [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [storageError, setStorageError] = useState('');
  function save(row: Pending) { sessionStorage.setItem(key, JSON.stringify(row)); pendingRef.current = row; setPending(row); }
  async function readReceipt(sent: Pending, token: number) {
    let identity = sent.recovery_id;
    if (!identity) {
      const rows = await api<CheckpointReceipt[]>(base);
      if (token !== version.current) return;
      setHistory(rows);
      const found = rows.find(row => row.request_id === sent.payload.request_id);
      if (!found) { setError('最近100条中尚未找到原请求。不能据此认定未受理；保留原请求。'); return; }
      match(found, sent); identity = found.id;
      sent = { ...sent, recovery_id: found.id, batch_id: found.batch_id, receipt: immutable(found), rejected: false }; save(sent);
    }
    const row = await api<CheckpointDetail>(`/checkpoint-recoveries/${identity}`);
    if (token !== version.current) return;
    match(row, sent, true);
    sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setReceipt(row); setPreview(sent.preview); setError('');
  }
  async function load() {
    if (writing.current) return;
    const token = ++version.current; setBusy(true); setError(''); setConfirmed(false);
    try {
      if (pendingRef.current) await readReceipt(pendingRef.current, token);
      else {
        const [value, rows] = await Promise.all([api<CheckpointPreview>(`/project-executions/${sourceBatchId}/checkpoint`), api<CheckpointReceipt[]>(base)]);
        await validPreview(value, sourceBatchId, plan);
        if (token === version.current) { setPreview(value); setHistory(rows); }
      }
    } catch (e) { if (token === version.current) setError(failure(e)); }
    finally { if (token === version.current) setBusy(false); }
  }
  useEffect(() => {
    const token = ++version.current; dialog.current?.showModal(); setBusy(true);
    void (async () => {
      try {
        const raw = sessionStorage.getItem(key);
        if (raw !== null) {
          const row: Pending = JSON.parse(raw), p = row?.payload;
          if (!row || Object.keys(row).some(x => !['source_batch_id', 'payload', 'preview', 'recovery_id', 'batch_id', 'receipt', 'rejected'].includes(x)) || row.source_batch_id !== sourceBatchId || !p || Object.keys(p).sort().join() !== 'checkpoint_fingerprint,confirm,reconciliation_note,request_id' || !id(p.request_id) || p.confirm !== true || typeof p.reconciliation_note !== 'string' || !p.reconciliation_note.trim() || p.reconciliation_note.length > 2000 || p.checkpoint_fingerprint !== row.preview?.fingerprint || row.recovery_id !== undefined && !id(row.recovery_id) || row.batch_id !== undefined && !id(row.batch_id) || row.rejected !== undefined && typeof row.rejected !== 'boolean') throw Error('原恢复请求损坏');
          await validPreview(row.preview, sourceBatchId, plan);
          if (row.preview.blockers.length || !row.preview.snapshot.nodes.some(n => n.action === 'retry')) throw Error('原恢复授权不是可恢复检查点');
          if (row.receipt) match(row.receipt, { ...row, receipt: undefined });
          if (token !== version.current) return;
          pendingRef.current = row; setPending(row); setPreview(row.preview);
        }
        if (token === version.current) void load();
      } catch (e) { if (token === version.current) { setStorageError(`${failure(e)}；原存储保留，不能创建替代请求。`); setBusy(false); } }
    })();
    return () => { version.current++; };
  }, [sourceBatchId]);
  async function submit() {
    if (busy || storageError || writing.current) return;
    const original = pendingRef.current;
    if (original?.recovery_id) { void load(); return; }
    if (!original && (!preview || preview.blockers.length || !confirmed || !note.trim())) return;
    const sent: Pending = original ? { ...original, rejected: false } : { source_batch_id: sourceBatchId, payload: { request_id: crypto.randomUUID(), checkpoint_fingerprint: preview!.fingerprint, reconciliation_note: note.trim(), confirm: true }, preview: preview! };
    try { save(sent); } catch { setStorageError('无法保存完整恢复请求，尚未发送。'); return; }
    writing.current = true; const token = ++version.current; setBusy(true); setConfirmed(false); setError('');
    let posted = false;
    try {
      const row = await api<CheckpointReceipt>(base, 'POST', sent.payload);
      posted = true;
      if (token !== version.current) return;
      match(row, sent);
      const accepted = { ...sent, recovery_id: row.id, batch_id: row.batch_id, receipt: immutable(row) }; save(accepted);
      await readReceipt(accepted, token);
    } catch (e) {
      if (token === version.current) {
        if (!original && !posted && e instanceof ApiError && [400, 409, 422].includes(e.status)) { try { save({ ...sent, rejected: true }); } catch { setStorageError('拒绝证据保存失败，继续保持锁定。'); } }
        setError(`${failure(e)}。原请求保留，不自动重新授权或重发。`);
      }
    } finally { writing.current = false; if (token === version.current) setBusy(false); }
  }
  function releaseRejected() {
    if (!pendingRef.current?.rejected || busy || storageError) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setPreview(null); setNote(''); setConfirmed(false); void load(); }
    catch { setStorageError('无法解除首次拒绝记录，继续锁定。'); }
  }
  async function openHistory(row: CheckpointReceipt) {
    if (busy || pendingRef.current || storageError) return;
    const token = ++version.current; setBusy(true); setError('');
    try {
      const value: CheckpointPreview = { source_batch_id: row.source_batch_id, snapshot: row.snapshot, fingerprint: row.checkpoint_fingerprint, blockers: [] };
      await validPreview(value, sourceBatchId, plan);
      const sent: Pending = { source_batch_id: sourceBatchId, payload: row.request_payload, preview: value, recovery_id: row.id, batch_id: row.batch_id, receipt: immutable(row) };
      match(row, sent);
      const detail = await api<CheckpointDetail>(`/checkpoint-recoveries/${row.id}`);
      match(detail, sent, true);
      if (token === version.current) { setReceipt(detail); setPreview(value); }
    } catch (e) { if (token === version.current) setError(failure(e)); }
    finally { if (token === version.current) setBusy(false); }
  }
  const shown = pending?.preview ?? preview;
  const title = (taskId: string) => plan.approved_plan.tasks.find(task => plan.task_ids[task.key] === taskId)?.title ?? `外部前置 ${taskId}`;
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="checkpoint-title" onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}>
    <header><h2 id="checkpoint-title">检查点恢复</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p>保留已批准成果，仅重新授权可重试节点。这不是 CLI 进程内存恢复；新批次可能产生新的模型费用和外部副作用，且必须独立停止。新前置成果仍需 Owner 批准。</p>
    <small>源批次 {sourceBatchId}</small>
    {error && <p role="alert" className="error">{error}</p>}{storageError && <p role="alert" className="error">{storageError}</p>}
    <button disabled={busy || !!storageError} onClick={() => void load()}>{pending ? '只读取原恢复回执' : '重新读取检查点'}</button>
    {shown && <section><h3>固定检查点</h3><small>指纹 {shown.fingerprint}</small>{shown.snapshot.nodes.map(node => <article className="task-card" key={node.task_id}><h4>{node.action === 'reuse' ? '保留批准成果' : node.action === 'retry' ? '重新执行' : '阻塞'} · {title(node.task_id)}</h4><small>任务 {node.task_id}</small><p>原执行 {node.execution_id ?? '无'} · 需求 v{node.requirement_version} · {node.state}{node.external ? ' · 外部前置引用' : ''}</p><details><summary>成果证据（{node.artifacts.length} 个）</summary>{node.artifacts.map(a => <p key={a.id}>成果 {a.path} · {a.size} bytes · SHA-256 {a.sha256}</p>)}</details></article>)}{shown.blockers.map((text, i) => <p className="error" key={i}>{text}</p>)}</section>}
    {pending ? <section><h3>原恢复请求待核对</h3><p>{pending.payload.request_id}</p><p>{pending.payload.reconciliation_note}</p><button disabled={busy || !!storageError || !!pending.recovery_id} onClick={() => void submit()}>使用同一请求重试恢复</button><p>若原请求尚未受理，同请求重试可能首次创建新批次。</p>{pending.rejected && <button disabled={busy || !!storageError} onClick={releaseRejected}>重新读取并授权未受理请求</button>}</section> : !receipt && <fieldset disabled={busy || !!storageError || !preview || !!preview.blockers.length}>
      <label>恢复核查说明<textarea maxLength={2000} value={note} onChange={e => { setNote(e.target.value); setConfirmed(false); }} /></label>
      <label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我已核查原实例与外部影响，并授权重试节点产生新的 CLI、模型调用及费用</label>
      <button disabled={!confirmed || !note.trim()} onClick={() => void submit()}>确认从检查点创建新批次</button>
    </fieldset>}
    {receipt && <section><h3>已核验恢复批次</h3><p>恢复 {receipt.id} · 新批次 {receipt.batch_id}</p><button disabled={busy || !!pending || !!storageError} onClick={() => onOpenBatch(receipt.batch_id)}>打开新恢复批次</button></section>}
    <details><summary>检查点恢复历史（最近100条）</summary>{history.map(row => <p key={row.id}><button disabled={busy || !!pending || !!storageError} onClick={() => void openHistory(row)}>查看恢复 {row.id}</button> · {row.created_at}</p>)}</details>
  </dialog>;
}
