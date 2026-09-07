# Apple 风格工作台界面验收

2026-09-07。Owner 授权重构界面、逐功能提交并推送，直接合并到 main。此次交付保留浏览器工作台与现有 Tauri 迁移边界。

## 需求与实现

结论：Pass。采用浅灰侧栏、白色会话区、系统字体、蓝色主操作、圆角输入和轻量头像；收窄两侧面板，让会话成为主要工作区域。七个次级操作集中到原生“管理工具”折叠区，模型与 CLI 设置保留直接入口。Agent 编辑表单、手机抽屉和横屏输入区同步调整。

前端实现仅涉及 App 标记与样式，没有新增依赖。后端/接口评审确认本次不需要后端变更。测试通过原生 summary 展开管理工具，再执行原有断言。产品设计、技术评审和开发检查合并为一次聚焦界面评审；集成、QA 与发布检查单独执行。

## 验证

- TypeScript 检查与 Vite 生产构建通过。最终资源为 index-XKpvIcV8.css、index-4hfXQwAj.js；现有 JS 大于 500 kB 的构建提示仍存在。
- 13 类真实 Chromium 浏览器回归通过：目标执行、检查点、预算、费用、上下文、工具活动、仓库、代码审核、代码集成、技能、记忆输入、Agent 创建、入门流程。这些使用隔离数据及受控模型/CLI fixture，不代表真实 Docker 或付费模型验收。
- 技能回归首次因系统分配 6668 端口而被 Chromium 拒绝访问；换用新的隔离 fixture 重跑通过，没有放宽浏览器安全设置。
- 最后一次表单及短屏样式调整后，补验目标执行、预算、技能、记忆输入、Agent 创建、入门流程与独立视觉检查。
- 独立视觉检查覆盖 1440×1000 桌面、390×844 手机、844×390 横屏：消息加载、身份切换、编辑器开关、Enter/Space 展开管理工具、预算弹窗 Escape 与焦点恢复、手机抽屉关闭、无横向溢出、减少动态效果。无页面或控制台错误，fixture 正常退出。

本地证据根目录：`H:\item\CorpPilot-test-evidence-20260907`。最终截图与视觉回执位于 `visual-final-xxtvIP`；各浏览器套件的 `browser-report.json` 保存在对应 `corppilot-*` 目录。截图是隔离验收数据，不是用户工作记录。

最终补验回执：`corppilot-browser-tVZH7O`、`corppilot-budget-m0LQDy`、`corppilot-skills-7CVaXM`、`corppilot-memory-inputs-5uEtRE`、`corppilot-agent-creation-V8Eibw`、`corppilot-onboarding-H5XdI9`。独立 UI/Ponytail 审查结论 Pass；测试范围为 Windows Chromium，不包含 Safari、Tauri 或额外的 Tab 首尾循环专项测试。

## 发布与边界

子代理负责界面实现，另一子代理独立审查；主代理复核截图、回归结果及运行资源。按 Owner 要求，本功能测试审核后单独提交并立即推送，再快进合并 main 并核对远端提交。运行中的 `http://127.0.0.1:7892` 已健康返回并提供最终构建资源；没有迁移或替换运行数据。

OpenCode 三个 key 的真实客户端结果和直接 API 限制见 [Zen 实测](zen-verification.md)。本次未实现 OpenCode CLI 适配或 Tauri 安装包，双 Docker Worker 实测仍遵循 [真实执行验收](live-execution-acceptance.md)。
