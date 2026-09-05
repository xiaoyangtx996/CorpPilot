import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type AgentInput, type Template } from './api';

export function AgentEditor({ agent, templates, onClose, onSaved }: {
  agent: Agent | null; templates: Template[]; onClose: () => void; onSaved: (agent: Agent) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(agent?.name ?? '');
  const [template, setTemplate] = useState(agent?.template_id ?? templates[0]?.id ?? '');
  const [model, setModel] = useState(agent?.model ?? 'default');
  const [skills, setSkills] = useState(agent?.skills.join(', ') ?? '');
  const [tools, setTools] = useState(agent?.tools ?? ['read']);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    dialog.current?.querySelector('input')?.focus();
    return () => { if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    const value: AgentInput = { name: name.trim(), template_id: template, model: model.trim(),
      skills: skills.split(/[,，]/).map(s => s.trim()).filter(Boolean), tools, enabled: agent?.enabled ?? true };
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
      <label>技能<input value={skills} onChange={e => setSkills(e.target.value)} placeholder="技能 ID，以逗号分隔" /></label>
      <fieldset><legend>工具范围</legend>{Object.entries({ read: '读取', write: '写入', execute: '执行工具', delegate: '委派任务' }).map(([key, label]) => <label className="check" key={key}><input type="checkbox" checked={tools.includes(key)} onChange={e => setTools(e.target.checked ? [...tools, key] : tools.filter(v => v !== key))} />{label}</label>)}</fieldset>
      {error && <p role="alert" className="error">{error}</p>}
      <footer><button type="button" disabled={busy} onClick={onClose}>取消</button><button className="primary" disabled={busy}>{busy ? '保存中…' : '保存'}</button></footer>
    </form>
  </dialog>;
}
