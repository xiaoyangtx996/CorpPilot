import type { TaskExecution } from './api';

export function ExecutionUsage({ run }: { run: TaskExecution }) {
  return <p className="muted">{run.usage
    ? `CLI 单轮回执 token：输入 ${run.usage.input_tokens ?? '未知'} / 输出 ${run.usage.output_tokens ?? '未知'} / 缓存输入 ${run.usage.cached_input_tokens ?? '未知'}。缓存输入包含在输入中；这是已观测回执，不是完整账单。`
    : 'CLI 用量未取得有效回执，不能视为零费用。'}</p>;
}
