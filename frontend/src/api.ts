export type Template = { id: string; name: string; department: string; source: string; instructions: string };
export type Agent = { id: string; template_id: string; name: string; model: string; skills: string[]; tools: string[]; enabled: boolean; is_default: boolean; created_at: string; updated_at: string };
export type AgentInput = Pick<Agent, 'name' | 'template_id' | 'model' | 'skills' | 'tools' | 'enabled'>;
export type Message = { id: string; sequence: number; conversation_id: string; sender_kind: 'owner' | 'agent'; sender_id: string | null; content: string; request_id: string; created_at: string };
export type Conversation = { id: string; type: 'dm' | 'board' | 'project'; title: string; archived: boolean; member_ids: string[]; updated_at: string; last_message: Message | null };
export type ModelSettingsValue = { enabled: boolean; model: string; base_url: string; api_key_env: string; max_output_tokens: number | null; timeout_seconds: number | null; rpm: number | null; max_concurrency: number | null; configured: boolean; credential_available: boolean };
export type CliSettingsValue = { enabled: boolean; backend: 'local' | 'docker'; executable: string; docker_executable: string; docker_image: string; docker_cpus: number; docker_memory_mb: number; docker_pids_limit: number; model: string; api_key_env: string; timeout_seconds: number; max_concurrency: number; configured: boolean; credential_available: boolean; executable_available: boolean; platform_supported: boolean };
export type WorkerStatus = { execution_id: string; backend: 'local' | 'docker' | 'unbound'; managed: boolean; verified: boolean; state: null | { status: string; running: boolean; exit_code: number | null; container_id: string | null }; can_stop: boolean; message: string };
export type CliProbeResult = { available: boolean; version: string | null; message: string };
export type ReplyRun = { id: string; conversation_id: string; agent_id: string; source_message_id: string; request_id: string; state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'unknown'; model: string | null; usage: { prompt_tokens: number | null; completion_tokens: number | null } | null; error: string | null; created_at: string; updated_at: string; reply_message_id: string | null };
export type ModelUnknownRun = ReplyRun & { attempt: number; requirement_version: number; kind?: 'reply' | 'planning' | 'retrospective'; scope?: 'agent' | 'project'; scope_id?: string };
export type ModelReconciliationRequest = { request_id: string; attempt: number; requirement_version: number; local_request_stopped: true; provider_effects_checked: true; note: string };
export type ModelReconciliationRecord = ModelReconciliationRequest & { run_id: string; reconciled_at: string };
export type ReplyRuntime = { error: string; active_requests: number; running: boolean };
export type TaskFields = { title: string; scope: string; acceptance: string; agent_id: string };
export type Task = TaskFields & { id: string; conversation_id: string; source_message_id: string; request_id: string; requirement_version: number; created_at: string; updated_at: string };
export type TaskRevision = TaskFields & { task_id: string; requirement_version: number; created_at: string; dependency_task_ids: string[] };
export type TaskDependencyStatus = { task_id: string; requirement_version: number; task_ids: string[]; ready: boolean; blocked_reason: string | null };
export type TaskDraft = TaskFields & { id?: string; source_message_id: string; source_content: string; request_id: string; expected_version?: number; pending?: boolean; conflict?: boolean };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}
export class AccessExpiredError extends Error {}

const accessKey = 'corppilot.owner-access.v1';
let accessToken = '';
export const accessExpiredEvent = 'corppilot-access-expired';
export function saveAccessToken(value: string) {
  if (!/^[A-Za-z0-9_-]{43}$/.test(value)) throw new Error('访问口令格式无效，请使用本次服务启动时提供的口令');
  try { sessionStorage.setItem(accessKey, value); }
  catch { throw new Error('无法保存当前标签页的访问口令，请允许会话存储后重试'); }
  accessToken = value;
}
export function restoreAccessToken(): boolean {
  accessToken = '';
  const fragment = new URLSearchParams(location.hash.slice(1));
  const provided = fragment.get('access_token');
  if (fragment.has('access_token')) {
    history.replaceState(null, '', location.pathname + location.search);
    saveAccessToken(provided ?? '');
    return true;
  }
  let saved: string | null;
  try { saved = sessionStorage.getItem(accessKey); }
  catch { throw new Error('无法读取当前标签页的访问口令，请允许会话存储后重试'); }
  if (!saved) { accessToken = ''; return false; }
  saveAccessToken(saved);
  return true;
}
async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const sentToken = accessToken;
  const headers = new Headers(init.headers);
  if (sentToken) headers.set('Authorization', `Bearer ${sentToken}`);
  const response = await fetch(`/api/workbench${path}`, { ...init, headers, redirect: 'error' });
  if (response.status === 401) {
    if (accessToken === sentToken) {
      accessToken = '';
      try { sessionStorage.removeItem(accessKey); } catch { /* In-memory access is revoked even if storage fails. */ }
      window.dispatchEvent(new Event(accessExpiredEvent));
    }
    throw new AccessExpiredError('访问口令已失效，请使用本次服务启动时的新口令');
  }
  return response;
}

export async function api<T>(path: string, method = 'GET', body?: unknown, timeout = 15000): Promise<T> {
  const response = await authorizedFetch(path, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(timeout),
  });
  const result = await response.json();
  if (!response.ok) throw new ApiError(result.error || '请求失败', response.status);
  return result;
}
export async function downloadArtifact(id: string, filename: string) {
  const response = await authorizedFetch(`/artifacts/${encodeURIComponent(id)}/download`, { signal: AbortSignal.timeout(60000) });
  if (!response.ok) throw new ApiError('成果下载失败，请刷新成果状态后重试', response.status);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.split(/[\\/]/).at(-1) || 'artifact';
  document.body.append(link);
  try { link.click(); } finally { link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
}
export type ExecutionRequest = { request_id: string; expected_version: number; reconciliation_note: string; previous_execution_id: string | null };
export type ExecutionArtifact = { id: string; execution_id: string; path: string; size: number; sha256: string };
export type ReviewRequest = { request_id: string; expected_version: number; decision: 'approved' | 'rejected'; note: string; artifact_ids: string[] };
export type OwnerReview = Omit<ReviewRequest, 'expected_version'> & { execution_id: string; requirement_version: number; reviewed_at: string };
export type TaskExecution = { id: string; task_id: string; agent_id: string; requirement_version: number; attempt: number; request_id: string; reconciliation_note: string; previous_execution_id: string | null; state: 'queued' | 'running' | 'stopping' | 'awaiting_review' | 'failed' | 'cancelled' | 'unknown' | 'superseded'; exit_code: number | null; summary: string | null; created_at: string; updated_at: string };
export type MemoryDocument = { scope: 'agent' | 'project'; scope_id: string; version: number; content: string };
export type MemoryRevision = MemoryDocument & { candidate_id: string | null; target_version: number | null; note: string; created_at: string };
export type MemoryDecision = { candidate_id: string; request_id: string; decision: 'approved' | 'rejected'; note: string; result_version: number; decided_at: string };
export type MemoryCandidate = { id: string; scope: 'agent' | 'project'; scope_id: string; expected_version: number; source_execution_id: string; source_task_id: string; source_requirement_version: number; content: string; created_at: string; decision: MemoryDecision | null };
export type ExecutionReconciliationRequest = { request_id: string; attempt: number; requirement_version: number; process_stopped: true; external_effects_checked: true; note: string };
export type ExecutionReconciliationRecord = ExecutionReconciliationRequest & { execution_id: string; reconciled_at: string };
export type CollaborationTask = { key: string; title: string; scope: string; acceptance: string; agent_id: string; depends_on: string[] };
export type CollaborationPlan = { request_id: string; source_message_id: string; title: string; shared_brief: string; coordinator_id: string; tasks: CollaborationTask[] };
export type CollaborationReceipt = { id: string; source_conversation_id: string; source_message_id: string; request_id: string; project_conversation_id: string; shared_message_id: string; coordinator_id: string; member_ids: string[]; task_ids: Record<string, string>; created_at: string; approved_plan: CollaborationPlan };
export type ProjectExecutionRequest = { request_id: string; tasks: { task_id: string; expected_version: number; previous_execution_id: string | null; reconciliation_note: string }[] };
export type ProjectExecutionReceipt = { id: string; collaboration_id: string; project_conversation_id: string; request_id: string; request_payload: ProjectExecutionRequest; tasks: { task_id: string; execution_id: string; request_id: string }[]; created_at: string };
export type ProjectExecutionDetail = ProjectExecutionReceipt & { items: { task: Task; execution: TaskExecution; review: OwnerReview | null; dependencies: TaskDependencyStatus; latest_execution_id: string | null }[] };
export type PlanningRequest = { agent_id: string; source_message_id: string; request_id: string; candidate_ids: string[] };
export type PlanningRun = ReplyRun & { candidate_snapshot: Pick<Agent, 'id' | 'name' | 'template_id' | 'skills'>[]; proposal: Pick<CollaborationPlan, 'title' | 'shared_brief' | 'tasks'> | null; request_payload: PlanningRequest };
export type RetrospectiveRequest = { request_id: string; expected_version: number; source_execution_id: string; artifact_ids: string[] };
export type RetrospectiveRun = ReplyRun & { scope: 'agent' | 'project'; scope_id: string; request_payload: RetrospectiveRequest; candidate_id: string | null; selected_artifacts: ExecutionArtifact[]; evidence_artifact_ids: string[] };
