# CorpPilot Skill 编写规范

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
