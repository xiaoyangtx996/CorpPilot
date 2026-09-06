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

## F25 Owner 评审服务验收

独立QA22项测试通过，真实SQLite及HTTP覆盖：并发8个互相竞争的决定仅一个保存；同键同内容在需求更新、撤权及服务重开后仍回读历史；不同请求/决定/说明不能覆盖；SQL UPDATE/DELETE/REPLACE拒绝。当前版本、最新执行、启用/成员/工具/归档边界及非待评审状态拒绝；批准要求完整非空成果清单且内容、大小、hash校验有效，拒绝允许空成果或内容损坏。HTTP不启动CLI/模型，未提供修改评审路由。

评审是独立Owner决定，不伪造执行退出结果或改写需求历史；历史批准仅绑定当时的execution/version。此增量不作为浏览器批准或真实模型交付通过的证据。

F25主代理最终全量回归：309 passed、8 subtests passed（52.17s）。

## F26 浏览器成果与 Owner 评审

- 前端实现：prompt_push_check；主代理负责契约、失败状态审查及真实浏览器验收。复用现有任务窗口，无新依赖。代码审查修正读取失败隐藏已保存决定、未知结果误称未评审、未核对版本误称历史三处状态文案问题。
- 独立 QA 服务 7896、断线代理 7897，使用外部固定成果样本；CLI 和模型禁用，未产生真实模型调用。浏览器从会话任务进入执行记录，展开成果，点击下载，填写说明并批准；空成果可拒绝。真实 HTTP 回读确认批准/拒绝均保存，各任务仅一次执行；下载 attachment/octet-stream，字节 SHA-256 与快照一致。
- 代理在实际保存201后丢弃首次响应，随后GET review持续503。刷新同标签页仍恢复请求 a553f1ce-b67e-462c-9a9d-d5b2f20d5e94；同键POST核对回读原批准，未新增决定。服务端已保存结果不会被读取失败隐藏。
- 任务 a1f83e61-f5d7-4f7d-8f46-7878d3741993 经真实PATCH由v1变v2，浏览器仍显示v1批准并明确“历史决定，不代表当前任务已通过验收”。
- IAB真实浏览器标题和地址正确、页面非空、无框架错误覆盖；桌面截图与DOM验证。未采集浏览器控制台日志，本增量尚未单独复测移动视口。预期断连与503为故障注入。
- 根代理最终 TypeScript/Vite 构建通过，38 modules；F25定向回归22 passed（2.91s）。后端本增量无改动，沿用F25全量309项及8子测试证据。
- 测试脚本、manifest、数据库位于 H:\item\CorpPilot-test-evidence-20260906，未进入仓库；这些证据不代表真实CLI模型或Docker验收通过。完整目标仍未完成。

## F27 前置任务调度与失效保护

主代理全量325 passed、8 subtests（55.01s）。QA随后补充边界与历史用例，新增依赖测试独立22 passed（3.42s），主代理复跑22 passed（3.32s）；补充用例未冒称包含在此前全量计数中。

真实SQLite/HTTP测试覆盖同会话、无环、两并发反向边只有一边成立、32直接边允许/33拒绝、1000祖先允许/1001拒绝、依赖变更递增版本、普通需求修订保留前置清单、历史依赖可读取、旧排队执行过期。前置未批准或被拒绝时后续保持queued；当前最新批准才可claim，冻结输入不可覆盖。上游改版或新attempt在排队/运行/待评审各阶段均阻断过期结果；A重新批准后B旧输入仍不能使C启动，B重新执行批准才解除。等待前置的队列不会饿死独立任务。

Ponytail/QA独立审查Pass，无新调度器或依赖库。CLI替身只验证调度，不证明真实模型消费前置成果；成果文件输入传递及浏览器依赖入口仍待实现。远端推送权限阻塞和完整目标未完成状态保持。

## F28 前置成果输入传递验收

主代理全量358 passed、8 subtests（55.93s）；此前主代理跨层集成8 passed、子代理落盘及既有CLI定向23 passed。input_materialization负责cli.py和落盘测试，主代理负责数据库/控制器集成；两侧交叉审查Pass。

真实文件和SQLite配合进程边界替身验证：仅直接依赖的已批准完整快照进入inputs；原路径/来源/大小/hash进入prompt映射，二进制data不塞入prompt；修改下游副本不影响数据库原成果及第二工作区；.codex/config.toml与AGENTS.md原名不会变成工作环境文件。仅当前执行artifacts目录产出进入新成果，输入不自动重发为输出。内容损坏、批准后多出文件、批准清单不一致、输入总量超限、已有工作区或文件写入失败均不启动CLI；上游改版在输入读取前阻止启动。任意实际runner异常仍保持未知，与明确准备失败区分。

未调用真实模型；进程边界替身只证明参数、文件和状态链路，不能作为真实模型协作或Docker隔离证据。浏览器依赖入口与完整目标仍未完成。

## F29 浏览器任务依赖验收

- dependencies_ui实现依赖面板、任务卡入口、历史清单与类型；主代理实现执行窗口前置状态、当前版本变化拦截及集成验收。TypeScript/Vite最终构建39 modules通过（index-DeFxH1g7.js），无新增依赖；后台未改，沿用F28全量358及8子测试证据。
- 独立QA服务7896与Vite StrictMode7893：QA下游任务v1无依赖→v2依赖已批准甲显示前置满足→v3追加未批准乙显示等待；历史逐版显示[]、甲、甲+乙及稳定ID。甲反向依赖下游被400拒绝，版本仍v1，保留草稿可明确重编辑。
- 外部HTTP更新任务范围后，旧v4清空请求409，保留0项草稿，读取v5后明确重编辑保存到v6；HTTP回读范围仍为“QA 并发修改 v5”，没有覆盖新版字段。
- 断线代理7897：上游实际保存后丢弃PATCH响应，随后GET dependencies持续503。刷新同标签页仍恢复v1原选择，旧版本重试409且仍保留；直接上游回读仅v1/v2两版，无重复修订。恢复转发后显示当前选择匹配但不宣称原请求确认，用户明确对照再编辑；后续添加乙形成v3。
- 执行窗口显示等待前置验收、确认后排队且不代表任务完成；CLI禁用时未启动。归档后面板仍可读，三个复选框、清空和保存均实际disabled。四任务执行总数仍1（仅预置甲的固定记录），未调用模型或CLI。
- 真实IAB页面身份/非空/无框架错误覆盖、桌面截图、DOM与交互验收通过；本增量未采集控制台日志、未另测移动视口。断连/503是预期注入。外部脚本与数据在H:\item\CorpPilot-test-evidence-20260906，未进产品仓库。
- 完整自动组队、真实模型、双Docker和其他目标能力仍未验收；本增量只证明浏览器依赖配置闭环。

## F30 批准记忆与冻结上下文验收

- 主代理全量 pytest：381 passed、8 subtests passed（59.91s）。独立 memory_integration_review：记忆核心 21 passed、既有 CLI/执行/评审 58 passed、新 HTTP/CLI 集成 2 passed；Ponytail 与权限审查 Pass。
- 临时 SQLite 验证：审批前正文为空；批准才递增；竞争候选仅一版本胜出；失效来源不能批准、可拒绝；来源跨身份/群、过期需求、停用/退群/归档/撤 execute、未批准或损坏成果均拒绝。重启与改名不丢记录，回滚新增修订，相同请求返回原响应。
- 真实 loopback HTTP 验证候选/决定/历史/回滚端点、跨来源拒绝、旧版本 409、禁止直接 PATCH 正文、服务重启后请求重放。SQL UPDATE/DELETE/REPLACE 无法覆盖审计和执行绑定。
- 真实 claim 和 CLI 边界替身验证：本身份及本群批准版本进入 prompt，未批准候选不进入；运行中批准/回滚后旧执行保持旧版，新执行读取新版；私聊不读取群共享记忆，缺绑定拒绝用当前版代替。修正测试替身补齐已有 runner reason 字段后全量通过，无生产错误被掩盖。
- 未调用真实模型或 Docker；本增量无前端修改，无浏览器记忆入口验收。个人记忆应用层选择不等于同用户 OS 安全隔离；整体目标保持未完成。

## F31 浏览器记忆验收

- 最终 TypeScript/Vite 构建通过：40 modules，index-r62MCKSu.js，无依赖变更。后台沿用 F30 全量 381 项及 8 子测试证据；此增量仅改前端。
- 独立数据 f31-memory-state，IAB 7893 Vite StrictMode、7896 构建页面，7897 故障代理。Browser plugin 未提供，使用可用 CUA Playwright；页面标题/非空/无框架错误覆盖、桌面截图与真实交互通过。未采集独立控制台日志、未另验移动视口。
- 个人候选提交仍 v0，批准后 v1，浏览器回滚到 v0 新建 v2，历史正文与决定保留。项目候选批准产生 v1，与个人范围分别存储。来源从批准任务下拉选择，无手输 ID。
- 7897 首次候选 POST 已由上游保存后断开响应，GET memories 持续 503；刷新恢复相同 request_id 0f746fb4-add6-4ef7-a14a-0f0a7d02a411 和原全文，同键重试获得确认，仅一条对应候选。GET 失败保持未核对，修正了误称“暂无候选”及假定当前 v0 的提示。
- 项目草稿基于 v1 时外部 API 回滚到 v2，旧草稿提交 409，全文保留；明确对照重新编辑后提交基于 v2 候选。归档后提案、采用版本、回滚 disabled，仍可拒绝未决定候选且不递增记忆版本。批准后已决定选择会清除，不再误提示可拒绝。
- 外部 f31_verify.py HTTP 回读：个人 1 候选/2 修订；项目 2 候选（批准、拒绝）/2 修订；任务执行始终只有初始固定样本 1 条，active_requests=0。无模型/CLI调用，不冒充真实智能复盘或 Docker 验收。
- 测试代理、Vite、测试 API 均已确认终止；空闲的正常 7892 服务重启加载 F30 后端与 F31 构建，保留正常数据库。自动复盘候选、Skill 发布与其余完整目标仍待完成。

## F32 Tauri 迁移文档验收

文档对照 api.ts、ExecutionReview.tsx、server/controller/cli_controller/provider/store/cli/process_tree 和各 pending UI 实现。技术方案明确暴露差距：下载绕过api、冻结sys.executable、迁移先于控制器锁、health无法认证、sessionStorage不保证桌面重启恢复。Tauri externalBin/capability/CSP/Windows安装资料以官方页面为准，链接保留在迁移文档。

本增量仅文档和链接，不修改运行代码，不重复执行F30全量回归或F31浏览器测试来充当桌面验收；桌面构建、安装、关闭/重启及升级回退均尚未执行。Docker绝对路径CLI再次只读检查，docker_engine管道仍不存在。文档路径与diff检查通过；独立审查结论记录后才提交。

F32 独立 tauri_boundary_review 审查 Pass；补清统一入口锁顺序以及停止发出后不能取消停止。所有新文档本地链接校验通过，仅规划交付。

## F33 未知执行核查验收

子代理 execution_reconciliation 实现核查存储与13项测试，主代理集成恢复门和3项跨层测试；子代理对根集成只读审查Pass。临时SQLite和loopback HTTP覆盖：不可变记录、并发唯一保存、同键重放、历史版本及改名/停用后的记录核对、仅unknown可核查、严格两项true声明、原attempt/version冲突409、重启后回读、仍持有执行线程时拒绝。

控制器边界替身验证：未知项未核查前已授权任务保持queued；核查后仅该queued任务被派发一次，原unknown没有被改写或重跑；迟到成功回调不生效，同任务新尝试仍需最近执行引用和说明。多个未知项只核查一项不会解除剩余阻塞。未调用真实CLI/模型，不将人工声明当作机器停止证明。

首次全量396通过、1失败：跨Origin负向请求出现Windows连接中断；现象与服务提前拒绝、未读正文的关闭竞态一致，未将具体TCP成因视为已直接证实。定向复跑3项通过；测试改为无body请求并明确断言跨来源拒绝及记录仍为空，避免未读body关闭竞态，不改变生产权限检查。最终全量结果追加如下。

F33 最终全量：397 passed、8 subtests passed（64.36s）。负向用例修正独立审查Pass。此增量无前端代码修改，未用浏览器测试替代后端恢复证据；UI核查入口和真实孤儿进程/容器验证仍未完成。

## F34 浏览器未知执行核查验收

reconciliation_ui 实现，corppilot_backend_review 独立源码/Ponytail审查Pass；主代理亲自浏览器验收。最终构建41 modules，index-C_CSGg17.js；复用现有dialog和API，无依赖变更。本增量不改后端，沿用F33 397项及8子测试证据。

独立数据 H:\item\CorpPilot-test-evidence-20260906\f34-reconciliation-state；f34_fixture.py、f34_conflict_fixture.py、f34_drop_proxy.py、f34_verify.py 为外部QA证据。IAB7896最终构建页面，7897断线代理；Browser plugin未提供，使用CUA Playwright。标题/非空页面、无框架错误覆盖、桌面1315×1272截图和交互通过；未采集独立控制台日志或移动视口，不将截图当作完整产品验收。

普通核查需两项确认和依据；记录仍unknown、exit_code=null。首次POST上游201后断开，核查GET持续503，刷新恢复请求db75d3b0-651c-424f-9c14-2ce1b484999b并同键核对成功，没有新尝试。失败读取不显示空态；任务窗口也保持阻断。已核查记录在正常任务窗口进入CLI配置门，不再被unknown历史永久阻断。

任务升级v2、会话归档、身份停用后，浏览器成功对原v1执行保存声明。随后新增第四条固定样本验证并发：另一个Owner声明先保存，浏览器POST被拒绝；成功读取另一声明并明确确认后结束本地等待，没有覆盖或宣称原请求获接受。发现并修正成功后旧错误残留及后续GET失败丢失已确认展示的问题。

API服务确认终止后重新启动，f34_verify.py重启前后均通过：四条执行全部unknown、退出码未确认、attempt/version均原值，四条不可变声明，没有新执行，CLI disabled且active_requests=0。固定样本没有实际进程，不代表已完成真实CLI/双Docker/自动恢复验证。

F34 测试API与故障代理均通过实际会话句柄确认退出。正常7892服务先核对聊天/CLI活动均为0，再有序重启加载F33后端与F34静态页面；原数据保留，CLI核查待办为空、控制器active_requests=0。

## F35 协作组建服务验收

后端新增collaboration.py，Tasks.create仅提取可复用现有db的内部方法，HTTP接入来源会话的collaboration-plans及按收据ID读取。计划和创建收据持久不可变，业务任务继续使用原tasks/task_revisions/task_dependencies；没有第二套任务状态或调度器。

定向30项（协作21、原Tasks6、跨层HTTP3）通过。真实loopback HTTP验证从私聊创建项目、精确成员、仅共享摘要、新任务v1与依赖、无运行副作用、跨来源拒绝、跨会话源消息拒绝、原请求重放及控制服务重启后收据一致。记录内approved_plan保持原批准字段，可供浏览器准确核对待确认请求。

临时SQLite故障注入覆盖循环/自依赖/重复或未知key、非Owner来源、归档/停用/非成员协调者、UTF-8非法值、体积上限、空与17项任务、16项链有效、晚期第二任务写入失败整体回滚。8次并发重复请求只有一个项目；receipt的UPDATE/DELETE/REPLACE被拒绝，任务修订后创建收据不变。

执行上下文跨层测试：计划本身没有task_executions/聊天runs；另行显式创建固定执行后，snapshot只含共享摘要，不含私聊前史或原私密目标，也不读取其他身份记忆。此测试未启动实际进程，不替代真实模型与双Docker验收。前端本增量未修改，不重复构建或用旧界面宣称协作UI已完成。

F35 最终全量：421 passed、8 subtests passed（65.34s），实际收集核对新增协作21项和HTTP3项，原Tasks6项。PM/Tech Lead/Ponytail成品审查Pass，主代理复核事务与隐私边界；未完成浏览器组建和真实自治协作验收。

正常7892服务在聊天和CLI均active_requests=0后，经实际会话句柄有序停止并重启，已加载F35 API；既有会话的协作计划GET返回空列表且CLI活动仍为0。没有为正常用户数据创建测试项目。

## F36 协作编排浏览器验收

四个前端文件实现消息入口、计划表单、依赖与成员确认、全局待确认恢复、批准历史和项目导航。前后端保持F35契约；Backend本轮未改，沿用421项及8子测试历史证据。本轮TypeScript/Vite构建42 modules，index-DS6DJ8Di.js，无新依赖。

独立数据f36-collaboration-state，外部f36_fixture.py、f36_drop_proxy.py、f36_verify.py。首次fixture因猜错秘书模板路径而停止，确认实际模板president_office/roles/secretary后仅在空测试会话库继续初始化，没有删除或覆盖真实数据。Browser plugin未提供，使用CUA Playwright：7893 Vite StrictMode正常流，7897转发7896构建页面故障流。页面身份/非空/无框架错误覆盖、1315×1272桌面截图与交互通过；未单独采集console日志或移动视口，不宣称这些检查已完成。

从Owner目标消息打开时shared_brief为空；填写明确摘要与开发/测试负责人两任务，循环依赖阻止提交。后台临时停用选定负责人使首发400，HTTP回读无项目残留；明确重新编辑保留全文、成员及依赖，重新确认后创建3成员/2任务项目，直接打开的新群仅有共享摘要，原私聊两条历史不复制。

第二计划先400，恢复成员后按原请求重试：上游事务201已提交，代理断开响应且后续协作历史GET持续503。修正并实测每次发送清除旧rejected标记，断线后不再提供重新编辑/替代项目入口。刷新、切换到另一项目、源会话归档后，全局恢复仍使用原source_id与请求f57ee28c-a121-47c7-934e-2ee28439d029及全文；同键POST确认后回执保留，GET失败不冒称空历史，仍能打开已经创建的项目。

第一项目改名后，归档来源的批准历史仍显示原名称与原计划，打开动作导航到同一已改名项目。重启测试API前后f36_verify.py均Pass：恰好2个项目/3任务，所有任务v1及实际依赖与批准计划一致，原源会话成员不变、消息仍2条，未建执行或模型回复，CLI与model均disabled且活动0。固定测试不代表模型提案、技能路由、真实CLI或双Docker已验收。

F36测试API、Vite和故障代理均经实际会话句柄确认退出；正常7892服务直接读取新静态构建，HTTP页面已回读index-DS6DJ8Di.js，本轮无后端变更故未重启正常服务或写入测试项目。

## F37 模型协作提案后端验收

复现命令（仓库根目录）：`.\.venv\Scripts\python.exe -m pytest tests/test_workbench_planning_api.py tests/test_workbench_controller.py -q`、`.\.venv\Scripts\python.exe -m pytest tests/test_workbench_planning.py -q`；完整回归使用 `.\.venv\Scripts\python.exe -m pytest tests/ -q`。

task_execution_ui 负责提案后端；主代理集成 Runs、控制器和 Owner API；corppilot_backend_review 负责设计审查，verify_prompt_push 独立成品静态审查 Pass；verify_history_scope 负责 Doc/PM 文档核对。提案沿用聊天队列的 Run ID、并发/RPM和活动上限，未增加第二套调度器。

Owner 请求显式传入协调人、本会话 Owner 目标消息及1–100个启用候选白名单。仅冻结候选公开字段并结合目标消息和协调人指令构造上下文；执行前复查全部候选，完成前复查实际选中成员及协调人/会话权限。严格 JSON、候选范围、1–16任务与DAG校验后才保存提案。成功只形成提案和 completed Run，普通聊天列表不展示该请求，不写聊天答复、项目、任务或CLI执行。缺配置排队等待、queued取消复用Run入口、running重启unknown，失败和未知均不自动重试。

主代理实际执行：规划API与控制器定向 **8 passed（10.64s）**；此前规划模块与既有Runs定向 **23 passed（2.93s）**。完整 `tests/` 回归 **440 passed、8 subtests passed（73.50s）**，此时包含规划15项与新增API4项。

全量通过后仅追加5条故障/边界测试，生产代码未修改。主代理最终规划模块定向 **20 passed（1.14s）**：补充SQLite元数据INSERT失败无Run残留、completed状态UPDATE失败时proposal为NULL且Run保持running并在恢复后成为unknown，以及双节点循环、17任务和超过100候选。最终collect-only为 **445 tests collected**；追加测试后没有再次执行全量，不将收集结果记为完整445项通过。

上述证据为后端/API及注入模型边界验证，未调用真实供应商、CLI或Docker。F37未改前端，不重复F36构建/浏览器路径来替代新提案界面验收；提案浏览器入口、导入F36后的Owner再次确认仍待实现，不能将模型建议标为approved_plan或宣称自治协作完成。提交与远端交付结果以台账中的后续实际回读为准。

正常7892服务加载F37前，主代理确认旧会话22073仍运行、聊天/CLI活动均0且error为空，再Ctrl+C退出（退出码1）。通过SQLite backup保存外部快照 `H:\item\CorpPilot-test-evidence-20260906\f37-before-108197c5-be85-46f9-bcda-1010550a9e7d.sqlite3`，integrity_check为ok。新会话61822启动后health为200，新提案GET为200且返回空列表，聊天/CLI活动仍0、error为空；未向正常数据创建提案或调用模型。

F37提交 `502b96202d3b6692b4d204c2db2b72f9d9f9f34d` 后立即推送，GitHub以403拒绝suiyue1990，目标远端分支回读为空。外部 `f37-delivery-result.json` 记录本次结果；本地验收与提交不代表远端交付完成。

## F38 模型提案浏览器验收

本增量完成消息/全局提案入口、候选与模型调用确认、原请求持久恢复、202后按Run ID读取，以及completed提案审阅和导入F36再次确认。独立成品审查Pass；主代理最终typecheck及Vite构建通过，43 modules、index-Dx-oq0Yc.js。未修改后端，不重复全量；沿用F37完整440项及8子用例、后加5条后的规划20项定向证据，445仍仅为收集数。

构建复现命令：在 `frontend` 目录分别执行 `npm run typecheck` 和 `npm run build`。隔离数据与恢复核验使用仓库外的 `f38_fixture_server.py`、`f38_drop_proxy.py`、`f38_verify.py`，不向正常用户数据库写入测试项目。

独立数据库f38-planning-state，外部f38_fixture_server.py、f38_drop_proxy.py、f38_verify.py提供测试与回读。本轮Browser plugin未提供，主代理使用CUA Playwright：7893 Vite StrictMode与7897构建故障代理，后者转发7896测试API；7900为本地fixture模型。页面身份/非空/无框架错误覆盖及真实交互通过，桌面截图覆盖1315×1272和1280×720；未独立采集控制台日志或测试移动视口。

正常路径选择秘书协调人及后端/测试候选，显式确认后仅产生1次本地模型HTTP请求，返回含依赖的两任务提案。导入F36时确认默认未选，Owner修改title与shared_brief后再次确认，实际生成3成员项目和2项任务，新群仅有修改后的共享摘要。202后刷新，从全局入口恢复原source/Run，仅GET查询，不再POST调用模型。

故障路径先因选定身份停用首发400；恢复身份后，同键 `c89f95cc-ac0c-49b3-a150-00fbb7556003` 被上游202接受但代理丢弃响应，随后GET持续503。刷新并换会话后，全局恢复保留原来源与请求，不出现早先rejected带来的编辑入口；同键POST核对成功后，completed结果即使后续GET仍503也保留。随后F36建项目的201响应也被丢弃、GET503；新模型提案导入被既有F36 pending阻止，经恢复入口核对原创建请求成功，未覆盖待确认计划。

非法模型JSON显示failed，无提案或新项目。另一路首发400后明确修改成新key，在模型禁用时保持queued，取消后刷新仍回读cancelled。测试发现已选停用身份不能取消选择，修为保留已选停用项可取消、取消后不可重新选择，主代理复验通过；另清除两处过时error/notice后完成最终构建。

f38_verify.py最终回读：4个Run（2 completed、1 failed、1 cancelled）、3次实际本地HTTP模型调用、2项目/4项v1任务、0次CLI执行；私聊前史未泄漏，测试停用的后端身份已恢复启用。7900响应为本地受控fixture，不是真实供应商生成证据，也不代表CLI/Docker或完整目标通过。

QA服务会话88766经Ctrl+C实际退出（退出码1），新会话77732启动后浏览器刷新全局入口仍显示原cancelled Run（ID前缀f152d6a7）及2 completed/1 failed历史。f38_verify.py重启前后均为同4个Run、3次本地模型调用、2项目/4任务、0CLI及无私聊前史泄漏，数据库integrity_check为ok。随后77732、代理12877、Vite80909均经实际句柄Ctrl+C确认退出（退出码1）。正常7892未重启、未创建测试数据，HTTP回读最终index-Dx-oq0Yc.js。

代理关停日志包含浏览器刷新取消请求造成的客户端写入端ConnectionAbortedError（WinError10053，外部脚本line34），不是应用HTTP500；数据库与本地模型核验通过，不宣称代理或浏览器console没有错误。未对storage损坏/QuotaExceeded进行浏览器注入，此部分只有静态fail-closed审查；console独立日志与mobile仍未验收。

本轮已验证网络受理结果未知时的同键恢复。服务端Run状态为unknown时仍保留原请求并禁止替代调用，尚无模型未知结果的人工核查/解除入口；F33核查仅适用于CLI。该恢复缺口保留为后续工作，不能宣称所有模型未知场景闭环。

F38本地提交 `c592eae` 后立即推送仍返回GitHub403，目标远端分支回读为空；外部 `f38-delivery-result.json` 为本次交付证据，本地验收不等于远端交付。

## F39 Owner API访问鉴权验收

F39交付范围为每启动随机token的Owner API鉴权、浏览器授权恢复及认证下载；Docker实现另列F40，未包含在本次独立提交与验收内，其后续状态见F40记录。全部 `/api/workbench` GET、写入与下载需Bearer授权，静态页面及基础health无需token。自动打开授权页不打印秘密；失败时宿主数据根提供临时本机入口，退出删除。本文不记录任何实际或测试token。

主代理负责后端、集成与QA；verify_prompt_push负责前端；task_execution_ui负责Tech Lead独立审查；verify_history_scope负责Doc/PM文档核对。401/fallback修正后的独立审查Pass。

本轮外部汇总证据为 `f39-access-verification.json`；以下只记录结果和非敏感标识，不复制任何访问凭据。

主代理在7897浏览器故障代理验证：任务创建已由上游201落库但响应丢失，刷新后原请求重试得到401；重新授权再沿同键恢复，task_id为 `e13e2ceb-aaea-4419-b89b-3083013d5f64`，request_id为 `fc6436a2-2b2e-488a-8e32-0289db1e7e22`，数据库始终只有1项任务。401走AccessExpiredError，不作为可抛弃原pending的业务拒绝。地址fragment及时清除，后续请求从sessionStorage取得访问凭据。

独立测试数据为1会话、1消息、1任务，以及手工预置的1执行和1成果；没有真实CLI或模型调用。浏览器点击33字节 `qa-access-report.txt` 下载无错误，HTTP测试核对附件字节一致；未回读浏览器最终保存文件，不能据此声明浏览器落盘字节或hash已核验。

最终Vite构建44 modules、index-BLeLdit0.js。主代理最终定向 **23 passed（14.11s）**，包含自动打开失败入口和拒绝data_dir处于frontend_dir内的测试。为单独验收F39，主代理将纯暂存树导出到仓库外 `H:\item\CorpPilot-test-evidence-20260906\f39-index-check`，排除所有未提交Docker修改，用原仓库 `.venv` Python在导出树执行 `-m pytest tests/ -q`，结果 **448 passed、8 subtests passed（75.04s）**。其后仅修正1行非行为注释。

此前完整工作树的 **488 passed、8 subtests passed（77.88s）** 包含尚未提交Docker修改，只作为历史工作树证据；F39独立全量结论采用上述纯暂存树448项及8子用例，不混算为Docker交付通过。

主代理确认QA服务83091停止（退出码1），95112在同一7896端口重新启动并生成新的运行期访问凭据。原浏览器刷新后旧授权得到401，重新授权后恢复同一任务，数据库仍只有1task；旧故障代理67742已确认停止，95112也最终经Ctrl+C确认退出（退出码1），本轮QA服务均已停止。过程中未把测试凭据写入文档。宿主token默认仅驻内存，只有打开浏览器失败才生成含运行期授权信息的本机HTML；owner-access文件已加入Git忽略，退出清理。

本轮不证明同一OS用户隔离或任意网络出口受控，真实Docker/供应商/CLI均未验证。Tauri Rust私有握手、受限请求桥和原生保存仍是后续规划；浏览器Bearer鉴权不等于桌面宿主身份已完成。F39实际提交 `36acd48` 后立即推送返回GitHub403，目标远端分支回读为空；外部 `f39-delivery-result.json` 记录本次结果，远端交付仍阻塞。

## F40 Docker Worker 适配与恢复验收

本增量接入固定本地 Docker/Linux 镜像、执行后端不可变绑定、受限容器参数和未知实例的状态/停止 API 与浏览器入口。原 F39 Owner 鉴权继续覆盖全部业务读写及下载。真实 Docker 镜像构建、Codex 容器运行、双 Worker 隔离与真实供应商调用尚未验收，以下注入测试不替代这些项目。

主代理在真实浏览器保存 Docker 配置并保持 disabled，显式运行实际只读 probe，结果为本地 Docker 服务或固定镜像不可用；没有拉取镜像或启动容器。受控 Worker 状态测试使用独立执行 `b011768c-5de6-4ad5-a77a-c1b642caa981`、任务 `aecb2528-cea1-4531-b4ac-9c69cc04bf83`（外部 `f40-manifest.json`）：未知状态阻断已停止声明；running 经 Owner 确认停止后仍 unknown，继续阻断；第二次停止回读 exited，但不自动保存声明。提交前将受控状态改回 running，服务端以400拒绝且浏览器保留原 pending；随后确认 absent，沿同一 request_id `2c1da3db-045c-4813-a2f4-1c2aa2184f34` 保存声明。stop helper 共调用2次，均为 fixture；真实容器、模型与 CLI 调用均为0。此证据证明前后端保护和恢复路径，不证明真实 daemon 的停止可靠性。

主代理最终前端构建44 modules，产物 `index-BQ5sRMyd.js`。首轮后端全量为508 passed、3 failed，失败原因为 Path 对象直接写入 SQLite；归一为 str 后定向10项通过。最终主代理全量 `python -m pytest tests/ -q` 为 **511 passed、8 subtests passed（78.33s）**，执行会话88456已结束、退出码0。Tech Lead独立定向 **36 passed（5.63s）**，审查结论Pass；Worker定向 **37 passed**。这些测试使用受控边界，不是511次真实容器或模型运行。

主代理确认QA服务会话12041仍在运行后，以IAB新标签页22重新进入7896，打开Execution tests的任务执行面板：仍为1任务、1次unknown执行、退出码尚未确认，Owner核查的原说明完整恢复。CLI设置仍为禁用的Docker后端，原固定测试镜像、模型/密钥环境变量名和CPU1、内存1024MiB、PID128均恢复。随后对会话12041发送Ctrl+C，工具确认终端退出码1（主动中断），本次QA服务已停止。此收尾证据为新标签页重新进入与状态恢复；没有真实容器、模型或CLI调用。

外部证据 `H:\item\CorpPilot-test-evidence-20260906\f40-verification.json` 已保存。主代理只读SQLite回查为1任务、1执行、1条核查收据、0成果、0模型Run；原执行仍unknown且exit_code为NULL，数据库integrity_check为ok。保存人工核查没有改写原执行结果或新增任务、成果与模型请求。

Doc/PM只读系统核查：Windows 11 Pro 26200.8737、WSL 2.6.2.0；固件虚拟化与SLAT为True，HypervisorPresent为False、vmcompute不存在。唯一现有分发 `alpine-ai-yss` 为Stopped/WSL1，未启动、转换或配置集成。Docker Desktop 4.88.1.237512和CLI 29.7.2文件存在，但HKLM/HKCU未发现Docker安装注册；8月27日安装日志退出1，9月6日启动日志因缺少Docker注册键失败，daemon管道不存在。VMP只读查询返回0x80040154，DISM日志为CBS package identity/Foundation package创建失败；不能把无法读取状态写成Disabled。`DISM /Online /Cleanup-Image /CheckHealth` 虽退出0，正文明确“无法修复组件存储”，不是健康通过。

Windows保留应用/数据修复及重启窗口的授权已异步待答，未执行系统配置写入、安装、重启或其他项目服务启停。分阶段修复建议及微软/Docker官方依据见 [环境阻塞与修复前提](docker-worker.md#环境阻塞与修复前提)。F40实际提交 `a632631` 并立即推送，GitHub403拒绝当前账号suiyue1990，远端实施分支回读为空；外部证据为 `f40-delivery-result.json`。不能宣称真实容器验收或远端交付完成。

## F41 模型复盘后端验收

主代理全量 **532 passed、8 subtests passed（85.60s）**，执行会话51604退出0。全量开始后只新增一项隐私测试，生产代码未改；最终 `tests/test_workbench_retrospective_api.py` **4 passed（5.96s）**。使用本地模型HTTP替身和真实请求子进程，覆盖：Owner访问鉴权、创建202、只调用一次、候选生成后批准版本仍为0、Owner明确批准成为v1、重启回读和回滚v2；排队取消不生成候选；非法模型输出标记failed、不写候选、不重试。项目范围模型输入包含选定成果正文，不含未选正文、个人私密记忆或无关聊天前史；公共复盘GET不返回输入正文，复盘结果未发布为群聊消息。

独立审查Pass：21项复盘单元及HTTP定向通过（6.33s），另在临时数据中核验项目输入排除个人记忆/未选成果/聊天前史、GET不返回正文，以及错误范围、未选证据、撤销execute权限被拒绝。候选与Run原子写入故障回滚、幂等和重启unknown均有直接测试。

这验证实际控制器/协议/持久化链路，不验证真实供应商经验质量、真实CLI或Docker。F41仅为后端/API增量，浏览器复盘入口与其故障恢复另列F42；Skill发布、自动闭环协作等其余目标仍未完成。F41实际提交 `ffb7c3a` 后立即推送返回GitHub403，远端实施分支回读为空；外部 `f41-delivery-result.json` 记录本次结果，本地提交不代表远端交付。

## F42 浏览器模型复盘验收

本增量修改MemoryPanel、RetrospectivePanel和api.ts，接入已批准成果选择、独立模型调用确认、原请求恢复、复盘历史和既有候选审批。task_execution_ui独立读审Pass；当前typecheck及Vite构建45 modules通过，产物 `index-CVd9BLsW.js`。本轮前端增量沿用F41后端完整532项及8子用例、全量之后新增隐私用例后的HTTP定向4项证据，不将既有测试记作新一轮后端全量。

主代理真实浏览器验证项目scope记忆v0：只选择 `selected.txt`，不选择 `private-unselected`，显式确认模型调用后生成候选；批准记忆仍为v0，手工候选草稿保持。随后Owner核对全文、填写说明并明确审批，才产生批准版本v1。模型生成不自动替换记忆。

第二请求（ID前缀ac21）首次400后沿同一key核对；上游接受202但响应被丢弃，随后GET503。页面不再提供基于旧rejected的修改入口；刷新并重新进入后保留原key，恢复GET后找到原Run（ID前缀6e27324b）及对应候选。两个有效请求各产生一次本地受控模型调用，累计2次；未选成果及个人私密记忆的泄漏检查均为false。本地替身验证协议和隐私选择，不代表真实供应商经验质量。

模型配置禁用时，第3个Run保持queued，经UI取消后为cancelled且没有candidate、没有模型调用。第4个Run收到非法模型JSON后为failed且没有candidate。本地模型调用累计3次（2次有效、1次非法输出），取消路径为0次调用。

原MemoryPanel手工候选首发400后沿同键重试，上游201已保存但响应丢失；pending的rejected为false，重新编辑按钮消失。刷新后仍保留原key（前缀1ab99600），未因候选GET已可见就清除pending。1315×1272桌面截图确认对话框可滚动、表单可操作；尚未据此声明独立console或移动视口验收。

主代理确认旧fixture会话93937仍运行后Ctrl+C停止，工具确认终端退出1；新会话92039使用同一测试数据重新启动并轮换运行期访问凭据。旧授权收到401并触发AccessGate，重新授权后MemoryPanel仍保留1ab99600原请求的完整payload和rejected=false；手动同键回读成功后才清除pending，项目批准记忆保持v1。本文不保存任何实际或测试token。

外部 `H:\item\CorpPilot-test-evidence-20260906\f42-verification.json` 已保存。主代理只读SQLite回查为4个Run（2 completed、1 cancelled、1 failed）、4候选（个人种子1、模型2、手工1）、2条决定/2个修订、1任务/1执行/2成果；原手工request_id在memory_requests中仅1条，integrity_check为ok。实际本地fixture模型调用共3次，选中正文进入模型，未选成果、个人记忆及聊天历史标记均未泄漏。该1次执行为测试来源，未据此声明真实CLI或Docker运行；真实供应商经验质量仍待验证。

新测试服务92039由持有句柄的Agent确认仍运行后Ctrl+C停止，工具确认终端退出1（主动中断）；主代理再次核对7896和7897均无Listen，f42-verification.json已补入最终退出结果。F42本地浏览器恢复与测试服务收尾完成；console/mobile未专项测试，真实供应商经验质量、真实CLI及Docker仍未验收。提交与远端推送结果须以随后实际操作为准。

F42 交付补记：已直接读取外部 `H:\item\CorpPilot-test-evidence-20260906\f42-delivery-result.json`，实际提交为 `55ff1ee922ca465cc3da826965d53cb410178738`；立即推送退出 1，GitHub 403（suiyue1990 无目标仓库写权限）。远端回读命令退出 0，但 `remote_head=null`，没有远端分支哈希；本地提交不代表远端交付成功。

## F43：协作项目批量执行后端验收

本增量复用 Executions/CLIController；批准计划中的选定 1–16 项任务在单事务内全部入队并留下不可变回执，或全部回滚。任务仍按原依赖及并发上限调度，前置成果需要 Owner 批准。回执绑定原 execution ID，停止只针对本批实例；后续替代实例不受影响，unknown 和未确认退出不能被改写成成功。

主代理本轮定向结果为 **18 passed（4.17s）**。其中调度集成使用实际 tick 与线程池，运行受控 runner，验证 A/C 同时运行、B 等待 A 获批准后自动启动。上述证据来自主代理本轮结果；文档维护未独立重跑，不将受控 runner 描述为真实 CLI、付费模型或 Docker。

Owner 鉴权覆盖批量创建、读取、停止与项目来源回执接口。首次创建检查配置和原单任务入队条件；精确重放返回原批次，同 key 异内容拒绝。停止需要 confirm=true，仅表示向固定绑定实例提出停止，不自动产生人工核查声明或批准成果。项目来源查询只用于 project 会话。

主代理完整后端回归 **551 passed、8 subtests passed（93.25s）**，session65228 退出0。独立审查 **Pass**，相关测试 **57 passed（10.60s）**。全量结束后产品代码无变化，仅新增容量测试2项：独立运行 **2 passed（1.07s）**，主代理复跑 **2 passed（1.28s）**；未将新增测试数合并伪称另一次全量结果。F44 前端仍独立开发/审查，尚未浏览器验收。F43 提交及推送结果等待实际操作补记，真实 CLI/模型、双 Docker Worker 与整体目标继续保持未验收边界。

F43 交付补记：已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f43-delivery-result.json`。实际提交 `47c4086d1f65326191ffaf12ba12491b6c161574` 后立即推送退出1、GitHub403；远端回读退出0但 remote_head=null。F44 前端当时仍留在工作树中，未混入 F43 提交；远端交付未完成。

## F44：浏览器协作批量执行验收

主代理在真实 IAB、7896 测试入口及1315×1272桌面视口完成浏览器验收并截图。源私聊历史和项目 origin-plan 均能找到协作创建回执。提交前呈现选定任务的当前 scope、acceptance、version 与负责人，再明确确认整批执行；独立审查两项缺陷（对非项目查询 origin-plan、封锁已人工核查 unknown）修复后 Pass。最终 typecheck/构建通过，46 modules，index-DNIGCKAo.js。

三个任务一次入队，受控 runner 中 A/C 并行、B 等待 A 成果批准；Owner 批准后 B 自动开始并取得1个批准输入。再次批量请求首发400后沿同 key 重试，202 已受理但响应丢失并遇到GET503；刷新保留完整原请求且清除旧 rejected，GET恢复原批次，没有新POST替代。fixture有序重启、访问口令轮换后旧授权401，重新授权仍保留原批次。

第三批仅运行 C，明确停止后响应丢失，界面经GET观察 stopping→cancelled；再次加载后 stop POST累计仍只有1次，没有盲重发或新执行。unknown UI未注入浏览器，仅有独立静态审查与F43后端证据，不扩大为浏览器实测结论。

已直接读取外部 `H:\item\CorpPilot-test-evidence-20260906\f44-verification.json`：3任务、3批次、5执行（4 awaiting_review、1 cancelled）、4成果、1评审、1输入、0人工核查收据；5次受控runner各绑定不同execution，unique_call_per_execution=true，4成果hash_ok均true，integrity_check=ok、foreign_key_check为空。事件记录4次创建POST（含1次首400）、3次受理、1次stop POST和1次stop受理；最终取消实例退出码130。

本轮真实CLI、模型、Docker调用均为0，受控runner不等于生产执行验收；console及移动视口未专项测试。后端沿用F43已记录的完整回归，未虚构新全量。测试fixture原38539与重启31647均先确认实际句柄仍运行，再Ctrl+C退出1；最终7896无监听。正常7892旧26019在数据库无活动执行后有序停止，新69967启动健康检查200并回读index-DNIGCKAo.js，新增批次表为空、任务执行0、原1个unknown模型Run保留，未注入测试数据。 F44 实际提交 `02b364144b890663efc3276a2dc81c65f34818e1` 后立即推送退出1：GitHub403（suiyue1990 无写入权限）。远端回读退出0但 remote_head=null；已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f44-delivery-result.json`。本地验收和提交不代表远端交付。

## F45：模型 unknown 核查与门禁验收

主代理报告定向 **29 passed（11.16s）**。领域层覆盖不可变声明、原 Run 不变、严格字段、并发及重启精确回读、真实 INSERT 故障回滚，以及普通回复/提案/复盘的创建、pending、claim 范围门禁。相同摘要不同来源执行的复盘不会互锁；未核查 queued 不进入调度候选，不消耗常态 RPM。声明后既有已授权 queued 可继续，旧 unknown 仍未知且不自动重试。

控制器/API 覆盖当前持有请求和提交不确定请求的声明阻挡、完整关闭/重启边界、Owner 鉴权及禁用配置后的精确回读。人工声明不改原 model、usage 或 RPM 历史，不把未知结果改成成功。主代理完整回归 **570 passed、8 subtests passed（97.96s）**，session88107 退出0，包含F45全部新增测试；独立QA **15 passed（2.19s）**、审查Pass。提交异常错误文案保留“本地请求可能仍在处理”的不确定性，不声称已退出。

本增量不声称真实供应商影响或模型质量已验收。F46 仅界面草稿和 typecheck，尚无该界面的浏览器验收。F40 真实双 Docker Worker、真实 CLI/模型和整体目标仍未验收。

F45 实际提交 `5b37cad73e97607f8f8f6709e779d060036a748d` 后立即推送退出1、GitHub403（suiyue1990 无写权限）；远端回读退出0且 remote_head=null。已直接核实外部 `f45-delivery-result.json`，F46草稿未包含在此提交；远端交付仍阻塞。

## F46：浏览器模型 unknown 核查验收（本地fixture通过）

独立类型检查、静态Ponytail审查Pass；主代理npm构建47 modules，bundle `index-DdLyA6xU.js`。主代理IAB 1315×1272、独立7896 fixture显示三类unknown列表；核查首400后，服务重启新口令401仍保留同key，重新授权同key201受理但响应丢失、GET503及reload不丢pending，GET恢复后读到原声明。

普通回复核查后准备新key，刷新并重选原source/member、明确确认，Run `58d56833-83a5-4368-ad0d-29ec66ca47b2` completed；查询原请求不新建。规划同Run被另一fixture声明占用时，Owner显式接受该不可变声明，再选coordinator/candidate并确认，Run `90c224f0-4bc2-4c5c-896d-7291b6714f56` completed。复盘历史核查后重新选来源、成果并明确确认，Run `c43d679f-54b2-4934-b261-3b0ddd6dac74` completed，生成候选 `bafd8b0d-88ef-4d27-86e4-d763dcc5542b` 待审批，目标记忆仍v0。

文档代理直接只读运行外部 `f46_verify.py`：6 Runs（原3 unknown、新3 completed）、3核查声明、2 planning_requests、2 retrospective_requests、1 memory_candidate、1原fixture task_execution、4消息。与原始SQLite基线逐字段比较的seed_runs_unchanged=true；声明唯一、新key与seed不同。三个新Run各对应一次真实本地HTTP fixture模型调用，old_unknown_never_called=true、each_called_run_once=true；无额外消息，integrity_check=ok、foreign_key_check为空。原三个unknown为状态注入测试种子，不是实际供应商故障；声明不改其状态/model/usage。

已直接核实外部 f46-boundary-review.json：passed=true，三项测试全部Pass。损坏global pending即使GET已有声明也不清存储/不解锁reply；规划与复盘GET503保留原pending，恢复GET后明确release才清除。writes=[]（零POST），console_errors=[]、page_errors=[]，另有2条预期503控制台信息单独记录，不冒充无故障请求。主代理已查看desktop1280×900和mobile390×844截图，移动dialog在视口内。F46本地fixture验收Pass。本轮未调用真实供应商、CLI或Docker；后端回归沿用F45的570项及8子用例，不虚构新全量。F46 已本地提交，立即推送遭 GitHub403，远端回读为空；详见下方实际交付记录，不能据此称远端交付或整体目标完成。

最终补充：主代理模型核查后端/控制器/API定向 **17 passed（4.39s）**，不记作新全量。正常7892服务原session69967先确认仍live再Ctrl-C退出1，保留browser-state由session19278重启；health200并回读index-DdLyA6xU.js，正常库仍只有历史unknown模型Run1、任务执行0、批次0，未作fixture写入。最终测试fixture session74866先poll确认live再Ctrl-C退出1，已停止；主代理重新运行f46_verify并落盘最终证据。真实供应商/CLI/Docker及整体目标未验收，F46 已本地提交，立即推送遭 GitHub403，远端回读为空；详见下方实际交付记录。

F46 实际交付记录：已直接读取外部 `H:\item\CorpPilot-test-evidence-20260906\f46-delivery-result.json`，提交 `18b5c481bd2cbb5d07cd21e953bf04f337673bb9` 成功后立即推送退出1，GitHub403：suiyue1990 无权写入 xiaoyangtx996/CorpPilot；远端回读退出0、`remote_head=null`。该记录报告提交后工作区干净，不能据此称远端交付完成。主代理已通过 git show 核实该提交为10文件、195行新增/20行删除。
## F47 同群单次评议后端验收（本地HTTP链路通过）

主代理定向25项通过（7.61s，当时尚未加入冻结角色单用例）；随后新增冻结角色测试单项通过（0.22s）。完整 pytest tests/ -q 为 **596 passed、8 subtests passed（105.83s）**，session37270退出0，包含冻结角色和HTTP严格字段用例。独立QA/TechLead/Ponytail审查Pass，四文件定向41项通过（9.88s），另复跑冻结角色1项通过（0.22s）；这是分次验证记录，不相加成42个不同用例。

原生ReplyController配合实际本地HTTP provider子进程验证A普通API回复→B评议，两次模型调用分别仅一次；8并发同key请求仅1个peer Run。快照不包含私聊、未选Owner历史及源角色指令；创建后改目标模板仍使用冻结指令。来源必须为completed Run发布消息，DM/跨群/自评/伪造Agent消息、Owner源以及confirm=false/1/缺字段/extra均拒绝且不新增Run。普通reply来源校验未放宽。

peer unknown核查后显式新key只调用一次，原unknown不变；provider返回前撤销目标B权限不发布消息。源/目标快照及完成双阶段撤权、事务故障回滚、单次完成无自动续轮、后续Owner再次授权peer源评议均有核心测试。配置关闭、成员停用、归档、shutdown/restart后的完整原请求仍精确回读；跨kind冲突拒绝。

主代理亲读五模块、核心测试及其HTTP测试，产品验收符合F47范围。F48前端已通过本地fixture浏览器验收，详见F48验收记录；不把此后端证据记为浏览器完成。全部模型调用为本地HTTP测试供应商，真实供应商质量、CLI和双Docker未验收，整体目标未完成。F47已本地提交并立即尝试推送，403阻塞；实际记录见下文。


F47 实际交付：直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f47-delivery-result.json`，本地提交 `a4f7875c73bc29d0d1570b4e3aaaf9f17f2562c4` 成功，11文件、461行新增/19行删除；随即推送退出1，GitHub403（suiyue1990无目标仓库写权限）。远端回读退出0但 `remote_head=null`，因此远端交付仍阻塞；F48五个前端文件未包含在F47提交中。

## F48 群内评议界面验收（本地fixture验收Pass）

五个前端文件提供群内消息评议入口、全局原请求恢复、来源全文/作者及逐次调用确认，复用Run排队取消和模型核查。根与独立审查退回的两处恢复缺陷已修复：POST终态只保存编号，必须独立GET详情精确匹配后才许可释放；unknown释放时声明GET失败撤销checked，原pending保留。typecheck与48modules构建通过（index-BDbMUDFL.js），此后无代码修改。

已直接读取外部f48-ui-review.json、f48-boundary-review.json、f48-restart-review.json和f48-cancel-review.json，报告均passed=true；主代理已亲读对应四个浏览器脚本并查看桌面、移动及发布截图。主流程3项验证首400→同key接受丢响应/GET503→刷新GET恢复→显式释放；B完成后Owner再确认C第二跳；原unknown通过UI核查且不新建调用。第二跳source_run_id精确引用第一跳cf1c4231…，目标Run为eb9f3930…；主代理CUA最终历史为3published、1cancelled、1unknown，并展开核对。

独立边界3项验证：模拟POST202终态不能绕过独立GET；损坏pending在关闭重开后原样保留且锁定；unknown声明GET503撤销许可、保留pending，恢复读取后再显式释放。模拟POST未转发到服务端，unexpected_writes=[]、console_errors=[]、page_errors=[]，2条预期503另列；1280×900和390×844截图dialog均在视口内。重启同库口令401后原request_id ced6d73c…和Run cf1c4231…保留，仅GET恢复，writes=[]、page_errors=[]。取消用例为一个延迟running占用槽，另一queued经UI取消，不产生该请求模型调用或消息。

最终f48-verification.json实际运行退出0、passed=true：7runs、7messages、5peer_review_requests、1model_run_reconciliation、0memory_candidates、0task_executions；原2completed及1unknown完整Run和原消息均未变，原Run调用0。新3completed各恰好一次本地HTTP调用且发布消息来源绑定正确，新1cancelled零调用且reply_message_id=null；无额外消息，integrity_check=ok、foreign_key_check=[]。以上使用state-only种子和本地HTTP fixture，不是真实供应商、CLI或Docker。本轮不记录provider输入，隐私入参证据沿用F47已测用例，不新增未验证的隐私结论。

正常库备份f48-normal-backup.json的SHA256为870f4cef4f31d00460674fb4c8d520d5a17bff3e2fba858a51ea445e5257d356；f48-normal-readback.json直接回读31张原有表逐行比较无变化、peers=0、完整性ok、外键检查空。旧正常session19278确认live后Ctrl+C退出1，新session68562加载F47，health200并提供新bundle，正常历史unknown保留。收尾直接核实f48-cleanup.json：fixture96419先live poll再Ctrl+C退出1，主代理独立回读7896/7897均无监听，正常68562保留。

PM/QA/TechLead/Ponytail对F48本地增量Pass。后端沿用F47完整596项及8子用例证据，不虚构新全量；F48已本地提交，立即推送403阻塞，远端未确认；实际记录见下文，真实供应商、CLI、双Docker及整体目标未完成。


F48实际交付：已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f48-delivery-result.json`，本地提交 `43be70154bc7d373932a0cd5f14ce672fbfef48f` 成功，9文件、179行新增/13行删除；立即推送退出128，GitHub返回403（suiyue1990无写权限），远端回读退出0但remote_head=null。记录中working_tree_status为空，提交后工作树干净；本地验收与提交不等于远端交付成功。


## F49 原子创建启动后端验收

主代理亲读新增ProjectLaunches、四个既有模块diff、核心测试和HTTP测试。核心13项覆盖同键并发、不可变回执、权限/源消息/DAG失败、批次和末步启动回执INSERT故障全回滚、99个真实独立排队任务仅剩1槽时两任务完整回滚，以及关闭后原请求精确回读。

主代理独立HTTP三项实际通过（3.44s）：Owner鉴权及严格确认、8并发同键只一个项目和批次、仅共享批准摘要、第二执行SQL注入故障回滚全部新项目数据、旧仅建群授权拒绝升级、配置关闭及归档/停用后精确回放、同库重启恢复和固定批次停止。实际控制器配受控runner仅调用上游一次，后继保持queued等待Owner审批，停止后后继cancelled且未调用。受控runner不是实际CLI或模型。

完整`python -m pytest tests/ -q`为 **612 passed、8 subtests passed（112.34s）**，session40355退出0。新增并发关闭审查测试在此次全量收集后加入，结果另记；不合并冒称一次新全量。F50前端静态检查及构建已通过、浏览器待验收，本段不证明浏览器入口或完整自动协作。正常工作台本轮未重启或迁移数据库，仍保持上一轮运行服务；真实CLI/模型、双Docker、费用硬门及整体目标未完成。F49已本地提交并立即推送403，远端未确认，实际记录见下文。


F49独立审查补充：QA/Ponytail审查Pass，定向54项通过（7.73s）。新增两个Event/RLock受控并发用例验证启动先行时关闭等待原子提交、关闭先行时拒绝新请求但允许精确回读；主代理亲读并复跑 **2 passed（0.42s）**。这两项在612项全量收集之后新增，未计入该全量；产品代码未再修改。PM及主代理对F49后端/API增量验收Pass，F50浏览器仍待验收。


F49实际交付：直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f49-delivery-result.json`，提交 `6e68e864ed6804972f13cd03e4cc92c1fa32ca3b` 成功，12文件、553行新增/67行删除；立即推送退出128、GitHub403（suiyue1990无写权限），远端回读退出0但remote_head=null。提交后仅三个F50前端文件未提交，F49本地验收和提交不等于远端交付。

## F50 完整计划一次启动界面验收（本地fixture验收Pass）

范围为三个前端文件：CollaborationPanel复用完整计划预览，api.ts类型及CollaborationExecutionPanel固定初始batch。静态QA/typecheckPass；根构建48modules通过，产物index-Bl2XnWmG.js。浏览器主流程、边界及重启恢复已完成，实际证据如下；提交及推送以实际操作结果为准。

静态Return已修：停止确认绑定当前detail.id并在历史切换时清除；组合回执补关键ID、批准coordinator/member集合、task/execution/request唯一性和逐任务映射；移除无关项目F44 pending全局互斥，同时保留同plan原pending优先和损坏锁定。

浏览器验收门：原仅创建路径不执行；一次明确批准完整计划后建项目和首批入队；前置仍等Owner审批；固定首批读取与停止；A批确认不用于B；旧F36/F50 pending不被覆盖、F44无关项目不阻塞且同plan旧请求优先；首4xx/401/受理丢响应/GET503同键恢复，POST终态不得绕过独立GET清pending；组合回执结构错误与存储损坏保持锁定。后端证据沿用F49，不虚构新全量或真实CLI/model/Docker验证。自动规划、无需Owner中间审批的交接及货币预算硬门未完成。


F50实际验证：主浏览器1315×1272两条完整流程通过：首400后同键受理丢响应/GET503，刷新保留完整操作和计划，独立GET核对后明确结束恢复；固定首批A/C并行、B等待，A经Owner批准后B消耗A的1份捕获成果并自动执行，最后停止同批活动C。最终只有1项目/1启动/1批次/3任务/3执行，A/B awaiting_review、C cancelled，3个不同受控runner各调用一次，无重复入队。主代理亲读脚本和报告，并在IAB独立展开历史及固定批次核对状态。

独立QA六项边界通过；其额度耗尽后主代理补损坏launch存储与无关F44 pending不阻新建两项，完整八项重跑通过。3次模拟POST均仅context内fulfill，真实写全部拦截，unexpected_writes/page_errors/console_errors为空；1条预期503单列。选择器两次定位失败均保留外部证据，修复仅测试脚本，无产品改动或额外真实启动。桌面1280×900及手机390×844截图经主代理查看，弹窗在视口内。

同库重启测试通过：原launch完整pending保持，实际新口令触发旧口令401，重新授权后GET恢复原回执，writes=[]、page_errors=[]。原fixture工具句柄54160失效，但PID44500及7896监听仍在；主代理核实精确命令及活动执行0后定向停止该fixture，未称其自然退出。同库重启session41860验收后先实际poll确认live，再Ctrl-C退出1；7896监听已消失，正常7892保留。页面关闭期间fixture出现ConnectionAborted日志，未生成新增任务或执行。

主代理最终只读f50-verification.json通过：原种子行保留、共享仅显式摘要、执行绑定及依赖精确、2份artifact字节长度及SHA-256一致、3次runner输入无私有源历史标记、SQLite integrity ok/外键无错误。f50-artifact-readback.json另核对B→A固定execution_inputs和唯一输入artifact。正文核对发生在Owner批准之后，不声称批准前已通过UI下载正文；本地Python受控runner不是实际CLI/模型/Docker验收。

主流程page_errors为空，原console包含400/丢连接/503故障注入及2条未记录URL的404；新只读context重走相关页面responses>=400、console及writes均空。两条原404端点无法追溯，保留未归因记录，不宣称原console全零。正常应用已由68562有序停止并重启为43954；health200、index-Bl2XnWmG.js回读成功，33张原表逐行同已核实SHA-256的备份、新launch表0行，原unknown不改。

PM/TechLead/Ponytail/QA及主代理对F50本地增量验收Pass。外部证据位于H:\item\CorpPilot-test-evidence-20260906，包含f50-ui-review、f50-boundary-review、f50-restart-review、f50-verification、f50-normal-readback；后端回归沿用F49，未虚构新全量。真实CLI/双Docker、自动组队后自主交接、费用硬门、检查点恢复等完整目标仍未完成。
