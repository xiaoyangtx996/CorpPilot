import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type OwnerReview, type Task, type TaskExecution, type ExecutionArtifact } from './api';
import { checkRepository, type RepositoryBinding } from './RepositorySettings';

type Change = { path: string; status: 'A' | 'M' | 'D'; old_mode: string | null; new_mode: string | null; old_size: number | null; new_size: number | null; old_sha256: string | null; new_sha256: string | null };
type Manifest = { version: 1; execution_id: string; conversation_id: string; repository_revision: number; integration_agent_id: string; base_commit: string; base_tree: string; observed_head: string; target_tree: string; selection: string; content: string; changes: Change[]; patch_sha256: string; patch_bytes: number };
type Snapshot = { source_execution: Pick<TaskExecution, 'id' | 'task_id' | 'agent_id' | 'attempt' | 'requirement_version' | 'state'>; source_task: Pick<Task, 'id' | 'conversation_id' | 'source_message_id' | 'requirement_version'>; repository: RepositoryBinding | null; artifacts: ExecutionArtifact[]; manifest: Manifest | null; owner_review: OwnerReview | null; source_inputs: { dependency_task_id: string; upstream_execution_id: string }[]; integrator: { id: string; enabled: boolean; execute: boolean; member: boolean } | null; project: { id: string; archived: boolean } };
type Preview = { fingerprint: string; snapshot: Snapshot; blockers: string[] };
type Request = { request_id: string; fingerprint: string; confirm: true };
type Receipt = { id: string; source_execution_id: string; request_id: string; request_payload: Request; task_id: string; requirement_version: 1; initial_execution_request_id: string; initial_execution_id: string; source_snapshot: Snapshot; created_at: string };
type Pending = { source_execution_id: string; preview: Preview; payload: Request; receipt?: Receipt; rejected?: boolean };
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const text = (v: unknown, max = 2000): v is string => typeof v === 'string' && v.length <= max;
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => Number.isSafeInteger(v) && (v as number) >= min && (v as number) <= max;
const hash = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const oid = (v: unknown): v is string => typeof v === 'string' && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(v);
const states = ['queued', 'running', 'stopping', 'awaiting_review', 'failed', 'cancelled', 'unknown', 'superseded'];
const stateLabels: Record<string, string> = { queued: '排队中', running: '运行中', stopping: '正在停止', awaiting_review: '已退出，等待 Owner 评审', failed: '失败', cancelled: '已取消', unknown: '结果未知，须核查', superseded: '需求已过期' };
const errorText = (e: unknown) => e instanceof Error ? e.message : '代码评审读取失败';
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error('代码评审回执或预览的格式、来源与固定关联不一致。'); }
function keys(v: unknown, names: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === names.split(' ').sort().join()); }
function canonical(value: unknown): string { return JSON.stringify(value, (_key, v) => object(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v); }
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
async function digest(snapshot: Snapshot, blockers: string[]) { const bytes = new TextEncoder().encode(canonical({ snapshot, blockers })); return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(v => v.toString(16).padStart(2, '0')).join(''); }
function request(v: unknown): asserts v is Request { keys(v, 'request_id fingerprint confirm'); requireValue(text(v.request_id, 120) && !!v.request_id.trim() && v.request_id === v.request_id.trim() && hash(v.fingerprint) && v.confirm === true); }
function path(v: unknown) { return text(v, 1000) && !!v && v.split('/').every(p => p && p !== '.' && p !== '..' && !/[\\:\u0000-\u001f\u007f]/.test(p)); }
function snapshot(raw: unknown, source: TaskExecution, ready: boolean): Snapshot {
  keys(raw, 'source_execution source_task repository artifacts manifest owner_review source_inputs integrator project');
  const run = raw.source_execution, task = raw.source_task, project = raw.project;
  keys(run, 'id task_id agent_id attempt requirement_version state'); keys(task, 'id conversation_id source_message_id requirement_version'); keys(project, 'id archived');
  requireValue(run.id === source.id && run.task_id === source.task_id && run.agent_id === source.agent_id && run.attempt === source.attempt && run.requirement_version === source.requirement_version && integer(run.attempt, 1) && integer(run.requirement_version, 1) && states.includes(run.state as string));
  requireValue(task.id === run.task_id && id(task.conversation_id) && id(task.source_message_id) && integer(task.requirement_version, 1) && project.id === task.conversation_id && typeof project.archived === 'boolean');
  const repository = raw.repository === null ? null : checkRepository(raw.repository, task.conversation_id);
  requireValue(Array.isArray(raw.artifacts) && raw.artifacts.length <= 101);
  const ids = new Set<string>(); const paths = new Set<string>();
  for (const entry of raw.artifacts) { keys(entry, 'id execution_id path size sha256'); requireValue(id(entry.id) && entry.execution_id === source.id && path(entry.path) && integer(entry.size, 0, 4 * 1024 * 1024) && hash(entry.sha256) && !ids.has(entry.id) && !paths.has((entry.path as string).toLowerCase())); ids.add(entry.id); paths.add((entry.path as string).toLowerCase()); }
  if (raw.owner_review !== null) {
    const review = raw.owner_review; keys(review, 'execution_id request_id requirement_version decision note artifact_ids reviewed_at');
    requireValue(review.execution_id === source.id && text(review.request_id, 120) && !!review.request_id && review.requirement_version === run.requirement_version && ['approved', 'rejected'].includes(review.decision as string) && text(review.note) && text(review.reviewed_at, 100) && Number.isFinite(Date.parse(review.reviewed_at)) && Array.isArray(review.artifact_ids) && review.artifact_ids.length <= 100 && review.artifact_ids.every(id) && new Set(review.artifact_ids).size === review.artifact_ids.length);
  }
  requireValue(Array.isArray(raw.source_inputs) && raw.source_inputs.length <= 1000);
  const deps = new Set<string>(); for (const input of raw.source_inputs) { keys(input, 'dependency_task_id upstream_execution_id'); requireValue(id(input.dependency_task_id) && id(input.upstream_execution_id) && !deps.has(input.dependency_task_id)); deps.add(input.dependency_task_id); }
  if (raw.integrator !== null) { const actor = raw.integrator; keys(actor, 'id enabled execute member'); requireValue(id(actor.id) && ['enabled', 'execute', 'member'].every(k => typeof actor[k] === 'boolean') && repository?.snapshot?.integration_agent_id === actor.id); }
  if (raw.manifest !== null) {
    const m = raw.manifest; keys(m, 'version execution_id conversation_id repository_revision integration_agent_id base_commit base_tree observed_head target_tree selection content changes patch_sha256 patch_bytes');
    requireValue(m.version === 1 && m.execution_id === source.id && m.conversation_id === task.conversation_id && integer(m.repository_revision, 1) && m.repository_revision === repository?.revision && m.integration_agent_id === repository?.snapshot?.integration_agent_id && m.base_commit === repository?.snapshot?.commit && m.base_tree === repository?.snapshot?.tree && oid(m.observed_head) && oid(m.target_tree) && m.observed_head.length === (m.base_commit as string).length && m.target_tree.length === (m.base_commit as string).length && m.selection === 'base_head_index_tracked_and_nonignored_new' && m.content === 'raw_worktree_bytes' && hash(m.patch_sha256) && integer(m.patch_bytes, 0, 4 * 1024 * 1024) && Array.isArray(m.changes) && m.changes.length <= 10000);
    const names = new Set<string>();
    for (const change of m.changes) { keys(change, 'path status old_mode new_mode old_size new_size old_sha256 new_sha256'); requireValue(path(change.path) && ['A', 'M', 'D'].includes(change.status as string) && !names.has((change.path as string).toLowerCase())); names.add((change.path as string).toLowerCase()); for (const side of ['old', 'new']) { const absent = change.status === 'A' && side === 'old' || change.status === 'D' && side === 'new'; requireValue(absent ? ['mode', 'size', 'sha256'].every(k => change[`${side}_${k}`] === null) : ['100644', '100755'].includes(change[`${side}_mode`] as string) && integer(change[`${side}_size`], 0, 32 * 1024 * 1024) && hash(change[`${side}_sha256`])); } }
    const patch = (raw.artifacts as ExecutionArtifact[]).find(a => a.path === 'corppilot-code/change.patch');
    requireValue(patch && patch.size === m.patch_bytes && patch.sha256 === m.patch_sha256 && paths.has('corppilot-code/manifest.json'));
  }
  const result = raw as Snapshot;
  if (ready) requireValue(run.state === 'awaiting_review' && task.requirement_version === run.requirement_version && !project.archived && result.repository?.snapshot && result.manifest && result.integrator?.enabled && result.integrator.execute && result.integrator.member && result.owner_review?.decision === 'approved' && same([...result.owner_review.artifact_ids].sort(), [...ids].sort()) && result.artifacts.length <= 100 && result.artifacts.reduce((sum, a) => sum + a.size, 0) <= 16 * 1024 * 1024);
  return result;
}
async function checkPreview(raw: unknown, source: TaskExecution): Promise<Preview> { keys(raw, 'fingerprint snapshot blockers'); requireValue(hash(raw.fingerprint) && Array.isArray(raw.blockers) && raw.blockers.length <= 100 && raw.blockers.every(v => text(v) && !!v)); const value = snapshot(raw.snapshot, source, raw.blockers.length === 0); requireValue(await digest(value, raw.blockers as string[]) === raw.fingerprint); return raw as Preview; }
async function checkReceipt(raw: unknown, source: TaskExecution, sent?: Pending): Promise<Receipt> {
  keys(raw, 'id source_execution_id request_id request_payload task_id requirement_version initial_execution_request_id initial_execution_id source_snapshot created_at'); request(raw.request_payload);
  requireValue(id(raw.id) && raw.source_execution_id === source.id && raw.request_id === raw.request_payload.request_id && id(raw.task_id) && raw.task_id !== source.task_id && raw.requirement_version === 1 && id(raw.initial_execution_request_id) && id(raw.initial_execution_id) && raw.initial_execution_id !== source.id && text(raw.created_at, 100) && Number.isFinite(Date.parse(raw.created_at)));
  const fixed = snapshot(raw.source_snapshot, source, true); requireValue(await digest(fixed, []) === raw.request_payload.fingerprint);
  if (sent) requireValue(raw.request_id === sent.payload.request_id && same(raw.request_payload, sent.payload) && same(fixed, sent.preview.snapshot) && (!sent.receipt || same(raw, sent.receipt)));
  return raw as Receipt;
}
function checkInitial(run: TaskExecution, task: Task, row: Receipt) {
  const fixed = row.source_snapshot;
  requireValue(object(run) && run.id === row.initial_execution_id && run.task_id === row.task_id && run.request_id === row.initial_execution_request_id && run.agent_id === fixed.integrator?.id && run.attempt === 1 && run.requirement_version === row.requirement_version && run.previous_execution_id === null && run.reconciliation_note === '' && states.includes(run.state));
  requireValue(object(task) && task.id === row.task_id && task.conversation_id === fixed.source_task.conversation_id && task.source_message_id === fixed.source_task.source_message_id && integer(task.requirement_version, 1) && id(task.agent_id) && ['title', 'scope', 'acceptance'].every(k => text(task[k as keyof Task], 16000)));
  if (task.requirement_version === row.requirement_version) requireValue(task.agent_id === fixed.integrator?.id);
}

export function CodeReview({ sourceRun, agents, onClose, onOpenTask }: { sourceRun: TaskExecution; agents: Agent[]; onClose: () => void; onOpenTask: (task: Task, conversation: Conversation, focusReturnTo: HTMLElement) => void }) {
  const key = `corppilot.code-review-pending.v1.${sourceRun.id}`, base = `/executions/${sourceRun.id}/code-review`;
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement), serial = useRef(0), writing = useRef(false), readable = useRef(false), pendingRef = useRef<Pending | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null), [pending, setPending] = useState<Pending | null>(null), [receipt, setReceipt] = useState<Receipt | null>(null), [observed, setObserved] = useState<{ run: TaskExecution; task: Task } | null>(null);
  const [conflict, setConflict] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false), [confirmed, setConfirmed] = useState(false), [absent, setAbsent] = useState(false), [error, setError] = useState(''), [storageError, setStorageError] = useState(''), [notice, setNotice] = useState('');
  function save(value: Pending) { sessionStorage.setItem(key, JSON.stringify(value)); pendingRef.current = value; setPending(value); }
  async function initial(row: Receipt) { const [run, task] = await Promise.all([api<TaskExecution>(`/executions/${row.initial_execution_id}`), api<Task>(`/tasks/${row.task_id}`)]); checkInitial(run, task, row); return { run, task }; }
  async function readReceipt(token: number) {
    const raw = await api<unknown>(base); if (token !== serial.current) return;
    const sent = pendingRef.current;
    if (raw === null) { setAbsent(!!sent?.rejected); if (sent) setNotice('尚未读到来源的代码评审回执，原请求继续保留；不据此认定从未受理。'); return; }
    const other = !!sent && object(raw) && raw.request_id !== sent.payload.request_id;
    if (other) requireValue(!sent!.receipt);
    const row = await checkReceipt(raw, sourceRun, other ? undefined : sent ?? undefined); if (token !== serial.current) return;
    if (other) requireValue(same(row.source_snapshot, sent!.preview.snapshot));
    const { run, task } = await initial(row); if (token !== serial.current) return;
    if (other) { setConflict(row); setNotice('其他请求已创建评审任务。本地原请求继续保留，尚未确认成功；可明确接受已有回执。'); return; }
    if (sent) { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); }
    setReceipt(row); setObserved({ run, task }); setAbsent(false); setNotice('已独立核对原授权与初始执行关联；当前状态不会替换原回执。');
  }
  async function load() {
    if (writing.current) return;
    const token = ++serial.current; writing.current = true; setBusy(true); setConfirmed(false); setPreview(null); setReceipt(null); setObserved(null); setConflict(null); setAbsent(false); setError('');
    try { await readReceipt(token); if (token !== serial.current) return; const raw = await api<unknown>(`${base}-preview`); const value = await checkPreview(raw, sourceRun); if (token === serial.current) setPreview(value); }
    catch (e) { if (token === serial.current) setError(errorText(e)); }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  useEffect(() => {
    const node = dialog.current; node?.showModal(); writing.current = true; const token = ++serial.current; setBusy(true);
    void (async () => {
      try {
        const raw = sessionStorage.getItem(key); let saved: Pending | null = null;
        if (raw !== null) { const value = JSON.parse(raw); requireValue(object(value) && Object.keys(value).every(k => ['source_execution_id', 'preview', 'payload', 'receipt', 'rejected'].includes(k)) && value.source_execution_id === sourceRun.id && (value.rejected === undefined || typeof value.rejected === 'boolean') && !(value.rejected && value.receipt)); request(value.payload); const prior = await checkPreview(value.preview, sourceRun); requireValue(!prior.blockers.length && value.payload.fingerprint === prior.fingerprint); saved = value as Pending; if (Object.hasOwn(value, 'receipt')) await checkReceipt(value.receipt, sourceRun, saved); }
        if (token !== serial.current) return;
        pendingRef.current = saved; setPending(saved); readable.current = true;
      } catch { if (token !== serial.current) return; readable.current = false; setStorageError('原代码评审请求存储损坏或不可读取，已保留并锁定提交。'); }
      if (token === serial.current) { writing.current = false; void load(); }
    })();
    return () => { serial.current++; node?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); };
  }, [sourceRun.id]);
  async function submit(retry = false) {
    if (writing.current || !readable.current || storageError || (!retry && (pendingRef.current || receipt || !preview || preview.blockers.length || !confirmed))) return;
    let sent = pendingRef.current;
    if (retry && !sent) return;
    try { sent = sent ?? { source_execution_id: sourceRun.id, preview: preview!, payload: { request_id: crypto.randomUUID(), fingerprint: preview!.fingerprint, confirm: true } }; sent = { ...sent, rejected: false }; save(sent); }
    catch (e) { setStorageError(errorText(e)); return; }
    const token = ++serial.current; writing.current = true; setBusy(true); setError(''); setNotice(''); setConfirmed(false); setAbsent(false); setReceipt(null); setObserved(null); setConflict(null); let posted = false;
    try { const raw = await api<unknown>(base, 'POST', sent.payload); posted = true; if (token !== serial.current) return; const row = await checkReceipt(raw, sourceRun, sent); if (token !== serial.current) return; await initial(row); if (token !== serial.current) return; save({ ...sent, receipt: row }); await readReceipt(token); }
    catch (e) { if (token !== serial.current) return; if (!retry && !posted && !sent.receipt && e instanceof ApiError && [400, 403, 409, 422].includes(e.status)) { try { save({ ...sent, rejected: true }); } catch { setStorageError('无法保存拒绝状态，原请求仍保留。'); } } setError(errorText(e)); }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  function release() { if (!pendingRef.current?.rejected || !absent || !preview || busy || storageError) return; try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setAbsent(false); setConfirmed(false); setNotice('已结束明确拒绝的请求，请按当前预览重新核查并授权。'); } catch { setStorageError('无法清除原请求，继续锁定。'); } }
  async function acceptExisting() {
    const sent = pendingRef.current, expected = conflict;
    if (!sent || sent.receipt || !expected || writing.current || !readable.current || storageError) return;
    const token = ++serial.current; writing.current = true; setBusy(true); setError('');
    try {
      const raw = await api<unknown>(base); const row = await checkReceipt(raw, sourceRun); if (token !== serial.current) return;
      requireValue(row.request_id !== sent.payload.request_id && same(row, expected) && same(row.source_snapshot, sent.preview.snapshot));
      const actual = await initial(row); if (token !== serial.current) return;
      sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setConflict(null); setReceipt(row); setObserved(actual); setConfirmed(false); setAbsent(false);
      setNotice('已按你的选择接受已有评审回执并结束本地请求；这不表示本地原请求已成功。');
    } catch (e) { if (token === serial.current) { setConflict(null); setError(errorText(e)); } }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  async function openTask(focusReturnTo: HTMLElement) {
    if (!receipt || !observed || writing.current) return;
    const row = receipt, token = ++serial.current; writing.current = true; setBusy(true); setError('');
    try { const [task, run, room] = await Promise.all([api<Task>(`/tasks/${row.task_id}`), api<TaskExecution>(`/executions/${row.initial_execution_id}`), api<Conversation>(`/conversations/${row.source_snapshot.project.id}`)]); if (token !== serial.current) return; checkInitial(run, task, row); requireValue(room && room.id === row.source_snapshot.project.id && room.type === 'project' && typeof room.archived === 'boolean' && Array.isArray(room.member_ids) && room.member_ids.every(id)); setObserved({ task, run }); onOpenTask(task, room, focusReturnTo); }
    catch (e) { if (token === serial.current) { setObserved(null); setError(errorText(e)); } }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  const fixed = pending?.preview.snapshot ?? receipt?.source_snapshot ?? preview?.snapshot;
  const actor = agents.find(a => a.id === fixed?.integrator?.id);
  return <dialog ref={dialog} aria-label="交指定集成人评审" onCancel={e => { e.preventDefault(); e.stopPropagation(); onClose(); }}><header><h2>交指定集成人评审</h2><button onClick={onClose}>关闭代码评审</button></header>
    <p>来源执行 <span style={{ overflowWrap: 'anywhere' }}>{sourceRun.id}</span> · 第 {sourceRun.attempt} 次 · 需求 v{sourceRun.requirement_version}。这会创建真实评审任务，可能产生费用；评审建议不会自动批准或合入代码。</p>
    {error && <p role="alert" className="error">{error}</p>}{storageError && <p role="alert" className="error">{storageError}</p>}{notice && <p role="status">{notice}</p>}
    <button disabled={busy} onClick={() => void load()}>{pending ? '只读核对原代码评审请求' : '刷新代码评审'}</button>
    {fixed && <section aria-label="固定代码评审来源"><h3>指定集成人：{actor?.name ?? fixed.integrator?.id ?? '尚无绑定'}</h3><p style={{ overflowWrap: 'anywhere' }}>{fixed.integrator?.id} · 原仓库版本 {fixed.repository?.revision ?? '未知'} · 完整来源成果 {fixed.artifacts.length} 项</p><p>会传递 Owner 批准的全部来源成果；使用原仓库绑定，不跟随项目最新版本。</p><details><summary>查看代码与成果明细</summary><p style={{ overflowWrap: 'anywhere' }}>Base {fixed.manifest?.base_commit ?? '未知'} · Target {fixed.manifest?.target_tree ?? '未知'}</p><p>净变更 {fixed.manifest?.changes.length ?? '未知'} 项；含追踪文件及未忽略的新文件，被忽略且未追踪的新增文件不在补丁内。</p>{fixed.artifacts.map(a => <p key={a.id} style={{ overflowWrap: 'anywhere' }}>{a.path} · {a.size} 字节 · {a.id} · SHA-256 {a.sha256}</p>)}</details></section>}
    {preview && <section aria-label="当前代码评审预览"><h3>当前准入检查</h3>{preview.blockers.length ? <ul>{preview.blockers.map((v, i) => <li key={i}>{v}</li>)}</ul> : <p>当前预览允许授权，实际启动仍会核查来源和权限。</p>}<details><summary>预览指纹</summary><p style={{ overflowWrap: 'anywhere' }}>{preview.fingerprint}</p></details></section>}
    {pending && <section aria-label="待确认代码评审请求"><h3>原代码评审请求待确认</h3><p style={{ overflowWrap: 'anywhere' }}>{pending.payload.request_id}</p><p>完整预览与请求已冻结。只读核对不会重新执行；同键重试可能首次创建已授权任务。</p><button disabled={busy || !!storageError} onClick={() => void submit(true)}>同键重试代码评审请求</button>{pending.rejected && absent && preview && <button disabled={busy || !!storageError} onClick={release}>结束已拒绝请求并重新预览</button>}</section>}
    {conflict && <section aria-label="其他请求的代码评审回执"><h3>其他请求已创建评审任务</h3><p style={{ overflowWrap: 'anywhere' }}>已有请求 {conflict.request_id} · 任务 {conflict.task_id} · 初始执行 {conflict.initial_execution_id}</p><p>已有回执及实际初始关联已核对。本地请求没有被确认成功；接受操作只读取并采用已有任务，不会再次执行。</p><button disabled={busy || !!storageError} onClick={() => void acceptExisting()}>接受已有评审回执并结束本地请求</button></section>}
    {receipt && <section aria-label="原代码评审回执"><h3>原代码评审回执</h3><p style={{ overflowWrap: 'anywhere' }}>请求 {receipt.request_id} · 任务 {receipt.task_id}</p><p style={{ overflowWrap: 'anywhere' }}>初始执行 {receipt.initial_execution_id} · 需求 v{receipt.requirement_version}</p><p>后续停止或重试不改变此初始关联；不能为同一来源重复创建评审任务。</p>{observed && <section aria-label="初始评审执行当前状态"><p>{stateLabels[observed.run.state]} · 当前任务需求 v{observed.task.requirement_version}</p>{observed.task.requirement_version !== receipt.requirement_version && <p>评审任务已修改，专用授权不会扩大到新版本。</p>}</section>}<button disabled={busy || !observed} onClick={event => void openTask(event.currentTarget)}>打开评审任务</button></section>}
    {!receipt && <form onSubmit={e => { e.preventDefault(); void submit(); }}><fieldset disabled={busy || !!pending || !!storageError || !preview || !!preview.blockers.length}><label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我确认交给原指定集成人执行代码评审，可能产生费用；不自动批准或合入</label><button disabled={!confirmed}>确认交集成人评审</button></fieldset></form>}
  </dialog>;
}
