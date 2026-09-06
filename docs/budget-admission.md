# USD 预算准入与持久预留

F61 提供后端准入和查询接口，F62 接通预算设置与 Agent 观察界面；F63 增加 Owner 费用声明与修订接口，F64 接通费用录入、更正和历史界面。这是每次派发的额度预留，不是供应商实际账单、单次 CLI 费用上限或自动退款。

Owner 设置一个累计 USD 总额，以及每个模型 Run、每个 CLI 执行各自的预留额。金额为整数微美元：1 USD = 1000000 微美元。总额允许 0–1000000000000，每次预留允许 1–1000000000000；不接受浮点、布尔、负数或缺失字段。预算默认关闭，默认预留值不构成执行授权。

模型和 CLI 共用同一额度。首次 claim 在同一 SQLite 写事务中校验状态和权限、检查当前预算占用加本次金额不超过总额、写入固定实例预留并转为运行。余额不足时保持原请求排队；依赖未就绪和已失效请求不扣额。普通回复、规划、复盘、同行评审共用模型入口，项目批次、目标自动启动及检查点恢复共用执行入口。实际 Runner 启动还须通过既有模型/CLI配置、并发、资源和权限检查。

预留绑定执行类型、Run ID、Agent ID、attempt、需求版本、当时金额及配置 revision。重复 claim 不二次占用；数据库事务失败回滚，提交线程结果不确定、运行失败、取消、unknown和重启均保留原占用。不自动释放；F63 允许 Owner 对已结束且控制器不再持有的实例明确声明费用，以最新声明替换该实例的预算占用。增加累计额度可允许后续原队列继续，降低额度可能使可用金额为负并阻止新派发。修改配置不改变旧记录；关闭预算后不新增预留，但历史仍保存。关闭期间的调用不自动预留，但允许事后声明费用；旧无预留记录不能当作费用为零。

每分钟调用限制和预算共同准入：只有持久 claim 成功才占用 RPM，预算不足的轮询不会烧掉调用次数。RPM 预留以 claim 成功时刻开始。

全部接口需要现有本机 Owner 认证，不向 Agent 上下文注入预算记录：

- `GET /api/workbench/budget-settings`：完整配置、USD、revision、总预留、可用金额和记录总数。
- `PATCH /api/workbench/budget-settings`：完整对象 `enabled`、`total_micro_usd`、`model_reserve_micro_usd`、`cli_reserve_micro_usd`。成功后读取回执，金额变化不清空历史。
- `GET /api/workbench/budget-reservations`：最近100条不可变预留。
- `GET /api/workbench/agents/{id}/budget-reservations`：指定身份最近100条；身份不存在返回404。

例如启用总额10 USD、每次模型0.1 USD、每次CLI1 USD，对应 `{ "enabled": true, "total_micro_usd": 10000000, "model_reserve_micro_usd": 100000, "cli_reserve_micro_usd": 1000000 }`。这是配置格式示例，不是已应用的额度或价格推荐。

实际费用可能超过预留，尤其 CLI 内部可能连续调用模型。最终账单硬限制需要服务方或工具可验证的限制能力。F63 的 Owner 声明不等于供应商验真；当次费率快照、自动估算与供应商账单核对仍未实现；不能用 token、运行超时或本预留金额代替账单证据。

测试：`.venv\Scripts\python.exe -m pytest tests/test_workbench_budgets.py tests/test_workbench_budget_integration.py -q`。包含模型/CLI争抢最后余额、重复请求、事务回滚、权限与依赖、改配置、HTTP认证、真实本地请求子进程、RPM、CLI受控runner和实际离线备份恢复；不会调用付费服务。

## 浏览器设置和核对（F62）

左侧“预算与预留”可读取累计额度、总预留、可用金额及记录数，填写 USD 总额和两类单次预留；右侧 Agent 视角的“查看预算预留”只读展示当前身份最近100条记录，额度汇总明确为全局。空记录不表示费用为零，负可用额度如实显示。

金额最多6位小数，通过十进制整数换算成微美元，非法或超范围输入不能发出配置请求。任意修改撤销确认；明确确认可能放行现有排队任务、关闭准入会移除后续预算限制后才能保存。降低额度和关闭不会删除历史预留。

发送前在当前标签页 sessionStorage 保存完整期望配置及原版本。PATCH 回应不单独作为完成依据：再用 GET 核对当前配置和版本。响应丢失、读取失败或口令失效时保留原请求，不自动重发。读取到不同配置时显示当前值与期望值，由 Owner 明确“采用当前配置并结束核对”；这个动作不写服务器，也不证明原请求从未受理。坏会话存储保持锁定，不静默清除。

配置接口没有 CAS 或请求幂等键，多个 Owner 标签页保存仍可能互相覆盖；界面只能确认当前读取结果，不能保证其他标签页之后不再修改。会话存储不承诺浏览器关闭后的恢复。

浏览器验证入口：frontend 中 `npm run test:browser:budget`。隔离数据先以0预算阻塞两个真实队列实例，再用UI追加金额逐次允许受控runner启动；并验证负余额、身份范围、精度、保存丢响应、异配采用、口令恢复和开发模式键盘操作。受控runner不替代真实付费CLI或Docker验收。

## Owner 费用声明（F63）

当前预算占用 = 未核销预留 + 每个实例最新的 Owner 声明金额。总预留保留历史累计值，不因声明而删除；可用额度允许为负。声明低于预留可放行原队列，向上更正阻止后续派发，不撤销已启动调用。允许无预留的历史实例和明确声明零，未知用量不自动记零。聚合整数上限为 JavaScript 安全整数，单笔仍为 0–1000000000000 微美元。

POST `/api/workbench/budget-settlements/{kind}/{run_id}`，kind 为 model 或 cli，字段为 request_id、attempt、requirement_version、expected_revision、amount_micro_usd、evidence_reference、note、confirm=true。首次 expected_revision=0；更正须匹配最新版本并追加不可变记录。同键完整请求重放返回原回执，不把旧费用重新设为最新；并发更正只有匹配版本的一方成功。证据引用为 Owner 文本，不自动访问或验证。

queued/running/stopping 或控制器仍持有的实例不能声明。unknown 必须先完成对应实例、attempt 和需求版本的停止及外部影响核查。停用身份仍可处理历史费用；CLI 待审成果可以声明费用，但不改变成果审批和原执行状态。

同路径 GET 返回最新回执或 null，`/history` 返回最近100个修订，`/requests/{request_id}` 精确读取原请求回执。GET `/api/workbench/budget-settlements` 与 `/api/workbench/agents/{id}/budget-settlements` 分别返回全局或该身份最近100个实例的最新声明，均须 Owner 认证。请求ID作为单一路径段编码。

预算界面展示未核销预留、Owner 声明费用与当前占用；F64 已接通下述费用操作界面。验证：pytest tests/test_workbench_budget_settlements.py tests/test_workbench_settlement_integration.py -q，以及 npm run test:browser:budget。这里不构成服务方硬费用上限。

## 费用操作界面（F64）

右侧 Agent 活动的每条实际执行、模型调用可打开“费用声明与更正”；“此身份的费用声明”另列最近100个实例的最新声明并提供同一入口，因此不局限于最近50条活动。列表只按当前身份查询，不是全局或全历史费用总和。

弹窗读取实际实例、最新声明和最近100个修订，金额初始为空。填写完整实例费用、证据参考与说明，明确确认可能放行既有队列后提交。允许六位小数和明确的零；修改任意字段撤销确认。正在排队或执行的实例只读；unknown仍须先走既有停止与外部影响核查。向上更正追加版本，不修改原成果和审批。

每个 kind/Run ID 使用独立 sessionStorage 待确认键，发出请求前保存完整授权。POST回执还须通过原 request_id 精确GET核验；丢响应、401、读取失败或不一致回执保留原请求，不自动重发。已核对的原回执与最新声明分开展示，即使期间其他客户端追加了修订，也不会把旧金额恢复成最新费用。

首次POST明确400/409/422拒绝后，再读原请求无回执及当前版本，才提供“结束已拒绝请求并重新核查”；随后新请求仍须重新确认。仅GET无回执不能证明未受理。坏会话存储保留并锁定，不静默覆盖。关闭或切换身份后迟到读取不能清除原pending或污染新弹窗。sessionStorage不承诺关闭浏览器后的待确认恢复，服务端历史与精确请求查询仍持久保存。

浏览器验证入口：frontend 中 `npm run test:browser:fees`，独立fixture包含三项真实持久队列和本地受控runner；无真实供应商调用。原预算设置仍由 `npm run test:browser:budget` 验证。
