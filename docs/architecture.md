# CorpPilot 架构说明

## 1. 目标

CorpPilot 用“组织架构”来表达多 Agent 系统中的职责边界。
当前阶段的目标不是做复杂自治，而是先把协作内核、控制面和事件留痕统一起来，让任务流、提案流和执行动作都能被稳定观察和人工干预。

## 2. 总体结构

```text
用户请求
  -> TaskService
  -> WorkflowEngine
  -> Dashboard API / CLI

并行存在的董事会流程
  -> BoardRoom
  -> Discussion / Vote / Tally / DirectOrder

统一观测层
  -> EventLogService
  -> AgentMonitorService
```

## 3. 核心组件

### 3.1 TaskService

职责：
- 创建、读取、更新、删除任务
- 维护统一任务模型
- 写入任务历史记录
- 汇总任务统计

关键约束：
- 所有任务都带 `current_owner` 和 `execution_owner`
- 状态流转和人工干预都要进入 `history`
- 任务事件同步写入 `events.json`

### 3.2 WorkflowEngine

职责：
- 作为统一编排入口
- 执行任务状态流转
- 提供路由快照
- 暴露任务时间线
- 转发人工干预动作

### 3.3 BoardRoom

职责：
- 创建董事会提案
- 记录讨论与投票
- 统计投票结果
- 支持紧急提案直接下令
- 提供提案摘要聚合

### 3.4 AgentCatalogService

职责：
- 扫描 `agents/*/SOUL.md`
- 生成 Agent 配置快照
- 提供按层级过滤的 Agent 元数据

### 3.5 SkillCatalogService

职责：
- 管理本地与远程 Skill 配置
- 记录 Skill 适用 Agent 范围
- 为后续热更新预留配置入口

### 3.6 AgentMonitorService

职责：
- 根据任务责任链和历史记录推导 Agent 健康状态
- 聚合责任任务数、执行中数量、阻塞数量和最近活跃时间

当前限制：
- 现在是推导式健康状态，不是真实心跳
- 更适合做控制面观察，不适合做生产级告警

### 3.7 EventLogService

职责：
- 统一记录任务与提案事件
- 支持按类别和主体过滤
- 默认按时间倒序返回

### 3.8 ExecutionService

职责：
- 对执行层动作提供统一协议
- 封装 `start`、`complete`、`block`

当前限制：
- 只是编排层薄封装
- 还没有真正接入 Worker 或 Agent 执行器

## 4. 数据存储

当前版本使用 JSON 文件存储：

- `data/tasks.json`
- `data/proposals.json`
- `data/agent_config.json`
- `data/skills.json`
- `data/events.json`

这是为了保持原型轻量。数据量放大后，需要迁移到数据库或事件存储。

## 5. 状态机

### 5.1 任务状态机

```text
pending
  -> classified
  -> planned
  -> reviewing
  -> approved / rejected
  -> dispatched
  -> executing
  -> review
  -> completed
```

补充分支：
- `rejected -> planned`
- `executing -> blocked`
- `blocked -> executing`
- `review -> executing`

### 5.2 人工干预

- `pause`
  - 允许状态：`executing`
  - 目标状态：`blocked`
- `resume`
  - 允许状态：`blocked`
  - 目标状态：`executing`
- `send_back`
  - 允许状态：`reviewing`、`approved`、`dispatched`、`executing`、`review`、`blocked`、`rejected`
  - 目标状态：`planned`

### 5.3 执行协议

- `start`
  - 允许状态：`dispatched`、`blocked`
- `complete`
  - 允许状态：`executing`
- `block`
  - 允许状态：`executing`

### 5.4 提案流程

- 普通提案：讨论 -> 投票 -> 计票
- 紧急提案：董事长直接下令

## 6. API 设计

### 任务

- `GET /api/tasks`
- `GET /api/tasks/:id`
- `GET /api/tasks/:id/timeline`
- `POST /api/tasks`
- `PUT /api/tasks/:id`
- `POST /api/tasks/:id/status`
- `POST /api/tasks/:id/intervene`
- `POST /api/tasks/:id/execute/start`
- `POST /api/tasks/:id/execute/complete`
- `POST /api/tasks/:id/execute/block`
- `DELETE /api/tasks/:id`

### 董事会

- `GET /api/board/proposals`
- `GET /api/board/summary`
- `GET /api/board/proposals/:id`
- `POST /api/board/proposals`
- `POST /api/board/proposals/:id/discuss`
- `POST /api/board/proposals/:id/vote`
- `POST /api/board/proposals/:id/tally`
- `POST /api/board/proposals/:id/order`

### 观测与配置

- `GET /api/agents`
- `GET /api/skills`
- `GET /api/stats`
- `GET /api/events`
- `GET /api/health`

## 7. 控制面现状

当前 Dashboard 已经支持：

- 任务列表与状态过滤
- 按 Agent 责任链过滤任务
- 查看任务详情与时间线
- 推进任务状态
- 人工干预
- 执行协议动作
- Agent 健康概览
- 提案摘要与最近提案
- 统一事件流展示

## 8. 当前边界

当前版本仍然是“协作内核 + 控制面原型”，还没有进入完整多 Agent 运行时阶段。明显缺口包括：

- 真实 Agent 执行器
- 事件总线与订阅机制
- 任务与提案的自动联动
- 实时心跳和执行耗时指标
- 模型与 Skill 运行时热更新
- 完整自动化验证链路

## 9. 推荐演进路径

1. 给 `WorkflowEngine` 增加事件订阅、重放和回溯能力。
2. 把 `ExecutionService` 扩展为真实 Worker 协议接入层。
3. 把 `AgentMonitorService` 升级为心跳与指标采集服务。
4. 给控制面补提案操作、事件筛选、SLA 和阻塞原因视图。
5. 将 JSON 存储逐步迁移到数据库或事件存储。
