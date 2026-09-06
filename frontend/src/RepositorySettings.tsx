import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation } from './api';

type Request = { request_id: string; expected_revision: number; source_path: string | null; commit: string | null; integration_agent_id: string | null; confirm: true };
export type RepositoryBinding = { conversation_id: string; revision: number; request_id: string; created_at: string; snapshot: { source_path: string; commit: string; tree: string; files: number; total_bytes: number; integration_agent_id: string } | null };
type Pending = { conversation_id: string; payload: Request; receipt?: RepositoryBinding; rejected?: boolean };
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => Number.isSafeInteger(v) && (v as number) >= min && (v as number) <= max;
const text = (e: unknown) => e instanceof Error ? e.message : '仓库读取失败';
const label = (v: unknown): v is string => typeof v === 'string' && !!v.trim() && v === v.trim() && v.length <= 200 && !/[\u0000-\u001f\u007f]/.test(v);
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error('仓库回执或原请求的关联与格式不一致。'); }
function keys(v: unknown, fields: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === fields.split(' ').sort().join()); }
function sourcePath(raw: unknown) {
  requireValue(typeof raw === 'string' && raw.length <= 4096 && !/[\u0000-\u001f\u007f]/.test(raw));
  let path = raw.trim().replaceAll('/', '\\');
  requireValue(/^[A-Za-z]:\\/.test(path));
  path = path[0].toUpperCase() + path.slice(1);
  while (path.length > 3 && path.endsWith('\\')) path = path.slice(0, -1);
  requireValue(path.slice(3).split('\\').every(part => !!part && part !== '.' && part !== '..' && !/[. ]$/.test(part) && !/[:<>"|?*]/.test(part)) || path.length === 3);
  return path;
}
function request(raw: unknown): asserts raw is Request {
  keys(raw, 'request_id expected_revision source_path commit integration_agent_id confirm');
  requireValue(typeof raw.request_id === 'string' && /^[A-Za-z0-9_-]{1,120}$/.test(raw.request_id) && integer(raw.expected_revision, 0, Number.MAX_SAFE_INTEGER - 1) && raw.confirm === true);
  if (raw.source_path === null && raw.commit === null && raw.integration_agent_id === null) return;
  requireValue(raw.source_path === sourcePath(raw.source_path) && typeof raw.commit === 'string' && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(raw.commit) && label(raw.integration_agent_id));
}
export function checkRepository(raw: unknown, conversationId: string): RepositoryBinding {
  keys(raw, 'conversation_id revision request_id created_at snapshot');
  requireValue(raw.conversation_id === conversationId && integer(raw.revision, 1) && typeof raw.request_id === 'string' && /^[A-Za-z0-9_-]{1,120}$/.test(raw.request_id) && typeof raw.created_at === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(raw.created_at) && Number.isFinite(Date.parse(raw.created_at)));
  if (raw.snapshot !== null) {
    keys(raw.snapshot, 'source_path commit tree files total_bytes integration_agent_id');
    sourcePath(raw.snapshot.source_path);
    requireValue(typeof raw.snapshot.commit === 'string' && /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(raw.snapshot.commit) && typeof raw.snapshot.tree === 'string' && new RegExp(`^[0-9a-f]{${raw.snapshot.commit.length}}$`).test(raw.snapshot.tree) && integer(raw.snapshot.files, 0, 10000) && integer(raw.snapshot.total_bytes, 0, 256 * 1024 * 1024) && label(raw.snapshot.integration_agent_id));
  }
  return raw as RepositoryBinding;
}
function same(a: RepositoryBinding, b: RepositoryBinding) {
  return a.conversation_id === b.conversation_id && a.revision === b.revision && a.request_id === b.request_id && a.created_at === b.created_at && (a.snapshot === null ? b.snapshot === null : b.snapshot !== null && sourcePath(a.snapshot.source_path) === sourcePath(b.snapshot.source_path) && (['commit', 'tree', 'files', 'total_bytes', 'integration_agent_id'] as const).every(k => a.snapshot![k] === b.snapshot![k]));
}
function matches(raw: unknown, sent: Pending) {
  const row = checkRepository(raw, sent.conversation_id), p = sent.payload;
  requireValue(row.request_id === p.request_id && row.revision === p.expected_revision + 1);
  requireValue(p.source_path === null ? row.snapshot === null : row.snapshot !== null && sourcePath(row.snapshot.source_path) === p.source_path && row.snapshot.commit === p.commit && row.snapshot.integration_agent_id === p.integration_agent_id);
  if (sent.receipt) requireValue(same(row, sent.receipt));
  return row;
}
function decode(raw: string, id: string): Pending {
  const row = JSON.parse(raw);
  requireValue(object(row) && Object.keys(row).every(k => ['conversation_id', 'payload', 'receipt', 'rejected'].includes(k)) && row.conversation_id === id && (row.rejected === undefined || typeof row.rejected === 'boolean') && !(row.rejected && row.receipt));
  request(row.payload);
  if (row.receipt !== undefined) matches(row.receipt, row as Pending);
  return row as Pending;
}
export function RepositoryEvidence({ row }: { row: RepositoryBinding }) {
  return <article className="task-card"><p>绑定版本 {row.revision} · 请求 {row.request_id}</p>{row.snapshot ? <><p style={{ overflowWrap: 'anywhere' }}>{row.snapshot.source_path}</p><p>集成人：{row.snapshot.integration_agent_id}</p><p>{row.snapshot.files} 个文件 · {row.snapshot.total_bytes} 字节</p><details><summary>查看固定提交与树</summary><p style={{ overflowWrap: 'anywhere' }}>提交 {row.snapshot.commit}</p><p style={{ overflowWrap: 'anywhere' }}>文件树 {row.snapshot.tree}</p></details></> : <p>此版本已停用后续仓库绑定。</p>}<small>{row.created_at}</small></article>;
}

export function RepositorySettings({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const base = `/conversations/${conversationId}/repository`, key = `corppilot.repository-pending.v1.${conversationId}`;
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement), serial = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null), storageReadable = useRef(false);
  const [pending, setPending] = useState<Pending | null>(null), [latest, setLatest] = useState<RepositoryBinding | null>(null), [verified, setVerified] = useState<RepositoryBinding | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null), [agents, setAgents] = useState<Agent[]>([]);
  const [path, setPath] = useState(''), [commit, setCommit] = useState(''), [integrator, setIntegrator] = useState(''), [disabled, setDisabled] = useState(false), [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false), [loaded, setLoaded] = useState(false), [error, setError] = useState(''), [storageError, setStorageError] = useState(''), [notice, setNotice] = useState(''), [absent, setAbsent] = useState(false);
  function save(row: Pending) { sessionStorage.setItem(key, JSON.stringify(row)); pendingRef.current = row; setPending(row); }
  async function current(token: number) {
    const [room, people, binding] = await Promise.all([api<Conversation>(`/conversations/${conversationId}`), api<Agent[]>('/agents'), api<unknown>(base)]);
    requireValue(room && room.id === conversationId && room.type === 'project' && typeof room.archived === 'boolean' && Array.isArray(room.member_ids) && room.member_ids.every(label));
    requireValue(Array.isArray(people) && people.every(person => person && label(person.id) && typeof person.name === 'string' && typeof person.enabled === 'boolean'));
    const row = binding === null ? null : checkRepository(binding, conversationId);
    if (token !== serial.current) return;
    setConversation(room); setAgents(people.filter(a => a.enabled && room.member_ids.includes(a.id))); setLatest(row); setLoaded(true); setConfirm(false);
    setPath(row?.snapshot?.source_path ?? ''); setCommit(row?.snapshot?.commit ?? ''); setIntegrator(row?.snapshot?.integration_agent_id ?? ''); setDisabled(row !== null && row.snapshot === null);
  }
  async function exact(sent: Pending, token: number) {
    const raw = await api<unknown>(`${base}/requests/${encodeURIComponent(sent.payload.request_id)}`);
    if (token !== serial.current) return;
    if (raw === null) { setAbsent(sent.rejected === true); setNotice('尚未读到原请求回执，不证明原请求从未受理；原内容继续保留。'); return; }
    const row = matches(raw, sent);
    sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setVerified(row); setAbsent(false); setNotice('已独立核对原仓库请求；原回执不代表当前最新绑定。');
  }
  async function refresh() {
    if (writing.current) return;
    const token = ++serial.current; writing.current = true; setBusy(true); setLoaded(false); setLatest(null); setError(''); setAbsent(false);
    try { if (storageReadable.current && pendingRef.current) await exact(pendingRef.current, token); await current(token); }
    catch (e) { if (token === serial.current) setError(text(e)); }
    finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  useEffect(() => {
    const node = dialog.current; node?.showModal(); writing.current = false;
    try { const raw = sessionStorage.getItem(key), row = raw ? decode(raw, conversationId) : null; pendingRef.current = row; setPending(row); storageReadable.current = true; }
    catch { storageReadable.current = false; setStorageError('原仓库请求存储损坏或不可读取，已保留并锁定提交。'); }
    void refresh();
    return () => { serial.current++; node?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); };
  }, [conversationId]);
  async function submit(retry = false) {
    if (writing.current || !storageReadable.current || (!retry && (pendingRef.current || !loaded || !confirm || conversation?.archived))) return;
    let sent = pendingRef.current;
    if (retry && !sent) return;
    try {
      if (!sent) {
        requireValue(disabled || agents.some(a => a.id === integrator));
        sent = { conversation_id: conversationId, payload: { request_id: crypto.randomUUID(), expected_revision: latest?.revision ?? 0, source_path: disabled ? null : sourcePath(path), commit: disabled ? null : commit.trim(), integration_agent_id: disabled ? null : integrator, confirm: true } };
        request(sent.payload);
      }
      sent = { conversation_id: sent.conversation_id, payload: sent.payload, ...(sent.receipt ? { receipt: sent.receipt } : {}) }; save(sent);
    } catch (e) { setError(text(e)); return; }
    const token = ++serial.current; writing.current = true; setBusy(true); setLoaded(false); setLatest(null); setError(''); setNotice(''); setAbsent(false); setConfirm(false); let posted = false;
    try {
      const raw = await api<unknown>(base, 'POST', sent.payload); posted = true;
      if (token !== serial.current) return;
      const row = matches(raw, sent); sent = { ...sent, receipt: row }; save(sent);
      await exact(sent, token); await current(token);
    } catch (e) {
      if (token !== serial.current) return;
      if (!posted && !sent.receipt && e instanceof ApiError && [400, 403, 409, 422].includes(e.status)) {
        try { save({ ...sent, rejected: true }); } catch { setStorageError('无法保存拒绝状态，原请求仍保留。'); }
      }
      setError(text(e));
    } finally { if (token === serial.current) { writing.current = false; setBusy(false); } }
  }
  function release() {
    if (busy || !loaded || !absent || !pendingRef.current?.rejected || !storageReadable.current) return;
    try { sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setAbsent(false); setConfirm(false); setNotice('已结束明确拒绝的请求，请基于当前版本重新核查授权。'); }
    catch { setStorageError('无法清除原请求，继续锁定。'); }
  }
  return <dialog ref={dialog} aria-label="项目代码仓库" onCancel={e => { e.preventDefault(); onClose(); }}><header><h2>项目代码仓库</h2><button onClick={onClose}>关闭项目代码仓库</button></header>
    <p>项目 {conversation?.title ?? conversationId}。新执行在入队创建时冻结绑定；保存或停用不改变已有执行，不自动启动任务，也不执行 Git 合入或推送。</p>
    {storageError && <p role="alert" className="error">{storageError}</p>}{error && <p role="alert" className="error">{error}</p>}{notice && <p role="status">{notice}</p>}
    <button disabled={busy} onClick={() => void refresh()}>{pending ? '只读核对原仓库请求' : '刷新项目仓库'}</button>
    {pending && <section aria-label="待确认仓库请求"><h3>原仓库请求待确认</h3><p>请求 {pending.payload.request_id} · 预期版本 {pending.payload.expected_revision}</p><p style={{ overflowWrap: 'anywhere' }}>{pending.payload.source_path ?? '停用后续绑定'}</p><p style={{ overflowWrap: 'anywhere' }}>{pending.payload.commit}</p><p>集成人 {pending.payload.integration_agent_id ?? '不适用'}</p><button disabled={busy || !!storageError} onClick={() => void submit(true)}>同键重试原仓库请求</button>{pending.rejected && absent && loaded && <button disabled={busy || !!storageError} onClick={release}>结束已拒绝请求并重新核查</button>}</section>}
    {verified && <section aria-label="已核对的原仓库回执"><h3>已核对的原仓库回执</h3><RepositoryEvidence row={verified} /></section>}
    <section aria-label="最新项目仓库"><h3>最新项目仓库</h3>{loaded ? latest ? <RepositoryEvidence row={latest} /> : <p>尚无项目仓库绑定。</p> : <p>最新绑定尚未完成核对。</p>}</section>
    {conversation?.archived && <p>项目已归档，只能观察已有绑定。</p>}
    <form onSubmit={e => { e.preventDefault(); void submit(); }}><fieldset className="task-fields" disabled={busy || !loaded || !!pending || !!storageError || !!conversation?.archived}>
      <legend>保存新绑定版本</legend><label className="check"><input type="checkbox" checked={disabled} onChange={e => { setDisabled(e.target.checked); setConfirm(false); }} />停用后续执行的仓库绑定</label>
      <label>本机仓库路径<input aria-label="本机仓库路径" disabled={disabled} required={!disabled} value={path} onChange={e => { setPath(e.target.value); setConfirm(false); }} /></label>
      <label>完整提交 SHA<input aria-label="完整提交 SHA" disabled={disabled} required={!disabled} value={commit} onChange={e => { setCommit(e.target.value); setConfirm(false); }} /></label>
      <label>代码集成人<select aria-label="代码集成人" disabled={disabled} required={!disabled} value={integrator} onChange={e => { setIntegrator(e.target.value); setConfirm(false); }}><option value="">选择项目内已启用成员</option>{agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="check"><input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />我确认新执行使用此固定提交，已有执行绑定不变</label><button disabled={!confirm}>确认保存仓库绑定</button>
    </fieldset></form>
    <details><summary>仓库准备与恢复边界</summary><p>仅本机盘符绝对路径与完整40/64位小写 SHA；不接受分支名、UNC、.或..路径段。原生 Git 会再次检查源库、普通文件、固定树及大小限制。源码副本尚未创建时，绑定回执也可以存在。</p><p>每实例独立 repository 子目录与分支；集成人只是明确责任与权限，不代表已经实现代码成果交接或合并。备份不含源库和 checkout。Git 准备未知不能仅凭 Docker 不存在确认停止。</p></details>
  </dialog>;
}
