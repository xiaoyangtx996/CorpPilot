import { TaskDependencies } from './TaskDependencies';
import { CodeIntegration } from './CodeIntegration';
import { TaskExecutions } from './TaskExecutions';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError, type Agent, type Conversation, type Message, type Task, type TaskDraft, type TaskFields, type TaskRevision } from './api';

const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试';
const fields = ({ title, scope, acceptance, agent_id }: TaskFields): TaskFields => ({ title, scope, acceptance, agent_id });

function TaskDialog({ title, children, onClose, busy = false }: { title: string; children: ReactNode; onClose: () => void; busy?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.showModal();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  return <dialog ref={ref} className="task-dialog" aria-labelledby="task-dialog-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header><h2 id="task-dialog-title">{title}</h2><button type="button" disabled={busy} onClick={onClose}>关闭</button></header>{children}
  </dialog>;
}

export function TaskBoard({ conversation, agents, messages, source, draft }: {
  conversation: Conversation; agents: Agent[]; messages: Message[]; source: { message: Message } | null; draft: { taskDraft?: TaskDraft };
}) {
  const storageKey = `corppilot.task-pending.v1.${conversation.id}`;
  const [storageError, setStorageError] = useState('');
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [editor, setEditor] = useState(false);
  const [executionTask, setExecutionTask] = useState<Task | null>(null);
  const [integrationOpen, setIntegrationOpen] = useState(false);
  const [dependencyTask, setDependencyTask] = useState<Task | null>(null);
  const [value, setValue] = useState<TaskDraft | undefined>(draft.taskDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [history, setHistory] = useState<{ task: Task; rows: TaskRevision[]; loading: boolean; error: string } | null>(null);
  const lifecycle = useRef(0);
  const reads = useRef(0);
  const historyReads = useRef(0);
  const posting = useRef(false);
  const members = agents.filter(agent => agent.enabled && conversation.member_ids.includes(agent.id));
  const owner = (id: string) => {
    const agent = agents.find(item => item.id === id);
    return `${agent?.name ?? id}${!conversation.member_ids.includes(id) ? ' · 已移出会话' : agent && !agent.enabled ? ' · 已停用' : ''}`;
  };
  function change(next: TaskDraft | undefined) { draft.taskDraft = next; setValue(next); }
  function clearPending() {
    try { sessionStorage.removeItem(storageKey); setStorageError(''); }
    catch { setStorageError('浏览器未能清除待确认记录；下次打开会沿用原请求核对，不会重复创建。'); }
  }
  function restorePending() {
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (raw) {
        const saved: unknown = JSON.parse(raw);
        if (!saved || typeof saved !== 'object') throw new Error();
        const row = saved as Record<string, unknown>;
        if (row.pending !== true || row.id !== undefined || !['title', 'scope', 'acceptance', 'agent_id', 'source_message_id', 'source_content', 'request_id'].every(key => typeof row[key] === 'string') || !row.request_id || !row.source_message_id) throw new Error();
        change({ ...fields(row as TaskDraft), source_message_id: row.source_message_id as string, source_content: row.source_content as string, request_id: row.request_id as string, pending: true });
      }
      setStorageError('');
    } catch { setStorageError('待确认记录读取失败。为避免重复创建，请先刷新任务列表核对；当前无法安全创建新任务。'); }
  }
  useEffect(() => { restorePending(); }, []);
  async function load() {
    const version = ++reads.current, life = lifecycle.current;
    setLoading(true); setLoadError('');
    try { const rows = await api<Task[]>(`/conversations/${conversation.id}/tasks`); if (life === lifecycle.current && version === reads.current) setTasks(rows); }
    catch (error) { if (life === lifecycle.current && version === reads.current) setLoadError(failure(error)); }
    finally { if (life === lifecycle.current && version === reads.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { lifecycle.current++; }; }, []);
  useEffect(() => {
    if (!source || conversation.archived || storageError) return;
    if (draft.taskDraft) { setValue(draft.taskDraft); setError('已有任务草稿，请先完成或取消该草稿。'); }
    else { change({ title: '', scope: source.message.content, acceptance: '', agent_id: members[0]?.id ?? '', source_message_id: source.message.id, source_content: source.message.content, request_id: crypto.randomUUID() }); setError(''); }
    setEditor(true);
  }, [source]);
  function edit(task: Task) {
    if (draft.taskDraft) { setValue(draft.taskDraft); setError('已有任务草稿，请先完成或取消该草稿。'); }
    else { change({ ...fields(task), id: task.id, source_message_id: task.source_message_id, source_content: messages.find(message => message.id === task.source_message_id)?.content ?? '', request_id: task.request_id, expected_version: task.requirement_version }); setError(''); }
    setEditor(true);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!value || posting.current || conversation.archived || value.conflict) return;
    if (!value.pending && !members.some(agent => agent.id === value.agent_id)) return;
    const sent = { ...value, pending: true };
    const life = lifecycle.current;
    // Persist before POST: an interrupted tab must reuse the identical creation payload.
    if (!sent.id) {
      try { sessionStorage.setItem(storageKey, JSON.stringify(sent)); setStorageError(''); }
      catch { setError('浏览器无法保存待确认请求，尚未发送。草稿已保留，请恢复浏览器会话存储后重试。'); return; }
    }
    change(sent); posting.current = true; setBusy(true); setError('');
    try {
      const saved = await api<Task>(sent.id ? `/tasks/${sent.id}` : `/conversations/${conversation.id}/tasks`, sent.id ? 'PATCH' : 'POST', sent.id ? { ...fields(sent), expected_version: sent.expected_version } : { ...fields(sent), source_message_id: sent.source_message_id, request_id: sent.request_id });
      if (life !== lifecycle.current) return;
      reads.current++; setLoading(false);
      setTasks(current => [saved, ...current.filter(task => task.id !== saved.id)]);
      if (!sent.id) clearPending();
      change(undefined); setEditor(false); setNotice(`任务“${saved.title}”已保存，需求版本 v${saved.requirement_version}。未启动执行。`);
    } catch (error) {
      if (life !== lifecycle.current) return;
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        if (!sent.id) clearPending();
        change({ ...sent, pending: false, conflict: error.status === 409 });
        setError(error.status === 409 ? '需求版本已变化。你的草稿已保留，请读取最新版本，再明确选择重新编辑。' : failure(error));
      } else setError(`保存结果未知：${failure(error)}。原内容与请求已保留，请使用“重试原请求”核对结果。`);
    } finally { if (life === lifecycle.current) { posting.current = false; setBusy(false); } }
  }
  async function refreshVersion() {
    if (!value?.id || posting.current) return;
    const life = lifecycle.current;
    posting.current = true; setBusy(true); setError('');
    try {
      const latest = await api<Task>(`/tasks/${value.id}`);
      if (life !== lifecycle.current) return;
      change({ ...value, expected_version: latest.requirement_version, pending: false, conflict: false });
      reads.current++; setLoading(false); setTasks(current => [latest, ...current.filter(task => task.id !== latest.id)]);
      setError(`已读取最新需求 v${latest.requirement_version}，并保留你的草稿。请对照下方最新内容，检查后再保存。`);
    } catch (error) { if (life === lifecycle.current) setError(failure(error)); }
    finally { if (life === lifecycle.current) { posting.current = false; setBusy(false); } }
  }
  async function showHistory(task: Task) {
    const request = ++historyReads.current, life = lifecycle.current;
    setHistory({ task, rows: [], loading: true, error: '' });
    try { const rows = await api<TaskRevision[]>(`/tasks/${task.id}/revisions`); if (life === lifecycle.current && request === historyReads.current) setHistory({ task, rows, loading: false, error: '' }); }
    catch (error) { if (life === lifecycle.current && request === historyReads.current) setHistory({ task, rows: [], loading: false, error: failure(error) }); }
  }
  const current = tasks.find(task => task.id === value?.id);
  return <section className="task-board" aria-label="本会话任务">
    {conversation.type === 'project' && <button onClick={() => setIntegrationOpen(true)}>代码集成</button>}
    {integrationOpen && <CodeIntegration key={conversation.id} conversation={conversation} tasks={tasks} agents={agents} onClose={() => setIntegrationOpen(false)} />}
    <header><h2>本会话任务 <span className="count">{tasks.length}</span></h2><button disabled={loading} onClick={() => void load()}>{loading ? '读取任务中…' : '刷新任务'}</button></header>
    <p className="muted">任务保存需求与负责人，不会调用模型或启动执行。{conversation.archived ? '本会话已归档，任务只读。' : '从 Owner 消息下方创建任务。'}</p>
    {notice && <p role="status">{notice}</p>}{loadError && <p className="error" role="alert">任务读取失败：{loadError}</p>}{storageError && <p className="error" role="alert">{storageError}<button onClick={restorePending}>重新读取待确认记录</button></p>}
    {value && <button onClick={() => { setEditor(true); setError(''); }}>{value.pending ? '核对待确认任务' : '继续任务草稿'}</button>}
    {!loading && !loadError && !tasks.length && <p className="muted">本会话暂无任务。</p>}
    {tasks.map(task => <article className="task-card" key={task.id}><header><h3>{task.title}</h3><span>需求 v{task.requirement_version}</span></header><p className="muted">负责人：{owner(task.agent_id)}</p><dl><dt>范围</dt><dd>{task.scope}</dd><dt>验收标准</dt><dd>{task.acceptance}</dd></dl><details><summary>来源：Owner 消息</summary><p>{messages.find(message => message.id === task.source_message_id)?.content ?? '该源消息尚未加载，可继续加载消息历史查看。'}</p><small>源消息 ID：{task.source_message_id}</small></details><footer><button disabled={conversation.archived} onClick={() => edit(task)}>编辑任务</button><button onClick={() => void showHistory(task)}>历史版本</button><button onClick={() => setDependencyTask(task)}>前置任务</button><button onClick={() => setExecutionTask(task)}>执行记录与控制</button></footer></article>)}
    {dependencyTask && <TaskDependencies key={dependencyTask.id} task={dependencyTask} onClose={() => setDependencyTask(null)} onUpdate={latest => { reads.current++; setLoading(false); setTasks(current => current.map(item => item.id === latest.id ? latest : item)); }} />}
    {executionTask && <TaskExecutions key={executionTask.id} task={executionTask} conversation={conversation} agents={agents} onClose={() => setExecutionTask(null)} />}
    {editor && value && <TaskDialog title={value.id ? '编辑任务需求' : '从消息创建任务'} busy={busy} onClose={() => setEditor(false)}><form onSubmit={save}>
      <p className="muted">{value.id ? `基于需求 v${value.expected_version}` : '新任务 · 需求 v1'} · 保存仅持久化，不启动执行。</p>
      <details><summary>来源：Owner 消息</summary><p className="task-source">{value.source_content || '源消息尚未加载。'}</p><small>源消息 ID：{value.source_message_id}</small></details>
      <fieldset className="task-fields" disabled={busy || value.pending || conversation.archived || value.conflict}>
        <label>任务标题<input autoFocus required maxLength={200} value={value.title} onChange={event => change({ ...value, title: event.target.value })} /></label>
        <label>范围<textarea required rows={4} maxLength={16000} value={value.scope} onChange={event => change({ ...value, scope: event.target.value })} /></label>
        <label>验收标准<textarea required rows={4} maxLength={16000} value={value.acceptance} onChange={event => change({ ...value, acceptance: event.target.value })} /></label>
        <label>负责人<select required value={value.agent_id} onChange={event => change({ ...value, agent_id: event.target.value })}><option value="" disabled>选择启用的会话成员</option>{value.agent_id && !members.some(agent => agent.id === value.agent_id) && <option value={value.agent_id} disabled>{owner(value.agent_id)}（请重新选择）</option>}{members.map(agent => <option value={agent.id} key={agent.id}>{agent.name}</option>)}</select></label>
      </fieldset>
      {!members.length && <p className="muted">没有启用的会话成员，请先管理会话成员或启用 Agent。</p>}
      {value.pending && <p role="status">该请求结果待确认，字段已锁定；重试沿用原始内容{value.id ? '与版本' : '与请求 ID'}，避免重复创建。</p>}
      {conversation.archived && <p className="archive-notice">会话已归档，仅可查看；恢复会话后才能保存或核对请求。</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {value.id && current && <details><summary>对照当前已读取需求 v{current.requirement_version}</summary><p className="task-source">{current.title}</p><p className="task-source">范围：{current.scope}</p><p className="task-source">验收标准：{current.acceptance}</p><p>负责人：{owner(current.agent_id)}</p></details>}
      <footer>{!value.pending && <button type="button" disabled={busy} onClick={() => { change(undefined); setEditor(false); }}>取消草稿</button>}{value.conflict ? <button type="button" disabled={busy || conversation.archived} onClick={() => void refreshVersion()}>读取最新版本并保留草稿重新编辑</button> : <button className="primary" disabled={busy || conversation.archived || (!value.id && !!storageError) || (!value.pending && (!value.title.trim() || !value.scope.trim() || !value.acceptance.trim() || !members.some(agent => agent.id === value.agent_id)))}>{busy ? '保存中…' : value.pending ? '重试原请求' : '保存任务'}</button>}</footer>
    </form></TaskDialog>}
    {history && <TaskDialog title={`历史版本 · ${history.task.title}`} onClose={() => { historyReads.current++; setHistory(null); }}><p className="muted">历史需求为只读快照，负责人及前置任务名称按当前记录显示，ID 保留历史绑定。</p>{history.loading && <p role="status">正在读取历史版本…</p>}{history.error && <p className="error" role="alert">{history.error}<button onClick={() => void showHistory(history.task)}>重试读取历史</button></p>}{history.rows.map(row => <article className="task-card" key={row.requirement_version}><header><h3>需求 v{row.requirement_version} · {row.title}</h3></header><small>{new Date(row.created_at).toLocaleString()} · 负责人：{owner(row.agent_id)}</small><dl><dt>范围</dt><dd>{row.scope}</dd><dt>验收标准</dt><dd>{row.acceptance}</dd><dt>前置任务</dt><dd>{row.dependency_task_ids?.length ? <ul>{row.dependency_task_ids.map(id => <li key={id}>{tasks.find(item => item.id === id)?.title ?? '未加载的任务'} · {id}</li>)}</ul> : '无'}</dd></dl></article>)}</TaskDialog>}
  </section>;
}
