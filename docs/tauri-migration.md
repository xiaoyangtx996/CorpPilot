# Tauri 2 桌面迁移实施方案

状态：F32 规划交付，2026-09-06；基于浏览器分支 d9b70e0 实际代码核对。本文是本轮明确要求的后续桌面方案，不是已实现的安装包、宿主权限或升级功能。浏览器完整目标仍在推进，桌面实施以浏览器核心验收通过为前置门。

## 选定边界

保留 Vite/React 页面与 Python 业务服务。Tauri 2 负责窗口、单实例、服务生命周期、受限请求转发、凭据和用户选定文件的保存；不把任务治理、记忆审批、SQLite 或调度器重写成 Rust。浏览器继续独立构建和运行。

桌面主窗口加载安装包内的静态 frontend/dist，通过一个小型 Rust 请求桥连接本次启动的 Python sidecar。不把 WebView 导航到可被其他进程占用的 localhost 页面，不给 WebView 通用 shell、任意 URL HTTP 或整盘文件权限。普通网页版本保留现有同源 fetch。

Tauri 官方支持将外部程序作为 externalBin，并按目标平台 triple 命名；Python 服务是适合此方式的应用场景。桌面开发时再建立 src-tauri、固定依赖与构建脚本，本轮不添加空壳插件或第二套业务后端。[官方 sidecar 文档](https://v2.tauri.app/develop/sidecar/)

## 当前代码与必须补齐的差距

| 已有权威实现 | 可复用 | 桌面实施前需要改变 |
|---|---|---|
| frontend/src/api.ts | JSON API、ApiError、请求超时 | 浏览器 fetch / 桌面 invoke 在此处分支；保留原 HTTP 状态和请求 ID；不能让 Rust 收任意目标 URL |
| frontend/src/ExecutionReview.tsx | 成果列表、批准流程 | 下载 anchor 直接使用相对 URL，必须接入独立保存入口；不能只替换 api() |
| scripts/workbench/server.py | 业务路由、64 KiB 请求体、Host/Origin 校验、loopback监听 | 桌面鉴权、结构化 readiness、协议/数据版本、受控关闭；现有 /health 只有 status=ok，不能认证实例 |
| scripts/workbench/controller.py、cli_controller.py | 数据目录 OS 锁、退出等待、未知结果恢复 | 迁移前生命周期锁；停止接单及停止领取队列后有序排空；宿主等待确实退出 |
| scripts/workbench/provider.py | 请求子进程和超时边界 | 当前 sys.executable -I -c 在冻结程序中不是 Python 解释器；需专用 --provider-worker 启动分派并保留 stdin JSON 协议 |
| scripts/workbench/store.py | SQLite、身份稳定 ID、v1→v2备份迁移 | 当前 Store 先于 Controller 锁初始化；把迁移/模板更新放到生命周期互斥之后；模板资源目录显式传入 |
| scripts/workbench/cli.py、process_tree.py | 执行工作区与 Windows 子进程树控制 | 验证嵌套宿主/CLI Job 的兼容与整树终止，不能把杀 sidecar 等同于终止所有 Worker |
| TaskBoard/TaskExecutions/TaskDependencies/ExecutionReview/MemoryPanel | 原请求重放和冲突交互 | sessionStorage 不保证桌面关闭后恢复；消息发送也有内存请求草稿，需要持久待确认记录 |
| settings.py、cli_settings.py | 配置校验、环境变量名称、不返回密钥值 | 应用内凭据录入及 OS 凭据存储，迁移时不复制明文环境到前端 |

## 进程启动与关闭

首个桌面版本限定一个 Owner 主窗口、一个数据集和一个本机控制服务。

1. 在所有入口（浏览器服务和桌面宿主）落实同一数据集生命周期互斥协议，覆盖初始化、迁移、恢复和完整退出。桌面启动由 Rust 先取得互斥，再进行任何备份、迁移或服务创建；与 Python 控制器锁协调，避免父子互相死锁。重复启动唤起已有窗口；若数据目录被浏览器版控制服务占用，提示关闭原服务或选择另一数据集，不解除对方锁、不自动附着。
2. 宿主从安装资源解析固定 sidecar 可执行文件；通过私有继承管道/stdin 传入一次性随机会话令牌，不放进命令行、URL、日志或 WebView。启动参数只允许明确的数据目录、资源目录、桌面模式和端口 0。
3. Python 在 127.0.0.1 绑定系统分配端口。初始化、版本检查及控制器取得锁后，从其 stdout 发出有界结构化 ready 记录：协议版本、构建版本、实例 ID、数据集 ID、实际端口。日志写 stderr，不能混入 ready 流或密钥。
4. Rust 只信任自己持有的子进程句柄和这次握手；用令牌调用该端口核对同一实例和数据集，才开放页面业务请求。超时、坏记录、版本不符或子进程退出显示启动失败；不得根据端口上任意 /health 自动认领服务。
5. 默认关闭主窗口即请求退出，不悄悄转托盘继续收费。先持久保存未确认请求并阻止新提交，再通过专用控制管道通知停止接单/停止领取新任务。只在尚未发出停止信号前允许用户取消关闭；进入停止后不能撤销已经发出的 CLI 取消并继续原执行。不让页面直接调用通用进程终止。
6. 服务停止派发，给已运行 CLI 发取消信号，等待现有子进程树确实退出；网络请求依据自己的总时限完成或成为未知。等待最终状态落库、HTTP 工作线程排空、Controller.close 完成及锁释放，再退出。
7. 等待较长时显示具体运行状态；超时不是“已停止”。仅明确选择强制退出后终止本次宿主管辖进程树。下次启动将不能确认的动作保留 unknown，先核对外部结果，禁止自动重跑。
8. 关闭握手由 serve_forever 所在线程之外发起 shutdown，避免同线程调用阻塞；最终 finally 统一清理。异常崩溃由进程句柄/OS回收处理，不能只检查 PID 文件是否存在。

锁获取顺序固定为生命周期互斥 → Python 控制器运行锁 → 备份/初始化/迁移/恢复，退出逆序释放。桌面模式的外层锁由 Rust 持有，受认证启动的子服务不再次抢同一外层锁；浏览器模式由 Python 持有两层锁。旧版浏览器只持有运行锁时，新版必须在写库前取得运行锁失败并退出，不能先迁移。备份/迁移在获得两层保护后由服务执行。

现有 Ctrl+C 和控制器退出等待可复用，但上述桌面握手、互斥顺序、控制管道及强制退出交互均尚未实现。queued 任务保留，重启是否继续领取应由启动恢复门控制，不能在用户核对未知记录之前悄悄开始。

## 通信与权限

Rust 请求桥仅接受固定 API 方法与路由模板、JSON 请求体及受限分页参数。只连接握手确认的 127.0.0.1 端口，禁用代理和重定向；固定头部，限制大小、超时及并发。拒绝绝对 URL、路径穿越、任意 header 与泛化 shell 命令。Python 保留全部业务权限和版本验证，Rust 不复制审批逻辑。

桌面模式下，Python 的读写接口、下载和健康握手都要求会话令牌；从环境中分离 Worker 凭据，Worker 不取得 Owner 令牌。已有 Host/Origin 约束用于防网页跨站，不能认证无 Origin 的本地程序。浏览器开发模式与桌面鉴权模式明确分开，不能通过失败后降级无鉴权来连通。

桌面窗口只启用所需自定义命令；Rust 内部启动 sidecar 不意味着给 JavaScript 开放 shell:allow-spawn。限制 capability 到主窗口，拒绝远程页面获得 IPC 权限。多个 capability 的权限会合并，审查应看有效权限全集。[官方 capability 说明](https://v2.tauri.app/security/capabilities/)

显式配置 CSP，仅允许打包资源和实际需要的 IPC 通信。页面不直接连接模型供应商或 localhost HTTP，禁止任意外部导航和内联脚本扩大权限；外链走明确的系统浏览器入口。CSP 需配置后才生效，不能把默认值视为已保护。[官方 CSP 说明](https://v2.tauri.app/security/csp/)

令牌与宿主桥限制普通页面/Worker 越权，但不是对同一用户恶意本机进程的绝对隔离证明；真实 Worker 仍需本目标要求的 Docker 挂载、凭据、网络和资源边界。不得向 Worker 挂载数据库、私有身份卷、宿主整盘或 Docker socket。

## 数据目录与凭据

默认继续使用 LOCALAPPDATA/CorpPilot/workbench，避免桌面第一次启动创建空库而让用户误以为历史丢失。显式选择的现有目录也必须保留；安装目录只放不可变程序、agents 模板与前端资源。不依赖当前工作目录或 PyInstaller 临时展开目录存储数据。

新增数据集 ID 后，将它与数据库一起备份；路径变动不重新生成身份 ID。role_catalog 使用模板相对路径生成稳定模板/身份标识，打包须保留 SOUL.md 和 roles/*.md 的准确相对路径。资源更新不能覆盖用户身份、会话和批准记忆。

持久待确认记录按数据集 ID、对象 ID、操作类型和请求 ID 保存完整原 payload。浏览器实现继续兼容；桌面采用受用户目录权限保护的原子文件存储，原子替换失败时禁止提交。不能只复制 sessionStorage 到新 WebView Origin 并宣称迁移成功。迁移前提示用户处理旧标签页待确认请求；若提供导入，严格校验并由用户确认来源数据集。不会恢复的未发送草稿必须提前说明。

启动恢复先读待确认请求，对照服务端事实；需要重新 POST 时只用原键和内容并经用户明确操作，不生成替代请求。消息与候选可能尚未被服务接收，同键核对可能首次产生动作，界面应保持现有说明。服务端未知执行依然不能通过清空前端待确认文件绕过。

模型/CLI 密钥通过设置界面一次输入，经受限 IPC 交给宿主写入 Windows 凭据存储；前端只临时持有用户输入，提交后清除，后续仅得到配置状态，不回传密钥。Owner 会话令牌始终不进入 WebView。Python/CLI仅接收当前执行所需凭据，禁止通用环境继承、明文日志、普通配置文件保存或将凭据带入备份/成果。现有 api_key_env 名称可保留为高级导入路径；不得自动扫描或导出用户其他工具的登录凭据。

## 文件、成果与工作区

保存成果时，页面仅传 artifact_id；Rust 从已认证服务取得元数据/字节并核对长度与 SHA-256，通过原生保存对话框让用户选择目标。取消不写文件，覆盖需明确确认；以临时文件写完并原子落盘，不接受模型提供的任意宿主路径。保存不执行 HTML、脚本或可执行文件。

目录选择只产生一个待授权项目根；Python执行仍使用独立 checkout/workspace 和原有路径校验。选目录不自动授权整盘、其他个人记忆或 Docker socket。Tauri WebView 不能直接读 SQLite；不启用第二个可写 SQL 插件。文件选择、下载、通知在真实接入点实现小函数即可，不建通用插件工厂。

## 打包与升级回退

首个目标为 Windows x64，复用 WebView2。冻结 Python 服务须打包 workbench、runtime 及实际用到的标准库/资源；采用独立 provider-worker 模式代替现有 -I -c。明确服务主入口与子请求入口，worker 模式不能再次启动服务或迁移数据库。用无系统 Python、无源码目录的干净环境验证后再交给 Tauri externalBin。不要直接复制开发机 .venv 或 Node 运行时到产品。

sidecar 的 exe 名称按实际目标 triple 生成；依赖 DLL/资源随同打包并从安装资源解析。此时才建立可运行的冻结构建脚本和 Tauri 配置，并锁定对应版本。Windows 安装器与 WebView2 部署策略需在干净机及离线场景验证；编译通过不等于安装可用。[官方 Windows 安装说明](https://v2.tauri.app/distribute/windows-installer/)

升级先停接单、排空进程、持锁备份。SQLite使用在线 backup API或彻底停止后的完整一致副本，不能在 WAL 活跃时只复制主文件。备份覆盖数据库、配置、待确认记录及恢复所需工作区/产物，记录 schema、应用/资源版本、数量及 hash；凭据另留在 OS 存储，不导出明文。

先在副本执行迁移并验收稳定 ID、消息数量、任务/依赖版本、执行决定、成果字节、个人/群记忆和待确认请求，再原子切换。失败保留原库与备份，禁止清库“修复”。当前主 schema_version=2 不能独自证明所有增量表兼容；桌面版本需完整 schema 清单与最低/最高支持版本门禁，在写库前拒绝不兼容版本。

程序回退只在旧版本确认兼容当前数据库时允许；否则恢复匹配的升级前数据副本，并明确升级后新增数据需要保留/导出处理，禁止静默丢失。安装器卸载默认保留用户数据，删除数据需独立明确选择。签名、更新包验证、真实安装升级/回退证据在后续桌面发布时单独验收，本轮不发布安装包。

## 分工、实施顺序与验收门

| 顺序 / 责任 | 独立交付 | 可观察验收门 |
|---|---|---|
| D1 Backend + DevOps | 资源路径、冻结服务、provider-worker入口 | 无Python/源码机器上角色清单一致；子请求无递归服务；超时与退出码保留 |
| D2 Backend + Tech Lead | 迁移前锁、桌面鉴权、ready/关闭协议 | 并发启动仅一控制器；占端口的陌生服务无法冒充；错误token读写/下载皆拒绝 |
| D3 Frontend + Host | 单窗口及 api() 请求桥、成果保存 | 现有浏览器仍运行；桌面身份/消息/任务/依赖/记忆闭环一致；下载hash一致、取消不落盘 |
| D4 Host + Backend | 凭据设置及持久待确认记录 | 重启与版本升级后原键原payload恢复；不给页面返回秘密；服务拒绝后不会自动换键 |
| D5 QA + DevOps | 关闭、崩溃、数据迁移与回退 | 运行中关闭确实停树；超时保留unknown；WAL备份可恢复；新旧schema拒绝不安全降级 |
| D6 QA + PM | 干净Windows安装及真实Worker验收 | 无开发依赖启动、WebView2策略验证、双Docker隔离、供应商授权测试、签名/hash及回退证据 |

D1–D6 各自实现、对应测试、Ponytail审查后单独 commit 并立即 push，远端回读成功才算交付。每门可 Return 修正，环境/权限变化才 Escalate。初版不多窗口、不托盘后台任务、不自动发布更新、不重复实现业务数据库。

## 本轮证据与未完成项

当前可执行入口仍为 frontend 的 npm run build 和仓库 .venv/Scripts/python.exe -m pytest tests/；从 scripts 目录执行 ..\.venv\Scripts\python.exe -m workbench.server --port 7892。未建立的 tauri build、冻结打包、安装测试均不能作为本轮已通过命令。

已对照上述源码和 Tauri 官方资料，独立子代理审查迁移差距。本轮只交付迁移契约，没有 Rust、安装器或 OS 凭据实现。Docker CLI不在当前PATH，使用已安装绝对路径 docker.exe info 后仍返回 docker_engine 管道不存在；没有启动、修复或重装系统服务。真实双Worker、模型供应商联调以及浏览器其余完整目标继续保留未完成状态。
