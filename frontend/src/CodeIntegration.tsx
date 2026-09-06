import { useEffect, useId, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type ExecutionArtifact, type OwnerReview, type Task, type TaskExecution } from './api';
import { canonical, checkReceipt as checkReviewReceipt, snapshot as checkSource, type Snapshot } from './CodeReview';
import { TaskExecutions } from './TaskExecutions';

type ReviewReceipt = Awaited<ReturnType<typeof checkReviewReceipt>>;
type Source = { source_snapshot: Snapshot; code_review_receipt: ReviewReceipt | null; review_execution: Pick<TaskExecution, 'id' | 'task_id' | 'agent_id' | 'attempt' | 'requirement_version' | 'state' | 'exit_code'> | null; review_owner_decision: OwnerReview | null; review_artifacts: ExecutionArtifact[] };
type IntegrationSnapshot = { project_id: string; sources: Source[] };
type Preview = { snapshot: IntegrationSnapshot; latest_integration_id: string | null; blockers: string[]; fingerprint: string };
type Request = { request_id: string; source_execution_ids: string[]; fingerprint: string; previous_integration_id: string | null; reconciliation_note: string; confirm: true };
type DeclarationRequest = { request_id: string; process_stopped: true; effects_checked: true; note: string };
type Declaration = { integration_id: string; request_payload: DeclarationRequest; source: 'owner_declared'; created_at: string };
type Result = { source_path: string; base_commit: string; base_tree: string; branch: string; commit: string; tree: string; files: number; total_bytes: number; conversation_id: string; integration_agent_id: string; repository_revision: number; source_execution_ids: string[]; patch_sha256s: string[] };
type Receipt = { id: string; project_id: string; request_id: string; request_payload: Request; authorization_snapshot: IntegrationSnapshot; fingerprint: string; destination_relative: string; branch: string; previous_integration_id: string | null; reconciliation_note: string; state: 'running' | 'stopping' | 'completed' | 'failed' | 'cancelled' | 'unknown'; result: Result | null; summary: string; created_at: string; updated_at: string; reconciliation: Declaration | null };
type Pending = { project_id: string; preview: Preview; payload: Request; receipt?: Receipt; rejected?: boolean };
type PendingDeclaration = { integration_id: string; payload: DeclarationRequest; rejected?: boolean };
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown, max = 2000): v is string => typeof v === 'string' && v.length <= max;
const id = (v: unknown): v is string => text(v, 120) && !!v && v === v.trim();
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v);
const integer = (v: unknown, max = Number.MAX_SAFE_INTEGER, min = 0) => Number.isSafeInteger(v) && (v as number) >= min && (v as number) <= max;
const hash = (v: unknown) => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
const failure = (e: unknown) => e instanceof Error ? e.message : '代码集成请求失败';
const states = { running: '运行中', stopping: '正在停止', completed: '已完成', failed: '失败', cancelled: '已取消', unknown: '结果未知' };
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error('集成预览或回执的格式、固定来源与授权关联不一致。'); }
function keys(v: unknown, fields: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === fields.split(' ').sort().join()); }
async function digest(v: unknown) { return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(v))))).map(b => b.toString(16).padStart(2, '0')).join(''); }
function ids(v: unknown): asserts v is string[] { requireValue(Array.isArray(v) && v.length >= 1 && v.length <= 16 && v.every(id) && new Set(v).size === v.length); }
function request(v: unknown): asserts v is Request {
  keys(v, 'request_id source_execution_ids fingerprint previous_integration_id reconciliation_note confirm'); ids(v.source_execution_ids);
  requireValue(id(v.request_id) && hash(v.fingerprint) && (v.previous_integration_id === null || uuid(v.previous_integration_id)) && text(v.reconciliation_note) && v.reconciliation_note === v.reconciliation_note.trim() && (v.previous_integration_id === null ? v.reconciliation_note === '' : !!v.reconciliation_note) && v.confirm === true);
}
function declarationRequest(v: unknown): asserts v is DeclarationRequest { keys(v, 'request_id process_stopped effects_checked note'); requireValue(id(v.request_id) && v.process_stopped === true && v.effects_checked === true && text(v.note) && !!v.note.trim() && v.note === v.note.trim()); }
function declaration(v: unknown, identity: string): asserts v is Declaration { keys(v, 'integration_id request_payload source created_at'); declarationRequest(v.request_payload); requireValue(v.integration_id === identity && v.source === 'owner_declared' && text(v.created_at, 100) && Number.isFinite(Date.parse(v.created_at))); }
async function snapshot(v: unknown, project: string, identities: string[], ready: boolean): Promise<IntegrationSnapshot> {
  keys(v, 'project_id sources'); requireValue(v.project_id === project && Array.isArray(v.sources) && v.sources.length === identities.length);
  let group = ''; let bytes = 0;
  for (const [index, item] of v.sources.entries()) {
    keys(item, 'source_snapshot code_review_receipt review_execution review_owner_decision review_artifacts');
    requireValue(object(item.source_snapshot) && object(item.source_snapshot.source_execution));
    const run = item.source_snapshot.source_execution;
    requireValue(run.id === identities[index] && uuid(run.id) && uuid(run.task_id) && id(run.agent_id));
    const source = checkSource(item.source_snapshot, run as unknown as TaskExecution, ready);
    requireValue(source.project.id === project);
    if (item.code_review_receipt !== null) { const receipt = await checkReviewReceipt(item.code_review_receipt, run as unknown as TaskExecution); if (ready) requireValue(same(receipt.source_snapshot, source)); }
    const review = item.review_execution;
    if (review !== null) {
      keys(review, 'id task_id agent_id attempt requirement_version state exit_code');
      requireValue(uuid(review.id) && uuid(review.task_id) && id(review.agent_id) && integer(review.attempt, Number.MAX_SAFE_INTEGER, 1) && integer(review.requirement_version, Number.MAX_SAFE_INTEGER, 1) && ['queued', 'running', 'stopping', 'awaiting_review', 'failed', 'cancelled', 'unknown', 'superseded'].includes(review.state as string) && (review.exit_code === null || Number.isSafeInteger(review.exit_code)));
      requireValue(item.code_review_receipt !== null && review.task_id === (item.code_review_receipt as ReviewReceipt).task_id);
      requireValue(review.attempt === 1 ? review.id === (item.code_review_receipt as ReviewReceipt).initial_execution_id : review.id !== (item.code_review_receipt as ReviewReceipt).initial_execution_id);
    }
    requireValue(Array.isArray(item.review_artifacts) && item.review_artifacts.length <= 101);
    const entries = item.review_artifacts as ExecutionArtifact[];
    for (const a of entries) { keys(a, 'id execution_id path size sha256'); requireValue(uuid(a.id) && a.execution_id === review?.id && text(a.path, 1000) && !!a.path && a.path.split('/').every(p => p && p !== '.' && p !== '..' && !/[\\:\u0000-\u001f\u007f]/.test(p)) && integer(a.size, 4 * 1024 * 1024) && hash(a.sha256)); }
    requireValue(new Set(entries.map(a => a.id)).size === entries.length && new Set(entries.map(a => a.path.toLowerCase())).size === entries.length);
    const decision = item.review_owner_decision;
    if (decision !== null) { keys(decision, 'execution_id request_id requirement_version decision note artifact_ids reviewed_at'); requireValue(decision.execution_id === review?.id && decision.requirement_version === review?.requirement_version && id(decision.request_id) && ['approved', 'rejected'].includes(decision.decision as string) && text(decision.note) && text(decision.reviewed_at, 100) && Number.isFinite(Date.parse(decision.reviewed_at)) && Array.isArray(decision.artifact_ids) && decision.artifact_ids.length <= 100 && decision.artifact_ids.every(uuid) && new Set(decision.artifact_ids).size === decision.artifact_ids.length); }
    if (ready) {
      requireValue(review && review.state === 'awaiting_review' && review.exit_code === 0 && review.requirement_version === 1 && review.agent_id === source.integrator?.id && decision?.decision === 'approved' && same([...(decision.artifact_ids as string[])].sort(), entries.map(a => a.id).sort()) && entries.length <= 100 && entries.reduce((sum, a) => sum + a.size, 0) <= 16 * 1024 * 1024 && entries.some(a => a.path === 'review.md' && a.size > 0));
      const m = source.manifest!; const current = canonical([m.conversation_id, m.base_commit, m.base_tree, m.integration_agent_id, m.repository_revision]);
      requireValue(!group || group === current); group = current;
      bytes += source.artifacts.filter(a => ['corppilot-code/change.patch', 'corppilot-code/manifest.json'].includes(a.path)).reduce((n, a) => n + a.size, 0);
    }
  }
  if (ready) requireValue(bytes <= 16 * 1024 * 1024);
  return v as IntegrationSnapshot;
}
async function checkPreview(v: unknown, project: string, identities: string[]): Promise<Preview> {
  keys(v, 'snapshot latest_integration_id blockers fingerprint'); requireValue(v.latest_integration_id === null || uuid(v.latest_integration_id));
  requireValue(Array.isArray(v.blockers) && v.blockers.every(b => text(b) && !!b) && hash(v.fingerprint));
  await snapshot(v.snapshot, project, identities, v.blockers.length === 0);
  requireValue(await digest({ snapshot: v.snapshot, latest_integration_id: v.latest_integration_id, blockers: v.blockers }) === v.fingerprint); return v as Preview;
}
function fixed(row: Receipt) { const { state: _state, result: _result, summary: _summary, updated_at: _updated, reconciliation: _reconciliation, ...authorization } = row; return authorization; }
function unchanged(row: Receipt, prior: Receipt) {
  requireValue(same(fixed(row), fixed(prior)));
  if (!['running', 'stopping'].includes(prior.state)) requireValue(same([row.state, row.result, row.summary, row.updated_at], [prior.state, prior.result, prior.summary, prior.updated_at]));
  if (prior.reconciliation) requireValue(same(row.reconciliation, prior.reconciliation));
}
async function checkReceipt(v: unknown, project: string, sent?: Pending): Promise<Receipt> {
  keys(v, 'id project_id request_id request_payload authorization_snapshot fingerprint destination_relative branch previous_integration_id reconciliation_note state result summary created_at updated_at reconciliation'); request(v.request_payload);
  requireValue(uuid(v.id) && v.project_id === project && v.request_id === v.request_payload.request_id && v.fingerprint === v.request_payload.fingerprint && v.previous_integration_id === v.request_payload.previous_integration_id && v.reconciliation_note === v.request_payload.reconciliation_note && v.destination_relative === `code-integrations/${v.id}/repository` && v.branch === `corppilot/integration-${v.id}` && Object.hasOwn(states, v.state as string) && text(v.summary) && [v.created_at, v.updated_at].every(t => text(t, 100) && Number.isFinite(Date.parse(t))));
  const source = await snapshot(v.authorization_snapshot, project, v.request_payload.source_execution_ids, true);
  requireValue(await digest({ snapshot: source, latest_integration_id: v.previous_integration_id, blockers: [] }) === v.fingerprint);
  if (v.result !== null) {
    keys(v.result, 'source_path base_commit base_tree branch commit tree files total_bytes conversation_id integration_agent_id repository_revision source_execution_ids patch_sha256s');
    const r = v.result, repo = source.sources[0].source_snapshot.repository!.snapshot!, manifest = source.sources[0].source_snapshot.manifest!;
    requireValue(r.source_path === repo.source_path && r.base_commit === repo.commit && r.base_tree === repo.tree && r.branch === v.branch && r.conversation_id === project && r.integration_agent_id === repo.integration_agent_id && r.repository_revision === manifest.repository_revision && same(r.source_execution_ids, v.request_payload.source_execution_ids) && same(r.patch_sha256s, source.sources.map(s => s.source_snapshot.manifest!.patch_sha256)) && typeof r.commit === 'string' && typeof r.tree === 'string' && [r.commit, r.tree].every(h => new RegExp(`^[0-9a-f]{${repo.commit.length}}$`).test(h)) && integer(r.files, 10000) && integer(r.total_bytes, 256 * 1024 * 1024));
  }
  requireValue(v.state !== 'completed' || v.result !== null);
  if (v.reconciliation !== null) { requireValue(v.state === 'unknown'); declaration(v.reconciliation, v.id); }
  const row = v as Receipt;
  if (sent) { requireValue(row.request_id === sent.payload.request_id && same(row.request_payload, sent.payload) && same(row.authorization_snapshot, sent.preview.snapshot)); if (sent.receipt) unchanged(row, sent.receipt); }
  return row;
}

export function CodeIntegration({ conversation, tasks, agents, onClose }: { conversation: Conversation; tasks: Task[]; agents: Agent[]; onClose: () => void }) {
  const project = conversation.id, base = `/conversations/${project}/code-integrations`, key = `corppilot.code-integration-pending.v1.${project}`, declarationKey = `corppilot.code-integration-reconciliation.v1.${project}`;
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement), title = useId(), serial = useRef(0), writing = useRef(false), alive = useRef(false), readable = useRef(false);
  const pendingRef = useRef<Pending | null>(null), declarationRef = useRef<PendingDeclaration | null>(null);
  const [pending, setPending] = useState<Pending | null>(null), [pendingDeclaration, setPendingDeclaration] = useState<PendingDeclaration | null>(null);
  const [selected, setSelected] = useState<string[]>([]), [candidates, setCandidates] = useState<Record<string, TaskExecution[]>>({});
  const [preview, setPreview] = useState<Preview | null>(null), [history, setHistory] = useState<Receipt[]>([]), [observed, setObserved] = useState<Receipt | null>(null);
  const [evidence, setEvidence] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false), [confirmed, setConfirmed] = useState(false), [absent, setAbsent] = useState(false), [note, setNote] = useState('');
  const [error, setError] = useState(''), [storageError, setStorageError] = useState(''), [notice, setNotice] = useState('');
  const [declarationId, setDeclarationId] = useState(''), [stopped, setStopped] = useState(false), [checked, setChecked] = useState(false), [declarationNote, setDeclarationNote] = useState('');
  const [declarationAbsent, setDeclarationAbsent] = useState(false), [declarationConflict, setDeclarationConflict] = useState<Declaration | null>(null);
  const [taskPanel, setTaskPanel] = useState<{ task: Task; focusReturnTo: HTMLElement; allowCodeReview: boolean } | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!busy && !taskPanel && returnFocus.current) { const target = returnFocus.current; returnFocus.current = null; if (target.isConnected) target.focus(); }
  }, [busy, taskPanel]);
  function save(value: Pending) { sessionStorage.setItem(key, JSON.stringify(value)); pendingRef.current = value; setPending(value); }
  function put(row: Receipt) { setObserved(row); setHistory(rows => rows.map(r => r.id === row.id ? row : r)); }
  function change(next: string[]) { if (writing.current || pendingRef.current) return; serial.current++; setSelected(next); setPreview(null); setEvidence(null); setConfirmed(false); setError(''); }
  async function readOriginal(token: number) {
    let restored: Receipt | null = null;
    const sent = pendingRef.current;
    if (sent) {
      const raw = await api<unknown>(`${base}/requests/${encodeURIComponent(sent.payload.request_id)}`); if (token !== serial.current) return;
      if (raw === null) { setAbsent(!!sent.rejected); setNotice('尚未读取到原请求；原内容继续保留，不表示从未受理。'); }
      else { const row = await checkReceipt(raw, project, sent); if (token !== serial.current) return; sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setAbsent(false); put(row); restored = row; setNotice('已独立核对原集成授权；没有重复集成。'); }
    }
    const check = declarationRef.current;
    if (check) {
      const row = await checkReceipt(await api<unknown>(`/code-integrations/${check.integration_id}`), project); if (token !== serial.current) return;
      requireValue(row.id === check.integration_id); put(row); restored = row;
      if (row.reconciliation) {
        if (!same(row.reconciliation.request_payload, check.payload)) {
          requireValue(row.reconciliation.request_payload.request_id !== check.payload.request_id);
          setDeclarationConflict(row.reconciliation); setDeclarationAbsent(false); setNotice('服务端已有另一条核查声明；本地原请求仍保留，未标记为成功。');
        } else { sessionStorage.removeItem(declarationKey); declarationRef.current = null; setPendingDeclaration(null); setDeclarationConflict(null); setDeclarationAbsent(false); setNotice('已核对 Owner 人工声明；原集成仍为结果未知，不会自动重跑。'); }
      } else { setDeclarationConflict(null); setDeclarationAbsent(!!check.rejected); }
    }
    return restored;
  }
  async function refresh(withPreview = false) {
    if (writing.current) return;
    const token = ++serial.current; writing.current = true; setBusy(true); setError(''); setConfirmed(false); setAbsent(false); setDeclarationAbsent(false); if (withPreview) setPreview(null);
    try {
      const restored = await readOriginal(token); if (token !== serial.current) return;
      const raw = await api<unknown>(base); requireValue(Array.isArray(raw) && raw.length <= 100);
      const rows = await Promise.all(raw.map(v => checkReceipt(v, project))); if (token !== serial.current) return; requireValue(new Set(rows.map(r => r.id)).size === rows.length); for (const row of rows) { const prior = history.find(r => r.id === row.id); if (prior) unchanged(row, prior); } setHistory(rows);
      const current = restored ?? observed;
      if (current && !pendingRef.current) { const row = await checkReceipt(await api<unknown>(`/code-integrations/${current.id}`), project); requireValue(row.id === current.id); unchanged(row, current); if (token !== serial.current) return; put(row); }
      const identities = pendingRef.current?.payload.source_execution_ids ?? selected;
      if (withPreview && identities.length) { const value = await checkPreview(await api<unknown>(`/conversations/${project}/code-integration-preview`, 'POST', { source_execution_ids: identities }), project, identities); if (token === serial.current) { setPreview(value); setEvidence(value); } }
    } catch (e) { if (token === serial.current) { setError(failure(e)); setPreview(null); setAbsent(false); setDeclarationAbsent(false); } }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  useEffect(() => {
    alive.current = true; dialog.current?.showModal(); const token = ++serial.current; writing.current = true; setBusy(true);
    void (async () => {
      try {
        const raw = sessionStorage.getItem(key), rawDeclaration = sessionStorage.getItem(declarationKey);
        let saved: Pending | null = null, savedDeclaration: PendingDeclaration | null = null;
        if (raw !== null) { const v = JSON.parse(raw); requireValue(object(v) && Object.keys(v).every(k => ['project_id', 'preview', 'payload', 'receipt', 'rejected'].includes(k)) && v.project_id === project && (v.rejected === undefined || typeof v.rejected === 'boolean') && !(v.rejected && v.receipt)); request(v.payload); const p = await checkPreview(v.preview, project, v.payload.source_execution_ids); requireValue(!p.blockers.length && p.fingerprint === v.payload.fingerprint && p.latest_integration_id === v.payload.previous_integration_id); saved = v as Pending; if (v.receipt !== undefined) await checkReceipt(v.receipt, project, saved); }
        if (rawDeclaration !== null) { const v = JSON.parse(rawDeclaration); requireValue(object(v) && Object.keys(v).every(k => ['integration_id', 'payload', 'rejected'].includes(k)) && uuid(v.integration_id) && (v.rejected === undefined || typeof v.rejected === 'boolean')); declarationRequest(v.payload); savedDeclaration = v as PendingDeclaration; }
        if (token !== serial.current) return;
        pendingRef.current = saved; setPending(saved); if (saved) { setSelected(saved.payload.source_execution_ids); setEvidence(saved.preview); } declarationRef.current = savedDeclaration; setPendingDeclaration(savedDeclaration); if (savedDeclaration) setDeclarationId(savedDeclaration.integration_id); readable.current = true;
      } catch { if (token === serial.current) setStorageError('原集成请求存储损坏或不可读取，已保留并锁定提交。'); }
      if (token === serial.current) { writing.current = false; void refresh(); }
    })();
    return () => { alive.current = false; serial.current++; dialog.current?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); };
  }, []);
  const active = history.some(r => r.state === 'running' || r.state === 'stopping') || observed?.state === 'running' || observed?.state === 'stopping';
  useEffect(() => { if (!active && !pending && !pendingDeclaration) return; const timer = window.setInterval(() => void refresh(), 2000); return () => window.clearInterval(timer); }, [active, pending, pendingDeclaration, observed]);
  async function loadCandidates(task: Task) {
    if (writing.current) return; const token = ++serial.current; writing.current = true; setBusy(true); setError('');
    try { const rows = await api<TaskExecution[]>(`/tasks/${task.id}/executions`); requireValue(Array.isArray(rows) && rows.every(r => uuid(r.id) && r.task_id === task.id && integer(r.attempt, Number.MAX_SAFE_INTEGER, 1) && integer(r.requirement_version, Number.MAX_SAFE_INTEGER, 1))); if (token === serial.current) { setCandidates(current => ({ ...current, [task.id]: rows })); setPreview(null); setConfirmed(false); } }
    catch (e) { if (token === serial.current) setError(failure(e)); }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  async function submit(retry = false) {
    if (writing.current || !readable.current || storageError || declarationRef.current) return;
    let sent = pendingRef.current;
    if (retry ? !sent : !!sent || !preview || preview.blockers.length > 0 || !confirmed || conversation.archived || !same(selected, preview.snapshot.sources.map(s => s.source_snapshot.source_execution.id)) || preview.latest_integration_id !== null && !note.trim()) return;
    try { sent = sent ?? { project_id: project, preview: preview!, payload: { request_id: crypto.randomUUID(), source_execution_ids: [...selected], fingerprint: preview!.fingerprint, previous_integration_id: preview!.latest_integration_id, reconciliation_note: preview!.latest_integration_id ? note.trim() : '', confirm: true } }; sent = { ...sent, rejected: false }; save(sent); }
    catch { setStorageError('无法保存原集成请求，尚未发送。'); return; }
    const token = ++serial.current; writing.current = true; setBusy(true); setError(''); setAbsent(false); setConfirmed(false); setPreview(null); let posted = false;
    try { const raw = await api<unknown>(base, 'POST', sent.payload); posted = true; const row = await checkReceipt(raw, project, sent); if (token !== serial.current) return; save({ ...sent, receipt: row }); await readOriginal(token); }
    catch (e) { if (token === serial.current) { if (!retry && !posted && !sent.receipt && e instanceof ApiError && [400, 403, 409, 422].includes(e.status)) { try { save({ ...sent, rejected: true }); } catch { setStorageError('拒绝状态未能保存，仍保留原请求。'); } } setError(`${failure(e)} 原请求已保留，请只读核对或同键重试。`); } }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  function release() {
    if (writing.current || storageError || !pendingRef.current?.rejected || !absent || !preview) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setAbsent(false); setConfirmed(false); setNotice('已结束明确拒绝的原请求，请按新预览重新确认。'); }
    catch { setStorageError('无法清除原请求，继续锁定。'); }
  }
  async function stop(row: Receipt) {
    if (writing.current) return; const token = ++serial.current; writing.current = true; setBusy(true); setError(''); setConfirmed(false); setPreview(null);
    try { await api<unknown>(`/code-integrations/${row.id}/stop`, 'POST', {}); const value = await checkReceipt(await api<unknown>(`/code-integrations/${row.id}`), project); requireValue(value.id === row.id); unchanged(value, row); if (token === serial.current) put(value); }
    catch (e) { if (token === serial.current) setError(`停止结果待核对：${failure(e)}；停止不会承诺回滚产物。`); }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  async function reconcile() {
    if (writing.current || storageError || !readable.current || pendingRef.current) return;
    const prior = declarationRef.current; if (!prior && (!declarationId || !stopped || !checked || !declarationNote.trim())) return;
    const sent: PendingDeclaration = { ...(prior ?? { integration_id: declarationId, payload: { request_id: crypto.randomUUID(), process_stopped: true as const, effects_checked: true as const, note: declarationNote.trim() } }), rejected: false };
    try { sessionStorage.setItem(declarationKey, JSON.stringify(sent)); declarationRef.current = sent; setPendingDeclaration(sent); }
    catch { setStorageError('无法保存原集成核查请求，尚未发送。'); return; }
    const token = ++serial.current; writing.current = true; setBusy(true); setError(''); setConfirmed(false); setPreview(null); setDeclarationAbsent(false); setDeclarationConflict(null); let posted = false;
    try { await api<unknown>(`/code-integrations/${sent.integration_id}/reconciliation`, 'POST', sent.payload); posted = true; await readOriginal(token); }
    catch (e) { if (token === serial.current) { if (!prior && !posted && e instanceof ApiError && [400, 403, 409, 422].includes(e.status)) { try { const rejected = { ...sent, rejected: true }; sessionStorage.setItem(declarationKey, JSON.stringify(rejected)); declarationRef.current = rejected; setPendingDeclaration(rejected); } catch { setStorageError('核查拒绝状态未能保存，仍保留原请求。'); } } setError(`${failure(e)} 原核查请求已保留，不会覆盖已有声明。`); } }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  async function releaseDeclaration(accept = false) {
    const sent = declarationRef.current, expected = declarationConflict;
    if (writing.current || storageError || !sent || (accept ? !expected : !sent.rejected || !declarationAbsent)) return;
    const token = ++serial.current; writing.current = true; setBusy(true); setError('');
    try {
      const row = await checkReceipt(await api<unknown>(`/code-integrations/${sent.integration_id}`), project); if (token !== serial.current) return;
      requireValue(row.id === sent.integration_id);
      if (accept) requireValue(row.reconciliation && same(row.reconciliation, expected) && row.reconciliation.request_payload.request_id !== sent.payload.request_id);
      else requireValue(row.reconciliation === null);
      sessionStorage.removeItem(declarationKey); declarationRef.current = null; setPendingDeclaration(null); setDeclarationConflict(null); setDeclarationAbsent(false); setStopped(false); setChecked(false); setDeclarationNote(''); setPreview(null); setConfirmed(false); put(row);
      setNotice(accept ? '已接受服务端已有核查声明并结束本地请求；这不表示本地原请求成功，原集成仍未知。' : '已结束首次明确拒绝的核查请求，请重新核查并填写说明。');
    } catch (e) { if (token === serial.current) { setError(failure(e)); setDeclarationAbsent(false); } }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  async function openTask(taskId: string, focusReturnTo: HTMLElement, allowCodeReview: boolean) {
    if (writing.current) return; const token = ++serial.current; writing.current = true; setBusy(true); setError(''); setConfirmed(false); setPreview(null);
    try { const task = await api<Task>(`/tasks/${taskId}`); requireValue(task.id === taskId && task.conversation_id === project); if (token === serial.current) setTaskPanel({ task, focusReturnTo, allowCodeReview }); }
    catch (e) { if (token === serial.current) setError(failure(e)); }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  const shown = pending?.preview ?? preview ?? evidence;
  const rows = observed && !history.some(r => r.id === observed.id) ? [observed, ...history] : history;
  return <dialog ref={dialog} className="task-dialog" aria-labelledby={title} onCancel={e => { e.preventDefault(); e.stopPropagation(); onClose(); }}>
    <header><h2 id={title}>代码集成</h2><button onClick={onClose}>关闭代码集成</button></header>
    <p>选择 1–16 个来源，按顺序在新的独立副本集成。不自动合入原仓库或推送，本次不调用模型。</p>
    {error && <p role="alert" className="error">{error}</p>}{storageError && <p role="alert" className="error">{storageError}</p>}{notice && <p role="status">{notice}</p>}
    <button disabled={busy} onClick={() => void refresh(true)}>{pending ? '只读核对原集成请求' : '刷新集成历史'}</button>
    <fieldset disabled={busy || !!pending || !!pendingDeclaration || !!storageError || conversation.archived}><legend>有序来源执行（最多16个）</legend>
      {tasks.map(task => <details key={task.id}><summary>选择来源 · {task.title}</summary><button onClick={() => void loadCandidates(task)}>读取来源执行 · {task.title}</button>{candidates[task.id]?.map((run, i) => <label key={run.id} className="check"><input type="checkbox" aria-label={`选择来源执行 ${run.id}`} checked={selected.includes(run.id)} disabled={!selected.includes(run.id) && (i !== 0 || selected.length >= 16)} onChange={e => change(e.target.checked ? [...selected, run.id] : selected.filter(id => id !== run.id))} />第 {run.attempt} 次 · 需求 v{run.requirement_version} · {run.id}{i !== 0 ? ' · 历史执行' : ' · 当前最新执行，准入以预览为准'}</label>)}</details>)}
      <ol>{selected.map((identity, i) => <li key={identity} style={{ overflowWrap: 'anywhere' }}>{identity}<button disabled={i === 0} onClick={() => { const ids = [...selected]; [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]]; change(ids); }}>上移来源 {i + 1}</button><button onClick={() => change(selected.filter(id => id !== identity))}>移除来源 {i + 1}</button></li>)}</ol>
      <button disabled={!selected.length} onClick={() => void refresh(true)}>读取集成预览</button>
    </fieldset>
    {shown && <section aria-label="固定集成预览"><h3>固定来源与最新评审</h3>{!preview && !pending && <p>以下为此前读取的固定证据；新授权须重新读取当前预览。</p>}{shown.snapshot.sources.map((s, i) => <article className="task-card" key={s.source_snapshot.source_execution.id}><h4>来源 {i + 1} · {s.source_snapshot.source_execution.id}</h4><p>需求 v{s.source_snapshot.source_execution.requirement_version} · 原仓库版本 {s.source_snapshot.repository?.revision} · 集成人 {agents.find(a => a.id === s.source_snapshot.integrator?.id)?.name ?? s.source_snapshot.integrator?.id}</p><p style={{ overflowWrap: 'anywhere' }}>基线 {s.source_snapshot.manifest?.base_commit} · 树 {s.source_snapshot.manifest?.base_tree}</p><p>来源 Owner 决定：{s.source_snapshot.owner_review?.decision === 'approved' ? '已批准' : '未批准'}</p><p>最新评审执行：{s.review_execution?.id ?? '尚无'} · 第 {s.review_execution?.attempt ?? '—'} 次 · 状态 {s.review_execution?.state ?? '尚无'} · Owner {s.review_owner_decision?.decision === 'approved' ? '已批准' : '未批准'}</p><details><summary>全部来源及评审成果</summary>{[...s.source_snapshot.artifacts, ...s.review_artifacts].map(a => <p key={a.id} style={{ overflowWrap: 'anywhere' }}>{a.path} · {a.size} 字节 · {a.id} · SHA-256 {a.sha256}</p>)}</details><button disabled={busy} onClick={e => void openTask(s.source_snapshot.source_task.id, e.currentTarget, true)}>查看来源成果 {i + 1}</button>{s.review_execution && <button disabled={busy} onClick={e => void openTask(s.review_execution!.task_id, e.currentTarget, false)}>查看最新评审报告 {i + 1}</button>}</article>)}<p>上次集成：{shown.latest_integration_id ?? '首次'}</p><details><summary>集成预览指纹</summary><p style={{ overflowWrap: 'anywhere' }}>{shown.fingerprint}</p></details>{preview?.blockers.length ? <ul>{preview.blockers.map((b, i) => <li key={i}>{b}</li>)}</ul> : preview && <p>当前预览允许确认，执行时仍会核对固定来源与权限。</p>}</section>}
    {pending ? <section aria-label="原集成请求待确认"><h3>原集成请求待确认</h3><p>{pending.payload.request_id}</p><p>来源、顺序及授权已锁定。只读核对不会运行；同键重试可能首次执行已授权请求。</p><button disabled={busy || !!storageError} onClick={() => void submit(true)}>同键重试集成请求</button>{pending.rejected && absent && preview && <button disabled={busy || !!storageError} onClick={release}>结束已拒绝集成请求</button>}</section>
      : <form onSubmit={e => { e.preventDefault(); void submit(); }}><fieldset disabled={busy || !!storageError || !!pendingDeclaration || !preview || !!preview.blockers.length || conversation.archived}>{preview?.latest_integration_id && <label>前次集成影响核查说明<textarea aria-label="前次集成影响核查说明" maxLength={2000} value={note} onChange={e => { setNote(e.target.value); setConfirmed(false); }} /></label>}<label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我已核对全部来源及最新评审成果，确认独立集成代码</label><button disabled={!confirmed || !!preview?.latest_integration_id && !note.trim()}>{preview?.latest_integration_id ? '确认新建一次集成' : '确认集成代码'}</button></fieldset></form>}
    <section aria-label="集成历史（最近100条）"><h3>集成历史（最近100条，不代表完整历史）</h3>{rows.map(row => <article className="task-card" key={row.id}><h4>{states[row.state]} · {row.id}</h4><p>原请求 {row.request_id} · {row.created_at}</p><p>{row.summary}</p><p style={{ overflowWrap: 'anywhere' }}>产物目录（相对服务数据目录）：{row.destination_relative}</p><p>分支：{row.branch}</p><details><summary>原授权来源与顺序</summary><ol>{row.request_payload.source_execution_ids.map(id => <li key={id}>{id}</li>)}</ol><p>前次集成 {row.previous_integration_id ?? '无'} · {row.reconciliation_note}</p></details>{row.result && <section aria-label={`实际集成结果 ${row.id}`}><p style={{ overflowWrap: 'anywhere' }}>实际提交 {row.result.commit} · 实际树 {row.result.tree}</p><p>{row.result.files} 个文件 · {row.result.total_bytes} 字节</p><p>原仓库路径：{row.result.source_path}</p>{row.state !== 'completed' && <p>已形成实际结果，但本次集成未成功验收，请核查。</p>}</section>}<p className="muted">历史回执不证明代码目录现在仍存在；代码副本不在数据库备份中。停止不会保证回滚产物。</p>{['running', 'stopping'].includes(row.state) && <button disabled={busy} onClick={() => void stop(row)}>请求停止集成</button>}{row.state === 'unknown' && (row.reconciliation ? <p>Owner 已声明核查（owner_declared）：{row.reconciliation.request_payload.note}。原结果仍未知。</p> : <button disabled={busy || !!pendingDeclaration || !!pending} onClick={() => { setDeclarationId(row.id); setStopped(false); setChecked(false); setDeclarationNote(''); }}>人工核查集成 {row.id}</button>)}</article>)}</section>
    {(declarationId || pendingDeclaration) && <section aria-label="集成人工核查"><h3>集成人工核查</h3><p>{pendingDeclaration?.integration_id ?? declarationId}</p><p>这是 Owner 声明，不是机器退出证据，不会把 unknown 改成成功。解除未知阻塞后，此前已授权 CLI 队列可能继续并产生费用；新集成仍须另行确认。</p>{pendingDeclaration ? <><p>{pendingDeclaration.payload.note} · 原请求 {pendingDeclaration.payload.request_id}</p><button disabled={busy || !!storageError} onClick={() => void reconcile()}>同键重试集成核查</button>{pendingDeclaration.rejected && declarationAbsent && <button disabled={busy || !!storageError} onClick={() => void releaseDeclaration()}>结束已拒绝集成核查</button>}{declarationConflict && <section aria-label="已有其他集成核查声明"><p>服务端已有声明：{declarationConflict.request_payload.note} · 请求 {declarationConflict.request_payload.request_id}</p><p>本地原请求未被确认成功；接受后仅结束本地等待。</p><button disabled={busy || !!storageError} onClick={() => void releaseDeclaration(true)}>接受已有集成核查并结束本地请求</button></section>}</> : <form onSubmit={e => { e.preventDefault(); void reconcile(); }}><fieldset disabled={busy || !!pending || !!storageError || rows.some(r => r.id === declarationId && !!r.reconciliation)}><label className="check"><input type="checkbox" checked={stopped} onChange={e => setStopped(e.target.checked)} />我已核实原生 Git 及其子进程均已停止</label><label className="check"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} />我已核查集成文件及其他外部影响</label><label>集成核查依据<textarea aria-label="集成核查依据" maxLength={2000} value={declarationNote} onChange={e => setDeclarationNote(e.target.value)} /></label><button disabled={!stopped || !checked || !declarationNote.trim()}>保存集成人工核查</button></fieldset></form>}</section>}
    {taskPanel && <TaskExecutions {...taskPanel} conversation={conversation} agents={agents} onClose={() => { returnFocus.current = taskPanel.focusReturnTo; setTaskPanel(null); void refresh(true); }} />}
  </dialog>;
}
