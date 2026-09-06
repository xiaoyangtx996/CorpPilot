import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type AgentInput, type Template } from './api';
import { checkSkills, SkillText, type Skill } from './Skills';

export function AgentEditor({ agent, templates, onClose, onSaved }: {
  agent: Agent | null; templates: Template[]; onClose: () => void; onSaved: (agent: Agent) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(agent?.name ?? '');
  const [template, setTemplate] = useState(agent?.template_id ?? templates[0]?.id ?? '');
  const [model, setModel] = useState(agent?.model ?? 'default');
  const originalSkills = useRef(agent?.skills ?? []);
  const [skills, setSkills] = useState<string[]>([...originalSkills.current]);
  const [catalog, setCatalog] = useState<Skill[] | null>(null), [skillLoading, setSkillLoading] = useState(false), [skillError, setSkillError] = useState('');
  const skillSerial = useRef(0);
  const [tools, setTools] = useState(agent?.tools ?? ['read']);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const changedSkills = JSON.stringify(skills) !== JSON.stringify(originalSkills.current);
  const unknownSkills = catalog ? skills.filter(id => !catalog.some(skill => skill.id === id)) : [];
  const skillBlocked = changedSkills && (skillLoading || !catalog || !!skillError || !!unknownSkills.length || new Set(skills).size !== skills.length);
  async function loadSkills() {
    const token = ++skillSerial.current; setSkillLoading(true); setSkillError(''); setCatalog(null);
    try { const rows = await checkSkills(await api<unknown>('/skills'), true); if (token === skillSerial.current) setCatalog(rows); }
    catch (e) { if (token === skillSerial.current) setSkillError(e instanceof Error ? e.message : '读取技能目录失败'); }
    finally { if (token === skillSerial.current) setSkillLoading(false); }
  }
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    dialog.current?.querySelector('input')?.focus();
    void loadSkills();
    return () => { skillSerial.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy || skillBlocked) return;
    setBusy(true); setError('');
    const value: AgentInput = { name: name.trim(), template_id: template, model: model.trim(),
      skills: [...skills], tools, enabled: agent?.enabled ?? true };
    try { onSaved(await api<Agent>(agent ? `/agents/${agent.id}` : '/agents', agent ? 'PATCH' : 'POST', value)); }
    catch (e) { setError(e instanceof Error ? e.message : '保存失败'); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} aria-labelledby="editor-title" onCancel={event => { if (busy) event.preventDefault(); else onClose(); }}>
    <form onSubmit={save}>
      <header><h2 id="editor-title">{agent ? '编辑 Agent' : '创建 Agent'}</h2><button type="button" aria-label="关闭" disabled={busy} onClick={onClose}>关闭</button></header>
      <label>名字<input autoFocus required maxLength={80} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>角色模板<select required value={template} onChange={e => setTemplate(e.target.value)}>{templates.map(role => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>
      <label>模型路由<input required maxLength={200} value={model} onChange={e => setModel(e.target.value)} /><small>default 使用默认模型路由。</small></label>
      <fieldset disabled={busy || skillLoading}><legend>内置技能</legend>
        <p>保存身份不会启动运行；运行使用当次固定的技能内容。未修改技能列表时，可保留旧标签并修改身份资料。</p>
        <button type="button" onClick={() => void loadSkills()}>重新读取技能目录</button>
        {skillLoading && <p role="status">读取技能目录中…</p>}{skillError && <p role="alert" className="error">{skillError}；原技能选择已保留。</p>}
        {catalog?.map(skill => <div key={skill.id}><label className="check"><input type="checkbox" aria-label={`选择技能 ${skill.id}`} checked={skills.includes(skill.id)} onChange={e => setSkills(e.target.checked ? [...skills, skill.id] : skills.filter(id => id !== skill.id))} />{skill.name} · {skill.id}</label><SkillText skill={skill} /></div>)}
        {unknownSkills.length > 0 && <section aria-label="当前不可用的技能"><h3>当前不可用的技能</h3><p>这些旧标签不会自动删除。需要替换时，请明确移除旧标签并选择内置技能。</p>{unknownSkills.map((id, index) => <p key={`${id}:${index}`}>{id}<button type="button" onClick={() => setSkills(skills.filter((_, i) => i !== skills.indexOf(id)))}>移除不可用技能 {id}</button></p>)}</section>}
        {!catalog && <p>保留的技能 ID：{skills.length ? skills.join('、') : '无'}</p>}
        {skillBlocked && <p role="status">技能列表已变更；请成功读取目录，并明确移除或替换不可用、重复的标签后保存。</p>}
      </fieldset>
      <fieldset><legend>工具范围</legend>{Object.entries({ read: '读取', write: '写入', execute: '执行工具', delegate: '委派任务' }).map(([key, label]) => <label className="check" key={key}><input type="checkbox" checked={tools.includes(key)} onChange={e => setTools(e.target.checked ? [...tools, key] : tools.filter(v => v !== key))} />{label}</label>)}</fieldset>
      {error && <p role="alert" className="error">{error}</p>}
      <footer><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy || skillBlocked}>{busy ? '保存中…' : '保存'}</button></footer>
    </form>
  </dialog>;
}
