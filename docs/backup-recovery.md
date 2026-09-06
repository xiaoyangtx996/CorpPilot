# 离线备份与恢复

先停止使用该数据目录的 CorpPilot 服务，再操作。命令会取得同一个控制器锁；在线服务或另一个维护命令持有锁时直接拒绝。不要同时启动原副本和恢复副本。

备份保存 SQLite 中的身份、会话、任务、执行、授权、批准记忆及成果字节，同时保存已有的 `reply-usage.jsonl` 和 `execution-workspaces/{执行ID}/docker-worker.json`。Docker 记录保留原实例身份，用于后续核查，不表示容器已停止。

不收集 CLI HOME、工作文件、配置、临时目录、Owner 登录页面或迁移旧数据库。这是工作台记录和核查证据备份，**不是完整开发 checkout 或执行工作区备份**。需要保留未采集的工作文件时，另行备份可信项目目录。审计日志不恢复内存中的 RPM 窗口。备份包含私人记录，应保存在自己的受控目录。

从仓库根目录使用 PowerShell，数据路径须与服务的 `--data-dir` 一致。例如默认数据目录：

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'scripts')
$dataDir = Join-Path $env:LOCALAPPDATA 'CorpPilot\workbench'
$backupDir = Join-Path 'H:\CorpPilotBackups' ('snapshot-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
& .venv\Scripts\python.exe -X utf8 -m workbench.backup backup $dataDir $backupDir
& .venv\Scripts\python.exe -X utf8 -m workbench.backup verify $backupDir
```

成功命令退出 0，输出版本、原目录和文件大小/哈希清单。数据库上限 1 GiB、审计日志 100 MiB、每个 Docker 记录 16 KiB、总量 2 GiB、最多 10002 个文件。超限不会静默截断。校验包括白名单、SHA-256、数据库完整性、外键及支持的 schema 版本，拒绝路径越界、链接和重解析点；它不是备份作者身份认证，也不防御同一操作系统用户的恶意并发修改。

恢复到一个**尚不存在**的新目录，不覆盖原数据：

```powershell
$restoredDir = Join-Path 'H:\CorpPilotBackups' ('restored-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
& .venv\Scripts\python.exe -X utf8 -m workbench.backup restore $backupDir $restoredDir
& .venv\Scripts\python.exe -X utf8 -m workbench.server --data-dir $restoredDir --port 7892
```

恢复命令不启动服务或工具。写数据前先持久化 `restore-quarantine.json`，全部复制、哈希和数据库校验通过后才写 `restore-complete.json`。失败时保留目标供检查，不能把部分副本当成成功恢复；换一个新目标重新恢复，不覆盖失败目录。

首次启动恢复副本时，原 running/stopping 按既有规则变为 unknown。即使只是 queued，旧备份之后也可能已在原系统执行，因此整个恢复副本默认暂停目标规划、模型与 CLI 调度。浏览器仍可查看记录、下载成果，并通过原有核查入口记录未知实例和外部结果。不要删除隔离文件来绕过检查。

实际确认原服务、其进程和容器不再运行，核对备份之后是否发生外部副作用；对每个未核查 unknown 完成原核查流程。关闭恢复副本服务后，明确解除：

```powershell
& .venv\Scripts\python.exe -X utf8 -m workbench.recovery --data-dir $restoredDir --confirm-original-stopped --confirm-external-effects-checked
```

该命令验证完成证据，并同时取得恢复目录及仍存在的原目录锁；运行记录或未核查 unknown 会阻止解除。两个确认是 Owner 对原实例和外部结果的声明，不是机器证明。命令先保存 `restore-release-*.json` 审计，再移除隔离标记，不改写历史结果，不启动服务。下次启动可能继续原来已授权的排队任务。不要再启动旧副本。

这不是进程检查点、任意时刻无损恢复或跨机 Docker 迁移。Docker 原工具路径、原实例所在宿主以及凭据环境仍须可用并核实；工作台不会凭空重建原容器、CLI 会话或已丢失的工作文件。
