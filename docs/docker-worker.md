# Docker Worker：配置、恢复与验收边界

F40 已接入浏览器配置和执行适配，复用工作台 CLI 队列。当前主机 Docker/WSL2 阻塞，下面构建与运行步骤尚未实测；浏览器受控状态和后端测试不能替代真实容器验收。最终测试及交付状态见 [验收记录](acceptance-report.md#f40-docker-worker-适配与恢复验收) 和 [交付台账](feature-delivery-log.md)。

F93 新增官方 OpenCode Zen Docker 入口，复用相同生命周期；旧 Codex 镜像及三字段协议保留。只使用已验证原生文件权限，不开放 Shell、网络工具或子代理。此增量代码和契约测试通过，专用镜像尚未构建、双Worker尚未运行。

## 镜像构建与配置

先确认本地 Docker Desktop 使用 Linux/WSL2 backend 且 daemon 可用。应用固定访问 `npipe:////./pipe/docker_engine`，不接受远程 Docker context；不需修改、启动或转换其他项目的 WSL 分发。Docker 官方说明，从 Windows 使用 Docker 不要求安装业务分发；Docker 使用自己的分发，其他分发集成是独立选项。[Docker WSL 说明](https://docs.docker.com/desktop/features/wsl/)

以下为待环境就绪、Owner批准后的 PowerShell 操作，在仓库根执行。构建会访问软件源并写入本地镜像；tag只用于构建后取ID，工作台保存固定sha256值。运行期不会自动build/pull：

```powershell
$workerDocker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
& $workerDocker --host npipe:////./pipe/docker_engine info --format '{{.OSType}}'
& $workerDocker --host npipe:////./pipe/docker_engine build -t corppilot-worker:codex-0.153.3 -f docker/worker/Dockerfile docker/worker
& $workerDocker --host npipe:////./pipe/docker_engine image inspect --format '{{.Id}}' corppilot-worker:codex-0.153.3
```

第一项须返回linux，构建须成功，最后记录实际 `sha256:` 加64位十六进制ID；此文不提供虚构镜像ID。Dockerfile基于node:22-bookworm-slim，安装Python/git/证书与 `@openai/codex@0.153.3`。Codex版本已固定，基础tag不是可重现构建保证；运行固定最终image ID，更新镜像需要重新构建、记录并显式改配置。

使用已获授权的官方 Zen 时，待环境恢复后改建专用镜像（以下命令未实测，不是成功记录）：

```powershell
& $workerDocker --host npipe:////./pipe/docker_engine build -t corppilot-worker:opencode-1.18.29 -f docker/worker/Dockerfile.opencode docker/worker
& $workerDocker --host npipe:////./pipe/docker_engine image inspect --format '{{.Id}}' corppilot-worker:opencode-1.18.29
```

在 CLI 设置同时选择 OpenCode Zen 与 Docker，填实际固定镜像ID和 `opencode/big-pickle`。新Dockerfile只安装 `opencode-ai@1.18.29`，带 `io.corppilot.opencode=1.18.29` 标签；probe和每次创建前均核对Linux、ID和该标签，旧Codex镜像不能误用。标签只声明镜像契约，不是客户端实际版本运行证明，镜像仍须来自可信构建。凭据依旧经attach stdin；每个容器的HOME/XDG位于原私有tmpfs，动态任务只传stdin，不进入配置模板。镜像内 `/etc/opencode/opencode.json` 或 `opencode.jsonc` 存在时入口拒绝，不覆盖受管理策略；路径依据[官方v1.18.29源码](https://github.com/anomalyco/opencode/blob/v1.18.29/packages/opencode/src/config/managed.ts)。

OpenCode 使用原生JSONL与原生工具回执，不冒充Codex。外层仍按时限停止并inspect核对，客户端内部可能重试；不能把工作台执行次数解释为供应商精确请求数或硬费用上限。OpenCode文件模式不能执行下方Codex专用shell任务；真实隔离探针需由Owner按验收授权在明确容器ID内完成，不开放Agent额外权限来方便测试。

在工作台CLI设置选择Docker，填Docker .exe绝对路径、实际镜像ID（亦支持repo@sha256摘要）、已授权模型名及**宿主密钥环境变量名称**，不把密钥值写入配置。先保持enabled=false保存，再显式检查；probe仅验证本地Linux服务、固定镜像和版本，不创建容器、不调用模型，也不验证模型授权或网络可达。默认CPU=1、内存=1024MiB、PID=128、超时120秒、并发2；范围分别为1–16、128–32768MiB、16–1024、1–3600秒、1–16。

只有环境、模型使用授权和真实隔离验收准备妥当后才启用。启用会允许此前已授权的排队执行继续调度；任务执行还须当前身份同时具备read、write、execute权限、需求版本、依赖成果与显式执行请求。协调人新建协作项目、规划或一次目标另需delegate，任务接收者不因此自动获得delegate。仅保存禁用配置或probe不会启动任务。新增权限门不改写历史回执；已启动批次不因协调人后来撤销delegate而整体停止。

## 实际隔离与凭据边界

- 每个执行使用新的run目录和独立容器，固定UID/GID1000、只读根、移除所有capability、no-new-privileges、restart=no；不挂Docker socket或宿主用户根。
- 仅当前run/work作为可写bind mount、其inputs覆盖为只读mount；数据库、其他run及 `docker-worker.json` 不挂载。HOME/CODEX_HOME与tmp为各自256MiB tmpfs；CPU、内存和同额memory-swap、PID、时限、输出大小均有限制。
- Docker宿主命令固定本地pipe，使用独立空Docker配置与精简环境，不继承远程context、Docker登录信息或其他密钥。模型API key通过attach stdin发送，不进入Docker argv、容器Config.Env或登录配置；Owner访问token不发给Worker。
- 入口将模型key放入Codex进程环境，工具shell不主动继承。该机制不保证同容器同UID工具无法读取进程环境或内存；容器身份labels中的随机标记用于核对实例，并非模型或Owner凭据。不得宣称所有工具绝对看不到密钥。
- 内层Codex采用 `--sandbox danger-full-access`，由外层容器提供隔离；忽略宿主用户配置、规则、登录shell并使用ephemeral模式。这个参数只属于容器入口，不能外推到本机CLI。
- 当前使用bridge网络，尚未提供域名出口白名单；F39 Bearer鉴权保护工作台控制面，不等于容器任意网络出口受控，也不隔离同一宿主OS用户。

## 退出与未知实例恢复

执行前数据库不可变绑定原backend和Docker工具路径。宿主run根原子保存创建/启动意图、镜像和容器身份；create后按执行标签、随机标记、实际image ID及container ID回读，create响应丢失不能再次创建。start/attach退出后还必须inspect；取消、超时、输出超限或异常时，先stop再回读，仍running才kill并再次回读。只有可信不再运行的状态才能返回退出信息，成功仍要求有效Codex完成事件，成果继续进入原快照与Owner评审。remove失败保留记录并明确清理未完成。

控制器重启后遗留running转unknown，不自动重试或宣称已停。全局核查入口可读取Worker状态；仍由当前控制器持有时，使用任务停止请求。恢复stop只针对已核实身份、未被当前控制器持有的unknown实例，不自动start/remove。接口为：

```text
GET  /api/workbench/executions/{id}/worker
POST /api/workbench/executions/{id}/worker/stop
POST /api/workbench/executions/{id}/reconciliation
```

所有接口沿用F39授权，stop请求体必须且只能为 `{"confirm":true}`。状态未知或仍running时不能保存“已停止”声明；本地文件缺失、inspect失败不等于容器不存在，absent须成功核对daemon容器清单及执行标签。浏览器显示exited后，保存前服务端仍重新核查，状态变回running则拒绝并保留原pending。机器停止/absent仅允许Owner继续核查外部影响、输入说明并显式保存，不修改原unknown结果或批准成果。已经存在的不可变声明按原请求精确重放，即使daemon后来不可用也能恢复历史收据；改内容仍拒绝。

## 环境阻塞与修复前提

2026-09-06只读预检：Windows 11 Pro26200.8737、WSL2.6.2.0已安装，固件虚拟化/SLAT=True，但HypervisorPresent=False、vmcompute缺失。`alpine-ai-yss` 是Stopped/WSL1，默认新分发版本2不表示该分发已变成WSL2；未操作它。Docker Desktop4.88.1.237512和CLI29.7.2文件存在，安装注册缺失；安装日志退出1、启动因注册键缺失失败，本地daemon pipe不存在。

VMP查询返回0x80040154，DISM日志显示CBS package identity/Foundation package创建失败，组件状态不能读。`DISM /Online /Cleanup-Image /CheckHealth` 的退出码0只代表命令结束，正文为“无法修复组件存储”，不是健康通过。当前BCD未发现hypervisorlaunchtype Off，固件虚拟化已开，没有直接修改BCD/BIOS的证据。[微软DISM说明](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/repair-a-windows-image?view=windows-11)

Windows保留式修复及重启授权仍待Owner答复，尚未写系统配置。最小分阶段计划是：

1. 核对备份与“设置→系统→恢复→使用Windows更新修复问题”可用性；该官方流程修复当前版本并保留应用、文件和设置。获得明确授权后执行，关闭自动重启并单独约定重启窗口。入口缺失、无法保留应用或失败时停止，不改走清装、重置电脑或批量注册表修补。[微软保留式修复](https://support.microsoft.com/en-us/windows/deployment/install-upgrade/fix-issues-by-reinstalling-the-current-version-of-windows)
2. 修复后重读VMP；仅确认未启用时，按授权执行 `dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart`，再按批准窗口重启并回读WSL2能力。不转换、注销、关闭或启用alpine-ai-yss集成。[微软WSL排障](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)
3. WSL2就绪后，核对官方签名安装程序与现有Docker数据保留方案，获得安装及启动授权后，以现有all-users路径和 `install --backend=wsl-2` 补齐安装。没有已验证的通用repair参数；若要求破坏性卸载则停止，不手工伪造注册键、不切换安装模式。[Docker安装说明](https://docs.docker.com/desktop/setup/install/windows-install/)
4. 本地Linux daemon就绪后才执行上面的镜像构建与只读probe，再开展经批准的真实Worker验收。Windows修复、安装、重启不是F40源码测试的一部分，当前未执行。

## 真实验收仍需完成

逐步操作、固定样例与证据清单见 [真实执行人工验收](live-execution-acceptance.md)。这是待执行的验收入口，未运行，不代表环境已经就绪或真实模型费用已授权。

至少两个实际Worker应验证不同任务的work/HOME/tmp、输入只读、输出归属及宿主/其他run不可达；实际CPU/内存/PID限制、密钥与控制面权限、网络出口也须核查。真实Codex任务需有效完成事件、成果快照与Owner评审，不能以 `--version` 代替。另需覆盖超时/取消、控制器重启、遗留容器身份核对、stop/kill后inspect、daemon断线时unknown阻断、remove失败与同键声明重放。当前fixture只证明这些接口和状态保护逻辑，没有提供真实容器停止或供应商调用证据。
