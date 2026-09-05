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
- 提交标识：feat(workbench): F41 generate scoped retrospective memory candidates；测试审查后立即独立提交并推送，实际结果记录于外部f41-delivery-result.json。F42浏览器入口、Skill发布、真实模型质量和完整目标仍待验收。
