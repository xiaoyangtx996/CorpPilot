## 项目上下文摘要（CorpPilot 审查）

生成时间：2026-03-18 09:55:00

### 1. 相似实现分析

- **实现1**: `/D:/CorpPilot/scripts/task_manager.py:19`
  - 模式：任务状态机 + JSON 文件持久化
  - 可复用：`create_task`、`update_task_status`、`list_tasks`
  - 需注意：核心常量和枚举定义存在被注释吞掉的迹象，任务模型未形成统一领域层

- **实现2**: `/D:/CorpPilot/dashboard/meeting_room.py:15`
  - 模式：董事会提案、讨论、投票、计票
  - 可复用：`BoardRoom.create_proposal`、`cast_vote`、`tally_votes`
  - 需注意：会议室仅停留在提案 JSON 流转，未与任务状态机、Agent 执行流闭环

- **实现3**: `/D:/CorpPilot/dashboard/server.py:28`
  - 模式：标准库 HTTPServer 暴露 REST API
  - 可复用：任务、提案、看板读写接口
  - 需注意：API 层重复维护状态流转规则，与脚本层分叉，缺少统一编排入口

- **实现4**: `/D:/CorpPilot/scripts/sync_agent_config.py:20`
  - 模式：从 `agents/*/SOUL.md` 扫描生成配置
  - 可复用：角色目录约定、SOUL 章节抽取
  - 需注意：仅同步静态元数据，没有运行时模型配置、路由策略、健康状态

- **实现5**: `/D:/CorpPilot/scripts/skill_manager.py:43`
  - 模式：本地/远程 Skill 元数据管理
  - 可复用：Skill 配置文件结构
  - 需注意：Skill 只是登记，不参与任务执行编排，也没有调用记录

### 2. 对照基线（edict）

- **来源**: `https://github.com/cft0808/edict`
- **参考证据**:
  - README 明确强调“制度性审核 + 完全可观测 + 实时可干预”
  - 具备实时看板、任务干预、审计存档、Agent 健康监控、模型热切换、Skill 管理等运行时能力
- **对 CorpPilot 的启发**:
  - 角色名称和组织隐喻不是核心竞争力
  - 核心差距在于是否有真实的编排引擎、审议回路、观测面和运维面

### 3. 项目约定

- **命名约定**: 目录名使用组织角色英文标识，如 `rd_center`、`president_office`
- **文件组织**:
  - `agents/` 存放角色人格模板
  - `scripts/` 存放 CLI 和配置工具
  - `dashboard/` 存放 API 与前端页面
  - `tests/` 目前仅有一个 E2E 文件
- **实现风格**: 倾向“零后端依赖 + JSON 文件存储 + 单文件页面”
- **风险约定**: 文档和源码混有乱码，现有内容不能稳定作为真实对外交付物

### 4. 可复用组件清单

- `/D:/CorpPilot/scripts/task_manager.py`: 任务模型和状态流转函数
- `/D:/CorpPilot/dashboard/meeting_room.py`: 提案与投票模型
- `/D:/CorpPilot/scripts/sync_agent_config.py`: Agent 元信息收集
- `/D:/CorpPilot/scripts/skill_manager.py`: Skill 元数据管理

### 5. 测试策略现状

- **测试文件**: `/D:/CorpPilot/tests/test_e2e_workflow.py`
- **测试模式**: 手写脚本式端到端测试，不是 `pytest` 或 `unittest` 规范风格
- **主要问题**:
  - 仅覆盖任务状态机
  - 未覆盖会议室、API、前端看板、Skill、Agent 配置同步
  - 测试文件本身引用了不存在的枚举值，可信度不足

### 6. 依赖和集成点

- **外部依赖**: 基本无，主要依赖 Python 标准库
- **内部依赖**:
  - `dashboard/server.py` 依赖 `meeting_room.py`
  - 前端页面依赖 `/api/*`
  - 配置同步和技能管理依赖 `data/*.json`
- **集成方式**: 文件驱动、HTTP 拉取
- **配置来源**: `data/tasks.json`、`data/agent_config.json`、`data/skills.json`

### 7. 关键风险点

- **可运行性风险**: 多个核心脚本存在明显静态错误，系统可能无法启动
- **一致性风险**: 状态机规则在 CLI 和 API 中重复定义，后续必然漂移
- **架构风险**: 缺少统一编排内核，当前更像静态演示而不是多 Agent 系统
- **观测风险**: 没有事件流、Agent 心跳、执行日志、人工干预闭环
- **文档风险**: README、架构文档和部分中文文案已乱码，影响可信度与传播
