import { useEffect, useRef, useState } from 'react';
import { api, type CliSettingsValue, type CliProbeResult } from './api';

type Fields = { enabled: boolean; executable: string; model: string; api_key_env: string; timeout_seconds: string; max_concurrency: string };
const numericFields = [
  { key: 'timeout_seconds', label: '执行超时（秒）', maximum: 3600 },
  { key: 'max_concurrency', label: '最大并发数', maximum: 16 },
] as const;
function fields(value: CliSettingsValue): Fields {
  return { enabled: value.enabled, executable: value.executable, model: value.model, api_key_env: value.api_key_env,
    timeout_seconds: String(value.timeout_seconds), max_concurrency: String(value.max_concurrency) };
}
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试';

export function CliSettings({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const requests = useRef(0);
  const working = useRef(false);
  const [saved, setSaved] = useState<CliSettingsValue | null>(null);
  const [draft, setDraft] = useState<Fields | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<'save' | 'probe' | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [probe, setProbe] = useState<CliProbeResult | null>(null);
  const dirty = saved && draft ? JSON.stringify(fields(saved)) !== JSON.stringify(draft) : false;
  async function load() {
    const request = ++requests.current;
    setLoading(true); setError('');
    try {
      const value = await api<CliSettingsValue>('/cli-settings');
      if (request !== requests.current) return;
      setSaved(value); setDraft(fields(value));
    } catch (error) { if (request === requests.current) setError(failure(error)); }
    finally { if (request === requests.current) setLoading(false); }
  }
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    void load();
    return () => { requests.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  function update<K extends keyof Fields>(key: K, value: Fields[K]) {
    setDraft(current => current ? { ...current, [key]: value } : current); setSuccess(false); setProbe(null);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft || !saved || loading || working.current || !dirty) return;
    working.current = true; setBusy('save'); setError(''); setSuccess(false); setProbe(null);
    try {
      const executable = draft.executable.trim();
      if ((draft.executable !== saved.executable || draft.enabled) && executable && (!/^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\).+\.exe$/i.test(executable) || /[\r\n"<>|]/.test(executable))) throw new Error('CLI 路径须为不带引号或参数的绝对 .exe 文件路径。');
      const previous = fields(saved);
      const patch: Record<string, string | number | boolean> = {};
      for (const key of Object.keys(draft) as (keyof Fields)[]) {
        if (draft[key] === previous[key]) continue;
        const numeric = numericFields.find(field => field.key === key);
        const value = draft[key];
        if (numeric) {
          const number = Number(value);
          if (!Number.isInteger(number) || number < 1 || number > numeric.maximum) throw new Error(`${numeric.label}须为 1–${numeric.maximum} 的整数。`);
          patch[key] = number;
        } else patch[key] = typeof value === 'string' ? value.trim() : value;
      }
      const value = await api<CliSettingsValue>('/cli-settings', 'PATCH', patch);
      setSaved(value); setDraft(fields(value)); setSuccess(true);
    } catch (error) { setError(failure(error)); }
    finally { working.current = false; setBusy(null); }
  }
  async function checkVersion() {
    if (!saved || loading || dirty || working.current) return;
    working.current = true; setBusy('probe'); setError(''); setProbe(null); setSuccess(false);
    try { setProbe(await api<CliProbeResult>('/cli-settings/probe', 'POST', {}, 30000)); }
    catch (error) { setError(failure(error)); }
    finally { working.current = false; setBusy(null); }
  }
  return <dialog ref={dialog} className="model-settings task-dialog" aria-labelledby="cli-settings-title" aria-busy={loading || busy !== null} onCancel={event => { event.preventDefault(); if (!working.current) onClose(); }}>
    <form onSubmit={save}><header><h2 id="cli-settings-title">CLI 设置</h2><button type="button" disabled={busy !== null} onClick={onClose}>关闭</button></header>
      <p className="muted">配置本机可信的 Codex CLI。使用工作目录隔离，不是 Docker 沙箱。保存不会执行 CLI、调用模型或启动任务。</p>
      {loading && <p role="status">正在读取已保存配置…</p>}
      {!loading && !saved && <p className="muted">尚未成功读取配置，暂不可编辑或保存。</p>}
      {saved && draft && <>
        <p className="settings-status">已保存配置：{saved.enabled ? '已启用' : '已禁用'} · {saved.configured ? '字段完整' : '尚未完整配置'}</p>
        <p className="muted">运行平台：{saved.platform_supported ? '支持' : '不支持'} · 可执行文件：{saved.executable_available ? '已找到' : '未找到'}。文件存在不代表可以正常执行。</p>
        <p className="muted">密钥环境变量：{saved.credential_available ? '服务进程中已设置' : '服务进程中未设置'}。此状态不代表凭据有效。</p>
        <fieldset className="settings-fields" disabled={busy !== null || loading}><legend className="sr-only">CLI 配置字段</legend>
          <label className="check"><input type="checkbox" checked={draft.enabled} onChange={event => update('enabled', event.target.checked)} />启用 CLI 配置</label>
          <label>CLI 可执行文件路径<input required={draft.enabled} maxLength={2048} autoComplete="off" spellCheck={false} value={draft.executable} placeholder="例如 C:\Tools\codex.exe" onChange={event => update('executable', event.target.value)} /><small>填写本机可信程序的绝对路径，不要填写命令参数或引号；保存时服务端会校验路径。</small></label>
          <label>模型标识<input required={draft.enabled} maxLength={200} value={draft.model} placeholder="填写可用的 OpenAI 模型标识" onChange={event => update('model', event.target.value)} /><small>当前 Codex 适配器使用 OpenAI API，不复用聊天的 API 基础地址。</small></label>
          <label>密钥环境变量名<input required={draft.enabled} pattern="[A-Za-z_][A-Za-z0-9_]*" maxLength={128} autoComplete="off" spellCheck={false} value={draft.api_key_env} placeholder="例如 OPENAI_API_KEY（不是密钥值）" onChange={event => update('api_key_env', event.target.value)} /><small>仅填写变量名。请在启动服务前设置该环境变量；修改环境后需重启服务。</small></label>
          {numericFields.map(field => <label key={field.key}>{field.label}<input type="number" required min={1} max={field.maximum} step={1} value={draft[field.key]} onChange={event => update(field.key, event.target.value)} /><small>范围 1–{field.maximum}，仅接受整数。</small></label>)}
        </fieldset>
        <button type="button" disabled={busy !== null || loading || dirty || !saved.executable || !saved.platform_supported} onClick={() => void checkVersion()}>{busy === 'probe' ? '正在检查版本…' : '检查已保存 CLI 版本'}</button>
        <p className="muted">检查会运行已保存程序的 --version，不调用模型，不验证模型账号。{dirty ? '有未保存修改，请先保存，再手动检查。' : '检查成功不代表任务已调度或执行。'}</p>
      </>}
      {probe && <p className={probe.available ? 'settings-status' : 'error'} role="status">{probe.available ? '版本检查成功' : '版本检查未通过'}{probe.version ? `：${probe.version}` : ''}。{probe.message}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!loading && !saved && <button type="button" onClick={() => void load()}>重试读取配置</button>}
      <p className="settings-status" role="status">{success ? 'CLI 配置已保存，未执行程序或调用模型。' : dirty ? '有未保存的修改' : ''}</p>
      <footer><button type="button" disabled={busy !== null} onClick={onClose}>{dirty ? '取消修改' : '关闭'}</button><button className="primary" disabled={!saved || loading || busy !== null || !dirty}>{busy === 'save' ? '保存中…' : '保存配置'}</button></footer>
    </form>
  </dialog>;
}
