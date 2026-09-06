import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Agent, type AgentInput, type Template } from './api';
import { checkSkills, SkillText, type Skill } from './Skills';

const creationKey = 'corppilot.agent-create-pending.v1';
type CreationReceipt = { request_id: string; payload: AgentInput; agent: Agent };
type Pending = { request_id: string; payload: AgentInput; receipt?: CreationReceipt; rejected?: boolean };
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(v);
const trim = (v: string) => v.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, '');
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error('身份创建原请求、回执或当前身份的格式与关联不一致。'); }
function keys(v: unknown, names: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === names.split(' ').sort().join()); }
function canonical(v: unknown): string { return JSON.stringify(v, (_key, row) => object(row) ? Object.fromEntries(Object.keys(row).sort().map(key => [key, row[key]])) : row); }
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
function configuration(v: unknown): asserts v is AgentInput {
  keys(v, 'name template_id model skills tools enabled');
  for (const key of ['name', 'template_id', 'model']) { const value = v[key]; requireValue(typeof value === 'string' && !!value && value === trim(value) && Array.from(value).length <= (key === 'name' ? 80 : 200)); }
  for (const key of ['skills', 'tools']) { const list = v[key]; requireValue(Array.isArray(list) && list.length <= 64 && list.every(item => typeof item === 'string' && !!item && item === trim(item) && Array.from(item).length <= 200) && new Set(list).size === list.length); }
  requireValue((v.tools as string[]).every(tool => ['read', 'write', 'execute', 'delegate'].includes(tool)) && typeof v.enabled === 'boolean');
}
function agentValue(v: unknown): Agent {
  keys(v, 'id name template_id model skills tools enabled is_default created_at updated_at');
  requireValue(typeof v.id === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v.id) && typeof v.is_default === 'boolean' && [v.created_at, v.updated_at].every(t => typeof t === 'string' && Number.isFinite(Date.parse(t))));
  const { id: _id, is_default: _default, created_at: _created, updated_at: _updated, ...config } = v; configuration(config); return v as Agent;
}
function original(v: unknown, sent: Pending): CreationReceipt {
  keys(v, 'request_id payload agent'); configuration(v.payload);
  const agent = agentValue(v.agent);
  requireValue(v.request_id === sent.request_id && same(v.payload, sent.payload) && !agent.is_default && same(Object.fromEntries(Object.keys(sent.payload).map(key => [key, agent[key as keyof Agent]])), sent.payload));
  if (sent.receipt) requireValue(same(v, sent.receipt));
  return v as CreationReceipt;
}
function decode(raw: string): Pending {
  const v = JSON.parse(raw); requireValue(object(v) && Object.keys(v).every(key => ['request_id', 'payload', 'receipt', 'rejected'].includes(key)) && uuid(v.request_id) && (v.rejected === undefined || typeof v.rejected === 'boolean') && !(v.rejected && v.receipt)); configuration(v.payload);
  const sent = v as Pending; if (Object.hasOwn(v, 'receipt')) original(v.receipt, sent); return sent;
}

export function AgentEditor({ agent, templates, onClose, onSaved }: {
  agent: Agent | null; templates: Template[]; onClose: () => void; onSaved: (agent: Agent, notice?: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement);
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
  const creationSerial = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null);
  const [pending, setPending] = useState<Pending | null>(null), [creationReady, setCreationReady] = useState(!!agent), [storageError, setStorageError] = useState(''), [absent, setAbsent] = useState(false), [creationNotice, setCreationNotice] = useState('');
  function remember(sent: Pending) { sessionStorage.setItem(creationKey, JSON.stringify(sent)); pendingRef.current = sent; setPending(sent); }
  function display(sent: Pending) { setName(sent.payload.name); setTemplate(sent.payload.template_id); setModel(sent.payload.model); setSkills([...sent.payload.skills]); setTools([...sent.payload.tools]); }
  async function recover(token: number) {
    const sent = pendingRef.current; if (!sent) return;
    const raw = await api<unknown>(`/agent-requests/${encodeURIComponent(sent.request_id)}`); if (token !== creationSerial.current) return;
    if (raw === null) { requireValue(!sent.receipt); setAbsent(!!sent.rejected); setCreationNotice('尚未读取到原创建回执；原请求继续保留，不能据此判定从未创建。'); return; }
    const receipt = original(raw, sent); remember({ ...sent, rejected: false, receipt }); setAbsent(false); setCreationNotice('创建已确认，正在独立读取当前身份；不会重复创建。');
    const current = agentValue(await api<unknown>(`/agents/${receipt.agent.id}`)); if (token !== creationSerial.current) return; requireValue(current.id === receipt.agent.id);
    sessionStorage.removeItem(creationKey); pendingRef.current = null; setPending(null);
    onSaved(current, `已按原请求核对身份创建，未重复创建。当前身份为“${current.name}”${current.enabled ? '' : '（已停用）'}；创建时名称为“${receipt.agent.name}”，当前配置以独立读取结果为准。`);
  }
  async function readCreation() {
    if (writing.current || !creationReady || storageError) return;
    const token = ++creationSerial.current; writing.current = true; setBusy(true); setError(''); setAbsent(false);
    try { await recover(token); }
    catch (e) { if (token === creationSerial.current) setError(`${e instanceof Error ? e.message : '读取失败'}；原创建记录保留，当前状态未确认。`); }
    finally { if (token === creationSerial.current) { writing.current = false; setBusy(false); } }
  }
  async function create(retry = false) {
    if (writing.current || !creationReady || storageError) return;
    const prior = pendingRef.current; if (retry ? !prior : !!prior || skillBlocked) return;
    let sent: Pending;
    try { const payload: AgentInput = { name: trim(name), template_id: trim(template), model: trim(model), skills: [...new Set(skills.map(trim))], tools: [...new Set(tools.map(trim))], enabled: true }; sent = prior ?? { request_id: crypto.randomUUID(), payload }; configuration(sent.payload); sent = { ...sent, rejected: false }; }
    catch (e) { setError(e instanceof Error ? e.message : '创建配置无效'); return; }
    try { remember(sent); } catch { setStorageError('无法保存原创建请求，尚未发送。'); return; }
    const token = ++creationSerial.current; writing.current = true; setBusy(true); setError(''); setAbsent(false); let posted = false;
    try {
      const created = await api<unknown>('/agents', 'POST', { ...sent.payload, request_id: sent.request_id }); posted = true; if (token !== creationSerial.current) return;
      const receipt = original({ request_id: sent.request_id, payload: sent.payload, agent: created }, sent); remember({ ...sent, receipt }); await recover(token);
    } catch (e) {
      if (token === creationSerial.current) {
        if (!prior && !posted && e instanceof ApiError && [400, 403, 409, 422].includes(e.status)) { try { remember({ ...sent, rejected: true }); } catch { setStorageError('无法保存首次拒绝状态，原请求继续保留。'); } }
        setError(`${e instanceof Error ? e.message : '创建失败'}；原请求已保留，请只读核对或同键重试。`);
        if (!posted) { try { await recover(token); } catch (readError) { if (token === creationSerial.current) setError(`${readError instanceof Error ? readError.message : '原请求读取失败'}；原请求继续保留，不会自动重发。`); } }
      }
    }
    finally { if (token === creationSerial.current) { writing.current = false; setBusy(false); } }
  }
  async function releaseCreation() {
    const sent = pendingRef.current; if (writing.current || storageError || !sent?.rejected || !absent) return;
    const token = ++creationSerial.current; writing.current = true; setBusy(true); setError(''); setAbsent(false);
    try { const raw = await api<unknown>(`/agent-requests/${encodeURIComponent(sent.request_id)}`); if (token !== creationSerial.current) return; requireValue(raw === null); sessionStorage.removeItem(creationKey); pendingRef.current = null; setPending(null); setCreationNotice('已结束首次明确拒绝的请求，原配置保留；修改后保存将使用新的请求 ID。'); }
    catch (e) { if (token === creationSerial.current) setError(`${e instanceof Error ? e.message : '读取失败'}；原请求仍保留。`); }
    finally { if (token === creationSerial.current) { writing.current = false; setBusy(false); } }
  }
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
    const node = dialog.current;
    node?.showModal();
    node?.querySelector('input')?.focus();
    void loadSkills();
    if (!agent) {
      const token = ++creationSerial.current;
      try { const raw = sessionStorage.getItem(creationKey); const sent = raw === null ? null : decode(raw); pendingRef.current = sent; setPending(sent); setCreationReady(true); if (sent) { display(sent); writing.current = true; setBusy(true); void recover(token).catch(e => { if (token === creationSerial.current) setError(e instanceof Error ? e.message : '读取原请求失败'); }).finally(() => { if (token === creationSerial.current) { writing.current = false; setBusy(false); } }); } }
      catch { setStorageError('身份创建请求存储损坏或不可读取；原记录保留，禁止创建替代身份。'); }
    }
    return () => { skillSerial.current++; creationSerial.current++; node?.close(); if (opener.current instanceof HTMLElement && opener.current.isConnected) opener.current.focus(); };
  }, []);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!agent) { await create(); return; }
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
      <fieldset disabled={!agent && (!!pending || !creationReady || !!storageError) || busy}>
      <label>名字<input required maxLength={80} value={name} onChange={e => setName(e.target.value)} /></label>
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
      <fieldset><legend>工具范围</legend><p>CLI任务需要同时勾选读取、写入和执行工具，用于读取任务输入并写入成果。缺项仍可保存身份，但不能运行CLI；系统不会自动增加权限。运行中撤销必要权限会请求停止，历史记录保留。</p><p>协调人需要“委派任务”权限才能组织项目和规划，且仍须Owner明确授权。接收任务的身份不因此需要委派权限；系统不会自动增加权限，原请求和历史记录仍可核对。</p>{Object.entries({ read: '读取', write: '写入', execute: '执行工具', delegate: '委派任务' }).map(([key, label]) => <label className="check" key={key}><input type="checkbox" checked={tools.includes(key)} onChange={e => setTools(e.target.checked ? [...tools, key] : tools.filter(v => v !== key))} />{label}</label>)}</fieldset>
      </fieldset>
      {storageError && <p role="alert" className="error">{storageError}</p>}{creationNotice && <p role="status">{creationNotice}</p>}
      {pending && <section aria-label="原身份创建请求待确认"><h3>原身份创建请求待确认</h3><p>{pending.request_id}</p><p>原配置已锁定；只读核对不会创建身份，同键重试可能首次完成此前已授权的创建。</p><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(pending.payload, null, 2)}</pre>{pending.receipt && <p>原创建已确认：{pending.receipt.agent.name} · {pending.receipt.agent.id}；当前身份尚须独立读取。</p>}<button type="button" disabled={busy || !!storageError} onClick={() => void readCreation()}>只读核对原身份请求</button><button type="button" disabled={busy || !!storageError} onClick={() => void create(true)}>同键重试身份创建</button>{pending.rejected && absent && <button type="button" disabled={busy || !!storageError} onClick={() => void releaseCreation()}>结束已拒绝身份请求</button>}</section>}
      {error && <p role="alert" className="error">{error}</p>}
      <footer><button type="button" disabled={busy} onClick={onClose}>{pending ? '关闭并保留原请求' : '取消'}</button><button className="primary" disabled={busy || skillBlocked || !agent && (!!pending || !creationReady || !!storageError)}>{busy ? '保存中…' : '保存'}</button></footer>
    </form>
  </dialog>;
}
