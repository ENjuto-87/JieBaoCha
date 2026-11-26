# 本机 DeepSeek 接口

这次把原有DeepSeek调用整理为可配置的后端接口，增加本机密钥配置工具、网页配置状态和后台连接测试。网页、桌面仍调用原 `review_contract(contract_text, use_ai=True)`，本地规则与三种报告沿用现有模块。

## 三步启用

1. 在DeepSeek官方平台获取自己的API Key并确认API账户可用。**不要把Key发到聊天里，也不要粘贴进合同文本区。**聊天产品订阅与API账户能力应以服务商控制台为准。
2. 双击项目目录的 `配置AI接口.bat`。在本机终端粘贴Key，输入不会显示；回车后填写模型ID，默认 `deepseek-flash`。配置工具本身不联网。空白Key或Ctrl+C取消，不破坏原配置。
3. 打开 http://127.0.0.1:8765 ，点击“刷新配置”，再点“测试连接”。测试仅发送固定的连通性消息，不发送合同，但可能产生少量API费用。通过后勾选“启用AI辅助审查”，核对并确认原文，点击“开始审查”。

双击 `启动网页版.bat` 可以启动服务。若已运行旧版本，请先保存当前报告、停止旧服务，再启动本版；新版本已由本次部署重启。桌面版可继续使用 `启动合同审查Demo.bat`，勾选原有AI模式即可读取同一配置。

Key使用Windows当前用户DPAPI加密后，保存在本目录隐藏名称 `.ai-settings.json`。密钥不写入HTML、浏览器存储、JSON/Word报告、控制台输出或代码包。该文件仅在用户配置后生成；请勿提交或分发。更换Windows用户/电脑后通常不能解密，应重新配置。加密不能防止同一Windows账户下的恶意程序读取，仍须妥善保护本机账户。

模型等参数与密钥一起读取，每次审查固定一份设置。改变配置后点击网页“刷新配置”；后端也会使使用旧AI配置的确认与快照失效。正在处理时变化会丢弃本次结果，要求重新确认。报告的流程消息保留请求模型及超时设置，不保存密钥。

## 既有环境变量仍兼容

可在PyCharm运行配置的“环境变量”栏配置以下名称；不要写进源码或启动脚本：

| 名称 | 用途 | 默认值 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 当前进程密钥，优先于本机加密文件 | 无 |
| `DEEPSEEK_MODEL` | 服务商模型ID | `deepseek-flash` |
| `DEEPSEEK_TIMEOUT` | 单次SDK网络超时配置，10–180秒 | 60 |
| `DEEPSEEK_MAX_TOKENS` | 输出上限，256–16384 | 8192 |

已有进程环境变量Key时，本机文件不生效；如要用配置工具保存的密钥，请移除该进程环境变量并重启。更改环境变量后须重启运行进程。`CONTRACT_REVIEW_AI_CONFIG`可由维护人员指定另一个本机加密配置路径，普通用户不需设置。

命令行入口：

```powershell
.\.venv\Scripts\python.exe configure_ai.py --status
.\.venv\Scripts\python.exe configure_ai.py --test
```

`--status`不联网且不显示Key；`--test`发送一次固定测试请求。默认交互配置必须在支持隐藏输入的终端中运行，不支持时停止，不会退回明文回显。

## 请求、结果与失败处理

- 当前接通DeepSeek官方HTTPS端点 `https://api.deepseek.com`，使用现有OpenAI兼容SDK的Chat Completions协议。不新增依赖，不开放网页任意URL代理。该接口不等同于OpenAI账户接口。
- 采用JSON Object输出、明确JSON示例、非思考模式与输出上限。官方当前文档列出 `deepseek-flash` 和 `deepseek-v4-pro`；模型可用性仍须用实际账户验证。参考：[接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON输出](https://api-docs.deepseek.com/guides/json_mode/)、[思考模式开关](https://api-docs.deepseek.com/guides/thinking_mode/)，检索日期2026-10-12。
- 默认60秒网络超时、0自动重试，避免错误请求重复计费。超时是SDK网络配置，不是整个审查的精确倒计时；本版不提供强制中断已发送AI请求。
- 单次AI文本上限3万字符。超限时保留本地结果，明确提示未发送、未截断；拆分审查仍须核对跨段关系。网页/本地规则原50万字符输入能力与AI上限分别显示。
- AI结构和字段严格验证；空内容、非法JSON、字段缺失、截断/过滤都标记AI未完成，不将半份意见当完整结果。401、402、403、429、5xx、网络失败有受控提示，不回显原始响应正文或认证信息。
- 原三类证据核验仍以确认后的文本为依据。AI意图分析、保证与抵押识别、法律解释正确性尚未经过真实法律样本验收，本次提示词调整不能宣称已解决这些问题。也未增加在线法条检索。
- 导出只读冻结快照，不重复调用AI；同次报告包含相同原文、风险和报告编号。

## 实现与维护

`ai_config.py`负责环境变量、Windows加密配置及无密钥公开状态；`configure_ai.py`负责本机交互。`ai_client.py`只负责SDK请求和不含合同的连接探测。`contract_checker.call_deepseek`保留原兼容函数，负责提示词与JSON校验；`review_service`组合本地与AI结果。网页新增 `/api/ai/status` 与本会话 `/api/documents/<id>/ai-test`，连接测试也在后台运行，并限制15秒内不重复测试。

以后扩展其他服务商应在后端增加明确的提供商适配与独立密钥配置，继续输出原审查协议；不要让网页把密钥或任意接口地址随合同发送。当前版本只配置DeepSeek，不假定其他服务商接受相同参数。

## 验证边界

本次尚未获得真实Key，**未进行真实DeepSeek连通、余额、计费及法律质量验收**。已用真实SDK向本机HTTP模拟服务验证请求、错误响应和网页任务；Windows加密存储使用真实DPAPI与虚构密钥。详见 `AI接口验证记录.md`，不要把这些测试理解为真实模型返回。
