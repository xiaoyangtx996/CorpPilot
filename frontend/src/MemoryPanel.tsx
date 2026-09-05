import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type Task, type TaskExecution, type OwnerReview, type MemoryDocument, type MemoryRevision, type MemoryCandidate } from './api';
import { RetrospectivePanel } from './RetrospectivePanel';

type Pending = { path: string; body: { request_id: string; expected_version?: number; source_execution_id?: string; content?: string; decision?: 'approved' | 'rejected'; note?: string; target_version?: number }; rejected: boolean };
type Source = { task: Task; run: TaskExecution; eligible: boolean };
const message = (error: unknown) => error instanceof Error ? error.message : '请求失败';

export function MemoryPanel({ scope, identity, title, conversationId, onClose }: { scope: 'agent' | 'project'; identity: string; title: string; conversationId: string; onClose: () => void }) {
  const base = `/memories/${scope}/${identity}`, key = `corppilot.memory-pending.v1.${scope}.${identity}`;
  const dialog = useRef<HTMLDialogElement>(null), serial = useRef(0), alive = useRef(false), writing = useRef(false), initialized = useRef(false);
  const [document, setDocument] = useState<MemoryDocument | null>(null), [history, setHistory] = useState<MemoryRevision[]>([]), [candidates, setCandidates] = useState<MemoryCandidate[]>([]), [sources, setSources] = useState<Source[]>([]);
  const [allowed, setAllowed] = useState(false), [checked, setChecked] = useState(false), [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null), [error, setError] = useState(''), [storageError, setStorageError] = useState(''), [notice, setNotice] = useState('');
  const [content, setContent] = useState(''), [version, setVersion] = useState(0), [source, setSource] = useState(''), [candidateId, setCandidateId] = useState(''), [decision, setDecision] = useState<'approved' | 'rejected'>('approved'), [note, setNote] = useState(''), [target, setTarget] = useState(0), [confirmed, setConfirmed] = useState(false);
  function restore() {
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const value = JSON.parse(raw) as Pending, body = value?.body;
        const proposal = value.path === `${base}/candidates`, rollback = value.path === `${base}/rollback`, verdict = /^\/memory-candidates\/[0-9a-f-]{36}\/decision$/.test(value.path);
        const keys = proposal ? 'content,expected_version,request_id,source_execution_id' : rollback ? 'expected_version,note,request_id,target_version' : 'decision,note,request_id';
        if (!body || Object.keys(value).sort().join() !== 'body,path,rejected' || typeof value.rejected !== 'boolean' || !(proposal || rollback || verdict) || Object.keys(body).sort().join() !== keys || typeof body.request_id !== 'string' || !/^[0-9a-f-]{36}$/.test(body.request_id) || (proposal || rollback) && (!Number.isSafeInteger(body.expected_version) || body.expected_version! < 0) || proposal && (typeof body.content !== 'string' || !body.content.trim() || body.content.length > 8000 || typeof body.source_execution_id !== 'string' || !/^[0-9a-f-]{36}$/.test(body.source_execution_id)) || (rollback || verdict) && (typeof body.note !== 'string' || !body.note.trim() || body.note.length > 2000) || rollback && (!Number.isSafeInteger(body.target_version) || body.target_version! < 0) || verdict && !['approved', 'rejected'].includes(body.decision!)) throw Error();
        setPending(value); if (proposal) { setContent(body.content!); setVersion(body.expected_version!); setSource(body.source_execution_id!); initialized.current = true; }
      }
      setStorageError('');
    } catch { setStorageError('无法安全读取待确认记录。恢复会话存储后重新读取，当前禁止写入。'); }
  }
  async function load() {
    if (writing.current) return;
    const token = ++serial.current; setLoading(true); setChecked(false); setError('');
    try {
      const [doc, revisions, rows, agents, conversations] = await Promise.all([api<MemoryDocument>(base), api<MemoryRevision[]>(`${base}/history`), api<MemoryCandidate[]>(`${base}/candidates`), api<Agent[]>('/agents'), api<Conversation[]>('/conversations')]);
      const tasks = conversationId ? await api<Task[]>(`/conversations/${conversationId}/tasks`) : [];
      const extra = [...new Set(rows.map(row => row.source_task_id))].filter(id => !tasks.some(task => task.id === id));
      const allTasks = [...tasks, ...await Promise.all(extra.map(id => api<Task>(`/tasks/${id}`)))];
      const loaded = await Promise.all(allTasks.map(async task => {
        const runs = await api<TaskExecution[]>(`/tasks/${task.id}/executions`), run = runs[0];
        if (!run) return null;
        const review = await api<OwnerReview | null>(`/executions/${run.id}/review`), person = agents.find(row => row.id === run.agent_id), conversation = conversations.find(row => row.id === task.conversation_id);
        return { task, run, eligible: !!(review?.decision === 'approved' && run.state === 'awaiting_review' && run.requirement_version === task.requirement_version && person?.enabled && person.tools.includes('execute') && conversation && !conversation.archived && conversation.member_ids.includes(run.agent_id) && (scope === 'agent' ? run.agent_id === identity : task.conversation_id === identity)) };
      }));
      const last = await api<MemoryDocument>(base);
      if (!alive.current || token !== serial.current) return;
      if (last.version !== doc.version || (revisions.at(-1)?.version ?? 0) !== doc.version) throw Error('读取期间记忆发生变化，请重新读取；草稿保留。');
      setDocument(doc); setHistory(revisions); setCandidates(rows); setCandidateId(current => rows.some(row => row.id === current && !row.decision) ? current : ''); setSources(loaded.filter((row): row is Source => row !== null));
      setAllowed(scope === 'agent' ? !!agents.find(row => row.id === identity)?.enabled : !!conversations.find(row => row.id === identity && !row.archived && row.type !== 'dm'));
      setChecked(true); setConfirmed(false);
      if (!initialized.current) { initialized.current = true; setVersion(doc.version); setContent(doc.content); }
    } catch (error) { if (alive.current && token === serial.current) setError(message(error)); }
    finally { if (alive.current && token === serial.current) setLoading(false); }
  }
  useEffect(() => { alive.current = true; dialog.current?.showModal(); restore(); void load(); return () => { alive.current = false; serial.current++; }; }, []);
  async function send(value: Pending) {
    if (writing.current || storageError) return;
    const first = !pending;
    value = { ...value, rejected: false };
    try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { setStorageError('无法保存原请求，尚未发送。'); return; }
    writing.current = true; serial.current++; setBusy(true); setPending(value); setChecked(false); setNotice('');
    try {
      await api(value.path, 'POST', value.body);
      if (!alive.current) return;
      sessionStorage.removeItem(key); setPending(null); setNotice('原请求已确认保存。候选须批准后生效；执行中的记忆快照保持原版本。');
    } catch (error) {
      if (!alive.current) return;
      const rejected = first && error instanceof ApiError && error.status >= 400 && error.status < 500;
      const saved = { ...value, rejected }; setPending(saved);
      try { sessionStorage.setItem(key, JSON.stringify(saved)); } catch { setStorageError('拒绝结果未能写入存储，原请求仍保留。'); }
      setNotice(`${rejected ? '请求被拒绝，原草稿保留。' : '结果未确认，仅可同键重试。'}${message(error)}`);
    } finally { writing.current = false; if (alive.current) { setBusy(false); void load(); } }
  }
  function reedit() {
    if (!checked || busy || pending && !pending.rejected) return;
    try { sessionStorage.removeItem(key); setPending(null); setVersion(document!.version); setConfirmed(false); setNotice('已明确采用当前记忆版本，全文草稿保留。请重新核对来源和内容。'); } catch { setStorageError('原记录清除失败。'); }
  }
  const blocked = busy || loading || !checked || !!pending || !!storageError;
  const selectedCandidate = candidates.find(row => row.id === candidateId);
  const candidateValid = selectedCandidate && selectedCandidate.expected_version === document?.version && sources.some(row => row.eligible && row.run.id === selectedCandidate.source_execution_id);
  const sourceTitle = (id: string) => sources.find(row => row.task.id === id)?.task.title ?? id;
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="memory-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header><h2 id="memory-title">{scope === 'agent' ? '个人记忆' : '项目共享记忆'} · {title}</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p className="muted">批准记忆按身份或群组供后续执行读取；这是应用内上下文范围，不代表操作系统隔离。未发送草稿关闭后丢弃；已发送请求保留在当前浏览器会话中。</p>
    <button disabled={busy || loading} onClick={() => void load()}>{loading ? '正在读取记忆…' : '读取最新状态（保留草稿）'}</button>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{storageError && <p role="alert">{storageError}<button disabled={busy} onClick={restore}>重新读取待确认记录</button></p>}
    {pending && <section><h3>待确认原请求</h3><p>读取到相同内容不代表原请求已确认。以下重试使用同一路径、请求 ID 和原内容。</p><pre>{JSON.stringify(pending, null, 2)}</pre><button disabled={busy || !!storageError} onClick={() => void send(pending)}>同键重试原请求</button>{pending.rejected && <button disabled={busy || loading || !checked} onClick={reedit}>对照最新状态后重新编辑</button>}</section>}
    {document && <section><h3>{checked ? '当前批准记忆' : '上次读取记忆（待重新核对）'} · v{document.version}</h3><pre>{document.content || '尚无批准内容'}</pre>{checked && !allowed && <p>当前范围已停用或归档：仅可读取及拒绝尚未决定的候选。</p>}</section>}
    <section><h3>提出经验候选{document || pending?.body.content !== undefined ? ` · 草稿基于 v${version}` : ' · 尚未读取记忆版本'}</h3><p>填写批准后替换整个文档的全文，不会自动追加。来源限定为当前会话的最新已批准执行。</p>{!conversationId && <p>先进入一个会话，即可选择该会话的批准成果作为候选来源。个人历史与回滚仍可使用。</p>}<label>来源任务与执行<select disabled={blocked || !allowed} value={source} onChange={event => setSource(event.target.value)}><option value="">选择已批准来源</option>{sources.filter(row => row.eligible && row.task.conversation_id === conversationId).map(row => <option key={row.run.id} value={row.run.id}>{row.task.title} · 需求 v{row.run.requirement_version} · 第 {row.run.attempt} 次 · {row.run.id}</option>)}</select></label>
      <label>候选记忆全文<textarea rows={6} maxLength={8000} disabled={blocked || !allowed} value={content} onChange={event => setContent(event.target.value)} /></label>
      {document && version !== document.version && <p>草稿基于旧版本，请对照当前批准全文后明确采用新版本。</p>}
      <button disabled={blocked || !allowed} onClick={reedit}>采用当前版本（保留全文草稿）</button>
      <button disabled={blocked || !allowed || !content.trim() || version !== document?.version || !sources.some(row => row.eligible && row.run.id === source && row.task.conversation_id === conversationId)} onClick={() => void send({ path: `${base}/candidates`, rejected: false, body: { request_id: crypto.randomUUID(), expected_version: version, source_execution_id: source, content: content.trim() } })}>提交候选</button>
    </section>
    <RetrospectivePanel scope={scope} identity={identity} version={document?.version ?? 0} sources={sources.filter(row => row.eligible && row.task.conversation_id === conversationId).map(row => ({ id: row.run.id, title: row.task.title }))} disabled={blocked || !allowed || !document} onCandidate={() => void load()} />
    <section><h3>经验候选</h3>{!candidates.length && <p>{checked ? '暂无候选。先完成任务成果评审，再提出经验。' : '尚未成功读取候选，不能判断是否为空。'}</p>}{candidates.map(row => <details key={row.id}><summary>{sourceTitle(row.source_task_id)} · 基于记忆 v{row.expected_version} · {row.decision ? row.decision.decision === 'approved' ? '已批准' : '已拒绝' : '待决定'}</summary><p>来源需求 v{row.source_requirement_version} · 执行 {row.source_execution_id} · {row.created_at}</p><pre>{row.content}</pre>{row.decision && <p>{row.decision.note} · 决定时记忆 v{row.decision.result_version} · {row.decision.decided_at}</p>}</details>)}
      <label>待决定候选<select disabled={blocked} value={candidateId} onChange={event => { setCandidateId(event.target.value); setConfirmed(false); }}><option value="">选择候选</option>{candidates.filter(row => !row.decision).map(row => <option key={row.id} value={row.id}>{sourceTitle(row.source_task_id)} · 记忆 v{row.expected_version} · {row.id}</option>)}</select></label>
      {selectedCandidate && <pre>{selectedCandidate.content}</pre>}
      <label>决定<select disabled={blocked} value={decision} onChange={event => { setDecision(event.target.value as 'approved' | 'rejected'); setConfirmed(false); }}><option value="approved">批准全文替换</option><option value="rejected">拒绝候选</option></select></label>
      {selectedCandidate && !selectedCandidate.decision && !candidateValid && <p>此候选的记忆版本、来源需求、最新成果或权限已不符合当前批准条件，可拒绝后重新提案。</p>}
      <label>决定或回滚说明<textarea maxLength={2000} rows={3} disabled={blocked} value={note} onChange={event => setNote(event.target.value)} /></label>
      <label className="check"><input type="checkbox" disabled={blocked} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />已核对全文及影响，确认批准替换或回滚</label>
      <button disabled={blocked || !selectedCandidate || !!selectedCandidate.decision || !note.trim() || decision === 'approved' && (!allowed || !candidateValid || !confirmed)} onClick={() => void send({ path: `/memory-candidates/${candidateId}/decision`, rejected: false, body: { request_id: crypto.randomUUID(), decision, note: note.trim() } })}>保存候选决定</button>
    </section>
    <section><h3>版本与回滚</h3>{!checked && <p>尚未完成最新版本核对；以下已读取记录可能过期，v0 仅表示固定的空记忆基线。</p>}<p>回滚会新建版本，保留所有旧记录；已开始执行的快照不变。</p><details><summary>v0 · 空记忆</summary><p>没有批准内容。</p></details>{history.map(row => <details key={row.version}><summary>v{row.version} · {row.target_version !== null ? `回滚自 v${row.target_version}` : '批准候选'} · {row.created_at}</summary><pre>{row.content || '空记忆'}</pre><p>{row.note}</p></details>)}
      <label>回滚目标<select disabled={blocked || !allowed} value={target} onChange={event => { setTarget(Number(event.target.value)); setConfirmed(false); }}><option value={0}>v0 · 清空批准记忆</option>{history.filter(row => row.version < (document?.version ?? 0)).map(row => <option key={row.version} value={row.version}>v{row.version}</option>)}</select></label><pre>{target === 0 ? '目标全文：空记忆' : history.find(row => row.version === target)?.content}</pre>
      <button disabled={blocked || !allowed || !document || target >= document.version || !note.trim() || !confirmed} onClick={() => void send({ path: `${base}/rollback`, rejected: false, body: { request_id: crypto.randomUUID(), expected_version: document!.version, target_version: target, note: note.trim() } })}>确认回滚并新建版本</button>
    </section>
  </dialog>;
}
