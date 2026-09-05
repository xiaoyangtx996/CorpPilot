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
