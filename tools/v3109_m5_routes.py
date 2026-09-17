"""V3.10.9 C3.2-B1 —— M5 信号 K 分类与路径选择（多头侧）。

来源：EA 源码 Include/XAUAI/V3109C32BParticipation/
      BullSignalBar.mqh（V310ClassifyBullSignal / V3103BullRecoveryBreak）
      M5RouteSelection.mqh（V3103SelectM5Route 及键值工具）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.v3109_m5_pullback import body_range_ratio, close_location

ROUTE_NONE = 0
ROUTE_H2_ORIGINAL = 1
ROUTE_H2_RECOVERY_BREAK = 2
ROUTE_EMA_RECOVERY = 3
ROUTE_EMA_COMPRESSION_BREAK = 4


def _body(bar: dict[str, Any]) -> float:
    return abs(float(bar["close"]) - float(bar["open"]))


def _range(bar: dict[str, Any]) -> float:
    return max(0.0, float(bar["high"]) - float(bar["low"]))


def _bullish(bar: dict[str, Any]) -> bool:
    return float(bar["close"]) > float(bar["open"])


def _bearish(bar: dict[str, Any]) -> bool:
    return float(bar["close"]) < float(bar["open"])


def bull_recovery_break(
    already_h2_candidate: bool,
    current: dict[str, Any],
    previous: dict[str, Any],
    tick_size: float,
) -> bool:
    """对应 V3103BullRecoveryBreak。"""
    if not already_h2_candidate or tick_size <= 0.0:
        return False
    rng = _range(current)
    eps = 1e-12
    if (
        float(current["close"]) <= float(current["open"])
        or rng <= 0.0
        or _body(current) + eps < tick_size
    ):
        return False
    return (
        float(current["close"]) + eps >= float(previous["high"]) + tick_size
        and close_location(current) + eps >= 0.75
    )


@dataclass
class SignalResult:
    valid: bool = False
    signal_type: str = ""
    pin: bool = False
    strong_bull: bool = False
    three_bear_engulf: bool = False
    location_fib236: bool = False
    location_fib382: bool = False
    location_ema20: bool = False
    location_breakout_retest: bool = False
    m2b: bool = False
    reason_code: str = ""


def classify_bull_signal(
    signal: dict[str, Any], previous: list[dict[str, Any]]
) -> SignalResult:
    """对应 V310ClassifyBullSignal；previous 为紧邻的已收线 M5（最新在前）。"""
    out = SignalResult()
    body = _body(signal)
    rng = _range(signal)
    lower = min(float(signal["open"]), float(signal["close"])) - float(signal["low"])
    upper = float(signal["high"]) - max(float(signal["open"]), float(signal["close"]))
    top_third = rng > 0.0 and min(float(signal["open"]), float(signal["close"])) >= (
        float(signal["low"]) + rng * (2.0 / 3.0)
    )
    out.pin = (
        _bullish(signal)
        and body > 0.0
        and lower >= 3.0 * body
        and top_third
        and upper <= 0.25 * body
    )
    out.strong_bull = (
        _bullish(signal)
        and body_range_ratio(signal) + 1e-12 >= 2.0 / 3.0
        and upper <= 0.5 * body
        and close_location(signal) >= 0.75
    )
    bears = len(previous) >= 3
    body_low = float("inf")
    body_high = float("-inf")
    for i in range(3):
        if not bears:
            break
        bears = _bearish(previous[i])
        body_low = min(body_low, min(float(previous[i]["open"]), float(previous[i]["close"])))
        body_high = max(body_high, max(float(previous[i]["open"]), float(previous[i]["close"])))
    out.three_bear_engulf = (
        _bullish(signal)
        and bears
        and float(signal["open"]) <= body_low
        and float(signal["close"]) >= body_high
        and body_range_ratio(signal) + 1e-12 >= 0.60
    )
    out.valid = out.pin or out.strong_bull or out.three_bear_engulf
    out.reason_code = "SIGNAL_VALID" if out.valid else "SIGNAL_INVALID"
    return out


def select_m5_route(
    original_h2: bool, recovery_break: bool, ema_recovery: bool, compression_break: bool
) -> int:
    """对应 V3103SelectM5Route（按优先级短路）。"""
    if original_h2:
        return ROUTE_H2_ORIGINAL
    if recovery_break:
        return ROUTE_H2_RECOVERY_BREAK
    if ema_recovery:
        return ROUTE_EMA_RECOVERY
    if compression_break:
        return ROUTE_EMA_COMPRESSION_BREAK
    return ROUTE_NONE


def setup_key(direction: int, pullback_start_time: int) -> str:
    if pullback_start_time <= 0 or direction not in (0, 1):
        return ""
    return ("BUY|" if direction == 0 else "SELL|") + str(int(pullback_start_time))


def signal_key(direction: int, signal_bar_time: int) -> str:
    if signal_bar_time <= 0 or direction not in (0, 1):
        return ""
    return ("BUY|" if direction == 0 else "SELL|") + str(int(signal_bar_time))


def can_emit_setup_candidate(key: str, emitted_setup_key: str) -> bool:
    return len(key) > 0 and key != emitted_setup_key


def can_emit_signal_candidate(key: str, emitted_signal_key: str) -> bool:
    return len(key) > 0 and key != emitted_signal_key


def may_form_new_candidate(
    own_pending_or_position: bool, key: str, emitted_signal_key: str
) -> bool:
    return (not own_pending_or_position) and can_emit_signal_candidate(key, emitted_signal_key)


def independent_ema_route_may_run(
    legacy_pullback_active: bool, legacy_pullback_locked: bool
) -> bool:
    return (not legacy_pullback_active) or legacy_pullback_locked
