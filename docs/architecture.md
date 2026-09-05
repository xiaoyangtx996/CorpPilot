# CorpPilot 架构说明

## 浏览器工作台增量（2026-09-06）

下文原有 TaskService/WorkflowEngine 描述属于旧组织流程。新浏览器工作台通过
`scripts/workbench/server.py` 的本机 Owner API 访问同一用户数据目录中的 SQLite；
新任务不写入旧 JSON TaskService，避免自动触发旧审批流程和双写。

任务卡由 `Tasks` 管理：长期身份与会话通过稳定 ID 引用，任务记录本会话 Owner 源消息、
负责人、目标标题、范围和验收文本。创建请求以会话和 request_id 唯一；重试比较原始版本，
即使任务后来被编辑也不会重复创建。每次实际修改保存不可覆盖的需求版本，
`expected_version` 冲突返回 HTTP 409；无变化不升级版本。归档可读，不可新建或修改。

接口前缀 `/api/workbench`：`GET/POST /conversations/{id}/tasks`、
`GET/PATCH /tasks/{id}`、`GET /tasks/{id}/revisions`。仅 Owner 管理接口使用这些方法，
不接受调用者自报身份；负责人必须是启用的会话成员。创建和编辑不调用模型或启动执行。

当前 `Runs` 是指定成员的聊天回复，尚未绑定任务。任务执行接入时必须明确绑定 task_id、
requirement_version、attempt，并在结果提交事务内检查当前版本和有效执行；
聊天回复完成不能作为任务验收通过。任务 UI 已接入并有独立浏览器证据；实际执行链路与产物验收仍待实现。

### 任务执行记录（F18）

`Executions` 使用同一 SQLite 的 `task_executions`，区别于只产生消息的聊天 `Runs`。
每条记录固定 task_id、负责人、requirement_version、跨版本递增的 attempt 和独立 UUID。
复合外键引用不可变需求版本，每任务最多一个 queued/running/stopping；全局最多100条活动记录。
创建请求幂等；再次执行必须引用该任务最近一次执行 ID 并记录副作用核查说明，换请求 ID 不能绕过。

领取和提交结果均在写事务检查需求版本与会话权限。旧排队不启动；已退出的旧版本记录为
superseded，保留历史但不推进新需求。回调核对 execution ID、attempt 和需求版本，终态不可覆盖。
零退出且显式 success=true 才进入 awaiting_review，不能直接通过任务验收。运行中的停止请求只进入 stopping 并继续占位；
只有可信 runner 确认进程树退出才能报告数值退出码，未证实退出或结果使用 unknown。

初始化存储不自动恢复/执行；独占控制器启动时可显式 recover，将遗留运行记录改成 unknown。
这不证明孤儿进程已停止。F21起存在unknown时禁止创建或领取替代执行，不能仅凭核查说明重跑。
F18 只提供内部持久化与回调契约，不开放 HTTP 创建或伪造 CLI；配置、真实执行、产物审核及恢复接入待续。

### 本机 CLI 适配与进程树（F19）

`cli.run_codex` 固定调用 Codex `.exe`，任务通过 stdin，模型单独参数；不接收任意附加参数，
不复制用户登录会话。每个执行 UUID 新建独立 work/home/tmp/CODEX_HOME，已有目录拒绝复用，
链接和 Windows 重解析路径拒绝。环境仅保留必要系统路径与本次显式 API key，不继承其他密钥、
PYTHONPATH、NODE_OPTIONS 或 Git 配置；工具 shell 的环境另排除 API key。

固定 `--ignore-user-config --ignore-rules --ephemeral --json --sandbox workspace-write`，
并以 `project_root_markers=[]` 停止父级项目发现、`allow_login_shell=false` 避免登录 shell 配置。
这些参数依据本机 CLI 0.153.3 帮助与 [官方配置文档](https://learn.chatgpt.com/docs/config-file/config-advanced)；
凭据使用与 JSONL 完成事件见 [官方非交互文档](https://learn.chatgpt.com/docs/non-interactive-mode)。
成功同时要求物理退出0、单次 turn.completed、有效最终 Agent 文本且无 turn.failed；
结果不返回原始 stdout/stderr，已知凭据在摘要中隐藏。`success=false` 即使物理退出0也不可提交待评审。

`process_tree.run_process` 使用 Windows Job Object：挂起创建，加入 Job 后恢复，杜绝子进程先逃逸的窗口。
双流合计输出默认上限4MiB，stdin写入与运行共用执行时限；超时、取消、溢出及主进程退出均清理剩余后代。
确认 Job 无活动进程且主进程退出才返回数值退出码；清理无法证实时为unknown。
执行时限后允许有界清理等待；Job句柄随控制进程关闭自动终止后代，已用真实崩溃场景验证。

这是可信本机工具的配置/会话分离，不阻止同一Windows用户读取其他宿主文件；Docker权限隔离仍为必做项。
本轮只验收适配函数、真实进程树和 CLI 版本探针，未接入任务控制器/配置UI、代码checkout或产物提取，
也未调用真实模型。部署需使用相同平台验证；非Windows进程后端目前明确返回start_failed。

### CLI 配置与任务调度（F20–F21）

`CLISettings` 提供独立持久配置，GET/PATCH不执行；显式版本检查使用临时HOME、无凭据环境与进程树时限。
`CLIController` 由现有 `ReplyController` 持有同一个生命周期锁后初始化和恢复，在同一派发线程中轮询。
线程池仅执行已领取任务，按CLI配置并发上限派发；角色身份默认没有execute，须显式授权才能创建/领取执行。
运行时重新检查需求版本、成员、启用和execute权限；撤销或取消通知Event终止本地CLI，退出结果仍经事务授权检查。
已完成Future保留到结果落库成功，写失败只重试报告；个别失败不阻断其他实例停止。
无法确认的提交/线程/进程结果归入unknown并暂停CLI队列，不暂停聊天。关闭先停止派发、通知所有CLI取消，
等待进程树退出及结果写入后才释放控制器锁。unknown解除和副作用核查仍待真实恢复流程实现。
F21内部调度已接入CLI适配器，但HTTP/UI执行入口、成果提取和评审、费用约束、真实模型与Docker联调仍未完成。

F22 开放 Owner HTTP：`GET /cli-runtime`、`GET/POST /tasks/{id}/executions`、
`GET /executions/{id}`、`POST /executions/{id}/cancel`（空对象）。新建体严格为
request_id、expected_version、reconciliation_note、previous_execution_id；返回202表示执行记录已接受，
不代表完成。新请求须配置就绪；同键原请求已存在时仍可在配置禁用后回读，不能更换内容。
HTTP不接受执行结果报告或直接状态修改，取消无需模型配置，运行中仅记stopping并由调度器通知实例退出。
所有接口沿用本机Host/Origin及JSON边界；实际任务执行界面与成果评审为后续增量。

### 成果快照（F24）

CLI提示要求将可交付文件写入当前工作目录的 `artifacts/`。可信进程退出且协议成功后，
从固定执行UUID路径采集，不使用runner返回的任意路径。Windows句柄固定祖先与读取文件，
拒绝链接、重解析点、硬链接和特殊文件；限制100文件、单文件4MiB、总16MiB、1000条目及12层。
当前API key按UTF8/UTF16及文件名精确检查；这不是通用DLP或同用户OS权限隔离。

文件字节、相对路径、大小、SHA256在同SQLite保存；immutable触发器禁止更新/删除/替换。
只有当前有效执行进入awaiting_review时与终态同事务写入；成果采集失败即使CLI exit0也不能进入评审。
`GET /executions/{id}/artifacts`提供元数据，`GET /artifacts/{id}/download`读取验证后的快照字节，
返回attachment/octet-stream/nosniff，不从工作目录实时下载或内联执行HTML。执行目录回收不影响快照。
空目录不伪造成果；当前成果批准与浏览器查看仍待后续增量实现。

### Owner 评审（F25）

`execution_reviews` 与执行物理状态分开：每次执行仅一个不可覆盖的approved/rejected决定，保存说明、
需求版本、完整成果ID集合、请求ID和时间。`GET/POST /executions/{id}/review`提供读取/明确提交，
未决定返回null，不提供修改或删除。原请求可幂等回读历史，不重新执行任务。

新评审在同一写事务中检查待评审状态、当前需求、最新执行及成员/启用/execute权限；批准还要求
非空成果、ID集合完全匹配，使用与下载相同的字节、大小、hash和路径验证。拒绝可用于无成果或损坏内容。
并发决定仅一个生效；后续需求或执行改变不会篡改历史评审，展示当前验收结论时必须同时匹配最新版本/执行。
保存决定不改写任务需求或执行退出码，不能用历史approved推断新版本完成。浏览器入口仍待下一增量。

## 1. 目标

CorpPilot 用“组织架构”来表达多 Agent 系统中的职责边界。
当前阶段的目标不是做复杂自治，而是先把协作内核、控制面和事件留痕统一起来，让任务流、提案流和执行动作都能被稳定观察和人工干预。

## 2. 总体结构

```text
用户请求
  -> TaskService
  -> WorkflowEngine
  -> Dashboard API / CLI

并行存在的董事会流程
  -> BoardRoom
  -> Discussion / Vote / Tally / DirectOrder

统一观测层
  -> EventLogService
  -> AgentMonitorService
```

## 3. 核心组件

### 3.1 TaskService

职责：
- 创建、读取、更新、删除任务
- 维护统一任务模型
- 写入任务历史记录
- 汇总任务统计

关键约束：
- 所有任务都带 `current_owner` 和 `execution_owner`
- 状态流转和人工干预都要进入 `history`
- 任务事件同步写入 `events.json`

### 3.2 WorkflowEngine

职责：
- 作为统一编排入口
- 执行任务状态流转
- 提供路由快照
- 暴露任务时间线
- 转发人工干预动作

### 3.3 BoardRoom

职责：
- 创建董事会提案
- 记录讨论与投票
- 统计投票结果
- 支持紧急提案直接下令
- 提供提案摘要聚合

### 3.4 AgentCatalogService

职责：
- 扫描 `agents/*/SOUL.md`
- 生成 Agent 配置快照
- 提供按层级过滤的 Agent 元数据

### 3.5 SkillCatalogService

职责：
- 管理本地与远程 Skill 配置
- 记录 Skill 适用 Agent 范围
- 为后续热更新预留配置入口

### 3.6 AgentMonitorService

职责：
- 根据任务责任链和历史记录推导 Agent 健康状态
- 聚合责任任务数、执行中数量、阻塞数量和最近活跃时间

当前限制：
- 现在是推导式健康状态，不是真实心跳
- 更适合做控制面观察，不适合做生产级告警

### 3.7 EventLogService

职责：
- 统一记录任务与提案事件
- 支持按类别和主体过滤
- 默认按时间倒序返回

### 3.8 ExecutionService

职责：
- 对执行层动作提供统一协议
- 封装 `start`、`complete`、`block`

当前限制：
- 只是编排层薄封装
- 还没有真正接入 Worker 或 Agent 执行器

## 4. 数据存储

当前版本使用 JSON 文件存储：

- `data/tasks.json`
- `data/proposals.json`
- `data/agent_config.json`
- `data/skills.json`
- `data/events.json`

这是为了保持原型轻量。数据量放大后，需要迁移到数据库或事件存储。

## 5. 状态机

### 5.1 任务状态机

```text
pending
  -> classified
  -> planned
  -> reviewing
  -> approved / rejected
  -> dispatched
  -> executing
  -> review
  -> completed
```

补充分支：
- `rejected -> planned`
- `executing -> blocked`
- `blocked -> executing`
- `review -> executing`

### 5.2 人工干预

- `pause`
  - 允许状态：`executing`
  - 目标状态：`blocked`
- `resume`
  - 允许状态：`blocked`
  - 目标状态：`executing`
- `send_back`
  - 允许状态：`reviewing`、`approved`、`dispatched`、`executing`、`review`、`blocked`、`rejected`
  - 目标状态：`planned`

### 5.3 执行协议

- `start`
  - 允许状态：`dispatched`、`blocked`
- `complete`
  - 允许状态：`executing`
- `block`
  - 允许状态：`executing`

### 5.4 提案流程

- 普通提案：讨论 -> 投票 -> 计票
- 紧急提案：董事长直接下令

## 6. API 设计

### 任务

- `GET /api/tasks`
- `GET /api/tasks/:id`
- `GET /api/tasks/:id/timeline`
- `POST /api/tasks`
- `PUT /api/tasks/:id`
- `POST /api/tasks/:id/status`
- `POST /api/tasks/:id/intervene`
- `POST /api/tasks/:id/execute/start`
- `POST /api/tasks/:id/execute/complete`
- `POST /api/tasks/:id/execute/block`
- `DELETE /api/tasks/:id`

### 董事会

- `GET /api/board/proposals`
- `GET /api/board/summary`
- `GET /api/board/proposals/:id`
- `POST /api/board/proposals`
- `POST /api/board/proposals/:id/discuss`
- `POST /api/board/proposals/:id/vote`
- `POST /api/board/proposals/:id/tally`
- `POST /api/board/proposals/:id/order`

### 观测与配置

- `GET /api/agents`
- `GET /api/skills`
- `GET /api/stats`
- `GET /api/events`
- `GET /api/health`

## 7. 控制面现状

当前 Dashboard 已经支持：

- 任务列表与状态过滤
- 按 Agent 责任链过滤任务
- 查看任务详情与时间线
- 推进任务状态
- 人工干预
- 执行协议动作
- Agent 健康概览
- 提案摘要与最近提案
- 统一事件流展示

## 8. 当前边界

当前版本仍然是“协作内核 + 控制面原型”，还没有进入完整多 Agent 运行时阶段。明显缺口包括：

- 真实 Agent 执行器
- 事件总线与订阅机制
- 任务与提案的自动联动
- 实时心跳和执行耗时指标
- 模型与 Skill 运行时热更新
- 完整自动化验证链路

## 9. 推荐演进路径

1. 给 `WorkflowEngine` 增加事件订阅、重放和回溯能力。
2. 把 `ExecutionService` 扩展为真实 Worker 协议接入层。
3. 把 `AgentMonitorService` 升级为心跳与指标采集服务。
4. 给控制面补提案操作、事件筛选、SLA 和阻塞原因视图。
5. 将 JSON 存储逐步迁移到数据库或事件存储。

## F27：版本化前置任务与调度门槛

依赖属于任务需求：同会话内每任务最多32个直接前置，当前版本图必须无环，祖先连同自身最多1000个。修改依赖复制当前需求字段并递增 requirement_version，普通需求编辑保留依赖；历史版本接口同时返回 dependency_task_ids。依赖和执行输入引用持久化到不可更新/删除/替换的 SQLite 行，不增设第二个调度器。

GET/PATCH `/api/workbench/tasks/{id}/dependencies` 读取或替换完整前置清单。PATCH只含 expected_version 与 task_ids；冲突409，不自动套用新版。GET返回 task_id、requirement_version、task_ids、ready、blocked_reason；ready只表明前置审批门槛满足，不代表任务已完成、权限有效或CLI配置可用。

排队执行只在所有前置的当前需求版本、最新执行均经Owner批准时可被claim；未就绪仍为queued，不占执行并发。现有队列继续遍历其余任务。claim同事务固定execution_inputs，snapshot返回直接dependency_inputs；运行中、完成回调和Owner评审通过既有授权路径再次核对这些绑定。递归检查祖先绑定，A新版获批不能让仍使用旧A成果的B自动有效。上游变化会触发控制器停止信号，必须等待实际进程结束才能确定退出；迟到结果不能提交验收。

本增量尚未把前置文件放入下游CLI工作区，也未提供浏览器依赖编辑入口；这两项继续实施。API和SQLite调度测试不代表实际模型已消费协作成果。
