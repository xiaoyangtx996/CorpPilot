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
- 提交：`d15b2ff`；立即推送再次返回同一 403，未标记远端交付。
- 首次测试生成的未跟踪文件已保留于 `H:\item\CorpPilot-test-evidence-20260906`；后续全套测试无新增运行产物。

## F03：持久身份与角色模板存储

- 实现：主代理；独立技术复审：pm_acceptance，修正后 Pass。
- SQLite 独立运行目录、角色 SOUL/岗位文件盘点、稳定默认 ID、幂等初始化、持久自建/编辑/停用身份。
- 输入字段及工具配置校验；并发 PATCH 仅修改指定字段，未知数据库版本在结构/种子写入前拒绝。
- 主代理全量回归：58 passed、1 skipped、8 subtests passed；独立复核身份定向测试：4 passed。
- 权限配置此时仅为持久数据，不声称已构成 Worker 权限边界；API、界面、配置目录来源核对、消息与记忆仍需实现。
- 提交标识：`feat(workbench): F03 persist agent identities and role templates`；推送受现有 GitHub 403 阻塞。
- 提交：`47c691e`，推送返回 403。目录盘点：13 个 SOUL + 34 个岗位文件，共 47 个模板。

## F04：本机身份 HTTP API

- 实现：runtime_reliability；主代理技术/Ponytail审查及全量复验：Pass。
- 标准库本机 HTTP 服务，健康检查、模板列表、身份列表/创建/读取/更新；不开放任意文件和网络监听。
- 检查 Host、Origin、跨站来源、JSON 类型/编码/大小及重复请求头；字段验证使用 Store。
- 真实 loopback HTTP 测试加存储定向测试 16 passed；主代理全量 70 passed、1 skipped、8 subtests passed。
- 唯一 skip 为原图像测试缺 Pillow；不视为该图像检查通过，后续补齐依赖再验证。
- 启动：`Set-Location scripts` 后运行 `..\.venv\Scripts\python.exe -m workbench.server --port 7892`；可用 `--data-dir` 指定独立运行目录。
- API：`GET /health`；`GET /api/workbench/templates`；`GET/POST /api/workbench/agents`；`GET/PATCH /api/workbench/agents/{id}`。
- 仅 Owner 本机服务，尚未向 Worker 提供能力接口；前端、聊天、任务、真实执行未完成。
- 提交标识：`feat(workbench): F04 expose local identity API`；已知 GitHub 写权限仍阻塞推送。
- 提交：`f3eb6a8`，立即推送仍 403。

## F05：真实身份管理浏览器界面

- 实现/浏览器验证：主代理；设计：frontend_design；独立技术/Ponytail审查：pm_acceptance，Pass。
- React/TypeScript/Vite 三栏界面，真实身份列表、模板选择、新建、改名、技能/工具配置、停用/启用和搜索。
- Python 同源提供静态构建；新增静态路径越界测试。侧栏窄屏焦点管理、Escape/Tab、表单必填/错误/恢复焦点。
- IAB：创建“QA 身份小林”，选择前端开发工程师，停用并改名，刷新后仍存在且停用；390×844 切换导航/详情及 Escape 焦点回到按钮；1536×1024 检查三栏布局。
- 浏览器使用独立测试数据 `H:\item\CorpPilot-test-evidence-20260906\browser-state`，未写默认用户数据。
- 主代理 TypeScript/Vite 构建通过；独立 API 回归 13 passed、独立前端构建通过。
- 安装 Pillow 后主代理完整回归 72 passed、8 subtests passed，无跳过。
- 视觉检查：三栏宽度/分隔线、白与冷灰底色、绿色操作色、字体层次、角色首字、窄屏收拢均已检查；当前中栏仍是身份空态，尚不声称完整聊天设计已落地。
- 概念中的样本消息不初始化；聊天、任务、模型配置、记忆仍待后续实现。
- 命令见 `frontend/README.md`；依赖使用锁文件，系统 Node20 不符合要求，实测使用 bundled Node24。
- 提交标识：`feat(workbench): F05 manage persistent identities in browser`；远端仍受 GitHub 403 阻塞。
- 提交：`52e5710`；立即推送仍返回403，不能标记为远端已交付。
- 下一阶段：持久会话/成员/消息权限与幂等投递、真实模型配置和回复，再接任务/Run、Worker与记忆。完整需求范围保留不变。

## F06：持久会话、成员权限与消息幂等

- 实现：runtime_reliability；独立代码/Ponytail审查：conversation_review；主代理修正、集成及复验，Pass。
- SQLite v2 保存 DM、董事会/项目群、成员、归档及带稳定序号的消息；并发创建 DM 和相同 request_id 发送均保持唯一。
- 成员撤销后禁止后续读取/发言，停用身份不得发言/新邀请；读取使用一致快照，不能混入撤销后的新消息。Owner 身份只由内部调用确定。
- v1 升级前保存独立 `workbench-before-migration-{uuid}.sqlite3` 快照；并发启动不覆盖旧备份，快照内 schema_version 是其真实版本依据。
- 审查发现并修复备份覆盖和会话详情读权限竞态，新增受控交错回归；12 项 Store/会话检查通过。主代理含在途 API 的全量回归 82 passed、8 subtests passed；最终备份连接关闭修正后会话 8 passed。
- 当前只证明存储能力，聊天界面、模型、任务和 Worker 权限边界仍未完成。
- 提交标识：`feat(workbench): F06 persist conversations and permission-scoped messages`；提交后立即推送，远端结果另记。
- 提交：`1a42ecf`；立即推送仍返回 GitHub 403（suiyue1990 无仓库写权限），远端未交付。

## F07：本机会话 HTTP API

- 实现及复验：主代理；独立代码/Ponytail审查：conversation_review，Pass。
- `GET/POST /api/workbench/conversations`；`GET/PATCH /{id}`；`GET/POST /{id}/messages`；`PATCH /{id}/members/{agent_id}`（以上后三者均接会话前缀）。
- messages GET 支持 after/limit 分页；POST 仅接受 content/request_id。成员 PATCH 仅接受 joined 布尔值，调用者不能从 body 或 query 伪装 Agent。
- 真实 loopback HTTP 测试覆盖建群、成员加入退出、幂等发送、归档/恢复、重启历史恢复、恶意发送者字段与无效分页；主代理全量 82 passed、8 subtests passed。
- 仅本机 Owner API；未开放 Worker 入口，未声称已接入模型自动回复。浏览器集成为下一项独立功能。
- 提交标识：`feat(workbench): F07 expose persistent conversation API`；提交后立即推送，远端结果另记。
- 提交：`20ddd3d`；立即推送仍为同一 GitHub 403，未标记远端交付。
- 运行环境复核：启动已有 Docker Desktop 后，其日志报告缺失注册表 `SOFTWARE\\Docker Inc.\\Docker Desktop`，backend 未启动，desktop-linux 管道不可用。双 Worker 真实验收仍待环境修复；未修改注册表、重装或重启系统。

## F08：持久私聊与群聊浏览器交互

- 实现：conversation_frontend；独立代码/Ponytail审查：conversation_review，修正后 Pass；主代理构建与 IAB 验证。
- 私聊/董事会/项目群列表、新建群、成员管理、会话名称、归档恢复、成员视角选择、Owner 消息发送、草稿与同键重试、50条正向分页。
- 修复迟到消息倒退预览及迟到列表覆盖新建会话；最新请求遇到本地修改后重读完整列表，更旧请求丢弃。
- IAB 实测：秘书私聊保存消息，归档禁止输入/恢复；两人董事会创建，移除/重新加入 CEO，不能移除最后成员，停用身份不能邀请，Escape 关闭管理窗焦点回入口。
- 暂停本次测试服务后发送，真实失败且草稿保留；同目录重启服务后重试成功，刷新后会话和已保存消息均恢复。此项不是“落库成功但响应丢失”的网络注入测试，该场景仍需后续 E2E 补充。
- 用真实 HTTP 在独立 browser-state 测试库准备205条消息；IAB显示数量依次50/100/150/200/205，最终DOM核对205条唯一、001–205顺序完整。
- 390×844及1536×1024截图检查通过：窄屏输入与按钮可达、桌面三栏完整。真实手机软键盘、受控延迟网络E2E及完整模型链路仍未验证。
- 主代理 `npm run build`（bundled Node24）通过，32模块；后端此前82 passed、8 subtests passed，本功能没有更改后端。
- 模型尚未接入，界面明确只保存Owner消息；没有生成假回复。当前新增消息手动刷新，后续真实执行事件接入时补自动同步。
- 提交标识：`feat(workbench): F08 add persistent direct and group conversations`；提交后立即推送，结果另记。
- 提交：`dfa1781`；立即推送仍403，ls-remote未找到目标远端分支，未交付远端。

## F09：每次模型尝试前的原子 RPM 准入

- 实现/复验：主代理；独立代码/Ponytail审查：admission_review，Pass。
- 修复旧 Agent Loop 的反向限流和一次等待后直接越限；每次模型尝试（含内部重试）都在调用前预占，同一控制器共享 TrafficMonitor 的所有身份使用同一窗口。
- 使用现有锁和 monotonic 时间；窗口检查与追加原子完成，完成量统计保持原义，失败尝试不退还已用名额。SDK关闭内部隐藏重试，保留现有显式路由重试策略。
- 30个并发请求仅允许3个、60秒边界释放、完成记录不重复预占、重试逐次准入、限流持续等待和空闲无额外等待均有定向回归：3 passed。
- 主代理全量85 passed、8 subtests passed。SDK禁重试按构造参数审查；没有用模型真实请求冒充测试。
- 本项为进程内单控制器RPM，不声称跨进程或重启持久限流；并发执行上限、费用硬预算和取消仍待后续运行调度实现。
- 提交标识：`fix(runtime): F09 reserve RPM before each provider attempt`；提交后立即推送，结果另记。
- 提交：`5258be3`；立即推送仍同一GitHub403，远端交付未完成。

## F10：显式模型配置持久层与本机 API

- 实现：model_settings及主代理API集成；独立代码/Ponytail审查：admission_review，Pass。
- 复用工作台SQLite，单独版本化配置记录；默认禁用且不默认选模型，model/base_url/api_key_env与输出上限、超时、RPM、并发参数严格校验。
- `GET/PATCH /api/workbench/model-settings`只提供配置及configured/credential_available；密钥从服务进程环境读取，不落数据库、不经公开API返回。保存配置不会触发模型调用。
- PATCH事务内读取合并，未来配置版本拒绝改写。HTTPS/本机HTTP校验属于输入与明文保护，不声称构成DNS或Worker网络隔离。
- 配置定向50 passed；主代理配置及真实HTTP回归66 passed，包含重启恢复、密钥不回传、移除环境变量后状态变化。
- 当前只交付配置能力；界面入口、真正模型调用和这些执行参数的工作台调度执行仍待接入，不能把配置已保存当作连接验证。
- 提交标识：`feat(workbench): F10 persist explicit model settings and expose API`；提交后立即推送，结果另记。
- 主代理全量136 passed、8 subtests passed。提交`78d59b4`后立即推送仍403，远端未交付。

## F11：软件内模型设置入口

- 实现：conversation_frontend；独立代码/Ponytail/React审查：admission_review，Pass；主代理构建及IAB验收。
- 左栏模型设置入口与原生对话框；8项配置、只提交改动字段、加载失败禁止编辑/保存、失败重试、忙态保护与焦点恢复。
- IAB在独立browser-state保存禁用测试配置`qa-model-disabled`及未设置的测试环境变量名；回读显示字段完整但凭据缺失，没有模型调用。
- 关闭重开后8项值恢复；停止测试服务再打开设置显示Failed to fetch且不能保存，服务同目录重启后重试完整恢复。
- 390px截图检查表单内部滚动、保存按钮可达；Escape焦点返回“模型设置”。主代理TypeScript/Vite构建通过，33模块。
- 现阶段凭据来自服务启动环境，尚未提供软件内密钥录入/系统凭据保管；连接验证和真实回复仍未完成，界面已明确说明。
- 提交标识：`feat(workbench): F11 add in-app model settings`；提交后立即推送，结果另记。
- 提交`726e53b`后立即推送仍GitHub403，未交付远端。

## F12：持久回复 Run 与上下文授权快照

- 实现：reply_runs；独立代码/Ponytail审查：reply_review，Pass；主代理Runs/Store定向复验11 passed。
- Run与Agent身份/会话分离，记录源Owner消息、request_id、状态、实际回复消息、模型及用量；相同请求幂等，事务claim与finish确保不会重复回复。
- 上下文仅来自该群截至源消息的最新100条，snapshot和finish再次检查成员/启用/归档；退群后的新结果不得发布。超出窗口明确返回context_truncated，不宣称完整历史注入。
- 排队可取消，运行中不伪称已停止；重启running标unknown且不自动重发，recover须由后续唯一控制服务启动锁保护。
- attempt与requirement_version首版固定1，版本变更/重试任务仍待后续实现；不是完整任务调度验收。
- 提交标识：`feat(workbench): F12 persist authorized reply runs`；提交后立即推送，结果另记。
- 提交`49bceba`，立即推送结果为GitHub403，远端未交付。

## F13：有总时限的单次模型请求

- 实现：主代理；独立代码/Ponytail审查及复验：reply_review，Pass。定向13 passed，独立Run/provider合计20 passed。
- OpenAI兼容chat/completions单次真实HTTP实现，无重定向、代理环境继承、工具调用或自动重试；非200、截断、空回复、错误格式均不算成功。
- 通过隔离Python请求进程和父进程总时限，覆盖DNS/慢响应；凭据只经私有stdin传入，不放命令行，子进程环境不继承其他API密钥。
- 错误不回显上游body和密钥；未知用量保留null。实际调用入口run_reply，直接reply仅用于协议单测。
- loopback HTTP与真实请求子进程验证完成，包括401/429/500/重定向拒绝、身份上下文角色映射及1秒总时限；这是本地协议与故障验收，不是付费模型质量或真实账号调用验收。
- 提交标识：`feat(workbench): F13 bound and sanitize model requests`；提交后立即推送，结果另记。
- 提交`c71f2c2`后立即推送仍GitHub403，未交付远端。

## F14：唯一控制器、回复队列与运行 API

- 实现：主代理；独立代码/Ponytail审查：reply_review，修正后Pass。主代理全量160 passed、8 subtests passed。
- 单数据目录OS文件锁覆盖recover与整个控制器生命周期；最多16执行线程，按实际配置max_concurrency派发，超额留SQLite排队，RPM在尝试前预占。
- 正式派发只走有总时限的run_reply请求进程；开始前取得授权快照，完成后再次验权并原子发布。关闭等待实际本地请求退出后才释放控制器锁。
- 修复异常Future丢失：claim/submit或状态写入失败保留待收敛Run，仅重试写入unknown，不重发模型请求；调度异常通过runtime接口可见。
- `GET/POST /conversations/{id}/runs`（工作台API前缀）、`GET /runs/{id}`、`POST /runs/{id}/cancel {}`、`GET /runtime`；缺少启用配置/凭据时不创建新执行。
- 4项集成回归通过：真实HTTP+子进程成功、重复请求唯一、并发排队/取消、双控制器拒绝、500无假消息、状态写入故障恢复不重发、失败释放槽位和RPM阻止第三次尝试。
- 重启后的RPM窗口仍从零开始，费用硬预算/Run新attempt/运行中主动停止/CLI与Docker任务执行尚未实现；配置并发上限现已对回复请求生效。
- 提交标识：`feat(workbench): F14 dispatch bounded persistent reply runs`；提交后立即推送，结果另记。
- 提交`e277205`后立即推送仍GitHub403，远端未交付。

### F14 集成修正：活动 Run 可发现性与有界队列

- F15前端审查发现最近100条历史可能挤出旧活动Run。列表改为全部活动+最近100条终态，终态按更新时间取窗；前端另对缺失已知活动ID单独回读，防轮询间终态再被挤出。
- 创建请求在同一事务检查全局最多100个活动Run，原request幂等回读不受容量限制；取消释放名额。
- 主代理新增105条终态历史、旧活动可见/转终态、队列满拒绝与取消释放回归，8项Runs测试通过；独立reply_review复验8 passed，审查Pass。
- 此修正单独提交并立即推送，不等待后续UI功能。
- 修正提交`ea5b8e6`，立即推送仍GitHub403。

## F15：指定成员回复与真实 Run 状态界面

- 实现：conversation_frontend；独立代码/Ponytail审查：reply_review，修正后Pass；主代理构建、真实浏览器验收。
- 每条已保存Owner消息可明确选择一名启用成员并确认调用；源消息/成员使用确定request_id，已知Run走GET，未确认创建按同键重试，不自动全员回复或重试失败。
- 显示6种Run状态、净化错误、实际模型和token（缺失显示未知）；仅排队可取消，运行中不假称可停止。活动期1秒轮询，缺失已知活动ID单独回读，完成触发真实消息分页刷新。
- IAB：禁用配置时拒绝且无Run；独立测试服务用dummy凭据与未监听本机端口执行一次请求，总时限到达后显示unknown和原Run ID，重复查询仍一条Run/一条Owner消息，无伪造回复。
- 刷新会话后同一unknown恢复；390×844截图与DOM确认无横向溢出，输入可达。测试结束恢复模型配置禁用，并重启服务去除临时dummy环境变量。
- 主代理TypeScript/Vite构建34模块通过；完整后端161 passed、8 subtests passed。
- 正向回复管道由本地HTTP协议fixture+真实子进程集成测过；浏览器真实供应商回复和费用验收仍缺可用授权配置，未以fixture或连接失败替代该项。运行中主动停止、任务闭环、CLI/Docker隔离和记忆仍未完成。
- 提交标识：`feat(workbench): F15 request and observe individual agent replies`；提交后立即推送，结果另记。

- F15 提交 `a84a286`，立即推送仍 GitHub 403，未交付远端。

## F16：持久任务契约与需求版本 API

- 后端：task_store；API/集成/文档：主代理；PM与技术/Ponytail独立审查：task_contract_review，Pass。
- 从本会话 Owner 消息创建任务，明确负责人、目标标题、范围与验收；负责人必须为启用成员，归档禁止更改。
- 同请求原始参数幂等，编辑后重试仍返回当前任务；不可变版本历史、事务乐观并发检查，冲突 HTTP 409，无变化不升级。
- API 支持会话任务创建/列表、单任务读取/修改及历史；创建/修改不会自动调用模型或启动旧组织流程。
- 独立定向 Tasks/API/Run 15 passed；主代理完整回归 168 passed、8 subtests passed（30.35s）。覆盖真实HTTP、重启、重复创建、并发编辑、权限撤销及版本历史。
- 任务卡浏览器界面、任务Run/attempt绑定、真实执行与产物评审仍待实现；本功能不代表 R03/R07 完整通过。
- 提交标识：`feat(workbench): F16 persist versioned task contracts and API`；提交后立即推送并记录结果。
- F16 提交 `51059a0`，立即推送返回 GitHub 403：当前账号 suiyue1990 无目标仓库写权限，未交付远端。

## F17：聊天任务卡、编辑及历史界面

- 前端：task_ui；独立 PM/React/Ponytail 与契约QA：task_ui_review，Pass；主代理真实浏览器验收通过。
- Owner消息创建需求任务，卡片显示范围/验收/负责人/来源/版本；编辑409保留草稿并显式读取最新，历史只读；归档禁用编辑。
- POST前将原payload/request_id写入sessionStorage，未知结果锁字段，切会话/同标签页刷新后可继续核对；存储不可用不发送并提供恢复入口。
- 主代理真实HTTP丢响应注入：实际提交后断连、刷新、同键重试仍只有一张任务，恢复正常服务后仍持久；另以外部PATCH验证v2冲突→读v3→保存v4。
- Desktop和390×844截图/DOM、表单必填禁用、Escape、归档只读通过；无新增模型Run。完整证据与未测边界见 acceptance-report.md。
- 主代理最终TypeScript/Vite构建35模块通过；独立Tasks/API7 passed。前一后端全量基线168 passed、8 subtests，本轮未改后端。
- 执行/评审/产物、双Docker与CLI、记忆及最终Tauri文档仍待完成，未将任务卡保存当成任务执行成功。
- 提交标识：`feat(workbench): F17 manage task requirements in conversations`；提交后立即推送并另记结果。

- F17 提交 `07205fd`，立即推送仍为 GitHub 403，账号 suiyue1990 无目标仓库写权限。

## F18：任务执行身份、版本与停止状态记录

- 实现/集成：主代理；独立测试：execution_tests；PM/技术/Ponytail审查：execution_contract_review，Pass。
- 同SQLite新增内部Executions；task/version/attempt/UUID固定，外键指向不可变需求版本，幂等请求和单任务active唯一约束；全局活动上限100。
- 再次执行必须指向最新执行ID并留下核查说明，防止换key绕过和迟到重试引用错误前次结果。该说明仅为调用方声明，真实runner仍需核查进程与副作用。
- 旧版本排队不执行，旧版本确定退出仅归入superseded；错误attempt/version及重复终态回调不改变结果；零退出仅待评审，不修改任务需求或批准任务。
- 排队可取消，运行取消仅stopping并占位，确切进程树退出才能确认cancelled；未知/重启不自动重试，恢复调用要求控制器独占锁。
- 定向测试最终17 passed；主代理全量185 passed、8 subtests passed（32.53s），含同角色多任务、并发创建/领取、权限撤销、版本变更、停止/unknown、队列上限与恢复。
- 内部记录契约可用，但没有接HTTP创建、真实CLI、容器或产物验收；本轮不把注入的退出码当作真实进程证据。
- 提交标识：`feat(workbench): F18 persist version-bound task execution lifecycle`；提交后立即推送并另记结果。

- F18 提交 `23fcb30`，立即推送仍 GitHub 403，未交付远端。

## F19：独立 CLI 目录与 Windows 进程树控制

- CLI适配/集成：主代理；进程树及真实测试：managed_process；独立PM/技术/Ponytail审查：cli_review，修正后Pass。
- 独立执行UUID目录、HOME/CODEX_HOME/临时目录，固定Codex参数，stdin任务，环境白名单及单一显式API key；不复制登录、配置或会话，不复用旧执行目录。
- Windows挂起创建→加入Job→恢复，KILL_ON_JOB_CLOSE、有限双流输出、含stdin的时限，取消/超时/主进程退出清理孙进程，未知退出不返回虚假数值状态。
- CLI成功需退出0+有效完成事件/最终文本，失败事件或缺完成不通过；物理exit0而协议失败的success=false必须由后续控制器视为失败。原始stdout/stderr不进入API结果，凭据摘要隐藏。
- 审查修复默认目录祖先.codex误拒绝（固定project_root_markers=[]），修复带空格凭据规范化，禁用login shell；补NaN/inf超时与非法输出上限拒绝。
- 独立CLI/进程树20 passed；主代理全量205 passed、8 subtests passed（35.15s）。真实Python子孙进程覆盖退出、取消、超时、输出溢出与controller os._exit崩溃；本机Codex版本探针经相同Job后端返回exit0、codex-cli 0.153.3，未调用模型。
- 尚未接任务控制器、配置UI、checkout、产物验收；本机目录分离不是Docker/OS权限隔离，真实模型及双Docker验收仍待完成。
- 提交标识：`feat(workbench): F19 isolate Codex CLI directories and process trees`；提交后立即推送并另记结果。

- F19 提交 `ee0ef14`，立即推送仍 GitHub 403，未交付远端。

## F20：CLI 配置与显式版本检查

- 后端：cli_settings_backend；前端：cli_settings_ui；两代理交叉审查及主代理集成验收 Pass。
- 同 SQLite 独立配置，严格原子 PATCH；只保存环境变量名。读取/保存不启动程序，启用前检查完整性、平台和文件；内部 resolve 才读取凭据。
- 显式 probe 固定 --version，临时独立 HOME、无凭据、单进程并发锁、10秒与64KiB上限；不返回原始进程输出。
- 中文设置表单仅发送修改字段；未保存不能检查版本，保存不隐式启用。修复默认.cmd且禁用时保存无关字段被前端拦截。
- 后端/API定向26 passed；主代理全量231 passed、8 subtests（36.50s），TypeScript/Vite36模块构建通过。
- 真实浏览器版本0.153.3、禁用配置保存重开、缺失程序错误、恢复路径、Escape和390×844布局通过。未调用模型，未启动任务。
- CLI任务控制器、真实模型、双Docker、产物和记忆仍待完成。
- 提交标识：`feat(workbench): F20 configure CLI and probe installed version`；提交后立即推送并核对结果。

- F20 提交 `3809b74` 后立即推送返回 GitHub 403，账号 suiyue1990 无写权限；ls-remote未找到目标分支。

## F21：任务 CLI 队列与停止控制

- 实现/集成：主代理；独立QA：cli_dispatch_tests；PM/技术/Ponytail：execution_controller_review，修正后Pass。
- CLIController由现有控制器独占锁保护，复用同SQLite任务记录与CLI设置，按配置有限并发；上下文仅显式任务、角色和Owner来源。
- create/claim/snapshot/report检查execute权限与需求版本；存在unknown不允许仅靠文字说明启动替代实例，CLI全局队列暂停，聊天不受该暂停影响。
- 运行中取消、撤权、需求更新与关闭触发Event；等待可信进程退出再落终态。物理exit0且success=false保留0但记failed，只有明确成功才能awaiting_review。
- 结果Future保留直到落库成功，不因写入失败重跑；逐项处理避免阻断其他实例停止。submit异常无法证明未启动，保守unknown并取消可能入队任务。
- 独立QA19项；主代理额外4项故障测试，含真实本机Python进程经Windows Job取消并记录实际退出码。均未调用模型，不冒称真实Codex任务已通过。
- 本增量先交付内部后端能力；HTTP/UI执行入口、成果评审、unknown核查恢复、CLI费用约束及真实模型/双Docker仍待完成。
- 主代理全量回归254 passed、8 subtests（41.47s）；最后未启动/旧版本判定调整后定向40 passed（4.25s）。
- 提交标识：`feat(workbench): F21 dispatch and stop versioned CLI tasks`；按功能立即推送并回读。

- F21 提交 `d805178` 后立即推送仍 GitHub 403；远端目标分支未创建。

## F22：任务执行与停止 HTTP 接口

- 后端集成：主代理；独立HTTP测试与契约/Ponytail审查：execution_http_qa，Pass。额外审查代理触发任务数量限制，由现有QA交叉复核并由主代理检查暂存差异。
- 新增任务执行列表/创建、单执行读取/取消及CLI运行状态；不开放伪造结果报告或状态PATCH。
- 新请求要求CLI配置凭据就绪，再由事务检查权限、版本、前次执行和unknown；已存在的同任务/请求ID先严格核对原payload，即使配置后来禁用仍可幂等回读。
- 正常服务7892实测：cli-runtime200且零活动；已有task执行列表为空；disabled创建返回400，随后列表仍为空。未调用模型。
- 浏览器确认/历史/停止界面由独立前端增量实现；真实CLI模型任务和产物评审仍未验收。
- 主代理全量258 passed、8 subtests（45.56s）；之后新增并发/unknown用例，独立最终HTTP6 passed（6.11s）。
- 提交标识：`feat(workbench): F22 expose task execution lifecycle API`；提交后立即推送并回读。

- F22 提交 `1e6fd11` 后立即推送仍GitHub403；远端目标分支未创建。

## F23：浏览器任务执行、停止及断线核对

- 前端：task_execution_ui；独立PM/QA/Ponytail审查：execution_http_qa，修正后Pass；主代理真实浏览器验收。
- 每任务原生执行窗口、历史/版本/attempt/退出码/摘要、明确确认和前次核查、权限/配置/unknown拦截、停止与活动轮询。
- sessionStorage先保存精确请求；断线/刷新后沿原键核对。首次明确4xx允许显式撤销未接受请求，未知重放不允许撤销；确认绑定任务版本与最新前次执行ID。
- 修复StrictMode首次加载卡住、首次409无法恢复、前次执行变化沿用旧确认；复用现有布局与check样式。
- 正常Vite StrictMode7893首次打开正常，read-only负责人拦截执行。独立QA注入服务7894完成运行→停止→cancelled及再次执行→awaiting_review；真实HTTP旧版409，刷新保留拒绝状态并可撤销，未产生第三次错误执行。
- 独立代理7895在上游实际接受后丢弃响应并拒绝历史读取；刷新同标签页后原键核对返回原第三次execution，直接上游GET确认总计3条且原键仅1条。未调用任何真实模型。
- 390×844截图及DOM：scrollWidth390、dialog352；Escape关闭。已关闭QA代理/注入/Vite进程，正常7892服务继续运行。
- 最终TypeScript/Vite37模块构建通过。F22后端全量258+8子测试以及后补HTTP6项通过，本轮只改前端。
- 提交标识：`feat(workbench): F23 control task executions from browser`；立即推送并回读。真实CLI模型、成果评审、unknown恢复及完整目标仍未完成。

- F23 提交 `666d879` 后立即推送仍GitHub403，远端目标分支未创建。

## F24：不可变成果快照与下载

- 文件采集/存储：artifact_capture；控制器/事务/API集成：主代理；两侧交叉审查Pass。额外审查代理受任务数量限制，主代理亲自检查实现与故障断言。
- 成功CLI从固定execution-workspaces/{id}/work/artifacts读取文件；不信任runner返回路径。每文件4MiB、总16MiB、100文件、1000项/12层上限。
- Windows读取句柄固定祖先与文件，拒绝链接/重解析/硬链接/特殊文件/不安全路径；已实测目录重命名和文件写删在持有句柄时失败。当前密钥UTF8/UTF16和文件名命中拒绝，不宣称通用敏感信息扫描。
- SQLite不可变字节快照、大小与SHA256；只在合法awaiting_review结果同事务提交。采集失败保留真实exit0但failed，旧需求/撤权/停止结果不发布成果。
- GET execution artifacts返回元数据；download按ID读取快照并验证hash/size，attachment+octet-stream+nosniff，不将HTML作为站内页面执行。
- 主代理全量285 passed、8 subtests（46.81s）；最后补充路径用例后定向27 passed（1.89s）。包含原文件修改不影响下载、事务失败回滚、迟到回调不替换快照、跨Origin拒绝、未知ID404。
- 无真实模型调用；采集Windows-only，下载/持久层可独立读取。成果浏览器入口与Owner评审为下一增量，整体目标未完成。
- 提交标识：`feat(workbench): F24 snapshot and download execution artifacts`；提交后立即推送并回读。

- F24 提交 `0f0dbd7` 后立即推送仍GitHub403；远端目标分支未创建。

## F25：Owner 对确切成果的不可变评审

- 服务/API实现：主代理；独立QA、PM/Ponytail审查：owner_review_qa，22项测试通过，Pass。前端代理因任务数量限制未能启动，本增量单独交付服务能力。
- 每执行仅一个不可覆盖决定，绑定需求版本与完整成果ID清单，明确approved/rejected及说明；同键原内容重放可回读历史，换键或换决定拒绝。
- 新决定必须针对待评审、当前需求和最新执行，并再次检查负责人/会话/execute权限。批准要求实际非空成果且通过同下载的大小/hash/路径检查；拒绝可记录空成果或内容损坏的结果。
- BEGIN IMMEDIATE与唯一键确保并发只有一个决定；SQL UPDATE/DELETE/REPLACE禁止。保存评审不修改执行物理退出结果或需求历史，也不启动CLI/模型。
- HTTP GET/POST /executions/{id}/review，未评审返回null，提交201；不提供PATCH/DELETE覆盖。
- 后续需求或执行更新后，旧批准只属于原版本/执行，不能作为新任务版本验收；浏览器成果/评审入口仍待后续增量。
- 提交标识：`feat(workbench): F25 record immutable Owner artifact reviews`；提交后立即推送并回读。
- 主代理全量回归309 passed、8 subtests（52.17s）；独立评审结论Pass。

- F25 提交 da029db 后立即推送仍 GitHub 403（当前账号 suiyue1990 无目标仓库写权限）；远端目标分支回读为空，尚未远端交付。

## F26：浏览器成果下载与不可变 Owner 评审

- 前端子代理实现，主代理集成验收，Ponytail审查Pass；在执行记录内按需读取成果与决定，批准需查验确认和理由，拒绝需理由。
- 实时读取版本、最新执行及权限；评审独立于CLI配置。历史决定只读，版本未核对时不猜测完成状态。
- 请求先持久保存到sessionStorage，未知结果刷新后沿原键核对；首次明确拒绝才可撤销。错误读取不隐藏已回读的决定。
- 浏览器批准、空成果拒绝、下载、版本更新后的历史标识、上游实际保存后丢响应及刷新同键核对通过；远端/真实模型证据与QA固定样本分开。
- 最终构建38 modules，TypeScript通过；详细HTTP/浏览器证据见acceptance-report F26。
- 提交标识：feat(workbench): F26 review execution artifacts in browser；独立提交后立即推送和远端回读。

- F26 提交 63dedce 后立即推送仍 GitHub 403；远端目标分支回读为空，未远端交付。

## F27：版本化任务依赖与前置验收调度

- 主代理实现，dependency_qa 独立测试和Ponytail审查Pass；复用任务版本、SQLite事务、既有CLI队列和评审授权。
- 依赖修改生成新需求版本；限制同会话、无环、32直接前置和1000祖先，历史清单持久可读。
- 前置未通过当前最新验收时排队等待；claim冻结输入，运行/回调/评审递归复查；上游新版本/新执行不自动替换下游旧输入。
- HTTP GET/PATCH dependencies返回清单与阻塞原因；现有UI还未接入，CLI消费前置文件也未完成。
- 全量325项及8子测试通过；后补依赖22项独立QA和主代理复跑通过，详见acceptance-report。
- 提交标识：feat(workbench): F27 gate executions on versioned task dependencies；提交后立即推送并核对远端。

- F27 提交 0ddec29 后立即推送仍 GitHub 403；远端目标分支回读为空，尚未远端交付。

## F28：前置成果副本传递到下游CLI

- input_materialization实现输入校验/落盘及定向测试，主代理实现同事务授权读取与控制器集成；交叉Ponytail审查Pass。
- 只读直接冻结前置的批准清单；校验ID全量匹配、大小/hash及100文件/4MiB单文件/16MiB总量。新执行inputs/{artifact_id}独立副本，不沿用原名配置或其他工作区。
- prompt提供元数据映射，状态轮询不加载文件字节；准备失败明确not_started，真实runner异常仍unknown，后置依赖失效保护保留。
- 全量358项及8子测试通过，真实文件/SQLite与注入进程跨层证据见acceptance-report；未调用真实模型，双Docker与依赖UI仍待完成。
- 提交标识：feat(workbench): F28 copy approved dependency artifacts into CLI inputs；单独提交后立即推送并回读。

- F28 提交7e79377后立即推送仍GitHub403，远端目标分支回读为空。

## F29：浏览器配置前置任务与版本恢复

- dependencies_ui前端实现；主代理契约/Ponytail审查和真实浏览器验收Pass，复用原生dialog/checkbox和既有API，无新依赖。
- 同会话选择/清空、历史稳定ID、前置等待与满足、归档只读；保存新增需求版本，执行窗口显示等待原因并拦截已变化版本确认。
- 原版本请求sessionStorage持久保留，未知响应刷新可重试；409须明确对照后重编辑，读取不覆盖草稿、不误认状态相同为请求确认。
- 浏览器普通/循环拒绝/版本冲突/上游保存后丢响应/恢复/归档只读均通过；构建39 modules及TypeScript通过。详细证据见acceptance-report F29。
- 提交标识：feat(workbench): F29 configure task dependencies in browser；单独提交后立即推送并回读。

- F29 提交 babc379 后立即推送返回 GitHub 403，目标远端分支未交付。

## F30：个人与项目批准记忆服务

- memory_store 子代理实现 SQLite 版本记忆及 21 项测试；主代理集成 HTTP、claim 冻结和 CLI 上下文，添加 2 项跨层测试。memory_integration_review 独立审查核心及集成 Pass。
- 个人稳定身份和群共享范围分离；候选有批准成果来源，批准生效，回滚产生新版本；精确请求重放、版本并发冲突、不可变历史和冻结快照通过验证。
- 复用原有成果校验与事务，无新增依赖。模型候选生产、记忆浏览器入口和 OS/Docker 真隔离仍未完成。
- 提交标识：feat(workbench): F30 persist approved scoped memory and freeze execution context；本功能单独提交后立即推送并回读。

- F30 提交 b3029be 后立即推送返回 GitHub 403：当前 suiyue1990 无目标仓库写权限；ls-remote 目标分支为空。

## F31：浏览器个人与项目记忆管理

- memory_browser_ui 实现 MemoryPanel、类型与入口；主代理完成契约/Ponytail审查、独立浏览器测试及问题回修验收。沿用原生 dialog、表单和现有 API，无新依赖。
- 当前会话批准成果通过任务名称选择；候选全文、来源、批准/拒绝、版本历史、回滚均可操作。个人与非私聊项目范围分离，停用/归档保护和失效候选拒绝保留。
- POST 发送前持久保存原路径/请求键/内容；断线刷新同键核对，版本冲突明确对照后重新编辑，草稿不被读取覆盖。未知读取不宣称空数据。
- TypeScript/Vite 40 modules 构建通过；真实 IAB 普通审批、回滚、断线重试、409、归档拒绝通过，证据详见 acceptance-report F31。
- 提交标识：feat(workbench): F31 manage approved agent and project memories in browser；单功能提交后立即推送并回读。

- F31 提交 d9b70e0 后立即推送仍返回 GitHub 403；目标远端分支回读为空。

## F32：Tauri 2 迁移契约

- 主代理依据实际源码与官方资料撰写 docs/tauri-migration.md；tauri_boundary_review 独立核对通信、下载、初始化锁、provider冻结、数据和待确认请求差距。
- 明确静态前端复用、小型Rust宿主、Python业务保留、鉴权握手、有序退出、模板资源、凭据、下载、WAL备份和升级回退；D1–D6分工与验收门可逐项实施。
- 本增量是明确要求的迁移规划文档，未创建Rust空壳、冻结exe或安装器，不宣称完成桌面能力。浏览器剩余功能与真实双Docker/模型验收保持未完成。
- 提交标识：docs(workbench): F32 define Tauri migration and acceptance boundaries；独立文档增量审查后单独提交并立即推送。

- F32 提交 a60af43 后立即推送仍 GitHub403，远端目标分支回读为空。

## F33：未知执行 Owner 核查服务

- execution_reconciliation 子代理负责不可变核查模块和单元测试；主代理集成控制器持有检查、全局/任务阻塞门、HTTP与跨层测试。
- 保留unknown历史和未观测退出码，追加人工核查声明；只有所有未知项核查后才恢复已授权队列；新尝试仍需前次引用与当前权限。核查不复活旧回调、不批准成果。
- 本增量仅后端能力；浏览器核查入口、自动进程识别与检查点恢复未完成。
- 提交标识：feat(workbench): F33 reconcile unknown executions without rewriting outcomes；测试审查通过后立即独立提交并推送。

- F33 提交 fcf911a 后立即推送返回 GitHub 403，目标远端分支回读为空。

## F34：浏览器未知执行核查

- reconciliation_ui 实现四个前端文件；主代理负责独立数据、断线代理、真实浏览器和重启回读；corppilot_backend_review 完成独立 Tech Lead/Ponytail 审查 Pass。
- 全局入口展示任务与身份、原 attempt/version、核查依据与两项显式确认；归档停用仍可处理历史。核查声明与 unknown 结果分别显示，任务窗口仅对未核查项阻断。
- 原请求持久保存后才发送，断线同键恢复；不同不可变声明成功读回后可明确结束本地等待，不覆盖、不误称原请求成功。
- TypeScript/Vite 41 modules 构建通过。浏览器普通保存、双确认、断线刷新/GET503、历史v1对应当前v2、归档停用、并发声明冲突、任务入口与重启回读通过；四条固定unknown与四条声明，无实际模型/CLI调用。
- 提交标识：feat(workbench): F34 reconcile unknown executions from browser；独立提交后立即推送并回读。

- F34 提交 3fe9cf7 后立即推送仍返回 GitHub403：suiyue1990 无目标仓库写权限；目标实施分支远端回读为空。

## F35：Owner 批准协作项目组建服务

- corppilot_backend_review 完成 PM/Tech Lead/Ponytail 方案与成品审查Pass；task_execution_ui 本轮承担后端角色，负责 collaboration.py、共享Tasks创建事务与单元测试；主代理集成HTTP与跨层测试，verify_history_scope 承担Doc/PM文档核对。
- 从源会话的Owner消息组建独立项目：成员仅协调人与任务负责人，显式共享摘要为新群源消息，1–16个任务及批内DAG依赖一次原子保存，任务初始v1。源私聊历史和其他身份记忆不复制，不授予额外工具，不启动模型/CLI。
- 复用现有任务、依赖和执行权限；不可变收据返回原approved_plan及任务映射，幂等重放不受后续任务修订、归档或停用影响。错误循环或中途写入失败全量回滚。
- 定向30项测试通过；完整回归结果见验收报告。本增量为后端与HTTP能力，浏览器计划编辑/确认、模型提案、技能路由和自动组队后续继续实现。
- 提交标识：feat(workbench): F35 atomically create approved collaboration projects；测试审查通过后独立提交并立即推送。

- F35 提交77d3132后立即推送仍返回GitHub403，目标实施分支远端回读为空。

## F36：聊天中的协作计划编排与恢复

- reconciliation_ui负责CollaborationPanel及三处前端接线；corppilot_backend_review独立Tech Lead/Ponytail审查Pass；主代理完成开发StrictMode与构建页面浏览器测试；verify_history_scope负责文档更新。
- Owner消息入口可填明确共享摘要、协调人、1–16任务和按名称选择的依赖；循环阻断，成员与完整计划确认后生成真实项目。全局入口恢复原请求及查看不可变批准历史，打开项目仅GET并按ID导航。
- 首次明确拒绝后可显式重新编辑；原请求再次发送先去除旧拒绝标记，之后结果未知时禁止创建替代项目。GET回读完整比较approved_plan，不只核对请求ID。
- TypeScript/Vite42模块通过，浏览器普通两任务/依赖、首发400重新编辑、重试后响应丢失+GET503+刷新换会话+归档来源、历史改名回读通过；实际数据仅两个项目/三个任务，无模型/CLI运行。
- 提交标识：feat(workbench): F36 compose collaboration projects from chat；按单功能测试审查后立即提交并推送。

- F36 提交 `4f24ea0` 后立即推送仍返回 GitHub 403；此处补记上轮实际记录，不代表本轮重新推送。

## F37：Owner 候选白名单内的模型协作提案

- corppilot_backend_review 负责设计审查；task_execution_ui 负责后端；主代理集成 Runs、控制器与 HTTP；verify_prompt_push 独立成品静态审查 Pass；verify_history_scope 负责 Doc/PM 文档核对。
- POST严格接受agent_id/source_message_id/request_id/candidate_ids，1–100个启用候选按原顺序冻结公开id/name/template_id/skills。上下文仅目标Owner消息、协调人指令与候选资料；执行前和完成前分别复查权限，不复制其他历史或个人记忆。
- 提案复用Run ID和聊天并发/RPM/100活动上限，配置未就绪保持queued；取消沿用Run接口，失败/重启unknown不自动重试。严格JSON、白名单和1–16任务DAG校验；成功只写提案与Run状态，不写普通聊天消息或创建项目/任务/CLI执行。
- 根集成定向8项通过，既有规划/Runs定向23项通过；完整440项及8子用例通过。随后只增加5条测试、生产代码未改，规划模块最终20项通过；未再跑全量，详见验收报告。
- 本增量纯后端/API；提案浏览器入口、导入F36并再次Owner确认、真实供应商/CLI/Docker仍待完成。模型建议不是approved_plan，不直接触发F35组建事务。
- F37实际提交 `502b96202d3b6692b4d204c2db2b72f9d9f9f34d` 后立即推送，被GitHub 403拒绝：当前账号suiyue1990无仓库写权限；目标远端分支回读为空。外部证据为 `f37-delivery-result.json`，本地提交完成、远端交付仍阻塞。
- 提交标识：`feat(workbench): F37 generate scoped collaboration proposals`；测试与审查通过后单独提交并立即推送。

## F38：浏览器模型提案与导入确认

- task_execution_ui负责前端实现与停用候选修正；verify_prompt_push负责PM/Tech Lead/Ponytail独立审查；主代理完成浏览器QA、集成与过期提示清理；verify_history_scope负责Doc/PM文档核对。

- 已接入Owner消息与全局提案入口：显式候选及模型调用确认、同键原请求恢复、202后按持久Run ID读取。首次明确4xx可修改，原请求重试清除旧rejected标记，未知结果不可创建替代请求；完成结果不因后续GET失败丢失。
- completed提案导入F36只填可编辑草稿和新创建请求，确认默认未勾选；本轮Owner修改标题与摘要后再次确认才建项目。已有F36 pending阻止导入并提供原计划恢复入口。选定身份停用后仍可取消选择，取消后不可重选，修正后浏览器复验通过。
- 独立成品审查Pass；主代理typecheck及Vite43模块构建通过，最终index-Dx-oq0Yc.js。CUA Playwright覆盖正常模型提案、202恢复、首发400后202丢响应/GET503/跨会话恢复、F36建群201丢响应pending冲突、非法JSON失败和排队取消；1315×1272及1280×720桌面交互通过，未单测console/mobile。
- 独立f38_verify.py回读4个Run（2完成/1失败/1取消）、3次实际本地HTTP模型调用、2项目4项v1任务、0CLI及无私聊前史泄漏。模型为本地7900 fixture，不是真实供应商；本轮纯前端，沿用F37后端证据，不重跑全量。
- QA服务重启前后f38_verify数据一致、数据库完整性通过，浏览器恢复原cancelled Run与历史；QA服务/代理/Vite均经实际句柄确认退出。代理有客户端取消写入的WinError10053，不宣称console/代理无错误；正常7892未重启且最终bundle已HTTP回读。
- 网络受理未知同键恢复已测；服务端Run=unknown仍无模型人工核查/解除入口，F33仅覆盖CLI。storage损坏/QuotaExceeded只有静态fail-closed审查，未注入浏览器；console/mobile及真实供应商/CLI/Docker仍保留未验证边界。
- 提交标识：`feat(workbench): F38 review model proposals in browser`。实际提交 `c592eae` 后立即推送返回GitHub403，目标远端分支回读为空；外部证据 `f38-delivery-result.json`，远端交付仍阻塞。

## F39：Owner API访问鉴权

- 主代理负责后端、集成与QA；verify_prompt_push负责前端；task_execution_ui负责Tech Lead独立审查；verify_history_scope负责Doc/PM文档核对。
- F39先交付Owner控制面鉴权；Docker实现另列F40，未包含在本次独立提交与验收内，其后续状态见F40记录。
- 每启动随机token保护全部/api/workbench读取、写入和下载；静态及基础health不需token。主入口自动打开fragment授权页且不打印秘密，失败生成宿主数据根临时owner-access入口并在退出删除；浏览器清fragment、sessionStorage保存凭据、下载改认证fetch/blob。
- 401以AccessExpiredError保留业务pending。主代理7897浏览器验证任务201已落库响应丢失、刷新后401、重新授权同键回读，数据库仅1任务。33字节成果UI下载点击无错误、HTTP字节一致，未核验浏览器保存文件；未调用真实模型或CLI。
- 最终44模块构建index-BLeLdit0.js，定向23项通过；排除全部未提交Docker改动的F39纯暂存树导出后，全量448项及8子用例通过（75.04s），其后仅更正1行非行为注释。此前含Docker的工作树488项及8子用例仅为历史证据；401/fallback独立审查Pass，静态目录保护已落实。
- QA服务真实停止重启后旧token401、重新授权恢复同1task；旧代理和重启后的95112服务均已确认退出，本轮QA服务已停止。token默认仅驻宿主内存，仅自动打开失败时生成运行期入口HTML并在退出删除，该文件已加入Git忽略，文档不保留凭据。
- 不隔离同OS用户、不解决任意网络出口；Tauri私有握手仍规划。F39实际提交 `36acd48` 后立即推送返回GitHub403，目标远端分支回读为空；外部证据 `f39-delivery-result.json`。真实Docker/供应商/CLI验收保持未完成。

## F40：Docker Worker 适配与恢复（真实容器尚未验收）

- 主代理负责后端集成、浏览器QA与最终回归；Tech Lead负责独立技术审查及36项定向复验，结论Pass；verify_history_scope负责Doc/PM及Windows/Docker只读预检。
- 复用CLI队列，固定本机daemon与已安装Linux sha256镜像，probe只读且不拉取。每执行独立非root容器，仅run/work可写和inputs只读挂载，HOME/tmp为tmpfs，限制CPU/内存/PID；create/start/inspect/stop-kill/remove均核对原实例，不能把Docker客户端退出当作容器退出。
- 原backend与工具路径不可变绑定到SQLite；run根身份记录不挂载给容器。模型key经attach stdin进入Codex，不写Docker参数、Config.Env或登录配置；同容器同UID凭据可见性及bridge网络出口仍有边界。镜像Codex固定0.153.3，内层danger-full-access依赖外层容器隔离，详见 [Docker Worker](docker-worker.md)。
- 新增受F39鉴权保护的Worker状态/停止入口。未知机器状态不可保存已停止声明；stop后仍需回读，exited不自动声明，提交前再次检查状态。既有不可变收据按原请求重放不依赖daemon，配置改变不能替代原工具绑定。
- 浏览器已保存禁用Docker配置并得到真实probe失败；受控状态验证unknown阻断、两次停止、exited后提交竞争400保留pending、absent同键保存。stop helper调用2次、真实容器/模型/CLI均0；最终44模块产物index-BQ5sRMyd.js。首轮508通过/3失败的Path入SQLite问题已str归一，定向10项通过；最终主代理全量 **511 passed、8 subtests passed（78.33s）**，会话88456退出0；Tech Lead独立 **36 passed（5.63s）**、Worker定向 **37 passed**。
- 主代理确认QA服务12041仍运行后，从IAB新标签页22重新进入7896，恢复同1任务/1次unknown、未确认退出码及完整Owner核查说明；Docker配置仍禁用，原测试镜像、模型/环境变量名和CPU1/1024MiB/PID128均保留。随后Ctrl+C停止12041，实际终端退出1；无真实容器、模型或CLI调用。
- 外部 `f40-verification.json` 已保存；只读SQLite回查为1任务/1执行/1核查收据、0成果/0模型Run，原执行unknown及exit_code=NULL，integrity_check为ok。核查未重写结果或触发替代执行。
- Windows VMP读取0x80040154，CheckHealth正文报告组件存储无法修复；Docker安装注册缺失、WSL2不可用。保留式Windows修复与重启授权待答，未系统写入，未操作alpine-ai-yss。真实镜像构建、双Worker隔离、真实供应商/CLI与恢复验收保持未完成。
- F40实际提交 `a632631` 后立即推送返回GitHub403（当前账号suiyue1990没有目标仓库写权限），目标分支回读为空；外部 `f40-delivery-result.json` 保留哈希与推送结果。正常工作台已重启加载新版本，页面200、业务匿名访问401；不能因本地实现或替身测试宣称远端交付。

## F41：已验收成果的模型复盘后端

- PM/Tech Lead预检与独立审查：verify_history_scope；后端：task_execution_ui；主代理负责HTTP集成、最终回归、文档及提交。复用Runs、Memories审批和回滚，不新增队列或依赖。前端另列F42，本提交不包含界面文件。
- Owner指定来源执行、目标记忆版本及批准成果选择；输入冻结实际有界UTF-8正文、任务需求和目标批准全文，未选正文、其他范围个人记忆及聊天前史不进入模型。复盘输出完整替换候选与所选证据ID，不自动批准或发布Skill。
- 新增18项复盘边界测试，既有相关回归共67项通过；主代理全量532项及8子用例通过（85.60s），之后仅增加1项隐私测试、生产代码未改，最终HTTP测试4项通过（5.96s）。实际本地HTTP模型替身与请求子进程验证创建、单次调用、候选、人工批准、回滚、重启、取消与失败；不是付费供应商质量验证。
- 独立定向21项通过（6.33s）；额外临时数据核验跨范围隐私、未选证据及撤销execute权限均通过，审查Pass。创建/完成事务失败不残留候选，running重启unknown不重发，批准前不改变有效记忆。
- 提交标识：feat(workbench): F41 generate scoped retrospective memory candidates；实际提交 `ffb7c3a` 后立即推送返回GitHub403，远端实施分支回读为空，外部证据 `f41-delivery-result.json`。F42浏览器入口、Skill发布、真实模型质量和完整目标仍待验收。

## F42：浏览器复盘、审批与原请求恢复

- 前端为MemoryPanel、RetrospectivePanel和api.ts；task_execution_ui独立读审Pass，主代理负责浏览器QA与集成，verify_history_scope负责Doc/PM文档核对。当前typecheck及45模块构建通过，产物index-CVd9BLsW.js；沿用F41后端完整532项/8子用例及随后HTTP4项证据，未重复后端全量。
- 个人/项目记忆内选择已批准来源与成果，单独确认一次模型调用；候选生成后仍须原全文审批。scope/identity内持久保存原请求，202后只GET，返回精确匹配完整payload及Run；首发4xx才可重新选择，原请求重试清除旧rejected，unknown不提供替代调用。MemoryPanel原候选/决定/回滚同时修正旧拒绝标记。
- 已测项目v0仅选择selected.txt生成候选、批准版本仍v0、手工草稿保留，再明确Owner审批后才v1。第二请求首400后同键202丢响应/GET503，旧拒绝修改入口关闭；刷新保留原key，恢复GET核对同Run及候选。两个有效请求各一次本地模型调用，未选正文及个人记忆泄漏检查均false。
- 第3个Run在模型禁用时queued，经UI取消为cancelled且无candidate/模型调用；第4个Run非法JSON为failed且无candidate。累计本地模型调用3次（2有效+1非法），取消0调用。
- MemoryPanel手工候选首400后同键201丢响应，rejected=false且重新编辑按钮消失；刷新仍保留1ab99600原key，候选GET可见不等于原请求已确认。1315×1272桌面截图确认对话框可滚动、表单可操作。
- 旧fixture93937确认live后Ctrl+C退出1；新92039同数据重启，旧授权401触发AccessGate。重新授权后完整1ab99600 payload及rejected=false保留，手动同键回读成功才清pending，项目批准记忆仍v1；未记录token。
- 外部f42-verification.json已保存：只读SQLite为4个Run（2完成/1取消/1失败）、4候选（个人种子1/模型2/手工1）、2决定/2修订、1任务/1执行/2成果，原手工memory_requests仅1条，integrity_check为ok。实际本地fixture模型3次，未选正文/个人记忆/聊天历史标记无泄漏。
- 新测试服务92039经持有句柄的Agent确认live后Ctrl+C退出1，主代理核对7896/7897均无Listen，f42-verification.json已补入最终退出结果。本地浏览器及测试服务收尾完成；console/mobile未专项测试，真实供应商经验质量、CLI、Docker与完整目标未验收，F42提交及推送须待实际操作记录。

- F42 实际提交 `55ff1ee922ca465cc3da826965d53cb410178738` 后立即推送退出 1，GitHub403：suiyue1990 无写权限；远端回读退出 0、`remote_head=null`。已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f42-delivery-result.json`，远端交付仍阻塞，此记录替代上文待提交描述。

## F43：批准计划原子批量执行后端

- task_execution_ui 负责后端实现；主代理负责 API、调度集成及最终测试；verify_history_scope 负责独立技术审查及容量测试；verify_prompt_push 本轮负责三份文档核对，未独立重跑测试。F44 界面另行验收和提交。
- Owner 选择原协作回执的 1–16 项任务，以当前版本、前次 execution ID 与再次执行核查说明原子入队；任何一项失败均回滚。不可变批次回执保留请求及每任务固定 execution ID。
- 复用 Executions 和 CLIController 队列、依赖与 max_concurrency，不新增调度层；后继等待前置 Owner 批准，未选前置不自动执行。停止仅针对本批绑定实例，不停止替代实例；unknown 保留并沿用既有人工核查门。
- 批量 API、固定批次读取/停止和仅项目适用的 origin-plan 均受 Owner 鉴权。首次创建核查配置，精确同键原内容重放只回原回执，异内容冲突拒绝。
- 主代理报告定向 **18 passed（4.17s）**，包含实际 tick/线程池配合受控 runner 的 A/C 同时运行、A 批准后 B 自动启动；不是实际 CLI、模型或 Docker 验收。最终主代理全量 **551 passed、8 subtests passed（93.25s）**，session65228 退出0；独立审查Pass、相关 **57 passed（10.60s）**。全量之后仅新增2项容量测试、产品代码未改，独立 **2 passed（1.07s）**，主代理复跑 **2 passed（1.28s）**，不虚构包含新增测试的第二次全量结果。
- F44 未完成浏览器验收；F43 当前提交/推送尚待实际操作记录，真实执行环境及整体目标仍未验收。

- F43实际提交 `47c4086d1f65326191ffaf12ba12491b6c161574` 后立即推送退出1、GitHub403，远端回读退出0但 remote_head=null。已直接核实外部 f43-delivery-result.json；F44前端未包含在该提交内，远端交付仍阻塞。

## F44：浏览器协作批量执行

- verify_prompt_push负责前端实现及文档；主代理补齐当前任务scope/acceptance展示、集成、构建与浏览器QA。独立审查所发现的非project origin查询、已核查unknown重试阻断已修复并Pass。
- 回执及项目入口选择原计划任务当前版本，明确确认范围、验收、前次实例及再次执行说明后整批入队；依赖仍需Owner批准，未选前置不自动执行。完整请求独立持久化，202后只GET，首4xx重试清旧拒绝，401/丢响应保留同键。停止只针对绑定Run，响应未知只GET核对。
- 真实IAB7896、1315×1272：DM历史/project origin入口通过；3任务一次入队A/C并行，A批准后B自动启动并消费1input。首400同key202丢响应/GET503/刷新保留完整请求，GET恢复不新增POST；fixture有序重启口令401后重新授权保留batch。第三批C停止丢响应后stopping→cancelled，reload后stopPOST仅1。
- 已核实外部f44-verification.json：3batches/5executions（4awaiting_review、1cancelled）/4artifacts/1review/1input，5unique受控runner，全部成果hash及数据库完整性通过；真实CLI/model/Docker均0。unknown UI未注入，仅静态独审和后端证据；console/mobile未专项。
- 最终typecheck/build通过，46modules、index-DNIGCKAo.js，已截图。F43后端测试沿用原记录；测试fixture原38539与重启31647均先确认实际句柄仍运行，再Ctrl+C退出1；最终7896无监听。正常7892旧26019在数据库无活动执行后有序停止，新69967启动健康检查200并回读index-DNIGCKAo.js，新增批次表为空、任务执行0、原1个unknown模型Run保留，未注入测试数据。F44 实际提交 `02b364144b890663efc3276a2dc81c65f34818e1` 后立即推送退出1：GitHub403（suiyue1990 无写入权限）。远端回读退出0但 remote_head=null；已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f44-delivery-result.json`。本地验收和提交不代表远端交付。

## F45：未知模型请求核查与范围门禁

- task_execution_ui 实现领域层与13项定向单测，主代理负责控制器所有权挡板、HTTP及集成测试；复用 Runs，不新增队列。不可变声明不改旧 Run、model、usage 或 RPM 账目。
- reply/planning 按会话、来源消息、身份与类型挡住未核查 unknown；retro 按目标范围及来源执行挡住，其他任务不互锁。pending 过滤防 RPM 消耗，claim 事务复查；精确旧 key 回读优先，声明可放行已有授权 queued，新意图须新 key，原 unknown 永不重跑。
- futures/unsettled/uncertain_submissions 仍持有时拒绝首次声明；提交结果不确定需完整关闭线程池/服务并重启后核查。API 沿用 Owner 鉴权，已保存声明在配置停用后仍可精确回读。
- 主代理定向 **29 passed（11.16s）**；全量 **570 passed、8 subtests passed（97.96s）**，session88107 退出0；独立QA **15 passed（2.19s）**、审查Pass，含F45全部新增测试。提交不确定错误文案不再声称请求已退出。F46 仅草稿 typecheck，不计浏览器完成；F45 提交/推送尚待主代理操作，整体目标未完成。

F45 实际提交 `5b37cad73e97607f8f8f6709e779d060036a748d` 后立即推送退出1、GitHub403（suiyue1990 无写权限）；远端回读退出0且 remote_head=null。已直接核实外部 `f45-delivery-result.json`，F46草稿未包含在此提交；远端交付仍阻塞。

## F46：浏览器模型 unknown 核查与新请求恢复

- 独立typecheck、静态Ponytail审查Pass；主代理构建47 modules、index-DdLyA6xU.js。真实IAB1315×1272测试独立7896服务，三类型unknown列表与核查入口、首400→重启401新授权→同key201丢响应/GET503/reload保留pending→GET恢复通过。
- 普通回复重新选择来源/member后确认新key；规划显式接受同Run另一声明冲突后重选coordinator/candidate；复盘从历史核查后重选source/artifact并确认。各自生成一个新completed；规划不建项目、复盘只产生待批准候选，v0不改。
- 文档代理直接运行只读f46_verify.py：原三unknown逐字段未变、三声明唯一、新三Run各对应一次本地HTTP模型调用、旧Run零调用、无额外消息，SQLite及外键完整性通过。全部6Runs/4messages/1memory_candidate；状态注入种子不是供应商真实故障，真实供应商/CLI/Docker调用0。
- 已直接核实f46-boundary-review.json，storage损坏及规划/复盘pending GET503三项Pass；writes=[]、非预期console/page错误均空，预期503信息2条单列。主代理已看1280×900桌面与390×844移动截图，移动dialog在视口内。F46本地fixture验收Pass。沿用F45后端回归，不虚构新的全量。F46 已本地提交，立即推送遭 GitHub403，远端回读为空；详见下方实际交付记录，整体目标仍未完成。

最终补充：主代理模型核查后端/控制器/API定向 **17 passed（4.39s）**，不记作新全量。正常7892服务原session69967先确认仍live再Ctrl-C退出1，保留browser-state由session19278重启；health200并回读index-DdLyA6xU.js，正常库仍只有历史unknown模型Run1、任务执行0、批次0，未作fixture写入。最终测试fixture session74866先poll确认live再Ctrl-C退出1，已停止；主代理重新运行f46_verify并落盘最终证据。真实供应商/CLI/Docker及整体目标未验收，F46 已本地提交，立即推送遭 GitHub403，远端回读为空；详见下方实际交付记录。

F46 实际交付记录：已直接读取外部 `H:\item\CorpPilot-test-evidence-20260906\f46-delivery-result.json`，提交 `18b5c481bd2cbb5d07cd21e953bf04f337673bb9` 成功后立即推送退出1，GitHub403：suiyue1990 无权写入 xiaoyangtx996/CorpPilot；远端回读退出0、`remote_head=null`。该记录报告提交后工作区干净，不能据此称远端交付完成。主代理已通过 git show 核实该提交为10文件、195行新增/20行删除。
## F47：Owner授权同群Agent单次评议后端（已验证）

- PM/TechLead需求技术门Pass；采用独立peer-reviews创建/列表/详情API，复用Runs/ReplyController队列，普通reply来源校验和列表语义保持。peer_review_requests冻结授权、源正文、来源Run和目标模板；不新增provider或调度框架。
- 同群不同启用成员，来源须真实completed Run发布；快照和发布复查权限。一次授权仅一个目标调用，无自动续轮，允许Owner另行授权下一轮。精确幂等回放、跨kind冲突和F45独立unknown范围均为验收门。
- task_execution_ui完成核心实施与测试，主代理集成严格HTTP用例并亲读五模块与测试；verify_history_scope独立QA/TechLead/Ponytail审查Pass。
- 主代理定向25项通过（7.61s，当时尚未加入冻结角色单用例）；随后新增冻结角色测试单项通过（0.22s）。完整 pytest tests/ -q 为 **596 passed、8 subtests passed（105.83s）**，session37270退出0，包含冻结角色和HTTP严格字段用例。独立QA/TechLead/Ponytail审查Pass，四文件定向41项通过（9.88s），另复跑冻结角色1项通过（0.22s）；这是分次验证记录，不相加成42个不同用例。
- 原生控制器及本地HTTP provider子进程A→B各调用一次、8同key只1peer、unknown声明后新key一次、返回前撤B不发布、重启精确回读均通过。F48前端已通过本地fixture浏览器验收，详见F48验收记录；真实供应商/CLI/Docker未验收。F47已本地提交并立即尝试推送，403阻塞；实际记录见下文。

F47 实际交付：直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f47-delivery-result.json`，本地提交 `a4f7875c73bc29d0d1570b4e3aaaf9f17f2562c4` 成功，11文件、461行新增/19行删除；随即推送退出1，GitHub403（suiyue1990无目标仓库写权限）。远端回读退出0但 `remote_head=null`，因此远端交付仍阻塞；F48五个前端文件未包含在F47提交中。

## F48：群内逐次授权评议界面（本地fixture验收Pass）

- verify_prompt_push实现五个前端文件，复用F47 API和原模型核查；根/独立审查两处恢复缺陷已修复：POST须独立GET精确核验后才可释放，unknown声明复查失败撤旧许可且保留pending。普通reply路径保持。
- 48modules构建index-BDbMUDFL.js通过且之后无代码修改。主代理已读四份浏览器脚本与报告、看桌面/手机/发布截图并CUA确认历史；主流程3项、边界3项、401重启恢复及queued取消均Pass。精确恢复和来源链接证据见[验收记录](acceptance-report.md#f48-群内评议界面验收本地fixture验收pass)。
- 最终外部f48-verification.json通过：7runs/7messages/5peer/1声明/0候选/0CLI，新3completed各1次本地HTTP调用、1cancelled零调用，原Run及消息未变，DB完整性通过。31张正常原表逐行同备份；fixture96419已确认live后退出1，7896/7897无监听，正常68562保留。
- PM/QA/TechLead/Ponytail本地增量Pass；本轮不记录provider输入，隐私证据沿用F47，不扩大为真实供应商/CLI/Docker验证。F48已本地提交，立即推送403阻塞，远端未确认；实际记录见下文，整体目标未完成。


F48实际交付：已直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f48-delivery-result.json`，本地提交 `43be70154bc7d373932a0cd5f14ce672fbfef48f` 成功，9文件、179行新增/13行删除；立即推送退出128，GitHub返回403（suiyue1990无写权限），远端回读退出0但remote_head=null。记录中working_tree_status为空，提交后工作树干净；本地验收与提交不等于远端交付成功。


## F49：批准计划原子创建并启动后端

- PM与Tech Lead确定一次明确批准完整计划，原子建群及首批入队的范围；自动规划、中间成果自动交接和费用硬门继续保持未完成。
- task_execution_ui持有五个后端文件及核心13项测试；主代理亲读实现并独立新增三项HTTP测试，verify_history_scope负责独立Ponytail/QA与并发关闭审查。前端F50独立开发，不混入本次提交。
- 主代理HTTP三项3.44s通过，完整612项及8子用例112.34s通过（session40355退出0）；详细证据见acceptance-report.md的F49段。新增审查测试单列，不伪合入全量。
- 新API复用原创建/批次事务与队列；同键恢复、旧授权不扩大、全事务回滚和固定实例停止为验收门。F49已独立提交并立即推送403，远端未确认，实际记录见下文；真实CLI/模型/Docker及整体目标未完成。


F49独立审查补充：QA/Ponytail审查Pass，定向54项通过（7.73s）。新增两个Event/RLock受控并发用例验证启动先行时关闭等待原子提交、关闭先行时拒绝新请求但允许精确回读；主代理亲读并复跑 **2 passed（0.42s）**。这两项在612项全量收集之后新增，未计入该全量；产品代码未再修改。PM及主代理对F49后端/API增量验收Pass，F50浏览器仍待验收。


F49实际交付：直接核实外部 `H:\item\CorpPilot-test-evidence-20260906\f49-delivery-result.json`，提交 `6e68e864ed6804972f13cd03e4cc92c1fa32ca3b` 成功，12文件、553行新增/67行删除；立即推送退出128、GitHub403（suiyue1990无写权限），远端回读退出0但remote_head=null。提交后仅三个F50前端文件未提交，F49本地验收和提交不等于远端交付。

## F50：完整计划一次批准并启动界面（本地fixture验收Pass）

- verify_prompt_push持有三个前端文件，复用计划预览及既有批次控制；原仅创建保留，新增明确批准并启动全部首次任务。独立操作/payload持久化、GET精确核对和固定batch定位，未新增通用框架。
- QA退回停止确认跨batch、回执结构核验不足及F44过宽互斥，已按当前批次与请求范围修正。静态QA/typecheckPass，root48modules构建index-Bl2XnWmG.js通过；浏览器主流程两项、完整边界八项及同库重启401恢复通过，详细证据及边界见acceptance-report.md的F50段。
- 本次仍需Owner依赖成果审批，不是自动规划/无审批交接/货币预算硬门；真实CLI/模型/Docker与全目标未完成。F50提交及推送待实际操作。


F50主代理最终复核：1launch/1batch/3executions、3unique受控runner、2产物hash及B消费A唯一输入均通过；主代理IAB历史与绑定状态、桌面/手机截图已核对。正常服务43954健康200，33原表逐行同备份；fixture41860确认live后Ctrl-C退出1、7896无监听。主流程两条404未记录URL、只读重走未复现，保留原始日志；不得声称原console全零或真实CLI/模型/Docker通过。部分子代理额度耗尽，主代理接手追加测试、重启及最终验收，无冒称子代理追加审查。F50提交/即时推送尚待下面实际操作记录。


F50实际交付：已核实外部f50-delivery-result.json，本地提交 `f52a1fb7c87f11a90af17005ebfcdb21848df1c0`，7文件186新增/26删除，立即推送退出128：GitHub403，suiyue1990无写权限。远端回读退出0、remote_head=null，提交后工作区干净；远端交付仍未完成。

## F51：固定首批成果自动交接（本地fixture验收Pass）

- 明确批准完整计划时可选自动交接；默认旧Owner门保持。不可变permission边与launch同事务，固定task/version/execution，复用队列、输入快照及产物校验，不伪造审核。拒绝、改版、替代尝试等使后继不执行/不能验收；重试不继承。
- 根实施及集成；handoff_review负责独立PM/TechLead/Ponytail/QA审查。分派实现碰线程数量上限后根接手。独审复现memory/retro单字段KeyError后修复并复验，旧schema不隐式授权；后续工作改为同时检查真实调用数据形状，不以新helper测试代替原调用回归。
- 全量636项及8子用例115.36s通过；独立24项2.78s、定向61项6.11s单列不相加。浏览器自动A→B→C、最终唯一Owner验收、独立9边界和最后只读文案回归Pass，最终构建index-BvE6_RC6.js。详细证据及原404记录见acceptance-report.md的F51段。
- 1launch/1batch/3exec/3artifact/2permission/2input/1最终review，真实CLI/model/Docker0。正常37580健康、34原表无变化，测试52349已停止，7896无监听。整体目标仍未完成；F51独立提交并即时推送待实际记录。

F51实际交付：已读取外部f51-delivery-result.json，本地提交 `2e5135855ba074caac3a401905ebf34716114408`，16文件380新增/32删除，立即推送退出128：GitHub403，suiyue1990无写权限。远端回读退出0、remote_head=null，提交后工作区干净；本地通过不等于远端交付。

## HTTP错误响应连接关闭修复（独立交付）

F52未提交工作区的两轮全量暴露原请求头/未鉴权早拒绝的Windows10053：首轮1失败663通过、次轮2失败662通过，均8子用例，原失败记录保留在外部f52-regression-attempts.json。handoff_review实现错误响应发送后半关闭写端并有界排空，最大100ms/65537字节；不改原请求校验、授权或测试断言。主代理审查并独立运行新增关闭边界、原API及复盘API共27项，24.75s通过；子代理8项2.46s通过。禁用辅助的独立对照两个延迟正文测试均复现10053，启用通过。此修复单独提交并立即推送，F52目标执行继续保持未提交直到全量通过。

HTTP修复实际提交为 `22481d77ea81acc7c0a656d46c3096e835818cee`，4文件121新增，提交退出0；立即推送退出1，GitHub403（suiyue1990无写权限）。独立远端回读退出0但无分支，remote_head=null。该提交仅包含错误响应关闭、其测试及交付记录，F52实现保持未提交；详见外部http-rejection-delivery-result.json。

## F52 一次目标授权连接秘书规划与执行（后端验收Pass）

- handoff_review实施后端四文件及24项核心检查；根实施3项真实HTTP集成检查、独立审查并补1项停止回归。原授权与唯一规划同事务，目标规划只接收明确共享摘要，复用原单次模型队列及固定首批启动。旧建议入口保持不执行语义。
- 审查修正先恢复停止再调度CLI、同键其他完整计划不得误绑/误停、mismatch仍取消自己的排队规划、相同错误不重复写入。根HTTP及旧规划27项18.06s通过，子代理核心及旧规划44项6.74s、最后单例0.33s通过，分批证据不重复相加。
- 两轮全量发现HTTP早拒绝连接问题，先独立交付22481d7；其后最终全量 **671 passed、8 subtests passed（170.26s，session70333）**，全部最终产品代码均已包含，之后只更新文档。没有放宽原测试断言。
- 正常服务37580确认live后Ctrl-C退出1；49304从同数据路径启动，health200。35张原表逐行同备份，新goal表0行，完整性与外键通过。真实CLI/付费模型/Docker未调用，本地HTTP provider和Python controlled runner证明一次规划→A/B/C捕获成果交接→仅C最终Owner验收。
- PM/TechLead/Ponytail与根对后端增量Pass；浏览器一次授权入口作为F53继续，不以API完成冒称用户交互已交付。F52本地提交与即时推送待实际结果，完整目标仍未完成。

F52实际交付：本地提交 ec60c186772372bd37736e74d37d489ffaec488a，10文件729新增/21删除；立即推送退出1、GitHub403，当前suiyue1990无写权限。独立远端回读退出0、remote_head=null；证据f52-delivery-result.json，远端交付未完成。

## F53 浏览器一次目标授权与恢复（本地验收Pass）

- Owner消息直接授权秘书规划一次、创建项目和启动固定首批；默认摘要为空，明确选择候选与交接，修改字段撤销确认。全局恢复入口核对完整原授权与固定关联，停止先保存原记录，未知不自动重规划。
- handoff_review实施前端及边界测试，根集成真实HTTP fixture、开发/生产浏览器驱动、成果下载与最终验收。独审发现StrictMode读取锁和同ID停止记录冲突，根修复并在最终命令中验证；后续审查同时检查开发挂载和每份恢复记录，避免只以生产构建通过判断。
- 最终 npm run test:browser 退出0（session69261）：类型检查、生产构建index-DXckru0z.js、真实Vite StrictMode、一次规划A→B→C、最终成果hash校验且仅C批准、17组边界、模型/执行中停止丢响应+503+reload均Pass。fixture最终退出0。证据corppilot-browser-eEnl7f/browser-report.json，详情见验收报告。
- PM/TechLead/Ponytail/QA针对本增量验收；沿用F52后端全量671及8子用例，本次没有产品后端变更。测试使用独立临时库、本地HTTP provider和受控runner，不等于真实CLI、付费模型、Docker或浏览器真实服务重启验收。Tauri沿用既有迁移边界，未生成安装包。
- 本功能独立提交后立即推送，实际结果记录外部f53-delivery-result.json；完整目标仍未完成。

F53实际交付：已核实f53-delivery-result.json，本地提交5d888ae5267b40bd174d99c7346278154273d542，12文件900新增/7删除，立即推送退出1、GitHub403，suiyue1990无写权限。远端回读退出0、无分支；工作区干净。本地通过不等于远端交付。

## F54 本机资源准入（本地验收Pass）

- handoff_review负责后端与25项新检查，根负责前端设置、浏览器、集成和文档；根复核资源预约释放与close并发，子代理独立审查前端/Ponytail/PM Pass。明确保守预约不是OS硬限、Docker虚拟机容量或费用预算。
- 子代理新25项及原CLIController21项共46 passed5.96s；根最终完整696 passed、8 subtests passed，130.87s（session11423）。原并发/依赖/unknown控制保持，不放宽旧断言。最终生产代码已包含，随后仅文档。
- 根浏览器session95391退出0，证据corppilot-browser-A3gqZp/browser-report.json；类型检查、49模块index-BzqwJHzC.js、原目标链和17恢复边界继续Pass。资源开关及2048宿主保留自动重开断言，1536本地内存和2CPU由根截图人工核对；实际GET资源8206MiB/32CPU，预约0。fixture退出0，未增加模型/runner调用。
- 正常数据备份SHA-256为84df2fc2cd8f5a6d43bcf04521096441768ec814079fac412dbaca8952ff2bd6。49304确认live后停止退出1，73418同数据目录启动；36张表逐行未变，integrity=ok、外键空、health200、新bundle回读通过。先访问错误健康路径获401，随后按源码正确/health核实200。
- F54独立提交后立即推送，结果外部f54-delivery-result.json；真实CLI/Docker、费用硬门和整体目标仍未完成。

F54实际交付：本地提交ff15347b48ee5abf62307991dcd03af3eb49fd53，12文件406新增/14删除，立即推送退出1、GitHub403（suiyue1990无写权限），远端回读退出0但无分支；工作区干净。外部f54-delivery-result.json保留结果，远端交付未完成。

## F55 Agent任务与实际活动视角（本地验收Pass）

- handoff_review负责只读后端与6项检查，根负责前端活动面板、浏览器、集成和文档。按当前任务负责人/实际历史执行者分别查询，三组最近50条及总数，固定当次需求标题、真实成果数与Owner决定；模型kind不漏规划、复盘、评议。无schema修改或额外模型上下文入口。
- 子代理新6项及旧API共22 passed16.98s；根全量702 passed、8 subtests passed，182.98s（session87496）。根审后端、子代理只读审前端PM/QA/Ponytail均Pass。
- 根浏览器session33540退出0，corppilot-browser-gENdqw/browser-report.json记录身份切换、实际2任务/2执行、最终C批准、协调人planning、打开任务全部执行、503清旧状态并恢复。该观察段无新增模型/CLI/goal请求计数，不宣称所有HTTP写入计数均已检查。原目标链、17边界与资源设置回归继续Pass，fixture退出0。
- 首轮sEt2aB未手动刷新已选身份的旧快照便断言最终批准，失败记录保留；测试修正为按产品手动刷新后通过。50模块index-Do5Jt2SN.js构建/typecheck通过，根已目视活动截图。
- 正常服务73418确认live后停止退出1，94528同数据路径启动7892；36表逐行同备份、完整性/外键通过、health200、新bundle可读。f55-normal-backup/readback.json记录证据。F55单独提交并立即推送，实际结果外部f55-delivery-result.json；全目标仍未完成。

F55实际交付：05bdcc887bc245701c8d3bfa4cb6718caa97a0e2，11文件322新增/2删除，立即推送退出1、GitHub403；远端回读退出0但无分支，工作区干净。f55-delivery-result.json保留结果。

## F56 离线工作台备份与恢复（本地验收Pass）

- handoff_review实现backup/verify/restore及14项检查，根实现共享锁、恢复调度门、离线解除和恢复测试；根审核备份，子代理只读审核恢复/Ponytail Pass。根审发现部分复制也可能被解除，增加完成证据并测试失败保留隔离。后续恢复审查同时核对复制完成与允许调度两道条件。
- 根全量722 passed、8 subtests passed，188.81s（session47434），全部最终产品代码包含。其后强化原恢复测试的CLI/goal调度入口不调用断言，6项3.44s通过；另新增批准记忆/来源/版本恢复及回滚1项0.35s通过，不冒称新增单例包含在前述全量。子代理备份14项2.09s单列。
- 实际CLI backup/verify/restore全部退出0，fixture恢复后真实HTTP核对47身份、3会话、4消息、6任务、3模型Run、6执行、3成果、1评审；3成果下载hash一致，隔离期间模型/CLI active0，测试服务已关闭。离线解除命令退出0并保留audit，没有启动恢复服务执行任务。模型排队解除后只执行原Run一次由独立本地HTTP单测证明。
- 正常94528确认live且零活动后停止，实际新命令创建f56-normal-backup（DB hash52d450363ae382eb58562892dc71718edfff66329429303e2b1dacc71725f854）；25767同数据路径启动，36表逐行未变、integrity/外键通过、health200，无恢复隔离标记。前端无产品代码改动，未重复宣称新浏览器构建。
- F56独立提交并立即推送，实际结果外部f56-delivery-result.json。此功能不是完整工具工作区或检查点恢复；真实CLI/Docker、费用硬预算和全目标仍未完成。
## F57 模型失败用量回执保留

根负责模型 provider/controller/Run 实现与测试；handoff_review 独立核查 CLI 计费能力，并对实现做只读审查。首轮 Return 指出 finish 可覆盖既存回执及模型名称可能反射凭据；修正后最终 Pass。此次只解决模型回执持久化，CLI 回执及金额硬限制未实现。

定向命令 `.venv\Scripts\python.exe -m pytest tests/test_workbench_usage_receipts.py tests/test_workbench_provider.py tests/test_workbench_controller.py -q`：27 passed，19.80s（session79959退出0）。新增10例含真实本地HTTP/请求子进程，不调用付费模型；无前端修改，不冒称新的浏览器验收。

根全量733 passed、8 subtests passed，199.07s（session52860退出0）。正常25767确认无活动后停止，离线f57-normal-backup成功，新67999启动，36表逐行相同、integrity/外键及health200通过。独立提交并立即推送、远端回读的精确哈希与结果保存在仓库外 `H:\item\CorpPilot-test-evidence-20260906\f57-delivery-result.json`；远端回读成功前不标记远端交付。
## F58 CLI 单轮用量持久化与观察

handoff_review负责后端及最终新14项测试；根负责前端、浏览器、备份回归和集成；verify_prompt_push只读前端QA/Ponytail Pass。根复核回执在成果采集前保存、写失败仅重试持久化、保留真实退出码、缓存数非法时未知、旧读取路径一致；不将多turn汇总或token估计称为金额硬限。

子代理定向60 passed，13.12s；追加缓存边界后新文件13 passed，0.88s。首轮两项旧读取一致性测试发现pending未加usage，修正后通过，没有放宽旧断言。根浏览器session86117退出0，corppilot-browser-Uf2wyZ/browser-report.json通过：真实API7/null/0回执、两个UI入口、整体null注入及恢复、观察无新增模型/CLI/目标调用；原目标闭环、17恢复边界、停止和资源设置继续通过，fixture退出0。类型检查与51模块index-CP-aAePY.js构建通过。模型/runner为本地受控fixture，不替代真实CLI/Docker烟测。

首轮根全量3 failed、744 passed、8 subtests passed，197.72s（session56965）：旧report直传结果不接受usage、两处核查队列缺usage。修复生产入口，原断言保持；report新增独立用量事务并保留旧attempt/迟到忽略语义，新旧指定38项6.48s通过。修后浏览器session10436退出0，corppilot-browser-p8qW5t/browser-report.json再次通过且fixture退出0。首次失败证据保留在外部f58-regression-attempts.json。

根最终全量748 passed、8 subtests passed，203.67s（session58652退出0）。包含新增CLI用量备份恢复用例，实际失败执行7/null/0回执通过backup/restore完整保留。正常库零活动停止67999，离线f58-normal-backup hash52d450363ae382eb58562892dc71718edfff66329429303e2b1dacc71725f854；新68875运行7892，原36表逐行未变，唯一新表execution_usage为空、完整性/外键/health200及构建资源字节回读通过。

本功能独立提交并立即推送，精确hash、推送退出码与远端回读保存在仓库外 `H:\item\CorpPilot-test-evidence-20260906\f58-delivery-result.json`。实际CLI/双Docker、金额限制和检查点恢复仍未完成；远端回读成功前不标记远端交付。
## F59 检查点恢复服务/API增量

handoff_review负责checkpoints/dependencies及新14项测试，根负责控制器/HTTP接线及3项集成测试；verify_prompt_push只读终审Pass。根复核固定输入、多层依赖、unknown核查后重试、事务内指纹校验、新批次停止与原goal隔离。F60再接浏览器操作，不称本轮完成检查点用户流程。

子代理定向71 passed13.05s，追加多层依赖后新文件14 passed1.88s；根HTTP/真实backup/goal隔离3 passed2.08s。根初次两项测试误假定Origin应403、snapshot默认含成果，按既有400拒绝和include_artifacts=True契约修正后通过，未改变产品拒绝策略。

原有浏览器全链session72239退出0，corppilot-browser-RLdPT8/browser-report.json通过、fixture退出0；前端未改，51模块index-CP-aAePY.js回归构建通过。这仅证明原流程兼容，不替代新增检查点UI验收。

根全量765 passed、8 subtests passed，209.97s（session64694退出0）。正常68875确认live及零活动后停止，离线f59-normal-backup DB hash004d1143ad528bd70d61ec6e4a1cb93e2405110ad8fc0eb8f28e799c0b91a45e；新39515启动7892，原37表逐行未变，仅增加空checkpoint_recoveries/checkpoint_dependency_pins，integrity/外键/health200通过。

独立提交并立即推送及远端回读结果保存在仓库外 `H:\item\CorpPilot-test-evidence-20260906\f59-delivery-result.json`；远端成功前不标记远端交付。费用约束、真实CLI/双Docker、检查点浏览器操作及整体目标仍未完成。

## F60 检查点浏览器流程

handoff_review负责3个前端生产文件；根负责隔离fixture、浏览器驱动、集成验收与文档；verify_prompt_push只读终审Pass。修复POST202后GET4xx误标首拒及历史快照混显。审查首次Return指出GET400测试路径不精准，追加真实POST202后GET400回归并通过，未放宽产品契约。

根检查点浏览器最终corppilot-checkpoint-TQHjz0/browser-report.json通过、fixture退出0、pageErrors空：两次恢复、保留成果不重跑、新批次停止、新前置Owner批准、精确3次受控CLI和0模型调用，响应丢失重新打开、POST202后GET400只读恢复及4个独立边界。原goal浏览器corppilot-browser-ZPR0nV/browser-report.json通过、session19937退出0。根目视恢复截图，根typecheck/build通过52模块index-BN3hmzBK.js。没有修改后端生产逻辑，未重复全量Python；最近后端全量是F59的765+8，不作为本轮新运行。

正常39515持续运行无需重启，health200、实际JS字节与构建一致。与F59离线备份相比原37表逐行未变、两张F59检查点表仍空、无活动执行、integrity/外键通过（外部f60-normal-readback.json）。本功能单独提交并立即推送，精确结果见仓库外H:\item\CorpPilot-test-evidence-20260906\f60-delivery-result.json；远端回读前不标记远端交付。费用约束、真实CLI/双Docker及整体目标仍未完成。

## F61 USD预算准入与持久预留（后端增量）

handoff_review负责budgets、Runs/Executions事务钩子与18项测试；根负责模型/CLI控制器错误、RPM协调、HTTP接口、5项集成测试与迁移；verify_prompt_push只读PM/Ponytail终审Pass。预算单独配置，默认关闭；不足不派发且不烧RPM，原占用不因关闭/失败/重启消失。没有加入价格估算、核销或前端设置，不把预留称为实际费用。

子代理新旧定向43 passed10.01s；根新测试23 passed8.26s。根首轮集成1 failed4 passed：备份恢复后试图创建同一未知模型操作，触发既有保护。改用另一Agent独立操作验证共享预算，未更改产品未知保护。全量最终788 passed、8 subtests passed，214.22s（session1736退出0）。含真实本地模型请求、模型/CLI共享最后额度、并发/事务回滚、RPM回调成功时刻、HTTP权限和实际备份恢复。

原浏览器全链corppilot-browser-kDvKNg/browser-report.json通过，session78933退出0且fixture正常退出；前端未改，用现有52模块index-BN3hmzBK.js，本轮没有新预算界面验收。正常39515零活动停止，离线f61-normal-backup hashf10cbf13f47fb02e044630faf18472ab13ebc821cf882e2a196a752081d9e91f；新99751启动7892，原39表逐行一致、仅两张预算表为空、integrity/外键/health200及JS字节回读通过（f61-normal-readback.json）。

本功能独立提交并立即推送，精确hash和远端结果记录在仓库外H:\item\CorpPilot-test-evidence-20260906\f61-delivery-result.json。远端未回读成功不能视为远端交付；预算UI、费用证据/核销、真实CLI/双Docker和完整目标仍未完成。

## F62 预算设置与Agent预留观察

handoff_review负责BudgetPanel/api/App；根负责可复现budget浏览器命令、隔离fixture、验收及文档；verify_prompt_push只读PM/Ponytail终审Pass。全局配置与Agent只读入口共用既有API；完整pending先保存，PATCH后只读核对，异配需Owner明确采用当前配置。微美元精度、负余额与关闭门禁边界保持真实，不把预留称为账单。

首轮osSQFV发现金额label混入辅助说明，修aria-label；VdHKcc主链通过。新增StrictMode/Escape检查后YPC2dU发现回焦问题，初次cleanup.close仍在q4XVR1/w9aSQl失败；最终稳定opener及捕获DOM节点修复，保留焦点断言，Lwx2FA通过。正式新命令npm run test:browser:budget根实际退出0，最终corppilot-budget-VaIIa7/browser-report.json通过、fixture退出0、pageErrors空；typecheck和53modules index-GHogZ-PU.js构建通过。根目视最终budget-negative.png。

浏览器验证0额度阻塞两个CLI实例、追加额度逐个运行、精确2次受控CLI且0模型；6位金额与上限、负余额、禁用保留、三个Agent范围、PATCH受理丢响应+GET503+刷新、异配采用、坏存储、错币种以及401后实际重新授权不PATCH。开发态StrictMode、Esc恢复入口焦点通过。原goal全链PNppvq/browser-report.json通过、session34004退出0；最终修改仅BudgetPanel焦点。无后端生产修改，不重复Python全量；最近基线为F61的788+8。

正常99751持续运行，无需重启；原39表与F61离线备份逐行相同、两预算表仍空、integrity/外键/health200及新JS字节回读通过（外部f62-normal-readback.json）。独立提交并立即推送的精确结果见仓库外H:\item\CorpPilot-test-evidence-20260906\f62-delivery-result.json。实际费用核算/核销、真实CLI/双Docker与完整目标仍未完成，远端回读成功前不称远端交付。

## F63 Owner 费用声明与预算核销

handoff_review 实现账本及19项新测试，根负责控制器/API、7项集成验证与预算显示兼容；verify_prompt_push 独立终审 Pass。修复控制器检查前 ID 规范化，补持有实例及填充空白 ID 拒绝测试。同键旧回执、追加更正、负余额、历史无预留、unknown 核查及备份恢复通过。费用录入 UI 留待 F64，不将声明视作供应商账单验真。

根全量814 passed、8 subtests passed，236.63s（session28763退出0）。正式预算浏览器 corppilot-budget-vtp8w1 通过，原目标浏览器 corppilot-browser-wL34v3 通过，fixture 均退出0；构建53模块 index-D-hUAdJH.js，根目视声明0.5 USD后的预算截图。使用受控runner，没有付费调用或真实Docker验收。

正常99751零活动停止后创建 f63-normal-backup，数据库 SHA256 d3a7c6c3b0621d649fe930966eb4cb47f5f4d6e4e089700d31151393666b5338；新76129运行7892，原41表逐行不变，仅新增空 budget_settlements，完整性、外键、health200及JS字节一致（f63-normal-readback.json）。首次只读检查误用 status 列，修正为实际 state 后通过，未改数据。

独立提交后立即推送，精确提交和远端结果见仓库外 H:\item\CorpPilot-test-evidence-20260906\f63-delivery-result.json；远端回读成功前不标记远端交付。整体目标仍未完成。
