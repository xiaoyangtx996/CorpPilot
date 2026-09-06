# 固定代码仓库与执行副本

F69 为 Workbench 增加项目仓库绑定和每次执行独立的代码副本。当前是后端 API 与执行适配器能力，尚无仓库配置浏览器面板，也没有代码提交成果的自动交接、批准后合并或推送功能。

每个副本从 Owner 指定的完整提交开始，不跟随移动分支，不修改源工作区。绑定保存、查询和原生 Git 检查本身不调用模型；实际任务仍需既有执行授权、身份权限、版本、依赖、预算和资源准入。

## Owner API

以下路径以 `/api/workbench` 为前缀，要求当前 Owner 访问认证。Worker 不取得 Owner 管理凭据。

| 方法与路径 | 返回与用途 |
| --- | --- |
| `GET /conversations/{id}/repository` | 最新项目仓库绑定；从未绑定时为 `null` |
| `POST /conversations/{id}/repository` | 保存一个新绑定版本，成功及同请求重放均返回201 |
| `GET /conversations/{id}/repository/requests/{request_id}` | 读取该请求的原始绑定回执；没有记录时为 `null` |
| `GET /executions/{id}/repository` | 读取该执行固定的仓库绑定及真实实例信息 |

首次绑定请求严格包含六个字段：

```json
{
  "request_id": "owner-repository-001",
  "expected_revision": 0,
  "source_path": "H:\\projects\\example",
  "commit": "0000000000000000000000000000000000000000",
  "integration_agent_id": "替换为本项目内已启用的Agent ID",
  "confirm": true
}
```

以上路径、提交和身份仅为格式示例，提交必须替换为源库真实完整 SHA。`request_id` 为1–120字符，只允许字母、数字、下划线和连字符。`expected_revision` 必须是非负整数，不能用布尔值；后续更新提交当前已读回版本。`confirm` 必须显式为 `true`。

只能向未归档的 `project` 会话保存绑定，集成人必须是该项目中的已启用成员。保存前检查本地源库、固定提交及文件树；检查结束后在写事务中重新检查授权和预期版本，冲突不覆盖他人的更新。

绑定回执字段为：

```text
conversation_id, revision, request_id, created_at,
snapshot: {source_path, commit, tree, files, total_bytes, integration_agent_id}
```

`source_path` 是检查后的绝对规范路径；`commit` 是完整40位或64位小写十六进制提交，`tree` 为对应文件树标识，`files` 和 `total_bytes` 为固定树内普通文件数量与 Git blob 字节总量。

同一项目、同一请求 ID、相同规范化请求重放原回执，即使项目已有更高版本；更换请求内容不能复用该 ID。响应丢失后，应保存原请求并读取精确请求路径，不能把最新项目绑定当作原请求的提交证明。精确读取暂时为 `null` 也不证明原请求从未受理。

停用后续仓库绑定仍使用相同六字段：`source_path`、`commit`、`integration_agent_id` 三项同时设为 `null`，配合新请求 ID、当前 `expected_revision` 和明确确认。回执的新版本 `snapshot` 为 `null`。不支持只将其中一项置空。

## 执行冻结与代码位置

`Executions._create()` 在创建执行的同一事务中冻结当时项目绑定版本，而不是到启动时再选择最新配置。之后项目改绑定或停用，不会替换该执行已经固定的来源。绑定前已创建的执行不会追溯加入仓库；新执行按创建时配置决定是否绑定。停止旧执行仍使用既有执行或固定批次控制。

执行仓库 GET 返回：

```text
execution_id, agent_id, attempt, requirement_version,
repository: 原绑定回执或null
```

`repository:null` 表示该执行没有冻结仓库绑定，不表示代码任务成功或失败。此 GET 读取账本，不创建 checkout，不重建输入，不检查或修改源仓库。

对有绑定的执行，适配器在实际 CLI/容器启动前准备：

```text
<data_dir>/execution-workspaces/<execution_id>/work/repository/
```

这里是自含 `.git` 的新仓库，不是链接到源仓库的 Git worktree。分支名固定为 `corppilot/run-<execution_id>`，起点为冻结提交；独立 fetch 不共享源对象文件或 alternates，不带 remote、源路径 FETCH_HEAD 或准备阶段 reflog。源仓库的未提交修改、未跟踪文件、用户配置、凭据及 hooks 不复制进副本。

副本设置明确的生成身份：`user.name=CorpPilot Worker`、`user.email=worker@corppilot.local`。它不冒用源库作者或宿主用户身份；代码在本地分支提交不会自动同步至源库。

`inputs/` 和 `artifacts/` 继续位于 `work/` 下，与 `repository/` 同级。CLI 提示提供相对代码目录、固定提交、分支、项目绑定版本和指定集成人，并要求不要推送或自动合并。现有成果捕获仍只处理显式导出的 `artifacts/`，不会把整个代码副本或其 `.git` 自动发布成成果。

集成人字段当前用于明确责任人、检查其项目成员权限并告知 Worker。它尚不是一个已经实现的 Git 合并执行器：没有自动收集最终 commit、代码差异评审、冲突处理、跨副本合入或推送链路。

## 原生 Git 准备边界

- 仅支持本机绝对路径下普通 `.git` 目录；不支持网络路径、linked worktree、`.git` 指针文件、alternates 或重解析路径。首版也拒绝主仓库含 `.git/worktrees` 的情况。
- 仅接受完整 SHA1/SHA256 提交，不接受分支名、HEAD、缩写、标签替代或新 HEAD 回退。源对象后续不存在时明确失败，不改用其他提交。
- 固定树最多10000个普通文件、单文件32MiB、总 blob 字节256MiB；拒绝 symlink、submodule、Windows 不可用路径及大小写/规范化路径碰撞。这些是树内容准入限制，不是整个执行目录的 OS 磁盘配额。
- 目的目录必须不存在，失败后留下的目录不能自动复用。准备过程先检查源树，在新仓库 fetch 后再次核对固定树，再 checkout。
- 原生 Git 使用受控进程树、每次命令60秒和有界输出；只有 file 协议可用。HOME、Git 全局/系统配置和继承环境隔离，禁用 hooks、fsmonitor、懒加载外部对象和替换对象，不自动拉取网络依赖或初始化子模块。
- 副本仅取固定提交的浅历史，不保留完整源历史。Git LFS 指针按普通已提交内容复制，不下载 LFS 实体；依赖安装也不属于仓库准备。
- 配置及源引用被检查不代表整个仓库内容不含秘密。这里只复制明确提交中的代码，不执行通用秘密扫描，也不证明 Agent 不会对代码内容执行有副作用的操作。

准备已明确失败时不启动新的 CLI 调用。准备后再次检查取消请求，取消已到达时不继续启动 CLI。原生 Git 进程树退出无法确认时使用独立的 `PreparationUnknownError`，执行保持未知并要求核查，不把它折叠为“所有进程确定未启动”。

**Git 准备发生在宿主上。Docker 容器不存在、未创建或已经停止，不能证明宿主 Git 准备进程已停止。** 必须另行核查本机准备进程及可能产生的副作用，不能仅凭容器检查解除未知状态后重试。

## 隔离、摘要与备份

本机 CLI 的独立目录、HOME 和配置不等于同一 Windows 用户下的 OS 访问隔离。Docker 使用既有只挂载当前 `work` 和只读 `inputs` 的路径，因此新代码副本在容器中为 `/work/repository`，不需要源仓库的共享 `.git` 路径。当前真实 Docker 环境与双容器代码隔离尚未实测，不能把受控 Docker 命令测试称为已通过真实容器验收。

F65 的上下文摘要 schema 没有扩充到 F69 仓库提示。它的指令/任务摘要不是包含新仓库提示的完整最终 prompt 哈希；仓库授权及实例绑定以独立的 `/executions/{id}/repository` 回执为准，不能由旧上下文摘要反推。

F56 离线备份随 SQLite 保存项目及执行仓库绑定，**不包含源仓库、执行 checkout、未提交代码或 Git 对象目录**。恢复后原源路径及固定对象可能不可用；恢复隔离与未知核查流程仍须执行，不能将账本恢复称为代码工作区恢复。重要代码需要独立保留和验收，不能依赖现有白名单备份。

## 验证范围

原生模块测试入口：

```text
.venv\Scripts\python.exe -m pytest tests/test_workbench_git_checkout.py -q
```

这些测试使用本机 Git 与独立临时仓库，覆盖固定旧提交、SHA1/SHA256、两个自含副本、生成提交身份、源 HEAD/status 不变、恶意配置不执行、路径与大小准入、失败不复用、未知进程异常。CLI/HTTP/控制器集成由独立受控测试验证；不据此宣称真实付费模型、真实 Docker、代码成果交接或多人合并闭环已完成。
