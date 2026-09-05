# 浏览器工作台验收记录

整体结论：未完成。按用户完整目标验收，以下已测子功能不能代替 CLI、双 Docker Worker、记忆与最终交付。

## 当前基线

- F19 主代理全量205 passed、8 subtests passed（35.15秒），独立CLI/进程树20 passed。真实Windows子孙进程、控制器崩溃清理通过；本机Codex 0.153.3仅运行--version探针成功，没有付费模型任务或双Docker证据。
- F18 主代理全量185 passed、8 subtests passed（32.53秒）；独立新增执行记录测试17项，覆盖版本/attempt、幂等、权限、停止请求和未知恢复。仅验证持久化/内部回调，未以注入退出码替代真实CLI/容器测试。
- F16 提交 `51059a0`：主代理 `.venv\Scripts\python.exe -m pytest tests/ -q`，168 passed、8 subtests passed，30.35 秒。
- F16 独立 QA：Tasks、Task API、Runs 共 15 passed；创建幂等、并发版本冲突、历史不可覆盖、会话权限及 HTTP 重启恢复通过。
- F15 已验收浏览器的指定成员回复入口、配置禁用反馈、真实本机连接失败后的 unknown、重复查询和刷新恢复；供应商正向回复尚未验收。测试模型配置已恢复禁用。
- 验收数据目录 `H:\item\CorpPilot-test-evidence-20260906\browser-state` 独立于默认用户目录；服务地址 `http://127.0.0.1:7892/`。

## F17 任务界面验收范围

F17 本轮通过：独立 PM/React/Ponytail 静态审查 Pass，独立 Tasks/API 7 passed。
主代理 TypeScript/Vite build 通过（35 模块，`index-BZ7m4w-k.js`）。

| 检查 | 直接证据与结果 |
|---|---|
| 页面及渲染 | IAB `127.0.0.1:7892`，标题 CorpPilot 工作台，任务卡/消息/成员内容及截图正常，无框架错误覆盖层 |
| 创建/编辑/历史 | `QA 任务版本验收` 从消息创建 v1，编辑 v2，历史逐版显示原范围，刷新后 v2 恢复 |
| 并发编辑 | 表单基于 v2，另一次真实 HTTP PATCH 写 v3；旧版保存收到409，草稿保留且字段锁定；明确读取 v3 后保存草稿为 v4 |
| 响应丢失 | 外部 QA 脚本在真实数据库提交后断开一次连接，日志记录任务 `239f85bd-6c32-4a8f-a5fa-04e9fbc4da09`；浏览器显示未知、锁字段，刷新同标签页后同键重试仅一张 `QA 断线同键任务` |
| 无隐式执行 | HTTP回读两张测试任务（分别v4/v1），Run仍只有原有一条unknown，没有新增模型请求 |
| 归档/校验/键盘 | 归档时编辑禁用、历史可读；恢复后可编辑；空标题/验收禁止保存，Escape关闭历史 |
| 窄屏 | 390×844，DOM scrollWidth=390，任务表单宽352，截图显示字段与底部按钮可达 |

正常服务已恢复，注入脚本不进入产品代码；模型配置仍禁用。测试脚本路径
`H:\item\CorpPilot-test-evidence-20260906\drop_task_response.py`，仅用于上述独立测试数据目录。
跨会话迟到响应隔离、负责人撤销和存储异常恢复本轮由代码审查/后端测试覆盖，未全部追加浏览器故障注入。
本轮没有采集浏览器控制台日志；HTTP409和连接断开是预期注入，不将页面截图当作控制台无错证明。

## 尚未满足的整体交付项

- 任务执行 Run 与 task/version/attempt 关联、依赖组队、产物评审及完整秘书交付链路。
- 至少一种真实 CLI 和两个实际 Docker Worker 的权限、工作区、HOME、配置隔离，停止与重建恢复。
- 个人/项目记忆权限、授权快照、经验候选批准和版本回滚。
- 实际资源与费用约束、Owner 运行干预和检查点恢复。
- 完整浏览器 E2E、已授权供应商/CLI 联调、Tauri 迁移与备份恢复说明。
- 远端交付：F16 立即 push 返回 403，账号 `suiyue1990` 无 `xiaoyangtx996/CorpPilot` 写权限。本地提交不视作远端交付。

最终仍须独立 QA 全量回归、PM 验收和主代理逐项复核。未验证项不记为通过。

## F20 CLI 配置验收

主代理全量 `.venv\Scripts\python.exe -m pytest tests/ -q`：231 passed、8 subtests passed（36.50s）；前端 `npm run build`：TypeScript和Vite36模块通过。前后端代理交叉审查 Pass。

在独立browser-state数据目录、IAB http://127.0.0.1:7892 实测：显式版本检查返回Codex 0.153.3；保存qa-cli-disabled及测试环境变量名后关闭重开保持一致，enabled仍false；不存在的.exe可在禁用状态保存，probe明确失败，随后恢复实际路径。未设置该测试凭据、未调用模型、未创建执行。

390×844下document scrollWidth=390、对话框宽352，无横向溢出；截图检查文字字段和滚动布局，Escape关闭。未采集浏览器控制台日志，不将版本检查成功视为真实模型/CLI任务联调通过。版本检查固定--version、无凭据环境、10秒/64KiB及单probe锁的故障场景由单元测试覆盖。

## F21 内部任务调度

内部CLI队列接入现有控制器的独占生命周期锁。独立QA覆盖并发、权限/版本撤销、取消/关闭、协议失败、写入失败与提交后响应丢失不重跑、unknown阻断。主代理额外覆盖线程提交异常、异常Future、单条写入失败仍能停止另一个执行，以及真实Python进程通过Windows Job取消后记录观察到的退出码。

上述队列测试使用真实SQLite及显式故障runner；本机进程测试调用真实进程树后端，但不调用Codex模型。尚未提供浏览器任务执行入口、产物评审或unknown核查恢复界面；这些边界不记为通过。

F21主代理全量回归：254 passed、8 subtests passed（41.47s）。最后回调判定小调整后的Executions/CLIController/故障测试40 passed（4.25s）。工作台整体仍未完成。

## F22 执行 HTTP 边界

正常服务http://127.0.0.1:7892的CLI runtime返回运行中/零活动；已有任务执行列表为空，CLI禁用时POST返回400，未创建排队记录。API新建会触发已配置CLI调度，测试仅用明确fake runner，未调用模型。

HTTP独立验证配置/execute权限/需求409、同键回读（含配置禁用及服务重开）、字段严格、跨来源拒绝、无report/状态PATCH入口、排队及运行中停止。停止请求返回stopping，只有runner确认退出才成为cancelled。

F22全量回归258 passed、8 subtests（45.56s）；随后HTTP扩展为6项，另验证8个并发同键请求仅一个execution/一次fake runner，以及重启unknown后原键可读、新键禁止替代。新增用例不冒称包含在此前258项全量结果中。

## F23 浏览器执行验收

- 开发模式：Vite7893、React.StrictMode首次打开任务执行窗口成功读取，负责人无execute时禁用确认；Escape关闭。
- 独立注入环境7894：任务c4924800-a3bd-44de-8cfb-a30c78ae9e0c，第一执行1519b780-4bcb-4cdc-a948-2fc4bb17dac5经UI确认/停止成为cancelled，第二44c411b2-5d41-4353-bb0b-e84d3c30afc3成为awaiting_review（未批准）。外部真实PATCH使任务v1→v2，旧窗口POST409，刷新后拒绝状态保持，可显式撤销，未创建错误执行。
- 断线注入代理7895：真实上游接受POST后断开响应，随后列表GET503，页面保留请求767d9927-f1e1-4bd3-8381-44cbc9c16771；刷新同标签页后同键核对返回eb93ff39-54e4-47cf-9c2b-60165cfc48a5。直接GET上游验证仅3条记录、该请求仅1条，无重复执行。
- 窄屏390×844，DOM宽390/对话框352，截图检查滚动与按钮可达，无横向溢出；未采集浏览器控制台日志。上述503/断连为预期注入。

执行器是明确标注的QA替身，仅验证浏览器+真实HTTP+SQLite+控制器交互，不能作为真实CLI模型或Docker证据。外部脚本位于H:\item\CorpPilot-test-evidence-20260906\f23_execution_ui.py和f23_drop_proxy.py，数据在f23-injected-state，均不进入产品仓库。验收后已停止注入服务、代理与Vite；正常7892保持CLI禁用。最终TypeScript/Vite37模块构建通过；独立代码审查Pass。

## F24 成果快照验收

全量285 passed、8 subtests（46.81s）；最终文件边界与HTTP定向27 passed（1.89s），包含后来增加的路径字符测试。实测Windows目录句柄禁止替换、文件句柄禁止修改/删除，拒绝符号/硬链接、大小/总量/数量/深度/条目上限、当前凭据UTF8/UTF16与文件名。快照写入和执行终态同事务，路径校验失败回滚为running，旧版本不发布成果；下载字节与hash正确，源文件后改不影响快照，数据库损坏拒绝下载，HTML作为附件返回且nosniff。

CLI集成测试使用假runner加真实文件，不代表真实模型生成成果。当前只提供成果API，尚未验收浏览器成果查看或Owner批准流程。采集仅Windows，当前凭据匹配非通用DLP，仍需双Docker实际权限隔离验收。
