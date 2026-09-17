"""V3.10.9 C3.2-B1 —— M15 主结构状态机的 Python 移植。

来源：EA 源码 Include/XAUAI/V3109C32BParticipation/M15PrimaryStructure.mqh
      （引擎本体）与 M15TrendState.mqh（包装层）。

这是"独立 AI 参考实现"的第 1 步：只做 M15 定方向，不涉及 M5 入场。
移植要求逐行为忠实，包括 reason 字符串，以便和 EA 的 EA_Trace 逐帧对账。

时间统一用整数秒表示（0 表示"未设置"），与 MQL5 的 datetime + ZeroMemory 语义一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- 方向与局部相位 -------------------------------------------------------

PRIMARY_RANGE = 0
PRIMARY_BULL = 1
PRIMARY_TRANSITION_FROM_BULL = 2
PRIMARY_BEAR = 3
PRIMARY_TRANSITION_FROM_BEAR = 4

LOCAL_NEUTRAL = 0
LOCAL_IMPULSE = 1
LOCAL_PULLBACK = 2
LOCAL_RESUME = 3
LOCAL_EXHAUSTED = 4

PRIMARY_LABELS = {
    PRIMARY_RANGE: "PRIMARY_RANGE",
    PRIMARY_BULL: "PRIMARY_BULL",
    PRIMARY_TRANSITION_FROM_BULL: "PRIMARY_TRANSITION_FROM_BULL",
    PRIMARY_BEAR: "PRIMARY_BEAR",
    PRIMARY_TRANSITION_FROM_BEAR: "PRIMARY_TRANSITION_FROM_BEAR",
}

LOCAL_PHASE_LABELS = {
    LOCAL_NEUTRAL: "LOCAL_NEUTRAL",
    LOCAL_IMPULSE: "LOCAL_IMPULSE",
    LOCAL_PULLBACK: "LOCAL_PULLBACK",
    LOCAL_RESUME: "LOCAL_RESUME",
    LOCAL_EXHAUSTED: "LOCAL_EXHAUSTED",
}

PRIMARY_LABEL_TO_VALUE = {v: k for k, v in PRIMARY_LABELS.items()}
LOCAL_PHASE_LABEL_TO_VALUE = {v: k for k, v in LOCAL_PHASE_LABELS.items()}


def primary_label(value: int) -> str:
    return PRIMARY_LABELS.get(value, "PRIMARY_UNKNOWN")


def local_phase_label(value: int) -> str:
    return LOCAL_PHASE_LABELS.get(value, "LOCAL_UNKNOWN")


# --- 缓冲与放行 -----------------------------------------------------------


def promotion_buffer(spread_price: float, atr14: float, tick_size: float) -> float:
    return max(
        2.0 * max(0.0, spread_price),
        max(0.10 * max(0.0, atr14), 2.0 * max(0.0, tick_size)),
    )


def break_buffer(spread_price: float, atr14: float, tick_size: float) -> float:
    return max(
        2.0 * max(0.0, spread_price),
        max(0.15 * max(0.0, atr14), 2.0 * max(0.0, tick_size)),
    )


def allows_buy(state: int) -> bool:
    return state == PRIMARY_BULL


def allows_sell(state: int) -> bool:
    return state == PRIMARY_BEAR


# --- 输入 / 记忆 / 输出 ---------------------------------------------------


@dataclass
class PrimaryInput:
    closed_bar_time: int = 0
    close_price: float = 0.0
    ema20: float = 0.0
    ema_slope_atr: float = 0.0
    atr14: float = 0.0
    spread_price: float = 0.0
    tick_size: float = 0.0
    latest_high: float = 0.0
    previous_high: float = 0.0
    latest_low: float = 0.0
    previous_low: float = 0.0
    latest_high_time: int = 0
    previous_high_time: int = 0
    latest_low_time: int = 0
    previous_low_time: int = 0
    bull_late: bool = False
    bear_late: bool = False


@dataclass
class PrimaryMemory:
    primary: int = PRIMARY_RANGE
    local_phase: int = LOCAL_NEUTRAL
    state_started_at: int = 0
    protected_low: float = 0.0
    protected_high: float = 0.0
    protected_time: int = 0
    dominant_high: float = 0.0
    dominant_low: float = 0.0
    dominant_time: int = 0
    candidate_low: float = 0.0
    candidate_low_time: int = 0
    candidate_high: float = 0.0
    candidate_high_time: int = 0
    break_close_count: int = 0
    broken_level: float = 0.0
    prebreak_dominant: float = 0.0
    transition_started_at: int = 0
    reversal_low: float = 0.0
    reversal_low_time: int = 0
    reversal_high: float = 0.0
    reversal_high_time: int = 0
    recovery_low: float = 0.0
    recovery_low_time: int = 0
    recovery_high: float = 0.0
    recovery_high_time: int = 0
    last_seen_high_time: int = 0
    last_seen_low_time: int = 0


@dataclass
class PrimaryResult:
    primary: int = PRIMARY_RANGE
    local_phase: int = LOCAL_NEUTRAL
    allow_buy: bool = False
    allow_sell: bool = False
    changed: bool = False
    reason: str = ""
    promotion_buffer: float = 0.0
    break_buffer: float = 0.0


def reset_primary_memory(memory: PrimaryMemory) -> None:
    fresh = PrimaryMemory()
    for name, value in vars(fresh).items():
        setattr(memory, name, value)


def _clear_candidate(memory: PrimaryMemory) -> None:
    memory.candidate_low = 0.0
    memory.candidate_low_time = 0
    memory.candidate_high = 0.0
    memory.candidate_high_time = 0


def _clear_transition_evidence(memory: PrimaryMemory) -> None:
    memory.break_close_count = 0
    memory.broken_level = 0.0
    memory.prebreak_dominant = 0.0
    memory.transition_started_at = 0
    memory.reversal_low = 0.0
    memory.reversal_low_time = 0
    memory.reversal_high = 0.0
    memory.reversal_high_time = 0
    memory.recovery_low = 0.0
    memory.recovery_low_time = 0
    memory.recovery_high = 0.0
    memory.recovery_high_time = 0


def _enter_bull(
    memory: PrimaryMemory,
    bar_time: int,
    protected_low: float,
    protected_time: int,
    dominant_high: float,
    dominant_time: int,
) -> None:
    memory.primary = PRIMARY_BULL
    memory.local_phase = LOCAL_IMPULSE
    memory.state_started_at = bar_time
    memory.protected_low = protected_low
    memory.protected_high = 0.0
    memory.protected_time = protected_time
    memory.dominant_high = dominant_high
    memory.dominant_low = 0.0
    memory.dominant_time = dominant_time
    _clear_candidate(memory)
    _clear_transition_evidence(memory)


def _enter_bear(
    memory: PrimaryMemory,
    bar_time: int,
    protected_high: float,
    protected_time: int,
    dominant_low: float,
    dominant_time: int,
) -> None:
    memory.primary = PRIMARY_BEAR
    memory.local_phase = LOCAL_IMPULSE
    memory.state_started_at = bar_time
    memory.protected_high = protected_high
    memory.protected_low = 0.0
    memory.protected_time = protected_time
    memory.dominant_low = dominant_low
    memory.dominant_high = 0.0
    memory.dominant_time = dominant_time
    _clear_candidate(memory)
    _clear_transition_evidence(memory)


def _bull_ema(data: PrimaryInput) -> bool:
    return data.close_price > data.ema20 and data.ema_slope_atr > 0.0


def _bear_ema(data: PrimaryInput) -> bool:
    return data.close_price < data.ema20 and data.ema_slope_atr < 0.0


def _finalize_primary(
    memory: PrimaryMemory,
    promotion: float,
    brk: float,
    changed: bool,
    reason: str,
    out: PrimaryResult,
) -> None:
    out.primary = memory.primary
    out.local_phase = memory.local_phase
    out.allow_buy = allows_buy(memory.primary) and memory.local_phase != LOCAL_EXHAUSTED
    out.allow_sell = allows_sell(memory.primary) and memory.local_phase != LOCAL_EXHAUSTED
    if out.allow_buy and out.allow_sell:
        out.allow_buy = False
        out.allow_sell = False
    out.changed = changed
    out.reason = reason
    out.promotion_buffer = promotion
    out.break_buffer = brk


def _advance_bull(
    data: PrimaryInput,
    promotion: float,
    brk: float,
    memory: PrimaryMemory,
    state: dict[str, Any],
) -> None:
    new_low = data.latest_low > 0.0 and data.latest_low_time > memory.last_seen_low_time
    new_high = data.latest_high > 0.0 and data.latest_high_time > memory.last_seen_high_time

    if new_low and data.latest_low_time > memory.dominant_time:
        memory.candidate_low = data.latest_low
        memory.candidate_low_time = data.latest_low_time
        memory.local_phase = LOCAL_PULLBACK
        state["changed"] = True
        state["reason"] = "BULL_CANDIDATE_LOW"

    if (
        new_high
        and memory.candidate_low_time > memory.dominant_time
        and data.latest_high_time > memory.candidate_low_time
        and data.latest_high > memory.dominant_high + promotion
    ):
        memory.protected_low = memory.candidate_low
        memory.protected_time = memory.candidate_low_time
        memory.dominant_high = data.latest_high
        memory.dominant_time = data.latest_high_time
        _clear_candidate(memory)
        memory.local_phase = LOCAL_RESUME
        state["changed"] = True
        state["reason"] = "BULL_PROMOTE_LOW_ON_NEW_HIGH"

    if memory.protected_low > 0.0 and data.close_price < memory.protected_low - brk:
        memory.break_close_count += 1
    else:
        memory.break_close_count = 0

    if memory.break_close_count >= 2:
        memory.primary = PRIMARY_TRANSITION_FROM_BULL
        memory.local_phase = LOCAL_NEUTRAL
        memory.state_started_at = data.closed_bar_time
        memory.transition_started_at = data.closed_bar_time
        memory.broken_level = memory.protected_low
        memory.prebreak_dominant = memory.dominant_high
        memory.break_close_count = 0
        _clear_candidate(memory)
        state["changed"] = True
        state["reason"] = "BULL_PROTECTED_LOW_TWO_CLOSE_BREAK"
    elif data.bull_late:
        memory.local_phase = LOCAL_EXHAUSTED
    elif memory.local_phase == LOCAL_EXHAUSTED:
        memory.local_phase = LOCAL_PULLBACK if memory.candidate_low_time > 0 else LOCAL_IMPULSE


def _advance_bear(
    data: PrimaryInput,
    promotion: float,
    brk: float,
    memory: PrimaryMemory,
    state: dict[str, Any],
) -> None:
    new_high = data.latest_high > 0.0 and data.latest_high_time > memory.last_seen_high_time
    new_low = data.latest_low > 0.0 and data.latest_low_time > memory.last_seen_low_time

    if new_high and data.latest_high_time > memory.dominant_time:
        memory.candidate_high = data.latest_high
        memory.candidate_high_time = data.latest_high_time
        memory.local_phase = LOCAL_PULLBACK
        state["changed"] = True
        state["reason"] = "BEAR_CANDIDATE_HIGH"

    if (
        new_low
        and memory.candidate_high_time > memory.dominant_time
        and data.latest_low_time > memory.candidate_high_time
        and data.latest_low < memory.dominant_low - promotion
    ):
        memory.protected_high = memory.candidate_high
        memory.protected_time = memory.candidate_high_time
        memory.dominant_low = data.latest_low
        memory.dominant_time = data.latest_low_time
        _clear_candidate(memory)
        memory.local_phase = LOCAL_RESUME
        state["changed"] = True
        state["reason"] = "BEAR_PROMOTE_HIGH_ON_NEW_LOW"

    if memory.protected_high > 0.0 and data.close_price > memory.protected_high + brk:
        memory.break_close_count += 1
    else:
        memory.break_close_count = 0

    if memory.break_close_count >= 2:
        memory.primary = PRIMARY_TRANSITION_FROM_BEAR
        memory.local_phase = LOCAL_NEUTRAL
        memory.state_started_at = data.closed_bar_time
        memory.transition_started_at = data.closed_bar_time
        memory.broken_level = memory.protected_high
        memory.prebreak_dominant = memory.dominant_low
        memory.break_close_count = 0
        _clear_candidate(memory)
        state["changed"] = True
        state["reason"] = "BEAR_PROTECTED_HIGH_TWO_CLOSE_BREAK"
    elif data.bear_late:
        memory.local_phase = LOCAL_EXHAUSTED
    elif memory.local_phase == LOCAL_EXHAUSTED:
        memory.local_phase = LOCAL_PULLBACK if memory.candidate_high_time > 0 else LOCAL_IMPULSE


def _advance_from_bull_transition(
    data: PrimaryInput,
    promotion: float,
    memory: PrimaryMemory,
    state: dict[str, Any],
) -> None:
    post_low = data.latest_low > 0.0 and data.latest_low_time > memory.transition_started_at
    post_high = data.latest_high > 0.0 and data.latest_high_time > memory.transition_started_at

    if post_low and data.latest_low_time > memory.reversal_low_time:
        memory.recovery_low = data.latest_low
        memory.recovery_low_time = data.latest_low_time
        if data.latest_low < memory.broken_level - promotion:
            memory.reversal_low = data.latest_low
            memory.reversal_low_time = data.latest_low_time

    if (
        post_high
        and memory.reversal_low_time > 0
        and data.latest_high_time > memory.reversal_low_time
        and data.latest_high < memory.prebreak_dominant
    ):
        memory.reversal_high = data.latest_high
        memory.reversal_high_time = data.latest_high_time

    if (
        post_high
        and memory.recovery_low_time > 0
        and data.latest_high_time > memory.recovery_low_time
        and data.latest_high > memory.prebreak_dominant + promotion
    ):
        memory.recovery_high = data.latest_high
        memory.recovery_high_time = data.latest_high_time

    if memory.recovery_high_time > memory.recovery_low_time and _bull_ema(data):
        _enter_bull(
            memory,
            data.closed_bar_time,
            memory.recovery_low,
            memory.recovery_low_time,
            memory.recovery_high,
            memory.recovery_high_time,
        )
        state["changed"] = True
        state["reason"] = "BULL_RECOVERY_NEW_STRUCTURE"
    elif memory.reversal_high_time > memory.reversal_low_time and _bear_ema(data):
        _enter_bear(
            memory,
            data.closed_bar_time,
            memory.reversal_high,
            memory.reversal_high_time,
            memory.reversal_low,
            memory.reversal_low_time,
        )
        state["changed"] = True
        state["reason"] = "BEAR_REVERSAL_CAUSAL_STRUCTURE"


def _advance_from_bear_transition(
    data: PrimaryInput,
    promotion: float,
    memory: PrimaryMemory,
    state: dict[str, Any],
) -> None:
    post_high = data.latest_high > 0.0 and data.latest_high_time > memory.transition_started_at
    post_low = data.latest_low > 0.0 and data.latest_low_time > memory.transition_started_at

    if post_high and data.latest_high_time > memory.reversal_high_time:
        memory.recovery_high = data.latest_high
        memory.recovery_high_time = data.latest_high_time
        if data.latest_high > memory.broken_level + promotion:
            memory.reversal_high = data.latest_high
            memory.reversal_high_time = data.latest_high_time

    if (
        post_low
        and memory.reversal_high_time > 0
        and data.latest_low_time > memory.reversal_high_time
        and data.latest_low > memory.prebreak_dominant
    ):
        memory.reversal_low = data.latest_low
        memory.reversal_low_time = data.latest_low_time

    if (
        post_low
        and memory.recovery_high_time > 0
        and data.latest_low_time > memory.recovery_high_time
        and data.latest_low < memory.prebreak_dominant - promotion
    ):
        memory.recovery_low = data.latest_low
        memory.recovery_low_time = data.latest_low_time

    if memory.recovery_low_time > memory.recovery_high_time and _bear_ema(data):
        _enter_bear(
            memory,
            data.closed_bar_time,
            memory.recovery_high,
            memory.recovery_high_time,
            memory.recovery_low,
            memory.recovery_low_time,
        )
        state["changed"] = True
        state["reason"] = "BEAR_RECOVERY_NEW_STRUCTURE"
    elif memory.reversal_low_time > memory.reversal_high_time and _bull_ema(data):
        _enter_bull(
            memory,
            data.closed_bar_time,
            memory.reversal_low,
            memory.reversal_low_time,
            memory.reversal_high,
            memory.reversal_high_time,
        )
        state["changed"] = True
        state["reason"] = "BULL_REVERSAL_CAUSAL_STRUCTURE"


def advance_primary(
    data: PrimaryInput,
    memory: PrimaryMemory,
    out: PrimaryResult | None = None,
) -> PrimaryResult:
    """推进一根已收线 M15；与 MQL5 的 V3109C21AdvancePrimary 逐行对应。"""
    if out is None:
        out = PrimaryResult()
    promotion = promotion_buffer(data.spread_price, data.atr14, data.tick_size)
    brk = break_buffer(data.spread_price, data.atr14, data.tick_size)
    before_primary = memory.primary
    before_phase = memory.local_phase
    before_protected_low = memory.protected_low
    before_protected_high = memory.protected_high
    before_dominant_high = memory.dominant_high
    before_dominant_low = memory.dominant_low
    state: dict[str, Any] = {"changed": False, "reason": "NO_PRIMARY_CHANGE"}

    if memory.primary == PRIMARY_RANGE:
        bull_order = (
            data.previous_high_time > 0
            and data.previous_low_time > 0
            and data.previous_high_time < data.latest_low_time
            and data.latest_low_time < data.latest_high_time
        )
        bull_structure = (
            bull_order
            and data.latest_high > data.previous_high + promotion
            and data.latest_low > data.previous_low
            and _bull_ema(data)
        )
        bear_order = (
            data.previous_low_time > 0
            and data.previous_high_time > 0
            and data.previous_low_time < data.latest_high_time
            and data.latest_high_time < data.latest_low_time
        )
        bear_structure = (
            bear_order
            and data.latest_low < data.previous_low - promotion
            and data.latest_high < data.previous_high
            and _bear_ema(data)
        )
        if bull_structure:
            _enter_bull(
                memory,
                data.closed_bar_time,
                data.latest_low,
                data.latest_low_time,
                data.latest_high,
                data.latest_high_time,
            )
            state["changed"] = True
            state["reason"] = "BULL_BOOTSTRAP_ORDERED_HH_HL"
        elif bear_structure:
            _enter_bear(
                memory,
                data.closed_bar_time,
                data.latest_high,
                data.latest_high_time,
                data.latest_low,
                data.latest_low_time,
            )
            state["changed"] = True
            state["reason"] = "BEAR_BOOTSTRAP_ORDERED_LL_LH"
    elif memory.primary == PRIMARY_BULL:
        _advance_bull(data, promotion, brk, memory, state)
    elif memory.primary == PRIMARY_BEAR:
        _advance_bear(data, promotion, brk, memory, state)
    elif memory.primary == PRIMARY_TRANSITION_FROM_BULL:
        _advance_from_bull_transition(data, promotion, memory, state)
    elif memory.primary == PRIMARY_TRANSITION_FROM_BEAR:
        _advance_from_bear_transition(data, promotion, memory, state)

    if data.latest_high_time > memory.last_seen_high_time:
        memory.last_seen_high_time = data.latest_high_time
    if data.latest_low_time > memory.last_seen_low_time:
        memory.last_seen_low_time = data.latest_low_time

    changed = bool(state["changed"]) or (
        before_primary != memory.primary
        or before_phase != memory.local_phase
        or before_protected_low != memory.protected_low
        or before_protected_high != memory.protected_high
        or before_dominant_high != memory.dominant_high
        or before_dominant_low != memory.dominant_low
    )
    _finalize_primary(memory, promotion, brk, changed, str(state["reason"]), out)
    return out


# --- 与 M15TrendState.mqh 对应的包装层 ------------------------------------

M15_RANGE = 0
M15_EARLY_BULL = 1
M15_BULL = 2
M15_LATE_BULL = 3
M15_TRANSITION = 4
M15_EARLY_BEAR = 5
M15_BEAR = 6
M15_LATE_BEAR = 7


def legacy_state(primary: PrimaryResult) -> int:
    if primary.primary == PRIMARY_BULL:
        return M15_LATE_BULL if primary.local_phase == LOCAL_EXHAUSTED else M15_BULL
    if primary.primary == PRIMARY_BEAR:
        return M15_LATE_BEAR if primary.local_phase == LOCAL_EXHAUSTED else M15_BEAR
    if primary.primary in (PRIMARY_TRANSITION_FROM_BULL, PRIMARY_TRANSITION_FROM_BEAR):
        return M15_TRANSITION
    return M15_RANGE


@dataclass
class TrendContext:
    state: int = M15_RANGE
    primary: int = PRIMARY_RANGE
    local_phase: int = LOCAL_NEUTRAL
    allow_buy_scan: bool = False
    allow_sell_scan: bool = False
    hard_exhaustion_bull: bool = False
    hard_exhaustion_bear: bool = False
    reason_buy: str = ""
    reason_sell: str = ""
    primary_reason: str = ""
    state_started_at: int = 0


@dataclass
class TrendEngine:
    memory: PrimaryMemory = field(default_factory=PrimaryMemory)
    state: int = M15_RANGE
    state_started_at: int = 0

    def advance(self, data: PrimaryInput) -> TrendContext:
        result = advance_primary(data, self.memory)
        nxt = legacy_state(result)
        if self.state != nxt:
            self.state = nxt
            self.state_started_at = data.closed_bar_time

        ctx = TrendContext(
            state=self.state,
            primary=result.primary,
            local_phase=result.local_phase,
            allow_buy_scan=(self.state == M15_BULL and result.allow_buy),
            allow_sell_scan=(self.state == M15_BEAR and result.allow_sell),
            hard_exhaustion_bull=(self.state == M15_LATE_BULL),
            hard_exhaustion_bear=(self.state == M15_LATE_BEAR),
            primary_reason=result.reason,
            state_started_at=self.state_started_at,
        )
        if self.state == M15_BULL:
            ctx.reason_buy = "C21_PRIMARY_BULL"
            ctx.reason_sell = "C21_BLOCK_SELL_PRIMARY_BULL"
        elif self.state == M15_BEAR:
            ctx.reason_buy = "C21_BLOCK_BUY_PRIMARY_BEAR"
            ctx.reason_sell = "C21_PRIMARY_BEAR"
        elif self.state == M15_LATE_BULL:
            ctx.reason_buy = "C21_LOCAL_EXHAUSTED_BULL"
            ctx.reason_sell = "C21_BLOCK_SELL_PRIMARY_BULL"
        elif self.state == M15_LATE_BEAR:
            ctx.reason_buy = "C21_BLOCK_BUY_PRIMARY_BEAR"
            ctx.reason_sell = "C21_LOCAL_EXHAUSTED_BEAR"
        elif self.state == M15_TRANSITION:
            ctx.reason_buy = "C21_PRIMARY_TRANSITION"
            ctx.reason_sell = "C21_PRIMARY_TRANSITION"
        else:
            ctx.reason_buy = "C21_PRIMARY_RANGE"
            ctx.reason_sell = "C21_PRIMARY_RANGE"
        return ctx


# --- M15 主结构 pivot（对应 M15PrimaryEvidence.mqh）------------------------

PRIMARY_PIVOT_WING = 20
PRIMARY_LOOKBACK = 300


@dataclass
class PrimaryPivots:
    latest_high: float = 0.0
    previous_high: float = 0.0
    latest_low: float = 0.0
    previous_low: float = 0.0
    latest_high_time: int = 0
    previous_high_time: int = 0
    latest_low_time: int = 0
    previous_low_time: int = 0


def derive_primary_pivots(
    bars: list[dict[str, Any]], wing: int = PRIMARY_PIVOT_WING
) -> PrimaryPivots:
    """取最近两个已确认摆动高点与低点。

    bars 必须"最新在前"（与 MQL5 的 V310LoadClosedBars 一致）：
    索引 0 为最新已收线 M15，索引越大越旧。wing=20 表示需要左右各 20 根确认。
    MQL5 侧调用方忽略返回值，失败时结构体保持全零，这里保持一致。
    """
    out = PrimaryPivots()
    total = len(bars)
    if wing < 2 or total < 2 * wing + 3:
        return out
    high_count = 0
    low_count = 0
    for i in range(wing, total - wing):
        is_high = True
        is_low = True
        for offset in range(1, wing + 1):
            if (
                bars[i]["high"] <= bars[i - offset]["high"]
                or bars[i]["high"] <= bars[i + offset]["high"]
            ):
                is_high = False
            if (
                bars[i]["low"] >= bars[i - offset]["low"]
                or bars[i]["low"] >= bars[i + offset]["low"]
            ):
                is_low = False
            if not is_high and not is_low:
                break
        if is_high and high_count < 2:
            if high_count == 0:
                out.latest_high = float(bars[i]["high"])
                out.latest_high_time = int(bars[i]["time"])
            else:
                out.previous_high = float(bars[i]["high"])
                out.previous_high_time = int(bars[i]["time"])
            high_count += 1
        if is_low and low_count < 2:
            if low_count == 0:
                out.latest_low = float(bars[i]["low"])
                out.latest_low_time = int(bars[i]["time"])
            else:
                out.previous_low = float(bars[i]["low"])
                out.previous_low_time = int(bars[i]["time"])
            low_count += 1
        if high_count >= 2 and low_count >= 2:
            break
    return out
