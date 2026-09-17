# C3.3-TF1 策略边界（仅按可见源码）

## 已从当前主 EA 核实

| 部分 | 可核实的行为 |
|---|---|
| 周期 | `V310ProcessTick()` 以 M15 和 M5 已闭合 K 线更新；无 H2 时间周期决策。 |
| M15 趋势 | `V310UpdateM15Context()` 推进趋势状态、宏观偏向、Primary pivots，并调用 Early Trend Gate；主结构方向拥有 M5 扫描权。 |
| Pure Trend | `g_v3109_c3_buy_tactical_counter`、`g_v3109_c3_sell_tactical_counter` 和 `counter_quota_available` 被置为 `false`，逆宏观方向 M5 扫描被抑制。 |
| 状态延续 | M15 暂时 LATE/RANGE 时暂停新入场但保存 M5 回调生命周期；真正反向或 `TRANSITION` 时重置对应方向状态和挂单。 |
| 启动回放 | 首次需要时回放 `400` 个已闭合 M15 节点，排除当时未闭合 K 线。 |
| M5 路线 | 主文件调用 Original H2/L2 Attempt、Recovery Break、EMA Recovery、EMA Compression Break 的选路函数；另有独立 Compression Break 分支。H2/L2 是尝试序号，不是周期。 |
| 挂单 | BUY、SELL 候选经风险/槽位检查和 EA AI Review 后，使用 Buy Stop / Sell Stop；默认挂单有效期输入为 `3` 根 M5。 |
| 风险与退出 | 默认 `InpRiskPercent=0.5`；`InpUseStagedExit=true`，TP1=1R 平 50%，保本开关为 true，TP2=2R 并退出剩余仓位，默认不使用结构目标作为服务器 TP。 |
| 槽位 | `TradeSlotPolicy.mqh` 定义最多 3 槽、同向敞口、至少 900 秒成交间隔、单槽风险硬上限 1%、总初始风险上限 1.5%；EA 默认单笔输入为 0.5%。Early Trend 仅在尚无已占槽位时允许入场。 |

主 EA 包含 `InpAIMode=AI_OFF`、API Key 文件输入和 `g_ai.Review()` 调用。实际挂载图表的输入值可能覆盖默认值；不能仅凭源码默认值断言当时 DeepSeek 是否参与交易。外部 Python/并行 AI 是另一个进程，不能混同于 EA 内置审核。

## 已核对的结构与入场细节

- `M15PrimaryStructure.mqh` 定义 `PRIMARY_RANGE/BULL/BEAR/TRANSITION_FROM_BULL/TRANSITION_FROM_BEAR`。RANGE 中需要有序 HH/HL 或 LL/LH、EMA 条件及提升缓冲才能建立主趋势。Promotion Buffer 为 `max(2×spread, 0.10×M15 ATR14, 2×tick)`；Break Buffer 为 `max(2×spread, 0.15×M15 ATR14, 2×tick)`。
- 多头回调形成候选低点后，越过前主高点及 Promotion Buffer 才将该低点提升为保护低点；空头镜像处理。价格连续两根已闭合 M15 K 线收在保护结构和 Break Buffer 之外，进入对应 TRANSITION。
- `M15EarlyTrend.mqh` 仅在 Primary 为 RANGE、方向不与宏观偏向冲突时评估早期趋势；还检查 EMA20 位置与斜率、最近四根 K 线同侧数量、保护结构和参考高低点突破。Early 只给一槽权限，不直接改写 Primary 状态。
- M5 有原尝试/恢复突破/EMA Recovery/EMA Compression Break 路线。EMA Recovery 使用 20 根 M5 的 EMA20 周期、真实回调或反弹、EMA 方向和方向性恢复 K 线；EMA Compression Break 要求压缩区多数 K 线靠近 EMA20 后出现方向突破。路线细节以相应头文件和主 EA 调用点为准。
- `M5TargetStructure.mqh` 只使用已确认、未被已闭合 K 线消耗的 M5/M15 枢轴作目标。`Strategy01Planner.mqh` / `Strategy01SellPlanner.mqh` 在存在有效结构目标且距离不足 2R 时拒绝；无有效结构目标时不因该门槛拒绝。
- `M5StructureStop.mqh` 用信号前一根 M5 K 线的极值及最近确认的 M5 结构枢轴选择更外侧锚点；多单在下方、空单在上方。交易计划分别在锚点外侧再留 2 美元价格空间。

以上是静态源码和 0 errors / 0 warnings 编译验证，不代表回测或实盘效果已经验证。

## 旧参数和兼容实现

主文件仍定义 `InpMaxSignalBarUSD`、`InpMaxSLATR`，并在旧 Signal Engine 调用点出现。这只证明参数/代码存在；是否影响 C3.3 Pure Trend 主路径必须继续核对调用关系，不能列为已确认的当前入场规则。旧逆势配额及 Countertrend 函数同理。
