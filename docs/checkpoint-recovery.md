# 任务级检查点恢复

本能力恢复固定任务批次的后续执行，不恢复 CLI 进程内存、终端会话或任意时刻的文件系统。F59 提供服务接口，F60 接通浏览器操作并通过本地受控流程验收；这不替代真实 CLI 与双 Docker Worker 验收。

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

## 浏览器操作与异常恢复（F60）

在项目群中打开“协作计划与恢复”→“批量执行当前项目任务”，查看原批次后选择“从本批检查点恢复”。页面展示任务标题、固定版本和原实例、保留成果证据、重试节点与阻塞原因。填写核查说明并明确确认后才能创建恢复批次。已核验回执可打开新批次，沿用本批停止和任务成果评审入口。

发送前将完整请求与检查点保存到当前标签页的 sessionStorage。响应丢失时保留原 request_id；重新打开优先只读查找及核验原回执，不自动发送替代请求。已受理编号、批次、任务/版本/attempt/前次实例与原授权全部核验后才释放存储。POST 已受理但后续 GET 失败不属于首次拒绝。坏存储或不一致回执保持锁定，不能通过重新授权绕过；同请求重试仅在没有已知恢复编号时由用户明确操作。

历史回执展示自己的固定快照。会话存储不承诺浏览器关闭后的持久化；服务端仍保留恢复历史。最近100条未查到原请求不代表未受理。

验证命令：在 frontend 运行 `npm run test:browser:checkpoint`，使用隔离临时数据及本地受控 runner/provider，不执行真实付费 CLI 或模型。覆盖批准成果保留、两次恢复、新批次停止、前置重新批准、响应丢失与重新打开、真实 POST 202 后 GET 400，以及错误批次/授权回执/坏存储。原流程用 `npm run test:browser` 回归。
