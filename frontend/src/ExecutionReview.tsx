import { useEffect, useRef, useState } from 'react';
import { CodeReview } from './CodeReview';
import { api, ApiError, downloadArtifact, type Agent, type Conversation, type Task, type ExecutionArtifact, type OwnerReview, type ReviewRequest, type TaskExecution } from './api';

function decode(raw: string): ReviewRequest {
  const row = JSON.parse(raw);
  if (row && Object.hasOwn(row, 'rejected')) { if (typeof row.rejected !== 'boolean') throw Error(); delete row.rejected; }
  if (!row || Object.keys(row).sort().join() !== 'artifact_ids,decision,expected_version,note,request_id' || typeof row.request_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(row.request_id) || !Number.isSafeInteger(row.expected_version) || row.expected_version < 1 || !['approved', 'rejected'].includes(row.decision) || typeof row.note !== 'string' || !row.note.trim() || row.note.length > 2000 || !Array.isArray(row.artifact_ids) || row.artifact_ids.length > 100 || row.artifact_ids.some((id: unknown) => typeof id !== 'string' || !id || id.length > 120) || new Set(row.artifact_ids).size !== row.artifact_ids.length) throw Error();
  return row;
}

type CodeReviewTarget = { agents: Agent[]; onOpenTask: (task: Task, conversation: Conversation, focusReturnTo: HTMLElement) => void; allowCodeReview: boolean };
export function ExecutionReview({ run, ...target }: { run: TaskExecution } & CodeReviewTarget) {
  const [open, setOpen] = useState(false);
  return <details onToggle={event => setOpen(event.currentTarget.open)}><summary>成果与 Owner 评审</summary>{open && <ReviewPanel key={run.id} run={run} {...target} />}</details>;
}

function ReviewPanel({ run, agents, onOpenTask, allowCodeReview }: { run: TaskExecution } & CodeReviewTarget) {
  const [codeOpen, setCodeOpen] = useState(false);
  const key = `corppilot.review-pending.v1.${run.id}`;
  const alive = useRef(false), serial = useRef(0), writing = useRef(false);
  const pendingRef = useRef<ReviewRequest | null>(null);
  const [pending, setPending] = useState<ReviewRequest | null>(null), [rejected, setRejected] = useState(false);
  const [artifacts, setArtifacts] = useState<ExecutionArtifact[]>([]), [review, setReview] = useState<OwnerReview | null>(null);
  const [loaded, setLoaded] = useState(false), [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [current, setCurrent] = useState<boolean | null>(null), [allowed, setAllowed] = useState(false);
  const [error, setError] = useState(''), [storageError, setStorageError] = useState('');
  const [downloading, setDownloading] = useState<string | null>(null), [downloadError, setDownloadError] = useState('');
  async function download(item: ExecutionArtifact) {
    if (downloading) return;
    setDownloading(item.id); setDownloadError('');
    try { await downloadArtifact(item.id, item.path); }
    catch (error) { if (alive.current) setDownloadError(error instanceof Error ? error.message : '成果下载失败，请重试'); }
    finally { if (alive.current) setDownloading(null); }
  }
  const [note, setNote] = useState(''), [decision, setDecision] = useState<'approved' | 'rejected'>('approved'), [confirmed, setConfirmed] = useState(false);
  function restore() {
    try { const raw = sessionStorage.getItem(key); const saved = raw === null ? null : decode(raw); pendingRef.current = saved; setPending(saved); setRejected(raw !== null && JSON.parse(raw).rejected === true); setStorageError(''); }
    catch { setStorageError('无法安全读取待确认评审。请恢复浏览器会话存储后重新读取。'); }
  }
  function reconcile(value: OwnerReview | null) {
    const saved = pendingRef.current;
    if (!saved || !value) return;
    if (value.execution_id !== run.id || value.request_id !== saved.request_id || value.requirement_version !== saved.expected_version || value.decision !== saved.decision || value.note !== saved.note.trim() || JSON.stringify([...value.artifact_ids].sort()) !== JSON.stringify([...saved.artifact_ids].sort())) { setError('已有不同的不可覆盖评审，原请求仍保留，请核查。'); return; }
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setRejected(false); setStorageError(''); }
    catch { setStorageError('评审已保存，但未能清除待确认记录；请重新核对原请求。'); }
  }
  async function load() {
    if (writing.current) return;
    const request = ++serial.current; setLoading(true); setLoaded(false); setCurrent(null); setError('');
    try {
      const [artifactResult, reviewResult, taskResult, runResult] = await Promise.allSettled([api<ExecutionArtifact[]>(`/executions/${run.id}/artifacts`), api<OwnerReview | null>(`/executions/${run.id}/review`), api<Task>(`/tasks/${run.task_id}`), api<TaskExecution[]>(`/tasks/${run.task_id}/executions`)]);
      if (!alive.current || request !== serial.current) return;
      if (reviewResult.status === 'fulfilled') { setReview(reviewResult.value); reconcile(reviewResult.value); }
      if (artifactResult.status === 'fulfilled') setArtifacts(artifactResult.value);
      for (const result of [artifactResult, reviewResult, taskResult, runResult]) if (result.status === 'rejected') throw result.reason;
      if (artifactResult.status !== 'fulfilled' || reviewResult.status !== 'fulfilled' || taskResult.status !== 'fulfilled' || runResult.status !== 'fulfilled') return;
      const task = taskResult.value, runs = runResult.value;
      const [agent, conversation] = await Promise.all([api<Agent>(`/agents/${task.agent_id}`), api<Conversation>(`/conversations/${task.conversation_id}`)]);
      if (!alive.current || request !== serial.current) return;
      setCurrent(task.requirement_version === run.requirement_version && runs[0]?.id === run.id);
      setAllowed(!conversation.archived && agent.enabled && agent.tools.includes('execute') && conversation.member_ids.includes(agent.id));
      setLoaded(true); setConfirmed(false);
    } catch (error) { if (alive.current && request === serial.current) setError(error instanceof Error ? error.message : '评审读取失败'); }
    finally { if (alive.current && request === serial.current) setLoading(false); }
  }
  useEffect(() => { alive.current = true; restore(); void load(); return () => { alive.current = false; serial.current++; }; }, []);
  const blocked = !loaded || loading ? '请先成功读取成果与评审。' : !current ? '历史需求或执行只读，请评审当前版本的最新执行。' : !allowed ? '会话已归档或负责人缺少当前执行权限，不能新建评审。' : run.state !== 'awaiting_review' ? '此次执行尚不可评审。' : '';
  async function submit() {
    if (writing.current || storageError) return;
    const saved = pendingRef.current;
    if (!saved && (blocked || review || !note.trim() || decision === 'approved' && (!confirmed || !artifacts.length))) return;
    const sent = saved ?? { request_id: crypto.randomUUID(), expected_version: run.requirement_version, decision, note: note.trim(), artifact_ids: artifacts.map(row => row.id).sort() };
    try { sessionStorage.setItem(key, JSON.stringify(sent)); } catch { setStorageError('无法保存待确认评审，尚未发送。'); return; }
    pendingRef.current = sent; setPending(sent); setRejected(false); writing.current = true; serial.current++; setBusy(true); setError('');
    try { const value = await api<OwnerReview>(`/executions/${run.id}/review`, 'POST', sent); if (alive.current) { setReview(value); reconcile(value); } }
    catch (error) {
      if (alive.current) {
        if (!saved && error instanceof ApiError && error.status >= 400 && error.status < 500) {
          try { sessionStorage.setItem(key, JSON.stringify({ ...sent, rejected: true })); setRejected(true); } catch { setStorageError('无法记录拒绝结果，原请求仍保留。'); }
        }
        setError(`${error instanceof Error ? error.message : '评审请求失败'}。原请求已保留，不会自动替换版本或决定。`);
      }
    } finally { writing.current = false; if (alive.current) { setBusy(false); setLoading(false); } }
  }
  function discard() {
    if (!rejected || writing.current) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setRejected(false); setStorageError(''); setConfirmed(false); void load(); }
    catch { setStorageError('无法清除未接受请求，仍保持锁定。'); }
  }
  return <section className="execution-review">
    {allowCodeReview && loaded && artifacts.some(a => a.path === 'corppilot-code/change.patch') && artifacts.some(a => a.path === 'corppilot-code/manifest.json') && <button disabled={busy || loading} onClick={() => setCodeOpen(true)}>交指定集成人评审</button>}
    {codeOpen && <CodeReview key={run.id} sourceRun={run} agents={agents} onClose={() => setCodeOpen(false)} onOpenTask={onOpenTask} />}
    <button disabled={loading || busy} onClick={() => { restore(); void load(); }}>{loading ? '读取成果与评审中…' : '刷新成果与评审'}</button>
    {error && <p className="error" role="alert">{error}</p>}{storageError && <p className="error" role="alert">{storageError}</p>}
    {loaded && <><h4>已保存成果（{artifacts.length}）</h4>{!artifacts.length && <p className="muted">暂无已保存成果，不能批准交付。</p>}<ul>{artifacts.map(item => <li key={item.id}><button type="button" disabled={!!downloading} onClick={() => void download(item)}>{downloading === item.id ? '正在下载…' : item.path}</button><small>{item.size.toLocaleString()} 字节 · SHA-256：{item.sha256}</small></li>)}</ul>{downloadError && <p className="error" role="alert">{downloadError}</p>}</>}
      {review ? <section aria-label="Owner 评审决定"><p><strong>Owner 已{review.decision === 'approved' ? '批准' : '拒绝'}</strong> · 需求 v{review.requirement_version} · {new Date(review.reviewed_at).toLocaleString()}</p><p className="task-source">{review.note}</p><p className="muted">决定不可覆盖。{current === null ? '当前任务版本尚未核对，此决定仅绑定记录中的执行与需求版本。' : current ? '此决定仅对应本次执行及评审绑定的成果快照。' : '这是历史决定，不代表当前任务已通过验收。'}</p></section> : loaded && <p>{pending ? '尚未读取到已保存的 Owner 决定；请核对待确认请求。' : 'Owner 尚未评审；执行退出成功不等于验收通过。'}</p>}
    {pending ? <section><p role="status">评审请求待确认 · 需求 v{pending.expected_version} · {pending.decision === 'approved' ? '批准' : '拒绝'}</p><p className="task-source">{pending.note}</p><small>{pending.request_id}</small><p className="muted">沿用原请求 ID 和完整内容核对。若服务端此前未接收，此操作可能首次保存这项决定。</p><button disabled={busy || !!storageError} onClick={() => void submit()}>{busy ? '核对中…' : '核对原评审请求'}</button>{rejected && <><p>首次提交已被明确拒绝，可撤销后重新读取。曾经结果未知的请求不能撤销。</p><button disabled={busy || !!storageError} onClick={discard}>撤销未接受评审</button></>}</section>
      : !review && <form onSubmit={event => { event.preventDefault(); void submit(); }}><fieldset className="task-fields" disabled={busy || !!blocked || !!storageError}><label>评审决定<select value={decision} onChange={event => { setDecision(event.target.value as 'approved' | 'rejected'); setConfirmed(false); }}><option value="approved">批准成果</option><option value="rejected">拒绝成果</option></select></label><label>评审理由<textarea required maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></label>{decision === 'approved' && <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我已查验全部成果，确认符合本次需求与验收标准</label>}<button className="primary" disabled={!note.trim() || decision === 'approved' && (!confirmed || !artifacts.length)}>{busy ? '保存评审中…' : '确认提交评审'}</button></fieldset>{blocked && <p className="muted">{blocked}</p>}</form>}
  </section>;
}
