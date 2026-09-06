import { useEffect, useRef, useState } from 'react';
import { api, type Agent, type BudgetConfig, type BudgetSettings, type BudgetReservation } from './api';

type Pending = { payload: BudgetConfig; previous_revision: number };
type Draft = { enabled: boolean; total_micro_usd: string; model_reserve_micro_usd: string; cli_reserve_micro_usd: string };
const key = 'corppilot.budget-settings-pending.v1';
const numeric = ['total_micro_usd', 'model_reserve_micro_usd', 'cli_reserve_micro_usd'] as const;
const labels = { total_micro_usd: '总额度（USD）', model_reserve_micro_usd: '每次模型请求预留（USD）', cli_reserve_micro_usd: '每次 CLI 执行预留（USD）' };
const failure = (e: unknown) => e instanceof Error ? e.message : '请求失败';
function money(value: number) {
  const amount = BigInt(value), absolute = amount < 0n ? -amount : amount;
  const fraction = (absolute % 1000000n).toString().padStart(6, '0').replace(/0+$/, '');
  return `${amount < 0n ? '-' : ''}${absolute / 1000000n}${fraction ? '.' + fraction : ''}`;
}
function micro(value: string, positive: boolean) {
  if (!/^\d+(?:\.\d{1,6})?$/.test(value) || value.length > 32) throw Error('USD 金额须为普通十进制，最多6位小数。');
  const [whole, fraction = ''] = value.split('.');
  const result = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, '0'));
  if (result > 1000000000000n || result < (positive ? 1n : 0n)) throw Error('总额度允许0；每次预留须大于0；每项最多1000000 USD。');
  return Number(result);
}
function config(value: BudgetConfig) { return { enabled: value.enabled, total_micro_usd: value.total_micro_usd, model_reserve_micro_usd: value.model_reserve_micro_usd, cli_reserve_micro_usd: value.cli_reserve_micro_usd }; }
function validConfig(value: BudgetConfig) {
  return value && Object.keys(value).sort().join() === 'cli_reserve_micro_usd,enabled,model_reserve_micro_usd,total_micro_usd' && typeof value.enabled === 'boolean' && numeric.every(k => Number.isSafeInteger(value[k]) && value[k] >= (k === 'total_micro_usd' ? 0 : 1) && value[k] <= 1000000000000);
}
function draftOf(value: BudgetConfig): Draft { return { enabled: value.enabled, total_micro_usd: money(value.total_micro_usd), model_reserve_micro_usd: money(value.model_reserve_micro_usd), cli_reserve_micro_usd: money(value.cli_reserve_micro_usd) }; }
function validSettings(value: BudgetSettings) {
  if (!value || !validConfig(config(value)) || value.currency !== 'USD' || !Number.isSafeInteger(value.revision) || value.revision < 0
    || !['reserved_micro_usd', 'unsettled_reserved_micro_usd', 'settled_micro_usd', 'committed_micro_usd', 'reservation_count', 'settlement_count'].every(key => {
      const amount = value[key as keyof BudgetSettings]; return typeof amount === 'number' && Number.isSafeInteger(amount) && amount >= 0;
    }) || value.unsettled_reserved_micro_usd > value.reserved_micro_usd
    || value.committed_micro_usd !== value.unsettled_reserved_micro_usd + value.settled_micro_usd
    || !Number.isSafeInteger(value.available_micro_usd) || value.available_micro_usd !== value.total_micro_usd - value.committed_micro_usd) throw Error('预算读取回执格式或金额不一致');
}

export function BudgetPanel({ agent, onClose }: { agent?: Agent; onClose: () => void }) {
  const opener = useRef(document.activeElement);
  const dialog = useRef<HTMLDialogElement>(null), serial = useRef(0), writing = useRef(false), pendingRef = useRef<Pending | null>(null);
  const [current, setCurrent] = useState<BudgetSettings | null>(null), [records, setRecords] = useState<BudgetReservation[]>([]), [draft, setDraft] = useState<Draft | null>(null);
  const [pending, setPending] = useState<Pending | null>(null), [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(true), [error, setError] = useState(''), [storageError, setStorageError] = useState(''), [message, setMessage] = useState('');
  const title = agent ? 'Agent 预算预留' : '预算与预留';
  async function read(token: number) {
    const [value, rows] = await Promise.all([api<BudgetSettings>('/budget-settings'), api<BudgetReservation[]>(agent ? `/agents/${agent.id}/budget-reservations` : '/budget-reservations')]);
    validSettings(value);
    if (!Array.isArray(rows) || rows.length > 100 || rows.some(row => !row || !['model', 'cli'].includes(row.kind) || typeof row.run_id !== 'string' || typeof row.agent_id !== 'string' || agent && row.agent_id !== agent.id || !Number.isSafeInteger(row.amount_micro_usd) || row.amount_micro_usd < 1 || row.amount_micro_usd > 1000000000000 || !Number.isSafeInteger(row.attempt) || row.attempt < 1 || !Number.isSafeInteger(row.requirement_version) || row.requirement_version < 1 || !Number.isSafeInteger(row.config_revision) || row.config_revision < 1 || typeof row.created_at !== 'string')) throw Error('预留记录读取回执不一致');
    if (token !== serial.current) return;
    setCurrent(value); setRecords(rows);
    const sent = pendingRef.current;
    if (sent) {
      const matches = JSON.stringify(config(value)) === JSON.stringify(config(sent.payload));
      if (matches && value.revision >= sent.previous_revision + 1) {
        sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setDraft(draftOf(value));
        setMessage('当前预算配置已通过独立读取确认；这不是实际账单核销。');
      } else setMessage('当前配置与期望或所需版本不一致；原保存是否曾受理仍不确定，请保留核对或明确采用当前配置。');
    } else setDraft(draftOf(value));
  }
  async function load() {
    if (writing.current || storageError) return;
    const token = ++serial.current; setBusy(true); setError(''); setCurrent(null); setRecords([]); setConfirmed(false); setMessage('');
    try { await read(token); }
    catch (e) { if (token === serial.current) setError(failure(e)); }
    finally { if (token === serial.current) setBusy(false); }
  }
  useEffect(() => {
    const token = ++serial.current, node = dialog.current;
    node?.showModal();
    try {
      if (!agent) {
        const raw = sessionStorage.getItem(key);
        if (raw !== null) {
          const saved: Pending = JSON.parse(raw);
          if (!saved || Object.keys(saved).sort().join() !== 'payload,previous_revision' || !validConfig(saved.payload) || !Number.isSafeInteger(saved.previous_revision) || saved.previous_revision < 0 || saved.previous_revision >= Number.MAX_SAFE_INTEGER) throw Error('原预算请求损坏');
          pendingRef.current = saved; setPending(saved); setDraft(draftOf(saved.payload));
        }
      }
      void load();
    } catch (e) { if (token === serial.current) { setStorageError(`${failure(e)}；原存储已保留，不能新保存。`); setBusy(false); } }
    return () => { serial.current++; node?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); };
  }, [agent?.id]);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (agent || busy || writing.current || storageError || pendingRef.current || !current || !draft || !confirmed) return;
    let sent: Pending;
    try {
      const payload: BudgetConfig = { enabled: draft.enabled, total_micro_usd: micro(draft.total_micro_usd, false), model_reserve_micro_usd: micro(draft.model_reserve_micro_usd, true), cli_reserve_micro_usd: micro(draft.cli_reserve_micro_usd, true) };
      sent = { payload, previous_revision: current.revision };
      sessionStorage.setItem(key, JSON.stringify(sent)); pendingRef.current = sent; setPending(sent);
    } catch (e) { setError(failure(e)); return; }
    const token = ++serial.current; writing.current = true; setBusy(true); setConfirmed(false); setError(''); setMessage(''); setCurrent(null); setRecords([]);
    try {
      await api<BudgetSettings>('/budget-settings', 'PATCH', sent.payload);
    } catch (e) { if (token === serial.current) setError(`${failure(e)}。保存结果只通过读取核对，不自动重发。`); }
    finally {
      writing.current = false;
      if (token === serial.current) {
        try { await read(token); }
        catch (e) { if (token === serial.current) setError(`${failure(e)}。原完整请求已保留。`); }
        if (token === serial.current) setBusy(false);
      }
    }
  }
  function adoptCurrent() {
    if (agent || busy || storageError || !pendingRef.current || !current) return;
    try {
      sessionStorage.removeItem(key); pendingRef.current = null; setPending(null); setDraft(draftOf(current)); setConfirmed(false); setError('');
      setMessage('已明确采用刚读取的当前配置；这不证明原保存从未受理，也没有再次写入配置。');
    } catch { setStorageError('无法结束核对，原请求继续保留。'); }
  }
  const dirty = !!draft && !!current && JSON.stringify(draft) !== JSON.stringify(draftOf(current));
  return <dialog ref={dialog} className="task-dialog model-settings" aria-labelledby="budget-title" onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}>
    <header><h2 id="budget-title">{title}</h2><button disabled={busy} onClick={onClose}>关闭</button></header>
    <p>这里管理启动额度与持久预留。预留不是已花金额或实际账单，不是 CLI 总费用硬上限；失败、停止和重启不会自动返还。预算占用按未核销预留与 Owner 声明费用计算，声明不代表系统验证过的账单。</p>
    {agent && <p>只读观察：{agent.name}。以下额度汇总为全局，记录仅属于当前 Agent；不会修改配置或调用模型。</p>}
    {error && <p className="error" role="alert">{error}</p>}{storageError && <p className="error" role="alert">{storageError}</p>}
    <button disabled={busy || !!storageError} onClick={() => void load()}>{pending ? '只读核对预算配置' : '刷新预算与预留'}</button>
    {busy && <p role="status">正在读取或核对预算…</p>}
    {current && <section aria-label="当前预算快照"><h3>当前预算快照</h3><p>准入：{current.enabled ? '启用' : '关闭'} · 配置版本 {current.revision}</p><dl><dt>总额度</dt><dd aria-label="总额度">{money(current.total_micro_usd)} USD</dd><dt>总预留</dt><dd aria-label="总预留">{money(current.reserved_micro_usd)} USD</dd><dt>未核销预留</dt><dd aria-label="未核销预留">{money(current.unsettled_reserved_micro_usd)} USD</dd><dt>Owner 声明费用</dt><dd aria-label="Owner 声明费用">{money(current.settled_micro_usd)} USD</dd><dt>当前预算占用</dt><dd aria-label="当前预算占用">{money(current.committed_micro_usd)} USD</dd><dt>可用额度</dt><dd aria-label="可用额度">{money(current.available_micro_usd)} USD</dd><dt>预留记录数</dt><dd aria-label="预留记录数">{current.reservation_count}</dd></dl>{current.available_micro_usd < 0 && <p>额度低于当前占用；不会削减历史记录，启用时新启动继续受余额限制。</p>}</section>}
    {message && <p role="status">{message}</p>}
    {!agent && pending && <section><h3>原预算配置待核对</h3><p>原配置版本：{pending.previous_revision} · 期望准入：{pending.payload.enabled ? '启用' : '关闭'}</p>{numeric.map(k => <p key={k}>期望{labels[k]}：{money(pending.payload[k])}</p>)}<p>原请求尚未明确核对，不能再次保存或自动覆盖当前配置。</p>{current && <button disabled={busy || !!storageError} onClick={adoptCurrent}>采用当前配置并结束核对</button>}</section>}
    {!agent && draft && <form onSubmit={save}><fieldset disabled={busy || !!pending || !!storageError || !current}>
      <label className="check"><input type="checkbox" checked={draft.enabled} onChange={e => { setDraft({ ...draft, enabled: e.target.checked }); setConfirmed(false); setMessage(''); }} />启用预算准入</label>
      {numeric.map(k => <label key={k}>{labels[k]}<input aria-label={labels[k]} inputMode="decimal" required maxLength={32} value={draft[k]} onChange={e => { setDraft({ ...draft, [k]: e.target.value }); setConfirmed(false); setMessage(''); }} /><small>最多6位小数，上限1000000 USD；{k === 'total_micro_usd' ? '启用准入时，允许0阻止新启动。' : '必须大于0。'}</small></label>)}
      <label className="check"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />我确认修改可能放行现有排队任务，关闭准入将取消后续预算门禁</label>
      <button disabled={!dirty || !confirmed}>确认保存预算配置</button>
    </fieldset></form>}
    <section><h3>{agent ? '当前 Agent 的预留记录' : '全局预留记录'}（最近100条）</h3>{!busy && current && !records.length && <p>本次读取范围内没有预留记录；这不代表历史费用为零。</p>}{records.map(row => <article className="task-card" key={`${row.kind}:${row.run_id}`}><h4>{row.kind === 'model' ? '模型请求预留' : 'CLI 执行预留'} · {money(row.amount_micro_usd)} USD</h4><p>配置版本 {row.config_revision} · 第{row.attempt}次 · 需求 v{row.requirement_version}</p><small>实例 {row.run_id} · Agent {row.agent_id} · {row.created_at}</small></article>)}</section>
  </dialog>;
}
