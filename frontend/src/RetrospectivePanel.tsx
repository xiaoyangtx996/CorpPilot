import { useEffect, useRef, useState } from 'react';
import { ModelRunReconciliation } from './ModelRunReconciliation';
import { api, ApiError, type ModelReconciliationRecord, type ExecutionArtifact, type OwnerReview, type RetrospectiveRequest, type RetrospectiveRun } from './api';

type Pending = { payload: RetrospectiveRequest; run_id?: string; rejected?: boolean };
const message = (error: unknown) => error instanceof Error ? error.message : '请求失败';
const active = (row: RetrospectiveRun) => row.state === 'queued' || row.state === 'running';
const labels = { queued: '排队中', running: '模型复盘中', completed: '已生成待审批候选', failed: '失败', cancelled: '已取消', unknown: '结果未知' };
function same(a: RetrospectiveRequest, b: RetrospectiveRequest) { return a.request_id === b.request_id && a.expected_version === b.expected_version && a.source_execution_id === b.source_execution_id && JSON.stringify(a.artifact_ids) === JSON.stringify(b.artifact_ids); }
function decode(raw: string): Pending {
  const row = JSON.parse(raw), p = row?.payload;
  const id = (value: unknown) => typeof value === 'string' && /^[0-9a-f-]{36}$/.test(value);
  if (!row || typeof row !== 'object' || Object.keys(row).some(k => !['payload', 'run_id', 'rejected'].includes(k)) || row.run_id !== undefined && !id(row.run_id) || row.rejected !== undefined && typeof row.rejected !== 'boolean' || !p || Object.keys(p).sort().join() !== 'artifact_ids,expected_version,request_id,source_execution_id' || !id(p.request_id) || !id(p.source_execution_id) || !Number.isSafeInteger(p.expected_version) || p.expected_version < 0 || !Array.isArray(p.artifact_ids) || p.artifact_ids.length < 1 || p.artifact_ids.length > 100 || p.artifact_ids.some((value: unknown) => !id(value)) || new Set(p.artifact_ids).size !== p.artifact_ids.length) throw Error('原复盘请求格式无效');
  return row;
}

export function RetrospectivePanel({ scope, identity, version, sources, disabled, onCandidate }: { scope: 'agent' | 'project'; identity: string; version: number; sources: { id: string; title: string }[]; disabled: boolean; onCandidate: () => void }) {
  const base = `/memories/${scope}/${identity}/retrospectives`, key = `corppilot.retrospective-pending.v1.${scope}.${identity}`;
  const alive = useRef(false), serial = useRef(0), writing = useRef(false), reading = useRef(false), pendingRef = useRef<Pending | null>(null), notified = useRef(new Set<string>()), callback = useRef(onCandidate);
  callback.current = onCandidate;
  const [checkId, setCheckId] = useState(''), [checkedRuns, setCheckedRuns] = useState<Record<string, ModelReconciliationRecord>>({});
  const [pending, setPending] = useState<Pending | null>(null), [current, setCurrent] = useState<RetrospectiveRun | null>(null), [history, setHistory] = useState<RetrospectiveRun[]>([]);
  const [source, setSource] = useState(''), [artifacts, setArtifacts] = useState<ExecutionArtifact[]>([]), [selected, setSelected] = useState<string[]>([]), [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [sourceLoading, setSourceLoading] = useState(false), [error, setError] = useState(''), [readError, setReadError] = useState(''), [storageError, setStorageError] = useState('');
  const sourceSerial = useRef(0);
  useEffect(() => { setConfirmed(false); }, [version, disabled]);
  function adopt(row: RetrospectiveRun, saved: Pending) {
    if (row.scope !== scope || row.scope_id !== identity || !same(row.request_payload, saved.payload) || saved.run_id && saved.run_id !== row.id) { setStorageError('复盘结果与原请求不一致，原请求继续锁定，请核查。'); return; }
    setCurrent(row); setError('');
    try { const next = { payload: saved.payload, run_id: row.id }; sessionStorage.setItem(key, JSON.stringify(next)); pendingRef.current = next; setPending(next); setStorageError(''); }
    catch { setStorageError('无法保存复盘编号，原请求继续保留，请恢复会话存储。'); }
  }
  async function load() {
    if (writing.current || reading.current) return;
    const token = ++serial.current; reading.current = true; setLoading(true);
    const saved = pendingRef.current;
    const results = await Promise.allSettled([api<RetrospectiveRun[]>(base), ...(saved?.run_id ? [api<RetrospectiveRun>(`/retrospectives/${saved.run_id}`)] : [])]);
    if (alive.current && token === serial.current) {
      const errors: string[] = [];
      if (results[0].status === 'fulfilled') {
        const rows = results[0].value as RetrospectiveRun[]; setHistory(rows);
        if (saved && !saved.run_id) { const row = rows.find(r => r.request_id === saved.payload.request_id); if (row) adopt(row, saved); }
        for (const row of rows) if (row.state === 'completed' && row.candidate_id && !notified.current.has(row.id)) { notified.current.add(row.id); callback.current(); }
      } else errors.push(message(results[0].reason));
      if (saved?.run_id) {
        const detail = results[1];
        if (detail.status === 'fulfilled') { const row = detail.value as RetrospectiveRun; adopt(row, saved); if (row.state === 'completed' && row.candidate_id && !notified.current.has(row.id)) { notified.current.add(row.id); callback.current(); } }
        else errors.push(message(detail.reason));
      }
      setReadError(errors.join('；')); setLoading(false);
    }
    reading.current = false;
    if (alive.current && token !== serial.current && !writing.current) void load();
  }
  useEffect(() => {
    alive.current = true;
    try { const raw = sessionStorage.getItem(key), saved = raw ? decode(raw) : null; pendingRef.current = saved; setPending(saved); if (saved) { setSource(saved.payload.source_execution_id); setSelected(saved.payload.artifact_ids); } }
    catch { setStorageError('无法安全读取原复盘请求，请恢复会话存储后重新打开；禁止新建请求。'); }
    void load();
    return () => { alive.current = false; serial.current++; sourceSerial.current++; };
  }, []);
  const polling = !!pending && (!current || active(current)) || history.some(active);
  useEffect(() => { if (!polling) return; const timer = window.setInterval(() => void load(), 1500); return () => clearInterval(timer); }, [polling]);
  async function chooseSource(id: string) {
    setSource(id); setSelected([]); setConfirmed(false); setArtifacts([]); setError('');
    const token = ++sourceSerial.current;
    if (!id) { setSourceLoading(false); return; }
    setSourceLoading(true);
    try {
      const [rows, review] = await Promise.all([api<ExecutionArtifact[]>(`/executions/${id}/artifacts`), api<OwnerReview | null>(`/executions/${id}/review`)]);
      if (!alive.current || token !== sourceSerial.current) return;
      if (review?.decision !== 'approved') throw Error('来源执行尚未获 Owner 批准');
      setArtifacts(rows.filter(row => review.artifact_ids.includes(row.id)));
    } catch (error) { if (alive.current && token === sourceSerial.current) setError(message(error)); }
    finally { if (alive.current && token === sourceSerial.current) setSourceLoading(false); }
  }
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (saved?.run_id) { void load(); return; }
    if (!saved && (disabled || loading || readError || sourceLoading || !confirmed || !selected.length || !sources.some(row => row.id === source))) return;
    const sent: Pending = { payload: saved?.payload ?? { request_id: crypto.randomUUID(), expected_version: version, source_execution_id: source, artifact_ids: selected } };
    try { sessionStorage.setItem(key, JSON.stringify(sent)); } catch { setStorageError('无法保存原复盘请求，尚未发送。'); return; }
    pendingRef.current = sent; setPending(sent); writing.current = true; serial.current++; setBusy(true); setError(''); setConfirmed(false);
    try { const row = await api<RetrospectiveRun>(base, 'POST', sent.payload); if (alive.current) adopt(row, sent); }
    catch (error) {
      if (alive.current) {
        if (!saved && error instanceof ApiError && error.status >= 400 && error.status < 500) {
          try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(key, JSON.stringify(rejected)); pendingRef.current = rejected; setPending(rejected); } catch { setStorageError('无法保存首次拒绝结果，仍需核对原请求。'); }
        }
        setError(`${message(error)}。原请求保留；不会自动再次调用模型。`);
      }
    } finally { writing.current = false; if (alive.current) { setBusy(false); void load(); } }
  }
  async function release() {
    if (writing.current || storageError || !pending || !(pending.rejected || current && (['completed', 'failed', 'cancelled'].includes(current.state) || current.state === 'unknown' && !!checkedRuns[current.id]))) return;
    if (current?.state === 'unknown') {
      writing.current = true; setBusy(true);
      try { const receipt = await api<ModelReconciliationRecord | null>(`/runs/${current.id}/reconciliation`); if (!receipt || receipt.run_id !== current.id || !receipt.local_request_stopped || !receipt.provider_effects_checked) throw Error('尚未读回原调用核查声明'); }
      catch (error) { setCheckedRuns(rows => { const next = { ...rows }; delete next[current.id]; return next; }); setError('核查声明读取失败，原请求继续保留。'); return; }
      finally { writing.current = false; if (alive.current) setBusy(false); }
      if (!alive.current) return;
    }
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setCurrent(null); setConfirmed(false); setSelected([]); setSource(''); setArtifacts([]); setSourceLoading(false); sourceSerial.current++; setError(''); serial.current++; void load(); }
    catch { setStorageError('无法清除查看记录，原请求继续锁定。'); }
  }
  async function cancel(row: RetrospectiveRun) {
    if (writing.current) return;
    writing.current = true; serial.current++; setBusy(true); setError('');
    try { await api(`/runs/${row.id}/cancel`, 'POST', {}); }
    catch (error) { if (alive.current) setError(`${message(error)}。取消结果待核对，不会自动重试。`); }
    finally { writing.current = false; if (alive.current) { setBusy(false); void load(); } }
  }
  function record(row: RetrospectiveRun) { return <article className="task-card"><h4>{labels[row.state]}</h4><small>复盘 {row.id} · 来源执行 {row.request_payload.source_execution_id} · 记忆 v{row.request_payload.expected_version}</small><p>模型：{row.model ?? '未知'} · 输入/输出 token：{row.usage?.prompt_tokens ?? '未知'} / {row.usage?.completion_tokens ?? '未知'}</p>{row.error && <p className="error">{row.error}</p>}{row.state === 'queued' && <button disabled={busy} onClick={() => void cancel(row)}>取消复盘排队</button>}{row.state === 'running' && <p>模型调用已开始，不能保证停止；不会自动重试。</p>}{row.state === 'unknown' && <section><p className="error">原调用结果未知，不自动重发；须核查本地请求、供应商结果及费用。</p><button disabled={busy} onClick={() => setCheckId(row.id)}>核查此模型调用</button>{checkedRuns[row.id] && <p>已读回人工声明，原状态仍为未知；结束原请求查看后才能重新选取并确认调用。</p>}</section>}<p>所选成果：{row.selected_artifacts.map(a => a.path).join('、') || '暂无'}</p>{row.evidence_artifact_ids.length > 0 && <p>模型引用证据：{row.evidence_artifact_ids.map(id => row.selected_artifacts.find(a => a.id === id)?.path ?? id).join('、')}</p>}{row.candidate_id && <p>候选 {row.candidate_id} 已生成。请在下方经验候选中审阅全文并单独决定；尚未修改批准记忆。</p>}</article>; }
  return <section><h3>从批准成果生成模型复盘</h3><p>选择 1–100 个 Owner 已批准的文本成果；服务端严格校验 UTF-8 正文，拒绝二进制和控制符，含全文快照的输入总量不得超过 64KiB。生成只创建待审批候选，不会自动批准或替换记忆。</p><button disabled={busy || loading} onClick={() => void load()}>刷新复盘状态</button>
    {error && <p className="error" role="alert">{error}</p>}{readError && <p className="error" role="alert">{readError}。读取失败不代表没有复盘，原记录保留。</p>}{storageError && <p className="error" role="alert">{storageError}</p>}
    {pending ? <section><p>已保存原请求 {pending.payload.request_id} · 记忆 v{pending.payload.expected_version}</p>{current && record(current)}<button disabled={busy || loading || !!storageError} onClick={() => void submit()}>{pending.run_id ? '读取原复盘（不调用模型）' : '核对原复盘请求'}</button>{!pending.run_id && <p>如果服务端尚未收到，手动核对可能首次发起这次已确认的模型调用。</p>}{(pending.rejected || current && (['completed', 'failed', 'cancelled'].includes(current.state) || current.state === 'unknown' && !!checkedRuns[current.id])) && <button disabled={busy || !!storageError} onClick={release}>{pending.rejected ? '修改首次未接受的复盘' : '结束查看并准备新复盘'}</button>}</section> : <fieldset className="task-fields" disabled={disabled || busy || loading || !!readError || !!storageError}>
      <label>已批准来源<select value={source} onChange={event => void chooseSource(event.target.value)}><option value="">选择来源执行</option>{sources.map(row => <option key={row.id} value={row.id}>{row.title} · {row.id}</option>)}</select></label>{sourceLoading && <p>正在读取批准成果…</p>}
      <fieldset className="member-choices"><legend>明确选择批准文本成果（{selected.length}/100）</legend>{artifacts.map(row => <label className="check" key={row.id}><input type="checkbox" checked={selected.includes(row.id)} disabled={sourceLoading || !selected.includes(row.id) && selected.length >= 100} onChange={event => { setSelected(event.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id)); setConfirmed(false); }} />{row.path} · {row.size} 字节</label>)}</fieldset>
      <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认调用一次模型，读取所选成果及目标记忆快照以生成候选，可能产生费用</label><button disabled={sourceLoading || !selected.length || !confirmed || !sources.some(row => row.id === source)} onClick={() => void submit()}>确认调用模型复盘</button>
    </fieldset>}
    <details><summary>复盘历史</summary>{history.filter(row => row.id !== current?.id).map(row => <div key={row.id}>{record(row)}</div>)}</details>
    {checkId && <ModelRunReconciliation runId={checkId} onClose={() => setCheckId('')} onVerified={row => setCheckedRuns(current => ({ ...current, [row.run_id]: row }))} />}
  </section>;
}
