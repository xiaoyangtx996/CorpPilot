import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type Template } from './api';
import { AgentEditor } from './AgentEditor';

export default function App() {
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
      <section className="conversations"><h2>会话</h2><p className="muted">暂无会话</p></section>
      <section className="agent-list"><header><h2>Agent <span className="count">{agents.length}</span></h2><button className="text-button" disabled={!templates.length} onClick={() => setEditor(null)}>新建</button></header>
        <label className="search"><span className="sr-only">搜索 Agent</span><input placeholder="搜索 Agent" value={query} onChange={e => setQuery(e.target.value)} /></label>
        <div className="people">{agents.filter(person => person.name.toLowerCase().includes(query.toLowerCase())).map(person => <button key={person.id} className={`person ${person.id === selected ? 'selected' : ''}`} onClick={() => { setSelected(person.id); setNavigation(false); }}>
          <span className="avatar">{person.name.charAt(0)}</span><span><strong>{person.name}</strong><small>{person.enabled ? '可用' : '已停用'}</small></span>
        </button>)}{!loading && agents.length > 0 && !agents.some(person => person.name.toLowerCase().includes(query.toLowerCase())) && <p className="muted">没有匹配的 Agent</p>}</div>
      </section>
    </aside>
    <main className="workspace" inert={navigation || inspector}>
      <header className="topbar"><button className="mobile" aria-expanded={navigation} onClick={() => setNavigation(true)}>会话与 Agent</button><h1>{agent?.name ?? 'Agent 工作台'}</h1><button className="inspector-toggle" aria-expanded={inspector} onClick={() => setInspector(true)}>Agent 视角</button></header>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => void load()}>重新连接</button></div>}
      <div className="empty-workspace"><div className="large-avatar">{agent?.name.charAt(0) ?? 'C'}</div><h2>{loading ? '正在读取工作台…' : agent?.name ?? '选择一位 Agent'}</h2><p>{agent ? '身份已保存。会话与任务能力正在接入。' : '从左侧选择一位 Agent，或创建自己的长期身份。'}</p>{agent && <button onClick={() => setEditor(agent)}>编辑身份</button>}</div>
    </main>
    <aside ref={inspectorRef} className={`inspector ${inspector ? 'open' : ''}`} aria-label="Agent 视角" role={inspector ? 'dialog' : undefined} aria-modal={inspector || undefined} inert={navigation}><header><h2>Agent 视角</h2><button className="inspector-toggle" onClick={() => setInspector(false)}>关闭</button></header>
      {agent ? <><section><div className="identity-heading"><span className="avatar">{agent.name.charAt(0)}</span><div><h2>{agent.name}</h2><small>{agent.enabled ? '可用' : '已停用'}</small></div></div><p className="muted">身份可用不代表执行实例正在运行。</p></section>
        <section><h3>身份配置</h3><dl><dt>角色</dt><dd>{template?.name}</dd><dt>模型路由</dt><dd>{agent.model}</dd><dt>技能</dt><dd>{agent.skills.join('、') || '未配置'}</dd><dt>工具范围</dt><dd>{agent.tools.join(' / ') || '无'}</dd></dl><button onClick={() => setEditor(agent)}>编辑配置</button> <button disabled={busy} onClick={() => void toggle()}>{agent.enabled ? '停用 Agent' : '重新启用'}</button></section>
        <section><h3>当前任务</h3><p className="muted">暂无任务</p><h3>成果</h3><p className="muted">暂无成果</p></section>
        <section><details><summary>角色定义</summary><p className="source">{template?.source}</p><pre>{template?.instructions}</pre></details></section></> : <p className="muted">请选择 Agent</p>}
    </aside>
    {editor !== undefined && <AgentEditor agent={editor} templates={templates} onClose={() => setEditor(undefined)} onSaved={saved} />}
  </div>;
}
