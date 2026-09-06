import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Target = { run_id: string; agent_id: string; conversation_id: string; modelKind?: 'reply' | 'planning' | 'retrospective' | 'peer_review' };
type Reference = { scope: 'agent' | 'project'; scope_id: string; version: number; chars: number; sha256: string };
type Receipt = Omit<Target, 'modelKind'> & { kind: 'model'; attempt: number; requirement_version: number; memories: Reference[] };
const emptyHash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown, min = 0): v is number => Number.isSafeInteger(v) && (v as number) >= min;
const hash = (v: unknown) => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error('当次批准记忆的范围、版本或内容摘要不一致，未展示内容。'); }
function keys(v: unknown, names: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === names.split(' ').sort().join()); }
function validate(value: unknown, run: unknown, room: unknown, target: Target): Receipt | null {
  requireValue(object(run) && run.id === target.run_id && run.agent_id === target.agent_id && run.conversation_id === target.conversation_id && integer(run.attempt, 1) && integer(run.requirement_version, 1));
  requireValue(object(room) && room.id === target.conversation_id && ['dm', 'board', 'project'].includes(room.type as string));
  if (value === null) return null;
  requireValue(!target.modelKind || target.modelKind === 'reply');
  keys(value, 'kind run_id agent_id conversation_id attempt requirement_version memories');
  requireValue(value.kind === 'model' && value.run_id === target.run_id && value.agent_id === target.agent_id && value.conversation_id === target.conversation_id && value.attempt === run.attempt && value.requirement_version === run.requirement_version);
  const expected = room.type === 'dm' ? ['agent'] : ['agent', 'project'];
  requireValue(Array.isArray(value.memories) && value.memories.length === expected.length);
  for (const [index, reference] of value.memories.entries()) {
    keys(reference, 'scope scope_id version chars sha256');
    requireValue(reference.scope === expected[index] && reference.scope_id === (reference.scope === 'agent' ? target.agent_id : target.conversation_id) && integer(reference.version) && integer(reference.chars) && reference.chars <= 8000 && hash(reference.sha256));
    requireValue(reference.version !== 0 || reference.chars === 0);
    requireValue(reference.chars !== 0 || reference.sha256 === emptyHash);
  }
  return value as Receipt;
}

function FixedBody({ reference }: { reference: Reference }) {
  const serial = useRef(0);
  const [body, setBody] = useState<string | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => () => { serial.current++; }, []);
  async function load() {
    const token = ++serial.current; setBusy(true); setError(''); setBody(null);
    try {
      const rows = await api<unknown>(`/memories/${reference.scope}/${encodeURIComponent(reference.scope_id)}/history`);
      requireValue(Array.isArray(rows));
      const versions = new Set<number>();
      for (const row of rows) {
        keys(row, 'scope scope_id version content candidate_id target_version note created_at');
        requireValue(row.scope === reference.scope && row.scope_id === reference.scope_id && integer(row.version, 1) && !versions.has(row.version) && typeof row.content === 'string');
        versions.add(row.version);
      }
      const row = rows.find(row => row.version === reference.version);
      requireValue(row && typeof row.content === 'string');
      const content: string = row.content, characters = Array.from(content);
      requireValue(characters.length === reference.chars && !characters.some(c => c.length === 1 && /[\ud800-\udfff]/.test(c)));
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content)))).map(b => b.toString(16).padStart(2, '0')).join('');
      requireValue(digest === reference.sha256);
      if (token === serial.current) setBody(content);
    } catch (e) { if (token === serial.current) setError(e instanceof Error ? e.message : '固定记忆版本读取失败'); }
    finally { if (token === serial.current) setBusy(false); }
  }
  if (reference.version === 0) return <p>当次固定为空记忆（v0）。</p>;
  return <section aria-label={`固定${reference.scope === 'agent' ? '个人' : '项目'}记忆正文 v${reference.version}`}>
    <button type="button" disabled={busy} onClick={() => void load()}>查看当次{reference.scope === 'agent' ? '个人' : '项目'}记忆正文 · v{reference.version}</button>
    {busy && <p role="status">读取固定版本正文中…</p>}{error && <p role="alert" className="error">{error} 不会使用当前版本替代。</p>}
    {body !== null && (body === '' ? <p>当次固定版本正文为空。</p> : <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{body}</pre>)}
  </section>;
}

function MemorySnapshot(target: Target) {
  const serial = useRef(0);
  const [row, setRow] = useState<Receipt | null>(null), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function load() {
    const token = ++serial.current; setRow(null); setLoaded(false); setBusy(true); setError('');
    const base = `/runs/${encodeURIComponent(target.run_id)}`;
    try {
      const [run, receipt, room] = await Promise.all([api<unknown>(base), api<unknown>(`${base}/memories`), api<unknown>(`/conversations/${encodeURIComponent(target.conversation_id)}`)]);
      const value = validate(receipt, run, room, target);
      if (token === serial.current) { setRow(value); setLoaded(true); }
    } catch (e) { if (token === serial.current) setError(e instanceof Error ? e.message : '读取当次批准记忆失败'); }
    finally { if (token === serial.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { serial.current++; }; }, [target.run_id, target.agent_id, target.conversation_id, target.modelKind]);
  return <section aria-label="当次批准记忆输入">
    <p>只读当次固定的批准记忆范围、版本及摘要；历史正文按原版本核对，不使用当前记忆补造。已固定不证明供应商接收，也不扩大权限。</p>
    <button type="button" disabled={busy} onClick={() => void load()}>刷新当次批准记忆</button>
    {busy && <p role="status">读取当次批准记忆中…</p>}{error && <p role="alert" className="error">{error}</p>}
    {loaded && !row && <p role="status">没有保存当次批准记忆绑定，实际输入未知。</p>}
    {row && <><p>模型调用 {row.run_id} · Agent {row.agent_id} · 会话 {row.conversation_id} · 第 {row.attempt} 次 · 需求 v{row.requirement_version}</p>{row.memories.map(reference => <article className="task-card" key={`${reference.scope}:${reference.scope_id}:${reference.version}:${reference.sha256}`}><h3>{reference.scope === 'agent' ? '个人记忆' : '项目记忆'} · v{reference.version}</h3><p>{reference.scope_id} · {reference.chars} 字符</p><details><summary>查看固定记忆摘要哈希</summary><code style={{ overflowWrap: 'anywhere' }}>{reference.sha256}</code></details><FixedBody reference={reference} /></article>)}</>}
  </section>;
}

export function MemoryInputs(target: Target) {
  const [open, setOpen] = useState(false);
  return <details onToggle={e => setOpen(e.currentTarget.open)}><summary>查看当次批准记忆</summary>{open && <MemorySnapshot key={`${target.run_id}:${target.agent_id}:${target.conversation_id}:${target.modelKind ?? ''}`} {...target} />}</details>;
}
