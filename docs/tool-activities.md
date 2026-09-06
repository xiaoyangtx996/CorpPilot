# 工具活动回执（F67）

Owner 认证后的 `GET /api/workbench/executions/{id}/tool-activities` 返回该 CLI 实例的持久观察回执，旧实例或未保存回执返回 null，不存在的实例返回404。没有公开写入接口，也不会因读取而启动工具、重建上下文或扫描工作目录。浏览器入口由后续独立功能接入。

## 观察边界

本轮复用现有 local/Docker adapter 在进程返回后取得的 stdout，不是实时事件流。事件形状以 [Codex 0.153.3 exec_events.rs](https://github.com/openai/codex/blob/rust-v0.153.3/codex-rs/exec/src/exec_events.rs) 为依据：识别命令、文件修改、MCP、搜索和 CLI 内部协作五类工具，以及 started/updated/completed 阶段。命令的 declined 状态独立保留；搜索没有 status，不推导一个成功状态。

回执证明 CLI 输出曾报告这些元数据，不能证明宿主或远端副作用，更不能替代进程树/Docker停止核查、成果验收或费用核销。同一item的多条更新不是多次工具调用。CLI内部协作线程不等于CorpPilot长期身份或调度出来的Worker。空事件不表示未执行工具；没有回执保持未知。运行中和服务在保存前崩溃时不能提供已持久化的实时工具日志。

## 协议与最小存储

外层包含execution_id、agent_id、attempt、requirement_version、recorded_at和payload。绑定从实际task_executions读取，记录时间是保存时间，不是每个工具的发生时间。

payload包含version=1、source=codex_jsonl、observation=after_process、process_reason、output_limited，以及events和invalid_lines/unknown_items/dropped_events计数。每条事件包含原stdout行号sequence、phase、type、item_sha256、CLI报告的status、命令exit_code和details。非命令exit_code及搜索status均为null。

details只保留白名单字段的字符数与SHA-256。文本按原UTF-8计算，结构字段按键排序且无多余空格的JSON计算；不保存原始命令、输出、路径、搜索词、MCP参数/结果、线程ID、协作prompt或身份凭据。item_id只保留hash用于关联同一工具的更新。hash不是对外部行为真实性的签名。

最多检查4MiB stdout，保留500条有效工具事件；超过数量记录dropped_events，输出受限记录output_limited。坏JSON、坏UTF-8、重复键、非法值或无效工具行计入invalid_lines，其他有效行仍保留；未知事件/工具类别计入unknown_items。已知非工具消息和推理内容不保存。计数只涵盖已取得的字节，不能统计丢失的尾部。原CLI成功/用量协议解析保持独立，不因保留了部分工具事件就判定执行成功。

## 故障与恢复

新增一张不可变tool_activities表，禁止更新、删除和replace。首次保存仅允许实际running/stopping实例；相同元数据幂等重放可以发生在终态，冲突拒绝。没有为历史任务补造日志。离线备份/恢复包含该表。

控制器在成果采集前保存回执。活动或用量首次保存失败时不采集成果，保留同一已完成Future及预留，仅重试写入，不重新启动CLI。保存恢复后仍沿原保守失败结果处理：已确认退出码会转failed（停止中的实例按原规则cancelled），退出未知保持unknown，不自动恢复为成功。若先保存成功而成果采集失败，观察回执仍可查询。保存前进程崩溃的缺失证据不能靠重试工具来补齐。

验证入口：`.venv\Scripts\python.exe -m pytest tests/test_workbench_tool_activities.py tests/test_workbench_tool_activity_integration.py tests/test_workbench_cli_usage.py tests/test_workbench_cli.py -q`。协议和受控runner测试分别验证解析及接线，不能替代真实付费CLI/两个Docker Worker验收。
