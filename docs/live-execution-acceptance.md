# 真实模型、CLI 与双 Docker Worker 人工验收

状态：**双 Docker Worker 待执行**。F89 已使用 Owner 授权的 OpenCode Zen key 完成一次真实本机文件任务、UI 成果批准及服务重启读回，见 [验收记录](acceptance-report.md#f89-opencode-真实文件执行与界面接入)。2026-09-08 复查 Docker daemon/WSL2 仍未就绪；本文双容器、故障注入及秘书规划场景尚未执行，相关系统修复与额外费用窗口尚需明确。本文提供操作入口；`test:browser` 的受控 runner 不能替代这些实测。不要为了运行本文修改产品源码、补数据库记录或启用隐藏测试开关。

本机 CLI 和 Docker 共用执行队列。本轮至少要实测一种 CLI 后端；真实 Docker 中的 Codex 完整任务可以满足这一项，无需再额外付费重复本机模式。秘书模型规划仍走独立的模型设置和供应商调用。

## 1. 先记录最小授权和环境

Owner 先明确以下内容，未明确的步骤保持待执行：

- 使用的供应商、模型、CLI 密钥环境变量名；有效凭据在启动服务的进程环境中提供，不写入命令记录、截图或本文。
- 允许的模型请求数、CLI 执行数和费用上限，以及中止条件。下述 A/B 首轮2次、失败隔离轮2次、重建轮1次、停止轮1次，共6次 CLI；秘书目标另需1次模型规划及最多1次 CLI。每次失败、unknown 或结果不符先核查，再单独决定是否增加调用，不自动重试。
- Docker/WSL2 修复、安装和重启的独立授权；按 [Docker 环境修复前提](docker-worker.md#环境阻塞与修复前提) 逐步处理，不操作其他项目 WSL 分发。本文不授权系统修复或重启。

工作台 USD 设置是调度预留，不是供应商硬账单上限；若 Owner 要求不可超出的金额，须同时使用供应商实际可用的账户限额。不要以超时或任务数代替货币限制。

Docker 就绪后，按 [固定镜像构建命令](docker-worker.md#镜像构建与配置) 构建，保存实际版本、Linux daemon 输出和镜像 ID。不能只凭 `--version` 或 probe 成功判定真实任务可用。构建前后检查命令退出码，失败即停，不继续后续命令。

## 2. 独立数据和配置

先完成 [前端安装与构建](../frontend/README.md#windows环境与启动)，然后从仓库根运行。以下生成新目录名，不复用正常工作台数据：

```powershell
$liveStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$liveData = Join-Path 'H:\item' ('CorpPilot-live-data-' + $liveStamp)
$liveEvidence = Join-Path 'H:\item' ('CorpPilot-live-evidence-' + $liveStamp)
New-Item -ItemType Directory -Path $liveEvidence -ErrorAction Stop
$nonceA = 'A-' + [guid]::NewGuid().ToString('N')
$nonceB = 'B-' + [guid]::NewGuid().ToString('N')
@{ data_dir=$liveData; nonce_a=$nonceA; nonce_b=$nonceB; status='not_started' } |
    ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $liveEvidence 'case.json')
Set-Location scripts
..\.venv\Scripts\python.exe -X utf8 -m workbench.server --port 7893 --data-dir $liveData
```

若7893被占用，选择空闲端口并记录。启动使用已设置必要密钥环境变量的终端；不要打印环境变量列表。通过自动授权页面进入；不复制 Owner token 或 `owner-access-*.html` 到证据目录。关闭浏览器不停止服务。

在 UI 中执行：

1. 创建长期身份“验收 A”“验收 B”，记录稳定 ID，各授予 read、write、execute；技能可留空。创建“验收协调人”，授予 read、delegate。保持其他默认身份不变，不自动扩大权限。
2. 创建仅含 A/B 的项目群。发送一条无秘密的 Owner 目标消息，从消息创建两项**无依赖**任务，分别指定 A/B。普通发消息不能启动任何调用。
3. CLI 设置选择 Docker，填写实际 docker.exe、固定镜像 ID、已授权模型及密钥环境变量名。保持禁用，检查已保存配置。并发设2；CPU、内存、PID按机器容量与Owner批准值设置，记录实际值。资源准入开启后必须有足够余量放行两个实例，否则记录排队原因，不关闭限制伪造双并发。
4. 预算设置启用，按批准的次数与额度分配每次 CLI/模型预留及总额；确认当前无其他待执行请求，才启用 CLI。暂不启用模型设置。

每次启动均通过任务“执行记录与控制”明确确认。记录 task_id、execution_id、attempt、requirement_version、负责人及请求键。不要重复点击创建新请求来解决未知结果。

## 3. 双 Worker 首轮：独立文件、HOME、配置和成果

分别给 A/B 填以下任务范围，替换 `本任务标记` 为各自 nonce；不要把 A 的标记放入 B 的任务或反之。验收标准为生成对应 JSON，正常退出，成果可下载并核对。

```text
只在本次授权工作区执行验收。用 Python 读取 HOME、CODEX_HOME、TMPDIR 和当前目录，
记录本任务标记“本任务标记”。在工作目录、HOME、CODEX_HOME、TMPDIR 各写入
名为 corppilot-live-marker.txt 的文件，内容只含本任务标记。
在 artifacts/isolation.json 写入这些路径、标记、各文件读回内容和当前 UID。
不要读取或输出密钥、进程环境全集、宿主私人文件，不进行网络请求或安装软件。
写完后用一次 Python sleep 等待90秒，给 Owner 检查实际容器，然后正常结束。
```

超时设置须大于模型处理及90秒观察时间，并受已批准上限约束。模型不一定严格执行要求：没有写文件、没有等待或未正常结束都应记录实际结果，不能伪造通过。

当两个执行都显示 running，在单独 PowerShell 终端设置与 `case.json` 相同的 `$liveData/$liveEvidence`，从各自记录取 container_id：

```powershell
$workerDocker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
# 下面两个值来自工作台执行记录，必须是本轮不同 execution_id。
$runA = '<A execution_id>'
$runB = '<B execution_id>'
$recordA = Get-Content -Raw (Join-Path $liveData "execution-workspaces/$runA/docker-worker.json") | ConvertFrom-Json
$recordB = Get-Content -Raw (Join-Path $liveData "execution-workspaces/$runB/docker-worker.json") | ConvertFrom-Json
$containerA = $recordA.container_id
$containerB = $recordB.container_id
& $workerDocker --host npipe:////./pipe/docker_engine container inspect --format '{{json .State}}' $containerA $containerB
& $workerDocker --host npipe:////./pipe/docker_engine container inspect --format '{{json .Mounts}}' $containerA $containerB
& $workerDocker --host npipe:////./pipe/docker_engine container inspect --format '{{json .HostConfig}}' $containerA $containerB
& $workerDocker --host npipe:////./pipe/docker_engine container inspect --format '{{json .Config.Labels}}' $containerA $containerB
& $workerDocker --host npipe:////./pipe/docker_engine container inspect --format '{{.Id}} {{.Image}}' $containerA $containerB
```

保存以上输出与时间。只检查这两个已核实记录的容器，不枚举或停止其他项目。通过条件：实际 ID 不同且同一时刻均 Running；标签对应各自 execution_id，实际镜像相同且为配置的固定 ID；`/work` 来源各自独立 run/work，只有本次 inputs 只读覆盖，没有其他run、数据库、用户根或Docker socket挂载；根只读、CPU/Memory/PidsLimit值正确、restart=no、cap-drop及no-new-privileges存在。`HostConfig` 可含本机测试路径，证据仅保存在受控目录。

Owner在运行窗口内做以下有限探针；将 `$containerA` 换成 `$containerB` 再执行一次。它们只读预定测试文件，且仅尝试在**本轮空 inputs 目录**创建一个标记，用来检验只读挂载：

```powershell
& $workerDocker --host npipe:////./pipe/docker_engine exec $containerA python3 -c 'from pathlib import Path; import json; p=["/work/corppilot-live-marker.txt","/home/worker/corppilot-live-marker.txt","/home/worker/.codex/corppilot-live-marker.txt","/tmp/corppilot-live-marker.txt"]; print(json.dumps({x:Path(x).read_text() for x in p}))'
& $workerDocker --host npipe:////./pipe/docker_engine exec $containerA python3 -c 'from pathlib import Path; Path("/work/inputs/corppilot-readonly-probe.txt").write_text("probe")'
```

首命令应只得到该身份 nonce；两容器同名 HOME/CODEX_HOME/tmp 路径内容不同。第二条预期非零退出且只读文件系统错误，记录退出码与错误；若写入成功则隔离验收失败，停止本轮，不把该文件批准为成果。不要以“路径字符串相同”推断HOME共享，也不要仅凭模型自述证明隔离。这些标记验证CLI配置目录的存储隔离，不验证所有CLI配置项的语义。

正常结束后两个执行应为 awaiting_review、实际exit_code=0；分别下载 isolation.json，以 `Get-FileHash -Algorithm SHA256 <下载路径>` 对照 UI 成果hash，核对nonce无串用后各自批准。没有成果、解析失败或unknown均不得批准。保存最终 worker 记录；源码会尝试删除已停止容器，phase=removed 后容器消失是正常回收，不能再要求 docker exec 成功。

## 4. 批准记忆、单个失败与重建

1. 用首轮 A 已批准成果，在 A 的个人记忆入口提出并批准候选：“本身份验收标记为 nonceA，仅适用于本次隔离验收。”B 同理使用 nonceB；项目记忆从本群已批准成果提出共享标记 P。记录各自 scope、scope_id、版本和正文hash，不把 A/B 私有标记放入共享记忆。
2. 建第二轮两项新任务，仍无依赖、两身份并发。任务要求先检查上节四个 marker 路径均不存在，再读取**已经提供的 memories**并写 artifacts/memory.json，记录scope/version、内容和 marker 检查结果，然后等待90秒；不要在任务中重复提供私有nonce。必须在CLI上下文摘要核对：A仅有A个人v1与本项目v1，B仅有B个人v1与项目v1。旧首轮回执仍是原来空记忆输入，不能被当前v1替换。
3. 两容器实际同时运行时，重新读取本轮 A 的 docker-worker.json，将本轮 container_id 赋给 `$containerA`，核对 execution_id、container_id、标签、镜像后，仅对 A 发出受控故障：`& $workerDocker --host npipe:////./pipe/docker_engine container kill $containerA`。不得沿用首轮容器ID。这一步属于事先批准的本轮故障注入，不替换为杀宿主进程或停daemon。B不操作。
4. A必须记录真实失败/非零退出；若出现unknown，先保留并核查，不能当作非零失败验收通过。B应正常exit0、awaiting_review，成果hash正确并可批准；B输入不能因A失败变化。保存两边记录/时间/退出码，证明失败隔离。
5. 以 A 身份另建一项新任务，读取已提供记忆、写报告并正常结束。若上一项unknown未核查，先解决原实例与外部结果；不绕开核查重试原任务。新容器、新 execution_id/HOME 清空，但长期A ID及个人v1仍在，输入仍只有A与当前项目的批准版本。
6. 可在重建任务已记录上下文之后，将A个人记忆回滚到v0，产生新的非零空版本；原执行上下文摘要应仍指向固定v1。新的输入版本验证会增加一次CLI调用，须先纳入Owner次数授权。回滚不应改写旧成果、旧版本或其他身份记忆。

## 5. 请求停止与服务重启

另建一项只写测试报告后等待的任务，实际容器 running 时在UI请求停止。保存停止请求时间、原实例身份、工作台状态及最终worker记录。未确认实例退出前不能显示成功；Owner请求停止通常走stopping→cancelled，记录实际退出码，不预设特定数值。停止/kill/inspect失败应保持unknown供核查，不能用“容器命令报错”证明已停止。

等本轮所有实例终止且核查完毕，通过Ctrl+C有序关闭独立服务，等待退出；用同一数据目录重启并重新授权。核对身份ID、群历史、任务版本、执行状态、成果hash、批准记忆及回滚历史均保留，不能有额外模型/CLI调用。此步骤验证有序重启；**不等于控制器崩溃及遗留容器恢复通过**。

崩溃/daemon断线、未知容器stop/kill核查和remove失败仍需单列实测场景及授权窗口。使用现有全局“执行核查”读取固定unknown的Worker状态、请求停止、核对外部影响并保存不可变声明；不要编造unknown、修改DB或删除worker记录制造成功。未实际执行的故障场景明确记待验收。

## 6. 秘书目标到成果

前面基础CLI通过后，按授权启用模型配置。进入协调人私聊，Owner发送“组织一项任务，交给验收 B 写一份本次测试总结文件；协调人只规划、不担任执行负责人”，选择“交给秘书组织并执行”，候选仅选协调人与B，任务上限1，提供同样约束且不含个人秘密的共享摘要。若模型仍分配给无执行权限的协调人，应该启动失败，不能临时扩大权限把错误规划记为通过。

确认本次模型规划及CLI费用后提交一次。保存真实规划Run、候选快照、原目标授权、固定launch/batch及执行ID；检查生成计划不越界、只一项任务、CLI实际成果可下载、Owner批准成功。同键刷新恢复不得新增规划或执行。非法计划、供应商失败或unknown只能如实记录，不手工把模型返回写入数据库完成链路。

## 7. 验收记录与收尾

每个场景保存：时间、源码commit、版本/固定image ID、脱敏配置、授权次数/费用边界、身份/任务/执行ID、状态与退出码、运行中inspect证据、最终worker记录、输入scope/version/hash、下载成果及hash、Owner决定、实际调用数、费用声明及供应商可核对凭据（不含密钥）。模型输出或Owner费用声明不能替代供应商实际账单证明。

使用 [离线备份、verify、restore命令](backup-recovery.md) 对已停止的本轮数据建立备份，在全新目录恢复并保持调度隔离，核对上述业务记录及成果字节。不要同时运行原副本和恢复副本。SQLite含工作台模型/CLI设置，但不包含密钥环境值、外部源库、CLI HOME和未采集工作文件；这些不在本轮备份通过的声明内。

结果逐场景填写 Pass/Return/未执行，附证据路径与失败原因。双容器没有同时running证据、没有真实Codex有效完成及成果、没有失败隔离/记忆重建证据时，不标双Worker完成。所有测试服务退出且仅本轮实例已停止，正常工作台及其他项目不受影响，才结束本轮。
