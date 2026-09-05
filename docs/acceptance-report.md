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
