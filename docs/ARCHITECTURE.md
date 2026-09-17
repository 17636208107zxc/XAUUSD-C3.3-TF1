# 架构与实际依赖

## 来源与入口

当前主 EA 来源：MT5 终端 `MQL5/Experts/XAUUSD_M15_M5_V3_10_9_C33_TF1_TREND_FREQUENCY.mq5`，发布区为 `src/` 下的逐字节副本。事件入口为 `OnInit()`、`OnTimer()`、`OnTick()`；`OnTick()` 调用 `V310ProcessTick()`，它在 M15、M5 新闭合 K 线出现时分别更新 M15 状态与 M5 候选。

## 主 EA 直接声明的 Include

主 EA 第 15–34 行直接包含 `Include/XAUAI/V3109C33TF1/` 下的 20 个文件，按真实 Include 和调用点分组如下。同版目录共 23 个 `.mqh`，另有 `M15PrimaryStructure.mqh`、`M15PrimaryEvidence.mqh` 和 `FastExecutionAudit.mqh`。

| 层 | 直接引用的文件 |
|---|---|
| 类型 | `Strategy01Types.mqh` |
| M15 | `M15TrendState.mqh`、`M15BullContext.mqh`、`M15BearContext.mqh`、`M15TrendReplay.mqh`、`M15EarlyTrend.mqh` |
| M5 状态/信号 | `M5PullbackState.mqh`、`M5BearPullbackState.mqh`、`BullSignalBar.mqh`、`BearSignalBar.mqh` |
| M5 路线/计划 | `M5EMARecovery.mqh`、`M5CompressionBreak.mqh`、`M5RouteSelection.mqh`、`M5TargetStructure.mqh`、`M5StructureStop.mqh`、`Strategy01Planner.mqh`、`Strategy01SellPlanner.mqh` |
| 方向/槽位 | `M5AsymmetricTrendPolicy.mqh`、`M5MicroCyclePolicy.mqh`、`TradeSlotPolicy.mqh` |

MT5 标准库还包括 `<Trade/Trade.mqh>`。递归检查显示 `M15TrendState.mqh` 传递引用 `M15PrimaryStructure.mqh` 和 `M15PrimaryEvidence.mqh`；其余项目内的 `#include` 均指向同版目录内现有文件。

`M15PrimaryStructure.mqh` **不在主 EA 直接 Include 清单中**，但经 `M15TrendState.mqh` 传递引用，属于当前依赖。`FastExecutionAudit.mqh` 位于同版目录，是否属于当前调用路径需按具体调用点判断，不能只凭文件存在下结论。

## 已可从主文件确认的数据流

```text
OnTick → V310ProcessTick
  ├─ M15 闭合：V310UpdateM15Context
  │   ├─ 400 个历史 M15 节点启动回放
  │   ├─ Primary Structure / Macro Bias / Early Trend
  │   └─ BUY/SELL M5 扫描权限；普通逆势许可关闭
  ├─ M5 闭合：更新 BUY/SELL 回调状态与候选
  │   └─ Original Attempt / Recovery Break / EMA Recovery / Compression Break
  ├─ 候选：槽位与风险检查 → EA AI Review → 挂 Buy Stop/Sell Stop
  └─ 持仓：槽位退出与风控管理
```

关键规则可以从现有依赖核对：Primary Structure 的提升/突破缓冲分别取 `max(2×spread, 0.10×M15 ATR, 2×tick)` 与 `max(2×spread, 0.15×M15 ATR, 2×tick)`；保护结构被连续两根已闭合 M15 K 线越过后进入 TRANSITION。结构止损从信号前一根 M5 极值与最近确认枢轴取更外侧锚点，再在交易计划中留 2 美元价格空间。详见 [STRATEGY.md](STRATEGY.md)。

## 辅助服务与历史代码

`tools/ai_review_service.py` 与其 Python 模块负责外部监控、复盘和飞书通知。它们是从本机现有辅助服务复制的快照，和 MQL5 主 EA 不属于同一进程。配置中的并行 AI 交易功能也必须与 EA 原生交易链分开审阅。

主 `.mq5` 是较长历史版本演进后的文件，保留旧 Signal Engine、旧规则和参数。`V310UpdateM15Context()` 中可直接看到 C3.3 关闭普通逆势战术许可；其他函数是否执行必须继续沿 `OnTick` 实际调用链判定。
