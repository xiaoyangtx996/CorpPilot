# Agent 工作台交付台账

目标：完整浏览器工作台、真实 CLI 与双 Docker Worker 隔离、持久身份/聊天/记忆、受控组队及 Tauri 迁移规划。
实施分支：`codex/corppilot-agent-workbench`；每个功能测试和审查后立即提交、推送、远端回读。

## 基线（2026-09-06）

- 源码：`6b5e7041d236c6dcfe25b44e0e9c627c2c18805e`，开始时工作树干净。
- 原有 Python 3.11 缺 pytest；在 `.venv` 安装 pytest 9.1.1。
- `.venv\Scripts\python.exe -m pytest tests/ -q`：51 passed，1 skipped。
- Docker 29.7.2 客户端存在，但默认 daemon 管道不可连接；真实容器验收尚未执行。
- PM 范围审查 Pass；运行与产品总验收未通过，不能把阶段成果等同完整交付。

## F01：CLI 失败不再触发成功完成

- 实现：runtime_reliability 后端代理；审查与集成：主代理。
- Claude CLI 非零退出、缺失、超时及异常走失败回调；成功回调保留兼容性。
- 串行失败记录错误并阻塞任务；并行任一失败禁止整体推进，全部分支结算后释放占用。
- 测试：`python -m unittest discover -s tests -p test_runtime_failures.py -v`，3 tests 通过。
- 主代理回归：`.venv\Scripts\python.exe -m pytest tests/ -q`，54 passed，1 skipped，8 subtests passed。
- Ponytail/code review：Pass；只修共同回调根因与调用方，没有新增依赖或调度框架。
- 验证边界：此项使用故障注入，未声称真实 CLI/容器验收；限流、停止、Run 版本及消息幂等属于后续功能。
- 提交标识：`fix(runtime): F01 prevent CLI failures from completing workflows`。
- 提交：`77d7e9e`。推送失败：GitHub 返回 403，认证账号 `suiyue1990` 无 `xiaoyangtx996/CorpPilot` 写权限。
- 状态：本地测试及审查通过，远端交付阻塞；已请求用户补充仓库写权限，未创建 PR 或更改远端。

## F02：旧测试的运行文件隔离

- 实现：runtime_reliability；审查及复验：主代理，Pass。
- pytest 将原有测试及明确列举的产物模块重定向至临时目录，复制角色/流程/技能种子。
- 原有测试三次运行（含倒序）均 54 passed、1 skipped；前后 39 个文件路径和 SHA256 一致。
- 集成在途身份测试后的主代理全量回归：56 passed、1 skipped、8 subtests passed。
- 范围限制：隔离适用于 pytest 入口；没有修改业务模块的正式数据目录。
- 提交标识：`test: F02 isolate legacy suite runtime files`；远端仍待 F01 所述写权限恢复。
