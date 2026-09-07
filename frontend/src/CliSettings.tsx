import { useEffect, useRef, useState } from 'react';
import { api, type CliSettingsValue, type CliProbeResult, type ResourceAdmission } from './api';

type Fields = { resource_admission_enabled: boolean; host_reserve_memory_mb: string; local_worker_memory_mb: string; local_worker_cpus: string; enabled: boolean; engine: 'codex' | 'opencode'; backend: 'local' | 'docker'; executable: string; docker_executable: string; docker_image: string; docker_cpus: string; docker_memory_mb: string; docker_pids_limit: string; model: string; api_key_env: string; timeout_seconds: string; max_concurrency: string };
const numericFields = [
  { key: 'timeout_seconds', label: '执行超时（秒）', minimum: 1, maximum: 3600 },
  { key: 'max_concurrency', label: '最大并发数', minimum: 1, maximum: 16 },
  { key: 'host_reserve_memory_mb', label: '为宿主保留内存（MiB）', minimum: 0, maximum: 1048576 },
  { key: 'local_worker_memory_mb', label: '每个本地执行预留内存（MiB）', minimum: 128, maximum: 1048576 },
  { key: 'local_worker_cpus', label: '每个本地执行预留逻辑 CPU 数', minimum: 1, maximum: 256 },
  { key: 'docker_cpus', label: '每容器 CPU 核数', minimum: 1, maximum: 16 },
  { key: 'docker_memory_mb', label: '每容器内存（MiB）', minimum: 128, maximum: 32768 },
  { key: 'docker_pids_limit', label: '每容器进程数上限', minimum: 16, maximum: 1024 },
] as const;
function fields(value: CliSettingsValue): Fields {
  return { resource_admission_enabled: value.resource_admission_enabled ?? false, host_reserve_memory_mb: String(value.host_reserve_memory_mb ?? 1024), local_worker_memory_mb: String(value.local_worker_memory_mb ?? 1024), local_worker_cpus: String(value.local_worker_cpus ?? 1), enabled: value.enabled, executable: value.executable, model: value.model, api_key_env: value.api_key_env,
    engine: value.engine ?? 'codex', backend: value.backend ?? 'local', docker_executable: value.docker_executable ?? '', docker_image: value.docker_image ?? '',
    docker_cpus: String(value.docker_cpus ?? 1), docker_memory_mb: String(value.docker_memory_mb ?? 1024), docker_pids_limit: String(value.docker_pids_limit ?? 128),
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
  const [resources, setResources] = useState<ResourceAdmission | null>(null);
  const [resourceError, setResourceError] = useState('');
  const [resourceLoading, setResourceLoading] = useState(false);
  const resourceRequest = useRef(0);
  async function readResources() {
    const request = ++resourceRequest.current;
    setResourceLoading(true); setResourceError('');
    try {
      const value = await api<{ resource_admission: ResourceAdmission }>('/cli-runtime');
      if (request === resourceRequest.current) setResources(value.resource_admission);
    } catch (error) { if (request === resourceRequest.current) { setResources(null); setResourceError(failure(error)); } }
    finally { if (request === resourceRequest.current) setResourceLoading(false); }
  }
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
    void readResources();
    return () => { requests.current++; resourceRequest.current++; if (previous instanceof HTMLElement) previous.focus(); };
  }, []);
  function update<K extends keyof Fields>(key: K, value: Fields[K]) {
    setDraft(current => current ? { ...current, [key]: value } : current); setSuccess(false); setProbe(null);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft || !saved || loading || working.current || !dirty) return;
    working.current = true; setBusy('save'); setError(''); setSuccess(false); setProbe(null);
    try {
      for (const name of ['executable', 'docker_executable'] as const) {
        const executable = draft[name].trim();
        const active = name === (draft.backend === 'docker' ? 'docker_executable' : 'executable');
        if ((draft[name] !== fields(saved)[name] || active && draft.enabled) && executable && (!/^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+\\).+\.exe$/i.test(executable) || /[\r\n"<>|]/.test(executable))) throw new Error('CLI 路径须为不带引号或参数的绝对 .exe 文件路径。');
      }
      if (draft.docker_image && !/^(?:[a-z0-9][a-z0-9._:/-]*@)?sha256:[0-9a-f]{64}$/.test(draft.docker_image.trim())) throw new Error('镜像须为固定 sha256 ID 或 repo@sha256 摘要，不接受可变标签。');
      const previous = fields(saved);
      const patch: Record<string, string | number | boolean> = {};
      for (const key of Object.keys(draft) as (keyof Fields)[]) {
        if (draft[key] === previous[key]) continue;
        const numeric = numericFields.find(field => field.key === key);
        const value = draft[key];
        if (numeric) {
          const number = Number(value);
          if (!Number.isInteger(number) || number < numeric.minimum || number > numeric.maximum) throw new Error(`${numeric.label}须为 ${numeric.minimum}–${numeric.maximum} 的整数。`);
          patch[key] = number;
        } else patch[key] = typeof value === 'string' ? value.trim() : value;
      }
      const value = await api<CliSettingsValue>('/cli-settings', 'PATCH', patch);
      setSaved(value); setDraft(fields(value)); setSuccess(true);
      void readResources();
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
      <p className="muted">选择本地 CLI 或 Docker Worker。已有运行继续使用启动时的配置；调整配置后，此前已授权的排队任务可能继续启动。</p>
      {loading && <p role="status">正在读取已保存配置…</p>}
      {!loading && !saved && <p className="muted">尚未成功读取配置，暂不可编辑或保存。</p>}
      {saved && draft && <>
        <p className="settings-status">已保存配置：{saved.enabled ? '已启用' : '已禁用'} · {saved.engine === 'opencode' ? 'OpenCode Zen' : 'Codex'} · {saved.configured ? '字段完整' : '尚未完整配置'}</p>
        <p className="muted">运行平台：{saved.platform_supported ? '支持' : '不支持'} · 可执行文件：{saved.executable_available ? '已找到' : '未找到'}。文件存在不代表可以正常执行。</p>
        <p className="muted">密钥环境变量：{saved.credential_available ? '服务进程中已设置' : '服务进程中未设置'}。此状态不代表凭据有效。</p>
        <fieldset className="settings-fields" disabled={busy !== null || loading}><legend className="sr-only">CLI 配置字段</legend>
          <label className="check"><input type="checkbox" checked={draft.enabled} onChange={event => update('enabled', event.target.checked)} />启用 CLI 配置</label>
          <label className="check"><input type="checkbox" checked={draft.resource_admission_enabled} onChange={event => update('resource_admission_enabled', event.target.checked)} />启用本机资源准入</label>
          <p>资源不足或读取失败时暂停新执行，已有执行不因此被终止。本地预留值用于控制启动数量，不是进程内存硬限制或费用预算。Docker 使用每容器资源配置参与准入。</p>
          <label>CLI 工具<select value={draft.engine} onChange={event => update('engine', event.target.value as Fields['engine'])}><option value="codex">Codex</option><option value="opencode">OpenCode Zen</option></select></label>
          <label>执行后端<select value={draft.backend} onChange={event => update('backend', event.target.value as Fields['backend'])}><option value="local">本地 CLI（工作目录隔离）</option><option value="docker">Docker Worker（容器隔离）</option></select></label>
          {draft.engine === 'opencode' && <p>OpenCode Zen 当前仅支持本地执行，允许使用内置文件读写工具，禁用 Shell、网络工具和子代理。每个 Agent 使用独立工作目录与配置；目录隔离不是操作系统沙箱。切换工具不会自动更改路径、模型或密钥环境变量。</p>}
          {draft.engine === 'opencode' && draft.backend === 'docker' && <p className="error">OpenCode Docker Worker 尚未接入。此组合仅可保存为禁用配置；启用时请先选择本地 CLI。</p>}
          {draft.backend === 'local' ? <label>CLI 可执行文件路径<input required={draft.enabled} maxLength={2048} autoComplete="off" spellCheck={false} value={draft.executable} placeholder={draft.engine === 'opencode' ? '例如 C:\\Tools\\opencode.exe' : '例如 C:\\Tools\\codex.exe'} onChange={event => update('executable', event.target.value)} /><small>填写可信程序的绝对 .exe 路径，不含命令参数或引号。本地后端不是容器沙箱。</small></label> : <>
            <label>Docker 可执行文件路径<input required={draft.enabled} maxLength={2048} autoComplete="off" spellCheck={false} value={draft.docker_executable} placeholder="例如 C:\Tools\docker.exe" onChange={event => update('docker_executable', event.target.value)} /></label>
            <label>固定 Docker 镜像<input required={draft.enabled} maxLength={200} spellCheck={false} value={draft.docker_image} placeholder="sha256:… 或 repo@sha256:…" onChange={event => update('docker_image', event.target.value)} /><small>须预先构建包含 CLI 工具链的 Linux 镜像，并使用本地镜像 ID 或固定摘要。检查不会拉取或构建镜像。</small></label>
          </>}
          <label>模型标识<input required={draft.enabled} maxLength={200} value={draft.model} placeholder={draft.engine === 'opencode' ? '例如 opencode/big-pickle' : '填写可用的 OpenAI 模型标识'} onChange={event => update('model', event.target.value)} /><small>{draft.engine === 'opencode' ? '使用官方 Zen 模型标识，例如 opencode/big-pickle；通过 OpenCode 客户端执行，不复用聊天的 API 基础地址。' : '当前 Codex 适配器使用 OpenAI API，不复用聊天的 API 基础地址。'}</small></label>
          <label>密钥环境变量名<input required={draft.enabled} pattern="[A-Za-z_][A-Za-z0-9_]*" maxLength={128} autoComplete="off" spellCheck={false} value={draft.api_key_env} placeholder={draft.engine === 'opencode' ? '例如 OPENCODE_API_KEY（不是密钥值）' : '例如 OPENAI_API_KEY（不是密钥值）'} onChange={event => update('api_key_env', event.target.value)} /><small>仅填写变量名。请在启动服务前设置该环境变量；修改环境后需重启服务。</small></label>
          {numericFields.filter(field => draft.backend === 'docker' ? !field.key.startsWith('local_worker_') : !field.key.startsWith('docker_')).map(field => <label key={field.key}>{field.label}<input type="number" required min={field.minimum} max={field.maximum} step={1} value={draft[field.key]} onChange={event => update(field.key, event.target.value)} /><small>范围 {field.minimum}–{field.maximum}，仅接受整数。</small></label>)}
        </fieldset>
        <button type="button" disabled={busy !== null || loading || dirty || !(saved.backend === 'docker' ? saved.docker_executable && saved.docker_image : saved.executable) || !saved.platform_supported} onClick={() => void checkVersion()}>{busy === 'probe' ? '正在检查…' : saved.backend === 'docker' ? '检查本地 Docker 与固定镜像' : '检查已保存 CLI 版本'}</button>
        <p className="muted">{saved.backend === 'docker' ? '检查只读取本地 Docker 服务和固定 Linux 镜像状态，不启动容器、不拉取镜像。' : '检查只运行已保存程序的 --version。'}不调用模型、不验证模型账号。{dirty ? '有未保存修改，请先保存，再手动检查。' : '检查成功不代表任务已调度或执行。'}</p>
      </>}
      {probe && <p className={probe.available ? 'settings-status' : 'error'} role="status">{probe.available ? '检查成功' : '检查未通过'}{probe.version ? `：${probe.version}` : ''}。{probe.message}</p>}
      <section aria-label="本机资源状态"><h3>本机资源状态</h3><button type="button" disabled={resourceLoading} onClick={() => void readResources()}>{resourceLoading ? '读取资源中…' : '刷新本机资源'}</button>
        {resources && <><p>已保存准入：{resources.enabled === null ? '未知' : resources.enabled ? '启用' : '关闭'} · 可用内存 {resources.available_memory_mb ?? '未知'} MiB · 逻辑 CPU {resources.cpu_count ?? '未知'}</p><p>活动预约：{resources.reserved_memory_mb} MiB / {resources.reserved_cpus} CPU。{resources.message}</p></>}
        {resourceError && <p role="alert" className="error">资源状态读取失败：{resourceError}</p>}
      </section>
      {error && <p className="error" role="alert">{error}</p>}
      {!loading && !saved && <button type="button" onClick={() => void load()}>重试读取配置</button>}
      <p className="settings-status" role="status">{success ? 'CLI 配置已保存，后续调度使用新配置。' : dirty ? '有未保存的修改' : ''}</p>
      <footer><button type="button" disabled={busy !== null} onClick={onClose}>{dirty ? '取消修改' : '关闭'}</button><button className="primary" disabled={!saved || loading || busy !== null || !dirty}>{busy === 'save' ? '保存中…' : '保存配置'}</button></footer>
    </form>
  </dialog>;
}
