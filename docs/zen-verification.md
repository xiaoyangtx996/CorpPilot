# OpenCode Zen 实测记录

2026-09-07，Owner明确授权使用其提供的三个OpenCode官方Zen密钥测试。密钥仅从本任务用户输入取入测试进程内存并注入独立子进程环境，不进入源码、命令行参数或本报告；以下仅用序号区分。

## 实际结果

| 路径 | key 1 | key 2 | key 3 | 结论 |
| --- | --- | --- | --- | --- |
| 官方Zen Chat Completions / big-pickle | HTTP 400 | HTTP 400 | HTTP 400 | 官方返回免费层只能在OpenCode中使用 |
| 官方Zen Chat Completions / minimax-m2.7 | HTTP 401 | HTTP 401 | HTTP 401 | 官方返回Insufficient balance；没有购买或充值 |
| 官方OpenCode 1.18.29 / opencode/big-pickle | 正常退出0 | 正常退出0 | 正常退出0 | 均返回CORPPILOT_ZEN_OK，并收到step_finish / stop |

每个key每种路径仅发起一次测试，API输出上限128 tokens；官方客户端为无工具的纯文本测试，permission=deny，外层90秒期限。客户端三个step_finish都记录cost=0；该值是客户端回执，未登录账单系统核销。没有通过伪造客户端标头绕过免费层限制。

## 隔离与复现

官方npm包opencode-ai@1.18.29安装在仓库外的独立工具目录；实际Windows x64二进制的--version与run --help均已核验。每个key使用不同HOME/USERPROFILE、XDG数据/配置/缓存/状态、TEMP/TMP、空工作目录；凭据仅由CORPPILOT_ZEN_KEY环境变量注入。设置主模型与small_model均为opencode/big-pickle，关闭分享、自动升级和外部插件，不读取项目业务数据。

非交互入口（先按官方说明配置进程环境；切勿把密钥值写进命令）：

```text
opencode --pure --log-level ERROR run --model opencode/big-pickle --format json --title "CorpPilot Zen connectivity" --dir <独立空工作目录> "Return exactly CORPPILOT_ZEN_OK; do not call tools."
```

根实际运行证据位于H:\item\CorpPilot-test-evidence-20260907\zen-client-kltr_krs，包含三个脱敏output.jsonl及report.json。后端代理独立复核事件、退出状态和隔离脚本；未接触密钥。

## 产品验收边界

这是三个key通过官方OpenCode客户端的真实连通性证据，不是CorpPilot完整任务交付、成果审批或双Docker Worker验收。当前工作台文本provider支持Chat Completions；当前CLI后端使用Codex协议，尚未实现OpenCode CLI适配。不能把Zen key直接填进Codex配置后声称已接通，也不能把本测试的免费客户端结果当作工作台直接API成功。

来源：[官方Zen接口与模型说明](https://opencode.ai/docs/zen/)、[CLI说明](https://opencode.ai/docs/cli/)。真实回执优先于文档中的可用性描述。
