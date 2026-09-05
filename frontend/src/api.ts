export type Template = { id: string; name: string; department: string; source: string; instructions: string };
export type Agent = { id: string; template_id: string; name: string; model: string; skills: string[]; tools: string[]; enabled: boolean; is_default: boolean; created_at: string; updated_at: string };
export type AgentInput = Pick<Agent, 'name' | 'template_id' | 'model' | 'skills' | 'tools' | 'enabled'>;
export type Message = { id: string; sequence: number; conversation_id: string; sender_kind: 'owner' | 'agent'; sender_id: string | null; content: string; request_id: string; created_at: string };
export type Conversation = { id: string; type: 'dm' | 'board' | 'project'; title: string; archived: boolean; member_ids: string[]; updated_at: string; last_message: Message | null };
export type ModelSettingsValue = { enabled: boolean; model: string; base_url: string; api_key_env: string; max_output_tokens: number | null; timeout_seconds: number | null; rpm: number | null; max_concurrency: number | null; configured: boolean; credential_available: boolean };
export type CliSettingsValue = { enabled: boolean; executable: string; model: string; api_key_env: string; timeout_seconds: number; max_concurrency: number; configured: boolean; credential_available: boolean; executable_available: boolean; platform_supported: boolean };
export type CliProbeResult = { available: boolean; version: string | null; message: string };
export type ReplyRun = { id: string; conversation_id: string; agent_id: string; source_message_id: string; request_id: string; state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'unknown'; model: string | null; usage: { prompt_tokens: number | null; completion_tokens: number | null } | null; error: string | null; created_at: string; updated_at: string; reply_message_id: string | null };
export type ReplyRuntime = { error: string; active_requests: number; running: boolean };
export type TaskFields = { title: string; scope: string; acceptance: string; agent_id: string };
export type Task = TaskFields & { id: string; conversation_id: string; source_message_id: string; request_id: string; requirement_version: number; created_at: string; updated_at: string };
export type TaskRevision = TaskFields & { task_id: string; requirement_version: number; created_at: string };
export type TaskDraft = TaskFields & { id?: string; source_message_id: string; source_content: string; request_id: string; expected_version?: number; pending?: boolean; conflict?: boolean };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function api<T>(path: string, method = 'GET', body?: unknown, timeout = 15000): Promise<T> {
  const response = await fetch(`/api/workbench${path}`, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(timeout),
  });
  const result = await response.json();
  if (!response.ok) throw new ApiError(result.error || '请求失败', response.status);
  return result;
}
export type ExecutionRequest = { request_id: string; expected_version: number; reconciliation_note: string; previous_execution_id: string | null };
export type ExecutionArtifact = { id: string; execution_id: string; path: string; size: number; sha256: string };
export type ReviewRequest = { request_id: string; expected_version: number; decision: 'approved' | 'rejected'; note: string; artifact_ids: string[] };
export type OwnerReview = Omit<ReviewRequest, 'expected_version'> & { execution_id: string; requirement_version: number; reviewed_at: string };
export type TaskExecution = { id: string; task_id: string; agent_id: string; requirement_version: number; attempt: number; request_id: string; reconciliation_note: string; previous_execution_id: string | null; state: 'queued' | 'running' | 'stopping' | 'awaiting_review' | 'failed' | 'cancelled' | 'unknown' | 'superseded'; exit_code: number | null; summary: string | null; created_at: string; updated_at: string };
