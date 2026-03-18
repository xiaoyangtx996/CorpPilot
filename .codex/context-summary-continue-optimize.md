## 项目上下文摘要（继续优化控制面）

生成时间：2026-03-18 10:35:00

### 1. 相似实现分析

- **实现1**: `D:\CorpPilot\dashboard\server.py`
  - 模式：单文件 HTTP API 路由分发
  - 可复用：`/api/board/summary`、`/api/events`、`/api/tasks/:id/execute/*`
  - 需注意：接口已经稳定，前端应直接消费，不要再新增适配层

- **实现2**: `D:\CorpPilot\scripts\core.py`
  - 模式：统一领域服务层，任务流、提案流、事件流都集中在这里
  - 可复用：`BoardRoom.get_summary()`、`EventLogService.list_events()`、`ExecutionService`
  - 需注意：摘要接口字段较轻量，前端要自己做展示映射

- **实现3**: `D:\CorpPilot\tests\test_e2e_workflow.py`
  - 模式：`unittest` 本地静态验证
  - 可复用：事件流、执行协议、董事会提案流的断言方式
  - 需注意：当前环境无 Python，测试只能静态补充，无法动态执行

### 2. 项目约定

- **命名约定**：Python 使用蛇形命名；前端 JS 使用驼峰命名；状态和类型采用英文枚举值
- **文件组织**：`scripts/` 放核心服务，`dashboard/` 放 API 与前端，`tests/` 放本地验证
- **导入顺序**：标准库优先，再导入项目内部模块
- **代码风格**：保持直接、轻量，不额外引入框架和构建工具

### 3. 可复用组件清单

- `D:\CorpPilot\scripts\core.py`: `BoardRoom.get_summary()`
- `D:\CorpPilot\scripts\core.py`: `EventLogService.list_events()`
- `D:\CorpPilot\scripts\core.py`: `ExecutionService.start/complete/block`
- `D:\CorpPilot\dashboard\server.py`: 现有 REST API 路由

### 4. 测试策略

- **测试框架**：`unittest`
- **测试模式**：端到端风格的本地服务层测试
- **参考文件**：`D:\CorpPilot\tests\test_e2e_workflow.py`
- **覆盖要求**：正常流程 + 边界条件 + 查询结果排序或聚合行为

### 5. 依赖和集成点

- **外部依赖**：无新增依赖，保持标准库实现
- **内部依赖**：前端依赖 `dashboard/server.py` 暴露的 REST API
- **集成方式**：浏览器 `fetch` 直接访问 `/api/*`
- **配置来源**：任务、提案、事件均来自 `data/*.json`

### 6. 技术选型理由

- **为什么用当前方案**：本轮目标是补全控制面，不是扩展新运行时内核；直接接现有接口成本最低
- **优势**：改动集中、回归范围小、和当前核心层一致
- **劣势和风险**：没有 Python 环境，无法跑自动化验证；前端仍是原生 HTML/JS，后续复杂度上升后维护成本会增加

### 7. 关键风险点

- **边界条件**：事件为空、提案为空、任务状态不支持执行动作时要给出清晰提示
- **性能瓶颈**：当前全量拉取任务和事件，数据放大后需要分页或订阅机制
- **验证缺口**：无法启动服务做真实浏览器验证，只能保证静态结构与接口契合
