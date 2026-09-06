# CorpPilot Skill 编写规范

## 新工作台：内置 Skill 实际输入（F77）

浏览器身份的技能字段目前支持 `coding`、`demo-generator`，对应仓库 `skills/coding.md` 和 `skills/demo-generator.md`。它们是内置参考流程，Owner选择绑定后，普通回复、协作规划、复盘、群内评议和CLI任务都会使用本身份实际绑定的正文。内置文档中的旧路径、工具名或演示标记不能替代当前任务的输出协议、工作区限制和真实验证。

Owner认证后可 `GET /api/workbench/skills` 读取当前ID、名称、相对来源、正文、UTF-8字节数和SHA-256内容版本。仅读取固定的两个文件，不读取旧catalog中的任意路径、URL或递归include；拒绝链接、重解析、硬链接、非普通文件、非法UTF-8和控制字符。单篇最多16000字符、合计32000字符，读文件前按UTF-8字节上限约束；角色加Skill最多64000字符，CLI最终完整提示仍受原64000字符限制。

认领Run时在同一SQLite事务保存 `skill_input_snapshots`：kind/run_id/agent_id/attempt/requirement_version以及有序Skill正文和hash，空选择也保存。未找到或无效绑定在预算及模型RPM预留前拒绝，不启动模型或工具。修改仓库正文只影响后续新认领的Run；原Run固定内容不跟随变化。Skill不增添工具权限、会话成员或文件访问范围；移除绑定后，尚未准备的原输入被拒绝，已发送的外部调用仍按既有停止/unknown规则处理。

`GET /api/workbench/runs/{id}/skills` 与 `GET /api/workbench/executions/{id}/skills` 返回原固定快照；历史缺记录返回null，不根据当前绑定补造。身份或hash损坏明确失败。历史GET不重新读取文件、不重新执行。快照随SQLite备份保留；恢复目录仍有原隔离门，不自动恢复执行。现有上下文回执的instructions摘要覆盖加入的Skill正文，专用接口可核对精确版本。

新建或实际改变技能列表会校验对应内置文件。升级前的未知标签可以读取，并可保持原列表修改名字；但执行明确失败，须将标签改成可用ID或清空。前端目录选择和快照专用展示另行接入；现有技能文本框可填写上述ID。本功能不是Skill发布/审批平台，也不自动授予任务执行授权。

验证入口：`python -m pytest tests/test_workbench_skill_inputs.py tests/test_workbench_skill_inputs_delivery.py -q`。模型验证使用本机受控HTTP服务，CLI/Docker适配器受控，不作为真实付费供应商或双容器验收。

## 原看板与旧 Runtime（历史接口）

以下JSON目录、Flow和spawn语义仅对应旧看板/runtime，不是新Workbench的第二个可写技能存储，也不会自动注入新Workbench。

## 目录与注册

- 本地 Skill 存放在 `skills/{skill_id}.md`
- 在 Dashboard 或 `SkillCatalogService` 中注册：`agents` 字段列出可使用该 Skill 的 Agent ID（如 `rd_center`）

## 正文结构建议

```markdown
# Skill 名称

## 适用场景
## 步骤
## 产出要求
## 禁止事项
```

## 与 Flow 联动

- Flow step 可通过 `skills: [skill_id]` 指定本步额外加载的 Skill
- 未指定时，Runtime 自动加载绑定到该 Agent 的全部 Skill

## 生效时机

- 保存 `data/skills.json` 后，下一次 Agent spawn 即生效（无需重启服务）
