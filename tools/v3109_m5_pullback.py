"""V3.10.9 C3.2-B1 —— M5 微回调状态机的 Python 移植（多头侧）。

来源：EA 源码 Include/XAUAI/V3109C32BParticipation/M5PullbackState.mqh

状态机由事件驱动：
    NEW_IMPULSE / PULLBACK_START / NEW_RECOVERY_ATTEMPT / CONTINUE_RECOVERY / RANGE_LIKE
以及每根已收线 M5 的 V310AdvanceFromClosedBar。

bars 为"最新在前"的已收线 M5，时间用整数秒。空头侧（M5BearPullbackState）为其镜像，
在下一步一并移植。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _bar_body(bar: dict[str, Any]) -> float:
    return abs(float(bar["close"]) - float(bar["open"]))


def _bar_range(bar: dict[str, Any]) -> float:
    return max(0.0, float(bar["high"]) - float(bar["low"]))


def body_range_ratio(bar: dict[str, Any]) -> float:
    rng = _bar_range(bar)
    return _bar_body(bar) / rng if rng > 0.0 else 0.0


def close_location(bar: dict[str, Any]) -> float:
    rng = _bar_range(bar)
    return (float(bar["close"]) - float(bar["low"])) / rng if rng > 0.0 else 0.0


def confirm_bull_impulse(
    break_bar: dict[str, Any],
    follow_bar: dict[str, Any],
    pullback_start_high: float,
    atr: float,
    tick_size: float,
) -> bool:
    """对应 V310ConfirmBullImpulse。"""
    buffer = max(tick_size, 0.10 * atr)
    return (
        float(break_bar["close"]) >= pullback_start_high + buffer
        and body_range_ratio(break_bar) >= 0.60
        and close_location(break_bar) >= 0.75
        and float(follow_bar["close"]) >= pullback_start_high
    )


def is_pullback_start(
    previous: dict[str, Any], current: dict[str, Any], tick_size: float = 0.0
) -> bool:
    """对应 V310IsPullbackStart（多头：小段下行）。"""
    tick = max(0.0, tick_size)
    return (
        float(current["low"]) <= float(previous["low"]) - tick + 1e-12
        and float(current["close"]) <= float(previous["close"]) - tick + 1e-12
        and (tick > 0.0 or float(current["low"]) < float(previous["low"]))
        and (tick > 0.0 or float(current["close"]) < float(previous["close"]))
    )


@dataclass
class PullbackState:
    active: bool = False
    locked: bool = False
    pullback_id: str = ""
    pullback_start_time: int = 0
    pullback_start_high: float = 0.0
    attempt_count: int = 0
    h_state: str = "NONE"
    h2_signal_time: int = 0
    h2_candidate_time: int = 0
    current_attempt_high: float = 0.0
    impulse_confirmed_time: int = 0
    h3_reached: bool = False
    range_like: bool = False
    attempt_active: bool = False
    down_leg_active: bool = False
    awaiting_down_leg: bool = False
    reason_code: str = "PULLBACK_IDLE"


def init_pullback_state(state: PullbackState) -> None:
    fresh = PullbackState()
    for name, value in vars(fresh).items():
        setattr(state, name, value)
    state.h_state = "NONE"
    state.reason_code = "PULLBACK_IDLE"


def advance_pullback_state(
    state: PullbackState,
    event_name: str,
    event_time: int,
    event_high: float = 0.0,
    signal_valid: bool = False,
) -> None:
    """对应 V310AdvancePullbackState。"""
    if event_name == "NEW_IMPULSE":
        init_pullback_state(state)
        state.impulse_confirmed_time = event_time
        state.pullback_id = f"IMP-{event_time}"
        state.reason_code = "NEW_BULL_IMPULSE_CONFIRMED"
        return

    if state.locked:
        return

    if event_name == "PULLBACK_START":
        state.active = True
        state.pullback_id = f"PB-{event_time}"
        state.pullback_start_time = event_time
        state.pullback_start_high = event_high
        state.attempt_count = 0
        state.h_state = "NONE"
        state.down_leg_active = True
        state.attempt_active = False
        state.awaiting_down_leg = False
        state.reason_code = "PULLBACK_STARTED"
        return

    if not state.active:
        return

    if event_name == "CONTINUE_RECOVERY":
        state.current_attempt_high = max(state.current_attempt_high, event_high)
        state.reason_code = "RECOVERY_ATTEMPT_CONTINUES"
        return

    if event_name == "NEW_RECOVERY_ATTEMPT":
        state.attempt_count += 1
        state.current_attempt_high = event_high
        state.attempt_active = True
        state.down_leg_active = False
        state.awaiting_down_leg = False
        if state.attempt_count == 1:
            state.h_state = "H1"
            state.reason_code = "H1_CONFIRMED"
            return
        if state.attempt_count == 2:
            state.h2_candidate_time = event_time
            state.h_state = "H2" if signal_valid else "H2_INVALID_SIGNAL"
            state.h2_signal_time = event_time if signal_valid else 0
            state.reason_code = "H2_SIGNAL_CONFIRMED" if signal_valid else "H2_SIGNAL_INVALID"
            return
        state.h_state = "H3"
        state.locked = True
        state.h3_reached = True
        state.reason_code = "PULLBACK_LOCK_H3"
        return

    if event_name == "RANGE_LIKE":
        state.locked = True
        state.range_like = True
        state.reason_code = "PULLBACK_LOCK_RANGE_LIKE"


def advance_from_closed_bar(
    state: PullbackState,
    previous: dict[str, Any],
    current: dict[str, Any],
    tick_size: float,
    signal_valid: bool = False,
) -> None:
    """对应 V310AdvanceFromClosedBar。"""
    if state.locked or not state.active:
        return
    tick = max(0.0, tick_size)
    higher_high = float(current["high"]) >= float(previous["high"]) + tick - 1e-12
    higher_close = float(current["close"]) >= float(previous["close"]) + tick - 1e-12

    if state.attempt_active:
        if higher_high:
            state.current_attempt_high = max(
                state.current_attempt_high, float(current["high"])
            )
            state.reason_code = "RECOVERY_ATTEMPT_CONTINUES"
            return
        next_down = is_pullback_start(previous, current, tick_size)
        state.attempt_active = False
        state.down_leg_active = next_down
        state.awaiting_down_leg = not next_down
        state.reason_code = (
            "NEXT_DOWN_LEG_STARTED" if next_down else "RECOVERY_ATTEMPT_STOPPED"
        )
        return

    if state.awaiting_down_leg:
        if not is_pullback_start(previous, current, tick_size):
            return
        state.down_leg_active = True
        state.awaiting_down_leg = False
        state.reason_code = "NEXT_DOWN_LEG_STARTED"
        return

    if state.down_leg_active and higher_high and higher_close:
        advance_pullback_state(
            state,
            "NEW_RECOVERY_ATTEMPT",
            int(current["time"]),
            float(current["high"]),
            signal_valid,
        )
