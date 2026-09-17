# XAUUSD C3.3-TF1

MT5 的 XAUUSD.s 趋势参与型 EA，当前识别的规则版本为 `EA_OPEN_V3_10_9_C33_TF1_PURE_TREND_FREQUENCY_R1`，参数版本为 `M15_EARLY_TREND_M5_FREQUENCY_3SLOT_C33TF1`。主运行周期为 M5，交易决策使用 M15 与 M5。

> **版本来源：** 主 `.mq5` 取自当前 MT5 终端；23 个同版 `.mqh` 取自本机隐藏开发工作树，20 个由主文件直接引用，其余为同版辅助/传递依赖。发布目录已用 MetaEditor 编译，结果为 **0 errors、0 warnings**。开发工作树的主 `.mq5` 与终端版在 AI 默认模式和模型名上有差异，本仓库保留终端版，未混入另一份主文件。详见 [代码审计记录](docs/CODE_AUDIT_NOTES.md)。

## 当前主链路

```text
M15 趋势/主结构判断 → M15 Early Trend 与方向权限
→ M5 回调、恢复和突破候选 → 风险与槽位检查
→ AI 审核（运行模式由 MT5 输入决定）→ Buy Stop / Sell Stop
→ 持仓保护及 1R/2R 分段退出
```

源码可直接确认：C3.3 将普通逆势战术许可置为关闭；EA 主入口按 M15、M5 已收 K 线更新；默认单笔风险输入为 0.5%，最多三槽、总初始风险上限 1.5%；默认 TP1 为 1R 平 50%，TP2 为 2R 退出剩余仓位；挂单有效期输入为 3 根 M5 K 线；启动时尝试回放 400 个已闭合 M15 节点。

`H1/H2/H3/L1/L2/L3` 是历史兼容的信号尝试名称；例如 `H2` 指 M5 回调后的第二次向上恢复尝试，**不是 H2/两小时图表周期**。当前交易决策周期为 M15 + M5，不使用 H2 时间周期。

源码仍可能包含 Legacy Strategy、旧 Signal Engine、逆势函数和历史参数。**仅凭文件名、函数或参数存在不能认定其属于当前主执行路径；必须从主 EA 的实际调用链核对。**

## 仓库内容

- `src/`：从当前 MT5 终端复制的 C3.3-TF1 主 EA，以及同版本 `Include/XAUAI/V3109C33TF1/`；源码字节保持原样。
- `tools/`、`tests/`：独立的 Python/AI/飞书辅助程序及其适用测试快照；它们不是 MQL5 交易决策函数，也不代表会自动随 EA 部署。含本机账户路径的本地维护脚本及依赖旧 V3.8 源码的测试未复制，见审计记录。
- `config/`：无凭证模板与提示词。真实运行配置、API Key、Webhook、账户路径未纳入。
- `docs/`：策略边界、架构、交接、版本变化和审计记录。

主 EA 原文件 SHA-256：`D50FE61592574FDB70EC5930534CB11E5912B5B3FB46493280BDE36262516D0F`。

当前发布区的 Python 测试：`python -m pytest tests -q`，结果为 379 passed。MetaEditor 对当前主 EA 的编译结果为 0 errors、0 warnings；这些检查不等于交易策略或收益已通过回测验证。

## 使用前

在 MetaEditor 中打开 `src/` 下主 EA 并编译；其相对 Include 已放在 `src/Include/`。实盘使用前仍需核对 MT5 图表实际输入值、经纪商交易规则及本机凭证。配置说明见 [config/README.md](config/README.md)。

此仓库用于协作与审阅，不构成交易收益保证。实盘部署前应独立检查代码、参数、编译、回测和风险。
