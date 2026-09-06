import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type Conversation, type Task, type TaskDependencyStatus } from './api';

type Request = { expected_version: number; task_ids: string[] };
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败';
const equal = (left: string[], right: string[]) => [...left].sort().join() === [...right].sort().join();

export function TaskDependencies({ task, onClose, onUpdate }: { task: Task; onClose: () => void; onUpdate: (task: Task) => void }) {
  const dialog = useRef<HTMLDialogElement>(null), generation = useRef(0), writing = useRef(false);
  const key = `corppilot.dependencies-pending.v1.${task.id}`;
  const [current, setCurrent] = useState<{ task: Task; status: TaskDependencyStatus; tasks: Task[]; editable: boolean } | null>(null);
  const [draft, setDraft] = useState<Request | null>(null), [pending, setPending] = useState<Request | null>(null);
  const initialized = useRef(false);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [storageError, setStorageError] = useState(''), [notice, setNotice] = useState('');
  const [checked, setChecked] = useState(false);

  function restore() {
    try {
      const raw = sessionStorage.getItem(key);
      if (raw !== null) {
        const row = JSON.parse(raw);
        if (!row || Object.keys(row).sort().join() !== 'expected_version,task_ids' || !Number.isSafeInteger(row.expected_version) || row.expected_version < 1 || !Array.isArray(row.task_ids) || row.task_ids.length > 32 || row.task_ids.some((id: unknown) => typeof id !== 'string' || !id || id.length > 120) || new Set(row.task_ids).size !== row.task_ids.length) throw new Error();
        initialized.current = true; setDraft(row); setPending(row);
      }
      setStorageError('');
    } catch { setStorageError('待确认依赖请求读取失败，当前禁止保存。请恢复会话存储后重新读取。'); }
  }
  async function load() {
    if (writing.current) return;
    const token = ++generation.current;
    setLoading(true); setError(''); setChecked(false);
    try {
      const [latest, status, tasks, conversation, agents] = await Promise.all([
        api<Task>(`/tasks/${task.id}`), api<TaskDependencyStatus>(`/tasks/${task.id}/dependencies`),
        api<Task[]>(`/conversations/${task.conversation_id}/tasks`), api<Conversation>(`/conversations/${task.conversation_id}`), api<Agent[]>('/agents'),
      ]);
      if (token !== generation.current) return;
      if (latest.requirement_version !== status.requirement_version) throw new Error('读取期间需求发生变化，请重新读取；草稿保持不变。');
      const editable = !conversation.archived && conversation.member_ids.includes(latest.agent_id) && agents.some(agent => agent.id === latest.agent_id && agent.enabled);
      setCurrent({ task: latest, status, tasks, editable }); onUpdate(latest); setChecked(true);
      if (!initialized.current) { initialized.current = true; setDraft({ expected_version: status.requirement_version, task_ids: status.task_ids }); }
    } catch (error) { if (token === generation.current) setError(failure(error)); }
    finally { if (token === generation.current) setLoading(false); }
  }
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal(); restore(); void load();
    return () => { generation.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  function removePending() {
    try { sessionStorage.removeItem(key); setStorageError(''); setPending(null); return true; }
    catch { setStorageError('待确认记录清除失败，继续保留原请求。请恢复会话存储后核对。'); return false; }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (writing.current || !draft || storageError || (pending ? current?.editable === false : !current?.editable || !checked)) return;
    const sent = pending ?? draft;
    try { sessionStorage.setItem(key, JSON.stringify(sent)); }
    catch { setStorageError('浏览器无法保存原请求，尚未发送；请恢复会话存储后重试。'); return; }
    setPending(sent); writing.current = true; const token = ++generation.current;
    setBusy(true); setError(''); setNotice(''); setChecked(false);
    try {
      const status = await api<TaskDependencyStatus>(`/tasks/${task.id}/dependencies`, 'PATCH', sent);
      if (token !== generation.current) return;
      if (removePending()) { setDraft({ expected_version: status.requirement_version, task_ids: status.task_ids }); }
      setNotice(`依赖已保存，需求 v${status.requirement_version}。未启动执行。`);
    } catch (error) {
      if (token !== generation.current) return;
      setNotice(error instanceof ApiError && error.status === 409 ? '需求版本冲突，草稿和原版本已保留。请读取最新状态、对照后明确选择重新编辑。' : error instanceof ApiError && error.status >= 400 && error.status < 500 ? `保存被拒绝：${failure(error)}。上次保存的内容与版本已保留。` : `保存未确认：${failure(error)}。上次保存的内容与版本已保留，重试仍使用原版本。`);
    } finally {
      if (token === generation.current) { writing.current = false; setBusy(false); void load(); }
    }
  }
  function reedit() {
    if (!current || !draft || !checked || busy || loading || !current.editable) return;
    if (!removePending()) return;
    setDraft({ ...draft, expected_version: current.status.requirement_version }); setNotice('已明确采用当前读取版本，选择草稿保持不变；请检查后重新保存。');
  }
  const title = (id: string) => `${current?.tasks.find(item => item.id === id)?.title ?? '未加载的任务'} · ${id}`;
  const conflict = draft && current && draft.expected_version !== current.status.requirement_version;
  return <dialog ref={dialog} className="task-dialog" aria-labelledby="dependencies-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header><h2 id="dependencies-title">前置任务 · {task.title}</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p className="muted">选择同一会话的前置任务，最多 32 项。变更会增加需求版本；保存不会调用模型或启动执行。未提交的选择草稿在关闭后丢弃；已发送的待确认请求会保留。</p>
    <button disabled={loading || busy} onClick={() => void load()}>{loading ? '读取依赖中…' : '读取最新状态（保留草稿）'}</button>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {storageError && <p className="error" role="alert">{storageError}<button disabled={busy} onClick={restore}>重新读取待确认记录</button></p>}
    {current && <section aria-label="当前前置状态"><h3>{checked ? '当前需求' : '上次读取需求'} v{current.status.requirement_version}</h3>{!checked && <p role="status">以下为上次读取状态，可能已过期；尚未完成最新核对。</p>}<p>{current.status.ready ? current.status.task_ids.length ? current.status.handoff_authorized ? '当前实例的前置成果可按本批授权交接，尚不代表Owner已批准。修改需求或再次执行不继承原授权。' : '前置成果已满足批准条件。' : '未设置前置任务。' : `前置条件未满足：${current.status.blocked_reason}`}</p><p className="muted">以上仅表示前置条件，不表示本任务已执行或已验收。执行时仍会重新核查。</p><ul>{current.status.task_ids.map(id => <li key={id}>{title(id)}</li>)}</ul>{!current.editable && <p className="archive-notice">当前为只读：会话已归档，或负责人未启用 / 不在会话中。</p>}</section>}
    {draft && <form onSubmit={save}><h3>选择草稿 · 基于 v{draft.expected_version} · {draft.task_ids.length} / 32 项</h3>
      {pending && <p role="status">上次保存的内容与版本已保留；当前选择一致不代表上次保存已确认。</p>}
      {conflict && <p className="error" role="alert">草稿版本与当前需求不同；不会自动改用新版本。</p>}
      <fieldset disabled={!checked || loading || busy || !!pending || !!storageError || !current?.editable || !!conflict}>
        <legend>同会话任务</legend>{current?.tasks.filter(item => item.id !== task.id).map(item => <label className="check" key={item.id}><input type="checkbox" checked={draft.task_ids.includes(item.id)} disabled={!draft.task_ids.includes(item.id) && draft.task_ids.length >= 32} onChange={event => setDraft({ ...draft, task_ids: event.target.checked ? [...draft.task_ids, item.id].sort() : draft.task_ids.filter(id => id !== item.id) })} />{item.title} · v{item.requirement_version}<small>{item.id}</small></label>)}
        {draft.task_ids.filter(id => !current?.tasks.some(item => item.id === id)).map(id => <p key={id}>未加载的已选任务：{id}</p>)}
        <button type="button" onClick={() => setDraft({ ...draft, task_ids: [] })}>清空选择</button>
      </fieldset>
      {pending && current && equal(pending.task_ids, current.status.task_ids) && checked && <p role="status">当前依赖与上次保存的选择一致；这不代表上次保存已确认。</p>}
      <footer><button className="primary" disabled={busy || loading || !!storageError || (pending ? current?.editable === false : !checked || !current?.editable || !!conflict)}>{busy ? '保存中…' : pending ? '重试原版本请求' : '保存前置任务'}</button>
        {(pending || conflict) && <button type="button" disabled={busy || loading || !checked || !current?.editable || !!storageError} onClick={reedit}>已对照最新状态，保留草稿重新编辑</button>}
      </footer>
    </form>}
  </dialog>;
}
