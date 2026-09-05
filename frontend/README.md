# 浏览器工作台开发

本阶段已实现持久身份管理、私聊与董事会/项目群、成员管理、归档/恢复、历史分页与 Owner 消息发送。
模型回复、任务、记忆及 Worker 集成仍在开发；目前发送只保存消息，不会触发 Agent 执行，不能视为完整目标验收。

需要 Node.js 22.12+（开发机可用 Codex bundled Node 24.19）、Python 3.11+。

在 `frontend` 目录运行：

```powershell
npm ci
npm run build
```

从仓库根启动 Python 服务（先创建 `.venv`）：

```powershell
Set-Location scripts
..\.venv\Scripts\python.exe -m workbench.server --port 7892
```

打开 `http://127.0.0.1:7892`。服务只监听本机，直接提供 `frontend/dist`、身份与会话 API。
默认数据在用户 `LOCALAPPDATA/CorpPilot/workbench` 下，源码重建不删除身份。可用 `--data-dir` 指定测试目录；数据库不得放入静态构建目录。
Ctrl+C 停止服务；重启使用同一数据目录。

点击 Agent 开启固定私聊，或新建群聊并选择成员。历史每次正向加载 50 条；“刷新消息”读取新增消息，当前不自动轮询。
发送失败保留当前页面内的草稿和请求 ID，相同内容重试不会重复落库；未发送草稿不保证刷新页面后恢复。
从 v1 数据库启动时会自动升级到 v2，并在数据目录留下独立 `workbench-before-migration-*.sqlite3` 快照，避免并发启动覆盖备份。

开发时另开终端在 `frontend` 运行 `npm run dev`；Vite 将 `/api` 转发到本机 7892。只转换同源开发请求的 Origin，跨来源请求仍由 API 拒绝。

```powershell
npm run typecheck
npm run build
```

后端及 HTTP 回归从仓库根运行 `.venv\Scripts\python.exe -m pytest tests/ -q`。测试依赖 pytest、Pillow；测试数据写临时目录。
前端是静态 SPA，不依赖 Tauri/Node 宿主 API。生产运行不需要 Vite 服务，Tauri 接入后仍可复用静态构建与业务协议。
