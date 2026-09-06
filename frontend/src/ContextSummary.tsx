import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { SkillInputs } from './Skills';

type Digest = { chars: number; sha256: string };
type Message = Digest & { id: string | null; sequence: number | null; sender_kind: 'owner' | 'agent'; sender_id: string | null; content_source: 'conversation_message' | 'authorized_shared_brief' | 'retrospective_snapshot' };
type ModelKind = 'reply' | 'planning' | 'retrospective' | 'peer_review';
type Common = { version: 1; kind: 'model' | 'cli'; run_id: string; agent_id: string; attempt: number; requirement_version: number; phase: 'prepared'; prepared_at: string; model: string; template_id: string; instructions: Digest };
type Summary = Common & ({ kind: 'model'; run_kind: ModelKind; messages: Message[]; context_truncated: boolean; source_sequence: number } | { kind: 'cli'; backend: 'local' | 'docker'; task: { id: string; requirement_version: number; title: Digest; scope: Digest; acceptance: Digest }; source_message: Message; memories: (Digest & { scope: 'agent' | 'project'; scope_id: string; version: number })[]; input_artifacts: { id: string; execution_id: string; path: string; size: number; sha256: string }[] });
type Target = { kind: 'model' | 'cli'; run_id: string; agent_id: string; conversation_id: string; modelKind?: ModelKind };
const issue = '当次上下文回执的关联或元数据格式不一致，未展示内容。';
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown, min = 0): v is number => Number.isSafeInteger(v) && (v as number) >= min;
const label = (v: unknown, max = 200): v is string => typeof v === 'string' && !!v.trim() && v === v.trim() && v.length <= max && !/[\u0000-\u001f\u007f]/.test(v);
const hash = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error(issue); }
function keys(v: unknown, names: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === names.split(' ').sort().join()); }
function digest(v: unknown): asserts v is Digest { keys(v, 'chars sha256'); requireValue(integer(v.chars) && hash(v.sha256)); }
function message(v: unknown): asserts v is Message {
  keys(v, 'id sequence sender_kind sender_id content_source chars sha256');
  requireValue((v.id === null || label(v.id)) && (v.sequence === null || integer(v.sequence, 1)) && ['owner', 'agent'].includes(v.sender_kind as string) && (v.sender_id === null || label(v.sender_id)) && ['conversation_message', 'authorized_shared_brief', 'retrospective_snapshot'].includes(v.content_source as string) && integer(v.chars) && hash(v.sha256));
  requireValue(v.sender_kind !== 'agent' || label(v.sender_id));
}
function validate(value: unknown, run: unknown, target: Target): Summary | null {
  requireValue(object(run) && run.id === target.run_id && run.agent_id === target.agent_id && integer(run.attempt, 1) && integer(run.requirement_version, 1));
  if (value === null) return null;
  const common = 'version kind run_id agent_id attempt requirement_version phase prepared_at model template_id instructions';
  keys(value, common + (target.kind === 'model' ? ' run_kind messages context_truncated source_sequence' : ' backend task source_message memories input_artifacts'));
  requireValue(value.version === 1 && value.kind === target.kind && value.run_id === target.run_id && value.agent_id === target.agent_id && value.attempt === run.attempt && value.requirement_version === run.requirement_version && value.phase === 'prepared' && label(value.model) && label(value.template_id) && typeof value.prepared_at === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(value.prepared_at) && Number.isFinite(Date.parse(value.prepared_at)));
  digest(value.instructions);
  if (target.kind === 'model') {
    requireValue(['reply', 'planning', 'retrospective', 'peer_review'].includes(value.run_kind as string) && (!target.modelKind || value.run_kind === target.modelKind) && Array.isArray(value.messages) && value.messages.length >= 1 && value.messages.length <= 100 && typeof value.context_truncated === 'boolean' && integer(value.source_sequence));
    value.messages.forEach(message);
    const rows = value.messages as Message[];
    if (value.run_kind === 'retrospective') requireValue(rows.length === 1 && rows[0].id === null && rows[0].sequence === null && rows[0].content_source === 'retrospective_snapshot' && value.source_sequence === 0 && value.context_truncated === false);
    else {
      requireValue(run.conversation_id === target.conversation_id && label(run.source_message_id) && integer(value.source_sequence, 1));
      requireValue(rows.every((row, index) => label(row.id) && integer(row.sequence, 1) && row.sequence <= (value.source_sequence as number) && (index === 0 || row.sequence > rows[index - 1].sequence!) && (row.content_source === 'conversation_message' || value.run_kind === 'planning' && row.content_source === 'authorized_shared_brief')));
      requireValue(rows.at(-1)!.id === run.source_message_id && rows.at(-1)!.sequence === value.source_sequence && new Set(rows.map(row => row.id)).size === rows.length);
      if (value.run_kind !== 'reply') requireValue(rows.length === 1 && value.context_truncated === false);
    }
  } else {
    requireValue(['local', 'docker'].includes(value.backend as string));
    keys(value.task, 'id requirement_version title scope acceptance');
    requireValue(value.task.id === run.task_id && label(value.task.id) && value.task.requirement_version === run.requirement_version);
    digest(value.task.title); digest(value.task.scope); digest(value.task.acceptance);
    message(value.source_message);
    requireValue(label(value.source_message.id) && integer(value.source_message.sequence, 1) && value.source_message.sender_kind === 'owner' && value.source_message.content_source === 'conversation_message');
    requireValue(Array.isArray(value.memories) && value.memories.length <= 2 && Array.isArray(value.input_artifacts) && value.input_artifacts.length <= 100);
    const scopes = new Set();
    value.memories.forEach(row => { keys(row, 'scope scope_id version chars sha256'); requireValue(['agent', 'project'].includes(row.scope as string) && !scopes.has(row.scope) && row.scope_id === (row.scope === 'agent' ? target.agent_id : target.conversation_id) && integer(row.version, 1) && integer(row.chars) && hash(row.sha256)); scopes.add(row.scope); });
    const ids = new Set(); let bytes = 0;
    value.input_artifacts.forEach(row => {
      keys(row, 'id execution_id path size sha256');
      requireValue(label(row.id) && label(row.execution_id) && !ids.has(row.id) && label(row.path, 1000) && integer(row.size) && row.size <= 4 * 1024 * 1024 && hash(row.sha256));
      requireValue(!/[\\:<>"|?*]/.test(row.path) && row.path.split('/').length <= 12 && row.path.split('/').every(part => !!part && part !== '.' && part !== '..' && !/[. ]$/.test(part)));
      ids.add(row.id); bytes += row.size;
    });
    requireValue(bytes <= 16 * 1024 * 1024);
  }
  return value as Summary;
}
function Hash({ value }: { value: string }) { return <details><summary>查看摘要哈希</summary><code style={{ overflowWrap: 'anywhere' }}>{value}</code></details>; }
function TextDigest({ title, value }: { title: string; value: Digest }) { return <div><p>{title}：{value.chars} 字符</p><Hash value={value.sha256} /></div>; }
const sources = { conversation_message: '会话消息', authorized_shared_brief: '授权共享摘要（消息 ID 仅关联原目标）', retrospective_snapshot: '复盘合成输入（不是一条真实会话消息）' };
function MessageRow({ row }: { row: Message }) { return <article className="task-card"><p>{sources[row.content_source]} · {row.chars} 字符</p><p>消息 {row.id ?? '无真实消息 ID'} · 序号 {row.sequence ?? '无真实序号'}</p><small>来源 {row.sender_kind === 'owner' ? 'Owner' : `Agent ${row.sender_id}`}</small><Hash value={row.sha256} /></article>; }

export function ContextSummary({ onClose, ...target }: Target & { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement), serial = useRef(0);
  const [row, setRow] = useState<Summary | null>(null), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function load() {
    const token = ++serial.current; setRow(null); setLoaded(false); setBusy(true); setError('');
    const base = `/${target.kind === 'model' ? 'runs' : 'executions'}/${target.run_id}`;
    try {
      const [run, receipt] = await Promise.all([api<unknown>(base), api<unknown>(`${base}/context-summary`)]);
      if (token !== serial.current) return;
      setRow(validate(receipt, run, target)); setLoaded(true);
    } catch (e) { if (token === serial.current) { setRow(null); setError(e instanceof Error ? e.message : '读取当次上下文失败'); } }
    finally { if (token === serial.current) setBusy(false); }
  }
  useEffect(() => { const node = dialog.current; node?.showModal(); void load(); return () => { serial.current++; node?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); }; }, [target.kind, target.run_id, target.agent_id]);
  return <dialog ref={dialog} aria-label="查看当次上下文" onCancel={e => { e.preventDefault(); onClose(); }}>
    <header><h2>查看当次上下文</h2><button onClick={onClose}>关闭当次上下文</button></header>
    <p style={{ overflowWrap: 'anywhere' }}>{target.kind === 'model' ? '模型调用' : 'CLI 执行'} · {target.run_id}</p>
    <p>仅供 Owner 只读观察，不会重新生成输入或向 Agent 发送资料。手动刷新获取已保存回执。</p>
    <button disabled={busy} onClick={() => void load()}>刷新当次上下文</button>
    {busy && <p role="status">读取当次上下文中…</p>}{error && <p className="error" role="alert">{error}</p>}
    {loaded && !row && <p role="status">没有保存当次上下文回执，实际输入未知。</p>}
    <SkillInputs kind={target.kind} run_id={target.run_id} agent_id={target.agent_id} />
    {row && <section aria-label="已准备上下文摘要">
      <p>已准备（prepared）：只证明入口保存了摘要，不证明供应商已接收、CLI 已启动或工具已执行。</p>
      <dl><dt>准备时间</dt><dd>{row.prepared_at}</dd><dt>Agent</dt><dd>{row.agent_id}</dd><dt>实例尝试 / 需求版本</dt><dd>第{row.attempt}次 / v{row.requirement_version}</dd><dt>当次模板</dt><dd>{row.template_id}</dd><dt>当次选用模型</dt><dd>{row.model}</dd></dl>
      <TextDigest title="当次指令" value={row.instructions} />
      <p className="muted">字符数与 SHA-256 对应当次授权输入原文，不是最终 HTTP 字节或完整 CLI 提示词的哈希；此摘要不展示指令正文与凭据。技能正文与版本请另查当次技能输入回执。</p>
      {row.kind === 'model' ? <section aria-label="消息来源摘要"><h3>消息来源摘要</h3><p>调用类别：{({ reply: '普通回复', planning: '协作规划', retrospective: '记忆复盘', peer_review: '成员评议' })[row.run_kind]} · 已记录 {row.messages.length} 条 / 上限100条</p><p>源序号：{row.source_sequence === 0 ? '合成输入，无会话源序号' : row.source_sequence} · {row.context_truncated ? '上下文已截断，只包含当时范围内最近100条' : '当次消息范围未截断'}</p><details><summary>查看消息来源明细（{row.messages.length}条）</summary>{row.messages.map((item, index) => <MessageRow key={index} row={item} />)}</details></section> : <>
        <section><h3>固定任务输入</h3><p>后端 {row.backend} · 任务 {row.task.id} · 当次需求 v{row.task.requirement_version}</p><TextDigest title="标题" value={row.task.title} /><TextDigest title="任务范围" value={row.task.scope} /><TextDigest title="验收要求" value={row.task.acceptance} /><MessageRow row={row.source_message} /></section>
        <section aria-label="当次记忆摘要"><h3>当次记忆摘要</h3>{!row.memories.length && <p>该次输入未加入非空版本记忆，不代表当前没有可用记忆。</p>}{row.memories.map(memory => <article className="task-card" key={memory.scope}><p>{memory.scope === 'agent' ? '个人记忆' : '项目记忆'} · {memory.scope_id} · v{memory.version}</p><TextDigest title="记忆" value={memory} /></article>)}</section>
        <section aria-label="当次输入成果摘要"><h3>当次输入成果摘要</h3><p>已记录 {row.input_artifacts.length} 项 / 上限100项；清单不证明工具已读取文件。</p>{row.input_artifacts.map(artifact => <article className="task-card" key={artifact.id}><p style={{ overflowWrap: 'anywhere' }}>{artifact.path} · {artifact.size} 字节</p><small>成果 {artifact.id} · 来源执行 {artifact.execution_id}</small><Hash value={artifact.sha256} /></article>)}</section>
      </>}
    </section>}
  </dialog>;
}
