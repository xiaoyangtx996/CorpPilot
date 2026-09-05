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
