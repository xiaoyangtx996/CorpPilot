export type Template = { id: string; name: string; department: string; source: string; instructions: string };
export type Agent = { id: string; template_id: string; name: string; model: string; skills: string[]; tools: string[]; enabled: boolean; is_default: boolean; created_at: string; updated_at: string };
export type AgentInput = Pick<Agent, 'name' | 'template_id' | 'model' | 'skills' | 'tools' | 'enabled'>;

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/workbench${path}`, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求失败');
  return result;
}
