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

主 EA 包含 `InpAIMode=AI_OFF`、API Key 文件输入和 `g_ai.Review()` 调用。实际挂载图表的输入值可能覆盖默认值；不能仅凭源码默认值断言当时 DeepSeek 是否参与交易。外部 Python/并行 AI 是另一个进程，不能混同于 EA 内置审核。

## 尚待同版 Include 核实

以下是项目要求关注的规则，但其定义或常量位于当前缺失的 `V3109C33TF1` 头文件中，**本发布区不能把它们写成已验证事实**：

- Primary Structure 的 Promotion Buffer、Break Buffer、保护结构及两根 M15 趋势失效确认公式；
- `PRIMARY_BULL`、`PRIMARY_BEAR`、`RANGE`、`TRANSITION` 的精确转换条件；
- Early Trend Gate 的完整判定与 Early 阶段最多一槽的实现；
- EMA Recovery、EMA Compression Break、目标空间和结构 SL 的具体阈值/计算；
- `V3109C32_MAX_SLOTS` 的值与总初始风险 1.5% 的常量实现。

找回同版头文件后，应从 `OnTick` 逐级确认调用链，补充准确公式并以 MetaEditor 编译与回测验证。不得用旧版文件推断这些规则。

## 旧参数和兼容实现

主文件仍定义 `InpMaxSignalBarUSD`、`InpMaxSLATR`，并在旧 Signal Engine 调用点出现。这只证明参数/代码存在；是否影响 C3.3 Pure Trend 主路径必须继续核对调用关系，不能列为已确认的当前入场规则。旧逆势配额及 Countertrend 函数同理。
