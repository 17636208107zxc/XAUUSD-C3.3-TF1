# AI 交接：C3.3-TF1

在没有得到明确授权之前：

1. 不得修改策略或自行放宽条件。
2. 不得把历史兼容代码当成当前执行逻辑。
3. 不得把 H1/H2/H3/L1/L2/L3 解释成时间周期。
4. 不得根据文件名推断策略；必须从主 EA 实际调用链确认规则。
5. 修改前必须先说明准备修改什么以及为什么。

本版定位为 **Pure Trend**。主 EA `V310UpdateM15Context()` 中将普通逆势战术许可明确置为 `false`，M15 宏观主多时抑制 SELL 方向 M5 扫描，主空时抑制 BUY 方向扫描。源码中保留的 Countertrend 函数、旧 3:1 配额、Legacy Strategy、旧 Signal Engine 和历史参数，不代表当前主链路启用。

当前交易决策周期为 M15 + M5。H2 代表 M5 回调后的第二次向上恢复尝试，不是 2 小时周期；L2 是对应的向下尝试命名。

## 阅读顺序

1. `src/XAUUSD_M15_M5_V3_10_9_C33_TF1_TREND_FREQUENCY.mq5` 的 `#include`、`OnInit()`、`OnTick()`、`V310ProcessTick()`。
2. `V310UpdateM15Context()`：M15 主结构、Early Trend、方向权限。
3. M5 BUY/SELL 候选与 `V3109C32SelectLiveSlot()`、挂单提交、退出管理。
4. 检查 `src/Include/XAUAI/V3109C33TF1/` 中实际被调用的函数及传递依赖；不要用开发工作树另一份 AI 默认参数不同的主文件替换本仓库主 EA。
5. Python `tools/` 是独立 AI/复盘/飞书服务。区分 EA 内置 `g_ai.Review()` 与外部并行 AI 程序，不能混为一谈；实际启用状态看运行配置。

当前主 EA 的规则标识为 `EA_OPEN_V3_10_9_C33_TF1_PURE_TREND_FREQUENCY_R1`，参数标识为 `M15_EARLY_TREND_M5_FREQUENCY_3SLOT_C33TF1`。不要把 C32B、C31 或 V3103 的 Include 当作同版 C33 依赖。

## 本次仓库状态

本仓库含当前终端主 EA 和 23 个同版头文件，独立 MetaEditor 编译为 0 errors、0 warnings。编译通过不等于策略收益或实盘参数已验证；详见 [CODE_AUDIT_NOTES.md](CODE_AUDIT_NOTES.md)。
