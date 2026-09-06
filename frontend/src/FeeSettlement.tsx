import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type FeeRequest, type FeeReceipt } from './api';

type Target = { kind: 'model' | 'cli'; run_id: string; agent_id: string };
type Run = { id: string; agent_id: string; attempt: number; requirement_version: number; state: string };
type Pending = Target & { payload: FeeRequest; receipt?: FeeReceipt; rejected?: boolean };
const fields = ['request_id', 'attempt', 'requirement_version', 'expected_revision', 'amount_micro_usd', 'evidence_reference', 'note', 'confirm'] as const;
const text = (e: unknown) => e instanceof Error ? e.message : '费用读取失败';
const integer = (n: unknown, min = 0): n is number => Number.isSafeInteger(n) && (n as number) >= min;
function valid(p: FeeRequest) {
  return p && Object.keys(p).sort().join() === [...fields].sort().join() && typeof p.request_id === 'string' && !!p.request_id.trim() && p.request_id === p.request_id.trim() && p.request_id.length <= 120 && integer(p.attempt, 1) && integer(p.requirement_version, 1) && integer(p.expected_revision) && integer(p.amount_micro_usd) && p.amount_micro_usd <= 1e12 && p.confirm === true && typeof p.evidence_reference === 'string' && !!p.evidence_reference.trim() && p.evidence_reference === p.evidence_reference.trim() && p.evidence_reference.length <= 500 && typeof p.note === 'string' && !!p.note.trim() && p.note === p.note.trim() && p.note.length <= 2000;
}
export function feeMoney(n: number) { const v = BigInt(n); return `${v / 1000000n}.${(v % 1000000n).toString().padStart(6, '0')}`; }
function amount(value: string) {
  if (!/^\d+(?:\.\d{1,6})?$/.test(value)) throw Error('费用必须明确填写非负 USD 金额，最多6位小数。');
  const [whole, fraction = ''] = value.split('.'), n = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, '0'));
  if (n > 1000000000000n) throw Error('单次完整费用不能超过 1000000 USD。');
  return Number(n);
}
function check(row: FeeReceipt, target: Target, sent?: Pending) {
  const p = row?.request_payload;
  if (!row || row.kind !== target.kind || row.run_id !== target.run_id || row.agent_id !== target.agent_id || row.source !== 'owner_declared' || !integer(row.revision, 1) || !valid(p) || row.revision !== p.expected_revision + 1 || row.request_id !== p.request_id || row.attempt !== p.attempt || row.requirement_version !== p.requirement_version || row.amount_micro_usd !== p.amount_micro_usd || row.evidence_reference !== p.evidence_reference || row.note !== p.note || typeof row.created_at !== 'string' || !row.created_at) throw Error('费用回执的实例、授权或修订关联不一致。');
  if (sent && (fields.some(k => p[k] !== sent.payload[k]) || sent.receipt && (row.created_at !== sent.receipt.created_at || row.revision !== sent.receipt.revision))) throw Error('费用回执与原请求不一致，保留原请求。');
  return row;
}
function decode(raw: string, target: Target): Pending {
  const p = JSON.parse(raw) as Pending;
  if (!p || Object.keys(p).some(k => !['kind', 'run_id', 'agent_id', 'payload', 'receipt', 'rejected'].includes(k)) || p.kind !== target.kind || p.run_id !== target.run_id || p.agent_id !== target.agent_id || !valid(p.payload) || p.rejected !== undefined && typeof p.rejected !== 'boolean' || p.rejected && p.receipt) throw Error('本地费用待确认请求损坏，已保留并锁定提交。');
  if (p.receipt) check(p.receipt, target, p);
  return p;
}
function Receipt({ row }: { row: FeeReceipt }) {
  return <article className="task-card"><p>修订 {row.revision} · USD {feeMoney(row.amount_micro_usd)}</p><p>证据参考：{row.evidence_reference}</p><p>核查说明：{row.note}</p><small>Owner 核查声明 · {row.created_at} · 请求 {row.request_id} · 第{row.attempt}次 / 需求 v{row.requirement_version}</small></article>;
}

export function FeeSettlement({ kind, run_id, agent_id, onClose }: Target & { onClose: () => void }) {
  const target = { kind, run_id, agent_id }, base = `/budget-settlements/${kind}/${run_id}`, key = `corppilot.fee-settlement-pending.v1.${kind}.${run_id}`;
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement), serial = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null), readable = useRef(false);
  const [run, setRun] = useState<Run | null>(null), [latest, setLatest] = useState<FeeReceipt | null>(null), [history, setHistory] = useState<FeeReceipt[]>([]), [verified, setVerified] = useState<FeeReceipt | null>(null);
  const [pending, setPending] = useState<Pending | null>(null), [storageError, setStorageError] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false), [loaded, setLoaded] = useState(false), [rejectedAbsent, setRejectedAbsent] = useState(false);
  const [usd, setUsd] = useState(''), [evidence, setEvidence] = useState(''), [note, setNote] = useState(''), [confirm, setConfirm] = useState(false);
  function save(p: Pending) { sessionStorage.setItem(key, JSON.stringify(p)); pendingRef.current = p; setPending(p); }
  async function readCurrent(token: number) {
    const [detail, current, rows] = await Promise.all([api<Run>(`/${kind === 'model' ? 'runs' : 'executions'}/${run_id}`), api<FeeReceipt | null>(base), api<FeeReceipt[]>(`${base}/history`)]);
    if (detail.id !== run_id || detail.agent_id !== agent_id || !integer(detail.attempt, 1) || !integer(detail.requirement_version, 1)) throw Error('调用实例关联不一致。');
    if (current) check(current, target);
    if (!Array.isArray(rows) || rows.length > 100) throw Error('费用历史格式不一致。');
    rows.forEach(row => check(row, target));
    if (token !== serial.current) return;
    setRun(detail); setLatest(current); setHistory(rows); setLoaded(true); setConfirm(false);
  }
  async function readRequest(sent: Pending, token: number) {
    const row = await api<FeeReceipt | null>(`${base}/requests/${encodeURIComponent(sent.payload.request_id)}`);
    if (token !== serial.current) return;
    if (!row) { setRejectedAbsent(sent.rejected === true); setNotice('尚未读到原请求回执；这不证明请求未受理。原请求继续保留。'); return; }
    check(row, target, sent);
    sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setVerified(row); setRejectedAbsent(false); setNotice('已独立核对原请求回执。该回执不代表当前最新修订。');
  }
  async function refresh() {
    if (writing.current) return;
    const token = ++serial.current; writing.current = true; setBusy(true); setLoaded(false); setError(''); setRejectedAbsent(false);
    try { if (readable.current && pendingRef.current) await readRequest(pendingRef.current, token); await readCurrent(token); }
    catch (e) { if (token === serial.current) setError(text(e)); }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  useEffect(() => {
    const node = dialog.current; node?.showModal(); writing.current = false;
    try { const raw = sessionStorage.getItem(key), saved = raw ? decode(raw, target) : null; pendingRef.current = saved; setPending(saved); readable.current = true; }
    catch (e) { readable.current = false; setStorageError(text(e)); }
    void refresh();
    return () => { serial.current++; node?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); };
  }, [key]);
  async function submit() {
    if (writing.current || pendingRef.current || !readable.current || !loaded || !run || !confirm) return;
    let sent: Pending;
    try { sent = { ...target, payload: { request_id: crypto.randomUUID(), attempt: run.attempt, requirement_version: run.requirement_version, expected_revision: latest?.revision ?? 0, amount_micro_usd: amount(usd), evidence_reference: evidence.trim(), note: note.trim(), confirm: true } }; if (!valid(sent.payload)) throw Error('请填写完整费用证据与说明。'); save(sent); }
    catch (e) { setError(text(e)); return; }
    const token = ++serial.current; writing.current = true; setBusy(true); setError(''); setNotice(''); setLoaded(false); setConfirm(false); let posted = false;
    try {
      const row = await api<FeeReceipt>(base, 'POST', sent.payload); posted = true;
      if (token !== serial.current) return;
      check(row, target, sent); sent = { ...sent, receipt: row }; save(sent);
      await readRequest(sent, token); await readCurrent(token);
    } catch (e) {
      if (token !== serial.current) return;
      if (!posted && e instanceof ApiError && [400, 409, 422].includes(e.status)) { try { save({ ...sent, rejected: true }); } catch { setStorageError('无法保存拒绝状态，原请求仍保留锁定。'); } }
      setError(text(e));
    } finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  function releaseRejected() {
    if (!pendingRef.current?.rejected || !rejectedAbsent || !loaded || busy || !readable.current) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setRejectedAbsent(false); setConfirm(false); setNotice('已结束明确拒绝的请求。请按当前修订重新核查并授权。'); }
    catch { setStorageError('无法清除原请求，继续保持锁定。'); }
  }
  const active = !run || ['queued', 'running', 'stopping'].includes(run.state);
  return <dialog ref={dialog} aria-label="费用声明与更正" onCancel={e => { e.preventDefault(); onClose(); }}>
    <header><h2>费用声明与更正</h2><button onClick={onClose}>关闭费用声明</button></header>
    <p>{kind === 'model' ? '模型调用' : 'CLI 执行'} · {run_id} · Agent {agent_id}</p>
    <p>这是 Owner 对该次实例完整 USD 费用的核查声明，不是服务方自动验真的账单。显式填写 0 表示已核查为零；未知不能填零。证据参考只保存文字，不会访问链接。</p>
    <p>减少预算占用可能启动已授权队列；增加占用可能阻止后续启动，不会停止已经运行的实例。更正会追加修订，旧声明保留。</p>
    {storageError && <p role="alert" className="error">{storageError}</p>}{error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <button disabled={busy} onClick={() => void refresh()}>{pending ? '只读核对原费用请求' : '刷新费用与历史'}</button>
    {pending && <section aria-label="待确认费用请求"><h3>原费用请求待确认</h3><p>请求 {pending.payload.request_id} · 期望修订 {pending.payload.expected_revision} · USD {feeMoney(pending.payload.amount_micro_usd)}</p><p>{pending.payload.evidence_reference}</p><p>{pending.payload.note}</p><p>原请求已冻结，不会自动重发或换键提交。关闭后重新打开仍保留。</p>{pending.rejected && rejectedAbsent && loaded && <button disabled={busy || !!storageError} onClick={releaseRejected}>结束已拒绝请求并重新核查</button>}</section>}
    {verified && <section aria-label="已核对的原请求回执"><h3>已核对的原请求回执</h3><Receipt row={verified} /></section>}
    <section aria-label="最新费用声明"><h3>最新费用声明</h3>{loaded ? latest ? <Receipt row={latest} /> : <p>当前尚无费用声明；这不表示费用为零。</p> : <p>最新费用尚未完成读取，不能据此提交更正。</p>}</section>
    {run && <p>实际状态 {run.state} · 第{run.attempt}次 · 需求 v{run.requirement_version}。{active ? '活动实例不能声明费用。' : run.state === 'unknown' ? '结果未知的实例必须先通过原有停止与外部影响核查；提交时服务端仍会检查是否持有活动实例。' : '服务端会再次检查活动持有与修订冲突。'}</p>}
    <form onSubmit={e => { e.preventDefault(); void submit(); }}><fieldset className="task-fields" disabled={busy || !loaded || !!pending || !!storageError || active}>
      <legend>{latest ? `追加更正（基于修订 ${latest.revision}）` : '首次完整费用声明'}</legend>
      <label>本次实例完整费用（USD）<input aria-label="本次实例完整费用（USD）" inputMode="decimal" required value={usd} onChange={e => { setUsd(e.target.value); setConfirm(false); }} /></label>
      <label>费用证据参考<input aria-label="费用证据参考" required maxLength={500} value={evidence} onChange={e => { setEvidence(e.target.value); setConfirm(false); }} /></label>
      <label>费用核查说明<textarea aria-label="费用核查说明" required maxLength={2000} value={note} onChange={e => { setNote(e.target.value); setConfirm(false); }} /></label>
      <label className="check"><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />我确认这是 Owner 核查声明，并理解减少占用可能启动已授权队列</label>
      <button disabled={!confirm}>确认提交费用声明</button>
    </fieldset></form>
    <details><summary>费用修订历史（最近100条）</summary>{loaded ? history.map(row => <Receipt key={row.revision} row={row} />) : <p>历史尚未完成读取。</p>}</details>
  </dialog>;
}
