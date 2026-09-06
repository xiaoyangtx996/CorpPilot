# 任务级检查点恢复

本能力恢复固定任务批次的后续执行，不恢复 CLI 进程内存、终端会话或任意时刻的文件系统。F59 提供服务接口；浏览器操作入口仍需后续集成，不能把本阶段标为完整用户流程交付。

检查点来自原项目执行批次。预览枚举固定需求版本、原执行 ID、复用成果、待重试节点和阻塞原因。已批准且仍有效的成果可复用；失败、取消、被拒绝，以及已有合法未知结果核查记录的实例可重新授权。活动实例、未核查未知、未验收结果、需求变更、替代实例或失效依赖阻塞恢复。原批次外的已批准依赖也须核对并固定，不重新执行。

恢复前须由 Owner 确认原执行与外部副作用已经核查，明确授权新 CLI 调用可能产生费用。客户端提交预览 fingerprint；服务在同一创建事务内重新核对，期间任何相关变化都要求重新预览。保留节点不重跑，重试节点创建新 execution_id/attempt 并引用前次执行；原记录不改为成功或失败。

新批次固定每项直接依赖的上游执行 ID，排队准入和运行中继续核查。上游出现新实例、需求变化或成果不再有效时阻塞，不能悄悄换成最新结果。重试节点之间仍须等待 Owner 批准新成果，不继承旧批次的自动交接豁免。

接口（均需现有本机 Owner 认证）：

- `GET /api/workbench/project-executions/{source_batch_id}/checkpoint`：只读预览与 fingerprint。
- `GET /api/workbench/project-executions/{source_batch_id}/checkpoint-recoveries`：最近100次恢复回执。
- `POST /api/workbench/project-executions/{source_batch_id}/checkpoint-recoveries`：提交 `request_id`、`checkpoint_fingerprint`、`reconciliation_note`、`confirm: true`，原子创建恢复批次。
- `GET /api/workbench/checkpoint-recoveries/{id}`：原检查点关联与新批次当前状态。
- 停止新批次仍用 `POST /api/workbench/project-executions/{batch_id}/stop` 和 `confirm: true`。

相同原批次及 request_id 的相同请求返回原回执；有冲突的请求拒绝。请求超时后核对同一请求回执，不应换 request_id 盲目重试。恢复生成独立批次，原 Goal 和原批次不改绑；停止原目标不代表新恢复批次已停止。
