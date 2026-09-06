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

## F28：下游执行消费前置成果副本

CLI启动时调用 snapshot(include_artifacts=True)：在同一SQLite读事务中核对当前执行授权和冻结依赖，再读取直接前置的已批准成果。完整成果ID集合必须与Owner批准清单一致；不自动取得传递祖先文件、其他任务成果或前置Agent的私聊/HOME。定期状态检查使用默认轻量snapshot，不重复加载文件字节。

总输入最多100文件、每文件4MiB、总16MiB。先检查数据库真实字节长度与声明大小，再加载和验证SHA-256；CLI落盘再次验证完整字段和限额。所有文件写到新执行 work/inputs/{artifact_id}，原路径只在prompt元数据中映射，避免 AGENTS.md、.codex/config.toml 等原名变成环境配置。文件以独占创建写入，已有执行目录不复用，不创建跨工作区链接。

前置副本是任务资料，不能扩大指令或工具权限。副本可由当前执行修改，但原数据库成果及其他执行副本不变；仅artifacts目录可发布新成果。准备错误明确发生在进程启动前，标记not_started并保留失败，不伪装成已执行或未知进程；真正runner异常仍保留unknown。启动后上游变化继续走F27取消信号和回调/评审复查。

这些是本机可信CLI的目录与副本边界，不宣称同用户OS沙箱或双Docker隔离。当前通过真实文件、数据库与注入进程边界测试；真实模型消费前置文件尚未验收。

## F29：依赖浏览器入口

任务卡“前置任务”以当前任务/依赖双版本一致读取初始化草稿，选择同会话任务。保存依赖只更新依赖和需求版本，不调用执行器；任务卡与历史同步版本。普通读取只更新对照状态，保留已编辑选择；未知PATCH持久保存原版本请求，409后明确对照才重新编辑。会话归档与负责人状态控制写入，后端仍独立重查。

依赖就绪状态只表示前置验收门槛；执行窗口显示等待原因，并在读取到需求版本变化后禁止沿旧确认新建执行。F28提供文件副本传递，本增量完成对应依赖配置UI，不代表智能自动分工或真实模型/Docker链路全部完成。

## F30：批准记忆与执行版本快照

个人范围 agent 使用稳定身份 ID；共享范围 project 使用董事会或项目会话 ID，私聊没有项目共享记忆。每范围是一份最多 8000 字符的版本文档，v0 为空。候选必须来自当前需求的最新执行及 Owner 已批准、完整校验的成果；提案与批准都复查身份、成员、execute 权限和冻结依赖。批准才产生新版本，拒绝不改正文；回滚复制旧内容形成新版本，历史不改写。

SQLite 保存不可变候选、决定、修订、请求响应及执行绑定。BEGIN IMMEDIATE 内核对 expected_version，竞争审批仅一个成功；相同请求键与内容重放原响应，不因后续改版变化，异内容复用键拒绝。失效候选允许 Owner 拒绝关闭。

claim 同事务冻结本身份及本群记忆版本，包括 v0；CLI 启动授权快照只读取这些批准版本，后续批准/回滚不替换正在运行的上下文。无历史绑定的执行拒绝用当前记忆补齐。轮询不重复加载正文。记忆作为经验资料进入 prompt，不扩大工具或数据权限。

Owner API：GET /api/workbench/memories/{scope}/{id}；GET history、GET/POST candidates、POST rollback 子路径；GET /api/workbench/memory-candidates/{id}；POST 其 decision 子路径。提案字段 request_id/expected_version/source_execution_id/content；决定 request_id/decision/note；回滚 request_id/expected_version/target_version/note。沿用同源限制、严格字段和 HTTP 409 版本冲突。

这是应用层上下文选择，不是对同一 Windows 用户恶意进程的 OS 隔离；Owner 管理 API 可查看全部范围，未来 Docker Worker 不得取得该管理接口。当前候选为有来源的显式文本，浏览器入口已由 F31 补齐；自动复盘和 Skill 发布尚未实现，不能称完整自我进化。

## F31：记忆浏览器入口与恢复

右侧个人记忆按稳定身份打开；非私聊会话工具条提供项目共享记忆。候选来源限定当前会话的当前最新批准执行，列表显示任务名、需求版本和执行标识。全文替换需 Owner 说明与确认；回滚显示目标内容并新建版本。读取其他身份不更改 CLI 的冻结上下文。

原生面板按 scope/identity 隔离状态和 sessionStorage。写入前保存原 POST 路径和 payload；未知响应只能同键重试，GET 相同内容不作为提交确认。明确 HTTP 拒绝可在最新状态读取成功后主动重新编辑；普通读取保留全文草稿。读取失败不标记空数据；旧快照标记待核对。后端始终重查授权、来源完整性和版本。

个人回滚由身份 enabled 控制，不因当前群归档而禁止；项目归档禁止新提案、批准和回滚，尚未决定候选仍可拒绝。当前候选由 Owner 明确填写，还未实现模型自动复盘或 Skill 发布。

## F32：后续桌面宿主边界

[Tauri 2 迁移方案](tauri-migration.md) 给出静态前端复用、受限Rust通信和Python sidecar生命周期。业务状态与权限仍由现有Python服务持有；桌面握手/会话鉴权、持久待确认请求及冻结worker入口是后续实际改造点，尚未实现。

## F33：未知执行的人工核查记录

未知执行不再只能通过修改数据库解除阻塞。新增独立 execution_reconciliations 记录，绑定原 execution_id、attempt 和 requirement_version；要求 Owner 明确声明进程已停止、外部影响已核查，并填写核查证据说明。记录不是机器退出证据，不能把声明当作已观测退出码。实际停止和外部结果不明时不得提交，也不能由助手替用户确认真实未知执行。

原执行仍保持 unknown、原 exit_code/summary 不变；不批准成果、不产生新执行，不使迟到回调成为成功。BEGIN IMMEDIATE 写入唯一不可变记录，UPDATE/DELETE/REPLACE 禁止；精确同请求重放原记录，不同内容拒绝。核对历史事实不要求当前任务仍为原版本、原成员仍启用，避免失效任务永久卡住全局队列；新执行仍独立验证当前权限与版本。

全局和任务阻塞判断仅考虑没有核查记录的 unknown。所有未知项核查后，既有已授权 queued 任务可继续调度；同任务新尝试仍须明确引用最近执行、写明再次执行理由并使用新请求。当前控制器 active 集合仍持有的执行及关闭中的控制器拒绝人工核查，不能用表单取代本地运行线程结束。

GET /api/workbench/execution-reconciliations/pending 返回最多100条未核查未知执行，按时间与ID排序；GET /api/workbench/executions/{id}/reconciliation 返回记录或null；POST 同路径只接收 request_id、attempt、requirement_version、process_stopped=true、external_effects_checked=true、note。核查字段绑定原run，不允许上传退出码或成功标志。保留同源检查和版本不符409。

F33 完成服务能力，F34 已接入浏览器全局核查入口并修正已核查 unknown 的任务提示。该能力不是自动孤儿进程探测或检查点续跑，不满足所有恢复要求；真实进程/容器识别与恢复仍需继续完成。

## F34：浏览器未知执行核查与请求恢复

全局核查窗口读取 F33 的未核查清单与原执行记录，展示任务、身份、attempt、requirement_version，并要求 Owner 填写依据和分别确认两项声明。核查针对历史事实，不以当前任务版本、会话归档或身份启用状态替代原执行身份；后端仍拒绝控制器持有或关闭中的执行核查。

POST 前持久保存原路径和 payload；断线、刷新及 GET 失败后保留原请求，同键重试核对。未成功读取不得显示空清单。若另一请求已写入不同的不可变声明，必须先成功读取该声明，再由 Owner 明确结束本地等待；不覆盖记录，也不把另一请求的成功冒认为原请求已接受。

任务执行窗口区分 unknown 结果与人工核查记录，只有未核查项继续阻断。全部未知项核查后，既有已授权队列可继续调度，新尝试仍走当前版本、权限、配置与前次执行引用检查。浏览器保存不改写退出码、批准成果或创建新尝试。构建、固定样本浏览器交互及服务重启回读已通过，证据见 [验收报告 F34](acceptance-report.md#f34-浏览器未知执行核查验收)；不据此宣称真实孤儿进程、模型或双 Docker 恢复完成。

## F35：Owner 确认的项目建立事务（后端与 HTTP 已验证）

`Collaboration` 复用同一 SQLite、`Tasks._create` 和现有依赖校验，不增加第二套任务或调度器。源为私聊或群聊的一条 Owner 消息；创建计划严格包含 request_id、source_message_id、title、shared_brief、coordinator_id、tasks。每计划 1–16 项任务，以唯一局部 key 引用前置；每项提供 title、scope、acceptance、agent_id、depends_on。请求上限 64KiB，共享摘要上限 16000 字符，依赖仅能引用计划内任务且必须无环。

新建前核对源会话未归档、源消息属于该会话且由 Owner 发出、协调人属于源会话，以及协调人和各负责人均启用。新项目成员为协调人与负责人的去重集合，不把获邀成员加入源会话。Owner 明确提供的 shared_brief 写成新项目的首条 Owner 消息，新任务绑定这条共享消息；源会话/消息 ID 保留在创建回执，不复制私聊/群聊历史或个人记忆。

`BEGIN IMMEDIATE` 内创建项目会话、成员、共享摘要、所有 v1 任务和 v1 依赖，通过现有 DAG 校验后保存不可变 `collaboration_receipts`。任何失败整体回滚。回执以源会话和 request_id 唯一，保存原请求与创建结果快照；同键同内容重放原结果，异内容拒绝，后续任务编辑不改变该创建回执。成员邀请只建立本项目范围，不赋予 execute 权限。

Owner HTTP 提供 `GET/POST /api/workbench/conversations/{id}/collaboration-plans` 和 `GET /api/workbench/collaboration-plans/{id}`。创建、同键重放、单条读取及列表均返回创建回执，包含项目/共享消息 ID、协调人、成员、局部 key 到任务 ID 的映射，以及 `approved_plan` 原计划。`approved_plan` 从既有不可变 payload 列读取，不另存副本或拼接当前任务字段；后续编辑、归档或成员改变不改写历史计划。

此事务不调用模型、不创建聊天 Run 或任务执行，也不自动派发。协调与拆分由 Owner 明确提交；智能组队、预算约束和真实模型/双 Docker 运行仍不在本增量已验收范围。后端及 HTTP 定向 30 项通过、Tech Lead 审查 Pass；主代理全量 421 项及 8 子用例通过（65.34s）。F36 已补齐浏览器入口，F37/F38 已提供提案后端与浏览器导入再次确认；自动组队和真实执行闭环仍未完成，不据此宣布完整协作产品交付。

## F36：浏览器协作计划与原请求恢复

`CollaborationPanel` 从 Owner 消息入口打开，明确共享摘要初始为空，不从原目标自动填入。Owner 选择源会话内启用的协调人、1–16 项任务负责人及依赖，查看完整计划和去重成员后确认提交。前端校验循环、必填项和 64KiB 上限，后端继续独立核对权限与 DAG；负责人技能仅供人工选择参考，不构成自动路由。成功回执与历史展示不可变 `approved_plan`，可打开实际创建的项目群。

当前标签页使用一条全局 sessionStorage 待确认记录，保存原 source_id 和完整 plan；从左侧“协作计划与恢复”可跨当前会话恢复原来源，普通未提交草稿不保证刷新恢复。每次 POST 前先持久保存原内容，存储无效或失败禁止发送。首次明确 4xx 拒绝后可主动保留全文重新编辑并生成新请求；若选择核对原请求，发送前清除旧 rejected 标记，之后出现断线或未知结果只能继续同键核对，不能沿用早先拒绝证据改建替代项目。源会话后来归档不妨碍已提交原请求的历史回读与重放。

确认创建须同时匹配回执中的源会话、request_id 和完整 approved_plan；GET 失败不表示没有项目，已确认回执不因后续历史读取失败消失。主代理浏览器验证正常双任务依赖、循环阻止、停用成员拒绝后重编辑，以及 POST 已提交但响应断开、GET 503、跨会话刷新恢复和同键核对；独立数据回读为两项目三任务，成员及依赖准确，无私聊历史复制或 CLI/模型调用。最终构建 42 modules、index-DS6DJ8Di.js；本增量无后端修改，沿用 F35 的 421 项及 8 子用例证据，不将其记录为新一轮后端回归或真实自治执行验收。

## F37：共享 Runs 队列的模型协作提案

Owner API：`GET/POST /api/workbench/conversations/{id}/collaboration-proposals`、`GET /api/workbench/collaboration-proposals/{id}`。POST 严格接受 agent_id、source_message_id、request_id、candidate_ids；agent_id 为协调人。候选为 1–100 个不重复、已启用身份，按 Owner 输入顺序保存，调换顺序属于不同请求内容；协调人无需列入候选，除非也将承担子任务。源消息必须属于该会话并由 Owner 发出。

创建时冻结候选公开 id、name、template_id、skills；模型上下文只使用目标 Owner 消息、协调人指令和这些候选资料，不读取其他聊天历史或个人记忆。执行前检查全部候选仍启用；完成前检查实际选中成员仍启用、协调人仍启用且属于未归档源会话。候选改名、技能或模板编辑不覆盖创建时快照；模型输出不能扩大白名单，最终 F35 创建事务再次核对当前身份与权限。

提案 ID 复用 Run ID，与普通回复共享活动上限100、并发和 RPM 限制，不另设调度器。普通聊天列表不展示提案 Run；专用端点查询其状态与结果。模型配置未就绪时 queued 等待，排队取消沿用 `POST /api/workbench/runs/{id}/cancel`。失败不自动重试；控制器重启把遗留 running 记为 unknown，不据此重发模型请求。

返回内容必须满足严格 JSON 结构、1–16 项任务、本计划局部 key 的 DAG 及负责人白名单。解析或校验错误以专门的安全错误归入 failed，不把非法模型内容当作已接受提案。成功仅保存提案并将 Run 记为 completed，不插入普通聊天消息、不创建协作项目/任务或 CLI 执行；提案结果写入与完成状态保持事务一致，不能留下半成功结果。

F37交付后端/API；F38已补齐浏览器显式导入F36，由Owner重新审阅共享摘要、成员、任务与依赖后提交F35，模型提案不能冒充不可变approved_plan。F37主代理全量440项及8子用例通过后，仅追加5条故障/边界测试且生产代码未改，规划模块最终20项定向通过，未重跑全量；详细证据见 [F37 验收记录](acceptance-report.md#f37-模型协作提案后端验收)。F37本身无前端变更，后续F38浏览器证据不替代真实供应商、CLI或Docker验收。

## F38：模型提案浏览器状态与草稿导入（已验证）

Owner消息与全局“模型协作提案”入口复用F37 API。调用前由Owner选择候选并明确确认模型请求；待确认记录在发送前保存原来源与完整请求到当前标签页sessionStorage。首次明确4xx拒绝可主动修改；每次重试前清除旧拒绝标记，之后发生未知结果仅沿原键核对。收到202后持久保存Run ID，刷新后通过GET跟踪同一提案，不重新POST生成替代请求。尚未收到202而结果未知时则保留原POST；匹配结果确认后，即使后续GET失败也保留已完成提案。

completed结果作为待审阅建议显示，不能因模型完成就创建项目。Owner显式导入后，F36以新的项目创建request_id接收草稿，摘要和分工仍可编辑，确认默认未勾选，必须重新确认才能提交F35事务。既有F36待确认请求阻止导入并提供恢复入口，原计划不被覆盖。模型调用确认与项目共享/创建确认是两次不同授权。候选停用后保留已选项供取消，取消后不可重新选择；非法JSON只有失败状态，不产生可导入结果，queued取消及其终态刷新继续沿F37接口读取。

主代理类型检查与最终43模块构建通过（index-Dx-oq0Yc.js），独立审查Pass。真实浏览器覆盖正常提案、202后GET恢复、首发400后原键受理却丢响应/GET503、跨会话恢复、F36待确认冲突、非法JSON和排队取消。独立数据为4个Run、3次实际本地HTTP模型调用、2项目/4任务v1、0次CLI，未泄漏私聊前史。沿用F37后端全量与追加定向历史证据；本地fixture不代表真实供应商、CLI或Docker通过，详见 [F38验收记录](acceptance-report.md#f38-模型提案浏览器验收)。

网络受理结果未知的同键恢复与服务端Run=unknown分别处理：后者继续保留原请求、禁止替代调用，尚无模型未知结果的人工核查/解除入口，不能复用仅用于CLI的F33声明。存储损坏/QuotaExceeded只有静态fail-closed审查，未作浏览器注入；服务重启回读已验证，仍不等于所有模型未知恢复场景闭环。

## F39：Owner API 访问鉴权

本增量先补齐控制面权限边界：服务每次启动生成随机Owner token，所有 `/api/workbench` 读取、写入和成果下载统一校验Bearer凭据，继续保留Host/Origin检查。静态页面、资源与基础health不要求token，也不返回业务访问凭据；没有凭据不能借助伪造Host/Origin访问Owner数据或操作接口。token不作为永久身份保存，服务重启后旧授权失效。

主入口通过 `webbrowser.open` 打开带fragment的本次授权页面，不打印秘密；宿主默认仅在内存持有token，不持久保存secret。仅自动打开失败时，在宿主数据根生成含本次授权信息的 `owner-access-*.html` 运行入口，并在退出时删除；该文件被Git忽略，服务拒绝将数据目录放在静态前端目录内，避免被静态路由读取。浏览器读取fragment后立即从地址栏清除并存入当前标签页sessionStorage，统一API请求携带认证头。成果下载使用认证fetch取得blob，不把token放进下载URL或保留裸API链接。

401使用独立 `AccessExpiredError`，只要求重新授权，不把它当作业务首次明确拒绝而清除或替换pending。原请求可能已被服务器接受，必须保留原request_id与完整payload；重新授权后沿原键恢复。主代理浏览器已验证任务201落库却丢失响应、刷新后401、重新授权再同键回读且仅一任务；下载API字节一致，浏览器点击无错误，但未核对浏览器保存文件。

本轮最终44模块构建为index-BLeLdit0.js；排除未提交Docker修改的F39纯暂存树导出后，全量448项及8子用例通过，最终定向23项通过，独立审查Pass，详见 [F39验收记录](acceptance-report.md#f39-owner-api访问鉴权验收)。此前含Docker的488项工作树回归另作历史记录。此权限边界不隔离同一OS用户，不限制Worker任意网络出口，也不证明真实Docker、供应商或CLI运行。

## F40：Docker Worker 适配与恢复（真实容器尚未验收）

`CLISettings.backend` 在 local/docker 间选择，复用原 CLI 队列、任务版本、attempt、权限和成果评审。Docker 配置包含 .exe 绝对路径、固定 sha256 镜像 ID 或 repo@sha256 摘要、CPU/内存/PID 上限。显式 probe 只向固定本机 `npipe:////./pipe/docker_engine` 执行 info 和 image inspect，要求服务及镜像均为 Linux；不拉取镜像、不启动容器、不调用模型。宿主 Docker 命令使用独立空配置及精简环境，忽略环境中的远程 context 和 Docker 登录凭据。

派发前将原 backend 与 executable 绑定到不可变 `execution_backends` 数据库记录，UPDATE/DELETE/REPLACE 均拒绝。改变当前配置、丢失本地 Worker 文件不能把历史 Docker 执行降级为普通人工声明流程。每次执行在宿主独立 run 根保存 `docker-worker.json`，记录 create/start 意图、容器 ID、实际 image ID 和随机身份标记；该根和数据库不挂入容器。容器 inspect 必须核对执行标签、随机标记、容器 ID 与 image ID，不能只相信名称或 Docker 客户端退出码。

`run_docker` 使用 create（pull=never）、start/attach/stdin、inspect、stop/必要时kill及最终remove。容器固定 UID/GID 1000、只读根文件系统、cap-drop ALL、no-new-privileges、无自动重启；仅当前 run/work 可写挂载、inputs 只读挂载，HOME/CODEX_HOME 与 tmp 使用独立 tmpfs。CPU、内存/交换内存、PID、执行时限和输出量均有上限。确认不再运行后才返回可信退出信息并进入原成果捕获链，成功仍需 Codex 完成事件与 Owner 成果评审；停止已证实但 remove 失败保留记录并报告清理失败。

镜像在 `docker/worker/Dockerfile` 固定 Codex 0.153.3。内层使用 `--sandbox danger-full-access`，隔离依赖上述外层容器限制，不能作为本机 CLI 参数使用。模型 key 经 attach stdin 传给入口，再进入 Codex 进程环境；不进入 Docker argv、容器 Config.Env、登录配置或持久 HOME，工具 shell 不主动继承 key。此设计不承诺同容器同 UID 工具无法读取进程凭据；身份 labels 的随机标记不是模型密钥或 F39 Owner token。F39 访问凭据不传给 Worker。当前 bridge 网络不是域名出口白名单，完整凭据及网络隔离仍需真实验证。

Owner API 增加 `GET /api/workbench/executions/{id}/worker` 与 `POST /api/workbench/executions/{id}/worker/stop`，均沿用 F39 鉴权。仍由控制器持有的执行使用原任务停止请求；恢复入口只停止未被当前控制器持有的 unknown Docker 实例，先核实身份，stop 后再次 inspect，仍 running 才 kill 并回读，不自动启动或删除历史容器。机器证据未知时禁止“已停止”声明；本地记录缺失不是 absent，必须成功核对 daemon 的完整容器清单及执行标签。首次保存声明前服务端再次核查，浏览器旧的 exited 状态不能绕过当前 running；既有不可变声明按原请求精确重放，不要求 daemon 后来仍可用。

浏览器已验证禁用配置保存、真实 probe 失败，以及受控 Worker 状态的核查、两次停止、提交前状态竞争拒绝和原键恢复；这些替身未创建真实容器。最终主代理全量511项及8子用例通过，Tech Lead独立36项与Worker定向37项通过、审查Pass；QA新标签页恢复原执行、声明和禁用配置，测试服务经实际句柄中断并确认退出。详细结果与环境证据见 [F40 验收记录](acceptance-report.md#f40-docker-worker-适配与恢复验收)，安装/操作及未验收边界见 [Docker Worker](docker-worker.md)。本机 Windows 修复授权待答，尚未进行系统写入，F40 不代表真实 CLI、双 Worker 或整体目标通过。

## F41：模型复盘生成待批准经验候选

复用模型 Runs 队列、配置、并发/RPM限制、子进程总时限和未知恢复；复盘不是普通群聊回复，不向消息历史发布结果。Owner 指定目标记忆范围、当前版本、来源执行和选定的已批准成果 ID，服务端派生原执行负责人及会话。来源必须仍为当前需求下最新且获 Owner 批准的执行，满足成员、工具、依赖与成果完整性检查。

`retrospective_requests` 冻结原请求和模型输入：当前任务需求、目标范围已批准记忆的全文/版本，以及所选成果的 ID、路径、哈希和完整 UTF-8 正文。选择1–100项，拒绝二进制、异常控制符及总输入快照超过64KiB的情况，不静默截断；不附加聊天前史、未选择成果正文或其他身份私有记忆。模型只允许输出 `content`（8000字符以内的完整替换文档）与非空 `evidence_artifact_ids`（所选清单子集），保留有效旧经验并描述适用边界。

创建、模型读取与完成时重新校验授权、成果及记忆基准版本；完成事务同时创建既有 `memory_candidates`、记录证据并结束Run。模型完成不等于记忆批准，不更新批准版本、不发布Skill。Owner继续使用原审批/回滚接口，批准前再次核查；已经开始执行的记忆快照保持原版本。精确同键重放不重复模型或候选，跨请求类型及异内容复用键被拒绝；queued可以取消，running在服务重启后为unknown且不自动重发。

Owner鉴权接口：`POST/GET /api/workbench/memories/{scope}/{id}/retrospectives` 创建或列出记录；POST字段为 `request_id、expected_version、source_execution_id、artifact_ids`，返回202。`GET /api/workbench/retrospectives/{run_id}` 返回状态、原请求、成果元数据、候选ID和引用ID，不公开冻结输入全文。取消使用已有 `POST /api/workbench/runs/{run_id}/cancel`。本增量是后端/API；浏览器复盘入口另作F42验收，真实供应商的经验质量仍待验证。

## F42：浏览器模型复盘与候选审批

`MemoryPanel` 内嵌 `RetrospectivePanel`，选择当前会话中符合条件的已批准执行，并由Owner逐项选择1–100份成果；界面展示路径和字节数，UTF-8正文与总输入64KiB限制由F41服务端严格校验。调用前单独确认所选成果与目标记忆快照会用于一次模型复盘。生成仅创建待批准候选，原手工候选草稿保留；候选全文、适用边界与引用成果可审阅，仍通过既有明确确认和审批说明才替换批准记忆。

复盘pending按scope/identity保存到当前标签页sessionStorage，发送前固定原request_id、来源执行、基准版本和成果选择顺序。收到202后持久保存Run ID并只通过GET读取；尚未获确认时可沿原键核对，返回必须匹配目标范围、完整请求及已知Run ID。首次明确4xx才提供重新选择入口；每次重试先清除旧rejected，之后受理未知不可沿用旧拒绝证据替代请求。读取失败保留原请求及已知结果，不等于没有复盘；存储无效或保存失败禁止新建。

完成记录显示候选ID与模型引用成果，候选审批独立于模型调用确认；queued沿原Run接口取消，running不宣称能停止，unknown继续锁定且不提供替代调用。原MemoryPanel的候选/决定/回滚请求也改为每次发送前清除旧rejected，仅首次明确拒绝可重新编辑；401继续要求重新授权并保留原请求。

主代理已验证项目范围v0选定文本生成候选、原草稿保留、再次Owner审批后才成为v1，以及首400后同键受理202但响应丢失、GET503、刷新保留原键和恢复GET核对原Run。两个有效请求各一次本地受控模型调用；配置禁用时queued可取消且不调用模型，非法JSON失败且不生成候选。原MemoryPanel候选首400后同键201响应丢失时，旧rejected清除、刷新保留pending，候选GET可见不替代原请求确认。测试服务真实重启并轮换口令后，401触发重新授权，原完整payload与rejected=false继续保留，同键回读成功才清除pending，项目批准记忆仍v1。数据库回读4个Run、4候选、2决定/2修订且完整性通过，本地模型共3次、隐私标记未泄漏；测试服务最终经实际句柄中断并确认退出，7896/7897均无监听。未专项测试console/mobile，真实供应商经验质量、CLI和Docker仍未验收；具体结果见 [F42验收记录](acceptance-report.md#f42-浏览器模型复盘验收)。

## F43：批准协作计划的原子批量执行

Owner 从不可变协作创建回执中明确选择 1–16 个原计划任务，以每项当前 `expected_version`、`previous_execution_id` 与 `reconciliation_note` 提交。`ProjectExecutions` 在同一 SQLite 写事务内复用 `Executions._create`，任何一项版本、权限、前次执行或队列条件不满足即整体回滚；成功保存不可变批次回执，将任务映射到固定 execution ID。批次不是新的调度器，实际执行继续使用 `CLIController`、原队列、依赖检查及 `max_concurrency`；未选前置不自动入队，已入队后继仍等待前置成果获 Owner 批准。

全部入口沿用 Owner Bearer 鉴权：`POST/GET /api/workbench/collaboration-plans/{plan_id}/executions` 创建或列出批次，POST 返回 202；`GET /api/workbench/project-executions/{batch_id}` 返回原回执和本批绑定实例的任务、执行、评审、当前依赖及最新执行 ID。`GET /api/workbench/conversations/{project_id}/origin-plan` 仅支持项目会话，按项目 ID 返回原协作回执或 null，不改变源会话协作历史查询语义。

控制器在首次入队前检查 CLI 配置及控制器未关闭；全局未核查执行仍由既有调度器暂停出队；已受理的相同请求与完整 payload 精确重放返回原回执，不因后续配置改变重新执行；同 key 异内容拒绝。`POST /api/workbench/project-executions/{batch_id}/stop` 要求 `{confirm:true}`，只复用原实例取消/停止路径，不寻找或停止后来替代的最新实例。取消排队与请求停止不等于已退出，unknown 保持未知且不会自动核查副作用或启动替代执行；再次执行继续遵循既有人工核查门。

F43 是后端/API 增量。主代理报告 18 项定向测试通过（4.17s），包括真实 `tick`/线程池调度配合受控 runner 的 A/C 并发与 A 获批准后 B 自动启动；这不是实际 CLI、模型或 Docker 运行证据。主代理完整回归 551 项及 8 子用例通过（93.25s，session65228 退出0）；之后产品代码未改，仅新增容量测试2项，独立运行2项通过（1.07s），主代理复跑2项通过（1.28s）。独立审查Pass，相关57项通过（10.60s）。F44 浏览器批量入口尚未验收，不能据此标记界面或整体目标通过。

## F44：浏览器协作批量执行与恢复

`CollaborationPanel` 在已保存创建回执、源会话历史和当前项目来源回执处提供批量执行入口；仅 project 会话查询 origin-plan，普通私聊和董事会不调用该接口。`CollaborationExecutionPanel` 显示原计划任务的当前标题、范围、验收标准、版本及负责人，由 Owner 选择 1–16 项并明确确认。再次执行显示最近实例，并逐项填写结果及副作用核查说明；unknown 沿已有 reconciliation 接口读取人工声明，未核查或读取失败不允许替代执行，已核查后仍须重新确认版本和说明。

提交前按协作回执保存完整请求，固定每项 expected_version、previous_execution_id 和说明。202 后保存批次 ID，仅 GET 查询；首次明确 4xx 才能重新编辑，重试先清旧 rejected，401 和丢响应保留原 payload/key。批次明细只跟踪回执绑定的 execution ID，显示任务版本变化、非最新实例、等待前置及 Owner 评审；awaiting_review 不等于批准。依赖沿原队列自动等待，未选任务不会自动执行。

本批停止另行勾选确认，先持久保存停止待核对标记再 POST；响应未知后仅 GET 刷新，不盲目重发，也不操作替代实例。独立审查发现的非项目 origin 查询及已核查 unknown 被封锁问题已修复，结论 Pass。主代理浏览器验证了批量并发、前置批准、请求及停止丢响应、重启401和同批恢复；实际 runner 为受控替身，真实 CLI/模型/Docker 均未调用。最终46模块构建 index-DNIGCKAo.js；具体证据及未测试边界见 F44 验收记录。

测试fixture原38539与重启31647均先确认实际句柄仍运行，再Ctrl+C退出1；最终7896无监听。正常7892旧26019在数据库无活动执行后有序停止，新69967启动健康检查200并回读index-DNIGCKAo.js，新增批次表为空、任务执行0、原1个unknown模型Run保留，未注入测试数据。

F44 实际提交 `02b364144b890663efc3276a2dc81c65f34818e1` 后立即推送退出1：GitHub403（suiyue1990 无写入权限）。远端回读退出0但 remote_head=null；已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f44-delivery-result.json`。本地验收和提交不代表远端交付。

## F45：未知模型请求的核查与精确范围门禁

`model_run_reconciliations` 保存 Owner 对 unknown 模型 Run 的不可变声明：原 run_id、request_id、attempt、requirement_version、本地请求已停止及供应商影响已核查两个 true 标志、说明和时间。更新、删除及替换均拒绝；声明不等于供应商成功或机器退出证据，不改原 Run 的 state、model、usage、attempt，也不回填或重置 RPM 账目。相同声明精确回读，异内容冲突拒绝，后续停用身份或模型配置不影响已有回执核对。

门禁复用 Runs：普通回复/协作提案按 conversation_id、source_message_id、agent_id 和 kind 分别约束；复盘按目标 scope、scope_id 和 source_execution_id 约束，不把同一共享摘要下不同任务成果互锁。范围内未核查 unknown 阻止新 key 创建；原 key 回读优先于门禁。pending 排除被挡住的 queued，避免常态轮询消耗 RPM 或饿死无关请求；claim 在事务中再次核查。被挡请求仍是 queued，在列表可见且可取消。声明可能放行已经获 Owner 授权的 queued；原 unknown 从不重新出队，新意图仍须 Owner 显式提交新 key。

控制器在共享状态锁下拒绝为仍由 futures、unsettled 或 uncertain_submissions 持有的请求首次保存声明。线程提交结果不确定时，即使表中已记 unknown，也保留所有权与并发槽；有可靠 Future 的请求必须等其结束并由控制器移除；没有可靠 Future 绑定的提交异常保守保留至完整关闭线程池和服务，重启后再核查，不能仅凭本地状态字符串解除。控制器关闭时拒绝新的声明，既有精确回执仍可读。

Owner Bearer 鉴权覆盖 `GET /api/workbench/model-run-reconciliations/pending` 和 `GET/POST /api/workbench/runs/{id}/reconciliation`。POST 严格六字段：request_id、attempt、requirement_version、local_request_stopped、provider_effects_checked、note；布尔项必须为 true，版本为非 bool 正整数，说明最多2000字符。待核查列表含 kind 及复盘 scope/scope_id 供导航，不含冻结输入正文。F45 是后端/API 增量；F46 界面当前仅草稿及 typecheck，未完成浏览器验收。

F45 主代理完整回归570项及8子用例通过（97.96s，session88107退出0）；独立QA15项通过（2.19s），审查Pass。该结果包含F45新增测试，不扩大为F46浏览器或真实供应商验收。

F45 实际提交 `5b37cad73e97607f8f8f6709e779d060036a748d` 后立即推送退出1、GitHub403（suiyue1990 无写权限）；远端回读退出0且 remote_head=null。已直接核实外部 `f45-delivery-result.json`，F46草稿未包含在此提交；远端交付仍阻塞。

## F46：浏览器模型未知请求核查与显式新意图

浏览器统一展示普通回复、协作提案、模型复盘的待核查 unknown，声明只记录本地请求已停止与供应商影响已核查，不把原模型结果改为成功。声明提交前保存完整原请求；首次明确400可修正，重发前清旧拒绝标记；401、服务重启、201受理丢响应及GET503均保留原key。读取原声明只核对事实，另一请求已为同Run保存声明时，须明确接受该不可变声明，不能悄悄覆盖冲突。

核查后可以准备新的意图，仍要按原产品路径重新选择来源/身份或候选/成果并明确确认，新key创建新的Run；原unknown不重发。普通回复仅新Run发布一条消息，规划只生成待导入提案，复盘只生成待审批记忆候选、目标批准版本不自动变化。F45服务端精确scope门禁与当前控制器持有检查始终有效。

独立typecheck与静态Ponytail审查Pass；主代理构建47 modules、index-DdLyA6xU.js，并在IAB 1315×1272、独立7896 fixture验证三类型核查及各一次明确新调用。直接只读验证表明三seed完整Run未变、三声明唯一、三个新completed与三次本地HTTP模型调用一一对应，旧unknown调用0，无额外消息。附加QA三项已通过：损坏声明存储即使GET已有回执仍不清除或解锁；规划/复盘原pending遇GET503保持，恢复GET后须明确release；全过程零POST。完整证据和限制见F46验收记录；真实供应商、CLI及Docker均未调用。

最终补充：主代理模型核查后端/控制器/API定向 **17 passed（4.39s）**，不记作新全量。正常7892服务原session69967先确认仍live再Ctrl-C退出1，保留browser-state由session19278重启；health200并回读index-DdLyA6xU.js，正常库仍只有历史unknown模型Run1、任务执行0、批次0，未作fixture写入。最终测试fixture session74866先poll确认live再Ctrl-C退出1，已停止；主代理重新运行f46_verify并落盘最终证据。真实供应商/CLI/Docker及整体目标未验收，F46 已本地提交，立即推送遭 GitHub403，远端回读为空；详见下方实际交付记录。

F46 实际交付记录：已直接读取外部 `H:\item\CorpPilot-test-evidence-20260906\f46-delivery-result.json`，提交 `18b5c481bd2cbb5d07cd21e953bf04f337673bb9` 成功后立即推送退出1，GitHub403：suiyue1990 无权写入 xiaoyangtx996/CorpPilot；远端回读退出0、`remote_head=null`。该记录报告提交后工作区干净，不能据此称远端交付完成。主代理已通过 git show 核实该提交为10文件、195行新增/20行删除。
## F47：Owner 授权的同群 Agent 单次评议（后端/API已验证）

复用 Runs、ReplyController 和既有模型子进程、并发/RPM/活动队列上限，不建立新的调度器，不改 provider。新增 `peer_review_requests` 保存不可变授权 payload、source_run_id、source_snapshot 和 target_instructions；授权时冻结源消息正文与目标自身模板指令，Run及回执同事务创建。普通reply列表排除peer，避免旧界面把评议当普通回复重试。

Owner Bearer 鉴权覆盖 `POST/GET /api/workbench/conversations/{id}/peer-reviews` 与 `GET /api/workbench/peer-reviews/{id}`。POST严格四字段 agent_id、source_message_id、request_id、confirm，confirm必须严格为true；返回Run字段与request_payload、source_run_id。读取历史不发起模型调用；取消与人工核查继续复用通用Run端点。

只允许未归档board/project中的不同启用成员A与B。源消息须证明由同会话A的completed Run发布：source_run.reply_message_id与消息ID一致，Run的conversation_id/agent_id与消息相符。入队、模型快照与发布事务复查来源和双方当前权限；撤权后不得发布。冻结输入只含选定source本体和B自身指令，不默认共享原Owner目标、其他聊天历史或A的私有记忆。普通reply的Owner来源校验保持不变。

同会话request_id跨reply/planning/retrospective/peer_review冲突拒绝；精确原请求回放优先返回原回执，不因配置关闭或后续撤权创建替代Run。F45为peer_review独立分类，unknown按类型、会话、源消息和目标Agent精确阻断新建及queued领取；已保存核查不改写原unknown，原请求不重发。每次Owner授权只产生一个目标Run，不自动续轮；允许Owner之后另行确认让C评议B。后端/API实现及独立审查已通过；F48前端已通过本地fixture浏览器验收，详见F48验收记录。
主代理定向25项通过（7.61s，当时尚未加入冻结角色单用例）；随后新增冻结角色测试单项通过（0.22s）。完整 pytest tests/ -q 为 **596 passed、8 subtests passed（105.83s）**，session37270退出0，包含冻结角色和HTTP严格字段用例。独立QA/TechLead/Ponytail审查Pass，四文件定向41项通过（9.88s），另复跑冻结角色1项通过（0.22s）；这是分次验证记录，不相加成42个不同用例。
原生控制器经实际本地HTTP provider子进程完成A回复→B评议，各一次调用，8并发同key仅1个peer Run；来源/目标撤权、未知核查新key和重启精确回读均验收。本增量不验证真实供应商、CLI或Docker，F47已本地提交并立即尝试推送，403阻塞；实际记录见下文。


F47 实际交付：直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f47-delivery-result.json`，本地提交 `a4f7875c73bc29d0d1570b4e3aaaf9f17f2562c4` 成功，11文件、461行新增/19行删除；随即推送退出1，GitHub403（suiyue1990无目标仓库写权限）。远端回读退出0但 `remote_head=null`，因此远端交付仍阻塞；F48五个前端文件未包含在F47提交中。

## F48 评议前端与请求恢复（本地fixture已验证）

新PeerReviews组件通过App全局入口及ConversationMessages群内Agent消息入口接入；api.ts增加专门request/run类型，ModelRunReconciliation增加peer_review标签。普通ReplyRuns保持不变。请求使用独立sessionStorage键corppilot.peer-review-pending.v1，保存原conversation、source全文和四字段payload，附加已受理run_id；不创建通用恢复框架。

POST响应须严格匹配原会话、来源、目标、请求键及payload，但仅持久化Run编号，不设置已核验current。从列表找回编号也必须另GET /peer-reviews/{id}精确回读，才能展示可释放状态。串行读写及失效回调检查防止旧会话结果污染新选择；存储损坏保守锁定，不清原请求。unknown核查回调按record.run_id关联，释放阶段持有写锁并重新GET原声明；失败清除该Run的checked许可，原请求继续保留。核查、列表轮询均不自动发起新模型调用。

原生dialog提供Escape/关闭与焦点恢复；UI明确一次调用、仅分享选中正文、排队可取消和完成后原群刷新。typecheck及48modules构建index-BDbMUDFL.js通过，之后无代码修改；两恢复边界及正常调用、重启401、取消已通过本地fixture浏览器验收，详见[验收记录](acceptance-report.md#f48-群内评议界面验收本地fixture验收pass)。本轮fixture不记录provider输入，输入隐私证据沿用F47用例，不扩大新增声明；F48已本地提交，立即推送403阻塞，远端未确认；实际记录见下文。


F48实际交付：已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f48-delivery-result.json`，本地提交 `43be70154bc7d373932a0cd5f14ce672fbfef48f` 成功，9文件、179行新增/13行删除；立即推送退出128，GitHub返回403（suiyue1990无写权限），远端回读退出0但remote_head=null。记录中working_tree_status为空，提交后工作树干净；本地验收与提交不等于远端交付成功。


## F49 原子创建并启动接口

新增POST/GET `/api/workbench/conversations/{source_id}/project-launches`及GET `/api/workbench/project-launches/{id}`，沿用Owner Bearer鉴权。POST严格为`{plan, confirm_execution:true}`，plan为现有完整六字段Collaboration计划，以plan.request_id作为源会话内幂等键；返回202不可变组合回执，含原request_payload、collaboration及batch。动态状态与停止复用project-executions端点。

ProjectLaunches在一个BEGIN IMMEDIATE内调用提取的Collaboration._create(db)和ProjectExecutions._create(db)，创建成员、共享消息、任务、依赖、固定v1首轮执行及两个回执，最后写不可变project_launches记录；任何写入或容量/权限校验失败全部回滚。事务内不调用模型或CLI。既有仅建群和批次入口语义保留，旧仅建群同键不得隐式升级，启动精确回读先于配置及当前权限检查。

CLIController的launch_lock仅串行新启动准入与closed标记，关闭等待线程池不持有该锁。复用原有执行队列和调度，不增加第二队列；新任务仍需已有execute权限并受全局队列容量限制，前置Owner审批及unknown边界不放宽。费用硬门后续须验证全部模型请求的预留和计费边界，不能以超时或一次CLI attempt代替金额上限。


F49实际交付：直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f49-delivery-result.json`，提交 `6e68e864ed6804972f13cd03e4cc92c1fa32ca3b` 成功，12文件、553行新增/67行删除；立即推送退出128、GitHub403（suiyue1990无写权限），远端回读退出0但remote_head=null。提交后仅三个F50前端文件未提交，F49本地验收和提交不等于远端交付。

## F50 一次启动前端与固定批次恢复（本地fixture验收Pass）

CollaborationPanel复用完整计划预览，api.ts增加ProjectLaunchRequest/Receipt类型；独立sessionStorage键corppilot.project-launch-pending.v1冻结source_id、operation=launch、完整payload及已受理launch_id。保留F36原创建键和语义；新创建/启动不能覆盖任一全局pending，F44批量pending只提示恢复，不全局阻塞无关新项目。

POST只精确核验并保存启动编号，必须再GET /project-launches/{id}核对原计划、会话、coordinator/member集合及唯一task/execution/request绑定后，才允许明确结束原请求核对。首4xx可编辑；重试先清旧rejected；401、丢响应、GET503保留完整原key；损坏存储保守锁定。操作种类冻结，不把旧仅创建授权升级为执行授权。

CollaborationExecutionPanel仅增加可选initialBatchId定位固定首批，已有同plan pending优先（即使尚无batch_id），损坏存储不打开替代批次。停止确认保存detail.id，切历史清许可，stop调用再比当前ID，防A确认用于B；未知停止响应仍只GET核对。未新建调度或巨型dialog。静态/typecheck及48modules构建index-Bl2XnWmG.js通过，浏览器主流程、八项边界和同库401恢复已通过，非自动规划、无审批交接或费用硬门。


## F51 固定执行的明确交接许可

ProjectLaunches请求新增可选严格布尔confirm_handoff。缺省/false沿用Owner前置批准，true在原创建事务末尾写execution_handoff_permissions，记录下游execution、前置task、固定upstream_execution及launch外键。复用原队列和回执，权限边不可更新、删除、替换；任一边写入失败回滚整个launch。完整payload幂等比较防止旧key扩大授权；迁移只建空表，不授权旧数据。

ready_inputs在claim/check_bound中接收具体execution身份。授权边要求前置当前版本和最新尝试仍是原绑定、状态awaiting_review、有捕获成果、未被Owner拒绝；递归祖先和既有execution_inputs仍逐层核对。即使替代上游被Owner批准，也不能换绑原批次。Tasks和批次详情按当前/固定execution展示handoff_authorized，重试不继承。

artifacts.input_snapshots仅在Executions.snapshot传入downstream_execution_id时核对固定输入和交接许可，继续验证非空成果、真实BLOB长度、SHA-256及总量上限。没有裸allow_unapproved参数。memories/retrospectives原单字段调用保持默认Owner approved要求；审查发现的dependency_task_id KeyError已通过短路读取修复及原场景回归。

前端原pending保存完整confirm_handoff，恢复/回执不一致锁定，选项改变撤最终确认。普通任务及依赖页面明确交接就绪不等于Owner批准。该许可不会启动额外Run、扩工具权限、跳过unknown核查或自动更新个人记忆。

## F52 持久目标授权连接规划与启动

GoalExecutions在SQLite保存不可变原授权、唯一planning_run_id、稳定launch_request_id，以及单向停止标记和启动关联。Planning._create接受现有事务，使规划Run与目标授权同时提交或回滚；普通Planning.create仍自行开事务。goal规划上下文替换为Owner明确shared_brief，校验max_tasks及候选范围，并固定最终共享摘要。普通提案历史与目标执行记录分开读取。

复用ReplyController的单控制器生命周期、原模型队列和CLIController.launch_project；不增加工作流引擎或后台线程。每轮先处理目标停止恢复与计划衔接，再调度CLI。停止先落持久标记，再取消仍排队的原规划和停止固定首批；失败清理可在重启后继续，运行中或未知模型状态仍独立呈现。启动与关联之间中断按固定请求恢复，异常不另建模型Run或CLI尝试。

Owner鉴权沿用全局Bearer边界。接口为GET/POST `/api/workbench/conversations/{id}/goal-executions`、GET `/api/workbench/goal-executions/{id}`、POST `/api/workbench/goal-executions/{id}/stop`（严格`confirm:true`）。创建严格使用request_id、source_message_id、agent_id、candidate_ids、shared_brief、max_tasks、confirm_execution和confirm_handoff八字段。回执同时提供原规划和固定launch/batch；本功能暂未增加浏览器入口、货币硬预算或第二套桌面后端。

## F54 本机资源准入

CLI设置增加resource_admission_enabled（默认false）、host_reserve_memory_mb（默认1024）、local_worker_memory_mb（默认1024）和local_worker_cpus（默认1）。旧配置按默认值读取，不批量改写已有数据。Owner可在CLI设置保存并从本机资源状态查看实际可用内存、逻辑CPU及活动预约。调整设置可能使此前已授权的排队工作恢复调度。

Windows通过GlobalMemoryStatusEx和os.cpu_count只读探测，无额外进程或依赖。启用时，新实例CPU与活动预约之和不得超过逻辑CPU；内存按活动完整预约+新实例预约+宿主保留不得超过当前实际可用内存。Docker沿用每容器cpus/memory设置；本地使用明确的启动预约值。实际可用内存已经反映部分活动用量，继续计入完整预约是保守余量，可能少启动实例；不是动态RSS计费或本地进程OS硬限制。

无论准入开关状态，每次claim成功后都保存启动时预约，报告成功落库后才释放；修改设置不缩旧预约，报告写失败继续占用。资源不足或探测失败只阻止新启动，原queued记录及实例标识不变，后续tick重新核对；不杀死活动执行、不新建替代Run。沿用unknown全局阻塞、依赖授权及最大并发。重启先把原活动执行恢复为unknown，需原核查门后才能再次启动。

GET /api/workbench/cli-runtime新增resource_admission，含enabled、available_memory_mb、cpu_count、reserved_memory_mb、reserved_cpus、message；enabled=null表示配置无法读取。状态读取也实时探测，不启动程序。此能力只控制本机CLI启动数量，不涵盖Docker虚拟机可用容量、实时CPU负载或货币预算；Docker实际限制继续由容器参数执行，真实双Worker验收另列。
