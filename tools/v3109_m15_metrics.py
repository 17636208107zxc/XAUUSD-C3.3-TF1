"""V3.10.9 C3.2-B1 —— M15 指标计算层的 Python 移植。

来源：EA 源码 Include/XAUAI/V3109C32BParticipation/
      M15BullContext.mqh、M15BearContext.mqh、M5PullbackState.mqh(V310EvaluateRangeLike)。

bars 一律为"最新在前"的已收线 K 线数组（索引 0 = 最近一根已收线），
与 MQL5 的 V310LoadClosedBars（ArraySetAsSeries + start=1）一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.v3109_m15_primary import (
    PrimaryInput,
    PrimaryPivots,
    PRIMARY_PIVOT_WING,
)

M15_LOOKBACK = 100
PRIMARY_LOOKBACK = 300
CLOSED_SHIFT = 1

V310_PHASE_INVALID = 0
V310_PHASE_EARLY = 1
V310_PHASE_MATURE = 2


def _bar_body(bar: dict[str, Any]) -> float:
    return abs(float(bar["close"]) - float(bar["open"]))


def _bar_range(bar: dict[str, Any]) -> float:
    return max(0.0, float(bar["high"]) - float(bar["low"]))


def _body_range_ratio(bar: dict[str, Any]) -> float:
    rng = _bar_range(bar)
    return _bar_body(bar) / rng if rng > 0.0 else 0.0


def _close_location(bar: dict[str, Any]) -> float:
    rng = _bar_range(bar)
    return (float(bar["close"]) - float(bar["low"])) / rng if rng > 0.0 else 0.0


def atr_from_newest(bars: list[dict[str, Any]], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    total = 0.0
    for i in range(period - 1, -1, -1):
        total += max(
            float(bars[i]["high"]) - float(bars[i]["low"]),
            max(
                abs(float(bars[i]["high"]) - float(bars[i + 1]["close"])),
                abs(float(bars[i]["low"]) - float(bars[i + 1]["close"])),
            ),
        )
    return total / period


@dataclass
class RangeLikeResult:
    is_range_like: bool = False
    overlap_ratio: float = 0.0
    overlap_share: float = 0.0
    net_efficiency: float = 0.0
    reason_code: str = ""


def evaluate_range_like(bars: list[dict[str, Any]], min_bars: int) -> RangeLikeResult:
    out = RangeLikeResult()
    if len(bars) < min_bars:
        out.reason_code = "RANGE_INSUFFICIENT_BARS"
        return out
    pairs = len(bars) - 1
    overlap_pass = 0
    overlap_sum = 0.0
    path = 0.0
    for i in range(pairs):
        overlap = max(
            0.0,
            min(float(bars[i]["high"]), float(bars[i + 1]["high"]))
            - max(float(bars[i]["low"]), float(bars[i + 1]["low"])),
        )
        denom = min(_bar_range(bars[i]), _bar_range(bars[i + 1]))
        ratio = overlap / denom if denom > 0.0 else 0.0
        overlap_sum += ratio
        if ratio >= 0.60:
            overlap_pass += 1
        path += abs(float(bars[i + 1]["close"]) - float(bars[i]["close"]))
    out.overlap_ratio = overlap_sum / pairs if pairs > 0 else 0.0
    out.overlap_share = overlap_pass / pairs if pairs > 0 else 0.0
    net = abs(float(bars[len(bars) - 1]["close"]) - float(bars[0]["close"]))
    out.net_efficiency = net / path if path > 0.0 else 0.0
    out.is_range_like = out.overlap_share >= 0.60 and out.net_efficiency <= 0.35
    out.reason_code = "RANGE_LIKE" if out.is_range_like else "RANGE_DIRECTIONAL"
    return out


def _confirmed_pivots(
    bars: list[dict[str, Any]],
) -> tuple[list[int], list[int]]:
    """与 MQL5 一致：i 从 2 到 97，左右各两根确认。"""
    high_idx: list[int] = []
    low_idx: list[int] = []
    for i in range(2, 98):
        if (
            float(bars[i]["high"]) > float(bars[i - 1]["high"])
            and float(bars[i]["high"]) > float(bars[i - 2]["high"])
            and float(bars[i]["high"]) > float(bars[i + 1]["high"])
            and float(bars[i]["high"]) > float(bars[i + 2]["high"])
        ):
            high_idx.append(i)
        if (
            float(bars[i]["low"]) < float(bars[i - 1]["low"])
            and float(bars[i]["low"]) < float(bars[i - 2]["low"])
            and float(bars[i]["low"]) < float(bars[i + 1]["low"])
            and float(bars[i]["low"]) < float(bars[i + 2]["low"])
        ):
            low_idx.append(i)
    return high_idx, low_idx


def _ema100(bars: list[dict[str, Any]]) -> list[float]:
    alpha = 2.0 / 21.0
    ema = [0.0] * 100
    ema[99] = float(bars[99]["close"])
    for i in range(98, -1, -1):
        ema[i] = alpha * float(bars[i]["close"]) + (1.0 - alpha) * ema[i + 1]
    return ema


def _common_tail(
    bars: list[dict[str, Any]],
    ema: list[float],
    atr: float,
    high_idx: list[int],
    low_idx: list[int],
    broken: int,
    break_level: float,
    origin: int,
    net: float,
    path: float,
) -> dict[str, float]:
    bodies = sorted(_bar_body(bars[i]) for i in range(2, 22))
    median_body = (bodies[9] + bodies[10]) * 0.5
    midpoint = (float(bars[2]["high"]) + float(bars[2]["low"])) * 0.5
    return {
        "move_atr": net / atr if atr > 0.0 else 0.0,
        "slope_atr": net / (max(1, origin) * atr) if atr > 0.0 else 0.0,
        "direction_efficiency": net / path if path > 0.0 else 0.0,
        "median_body": median_body,
        "climax": (
            atr > 0.0 and _bar_body(bars[2]) / atr >= 1.50
        )
        or (median_body > 0.0 and _bar_body(bars[2]) >= 1.80 * median_body),
        "midpoint": midpoint,
    }


@dataclass
class M15Metrics:
    """多头侧指标；空头侧字段同名镜像。"""

    bull_context: bool = False
    protected_swing_low_intact: bool = False
    trend_phase: int = V310_PHASE_INVALID
    valid_bull_breakout: bool = False
    broken_level_count: int = 0
    breakout_follow_through: bool = False
    move_atr: float = 0.0
    slope_atr: float = 0.0
    direction_efficiency: float = 0.0
    breakout_body_atr: float = 0.0
    breakout_body_range_ratio: float = 0.0
    breakout_close_location: float = 0.0
    break_distance_atr: float = 0.0
    bull_structure: bool = False
    bull_higher_low: bool = False
    bull_higher_high: bool = False
    ema_slope_atr: float = 0.0
    same_side_close_ratio: float = 0.0
    opposite_close_count: int = 999
    ema_cross_count: int = 999
    failed_breakout_m15: bool = False
    protected_swing_low_broken_m15: bool = False
    m15_range_like: bool = False
    climax_candidate_m15: bool = False
    no_follow_through_m15: bool = False
    closed_bar_time: int = 0
    closed_bar_close: float = 0.0
    ema20: float = 0.0
    atr14: float = 0.0
    latest_confirmed_high: float = 0.0
    previous_confirmed_high: float = 0.0
    latest_confirmed_low: float = 0.0
    previous_confirmed_low: float = 0.0
    latest_confirmed_high_time: int = 0
    previous_confirmed_high_time: int = 0
    latest_confirmed_low_time: int = 0
    previous_confirmed_low_time: int = 0
    protected_level: float = 0.0
    protected_break_buffer: float = 0.0


@dataclass
class M15BearMetrics:
    bear_context: bool = False
    protected_swing_high_intact: bool = False
    trend_phase: int = V310_PHASE_INVALID
    valid_bear_breakdown: bool = False
    broken_level_count: int = 0
    breakdown_follow_through: bool = False
    move_atr: float = 0.0
    slope_atr: float = 0.0
    direction_efficiency: float = 0.0
    breakdown_body_atr: float = 0.0
    breakdown_body_range_ratio: float = 0.0
    breakdown_close_location: float = 0.0
    break_distance_atr: float = 0.0
    bear_structure: bool = False
    bear_lower_low: bool = False
    bear_lower_high: bool = False
    ema_slope_atr: float = 0.0
    same_side_close_ratio: float = 0.0
    opposite_close_count: int = 999
    ema_cross_count: int = 999
    failed_breakdown_m15: bool = False
    protected_swing_high_broken_m15: bool = False
    m15_range_like: bool = False
    climax_candidate_m15: bool = False
    no_follow_through_m15: bool = False
    closed_bar_time: int = 0
    closed_bar_close: float = 0.0
    ema20: float = 0.0
    atr14: float = 0.0
    latest_confirmed_high: float = 0.0
    previous_confirmed_high: float = 0.0
    latest_confirmed_low: float = 0.0
    previous_confirmed_low: float = 0.0
    latest_confirmed_high_time: int = 0
    previous_confirmed_high_time: int = 0
    latest_confirmed_low_time: int = 0
    previous_confirmed_low_time: int = 0
    protected_level: float = 0.0
    protected_break_buffer: float = 0.0


def derive_m15_metrics(
    bars: list[dict[str, Any]],
) -> tuple[M15Metrics | None, float, float, float]:
    """对应 V310DeriveM15Metrics。bars 为 100 根已收线 M15（最新在前）。"""
    m = M15Metrics()
    impulse_low = 0.0
    impulse_high = 0.0
    nearest_resistance = 0.0
    if len(bars) < 100:
        return None, impulse_low, impulse_high, nearest_resistance
    ema = _ema100(bars)
    atr = atr_from_newest(bars, 14)
    high_idx, low_idx = _confirmed_pivots(bars)

    m.bull_higher_low = (
        len(low_idx) >= 2 and float(bars[low_idx[0]]["low"]) > float(bars[low_idx[1]]["low"])
    )
    m.bull_higher_high = (
        len(high_idx) >= 2
        and float(bars[high_idx[0]]["high"]) > float(bars[high_idx[1]]["high"])
    )
    m.bull_structure = m.bull_higher_low and m.bull_higher_high
    impulse_low = float(bars[low_idx[0]]["low"]) if low_idx else float(bars[19]["low"])
    for i in range(20):
        impulse_low = min(impulse_low, float(bars[i]["low"]))
    impulse_high = float(bars[0]["high"])
    for i in range(20):
        impulse_high = max(impulse_high, float(bars[i]["high"]))
    protected_low = float(bars[low_idx[0]]["low"]) if low_idx else impulse_low

    m.closed_bar_time = int(bars[0]["time"])
    m.closed_bar_close = float(bars[0]["close"])
    m.ema20 = ema[0]
    m.atr14 = atr
    m.latest_confirmed_high = float(bars[high_idx[0]]["high"]) if high_idx else 0.0
    m.previous_confirmed_high = float(bars[high_idx[1]]["high"]) if len(high_idx) > 1 else 0.0
    m.latest_confirmed_low = float(bars[low_idx[0]]["low"]) if low_idx else 0.0
    m.previous_confirmed_low = float(bars[low_idx[1]]["low"]) if len(low_idx) > 1 else 0.0
    m.latest_confirmed_high_time = int(bars[high_idx[0]]["time"]) if high_idx else 0
    m.previous_confirmed_high_time = int(bars[high_idx[1]]["time"]) if len(high_idx) > 1 else 0
    m.latest_confirmed_low_time = int(bars[low_idx[0]]["time"]) if low_idx else 0
    m.previous_confirmed_low_time = int(bars[low_idx[1]]["time"]) if len(low_idx) > 1 else 0
    m.protected_level = protected_low
    m.protected_break_buffer = 0.15 * atr
    if atr <= 0.0 or len(bars) < 2:
        m.protected_swing_low_broken_m15 = False
    else:
        buffered = protected_low - 0.15 * atr
        m.protected_swing_low_broken_m15 = (
            float(bars[0]["close"]) < buffered and float(bars[1]["close"]) < buffered
        )
    m.protected_swing_low_intact = not m.protected_swing_low_broken_m15
    m.bull_context = atr > 0.0 and float(bars[0]["close"]) > ema[0]
    m.trend_phase = V310_PHASE_MATURE if m.bull_structure else V310_PHASE_EARLY
    same = 0
    opposite = 0
    for i in range(20):
        if float(bars[i]["close"]) > ema[i]:
            same += 1
        elif float(bars[i]["close"]) < ema[i]:
            opposite += 1
    crosses = 0
    for i in range(19):
        if (float(bars[i]["close"]) - ema[i]) * (float(bars[i + 1]["close"]) - ema[i + 1]) < 0.0:
            crosses += 1
    m.same_side_close_ratio = same / 20.0
    m.opposite_close_count = opposite
    m.ema_cross_count = crosses
    m.ema_slope_atr = (ema[0] - ema[10]) / (10.0 * atr) if atr > 0.0 else 0.0

    m.m15_range_like = evaluate_range_like(bars[0:20], 20).is_range_like

    broken = 0
    break_level = 0.0
    for i in high_idx:
        level = float(bars[i]["high"])
        if float(bars[0]["close"]) > level:
            clustered = break_level > 0.0 and atr > 0.0 and abs(level - break_level) <= 0.15 * atr
            if not clustered:
                broken += 1
                break_level = max(break_level, level)
        if level > float(bars[0]["close"]) and (nearest_resistance <= 0.0 or level < nearest_resistance):
            nearest_resistance = level
    m.broken_level_count = broken
    m.valid_bull_breakout = (
        broken >= 2
        and break_level > 0.0
        and float(bars[1]["close"]) > break_level
        and float(bars[2]["close"]) <= break_level
    )
    m.breakout_follow_through = m.valid_bull_breakout and float(bars[0]["close"]) > break_level
    origin = low_idx[0] if low_idx else 19
    net = float(bars[0]["close"]) - float(bars[origin]["low"])
    path = 0.0
    for i in range(origin - 1, -1, -1):
        path += abs(float(bars[i]["close"]) - float(bars[i + 1]["close"]))
    tail = _common_tail(bars, ema, atr, high_idx, low_idx, broken, break_level, origin, net, path)
    m.move_atr = tail["move_atr"]
    m.slope_atr = tail["slope_atr"]
    m.direction_efficiency = tail["direction_efficiency"]
    m.breakout_body_atr = _bar_body(bars[1]) / atr if atr > 0.0 else 0.0
    m.breakout_body_range_ratio = _body_range_ratio(bars[1])
    m.breakout_close_location = _close_location(bars[1])
    m.break_distance_atr = (
        (float(bars[1]["close"]) - break_level) / atr
        if m.valid_bull_breakout and atr > 0.0
        else 0.0
    )
    m.failed_breakout_m15 = (
        break_level > 0.0
        and float(bars[2]["close"]) > break_level
        and float(bars[0]["close"]) < break_level
    )
    m.climax_candidate_m15 = bool(tail["climax"])
    midpoint = tail["midpoint"]
    m.no_follow_through_m15 = (
        m.climax_candidate_m15
        and float(bars[1]["close"]) <= float(bars[2]["high"])
        and float(bars[0]["close"]) <= float(bars[2]["high"])
        and (float(bars[1]["close"]) < midpoint or float(bars[0]["close"]) < midpoint)
    )
    return m, impulse_low, impulse_high, nearest_resistance


def derive_m15_bear_metrics(
    bars: list[dict[str, Any]],
) -> tuple[M15BearMetrics | None, float, float, float]:
    """对应 V3101DeriveM15BearMetrics（多头侧的镜像）。"""
    m = M15BearMetrics()
    impulse_low = 0.0
    impulse_high = 0.0
    nearest_support = 0.0
    if len(bars) < 100:
        return None, impulse_low, impulse_high, nearest_support
    ema = _ema100(bars)
    atr = atr_from_newest(bars, 14)
    high_idx, low_idx = _confirmed_pivots(bars)

    m.bear_lower_low = (
        len(low_idx) >= 2 and float(bars[low_idx[0]]["low"]) < float(bars[low_idx[1]]["low"])
    )
    m.bear_lower_high = (
        len(high_idx) >= 2
        and float(bars[high_idx[0]]["high"]) < float(bars[high_idx[1]]["high"])
    )
    m.bear_structure = m.bear_lower_low and m.bear_lower_high
    impulse_high = float(bars[high_idx[0]]["high"]) if high_idx else float(bars[19]["high"])
    impulse_low = float(bars[0]["low"])
    for i in range(20):
        impulse_high = max(impulse_high, float(bars[i]["high"]))
        impulse_low = min(impulse_low, float(bars[i]["low"]))
    protected_high = float(bars[high_idx[0]]["high"]) if high_idx else impulse_high

    m.closed_bar_time = int(bars[0]["time"])
    m.closed_bar_close = float(bars[0]["close"])
    m.ema20 = ema[0]
    m.atr14 = atr
    m.latest_confirmed_high = float(bars[high_idx[0]]["high"]) if high_idx else 0.0
    m.previous_confirmed_high = float(bars[high_idx[1]]["high"]) if len(high_idx) > 1 else 0.0
    m.latest_confirmed_low = float(bars[low_idx[0]]["low"]) if low_idx else 0.0
    m.previous_confirmed_low = float(bars[low_idx[1]]["low"]) if len(low_idx) > 1 else 0.0
    m.latest_confirmed_high_time = int(bars[high_idx[0]]["time"]) if high_idx else 0
    m.previous_confirmed_high_time = int(bars[high_idx[1]]["time"]) if len(high_idx) > 1 else 0
    m.latest_confirmed_low_time = int(bars[low_idx[0]]["time"]) if low_idx else 0
    m.previous_confirmed_low_time = int(bars[low_idx[1]]["time"]) if len(low_idx) > 1 else 0
    m.protected_level = protected_high
    m.protected_break_buffer = 0.15 * atr
    if atr <= 0.0 or len(bars) < 2:
        m.protected_swing_high_broken_m15 = False
    else:
        buffered = protected_high + 0.15 * atr
        m.protected_swing_high_broken_m15 = (
            float(bars[0]["close"]) > buffered and float(bars[1]["close"]) > buffered
        )
    m.protected_swing_high_intact = not m.protected_swing_high_broken_m15
    m.bear_context = atr > 0.0 and float(bars[0]["close"]) < ema[0]
    m.trend_phase = V310_PHASE_MATURE if m.bear_structure else V310_PHASE_EARLY
    same = 0
    opposite = 0
    for i in range(20):
        if float(bars[i]["close"]) < ema[i]:
            same += 1
        elif float(bars[i]["close"]) > ema[i]:
            opposite += 1
    crosses = 0
    for i in range(19):
        if (float(bars[i]["close"]) - ema[i]) * (float(bars[i + 1]["close"]) - ema[i + 1]) < 0.0:
            crosses += 1
    m.same_side_close_ratio = same / 20.0
    m.opposite_close_count = opposite
    m.ema_cross_count = crosses
    m.ema_slope_atr = (ema[10] - ema[0]) / (10.0 * atr) if atr > 0.0 else 0.0

    m.m15_range_like = evaluate_range_like(bars[0:20], 20).is_range_like

    broken = 0
    break_level = 0.0
    for i in low_idx:
        level = float(bars[i]["low"])
        if float(bars[0]["close"]) < level:
            clustered = break_level > 0.0 and atr > 0.0 and abs(level - break_level) <= 0.15 * atr
            if not clustered:
                broken += 1
                break_level = level if break_level <= 0.0 else min(break_level, level)
        if level < float(bars[0]["close"]) and (nearest_support <= 0.0 or level > nearest_support):
            nearest_support = level
    m.broken_level_count = broken
    m.valid_bear_breakdown = (
        broken >= 2
        and break_level > 0.0
        and float(bars[1]["close"]) < break_level
        and float(bars[2]["close"]) >= break_level
    )
    m.breakdown_follow_through = m.valid_bear_breakdown and float(bars[0]["close"]) < break_level
    origin = high_idx[0] if high_idx else 19
    net = float(bars[origin]["high"]) - float(bars[0]["close"])
    path = 0.0
    for i in range(origin - 1, -1, -1):
        path += abs(float(bars[i]["close"]) - float(bars[i + 1]["close"]))
    tail = _common_tail(bars, ema, atr, high_idx, low_idx, broken, break_level, origin, net, path)
    m.move_atr = tail["move_atr"]
    m.slope_atr = tail["slope_atr"]
    m.direction_efficiency = tail["direction_efficiency"]
    m.breakdown_body_atr = _bar_body(bars[1]) / atr if atr > 0.0 else 0.0
    m.breakdown_body_range_ratio = _body_range_ratio(bars[1])
    m.breakdown_close_location = 1.0 - _close_location(bars[1])
    m.break_distance_atr = (
        (break_level - float(bars[1]["close"])) / atr
        if m.valid_bear_breakdown and atr > 0.0
        else 0.0
    )
    m.failed_breakdown_m15 = (
        break_level > 0.0
        and float(bars[2]["close"]) < break_level
        and float(bars[0]["close"]) > break_level
    )
    m.climax_candidate_m15 = bool(tail["climax"])
    midpoint = tail["midpoint"]
    m.no_follow_through_m15 = (
        m.climax_candidate_m15
        and float(bars[1]["close"]) >= float(bars[2]["low"])
        and float(bars[0]["close"]) >= float(bars[2]["low"])
        and (float(bars[1]["close"]) > midpoint or float(bars[0]["close"]) > midpoint)
    )
    return m, impulse_low, impulse_high, nearest_support


def _ema_slope_of(metrics: Any) -> float:
    return float(getattr(metrics, "ema_slope_atr", 0.0))


def bull_is_late(m: M15Metrics) -> bool:
    climax_no_follow = m.climax_candidate_m15 and m.no_follow_through_m15
    return climax_no_follow or (m.move_atr >= 1.50 and m.opposite_close_count <= 2)


def bear_is_late(m: M15BearMetrics) -> bool:
    climax_no_follow = m.climax_candidate_m15 and m.no_follow_through_m15
    return climax_no_follow or (m.move_atr >= 1.50 and m.opposite_close_count <= 2)


def build_primary_input(
    bull: M15Metrics,
    bear: M15BearMetrics,
    pivots: PrimaryPivots,
    closed_bar_time: int,
    spread_price: float,
    tick_size: float,
) -> PrimaryInput:
    """对应 V3109C21BuildPrimaryInput。"""
    return PrimaryInput(
        closed_bar_time=int(closed_bar_time),
        close_price=bull.closed_bar_close,
        ema20=bull.ema20,
        ema_slope_atr=bull.ema_slope_atr,
        atr14=bull.atr14,
        spread_price=spread_price,
        tick_size=tick_size,
        latest_high=pivots.latest_high,
        previous_high=pivots.previous_high,
        latest_low=pivots.latest_low,
        previous_low=pivots.previous_low,
        latest_high_time=pivots.latest_high_time,
        previous_high_time=pivots.previous_high_time,
        latest_low_time=pivots.latest_low_time,
        previous_low_time=pivots.previous_low_time,
        bull_late=bull_is_late(bull),
        bear_late=bear_is_late(bear),
    )
