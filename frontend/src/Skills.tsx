import { useEffect, useRef, useState } from 'react';
import { api } from './api';

export type Skill = { id: string; name: string; source: string; content: string; version: string; bytes: number };
type Target = { kind: 'model' | 'cli'; run_id: string; agent_id: string };
type Receipt = Target & { attempt: number; requirement_version: number; skills: Skill[] };
const builtins = ['coding', 'demo-generator'];
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error('技能内容、摘要或当次运行关联不一致，未展示内容。'); }
function keys(v: unknown, names: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === names.split(' ').sort().join()); }
const positive = (v: unknown) => Number.isSafeInteger(v) && (v as number) >= 1;
const trim = (v: string) => v.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, '');

export async function checkSkills(value: unknown, catalog = false): Promise<Skill[]> {
  requireValue(Array.isArray(value) && value.length <= 16);
  const ids = new Set<string>(); let chars = 0;
  for (const item of value) {
    keys(item, 'id name source content version bytes');
    requireValue(typeof item.id === 'string' && builtins.includes(item.id) && !ids.has(item.id) && item.source === `skills/${item.id}.md` && typeof item.name === 'string' && typeof item.content === 'string' && !!trim(item.content) && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(item.content) && typeof item.version === 'string' && /^[0-9a-f]{64}$/.test(item.version));
    // Match Python's Unicode character budget without rewriting the UTF-8 input.
    const count = Array.from(item.content).length;
    requireValue(count <= 16000 && !Array.from(item.content).some(c => c.length === 1 && /[\ud800-\udfff]/.test(c)));
    const bytes = new TextEncoder().encode(item.content);
    requireValue(Number.isSafeInteger(item.bytes) && item.bytes === bytes.length && bytes.length <= 64000);
    const name = item.content.split(/\r\n|[\n\r\u0085\u2028\u2029]/).find(line => line.startsWith('# ') && !!trim(line.slice(2)));
    requireValue(item.name === (name ? trim(name.slice(2)) : item.id));
    const version = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))).map(b => b.toString(16).padStart(2, '0')).join('');
    requireValue(version === item.version); ids.add(item.id); chars += count;
  }
  requireValue(chars <= 32000 && (!catalog || ids.size === builtins.length && builtins.every(id => ids.has(id))));
  return value as Skill[];
}

export function SkillText({ skill, historical = false }: { skill: Skill; historical?: boolean }) {
  return <details><summary>{historical ? '查看固定技能正文' : '查看技能正文'} · {skill.id}</summary>
    <p>{skill.name} · 来源 {skill.source} · {skill.bytes} UTF-8 字节</p>
    <p style={{ overflowWrap: 'anywhere' }}>内容版本 SHA-256：{skill.version}</p>
    <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{skill.content}</pre>
  </details>;
}

function SkillSnapshot(target: Target) {
  const serial = useRef(0);
  const [row, setRow] = useState<Receipt | null>(null), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function load() {
    const token = ++serial.current; setRow(null); setLoaded(false); setBusy(true); setError('');
    const base = `/${target.kind === 'model' ? 'runs' : 'executions'}/${encodeURIComponent(target.run_id)}`;
    try {
      const [run, receipt] = await Promise.all([api<unknown>(base), api<unknown>(`${base}/skills`)]);
      requireValue(object(run) && run.id === target.run_id && run.agent_id === target.agent_id && positive(run.attempt) && positive(run.requirement_version));
      if (receipt !== null) {
        keys(receipt, 'kind run_id agent_id attempt requirement_version skills');
        requireValue(receipt.kind === target.kind && receipt.run_id === target.run_id && receipt.agent_id === target.agent_id && receipt.attempt === run.attempt && receipt.requirement_version === run.requirement_version);
        await checkSkills(receipt.skills);
      }
      if (token === serial.current) { setRow(receipt as Receipt | null); setLoaded(true); }
    } catch (e) { if (token === serial.current) setError(e instanceof Error ? e.message : '读取当次技能输入失败'); }
    finally { if (token === serial.current) setBusy(false); }
  }
  useEffect(() => { void load(); return () => { serial.current++; }; }, [target.kind, target.run_id, target.agent_id]);
  return <section aria-label="当次固定技能输入">
    <p>只读取该次保存的参考资料，不使用当前技能目录补造历史；固定输入不证明供应商已接收、工具已执行或权限扩大。</p>
    <button type="button" disabled={busy} onClick={() => void load()}>刷新当次技能输入</button>
    {busy && <p role="status">读取当次技能输入中…</p>}{error && <p className="error" role="alert">{error}</p>}
    {loaded && !row && <p role="status">没有保存当次技能输入，无法确认当时内容。</p>}
    {row && <><p>{row.kind === 'model' ? '模型调用' : 'CLI 执行'} · {row.run_id} · Agent {row.agent_id} · 第 {row.attempt} 次 · 需求 v{row.requirement_version}</p>{row.skills.length ? row.skills.map(skill => <SkillText key={skill.id} skill={skill} historical />) : <p role="status">当次已固定为空技能选择。</p>}</>}
  </section>;
}

export function SkillInputs(target: Target) {
  const [open, setOpen] = useState(false);
  return <details onToggle={e => setOpen(e.currentTarget.open)}><summary>查看当次技能输入</summary>{open && <SkillSnapshot key={`${target.kind}:${target.run_id}:${target.agent_id}`} {...target} />}</details>;
}
