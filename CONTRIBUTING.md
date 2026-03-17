# 贡献指南

感谢你对 CorpPilot 感兴趣！欢迎参与项目贡献。

## 🤝 如何贡献

### 报告问题

如果你发现了 Bug 或有功能建议：

1. 在 [Issues](https://github.com/your-org/corppilot/issues) 中搜索是否已有相关问题
2. 如果没有，创建新的 Issue，包含：
   - 清晰的标题
   - 详细的描述
   - 复现步骤（如果是 Bug）
   - 期望行为

### 提交代码

1. **Fork 仓库**
   ```bash
   git clone https://github.com/your-username/corppilot.git
   cd corppilot
   ```

2. **创建分支**
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **进行修改**
   - 遵循现有代码风格
   - 添加必要的测试
   - 更新相关文档

4. **提交更改**
   ```bash
   git add .
   git commit -m "feat: 添加 XXX 功能"
   ```
   
   提交信息格式：
   - `feat:` 新功能
   - `fix:` Bug 修复
   - `docs:` 文档更新
   - `refactor:` 代码重构
   - `test:` 测试相关
   - `chore:` 其他修改

5. **推送分支**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **创建 Pull Request**
   - 描述你的修改内容
   - 关联相关 Issue
   - 等待审核

## 📝 代码规范

### Python

- 遵循 PEP 8 编码规范
- 使用 4 空格缩进
- 函数和类添加文档字符串
- 类型注解推荐使用

### Markdown

- 使用标准 Markdown 语法
- 中文文档使用中文标点

## 🏗️ 项目结构

```
corppilot/
├── agents/          # Agent 人格模板
├── dashboard/       # 看板前端和后端
├── scripts/         # 工具脚本
├── tests/           # 测试文件
├── docs/            # 文档
└── data/            # 运行时数据（不提交）
```

## 🧪 测试

运行测试：

```bash
python -m pytest tests/
```

## 📚 文档

- 架构文档位于 `docs/architecture.md`
- API 文档在代码注释中
- README 包含快速上手指南

## 🙋 问答

如有问题，可以：
- 在 Issues 中提问
- 查阅文档

## 📜 许可证

贡献的代码将采用 MIT 许可证。

---

再次感谢你的贡献！
