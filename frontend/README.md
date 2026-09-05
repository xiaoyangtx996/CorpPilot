# 浏览器工作台开发

本阶段已实现持久身份列表、新建/编辑/停用/启用；聊天、任务、记忆及 Worker 集成仍在开发，空态不代表完整目标已验收。

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

打开 `http://127.0.0.1:7892`。服务只监听本机，直接提供 `frontend/dist` 与身份 API。
默认数据在用户 `LOCALAPPDATA/CorpPilot/workbench` 下，源码重建不删除身份。可用 `--data-dir` 指定测试目录；数据库不得放入静态构建目录。
Ctrl+C 停止服务；重启使用同一数据目录。

开发时另开终端在 `frontend` 运行 `npm run dev`；Vite 将 `/api` 转发到本机 7892。只转换同源开发请求的 Origin，跨来源请求仍由 API 拒绝。

```powershell
npm run typecheck
npm run build
```

后端及 HTTP 回归从仓库根运行 `.venv\Scripts\python.exe -m pytest tests/ -q`。测试依赖 pytest、Pillow；测试数据写临时目录。
前端是静态 SPA，不依赖 Tauri/Node 宿主 API。生产运行不需要 Vite 服务，Tauri 接入后仍可复用静态构建与业务协议。
