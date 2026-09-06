# 当次上下文准备证据

F65 在实际模型/CLI 调用入口保存不可变元数据，F66 已提供浏览器观察入口。当前 GET 接口只供已认证 Owner，不会启动执行、重新生成输入、读取新的私有记忆或改变 Agent 权限。

`GET /api/workbench/runs/{id}/context-summary` 和 `GET /api/workbench/executions/{id}/context-summary` 返回当前实例的摘要回执。历史或尚未准备上下文的实例返回 `null`，不能据此判定没有输入或从未执行；不存在的实例为404。无 POST/PATCH 写接口。

回执的 `phase=prepared` 只证明调用入口取得并保存了当次上下文摘要，不证明供应商已接收、CLI 已启动或工具已执行。记录成功后才调用 provider/runner，记录失败不启动新的外部调用。重复相同元数据返回原时间和回执，冲突拒绝；运行结束、配置改变、身份停用和重启不会覆盖旧记录。

## 记录范围

- 通用：kind、Run ID、Agent ID、attempt、需求版本、模板ID、选用模型标识、准备时间。指令只保留字符数与原始UTF-8 SHA-256，不保存正文或完整配置。模型标识包含当前密钥值时隐藏标识。
- 模型：实际准备的最多100条消息元数据（ID、sequence、发送者、字符数、内容hash）、源序号、截断标记和调用类别。普通回复与评议是会话消息；目标规划的内容来源明确为 `authorized_shared_brief`，消息ID仍只用于关联源目标，不表示发送了原私聊正文；复盘为 `retrospective_snapshot`，合成消息的ID和sequence为null。
- CLI：固定任务/需求版本，标题、范围、验收的字符数与hash；源消息引用和摘要；实际加入的个人/项目记忆范围、版本、字符数和hash；实际输入成果的ID、来源执行、相对路径、大小和hash，以及local/docker后端。空记忆列表只表示该次snapshot未加入非空版本，不倒推当前可用记忆。

SHA-256对应授权snapshot的原内容；不是最终HTTP字节或CLI完整prompt的hash。provider还会映射角色并给其他成员消息添加前缀，CLI还有静态任务包装。没有保存正文、完整prompt、凭据、可执行文件路径或宿主工作目录。角色/身份的skills标签不等于加载了Skill内容，当前摘要不作此声明。

## 权限与恢复

摘要在原有snapshot授权成功之后生成，保存时再次绑定实际实例、身份及需求版本。只读接口直接读取持久账本，不调用 `Runs.snapshot`、`Executions.snapshot` 或记忆/成果快照生成方法。观察他人身份不会把所见数据发送给任何 Agent。

新增一张 `context_receipts` 表，update/delete/replace均拒绝。旧记录不补造摘要；离线备份与恢复包含该表。准备完成后在外部调用前崩溃仍可能留下prepared而没有真正执行，这是保留该阶段名称的原因。工具内部命令事件另行实现，不把本回执当作完整工具日志。

验证入口：`.venv\Scripts\python.exe -m pytest tests/test_workbench_context_receipts.py tests/test_workbench_context_integration.py -q`。本地模型HTTP/请求子进程和受控runner用于时序、保存失败、来源和恢复验证，不能替代真实付费CLI或双Docker隔离验收。

## 浏览器观察（F66）

右侧 Agent 实际执行和模型活动卡片中的“查看当次上下文”打开只读面板，核对实际实例详情与回执的kind、身份、attempt和需求版本。显示当次模板、选用模型、指令字符数、消息范围和来源、CLI任务与记忆版本、输入成果清单。消息明细默认折叠，长hash按需展开。不会额外请求消息正文、记忆文档或成果内容，也不把当前Skill标签当作已加载内容。

无回执明确显示“实际输入未知”；读取失败或关联不符立即清除旧内容。手动刷新，401重新授权后可再次打开，关闭/Escape恢复入口焦点；切换身份后迟到请求不能覆盖新视角。摘要与费用、审批状态独立，不提供执行或修改操作。

`npm run test:browser:context` 在frontend目录构建并运行隔离测试：100条截断、旧记忆v1而当前v2、输入成果、旧记录无摘要，以及坏关联/范围/哈希、503、401与卸载迟到。`npm run test:browser` 还核对真实本地规划入口保存的授权共享摘要，仍使用本地受控provider而非付费模型。
