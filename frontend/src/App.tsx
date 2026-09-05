import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type Template, type Conversation, type Message, type CollaborationPlan } from './api';
import { AgentEditor } from './AgentEditor';
import { ConversationEditor, ConversationMessages, type Draft } from './ConversationEditor';
import { ModelSettings } from './ModelSettings';
import { CliSettings } from './CliSettings';
import { ExecutionReconciliation } from './ExecutionReconciliation';
import { CollaborationPanel } from './CollaborationPanel';
import { PlanningPanel } from './PlanningPanel';
import { MemoryPanel } from './MemoryPanel';

export default function App() {
  const [collaboration, setCollaboration] = useState<{ conversationId: string; source?: Message; initialPlan?: CollaborationPlan } | null>(null);
  const [planning, setPlanning] = useState<{ conversationId: string; source?: Message } | null>(null);
  const [memory, setMemory] = useState<{ scope: 'agent' | 'project'; identity: string; title: string; conversationId: string } | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editor, setEditor] = useState<Agent | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [navigation, setNavigation] = useState(false);
  const [inspector, setInspector] = useState(false);
  const [modelSettings, setModelSettings] = useState(false);
  const [cliSettings, setCliSettings] = useState(false);
  const [reconciliation, setReconciliation] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState('');
  const [conversationEditor, setConversationEditor] = useState<Conversation | null | undefined>(undefined);
  const [conversationError, setConversationError] = useState('');
  const [conversationBusy, setConversationBusy] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const drafts = useRef<Record<string, Draft>>({});
  const selection = useRef(0);
  const conversationRequest = useRef(0);
  const conversationRevision = useRef(0);
  const navigationRef = useRef<HTMLElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const panel = navigation ? navigationRef.current : inspector ? inspectorRef.current : null;
    if (!panel) return;
    const previous = document.activeElement;
    const focusable = () => Array.from(panel.querySelectorAll<HTMLElement>('button:not(:disabled),input,select,summary,[tabindex="0"]')).filter(el => el.getClientRects().length);
    focusable()[0]?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') { setNavigation(false); setInspector(false); }
      if (event.key === 'Tab') {
        const elements = focusable();
        const first = elements[0], last = elements.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }
    panel.addEventListener('keydown', keydown);
    return () => { panel.removeEventListener('keydown', keydown); if (previous instanceof HTMLElement) previous.focus(); };
  }, [navigation, inspector]);
  const agent = agents.find(item => item.id === selected);
  const template = templates.find(item => item.id === agent?.template_id);
  const conversation = conversations.find(item => item.id === conversationId);
  if (conversation && !drafts.current[conversation.id]) drafts.current[conversation.id] = { content: '', request_id: '', sentContent: '' };
  function changedConversation(value: Conversation) {
    conversationRevision.current++;
    setConversations(current => {
      const previous = current.find(item => item.id === value.id);
      const next = previous && (previous.last_message?.sequence ?? 0) > (value.last_message?.sequence ?? 0)
        ? { ...value, last_message: previous.last_message, updated_at: previous.updated_at > value.updated_at ? previous.updated_at : value.updated_at }
        : value;
      return [next, ...current.filter(item => item.id !== value.id)].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    });
  }
  function chooseConversation(value: Conversation) {
    selection.current++; setConversationId(value.id); setConversationError(''); setNavigation(false);
    if (value.member_ids.length) setSelected(value.member_ids[0]);
  }
  async function loadConversations() {
    const request = ++conversationRequest.current;
    const revision = conversationRevision.current;
    const isCurrent = () => request === conversationRequest.current && revision === conversationRevision.current;
    setConversationError('');
    try {
      const values = await api<Conversation[]>('/conversations');
      if (isCurrent()) setConversations(values);
      else if (request === conversationRequest.current) void loadConversations();
    } catch (error) {
      if (isCurrent()) setConversationError(error instanceof Error ? error.message : '会话读取失败');
      else if (request === conversationRequest.current) void loadConversations();
    }
  }
  useEffect(() => { void loadConversations(); }, []);
  async function openDm(person: Agent) {
    const version = ++selection.current;
    setSelected(person.id); setNavigation(false); setConversationError('');
    const existing = conversations.find(item => item.type === 'dm' && item.member_ids.includes(person.id));
    if (existing) { setConversationId(existing.id); return; }
    setConversationId('');
    if (!person.enabled) { setConversationError('Agent 已停用，重新启用后可新建私聊。'); return; }
    try {
      const value = await api<Conversation>('/conversations', 'POST', { type: 'dm', title: person.name, member_ids: [person.id] });
      changedConversation(value);
      if (selection.current === version) setConversationId(value.id);
    } catch (error) { if (selection.current === version) setConversationError(error instanceof Error ? error.message : '私聊创建失败，请重新选择 Agent'); }
  }
  async function archive() {
    if (!conversation || conversationBusy) return;
    setConversationBusy(true); setConversationError('');
    try { changedConversation(await api<Conversation>(`/conversations/${conversation.id}`, 'PATCH', { archived: !conversation.archived })); }
    catch (error) { setConversationError(error instanceof Error ? error.message : '会话更新失败'); }
    finally { setConversationBusy(false); }
  }
  function receivedMessage(message: Message) {
    conversationRevision.current++;
    setConversations(current => current.map(item => item.id === message.conversation_id && message.sequence > (item.last_message?.sequence ?? 0) ? { ...item, last_message: message, updated_at: message.created_at } : item).sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
  }
  async function load() {
    setLoading(true); setError('');
    try {
      const [roles, people] = await Promise.all([api<Template[]>('/templates'), api<Agent[]>('/agents')]);
      setTemplates(roles); setAgents(people);
      setSelected(current => people.some(person => person.id === current) ? current : (people.find(person => person.template_id.includes('secretary')) ?? people[0])?.id ?? '');
    } catch (e) { setError(e instanceof Error ? e.message : '连接失败'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  function saved(person: Agent) {
    setAgents(current => current.some(item => item.id === person.id) ? current.map(item => item.id === person.id ? person : item) : [...current, person]);
    setSelected(person.id); setEditor(undefined);
  }
  async function toggle() {
    if (!agent || busy) return;
    setBusy(true); setError('');
    try { saved(await api<Agent>(`/agents/${agent.id}`, 'PATCH', { enabled: !agent.enabled })); }
    catch (e) { setError(e instanceof Error ? e.message : '更新失败'); }
    finally { setBusy(false); }
  }
  return <div className="app" aria-busy={loading}>
    <aside ref={navigationRef} className={`navigation ${navigation ? 'open' : ''}`} aria-label="会话与 Agent" role={navigation ? 'dialog' : undefined} aria-modal={navigation || undefined} inert={inspector}>
      <div className="brand">CorpPilot<button className="mobile" onClick={() => setNavigation(false)}>关闭</button></div>
      <section className="conversations"><header><h2>会话</h2><button className="text-button" disabled={!agents.some(agent => agent.enabled)} onClick={() => setConversationEditor(null)}>新建群聊</button></header>
        <label className="check"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />显示已归档</label>
        <div className="conversation-list">{conversations.filter(item => showArchived || !item.archived).map(item => <button key={item.id} className={`conversation-item ${item.id === conversationId ? 'selected' : ''}`} onClick={() => chooseConversation(item)}><strong>{item.title}</strong><small>{item.archived ? '已归档' : item.type === 'dm' ? '私聊' : item.type === 'board' ? '董事会' : '项目群'} · {item.last_message?.content ?? '暂无消息'}</small></button>)}{!conversations.some(item => showArchived || !item.archived) && <p className="muted">暂无会话，选择 Agent 开始私聊。</p>}</div>
      </section>
      <section className="agent-list"><header><h2>Agent <span className="count">{agents.length}</span></h2><button className="text-button" disabled={!templates.length} onClick={() => setEditor(null)}>新建</button></header>
        <label className="search"><span className="sr-only">搜索 Agent</span><input placeholder="搜索 Agent" value={query} onChange={e => setQuery(e.target.value)} /></label>
        <div className="people">{agents.filter(person => person.name.toLowerCase().includes(query.toLowerCase())).map(person => <button key={person.id} className={`person ${person.id === selected ? 'selected' : ''}`} onClick={() => void openDm(person)}>
          <span className="avatar">{person.name.charAt(0)}</span><span><strong>{person.name}</strong><small>{person.enabled ? '可用' : '已停用'}</small></span>
        </button>)}{!loading && agents.length > 0 && !agents.some(person => person.name.toLowerCase().includes(query.toLowerCase())) && <p className="muted">没有匹配的 Agent</p>}</div>
      </section>
      <div className="navigation-settings"><button onClick={() => { setNavigation(false); setPlanning({ conversationId }); }}>模型协作提案</button><button onClick={() => { setNavigation(false); setCollaboration({ conversationId }); }}>协作计划与恢复</button><button onClick={() => { setNavigation(false); setReconciliation(true); }}>执行核查</button><button onClick={() => setModelSettings(true)}>模型设置</button><button onClick={() => setCliSettings(true)}>CLI 设置</button></div>
    </aside>
    <main className="workspace" inert={navigation || inspector}>
      <header className="topbar"><button className="mobile" aria-expanded={navigation} onClick={() => setNavigation(true)}>会话与 Agent</button><h1>{conversation?.title ?? agent?.name ?? 'Agent 工作台'}</h1><button className="inspector-toggle" aria-expanded={inspector} onClick={() => setInspector(true)}>Agent 视角</button></header>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => void load()}>重新连接</button></div>}
      {conversationError && <div className="error-banner" role="alert">{conversationError}<button onClick={() => void loadConversations()}>刷新会话列表</button></div>}
      {conversation ? <><div className="conversation-toolbar"><span className="muted">{conversation.type === 'dm' ? '私聊' : conversation.type === 'board' ? '董事会' : '项目群'} · {conversation.member_ids.length} 位 Agent{conversation.archived ? ' · 已归档' : ''}</span><button onClick={() => setConversationEditor(conversation)}>管理会话</button>{conversation.type !== 'dm' && <button onClick={() => setMemory({ scope: 'project', identity: conversation.id, title: conversation.title, conversationId: conversation.id })}>项目共享记忆</button>}<button disabled={conversationBusy} onClick={() => void archive()}>{conversation.archived ? '恢复会话' : '归档会话'}</button></div><ConversationMessages key={conversation.id} conversation={conversation} agents={agents} draft={drafts.current[conversation.id]} onMessage={receivedMessage} onPlanning={source => setPlanning({ conversationId: conversation.id, source })} onCollaboration={source => setCollaboration({ conversationId: conversation.id, source })} onSettings={() => setModelSettings(true)} /></> : <div className="empty-workspace"><div className="large-avatar">{agent?.name.charAt(0) ?? 'C'}</div><h2>{loading ? '正在读取工作台…' : '选择会话开始讨论'}</h2><p>选择左侧 Agent 开启私聊，或创建董事会与项目群。消息会持久保存；选定成员并确认后，才会调用已配置模型回复。</p>{agent && <button onClick={() => setEditor(agent)}>编辑身份</button>}</div>}
    </main>
    <aside ref={inspectorRef} className={`inspector ${inspector ? 'open' : ''}`} aria-label="Agent 视角" role={inspector ? 'dialog' : undefined} aria-modal={inspector || undefined} inert={navigation}><header><h2>Agent 视角</h2><button className="inspector-toggle" onClick={() => setInspector(false)}>关闭</button></header>
      {conversation && <section><label>查看会话成员<select aria-label="查看会话成员" value={conversation.member_ids.includes(selected) ? selected : ''} onChange={event => setSelected(event.target.value)}><option value="" disabled>选择 Agent</option>{agents.filter(person => conversation.member_ids.includes(person.id)).map(person => <option value={person.id} key={person.id}>{person.name}{person.enabled ? '' : ' · 已停用'}</option>)}</select></label></section>}
      {agent ? <><section><div className="identity-heading"><span className="avatar">{agent.name.charAt(0)}</span><div><h2>{agent.name}</h2><small>{agent.enabled ? '可用' : '已停用'}</small></div></div><p className="muted">身份可用不代表执行实例正在运行。</p></section>
        <section><h3>身份配置</h3><dl><dt>角色</dt><dd>{template?.name}</dd><dt>模型路由</dt><dd>{agent.model}</dd><dt>技能</dt><dd>{agent.skills.join('、') || '未配置'}</dd><dt>工具范围</dt><dd>{agent.tools.join(' / ') || '无'}</dd></dl><button onClick={() => setEditor(agent)}>编辑配置</button> <button disabled={busy} onClick={() => void toggle()}>{agent.enabled ? '停用 Agent' : '重新启用'}</button></section>
        <section><h3>持久记忆</h3><button onClick={() => { setInspector(false); setMemory({ scope: 'agent', identity: agent.id, title: agent.name, conversationId: conversationId }); }}>个人记忆</button></section><section><h3>任务需求</h3><p className="muted">在会话消息下方的“本会话任务”查看任务、负责人和需求版本。</p><h3>成果</h3><p className="muted">在任务的“执行记录与控制”中展开“成果与 Owner 评审”，查看下载与验收决定。</p></section>
        <section><details><summary>角色定义</summary><p className="source">{template?.source}</p><pre>{template?.instructions}</pre></details></section></> : <p className="muted">请选择 Agent</p>}
    </aside>
    {editor !== undefined && <AgentEditor agent={editor} templates={templates} onClose={() => setEditor(undefined)} onSaved={saved} />}
    {modelSettings && <ModelSettings onClose={() => setModelSettings(false)} />}
    {planning && <PlanningPanel {...planning} onClose={() => setPlanning(null)} onImport={(id, initialPlan) => { setPlanning(null); setCollaboration({ conversationId: id, initialPlan }); }} onRecoverCollaboration={() => { setPlanning(null); setCollaboration({ conversationId: planning.conversationId }); }} />}
    {collaboration && <CollaborationPanel {...collaboration} onClose={() => setCollaboration(null)} onOpenProject={value => { changedConversation(value); chooseConversation(value); }} />}
    {memory && <MemoryPanel key={`${memory.scope}.${memory.identity}`} {...memory} onClose={() => setMemory(null)} />}
    {reconciliation && <ExecutionReconciliation onClose={() => setReconciliation(false)} />}
    {cliSettings && <CliSettings onClose={() => setCliSettings(false)} />}
    {conversationEditor !== undefined && <ConversationEditor conversation={conversationEditor} agents={agents} onClose={() => setConversationEditor(undefined)} onSaved={value => { changedConversation(value); if (!conversationEditor) chooseConversation(value); }} />}
  </div>;
}
