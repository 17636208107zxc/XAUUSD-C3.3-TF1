# 架构与实际依赖

## 来源与入口

当前主 EA 来源：MT5 终端 `MQL5/Experts/XAUUSD_M15_M5_V3_10_9_C33_TF1_TREND_FREQUENCY.mq5`，发布区为 `src/` 下的逐字节副本。事件入口为 `OnInit()`、`OnTimer()`、`OnTick()`；`OnTick()` 调用 `V310ProcessTick()`，它在 M15、M5 新闭合 K 线出现时分别更新 M15 状态与 M5 候选。

## 主 EA 直接声明的 Include

主 EA 第 15–34 行直接包含 `Include/XAUAI/V3109C33TF1/` 下的 20 个文件，按名称和主文件调用点分组如下。**这只是直接引用清单；文件当前缺失，不能据此推断每个模块内部实现或传递依赖。**

| 层 | 直接引用的文件 |
|---|---|
| 类型 | `Strategy01Types.mqh` |
| M15 | `M15TrendState.mqh`、`M15BullContext.mqh`、`M15BearContext.mqh`、`M15TrendReplay.mqh`、`M15EarlyTrend.mqh` |
| M5 状态/信号 | `M5PullbackState.mqh`、`M5BearPullbackState.mqh`、`BullSignalBar.mqh`、`BearSignalBar.mqh` |
| M5 路线/计划 | `M5EMARecovery.mqh`、`M5CompressionBreak.mqh`、`M5RouteSelection.mqh`、`M5TargetStructure.mqh`、`M5StructureStop.mqh`、`Strategy01Planner.mqh`、`Strategy01SellPlanner.mqh` |
| 方向/槽位 | `M5AsymmetricTrendPolicy.mqh`、`M5MicroCyclePolicy.mqh`、`TradeSlotPolicy.mqh` |

MT5 标准库还包括 `<Trade/Trade.mqh>`。若找回头文件，应继续检查每个文件内部的 `#include`，递归完成依赖清单，不能只按上表复制。

`M15PrimaryStructure.mqh` **未在主 EA 直接 Include 清单中出现**；Primary Structure 相关调用在主 EA 中存在，但具体定义可能来自上述头文件。未找回依赖前不能断言其单独文件属于 C33。

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

具体 Promotion Buffer、Break Buffer、保护结构、两根确认、目标空间和结构止损公式位于未取得的依赖中；本文件不擅自补写。

## 辅助服务与历史代码

`tools/ai_review_service.py` 与其 Python 模块负责外部监控、复盘和飞书通知。它们是从本机现有辅助服务复制的快照，和 MQL5 主 EA 不属于同一进程。配置中的并行 AI 交易功能也必须与 EA 原生交易链分开审阅。

主 `.mq5` 是较长历史版本演进后的文件，保留旧 Signal Engine、旧规则和参数。`V310UpdateM15Context()` 中可直接看到 C3.3 关闭普通逆势战术许可；其他函数是否执行必须继续沿 `OnTick` 实际调用链判定。
