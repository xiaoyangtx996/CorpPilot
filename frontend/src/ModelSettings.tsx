import { useEffect, useRef, useState } from 'react';
import { api, type ModelSettingsValue } from './api';

const numericFields = [
  { key: 'max_output_tokens', label: '最大输出 token 数', maximum: 131072, suggestion: '建议 4096' },
  { key: 'timeout_seconds', label: '请求超时（秒）', maximum: 300, suggestion: '建议 60' },
  { key: 'rpm', label: '每分钟请求上限（RPM）', maximum: 600, suggestion: '建议 30' },
  { key: 'max_concurrency', label: '最大并发请求数', maximum: 16, suggestion: '建议 1' },
] as const;
type Fields = { enabled: boolean; model: string; base_url: string; api_key_env: string; max_output_tokens: string; timeout_seconds: string; rpm: string; max_concurrency: string };
function fields(value: ModelSettingsValue): Fields {
  return { enabled: value.enabled, model: value.model, base_url: value.base_url, api_key_env: value.api_key_env,
    max_output_tokens: value.max_output_tokens?.toString() ?? '', timeout_seconds: value.timeout_seconds?.toString() ?? '',
    rpm: value.rpm?.toString() ?? '', max_concurrency: value.max_concurrency?.toString() ?? '' };
}
const failure = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试';

export function ModelSettings({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const requests = useRef(0);
  const saving = useRef(false);
  const [saved, setSaved] = useState<ModelSettingsValue | null>(null);
  const [draft, setDraft] = useState<Fields | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const dirty = saved && draft ? JSON.stringify(fields(saved)) !== JSON.stringify(draft) : false;
  async function load() {
    const request = ++requests.current;
    setLoading(true); setError('');
    try {
      const value = await api<ModelSettingsValue>('/model-settings');
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
    setDraft(current => current ? { ...current, [key]: value } : current); setSuccess(false);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft || !saved || loading || saving.current || !dirty) return;
    saving.current = true; setBusy(true); setError(''); setSuccess(false);
    try {
      const previous = fields(saved);
      const patch: Record<string, string | number | boolean> = {};
      for (const key of Object.keys(draft) as (keyof Fields)[]) {
        if (draft[key] === previous[key]) continue;
        const value = draft[key];
        if (typeof value === 'string' && !value.trim()) throw new Error('已修改的配置字段不能为空；若需暂停使用，请取消启用。');
        patch[key] = numericFields.some(field => field.key === key) ? Number(value) : value;
      }
      const value = await api<ModelSettingsValue>('/model-settings', 'PATCH', patch);
      setSaved(value); setDraft(fields(value)); setSuccess(true);
    } catch (error) { setError(failure(error)); }
    finally { saving.current = false; setBusy(false); }
  }
  return <dialog ref={dialog} className="model-settings" aria-labelledby="model-settings-title" aria-busy={loading || busy} onCancel={event => { event.preventDefault(); if (!saving.current) onClose(); }}>
    <form onSubmit={save}><header><h2 id="model-settings-title">模型设置</h2><button type="button" disabled={busy} onClick={onClose}>关闭</button></header>
      <p className="muted">保存配置不会调用模型。并发与 RPM 限制供执行层使用，此处不代表任务已执行，也未进行连接测试。</p>
      {loading && <p role="status">正在读取已保存配置…</p>}
      {!loading && !saved && <p className="muted">尚未成功读取配置，暂不可编辑或保存。</p>}
      {saved && draft && <>
        <p className="settings-status">已保存配置：{saved.enabled ? '已启用' : '已禁用'} · {saved.configured ? '字段完整' : '尚未完整配置'}</p>
        <p className="muted">密钥环境变量：{saved.api_key_env || '未指定'} · {saved.credential_available ? '服务进程中已设置' : '服务进程中未设置'}。此状态不代表凭据有效或 API 可连接。</p>
        <fieldset className="settings-fields" disabled={busy || loading}><legend className="sr-only">模型配置字段</legend>
          <label className="check"><input type="checkbox" checked={draft.enabled} onChange={event => update('enabled', event.target.checked)} />启用模型配置</label>
          <label>模型标识<input required={draft.enabled} maxLength={200} value={draft.model} placeholder="填写服务商提供的模型标识" onChange={event => update('model', event.target.value)} /></label>
          <label>API 基础地址<input type="url" required={draft.enabled} maxLength={2048} value={draft.base_url} placeholder="https://api.example.com/v1" onChange={event => update('base_url', event.target.value)} /><small>公网地址使用 HTTPS；本机 HTTP 仅支持 localhost 或 127.0.0.1。地址中不可包含密钥、凭据、查询参数或片段。</small></label>
          <label>密钥环境变量名<input required={draft.enabled} pattern="[A-Za-z_][A-Za-z0-9_]*" maxLength={128} autoComplete="off" spellCheck={false} value={draft.api_key_env} placeholder="例如 CORPPILOT_API_KEY（不是密钥值）" onChange={event => update('api_key_env', event.target.value)} /><small>仅填写变量名。密钥从启动工作台服务的进程环境读取；请在启动服务前设置该变量，修改系统环境后需重启服务。</small></label>
          {numericFields.map(field => <label key={field.key}>{field.label}<input type="number" required={draft.enabled} min={1} max={field.maximum} step={1} placeholder={field.suggestion} value={draft[field.key]} onChange={event => update(field.key, event.target.value)} /><small>范围 1–{field.maximum}，仅接受整数；建议值不会自动保存。</small></label>)}
        </fieldset>
      </>}
      {error && <p className="error" role="alert">{error}</p>}
      {!loading && !saved && <button type="button" onClick={() => void load()}>重试读取配置</button>}
      <p className="settings-status" role="status">{success ? '配置已保存，未发起模型调用。' : dirty ? '有未保存的修改' : ''}</p>
      <footer><button type="button" disabled={busy} onClick={onClose}>{dirty ? '取消修改' : '关闭'}</button><button className="primary" disabled={!saved || loading || busy || !dirty}>{busy ? '保存中…' : '保存配置'}</button></footer>
    </form>
  </dialog>;
}
