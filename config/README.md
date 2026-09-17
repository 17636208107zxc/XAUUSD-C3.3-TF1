# 配置与凭证

`review_config.example.json` 是脱敏示例，不是当前机器的运行配置。复制为本机 `review_config_v3109.json` 后，按本机路径与用途配置；真实配置文件被 `.gitignore` 排除。

DeepSeek API Key 可由环境变量 `DEEPSEEK_API_KEY` 或本机凭证文件提供；飞书 Webhook 可由环境变量或本机 `lark_webhook.txt` 提供。请勿提交真实值，也不要把账号、交易流水或 MT5 Common/Files 运行目录复制进仓库。

本目录的提示词来自本机辅助服务当前快照。Python 辅助服务与 EA 主交易逻辑是不同组件；服务是否启用、使用哪个模型以及通知目标，均取决于本机运行配置。
