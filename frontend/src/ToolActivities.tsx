import { useEffect, useRef, useState } from 'react';
import { api } from './api';

const detailFields = {
  command_execution: ['command', 'aggregated_output'], file_change: ['changes'],
  mcp_tool_call: ['server', 'tool', 'arguments', 'result', 'error'],
  collab_tool_call: ['tool', 'sender_thread_id', 'receiver_thread_ids', 'prompt', 'agents_states'],
  web_search: ['query', 'action'],
};
type ToolType = keyof typeof detailFields;
type Event = { sequence: number; phase: 'started' | 'updated' | 'completed'; type: ToolType; item_sha256: string; status: 'in_progress' | 'completed' | 'failed' | 'declined' | null; exit_code: number | null; details: Record<string, { chars: number; sha256: string }> };
const reasons = { exited: '进程返回', cancelled: '已请求取消', timeout: '超时', output_limit: '输出达到上限', start_failed: '启动失败', unknown: '退出情况未知', protocol_error: '完成协议未确认' };
type Receipt = { execution_id: string; agent_id: string; attempt: number; requirement_version: number; recorded_at: string; payload: { version: 1; source: 'codex_jsonl'; observation: 'after_process'; events: Event[]; invalid_lines: number; unknown_items: number; dropped_events: number; output_limited: boolean; process_reason: keyof typeof reasons } };
const types = { command_execution: '命令执行', file_change: '文件变更', mcp_tool_call: 'MCP 工具', collab_tool_call: 'CLI 内部协作', web_search: '网页搜索' };
const phases = { started: '开始通知', updated: '更新通知', completed: '完成通知' };
const statuses = { in_progress: '进行中', completed: '完成', failed: '失败', declined: '已拒绝' };
const names: Record<string, string> = { command: '命令', aggregated_output: '聚合输出', changes: '文件变更清单', server: '服务器标识', tool: '工具标识', arguments: '参数', result: '结果', error: '错误', sender_thread_id: '发送线程标识', receiver_thread_ids: '接收线程标识', prompt: '提示内容', agents_states: '内部成员状态', query: '查询', action: '搜索动作' };
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => Number.isSafeInteger(v) && (v as number) >= min && (v as number) <= max;
const hash = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
function requireValue(ok: unknown): asserts ok { if (!ok) throw Error('工具活动回执关联或元数据格式不一致，未展示内容。'); }
function keys(v: unknown, expected: string): asserts v is Record<string, unknown> { requireValue(object(v) && Object.keys(v).sort().join() === expected.split(' ').sort().join()); }
function validate(raw: unknown, run: unknown, executionId: string, agentId: string): Receipt | null {
  requireValue(object(run) && run.id === executionId && run.agent_id === agentId && integer(run.attempt, 1) && integer(run.requirement_version, 1));
  if (raw === null) return null;
  keys(raw, 'execution_id agent_id attempt requirement_version recorded_at payload');
  requireValue(raw.execution_id === executionId && raw.agent_id === agentId && raw.attempt === run.attempt && raw.requirement_version === run.requirement_version && typeof raw.recorded_at === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(raw.recorded_at) && Number.isFinite(Date.parse(raw.recorded_at)));
  const p = raw.payload;
  keys(p, 'version source observation events invalid_lines unknown_items dropped_events output_limited process_reason');
  requireValue(p.version === 1 && p.source === 'codex_jsonl' && p.observation === 'after_process' && Array.isArray(p.events) && p.events.length <= 500 && integer(p.invalid_lines) && integer(p.unknown_items) && integer(p.dropped_events) && typeof p.output_limited === 'boolean' && typeof p.process_reason === 'string' && Object.hasOwn(reasons, p.process_reason));
  let previous = 0;
  for (const row of p.events) {
    keys(row, 'sequence phase type item_sha256 status exit_code details');
    requireValue(integer(row.sequence, previous + 1) && typeof row.phase === 'string' && Object.hasOwn(phases, row.phase) && typeof row.type === 'string' && Object.hasOwn(detailFields, row.type) && hash(row.item_sha256) && object(row.details));
    const type = row.type as ToolType;
    requireValue(type === 'web_search' ? row.status === null : typeof row.status === 'string' && (['in_progress', 'completed', 'failed'].includes(row.status) || type === 'command_execution' && row.status === 'declined'));
    requireValue(row.exit_code === null || type === 'command_execution' && integer(row.exit_code, -(2 ** 31), 2 ** 31 - 1));
    for (const [field, value] of Object.entries(row.details)) {
      requireValue(detailFields[type].includes(field)); keys(value, 'chars sha256');
      requireValue(integer(value.chars) && hash(value.sha256));
    }
    previous = row.sequence;
  }
  return raw as Receipt;
}

export function ToolActivities({ executionId, agentId, onClose }: { executionId: string; agentId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), opener = useRef(document.activeElement), serial = useRef(0);
  const [receipt, setReceipt] = useState<Receipt | null>(null), [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function load() {
    const token = ++serial.current; setReceipt(null); setLoaded(false); setBusy(true); setError('');
    try {
      const base = `/executions/${executionId}`;
      const [run, value] = await Promise.all([api<unknown>(base), api<unknown>(`${base}/tool-activities`)]);
      if (token !== serial.current) return;
      setReceipt(validate(value, run, executionId, agentId)); setLoaded(true);
    } catch (e) { if (token === serial.current) { setReceipt(null); setError(e instanceof Error ? e.message : '读取工具活动失败'); } }
    finally { if (token === serial.current) setBusy(false); }
  }
  useEffect(() => {
    const node = dialog.current; node?.showModal(); void load();
    return () => { serial.current++; node?.close(); if (opener.current instanceof HTMLElement) opener.current.focus(); };
  }, [executionId, agentId]);
  const payload = receipt?.payload;
  return <dialog ref={dialog} aria-label="查看工具活动" onCancel={e => { e.preventDefault(); onClose(); }}>
    <header><h2>查看工具活动</h2><button onClick={onClose}>关闭工具活动</button></header>
    <p style={{ overflowWrap: 'anywhere' }}>CLI 执行 {executionId}</p>
    <p>这是进程返回后的只读观察（after_process），不是实时工具状态。手动刷新只读取已保存回执，不启动工具。</p>
    <button disabled={busy} onClick={() => void load()}>刷新工具活动</button>
    {busy && <p role="status">读取工具活动中…</p>}{error && <p className="error" role="alert">{error}</p>}
    {loaded && !receipt && <p role="status">没有保存工具活动回执，实际工具活动未知。</p>}
    {receipt && payload && <section aria-label="工具活动摘要">
      <dl><dt>记录时间</dt><dd>{receipt.recorded_at}</dd><dt>Agent</dt><dd>{receipt.agent_id}</dd><dt>实例尝试 / 需求版本</dt><dd>第{receipt.attempt}次 / v{receipt.requirement_version}</dd><dt>观察阶段</dt><dd>进程返回后（after_process）</dd><dt>进程返回原因</dt><dd>{reasons[payload.process_reason]}</dd><dt>已记录事件</dt><dd>{payload.events.length} / 500 条</dd><dt>无效输出行</dt><dd>{payload.invalid_lines}</dd><dt>未知事件或类型</dt><dd>{payload.unknown_items}</dd><dt>超限未保留事件</dt><dd>{payload.dropped_events}</dd><dt>输出是否受限</dt><dd>{payload.output_limited ? '是' : '否'}</dd></dl>
      <p>事件数量不是工具调用次数；完成通知不等于工具成功。</p>
      <details><summary>工具观察说明</summary>
        <p>记录可能不完整；以上计数为零也不是完整性证明。同一工具可产生多条事件。事件阶段与工具状态分别记录，完成通知不证明进程树已退出或副作用已核查。</p>
        <p>CLI 内部协作是该 CLI 自身的协作事件，不代表 CorpPilot Worker 创建或扩容。这里只显示内容字符数和哈希，不显示命令、输出、参数、路径或线程原文。</p>
      </details>
      {!payload.events.length && <p>未记录可识别的工具事件，不证明没有使用工具。</p>}
      <details><summary>查看工具事件明细（{payload.events.length}条）</summary>
        {payload.events.map(row => <article className="task-card" key={row.sequence} aria-label={`工具事件第${row.sequence}行`}>
          <h3>第{row.sequence}行 · {types[row.type]}</h3>
          <dl><dt>事件阶段</dt><dd>{phases[row.phase]}</dd><dt>工具状态</dt><dd>{row.status === null ? '未提供' : statuses[row.status]}</dd><dt>工具退出码</dt><dd>{row.exit_code === null ? '未提供' : row.exit_code}</dd></dl>
          <details><summary>查看工具内容摘要</summary><p>同一 item 标识哈希相同，可用于关联其开始、更新和完成事件。</p><code style={{ overflowWrap: 'anywhere' }}>{row.item_sha256}</code>
            {Object.entries(row.details).map(([field, value]) => <div key={field}><p>{names[field]}：{value.chars} 字符</p><code style={{ overflowWrap: 'anywhere' }}>{value.sha256}</code></div>)}
            <p className="muted">文本按原文计算；结构字段按规范化 JSON 计算摘要。哈希不验证工具实际执行结果。</p>
          </details>
        </article>)}
      </details>
    </section>}
  </dialog>;
}
