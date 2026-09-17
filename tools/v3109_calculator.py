"""V3.10.9 C3.2-B1 independent calculator for the parallel AI (Trader B).

Sources of truth (EA include tree ``V3109C32BParticipation``):

    M15TrendState / M15PrimaryStructure / M15PrimaryEvidence  -> tools/v3109_m15_primary.py
    M15BullContext / M15BearContext / M15*Metrics             -> tools/v3109_m15_metrics.py
    M5PullbackState / M5BearPullbackState                     -> tools/v3109_m5_pullback.py
    BullSignalBar / BearSignalBar / M5RouteSelection          -> tools/v3109_m5_routes.py
    M5EMARecovery / M5CompressionBreak                        -> this module
    M5TargetStructure / M5StructureStop                       -> this module
    Strategy01Planner / Strategy01SellPlanner                 -> this module
    V310UpdateM5State / V3101UpdateM5SellState                -> this module

Scope note (deliberate and documented):

* The per-bar decision path is reproduced one for one: impulse -> pullback ->
  H1/H2/H3 state machine, the four entry routes, the structure-stop anchor,
  the nearest valid structure target, and every plan rejection reason.
* The EA keeps long-lived private state (its own order/slot book, and the M15
  primary machine that is reset by every EA reload).  The parallel AI must not
  read EA private state, so this calculator always re-derives everything from
  published market data.  It is therefore an *independent* read of the same
  rule set, exactly like the V3.9.20 parallel AI, and the difference against
  the EA trace is reported rather than hidden.
* Counter-trend participation (V3109C3TryCounterBuy/Sell) depends on the EA's
  live fill quota and is not part of the AI floor; the gates below never grant
  permission that the aligned-trend rules would refuse.

Nothing here can place an order, and nothing here mutates an AI decision: the
module only produces facts, gates and a hidden reference plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import ceil, floor
import time
from typing import Any

from tools.v3109_m15_metrics import (
    atr_from_newest,
    build_primary_input,
    derive_m15_bear_metrics,
    derive_m15_metrics,
    evaluate_range_like,
)
from tools.v3109_m15_primary import (
    PRIMARY_BEAR,
    PRIMARY_BULL,
    PRIMARY_TRANSITION_FROM_BEAR,
    PRIMARY_TRANSITION_FROM_BULL,
    TrendEngine,
    derive_primary_pivots,
    local_phase_label,
    primary_label,
)
from tools.v3109_m5_pullback import (
    PullbackState,
    advance_from_closed_bar,
    advance_pullback_state,
    body_range_ratio,
    close_location,
    confirm_bull_impulse,
    init_pullback_state,
    is_pullback_start,
)
from tools.v3109_m5_routes import (
    ROUTE_EMA_COMPRESSION_BREAK,
    ROUTE_EMA_RECOVERY,
    ROUTE_H2_ORIGINAL,
    ROUTE_H2_RECOVERY_BREAK,
    ROUTE_NONE,
    bull_recovery_break,
    classify_bull_signal,
    select_m5_route,
)

DIR_BUY = 0
DIR_SELL = 1
DIR_NONE = -1

EMA_CYCLE_BARS = 20
EMA_RECOVERY_MIN_CLOSE_LOCATION = 0.65

# The EA's own hard plan floor: V310BuildTradePlan rejects any target closer
# than 2R.  G09 enforces that same 2R instead of the generic 0.8R used by the
# older rule versions.
V3109_TARGET_SPACE_R = 2.0

M5_SECONDS = 300
M15_SECONDS = 900

# EA 每根收盘 M5 取值时固定加载 60 根已收线 M5（V310LoadClosedBars(...,60,...)）。
# EMA/ATR/位置/目标扫描都只在这 60 根里取，所以每根 K 线的评估也必须用同样长度，
# 否则 EMA 的种子点不同、算出来的位置与结构目标都和 EA 不一致。
M5_EVAL_BARS = 60

# 预热重放窗口（M5 根数）：1500 根 ≈ 5 个交易日。
# 微周期（冲动→回调→H1/H2/H3）是跨 K 线记忆，只从 200 根载荷开始重放会和 EA 的
# 真实状态脱节；用更长的历史把状态机"养"到当天，决策窗口仍然只用 EA 的 200 根。
M5_WARMUP_BARS = 1500

GATE_NAME_CN = {
    "G01": "交易环境",
    "G02": "本AI自身敞口",
    "G03": "趋势结构",
    "G04": "推动幅度",
    "G05": "回调上下文",
    "G06": "入场路径",
    "G07": "信号K长度",
    "G08": "止损距离",
    "G09": "结构目标空间",
    "G10": "点差",
}

ROUTE_LABEL_CN = {
    "FIB_PA": "Fib + PA 路径",
    "EMA_H23": "EMA 回调恢复路径（多头）",
    "EMA_L23": "EMA 反弹恢复路径（空头）",
    "EMA_COMPRESSION": "EMA 压缩突破路径",
    "NONE": "无有效入场路径",
}


def gate_cn(gate_id: str) -> str:
    gid = str(gate_id or "").strip().upper()
    name = GATE_NAME_CN.get(gid, "")
    return f"{name}（{gid}）" if name else gid


def _directional_move_atr(m15: dict[str, Any], direction: Any) -> tuple[float, str]:
    """按方向取用 M15 推动幅度：做多用多头口径，做空用空头口径。

    两边的 "move_atr" 都是"顺着该方向的净位移 / ATR"，方向不明时退回多头口径
    （与 EA 的多头优先一致），但会把口径标签一起带出来，便于核对。
    """
    bull_move = float(m15.get("move_atr") or 0.0)
    bear_move = float(m15.get("move_atr_sell") or 0.0)
    if str(direction or "").strip().upper() == "SELL":
        return bear_move, "空头方向M15"
    if str(direction or "").strip().upper() == "BUY":
        return bull_move, "多头方向M15"
    # 方向未定：取两边里更强的一侧，避免用错口径误判
    if bear_move > bull_move:
        return bear_move, "空头方向M15"
    return bull_move, "多头方向M15"


def route_label_cn(route: str) -> str:
    return ROUTE_LABEL_CN.get(str(route or "NONE").upper(), str(route or "NONE"))


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def _range(bar: dict[str, Any]) -> float:
    return max(0.0, float(bar["high"]) - float(bar["low"]))


def _body(bar: dict[str, Any]) -> float:
    return abs(float(bar["close"]) - float(bar["open"]))


def _bullish(bar: dict[str, Any]) -> bool:
    return float(bar["close"]) > float(bar["open"])


def _bearish(bar: dict[str, Any]) -> bool:
    return float(bar["close"]) < float(bar["open"])


def bar_time(bar: dict[str, Any]) -> int:
    value = bar.get("time")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip().replace(".", "-")
    if not text:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


def with_int_time(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 "YYYY.MM.DD HH:MM:SS" 时间统一转成 epoch 秒。

    状态机与 M15 指标层沿用 EA 的 datetime（整数秒）；审计载荷里是字符串。
    """
    out: list[dict[str, Any]] = []
    for bar in bars:
        item = dict(bar)
        item["time"] = bar_time(bar)
        out.append(item)
    return out


def ema_from_newest(bars: list[dict[str, Any]], period: int) -> float:
    """对应 V310EMAFromNewest：bars 最新在前，从最旧一根收盘递归到最新。"""
    if len(bars) < period or period < 1:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    value = float(bars[-1]["close"])
    for index in range(len(bars) - 2, -1, -1):
        value = alpha * float(bars[index]["close"]) + (1.0 - alpha) * value
    return value


def recent_m5_ema20(bars: list[dict[str, Any]]) -> list[float] | None:
    """对应 V3103RecentM5EMA20：确认K前三根已收线各自的 EMA20。"""
    if len(bars) < 23:
        return None
    out: list[float] = []
    for i in range(3):
        value = ema_from_newest(bars[i + 1 :], 20)
        if value <= 0.0:
            return None
        out.append(value)
    return out


def m5_ema20_cycle(bars: list[dict[str, Any]]) -> list[float] | None:
    """对应 V3103M5EMA20Cycle：一个完整 EMA20 周期的逐根 EMA。"""
    if len(bars) < EMA_CYCLE_BARS + 20:
        return None
    out: list[float] = []
    for i in range(EMA_CYCLE_BARS):
        value = ema_from_newest(bars[i + 1 :], 20)
        if value <= 0.0:
            return None
        out.append(value)
    return out


def _step_digits(step: float) -> int:
    digits = 0
    scaled = float(step)
    while digits < 8 and abs(scaled - round(scaled)) > 1e-9:
        scaled *= 10.0
        digits += 1
    return digits


def align_up(price: float, tick: float) -> float:
    """对应 V310AlignUp。"""
    if tick <= 0.0:
        return 0.0
    return round(ceil(price / tick - 1e-10) * tick, _step_digits(tick))


def align_down(price: float, tick: float) -> float:
    """对应 V310AlignDown。"""
    if tick <= 0.0:
        return 0.0
    return round(floor(price / tick + 1e-10) * tick, _step_digits(tick))


# --------------------------------------------------------------------------
# M5EMARecovery.mqh
# --------------------------------------------------------------------------


def bull_ema_overall_direction(cycle_ema: list[float]) -> bool:
    if len(cycle_ema) < EMA_CYCLE_BARS:
        return False
    return cycle_ema[0] > cycle_ema[EMA_CYCLE_BARS - 1] + 1e-12


def bear_ema_overall_direction(cycle_ema: list[float]) -> bool:
    if len(cycle_ema) < EMA_CYCLE_BARS:
        return False
    return cycle_ema[0] < cycle_ema[EMA_CYCLE_BARS - 1] - 1e-12


def has_cycle_ema_touch(
    cycle_closed: list[dict[str, Any]], cycle_ema: list[float], tolerance: float
) -> bool:
    if len(cycle_closed) < EMA_CYCLE_BARS or len(cycle_ema) < EMA_CYCLE_BARS or tolerance < 0.0:
        return False
    for i in range(EMA_CYCLE_BARS):
        if (
            float(cycle_closed[i]["low"]) <= cycle_ema[i] + tolerance
            and float(cycle_closed[i]["high"]) >= cycle_ema[i] - tolerance
        ):
            return True
    return False


def has_cycle_bull_ema_pullback(
    cycle_closed: list[dict[str, Any]], cycle_ema: list[float], tolerance: float
) -> bool:
    if len(cycle_closed) < EMA_CYCLE_BARS or len(cycle_ema) < EMA_CYCLE_BARS or tolerance < 0.0:
        return False
    for i in range(EMA_CYCLE_BARS):
        if (
            float(cycle_closed[i]["close"]) < float(cycle_closed[i]["open"])
            and float(cycle_closed[i]["low"]) <= cycle_ema[i] + tolerance
        ):
            return True
    return False


def has_cycle_bear_ema_rally(
    cycle_closed: list[dict[str, Any]], cycle_ema: list[float], tolerance: float
) -> bool:
    if len(cycle_closed) < EMA_CYCLE_BARS or len(cycle_ema) < EMA_CYCLE_BARS or tolerance < 0.0:
        return False
    for i in range(EMA_CYCLE_BARS):
        if (
            float(cycle_closed[i]["close"]) > float(cycle_closed[i]["open"])
            and float(cycle_closed[i]["high"]) >= cycle_ema[i] - tolerance
        ):
            return True
    return False


def bull_ema_pullback_start(
    cycle_closed: list[dict[str, Any]], cycle_ema: list[float], tolerance: float
) -> int:
    count = min(len(cycle_closed), len(cycle_ema))
    if count <= 0 or tolerance < 0.0:
        return 0
    for i in range(count - 1, -1, -1):
        if (
            float(cycle_closed[i]["close"]) < float(cycle_closed[i]["open"])
            and float(cycle_closed[i]["low"]) <= cycle_ema[i] + tolerance
        ):
            return bar_time(cycle_closed[i])
    return 0


def bear_ema_rally_start(
    cycle_closed: list[dict[str, Any]], cycle_ema: list[float], tolerance: float
) -> int:
    count = min(len(cycle_closed), len(cycle_ema))
    if count <= 0 or tolerance < 0.0:
        return 0
    for i in range(count - 1, -1, -1):
        if (
            float(cycle_closed[i]["close"]) > float(cycle_closed[i]["open"])
            and float(cycle_closed[i]["high"]) >= cycle_ema[i] - tolerance
        ):
            return bar_time(cycle_closed[i])
    return 0


def recent_ema_interaction_start(
    recent_closed: list[dict[str, Any]], recent_ema: list[float], tolerance: float
) -> int:
    count = min(3, min(len(recent_closed), len(recent_ema)))
    if count <= 0 or tolerance < 0.0:
        return 0
    for i in range(count - 1, -1, -1):
        if (
            float(recent_closed[i]["low"]) <= recent_ema[i] + tolerance
            and float(recent_closed[i]["high"]) >= recent_ema[i] - tolerance
        ):
            return bar_time(recent_closed[i])
    return 0


def bull_directional_recovery(
    current: dict[str, Any],
    previous: dict[str, Any],
    recent_closed: list[dict[str, Any]],
    tick: float,
) -> bool:
    if tick <= 0.0 or len(recent_closed) <= 0 or float(current["close"]) <= float(previous["close"]):
        return False
    pullback_low_close = float(recent_closed[0]["close"])
    for i in range(1, min(len(recent_closed), 3)):
        pullback_low_close = min(pullback_low_close, float(recent_closed[i]["close"]))
    return float(current["close"]) + 1e-12 >= pullback_low_close + tick


def bear_directional_recovery(
    current: dict[str, Any],
    previous: dict[str, Any],
    recent_closed: list[dict[str, Any]],
    tick: float,
) -> bool:
    if tick <= 0.0 or len(recent_closed) <= 0 or float(current["close"]) >= float(previous["close"]):
        return False
    rally_high_close = float(recent_closed[0]["close"])
    for i in range(1, min(len(recent_closed), 3)):
        rally_high_close = max(rally_high_close, float(recent_closed[i]["close"]))
    return float(current["close"]) - 1e-12 <= rally_high_close - tick


def bull_ema_recovery(
    m15_allowed: bool,
    hard_exhaustion: bool,
    protected_structure_intact: bool,
    ema_rising: bool,
    real_pullback: bool,
    cycle_closed: list[dict[str, Any]],
    cycle_ema: list[float],
    current: dict[str, Any],
    previous: dict[str, Any],
    tolerance: float,
    tick: float,
) -> bool:
    eps = 1e-12
    rng = _range(current)
    if (
        not m15_allowed
        or hard_exhaustion
        or not protected_structure_intact
        or not ema_rising
        or not real_pullback
        or tick <= 0.0
        or rng <= 0.0
        or float(current["close"]) <= float(current["open"])
        or _body(current) + eps < tick
    ):
        return False
    previous_bar_break = float(current["close"]) + eps >= float(previous["high"]) + tick
    directional = bull_directional_recovery(current, previous, cycle_closed, tick)
    return (
        has_cycle_ema_touch(cycle_closed, cycle_ema, tolerance)
        and (previous_bar_break or directional)
        and close_location(current) + eps >= EMA_RECOVERY_MIN_CLOSE_LOCATION
    )


def bear_ema_recovery(
    m15_allowed: bool,
    hard_exhaustion: bool,
    protected_structure_intact: bool,
    ema_falling: bool,
    real_rally: bool,
    cycle_closed: list[dict[str, Any]],
    cycle_ema: list[float],
    current: dict[str, Any],
    previous: dict[str, Any],
    tolerance: float,
    tick: float,
) -> bool:
    eps = 1e-12
    rng = _range(current)
    if (
        not m15_allowed
        or hard_exhaustion
        or not protected_structure_intact
        or not ema_falling
        or not real_rally
        or tick <= 0.0
        or rng <= 0.0
        or float(current["close"]) >= float(current["open"])
        or _body(current) + eps < tick
    ):
        return False
    previous_bar_break = float(current["close"]) - eps <= float(previous["low"]) - tick
    directional = bear_directional_recovery(current, previous, cycle_closed, tick)
    return (
        has_cycle_ema_touch(cycle_closed, cycle_ema, tolerance)
        and (previous_bar_break or directional)
        and (float(current["high"]) - float(current["close"])) / rng + eps
        >= EMA_RECOVERY_MIN_CLOSE_LOCATION
    )


# --------------------------------------------------------------------------
# M5CompressionBreak.mqh
# --------------------------------------------------------------------------


def mostly_near_ema(
    local_closed: list[dict[str, Any]], local_ema: list[float], tolerance: float
) -> bool:
    if len(local_closed) < 3 or len(local_ema) < 3 or tolerance < 0.0:
        return False
    near = 0
    for i in range(3):
        if (
            float(local_closed[i]["low"]) <= local_ema[i] + tolerance
            and float(local_closed[i]["high"]) >= local_ema[i] - tolerance
        ):
            near += 1
    return near >= 2


def bull_compression_break(
    m15_allowed: bool,
    hard_exhaustion: bool,
    protected_structure_intact: bool,
    ema_rising: bool,
    range_like_state: bool,
    local_closed: list[dict[str, Any]],
    local_ema: list[float],
    current: dict[str, Any],
    tolerance: float,
    tick: float,
) -> bool:
    if (
        not m15_allowed
        or hard_exhaustion
        or not protected_structure_intact
        or not ema_rising
        or not range_like_state
        or tick <= 0.0
        or not mostly_near_ema(local_closed, local_ema, tolerance)
    ):
        return False
    rng = _range(current)
    eps = 1e-12
    if float(current["close"]) <= float(current["open"]) or rng <= 0.0 or _body(current) + eps < tick:
        return False
    local_high = max(float(local_closed[i]["high"]) for i in range(3))
    return float(current["close"]) + eps >= local_high + tick


def bear_compression_break(
    m15_allowed: bool,
    hard_exhaustion: bool,
    protected_structure_intact: bool,
    ema_falling: bool,
    range_like_state: bool,
    local_closed: list[dict[str, Any]],
    local_ema: list[float],
    current: dict[str, Any],
    tolerance: float,
    tick: float,
) -> bool:
    if (
        not m15_allowed
        or hard_exhaustion
        or not protected_structure_intact
        or not ema_falling
        or not range_like_state
        or tick <= 0.0
        or not mostly_near_ema(local_closed, local_ema, tolerance)
    ):
        return False
    rng = _range(current)
    eps = 1e-12
    if float(current["close"]) >= float(current["open"]) or rng <= 0.0 or _body(current) + eps < tick:
        return False
    local_low = min(float(local_closed[i]["low"]) for i in range(3))
    return float(current["close"]) - eps <= local_low - tick


# --------------------------------------------------------------------------
# M5BearPullbackState.mqh
# --------------------------------------------------------------------------


def confirm_bear_impulse(
    break_bar: dict[str, Any],
    follow_bar: dict[str, Any],
    pullback_start_low: float,
    atr: float,
    tick: float,
) -> bool:
    buffer = max(tick, 0.10 * atr)
    return (
        float(break_bar["close"]) <= pullback_start_low - buffer
        and body_range_ratio(break_bar) >= 0.60
        and close_location(break_bar) <= 0.25
        and float(follow_bar["close"]) <= pullback_start_low
    )


def is_rally_start(
    previous: dict[str, Any], current: dict[str, Any], tick_size: float = 0.0
) -> bool:
    tick = max(0.0, tick_size)
    return (
        float(current["high"]) >= float(previous["high"]) + tick - 1e-12
        and float(current["close"]) >= float(previous["close"]) + tick - 1e-12
        and (tick > 0.0 or float(current["high"]) > float(previous["high"]))
        and (tick > 0.0 or float(current["close"]) > float(previous["close"]))
    )


def bear_recovery_break(
    already_l2_candidate: bool, current: dict[str, Any], previous: dict[str, Any], tick: float
) -> bool:
    if not already_l2_candidate or tick <= 0.0:
        return False
    rng = _range(current)
    eps = 1e-12
    if float(current["close"]) >= float(current["open"]) or rng <= 0.0 or _body(current) + eps < tick:
        return False
    return (
        float(current["close"]) - eps <= float(previous["low"]) - tick
        and (float(current["high"]) - float(current["close"])) / rng + eps >= 0.75
    )


@dataclass
class BearPullbackState:
    active: bool = False
    locked: bool = False
    pullback_id: str = ""
    pullback_start_time: int = 0
    pullback_start_low: float = 0.0
    attempt_count: int = 0
    h_state: str = "NONE"
    h2_signal_time: int = 0
    h2_candidate_time: int = 0
    current_attempt_low: float = 0.0
    impulse_confirmed_time: int = 0
    h3_reached: bool = False
    range_like: bool = False
    attempt_active: bool = False
    up_leg_active: bool = False
    awaiting_up_leg: bool = False
    reason_code: str = "PULLBACK_IDLE"


@dataclass
class BearSignalResult:
    valid: bool = False
    signal_type: str = ""
    pin: bool = False
    strong_bear: bool = False
    three_bull_engulf: bool = False
    reason_code: str = ""


def init_bear_pullback_state(state: BearPullbackState) -> None:
    fresh = BearPullbackState()
    for name, value in vars(fresh).items():
        setattr(state, name, value)


def advance_bear_pullback_state(
    state: BearPullbackState,
    event_name: str,
    event_time: int,
    event_low: float = 0.0,
    signal_valid: bool = False,
) -> None:
    if event_name == "NEW_IMPULSE":
        init_bear_pullback_state(state)
        state.impulse_confirmed_time = event_time
        state.pullback_id = f"IMP-S-{event_time}"
        state.reason_code = "NEW_BEAR_IMPULSE_CONFIRMED"
        return
    if state.locked:
        return
    if event_name == "PULLBACK_START":
        state.active = True
        state.pullback_id = f"PB-S-{event_time}"
        state.pullback_start_time = event_time
        state.pullback_start_low = event_low
        state.attempt_count = 0
        state.h_state = "NONE"
        state.up_leg_active = True
        state.attempt_active = False
        state.awaiting_up_leg = False
        state.reason_code = "PULLBACK_STARTED"
        return
    if not state.active:
        return
    if event_name == "CONTINUE_RECOVERY":
        if state.current_attempt_low <= 0.0:
            state.current_attempt_low = event_low
        else:
            state.current_attempt_low = min(state.current_attempt_low, event_low)
        state.reason_code = "RECOVERY_ATTEMPT_CONTINUES"
        return
    if event_name == "NEW_RECOVERY_ATTEMPT":
        state.attempt_count += 1
        state.current_attempt_low = event_low
        state.attempt_active = True
        state.up_leg_active = False
        state.awaiting_up_leg = False
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


def advance_bear_from_closed_bar(
    state: BearPullbackState,
    previous: dict[str, Any],
    current: dict[str, Any],
    tick_size: float,
    signal_valid: bool = False,
) -> None:
    if state.locked or not state.active:
        return
    tick = max(0.0, tick_size)
    lower_low = float(current["low"]) <= float(previous["low"]) - tick + 1e-12
    lower_close = float(current["close"]) <= float(previous["close"]) - tick + 1e-12
    if state.attempt_active:
        if lower_low:
            if state.current_attempt_low <= 0.0:
                state.current_attempt_low = float(current["low"])
            else:
                state.current_attempt_low = min(state.current_attempt_low, float(current["low"]))
            state.reason_code = "RECOVERY_ATTEMPT_CONTINUES"
            return
        next_up = is_rally_start(previous, current, tick_size)
        state.attempt_active = False
        state.up_leg_active = next_up
        state.awaiting_up_leg = not next_up
        state.reason_code = "NEXT_UP_LEG_STARTED" if next_up else "RECOVERY_ATTEMPT_STOPPED"
        return
    if state.awaiting_up_leg:
        if not is_rally_start(previous, current, tick_size):
            return
        state.up_leg_active = True
        state.awaiting_up_leg = False
        state.reason_code = "NEXT_UP_LEG_STARTED"
        return
    if state.up_leg_active and lower_low and lower_close:
        advance_bear_pullback_state(
            state,
            "NEW_RECOVERY_ATTEMPT",
            bar_time(current),
            float(current["low"]),
            signal_valid,
        )


def classify_bear_signal(
    signal: dict[str, Any], previous: list[dict[str, Any]]
) -> BearSignalResult:
    out = BearSignalResult()
    body = _body(signal)
    rng = _range(signal)
    lower = min(float(signal["open"]), float(signal["close"])) - float(signal["low"])
    upper = float(signal["high"]) - max(float(signal["open"]), float(signal["close"]))
    bottom_third = rng > 0.0 and max(float(signal["open"]), float(signal["close"])) <= (
        float(signal["low"]) + rng / 3.0
    )
    out.pin = (
        _bearish(signal)
        and body > 0.0
        and upper >= 3.0 * body
        and bottom_third
        and lower <= 0.25 * body
    )
    out.strong_bear = (
        _bearish(signal)
        and body_range_ratio(signal) + 1e-12 >= 2.0 / 3.0
        and lower <= 0.5 * body
        and close_location(signal) <= 0.25
    )
    bulls = len(previous) >= 3
    body_low = float("inf")
    body_high = float("-inf")
    for i in range(3):
        if not bulls:
            break
        bulls = _bullish(previous[i])
        body_low = min(body_low, min(float(previous[i]["open"]), float(previous[i]["close"])))
        body_high = max(body_high, max(float(previous[i]["open"]), float(previous[i]["close"])))
    out.three_bull_engulf = (
        _bearish(signal)
        and bulls
        and float(signal["open"]) >= body_high
        and float(signal["close"]) <= body_low
        and body_range_ratio(signal) + 1e-12 >= 0.60
    )
    out.valid = out.pin or out.strong_bear or out.three_bull_engulf
    if out.pin:
        out.signal_type = "BEAR_PIN"
    elif out.strong_bear:
        out.signal_type = "STRONG_BEAR"
    elif out.three_bull_engulf:
        out.signal_type = "ONE_BEAR_ENGULF_THREE_BULL"
    out.reason_code = "SIGNAL_VALID" if out.valid else "SIGNAL_INVALID"
    return out


# --------------------------------------------------------------------------
# M5TargetStructure.mqh / M5StructureStop.mqh
# --------------------------------------------------------------------------


def _is_confirmed_pivot_high(bars: list[dict[str, Any]], center: int) -> bool:
    if center < 2 or center + 2 >= len(bars):
        return False
    level = float(bars[center]["high"])
    for i in (1, 2):
        if level <= float(bars[center - i]["high"]) or level <= float(bars[center + i]["high"]):
            return False
    return True


def _is_confirmed_pivot_low(bars: list[dict[str, Any]], center: int) -> bool:
    if center < 2 or center + 2 >= len(bars):
        return False
    level = float(bars[center]["low"])
    for i in (1, 2):
        if level >= float(bars[center - i]["low"]) or level >= float(bars[center + i]["low"]):
            return False
    return True


def _pivot_consumed_by_closed_bars(
    bars: list[dict[str, Any]], center: int, resistance: bool, level: float, tick: float
) -> bool:
    if center <= 0 or center >= len(bars) or level <= 0.0 or tick <= 0.0:
        return False
    for i in range(center):
        if resistance and float(bars[i]["close"]) >= level + tick:
            return True
        if not resistance and float(bars[i]["close"]) <= level - tick:
            return True
    return False


def _is_current_route_b_internal_pivot(
    bar: dict[str, Any], setup_anchor: int, exclude_setup_internal: bool
) -> bool:
    return bool(exclude_setup_internal and setup_anchor > 0 and bar_time(bar) >= setup_anchor)


def nearest_valid_buy_resistance(
    m5_bars: list[dict[str, Any]],
    m15_bars: list[dict[str, Any]],
    entry: float,
    tick: float,
    setup_anchor: int,
    exclude_setup_internal: bool,
) -> float:
    result = 0.0
    for bars in (m15_bars, m5_bars):
        for i in range(2, max(0, len(bars) - 2)):
            if not _is_confirmed_pivot_high(bars, i):
                continue
            level = float(bars[i]["high"])
            if level <= entry:
                continue
            if _pivot_consumed_by_closed_bars(bars, i, True, level, tick):
                continue
            if _is_current_route_b_internal_pivot(bars[i], setup_anchor, exclude_setup_internal):
                continue
            if result <= 0.0 or level < result:
                result = level
    return result


def nearest_valid_sell_support(
    m5_bars: list[dict[str, Any]],
    m15_bars: list[dict[str, Any]],
    entry: float,
    tick: float,
    setup_anchor: int,
    exclude_setup_internal: bool,
) -> float:
    result = 0.0
    for bars in (m15_bars, m5_bars):
        for i in range(2, max(0, len(bars) - 2)):
            if not _is_confirmed_pivot_low(bars, i):
                continue
            level = float(bars[i]["low"])
            if level >= entry:
                continue
            if _pivot_consumed_by_closed_bars(bars, i, False, level, tick):
                continue
            if _is_current_route_b_internal_pivot(bars[i], setup_anchor, exclude_setup_internal):
                continue
            if result <= 0.0 or level > result:
                result = level
    return result


@dataclass
class StructureStopSelection:
    valid: bool = False
    previous_extreme: float = 0.0
    structure_extreme: float = 0.0
    anchor: float = 0.0
    structure_bar_index: int = -1
    source: str = ""


def select_structure_stop(bars: list[dict[str, Any]], is_sell: bool) -> StructureStopSelection:
    out = StructureStopSelection()
    if len(bars) < 2:
        return out
    if is_sell:
        out.previous_extreme = float(bars[1]["high"])
        for i in range(2, max(0, len(bars) - 2)):
            if _is_confirmed_pivot_high(bars, i):
                out.structure_extreme = float(bars[i]["high"])
                out.structure_bar_index = i
                break
        if out.previous_extreme <= 0.0:
            return out
        out.anchor = max(out.previous_extreme, out.structure_extreme)
    else:
        out.previous_extreme = float(bars[1]["low"])
        for i in range(2, max(0, len(bars) - 2)):
            if _is_confirmed_pivot_low(bars, i):
                out.structure_extreme = float(bars[i]["low"])
                out.structure_bar_index = i
                break
        if out.previous_extreme <= 0.0:
            return out
        out.anchor = (
            min(out.previous_extreme, out.structure_extreme)
            if out.structure_extreme > 0.0
            else out.previous_extreme
        )

    if out.structure_extreme <= 0.0:
        out.source = "PREVIOUS_BAR"
    elif (
        abs(out.anchor - out.previous_extreme) < 1e-7
        and abs(out.anchor - out.structure_extreme) < 1e-7
    ):
        out.source = "STRUCTURE_AND_PREVIOUS"
    elif abs(out.anchor - out.structure_extreme) < 1e-7:
        out.source = "STRUCTURE_PIVOT"
    else:
        out.source = "PREVIOUS_BAR"
    out.valid = out.anchor > 0.0
    return out


# --------------------------------------------------------------------------
# Strategy01Planner.mqh / Strategy01SellPlanner.mqh
# --------------------------------------------------------------------------


@dataclass
class LocationResult:
    valid: bool = False
    location_fib236: bool = False
    location_fib382: bool = False
    location_ema20: bool = False
    location_breakout_retest: bool = False
    m2b: bool = False
    reason_code: str = ""


def evaluate_location_bar(
    signal: dict[str, Any], atr: float, levels: dict[str, float], tolerance_atr: float
) -> LocationResult:
    out = LocationResult()
    tolerance = max(0.0, tolerance_atr * atr)
    out.location_fib236 = (
        float(signal["low"]) - tolerance <= levels["fib236"] <= float(signal["high"]) + tolerance
    )
    out.location_fib382 = (
        float(signal["low"]) - tolerance <= levels["fib382"] <= float(signal["high"]) + tolerance
    )
    out.location_ema20 = (
        float(signal["low"]) - tolerance <= levels["ema20"] <= float(signal["high"]) + tolerance
    )
    out.location_breakout_retest = (
        float(signal["low"]) - tolerance
        <= levels["breakout_retest"]
        <= float(signal["high"]) + tolerance
    )
    out.m2b = out.location_ema20
    out.valid = (
        out.location_fib236
        or out.location_fib382
        or out.location_ema20
        or out.location_breakout_retest
    )
    out.reason_code = "LOCATION_VALID" if out.valid else "LOCATION_INVALID"
    return out


@dataclass
class TradePlan:
    valid: bool = False
    armed: bool = False
    entry: float = 0.0
    final_stop: float = 0.0
    planned_r: float = 0.0
    expiry_bars: int = 3
    reason_code: str = "NOT_EVALUATED"
    target: float = 0.0
    target_r: float = 0.0


def build_buy_plan(
    signal: dict[str, Any],
    stop_anchor: float,
    tick: float,
    broker_min_distance: float,
    market_ask: float,
    target_high: float,
) -> TradePlan:
    out = TradePlan()
    out.entry = align_up(float(signal["high"]) + tick, tick)
    if stop_anchor <= 0.0:
        out.reason_code = "PLAN_REJECT_NO_STRUCTURE_STOP"
        return out
    out.final_stop = align_down(stop_anchor - 2.0, tick)
    out.planned_r = out.entry - out.final_stop
    if out.planned_r <= 0.0:
        out.reason_code = "PLAN_REJECT_NONPOSITIVE_R"
        return out
    if out.entry - out.final_stop < broker_min_distance:
        out.reason_code = "PLAN_REJECT_BROKER_STOP_DISTANCE"
        return out
    out.target = target_high
    out.target_r = (target_high - out.entry) / out.planned_r if out.planned_r > 0 else 0.0
    if target_high - out.entry < V3109_TARGET_SPACE_R * out.planned_r:
        out.reason_code = "PLAN_REJECT_TARGET_SPACE_LT_2R"
        return out
    out.armed = (out.entry - market_ask) < broker_min_distance
    out.valid = True
    out.reason_code = "PLAN_ARMED_WAIT_DISTANCE" if out.armed else "PLAN_VALID"
    return out


def build_sell_plan(
    signal: dict[str, Any],
    stop_anchor: float,
    tick: float,
    broker_min_distance: float,
    market_bid: float,
    target_low: float,
) -> TradePlan:
    out = TradePlan()
    out.entry = align_down(float(signal["low"]) - tick, tick)
    if stop_anchor <= 0.0:
        out.reason_code = "PLAN_REJECT_NO_STRUCTURE_STOP"
        return out
    out.final_stop = align_up(stop_anchor + 2.0, tick)
    out.planned_r = out.final_stop - out.entry
    if out.planned_r <= 0.0:
        out.reason_code = "PLAN_REJECT_NONPOSITIVE_R"
        return out
    if out.final_stop - out.entry < broker_min_distance:
        out.reason_code = "PLAN_REJECT_BROKER_STOP_DISTANCE"
        return out
    out.target = target_low
    out.target_r = (out.entry - target_low) / out.planned_r if out.planned_r > 0 else 0.0
    if out.entry - target_low < V3109_TARGET_SPACE_R * out.planned_r:
        out.reason_code = "PLAN_REJECT_TARGET_SPACE_LT_2R"
        return out
    out.armed = (market_bid - out.entry) < broker_min_distance
    out.valid = True
    out.reason_code = "PLAN_ARMED_WAIT_DISTANCE" if out.armed else "PLAN_VALID"
    return out


def bull_recovery_quality(
    current: dict[str, Any], previous: dict[str, Any], atr: float, tick: float
) -> bool:
    """对应 V3109C31BullRecoveryQuality。"""
    if (
        atr <= 0.0
        or tick <= 0.0
        or float(current["close"]) <= float(current["open"])
        or float(current["close"]) <= float(previous["close"])
    ):
        return False
    rng = _range(current)
    if rng <= 0.0 or (float(current["close"]) - float(current["open"])) / rng < 0.25:
        return False
    allowed_new_low = max(tick, 0.20 * atr)
    return float(current["low"]) >= float(previous["low"]) - allowed_new_low


def has_immediate_bull_obstacle(
    current: dict[str, Any],
    previous: dict[str, Any],
    older: dict[str, Any],
    entry: float,
    planned_r: float,
) -> bool:
    """对应 V3109C31HasImmediateBullObstacle。"""
    if entry <= 0.0 or planned_r <= 0.0 or float(previous["high"]) <= entry:
        return False
    local_pivot = float(previous["high"]) > float(current["high"]) and float(
        previous["high"]
    ) > float(older["high"])
    return local_pivot and float(previous["high"]) - entry < planned_r


def bull_stop_reliable(
    attempt_no: int, stop_source: str, entry: float, final_stop: float, m15_atr: float
) -> bool:
    """对应 V3109C31BullStopReliable。"""
    if entry <= final_stop or m15_atr <= 0.0:
        return False
    if attempt_no != 2 or stop_source != "PREVIOUS_BAR":
        return True
    return entry - final_stop >= 0.50 * m15_atr


# --------------------------------------------------------------------------
# M15 上下文（M15BullContext / M15BearContext）
# --------------------------------------------------------------------------


@dataclass
class M15Context:
    primary: int = 0
    local_phase: int = 0
    primary_label: str = "PRIMARY_RANGE"
    local_phase_label: str = "LOCAL_NEUTRAL"
    direction: str = "NONE"
    allow_buy: bool = False
    allow_sell: bool = False
    hard_exhaustion_bull: bool = False
    hard_exhaustion_bear: bool = False
    protected_low_intact: bool = False
    protected_high_intact: bool = False
    reason_buy: str = ""
    reason_sell: str = ""
    bull_context_reason: str = ""
    bear_context_reason: str = ""
    atr14: float = 0.0
    ema20: float = 0.0
    move_atr: float = 0.0
    move_atr_sell: float = 0.0
    range_like: bool = False
    impulse_low: float = 0.0
    impulse_high: float = 0.0
    nearest_resistance: float = 0.0
    nearest_support: float = 0.0
    closed_bar_time: int = 0

    def as_facts(self) -> dict[str, Any]:
        authority_cn = {
            "BUY": "M15主结构为多头，放行做多",
            "SELL": "M15主结构为空头，放行做空",
        }
        blocked_cn = {
            "BUY": "M15主结构未放行做多（当前方向不是多头或已进入衰竭/过渡）",
            "SELL": "M15主结构未放行做空（当前方向不是空头或已进入衰竭/过渡）",
        }
        return {
            "primary_state": self.primary_label,
            "local_phase": self.local_phase_label,
            "direction": self.direction,
            "allow_buy": self.allow_buy,
            "allow_sell": self.allow_sell,
            # 与 EA 的 V3109ApplyTrendToContexts 一致：放行/原因由主结构状态决定，
            # 指标层的原因只作为诊断信息（metrics_reason_*）保留。
            "reason_buy": self.reason_buy,
            "reason_sell": self.reason_sell,
            "metrics_reason_buy": self.bull_context_reason,
            "metrics_reason_sell": self.bear_context_reason,
            "authority_buy_cn": authority_cn["BUY"] if self.allow_buy else blocked_cn["BUY"],
            "authority_sell_cn": authority_cn["SELL"] if self.allow_sell else blocked_cn["SELL"],
            "hard_exhaustion_bull": self.hard_exhaustion_bull,
            "hard_exhaustion_bear": self.hard_exhaustion_bear,
            "protected_low_intact": self.protected_low_intact,
            "protected_high_intact": self.protected_high_intact,
            "atr14": self.atr14,
            "ema20": self.ema20,
            "move_atr": self.move_atr,
            "move_atr_sell": self.move_atr_sell,
            "range_like": self.range_like,
        }


def _evaluate_m15_bull(metrics: Any) -> tuple[bool, bool, str]:
    spike_score = (
        int(metrics.move_atr >= 2.0)
        + int(metrics.slope_atr >= 0.20)
        + int(metrics.direction_efficiency >= 0.65)
        + int(metrics.breakout_body_atr >= 0.80)
        + int(metrics.breakout_body_range_ratio >= 0.60)
        + int(metrics.breakout_close_location >= 0.75)
        + int(metrics.break_distance_atr >= 0.10)
    )
    spike_path = bool(
        metrics.valid_bull_breakout
        and metrics.broken_level_count >= 2
        and metrics.breakout_follow_through
        and spike_score >= 5
    )
    ema_score = (
        int(metrics.same_side_close_ratio >= 0.80)
        + int(metrics.opposite_close_count <= 2)
        + int(metrics.ema_cross_count <= 2)
        + int(metrics.ema_slope_atr >= 0.03)
    )
    ema_path = bool(
        metrics.bull_structure
        and metrics.ema_slope_atr > 0.0
        and metrics.protected_swing_low_intact
        and ema_score >= 3
    )
    climax_no_follow = bool(metrics.climax_candidate_m15 and metrics.no_follow_through_m15)
    hard_exhaustion = bool(
        metrics.failed_breakout_m15
        or metrics.protected_swing_low_broken_m15
        or metrics.m15_range_like
        or climax_no_follow
    )
    strong = bool(spike_path or ema_path)
    common_gate = bool(
        metrics.bull_context
        and metrics.protected_swing_low_intact
        and metrics.trend_phase in (1, 2)
        and not hard_exhaustion
    )
    allow = bool(common_gate and strong)

    continuation_memory = bool(metrics.bull_structure or metrics.same_side_close_ratio >= 0.80)
    continuation_pause = bool(metrics.m15_range_like or climax_no_follow)
    continuation = bool(
        continuation_pause
        and metrics.protected_swing_low_intact
        and not metrics.failed_breakout_m15
        and not metrics.protected_swing_low_broken_m15
        and metrics.ema_slope_atr >= 0.03
        and continuation_memory
    )
    if continuation:
        return True, False, "M15_ALLOW_BULL_CONTINUATION_PAUSE"
    if not metrics.bull_context:
        reason = "M15_BLOCK_BULL_CONTEXT"
    elif not metrics.protected_swing_low_intact:
        reason = "M15_BLOCK_PROTECTED_LOW"
    elif metrics.trend_phase not in (1, 2):
        reason = "M15_BLOCK_TREND_PHASE"
    elif hard_exhaustion:
        reason = "M15_HARD_EXHAUSTION"
    elif not strong:
        reason = "M15_BLOCK_NO_STRONG_BULL_PATH"
    else:
        reason = "M15_ALLOW_BULL"
    return allow, hard_exhaustion, reason


def _evaluate_m15_bear(metrics: Any) -> tuple[bool, bool, str]:
    spike_score = (
        int(metrics.move_atr >= 2.0)
        + int(metrics.slope_atr >= 0.20)
        + int(metrics.direction_efficiency >= 0.65)
        + int(metrics.breakdown_body_atr >= 0.80)
        + int(metrics.breakdown_body_range_ratio >= 0.60)
        + int(metrics.breakdown_close_location >= 0.75)
        + int(metrics.break_distance_atr >= 0.10)
    )
    spike_path = bool(
        metrics.valid_bear_breakdown
        and metrics.broken_level_count >= 2
        and metrics.breakdown_follow_through
        and spike_score >= 5
    )
    ema_score = (
        int(metrics.same_side_close_ratio >= 0.80)
        + int(metrics.opposite_close_count <= 2)
        + int(metrics.ema_cross_count <= 2)
        + int(metrics.ema_slope_atr >= 0.03)
    )
    ema_path = bool(
        metrics.bear_structure
        and metrics.ema_slope_atr > 0.0
        and metrics.protected_swing_high_intact
        and ema_score >= 3
    )
    climax_no_follow = bool(metrics.climax_candidate_m15 and metrics.no_follow_through_m15)
    hard_exhaustion = bool(
        metrics.failed_breakdown_m15
        or metrics.protected_swing_high_broken_m15
        or metrics.m15_range_like
        or climax_no_follow
    )
    strong = bool(spike_path or ema_path)
    common_gate = bool(
        metrics.bear_context
        and metrics.protected_swing_high_intact
        and metrics.trend_phase in (1, 2)
        and not hard_exhaustion
    )
    allow = bool(common_gate and strong)

    continuation_memory = bool(metrics.bear_structure or metrics.same_side_close_ratio >= 0.80)
    continuation_pause = bool(metrics.m15_range_like or climax_no_follow)
    continuation = bool(
        continuation_pause
        and metrics.protected_swing_high_intact
        and not metrics.failed_breakdown_m15
        and not metrics.protected_swing_high_broken_m15
        and metrics.ema_slope_atr >= 0.03
        and continuation_memory
    )
    if continuation:
        return True, False, "M15_ALLOW_BEAR_CONTINUATION_PAUSE"
    if not metrics.bear_context:
        reason = "M15_BLOCK_BEAR_CONTEXT"
    elif not metrics.protected_swing_high_intact:
        reason = "M15_BLOCK_PROTECTED_HIGH"
    elif metrics.trend_phase not in (1, 2):
        reason = "M15_BLOCK_TREND_PHASE"
    elif hard_exhaustion:
        reason = "M15_HARD_EXHAUSTION"
    elif not strong:
        reason = "M15_BLOCK_NO_STRONG_BEAR_PATH"
    else:
        reason = "M15_ALLOW_BEAR"
    return allow, hard_exhaustion, reason


def m15_open_for_m5(m5_open_time: int) -> int:
    """M5 收盘那一刻的"最后一根已收线 M15"开盘时间。"""
    tick = int(m5_open_time) + M5_SECONDS
    return ((tick - M15_SECONDS) // M15_SECONDS) * M15_SECONDS


def build_m15_context_series(
    m15_bars: list[dict[str, Any]],
    spread_price: float,
    tick: float,
    replay_anchor: int = 0,
) -> dict[int, M15Context]:
    """按 M15 收线顺序推进主结构状态机，返回 {m15 开盘时间: 上下文}。

    replay_anchor 非 0 时（EA 重载），锚点起重建主结构记忆，模拟 EA 重挂后
    从 RANGE 重新起步。
    """
    m15_bars = with_int_time(m15_bars)
    if len(m15_bars) >= 2 and m15_bars[0]["time"] < m15_bars[-1]["time"]:
        m15_bars = list(reversed(m15_bars))  # 传入的是"由旧到新"，统一成"最新在前"
    engine = TrendEngine()
    chronological = list(reversed(m15_bars))
    series: dict[int, M15Context] = {}
    # EA 重挂后会立刻用"最后一根已收线 M15"重新评估一次主结构（记忆为空）。
    # 所以锚点要落在该 M5 时刻之前那根已收线 M15 上，而不是下一根。
    m15_anchor = (
        ((int(replay_anchor) - M15_SECONDS) // M15_SECONDS) * M15_SECONDS
        if replay_anchor
        else 0
    )
    anchored = False
    for index in range(len(chronological)):
        if index + 1 < 100:
            continue
        window = list(reversed(chronological[: index + 1]))
        if m15_anchor and not anchored and bar_time(window[0]) >= m15_anchor:
            # 跨过锚点：主结构记忆清零（只清一次），模拟 EA 重挂后从 RANGE 重新起步。
            engine = TrendEngine()
            anchored = True
        metrics_window = window[:100]
        bull, impulse_low, impulse_high, nearest_resistance = derive_m15_metrics(metrics_window)
        bear, _sell_low, _sell_high, nearest_support = derive_m15_bear_metrics(metrics_window)
        if bull is None or bear is None:
            continue
        pivots = derive_primary_pivots(window[:300])
        data = build_primary_input(
            bull, bear, pivots, bar_time(window[0]), float(spread_price), float(tick)
        )
        trend = engine.advance(data)
        # 对应 V3109ApplyTrendToContexts：主结构状态覆盖指标层的放行/衰竭结论。
        allow_bull, hard_bull, bull_reason = _evaluate_m15_bull(bull)
        _allow_bear, hard_bear, bear_reason = _evaluate_m15_bear(bear)
        context = M15Context(
            primary=trend.primary,
            local_phase=trend.local_phase,
            primary_label=primary_label(trend.primary),
            local_phase_label=local_phase_label(trend.local_phase),
            direction=(
                "BUY"
                if trend.primary == PRIMARY_BULL
                else "SELL"
                if trend.primary == PRIMARY_BEAR
                else "NONE"
            ),
            allow_buy=bool(trend.allow_buy_scan),
            allow_sell=bool(trend.allow_sell_scan),
            hard_exhaustion_bull=bool(trend.hard_exhaustion_bull),
            hard_exhaustion_bear=bool(trend.hard_exhaustion_bear),
            protected_low_intact=bool(bull.protected_swing_low_intact),
            protected_high_intact=bool(bear.protected_swing_high_intact),
            reason_buy=trend.reason_buy,
            reason_sell=trend.reason_sell,
            bull_context_reason=bull_reason,
            bear_context_reason=bear_reason,
            atr14=float(bull.atr14),
            ema20=float(bull.ema20),
            move_atr=float(bull.move_atr),
            move_atr_sell=float(bear.move_atr),
            range_like=bool(bull.m15_range_like),
            impulse_low=float(impulse_low),
            impulse_high=float(impulse_high),
            nearest_resistance=float(nearest_resistance),
            nearest_support=float(nearest_support),
            closed_bar_time=bar_time(window[0]),
        )
        series[bar_time(window[0])] = context
    return series


# --------------------------------------------------------------------------
# 每根 M5 的完整决策
# --------------------------------------------------------------------------


@dataclass
class EngineState:
    bull: PullbackState = field(default_factory=PullbackState)
    bear: BearPullbackState = field(default_factory=BearPullbackState)
    bull_impulse_confirmed: bool = False
    bull_impulse_high: float = 0.0
    bull_pullback_bars: list[dict[str, Any]] = field(default_factory=list)
    bear_impulse_confirmed: bool = False
    bear_impulse_low: float = 0.0
    bear_pullback_bars: list[dict[str, Any]] = field(default_factory=list)
    m15_impulse_low: float = 0.0
    m15_impulse_high: float = 0.0
    m15_impulse_low_sell: float = 0.0
    m15_impulse_high_sell: float = 0.0
    bull_reset_reason: str = ""
    bear_reset_reason: str = ""


@dataclass
class Decision:
    action: str = "WAIT"
    direction: str = "NONE"
    route: str = "NONE"
    route_code: int = ROUTE_NONE
    entry: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    risk: float = 0.0
    rr: float = 0.0
    reason: str = ""
    signal_type: str = ""
    attempt_no: int = 0
    stop_source: str = ""
    signal_bar_range: float = 0.0
    location: dict[str, Any] = field(default_factory=dict)
    targets: dict[str, Any] = field(default_factory=dict)


def _append_pullback_bar(store: list[dict[str, Any]], bar: dict[str, Any], limit: int = 20) -> None:
    store.insert(0, bar)
    while len(store) > limit:
        store.pop()


def _side_snapshot(state: Any, reset_reason: str = "") -> dict[str, Any]:
    return {
        "active": bool(state.active),
        "locked": bool(state.locked),
        "h_state": str(state.h_state),
        "attempt_count": int(state.attempt_count),
        "reason_code": str(state.reason_code),
        "reset_reason": str(reset_reason or ""),
    }


def _evaluate_bull_bar(
    engine: EngineState,
    bars: list[dict[str, Any]],
    m15: M15Context,
    m15_bars: list[dict[str, Any]],
    params: dict[str, Any],
    tick: float,
    ask: float,
    broker_min_distance: float,
    cheap: bool = False,
) -> Decision:
    out = Decision()
    bars = bars[:M5_EVAL_BARS]
    out.signal_bar_range = _range(bars[0])
    atr = atr_from_newest(bars, 14)
    if atr <= 0.0:
        out.reason = "NO_ATR"
        return out
    tolerance = float(params["location_tolerance_atr"]) * atr

    bootstrap = _try_independent_bull_ema_recovery(
        engine, bars, m15, params, tick, ask, broker_min_distance, cheap=cheap
    )
    if bootstrap is not None:
        return bootstrap

    if engine.bull.active and confirm_bull_impulse(
        bars[1], bars[0], engine.bull.pullback_start_high, atr, tick
    ):
        advance_pullback_state(engine.bull, "NEW_IMPULSE", bar_time(bars[0]))
        engine.bull_impulse_confirmed = True
        engine.bull_impulse_high = max(float(bars[1]["high"]), float(bars[0]["high"]))
        engine.bull_pullback_bars = []
        out.reason = "NEW_BULL_IMPULSE"
        return out

    if not engine.bull.active and not engine.bull_impulse_confirmed:
        prior_high = float(bars[2]["high"])
        for i in range(3, min(12, len(bars))):
            prior_high = max(prior_high, float(bars[i]["high"]))
        if (
            float(bars[1]["close"])
            >= prior_high + max(tick, float(params["impulse_buffer_atr"]) * atr)
            and body_range_ratio(bars[1]) >= 0.60
            and close_location(bars[1]) >= 0.75
            and float(bars[0]["close"]) > prior_high
        ):
            engine.bull_impulse_confirmed = True
            engine.bull_impulse_high = max(float(bars[1]["high"]), float(bars[0]["high"]))

    if (
        not engine.bull.active
        and engine.bull_impulse_confirmed
        and is_pullback_start(bars[1], bars[0], tick)
    ):
        advance_pullback_state(
            engine.bull,
            "PULLBACK_START",
            bar_time(bars[0]),
            max(engine.bull_impulse_high, float(bars[0]["high"])),
        )
        engine.bull_pullback_bars = []
        _append_pullback_bar(engine.bull_pullback_bars, bars[0])
        out.reason = "PULLBACK_STARTED"
        return out

    if not engine.bull.active or engine.bull.locked:
        out.reason = "PULLBACK_IDLE"
        return out

    _append_pullback_bar(engine.bull_pullback_bars, bars[0])
    range_like = False
    if len(engine.bull_pullback_bars) >= int(params["m5_range_min_bars"]):
        range_like = evaluate_range_like(
            engine.bull_pullback_bars, int(params["m5_range_min_bars"])
        ).is_range_like

    signal = classify_bull_signal(bars[0], bars[1:4])
    impulse_range = max(0.0, engine.bull_impulse_high - engine.m15_impulse_low)
    levels = {
        "fib236": engine.bull_impulse_high - 0.236 * impulse_range,
        "fib382": engine.bull_impulse_high - 0.382 * impulse_range,
        "ema20": ema_from_newest(bars, 20),
        "breakout_retest": engine.bull_impulse_high,
    }
    location = evaluate_location_bar(
        bars[0], atr, levels, float(params["location_tolerance_atr"])
    )
    advance_from_closed_bar(engine.bull, bars[1], bars[0], tick, signal.valid)

    recent_closed = bars[1:4]
    recent_ema = recent_m5_ema20(bars)
    ema_ready = recent_ema is not None
    ema_rising = bool(ema_ready and ema_from_newest(bars, 20) > recent_ema[0])
    cycle_closed = bars[1 : 1 + EMA_CYCLE_BARS]
    cycle_ema = m5_ema20_cycle(bars)
    cycle_ready = cycle_ema is not None
    cycle_rising = bool(cycle_ready and bull_ema_overall_direction(cycle_ema))

    h2_candidate = engine.bull.h2_candidate_time == bar_time(bars[0])
    original_h2 = bool(
        not range_like
        and engine.bull.h_state == "H2"
        and engine.bull.h2_signal_time == bar_time(bars[0])
        and location.valid
    )
    recovery_break = bool(
        not range_like
        and location.valid
        and bull_recovery_break(h2_candidate, bars[0], bars[1], tick)
    )
    real_pullback = bool(
        cycle_ready and has_cycle_bull_ema_pullback(cycle_closed, cycle_ema, tolerance)
    )
    recovery_quality = bull_recovery_quality(bars[0], bars[1], atr, tick)
    ema_recovery = bool(
        cycle_ready
        and recovery_quality
        and bull_ema_recovery(
            m15.allow_buy,
            m15.hard_exhaustion_bull,
            m15.protected_low_intact,
            cycle_rising,
            real_pullback,
            cycle_closed,
            cycle_ema,
            bars[0],
            bars[1],
            tolerance,
            tick,
        )
    )
    compression_break = bool(
        ema_ready
        and bull_compression_break(
            m15.allow_buy,
            m15.hard_exhaustion_bull,
            m15.protected_low_intact,
            ema_rising,
            range_like,
            recent_closed,
            recent_ema,
            bars[0],
            tolerance,
            tick,
        )
    )
    route_code = select_m5_route(original_h2, recovery_break, ema_recovery, compression_break)
    out.location = {
        "valid": location.valid,
        "fib236": location.location_fib236,
        "fib382": location.location_fib382,
        "ema20": location.location_ema20,
        "breakout_retest": location.location_breakout_retest,
        "range_like": range_like,
        "h_state": engine.bull.h_state,
        "attempt_count": engine.bull.attempt_count,
    }
    if route_code == ROUTE_NONE:
        if range_like:
            advance_pullback_state(engine.bull, "RANGE_LIKE", bar_time(bars[0]))
        out.reason = "NO_ROUTE"
        return out
    if cheap:
        # 预热阶段只需要状态机推进（含上面的 RANGE_LIKE 锁定），
        # 目标/止损/计划属于决策层，跳过可以省掉大量 pivot 扫描。
        out.route_code = route_code
        out.direction = "BUY"
        out.reason = "WARMUP_STATE_ONLY"
        return out

    target_anchor = 0
    if route_code == ROUTE_EMA_RECOVERY:
        target_anchor = bull_ema_pullback_start(cycle_closed, cycle_ema, tolerance)
    elif route_code == ROUTE_EMA_COMPRESSION_BREAK:
        target_anchor = recent_ema_interaction_start(recent_closed, recent_ema, tolerance)
    route_b = route_code in (ROUTE_EMA_RECOVERY, ROUTE_EMA_COMPRESSION_BREAK)
    exclude_internal = bool(route_b and target_anchor > 0)

    attempt_no = engine.bull.attempt_count
    resistance = nearest_valid_buy_resistance(
        bars, m15_bars, float(bars[0]["high"]) + tick, tick, target_anchor, exclude_internal
    )
    stop_selection = select_structure_stop(bars, False)
    plan = build_buy_plan(bars[0], stop_selection.anchor, tick, broker_min_distance, ask, resistance)
    if (
        plan.valid
        and route_code == ROUTE_EMA_RECOVERY
        and has_immediate_bull_obstacle(bars[0], bars[1], bars[2], plan.entry, plan.planned_r)
    ):
        plan.valid = False
        plan.reason_code = "PLAN_REJECT_IMMEDIATE_M5_RESISTANCE_LT_1R"
    if plan.valid and not bull_stop_reliable(
        attempt_no, stop_selection.source, plan.entry, plan.final_stop, m15.atr14
    ):
        plan.valid = False
        plan.reason_code = "PLAN_REJECT_A2_TIGHT_PREVIOUS_BAR_STOP"

    signal_type = {
        ROUTE_H2_ORIGINAL: (
            "BULL_PIN_BAR"
            if signal.pin
            else "STRONG_BULL_BAR"
            if signal.strong_bull
            else "BULL_ENGULF_THREE_BEAR"
        ),
        ROUTE_H2_RECOVERY_BREAK: "BULL_RECOVERY_BREAK",
        ROUTE_EMA_RECOVERY: "EMA_RECOVERY_BUY",
        ROUTE_EMA_COMPRESSION_BREAK: "EMA_COMPRESSION_BREAK_BUY",
    }[route_code]
    out.route_code = route_code
    out.route = (
        "FIB_PA"
        if route_code in (ROUTE_H2_ORIGINAL, ROUTE_H2_RECOVERY_BREAK)
        else ("EMA_H23" if route_code == ROUTE_EMA_RECOVERY else "EMA_COMPRESSION")
    )
    out.direction = "BUY" if route_code != ROUTE_NONE else "NONE"
    out.signal_type = signal_type
    out.attempt_no = attempt_no
    out.stop_source = stop_selection.source
    out.entry = plan.entry
    out.sl = plan.final_stop
    out.risk = plan.planned_r
    out.rr = plan.target_r
    out.tp1 = resistance
    out.tp2 = plan.entry + 2.0 * plan.planned_r if plan.planned_r > 0 else 0.0
    out.targets = {
        "resistance": resistance,
        "stop_anchor": stop_selection.anchor,
        "stop_source": stop_selection.source,
        "plan_reason": plan.reason_code,
        "target_r": plan.target_r,
    }
    out.action = "OPEN" if plan.valid else "WAIT"
    out.reason = plan.reason_code
    return out


def _try_independent_bull_ema_recovery(
    engine: EngineState,
    bars: list[dict[str, Any]],
    m15: M15Context,
    params: dict[str, Any],
    tick: float,
    ask: float,
    broker_min_distance: float,
    cheap: bool = False,
) -> Decision | None:
    """对应 V3103TryIndependentBullEMARecovery（C3 bootstrap）。"""
    atr = atr_from_newest(bars, 14)
    if atr <= 0.0:
        return None
    tolerance = float(params["location_tolerance_atr"]) * atr
    cycle_closed = bars[1 : 1 + EMA_CYCLE_BARS]
    cycle_ema = m5_ema20_cycle(bars)
    if cycle_ema is None:
        return None
    cycle_rising = bull_ema_overall_direction(cycle_ema)
    real_pullback = has_cycle_bull_ema_pullback(cycle_closed, cycle_ema, tolerance)
    recovery_quality = bull_recovery_quality(bars[0], bars[1], atr, tick)
    if not (
        recovery_quality
        and bull_ema_recovery(
            m15.allow_buy,
            m15.hard_exhaustion_bull,
            m15.protected_low_intact,
            cycle_rising,
            real_pullback,
            cycle_closed,
            cycle_ema,
            bars[0],
            bars[1],
            tolerance,
            tick,
        )
    ):
        return None
    anchor = bull_ema_pullback_start(cycle_closed, cycle_ema, tolerance)
    if anchor <= 0:
        return None
    if engine.bull.active and not engine.bull.locked:
        return None
    if engine.bull.active and engine.bull.locked and anchor == engine.bull.pullback_start_time:
        return None

    init_pullback_state(engine.bull)
    engine.bull.active = True
    engine.bull.pullback_id = f"EMA-PB-{anchor}"
    engine.bull.pullback_start_time = anchor
    engine.bull.pullback_start_high = max(float(bars[0]["high"]), float(bars[1]["high"]))
    engine.bull.attempt_count = 1
    engine.bull.h_state = "H1"
    engine.bull.attempt_active = True
    engine.bull.down_leg_active = False
    engine.bull.current_attempt_high = float(bars[0]["high"])
    engine.bull_pullback_bars = [bars[0]]

    if cheap:
        # 预热阶段只保留"建立 H1 周期"这个状态效果，不算目标/止损。
        return Decision(
            direction="BUY",
            route="EMA_H23",
            route_code=ROUTE_EMA_RECOVERY,
            reason="WARMUP_STATE_ONLY",
        )

    resistance = nearest_valid_buy_resistance(
        bars, bars, float(bars[0]["high"]) + tick, tick, anchor, True
    )
    stop_selection = select_structure_stop(bars, False)
    plan = build_buy_plan(bars[0], stop_selection.anchor, tick, broker_min_distance, ask, resistance)
    if plan.valid and has_immediate_bull_obstacle(
        bars[0], bars[1], bars[2], plan.entry, plan.planned_r
    ):
        plan.valid = False
        plan.reason_code = "PLAN_REJECT_IMMEDIATE_M5_RESISTANCE_LT_1R"
    out = Decision(
        direction="BUY",
        route="EMA_H23",
        route_code=ROUTE_EMA_RECOVERY,
        signal_type="EMA_RECOVERY_BUY",
        attempt_no=1,
        stop_source=stop_selection.source,
        entry=plan.entry,
        sl=plan.final_stop,
        risk=plan.planned_r,
        rr=plan.target_r,
        tp1=resistance,
        tp2=plan.entry + 2.0 * plan.planned_r if plan.planned_r > 0 else 0.0,
        targets={
            "resistance": resistance,
            "stop_anchor": stop_selection.anchor,
            "stop_source": stop_selection.source,
            "plan_reason": plan.reason_code,
            "target_r": plan.target_r,
        },
        signal_bar_range=_range(bars[0]),
    )
    out.action = "OPEN" if plan.valid else "WAIT"
    out.reason = plan.reason_code
    return out


def _try_independent_bear_ema_recovery(
    engine: EngineState,
    bars: list[dict[str, Any]],
    m15: M15Context,
    params: dict[str, Any],
    tick: float,
    bid: float,
    broker_min_distance: float,
    cheap: bool = False,
) -> Decision | None:
    """对应 V3103TryIndependentBearEMARecovery（空头侧 C3 bootstrap）。

    EA 在每次更新空头状态前会先走这一条：如果出现"反弹后重新转空"的恢复 K 线，
    它会**重置**当前空头周期并按 H1 重新开始。这一条是跨周期记忆的关键，
    少了它重放会一直停在旧的 H3 锁定状态（2026-09-15 就是这样）。
    """
    atr = atr_from_newest(bars, 14)
    if atr <= 0.0:
        return None
    tolerance = float(params["location_tolerance_atr"]) * atr
    cycle_closed = bars[1 : 1 + EMA_CYCLE_BARS]
    cycle_ema = m5_ema20_cycle(bars)
    if cycle_ema is None:
        return None
    cycle_falling = bear_ema_overall_direction(cycle_ema)
    real_rally = has_cycle_bear_ema_rally(cycle_closed, cycle_ema, tolerance)
    if not bear_ema_recovery(
        m15.allow_sell,
        m15.hard_exhaustion_bear,
        m15.protected_high_intact,
        cycle_falling,
        real_rally,
        cycle_closed,
        cycle_ema,
        bars[0],
        bars[1],
        tolerance,
        tick,
    ):
        return None
    anchor = bear_ema_rally_start(cycle_closed, cycle_ema, tolerance)
    if anchor <= 0:
        return None
    if engine.bear.active and not engine.bear.locked:
        return None
    if engine.bear.active and engine.bear.locked and anchor == engine.bear.pullback_start_time:
        return None

    init_bear_pullback_state(engine.bear)
    engine.bear.active = True
    engine.bear.pullback_id = f"EMA-PB-S-{anchor}"
    engine.bear.pullback_start_time = anchor
    engine.bear.pullback_start_low = min(float(bars[0]["low"]), float(bars[1]["low"]))
    engine.bear.attempt_count = 1
    engine.bear.h_state = "H1"
    engine.bear.attempt_active = True
    engine.bear.up_leg_active = False
    engine.bear.current_attempt_low = float(bars[0]["low"])
    engine.bear_pullback_bars = [bars[0]]

    if cheap:
        return Decision(
            direction="SELL",
            route="EMA_L23",
            route_code=ROUTE_EMA_RECOVERY,
            reason="WARMUP_STATE_ONLY",
        )

    support = nearest_valid_sell_support(
        bars, bars, float(bars[0]["low"]) - tick, tick, anchor, True
    )
    stop_selection = select_structure_stop(bars, True)
    plan = build_sell_plan(bars[0], stop_selection.anchor, tick, broker_min_distance, bid, support)
    out = Decision(
        direction="SELL",
        route="EMA_L23",
        route_code=ROUTE_EMA_RECOVERY,
        signal_type="EMA_RECOVERY_SELL",
        attempt_no=1,
        stop_source=stop_selection.source,
        entry=plan.entry,
        sl=plan.final_stop,
        risk=plan.planned_r,
        rr=plan.target_r,
        tp1=support,
        tp2=plan.entry - 2.0 * plan.planned_r if plan.planned_r > 0 else 0.0,
        targets={
            "support": support,
            "stop_anchor": stop_selection.anchor,
            "stop_source": stop_selection.source,
            "plan_reason": plan.reason_code,
            "target_r": plan.target_r,
        },
        signal_bar_range=_range(bars[0]),
    )
    out.action = "OPEN" if plan.valid else "WAIT"
    out.reason = plan.reason_code
    return out


def _evaluate_sell_bar(
    engine: EngineState,
    bars: list[dict[str, Any]],
    m15: M15Context,
    m15_bars: list[dict[str, Any]],
    params: dict[str, Any],
    tick: float,
    bid: float,
    broker_min_distance: float,
    cheap: bool = False,
) -> Decision:
    out = Decision()
    bars = bars[:M5_EVAL_BARS]
    out.signal_bar_range = _range(bars[0])
    atr = atr_from_newest(bars, 14)
    if atr <= 0.0:
        out.reason = "NO_ATR"
        return out
    tolerance = float(params["location_tolerance_atr"]) * atr

    bootstrap = _try_independent_bear_ema_recovery(
        engine, bars, m15, params, tick, bid, broker_min_distance, cheap=cheap
    )
    if bootstrap is not None:
        return bootstrap

    if engine.bear.active and confirm_bear_impulse(
        bars[1], bars[0], engine.bear.pullback_start_low, atr, tick
    ):
        advance_bear_pullback_state(engine.bear, "NEW_IMPULSE", bar_time(bars[0]))
        engine.bear_impulse_confirmed = True
        engine.bear_impulse_low = min(float(bars[1]["low"]), float(bars[0]["low"]))
        engine.bear_pullback_bars = []
        out.reason = "NEW_BEAR_IMPULSE"
        return out

    if not engine.bear.active and not engine.bear_impulse_confirmed:
        prior_low = float(bars[2]["low"])
        for i in range(3, min(12, len(bars))):
            prior_low = min(prior_low, float(bars[i]["low"]))
        if (
            float(bars[1]["close"])
            <= prior_low - max(tick, float(params["impulse_buffer_atr"]) * atr)
            and body_range_ratio(bars[1]) >= 0.60
            and close_location(bars[1]) <= 0.25
            and float(bars[0]["close"]) < prior_low
        ):
            engine.bear_impulse_confirmed = True
            engine.bear_impulse_low = min(float(bars[1]["low"]), float(bars[0]["low"]))

    if (
        not engine.bear.active
        and engine.bear_impulse_confirmed
        and is_rally_start(bars[1], bars[0], tick)
    ):
        advance_bear_pullback_state(
            engine.bear,
            "PULLBACK_START",
            bar_time(bars[0]),
            min(engine.bear_impulse_low, float(bars[0]["low"])),
        )
        engine.bear_pullback_bars = []
        _append_pullback_bar(engine.bear_pullback_bars, bars[0])
        out.reason = "PULLBACK_STARTED"
        return out

    if not engine.bear.active or engine.bear.locked:
        out.reason = "PULLBACK_IDLE"
        return out

    _append_pullback_bar(engine.bear_pullback_bars, bars[0])
    range_like = False
    if len(engine.bear_pullback_bars) >= int(params["m5_range_min_bars"]):
        range_like = evaluate_range_like(
            engine.bear_pullback_bars, int(params["m5_range_min_bars"])
        ).is_range_like

    signal = classify_bear_signal(bars[0], bars[1:4])
    impulse_range = max(
        0.0, engine.m15_impulse_high_sell - engine.m15_impulse_low_sell
    )
    levels = {
        "fib236": engine.m15_impulse_low_sell + 0.236 * impulse_range,
        "fib382": engine.m15_impulse_low_sell + 0.382 * impulse_range,
        "ema20": ema_from_newest(bars, 20),
        "breakout_retest": engine.m15_impulse_low_sell,
    }
    location = evaluate_location_bar(
        bars[0], atr, levels, float(params["location_tolerance_atr"])
    )
    advance_bear_from_closed_bar(engine.bear, bars[1], bars[0], tick, signal.valid)

    recent_closed = bars[1:4]
    recent_ema = recent_m5_ema20(bars)
    ema_ready = recent_ema is not None
    ema_falling = bool(ema_ready and ema_from_newest(bars, 20) < recent_ema[0])
    cycle_closed = bars[1 : 1 + EMA_CYCLE_BARS]
    cycle_ema = m5_ema20_cycle(bars)
    cycle_ready = cycle_ema is not None
    cycle_falling = bool(cycle_ready and bear_ema_overall_direction(cycle_ema))

    l2_candidate = engine.bear.h2_candidate_time == bar_time(bars[0])
    original_l2 = bool(
        not range_like
        and engine.bear.h_state == "H2"
        and engine.bear.h2_signal_time == bar_time(bars[0])
        and location.valid
    )
    recovery_break = bool(
        not range_like
        and location.valid
        and bear_recovery_break(l2_candidate, bars[0], bars[1], tick)
    )
    real_rally = bool(
        cycle_ready and has_cycle_bear_ema_rally(cycle_closed, cycle_ema, tolerance)
    )
    ema_recovery = bool(
        cycle_ready
        and bear_ema_recovery(
            m15.allow_sell,
            m15.hard_exhaustion_bear,
            m15.protected_high_intact,
            cycle_falling,
            real_rally,
            cycle_closed,
            cycle_ema,
            bars[0],
            bars[1],
            tolerance,
            tick,
        )
    )
    compression_break = bool(
        ema_ready
        and bear_compression_break(
            m15.allow_sell,
            m15.hard_exhaustion_bear,
            m15.protected_high_intact,
            ema_falling,
            range_like,
            recent_closed,
            recent_ema,
            bars[0],
            tolerance,
            tick,
        )
    )
    route_code = select_m5_route(original_l2, recovery_break, ema_recovery, compression_break)
    out.location = {
        "valid": location.valid,
        "fib236": location.location_fib236,
        "fib382": location.location_fib382,
        "ema20": location.location_ema20,
        "breakout_retest": location.location_breakout_retest,
        "range_like": range_like,
        "h_state": engine.bear.h_state,
        "attempt_count": engine.bear.attempt_count,
    }
    if route_code == ROUTE_NONE:
        if range_like:
            advance_bear_pullback_state(engine.bear, "RANGE_LIKE", bar_time(bars[0]))
        out.reason = "NO_ROUTE"
        return out
    if cheap:
        out.route_code = route_code
        out.direction = "SELL"
        out.reason = "WARMUP_STATE_ONLY"
        return out

    target_anchor = 0
    if route_code == ROUTE_EMA_RECOVERY:
        target_anchor = bear_ema_rally_start(cycle_closed, cycle_ema, tolerance)
    elif route_code == ROUTE_EMA_COMPRESSION_BREAK:
        target_anchor = recent_ema_interaction_start(recent_closed, recent_ema, tolerance)
    route_b = route_code in (ROUTE_EMA_RECOVERY, ROUTE_EMA_COMPRESSION_BREAK)
    exclude_internal = bool(route_b and target_anchor > 0)

    attempt_no = engine.bear.attempt_count
    support = nearest_valid_sell_support(
        bars, m15_bars, float(bars[0]["low"]) - tick, tick, target_anchor, exclude_internal
    )
    stop_selection = select_structure_stop(bars, True)
    plan = build_sell_plan(bars[0], stop_selection.anchor, tick, broker_min_distance, bid, support)

    signal_type = {
        ROUTE_H2_ORIGINAL: (
            "BEAR_PIN_BAR"
            if signal.pin
            else "STRONG_BEAR_BAR"
            if signal.strong_bear
            else "BEAR_ENGULF_THREE_BULL"
        ),
        ROUTE_H2_RECOVERY_BREAK: "BEAR_RECOVERY_BREAK",
        ROUTE_EMA_RECOVERY: "EMA_RECOVERY_SELL",
        ROUTE_EMA_COMPRESSION_BREAK: "EMA_COMPRESSION_BREAK_SELL",
    }[route_code]
    out.route_code = route_code
    out.route = (
        "FIB_PA"
        if route_code in (ROUTE_H2_ORIGINAL, ROUTE_H2_RECOVERY_BREAK)
        else ("EMA_L23" if route_code == ROUTE_EMA_RECOVERY else "EMA_COMPRESSION")
    )
    out.direction = "SELL"
    out.signal_type = signal_type
    out.attempt_no = attempt_no
    out.stop_source = stop_selection.source
    out.entry = plan.entry
    out.sl = plan.final_stop
    out.risk = plan.planned_r
    out.rr = plan.target_r
    out.tp1 = support
    out.tp2 = plan.entry - 2.0 * plan.planned_r if plan.planned_r > 0 else 0.0
    out.targets = {
        "support": support,
        "stop_anchor": stop_selection.anchor,
        "stop_source": stop_selection.source,
        "plan_reason": plan.reason_code,
        "target_r": plan.target_r,
    }
    out.action = "OPEN" if plan.valid else "WAIT"
    out.reason = plan.reason_code
    return out


@dataclass
class Simulation:
    decision: Decision
    m15: M15Context
    buy_state: dict[str, Any]
    sell_state: dict[str, Any]
    indicators: dict[str, float]
    reference_plan: dict[str, Any]
    m5_bars_used: int = 0
    m15_history_bars: int = 0
    m5_history_bars: int = 0
    m5_warmup_bars: int = 0


def _normalise_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    out = dict(parameters or {})
    out.setdefault("location_tolerance_atr", 0.15)
    out.setdefault("impulse_buffer_atr", 0.10)
    out.setdefault("m5_range_min_bars", 5)
    out.setdefault("max_signal_bar_usd", 10.0)
    out.setdefault("max_sl_atr", 2.0)
    out.setdefault("max_spread_atr_ratio", 0.08)
    out.setdefault("absolute_max_spread_points", 0)
    out.setdefault("min_rr_to_tp1", 0.8)
    out.setdefault("m5_min_window", 41)
    return out


def _prefer(left: Decision, right: Decision) -> Decision:
    """同一根 K 线两个方向都有结论时的取舍：能开仓的优先，其次更接近开仓的。"""
    if left.action == "OPEN" and right.action != "OPEN":
        return left
    if right.action == "OPEN" and left.action != "OPEN":
        return right
    if left.action == "OPEN" and right.action == "OPEN":
        return left if left.rr >= right.rr else right
    if right.route_code != ROUTE_NONE and left.route_code == ROUTE_NONE:
        return right
    return left


def simulate(
    bars: list[dict[str, Any]],
    m15_bars: list[dict[str, Any]],
    parameters: dict[str, Any],
    bid: float,
    ask: float,
    tick_size: float,
    broker_min_distance: float = 0.0,
    m15_history: list[dict[str, Any]] | None = None,
    m15_contexts: dict[int, M15Context] | None = None,
    m5_history: list[dict[str, Any]] | None = None,
    replay_anchor: int = 0,
    m15_replay_anchor: int = 0,
) -> Simulation:
    """重放 M5 状态机，返回最后一根 K 线的独立判定。

    * 决策窗口 = EA 载荷里的 200 根 M5（与 input_hash 同一份数据）；
    * 若提供更长的 M5 历史（默认由 MT5 取 1500 根），先用它把微周期状态机预热，
      决策窗口内再逐根做完整评估。这样两边跨日/跨周期的记忆起点更接近。
    """
    params = _normalise_parameters(parameters)
    tick = float(tick_size or 0.0)
    # 审计载荷是"由旧到新"，状态机与指标层统一用"最新在前"。
    payload_bars = with_int_time(bars)
    bars = list(reversed(payload_bars))
    m15_bars = list(reversed(with_int_time(m15_bars)))
    history = list(reversed(with_int_time(m15_history))) if m15_history else m15_bars
    contexts = (
        m15_contexts
        if m15_contexts is not None
        else build_m15_context_series(history, float(ask) - float(bid), tick, m15_replay_anchor)
    )

    decision_bars = len(payload_bars)
    series = merge_m5_history(payload_bars, m5_history) if m5_history else payload_bars
    warmup_bars = max(0, len(series) - decision_bars)

    engine = EngineState()
    chronological = list(series)
    min_window = max(41, int(params["m5_min_window"]))
    decision = Decision(reason="NO_CLOSED_BAR")
    last_context = M15Context()
    for index in range(len(chronological)):
        if index + 1 < min_window:
            continue
        in_decision_window = index >= warmup_bars
        window = list(reversed(chronological[: index + 1]))
        context = contexts.get(m15_open_for_m5(bar_time(window[0])))
        if context is None:
            continue
        if in_decision_window:
            last_context = context
        if replay_anchor and bar_time(window[0]) == int(replay_anchor):
            # EA 在这根之前把周期记忆清空了（重挂/重启/新一轮冲动）：
            # 我们的重放也在这一刻归零，之后两边从同一个空状态往前走。
            init_pullback_state(engine.bull)
            engine.bull_impulse_confirmed = False
            engine.bull_impulse_high = 0.0
            engine.bull_pullback_bars = []
            engine.bull_reset_reason = "EA_STATE_EMPTY"
            init_bear_pullback_state(engine.bear)
            engine.bear_impulse_confirmed = False
            engine.bear_impulse_low = 0.0
            engine.bear_pullback_bars = []
            engine.bear_reset_reason = "EA_STATE_EMPTY"
        engine.m15_impulse_low = context.impulse_low
        engine.m15_impulse_high = context.impulse_high
        engine.m15_impulse_low_sell = context.impulse_low
        engine.m15_impulse_high_sell = context.impulse_high
        # 对应 V310UpdateM15Context：方向相反或过渡时清空该侧的周期状态。
        reset_bull = context.primary in (PRIMARY_BEAR, PRIMARY_TRANSITION_FROM_BULL, PRIMARY_TRANSITION_FROM_BEAR)
        reset_bear = context.primary in (PRIMARY_BULL, PRIMARY_TRANSITION_FROM_BULL, PRIMARY_TRANSITION_FROM_BEAR)
        if reset_bull and (engine.bull.active or engine.bull_impulse_confirmed):
            init_pullback_state(engine.bull)
            engine.bull_impulse_confirmed = False
            engine.bull_impulse_high = 0.0
            engine.bull_pullback_bars = []
            engine.bull_reset_reason = "M15_DIRECTION_INVALID"
        if reset_bear and (engine.bear.active or engine.bear_impulse_confirmed):
            init_bear_pullback_state(engine.bear)
            engine.bear_impulse_confirmed = False
            engine.bear_impulse_low = 0.0
            engine.bear_pullback_bars = []
            engine.bear_reset_reason = "M15_DIRECTION_INVALID"
        sell_decision = (
            _evaluate_sell_bar(
                engine, window, context, m15_bars, params, tick, float(bid), broker_min_distance,
                cheap=not in_decision_window,
            )
            if context.allow_sell
            else Decision(reason="M15_BLOCK_SELL")
        )
        bull_decision = (
            _evaluate_bull_bar(
                engine, window, context, m15_bars, params, tick, float(ask), broker_min_distance,
                cheap=not in_decision_window,
            )
            if context.allow_buy
            else Decision(reason="M15_BLOCK_BUY")
        )
        if in_decision_window:
            decision = _prefer(bull_decision, sell_decision)

    indicators = _indicators(bars, last_context)
    reference = {
        "action": decision.action,
        "direction": decision.direction if decision.action == "OPEN" else "NONE",
        "route": decision.route if decision.action == "OPEN" else "NONE",
        "entry": decision.entry if decision.action == "OPEN" else None,
        "sl": decision.sl if decision.action == "OPEN" else None,
        "tp1": decision.tp1 if decision.action == "OPEN" else None,
        "tp2": decision.tp2 if decision.action == "OPEN" else None,
        "rr_to_tp1": decision.rr if decision.action == "OPEN" else 0.0,
        "reason": decision.reason,
        "signal_type": decision.signal_type,
        "attempt_no": decision.attempt_no,
        "stop_source": decision.stop_source,
    }
    return Simulation(
        decision=decision,
        m15=last_context,
        buy_state=_side_snapshot(engine.bull, engine.bull_reset_reason),
        sell_state=_side_snapshot(engine.bear, engine.bear_reset_reason),
        indicators=indicators,
        reference_plan=reference,
        m5_bars_used=len(bars),
        m15_history_bars=len(history),
        m5_history_bars=len(series),
        m5_warmup_bars=warmup_bars,
    )


def _indicators(bars: list[dict[str, Any]], m15: M15Context) -> dict[str, float]:
    closes = [float(bar["close"]) for bar in bars]

    def _ema(values: list[float], period: int) -> list[float]:
        alpha = 2.0 / (period + 1.0)
        out = [values[0]]
        for value in values[1:]:
            out.append(alpha * value + (1.0 - alpha) * out[-1])
        return out

    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    main = [a - b for a, b in zip(fast, slow)]
    signal_line: list[float] = []
    running = 0.0
    for index, value in enumerate(main):
        running += value
        if index >= 9:
            running -= main[index - 9]
        signal_line.append(running / min(9, index + 1))
    return {
        "ema20": ema_from_newest(bars, 20),
        "atr14": atr_from_newest(bars, 14),
        "macd_hist": main[-1] - signal_line[-1],
        "m15_ema20": m15.ema20,
        "m15_atr14": m15.atr14,
        "m15_move_atr": m15.move_atr,
    }


# --------------------------------------------------------------------------
# M15 历史（供审计服务使用；缺失时退化为载荷自带的 100 根）
# --------------------------------------------------------------------------


def load_m15_history(
    symbol: str = "XAUUSD.s", count: int = 1500, terminal_path: str | None = None
) -> list[dict[str, Any]]:
    """从 MT5 读取 M15 历史（由旧到新），用于 M15 主结构状态机的因果重放。"""
    return _load_rates("XAUUSD_M15", symbol, count, terminal_path)


def load_m5_history(
    symbol: str = "XAUUSD.s", count: int = M5_WARMUP_BARS, terminal_path: str | None = None
) -> list[dict[str, Any]]:
    """从 MT5 读取 M5 历史（由旧到新），用于 M5 微周期的长预热重放。"""
    return _load_rates("XAUUSD_M5", symbol, count, terminal_path)


def _load_rates(
    timeframe_name: str, symbol: str, count: int, terminal_path: str | None = None
) -> list[dict[str, Any]]:
    import MetaTrader5 as mt5

    from tools.ai_trade_manager import TERMINAL_PATH

    path = terminal_path or TERMINAL_PATH
    if not mt5.initialize(path, timeout=10000):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        timeframe = mt5.TIMEFRAME_M15 if timeframe_name == "XAUUSD_M15" else mt5.TIMEFRAME_M5
        rates = mt5.copy_rates_from_pos(str(symbol), timeframe, 1, int(count))
    finally:
        mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no rates returned for {timeframe_name}")
    return [
        {
            "time": int(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for row in rates
    ]


_M15_HISTORY_CACHE: dict[str, Any] = {"key": "", "bars": None, "loaded_at": 0.0}
_M15_CONTEXT_CACHE: dict[str, Any] = {"key": None, "contexts": None}
_FACTS_CACHE: dict[str, Any] = {"key": None, "facts": None}


def default_m15_history(
    symbol: str = "XAUUSD.s",
    count: int = 1500,
    max_age_seconds: float = 900.0,
    terminal_path: str | None = None,
) -> list[dict[str, Any]] | None:
    """带缓存的 M15 历史（默认 15 分钟刷新一次）；失败时返回 None。"""
    now = time.time()
    cache = _M15_HISTORY_CACHE
    if (
        cache["bars"] is not None
        and cache["key"] == f"{symbol}|{count}|{terminal_path}"
        and now - float(cache["loaded_at"]) < max_age_seconds
    ):
        return cache["bars"]
    try:
        bars = load_m15_history(symbol, count, terminal_path)
    except Exception:  # noqa: BLE001 - MT5 不可用时退化为载荷自带的 100 根
        return cache["bars"]
    cache.update({"key": f"{symbol}|{count}|{terminal_path}", "bars": bars, "loaded_at": now})
    return bars


_M5_HISTORY_CACHE: dict[str, Any] = {"key": "", "bars": None, "loaded_at": 0.0}


def default_m5_history(
    symbol: str = "XAUUSD.s",
    count: int = M5_WARMUP_BARS,
    max_age_seconds: float = 900.0,
    terminal_path: str | None = None,
) -> list[dict[str, Any]] | None:
    """带缓存的 M5 历史（默认 15 分钟刷新）；MT5 不可用时返回 None。"""
    now = time.time()
    cache = _M5_HISTORY_CACHE
    if (
        cache["bars"] is not None
        and cache["key"] == f"{symbol}|{count}|{terminal_path}"
        and now - float(cache["loaded_at"]) < max_age_seconds
    ):
        return cache["bars"]
    try:
        bars = load_m5_history(symbol, count, terminal_path)
    except Exception:  # noqa: BLE001 - MT5 不可用时退化为载荷自带的 200 根
        return cache["bars"]
    cache.update({"key": f"{symbol}|{count}|{terminal_path}", "bars": bars, "loaded_at": now})
    return bars


def merge_m5_history(
    payload_bars: list[dict[str, Any]], history_bars: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """把 MT5 长历史与 EA 载荷合并：同一时间的 K 线以 **载荷** 为准。

    EA 写出的 200 根是它当时实际看到的数据（也是 input_hash 的组成部分），
    所以决策窗口必须保持载荷口径；更早的历史用于把微周期状态机预热起来。
    返回"由旧到新"的序列。
    """
    payload = {bar_time(bar): dict(bar) for bar in payload_bars if bar_time(bar)}
    if not payload:
        return []
    cut = max(payload)
    merged: dict[int, dict[str, Any]] = {}
    for bar in history_bars or []:
        when = bar_time(bar)
        if not when or when > cut:
            continue
        merged[when] = {
            "time": when,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
        }
    merged.update({when: dict(bar) for when, bar in payload.items()})
    return [merged[key] for key in sorted(merged)]


def replay_anchor_from_ea_trace(trace: dict[str, Any] | None) -> int:
    """从 EA 的上一根 Trace 判断"EA 状态是否被清零"，返回需要重新起算的时间。

    两种情况 EA 的微周期记忆是空的：
      1. EA 重挂/终端重启（primary 回到 RANGE，两侧字段被整段清零）；
      2. 出现"新一轮冲动"（NEW_IMPULSE）把旧周期清掉，或周期本来就还没开始。
    只要两侧都没有活跃周期/锁定/尝试次数，就说明 EA 此刻的记忆是空的，
    从这一刻起以空状态重新重放，两边起点才一致。返回 0 表示不需要重新起算。
    """
    if not isinstance(trace, dict) or not trace.get("m5_time"):
        return 0
    for side in ("buy_state", "sell_state"):
        state = trace.get(side) or {}
        if bool(state.get("pullback_active")) or bool(state.get("locked")):
            return 0
        if int(state.get("attempt_count") or 0) > 0:
            return 0
    return bar_time({"time": trace["m5_time"]})


def m15_anchor_from_ea_trace(trace: dict[str, Any] | None) -> int:
    """只有"EA 真的被重挂/重启"时才重启 M15 主结构记忆。

    判据：主结构回到 RANGE，且两侧状态字段被整段清零（h_state 是空字符串，
    而不是规则里的 "NONE"）。"新一轮冲动"只会清 M5 周期，不会重置 M15。
    """
    if not isinstance(trace, dict) or not trace.get("m5_time"):
        return 0
    if str(trace.get("primary_state") or "") != "PRIMARY_RANGE":
        return 0
    for side in ("buy_state", "sell_state"):
        state = trace.get(side) or {}
        if str(state.get("h_state") or "").strip() != "":
            return 0
        if bool(state.get("pullback_active")) or bool(state.get("locked")):
            return 0
        if int(state.get("attempt_count") or 0) > 0:
            return 0
    return bar_time({"time": trace["m5_time"]})


def _cached_contexts(
    history: list[dict[str, Any]], spread_price: float, tick: float, replay_anchor: int = 0
) -> dict[int, M15Context]:
    key = (
        len(history),
        bar_time(history[0]) if history else 0,
        bar_time(history[-1]) if history else 0,
        round(float(spread_price), 4),
        round(float(tick), 6),
        int(replay_anchor or 0),
    )
    cache = _M15_CONTEXT_CACHE
    if cache["contexts"] is not None and cache["key"] == key:
        return cache["contexts"]
    contexts = build_m15_context_series(history, spread_price, tick, replay_anchor)
    cache.update({"key": key, "contexts": contexts})
    return contexts


# --------------------------------------------------------------------------
# 公开 API：facts / gates / reference plan / 合规闸门
# --------------------------------------------------------------------------


def calculate_independent_facts(
    independent_input: dict[str, Any],
    m15_history: list[dict[str, Any]] | None = None,
    broker_min_distance: float = 0.0,
    replay_anchor: int = 0,
    m15_replay_anchor: int = 0,
) -> dict[str, Any]:
    """返回喂给 AI 的、只含"规则事实"的字典（不含最终 Entry/SL 计划）。"""
    cache_key = (
        str(independent_input.get("input_hash") or independent_input.get("snapshot_id") or ""),
        round(float(broker_min_distance or 0.0), 4),
        int(len(m15_history)) if m15_history else 0,
    )
    cache = _FACTS_CACHE
    if cache["facts"] is not None and cache["key"] == cache_key:
        return cache["facts"]

    bars = independent_input["bars"]
    m15_bars = independent_input.get("m15_bars") or []
    parameters = independent_input.get("parameters") or {}
    tick = float(independent_input.get("tick_size") or 0.0)
    bid = float(independent_input.get("bid") or 0.0)
    ask = float(independent_input.get("ask") or 0.0)
    symbol = str(independent_input.get("symbol") or "XAUUSD.s")
    if m15_history is None:
        m15_history = default_m15_history(symbol)
    m5_history = default_m5_history(symbol)
    contexts = (
        _cached_contexts(m15_history, ask - bid, tick, m15_replay_anchor) if m15_history else None
    )
    sim = simulate(
        bars,
        m15_bars,
        parameters,
        bid,
        ask,
        tick,
        broker_min_distance=broker_min_distance,
        m15_history=m15_history,
        m15_contexts=contexts,
        m5_history=m5_history,
        replay_anchor=replay_anchor,
        m15_replay_anchor=m15_replay_anchor,
    )
    decision = sim.decision
    m15 = sim.m15
    m5_newest = list(reversed(with_int_time(bars)))
    signal_bar = m5_newest[0] if m5_newest else {}
    m5_atr = float(sim.indicators.get("atr14") or 0.0)
    impulse_range = max(0.0, m15.impulse_high - m15.impulse_low)
    retracement = 0.0
    if impulse_range > 0.0 and signal_bar:
        if decision.direction == "SELL":
            retracement = (float(signal_bar["high"]) - m15.impulse_low) / impulse_range * 100.0
        else:
            retracement = (m15.impulse_high - float(signal_bar["low"])) / impulse_range * 100.0
    structure_target = decision.tp1 if decision.action == "OPEN" else 0.0
    facts = {
        "rule_version": independent_input.get("rule_version"),
        "snapshot_id": independent_input.get("snapshot_id"),
        "m5_time": independent_input.get("m5_time"),
        "symbol": independent_input.get("symbol"),
        "indicators": {
            "ema20": float(sim.indicators.get("ema20") or 0.0),
            "atr14": m5_atr,
            "macd_hist": float(sim.indicators.get("macd_hist") or 0.0),
            "m15_ema20": m15.ema20,
            "m15_atr14": m15.atr14,
        },
        "m15_trend": m15.as_facts(),
        "m5_state": {
            "buy": sim.buy_state,
            "sell": sim.sell_state,
            "signal_bar_range": float(signal_bar.get("high", 0.0)) - float(signal_bar.get("low", 0.0))
            if signal_bar
            else 0.0,
            "location": decision.location,
            "range_like": bool((decision.location or {}).get("range_like", False)),
            "h_state": (decision.location or {}).get("h_state", "NONE"),
            "attempt_count": int((decision.location or {}).get("attempt_count", 0) or 0),
            "retracement": retracement,
        },
        "price_action": {
            "route": decision.route,
            "route_code": decision.route_code,
            "route_label_cn": route_label_cn(decision.route),
            "signal_type": decision.signal_type,
            "fib_path_valid": decision.route == "FIB_PA",
            "ema_path_valid": decision.route in ("EMA_H23", "EMA_L23"),
            "compression_path_valid": decision.route == "EMA_COMPRESSION",
            "attempt_no": decision.attempt_no,
            "reason": decision.reason,
        },
        "structure": {
            "direction": decision.direction if decision.route != "NONE" else m15.direction,
            "m15_direction": m15.direction,
            "deterministic_direction": decision.direction,
            "structure_target": structure_target,
            "stop_source": decision.stop_source,
        },
        "market": {
            "bid": bid,
            "ask": ask,
            "spread": ask - bid,
            "tick_size": tick,
        },
        "quality": {
            "m5_bars_used": sim.m5_bars_used,
            "m15_history_bars": sim.m15_history_bars,
            "m5_history_bars": sim.m5_history_bars,
            "m5_warmup_bars": sim.m5_warmup_bars,
            "replay_anchor": int(replay_anchor or 0),
            "m15_replay_anchor": int(m15_replay_anchor or 0),
            "independent_m15_replay": True,
        },
    }
    cache.update({"key": cache_key, "facts": facts})
    return facts


def _valid_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number)


def evaluate_gate_facts(
    independent_input: dict[str, Any], facts: dict[str, Any]
) -> list[dict[str, Any]]:
    """按 V3.10.9 规则给出 G01-G10 事实（用于参考计划，不是 AI 合规判定）。"""
    parameters = independent_input.get("parameters") or {}
    state = independent_input.get("account_state") or {}
    m5_atr = float(facts.get("indicators", {}).get("atr14") or 0.0)
    m15 = facts.get("m15_trend", {})
    m5_state = facts.get("m5_state", {})
    pa = facts.get("price_action", {})
    structure = facts.get("structure", {})
    market = facts.get("market", {})
    tick = float(market.get("tick_size") or 0.0)
    gates: list[dict[str, Any]] = []

    def add(gate_id: str, result: str, reason: str, values: dict[str, Any] | None = None) -> None:
        gates.append(
            {
                "id": gate_id,
                "name": GATE_NAME_CN.get(gate_id, gate_id),
                "result": result,
                "reason": reason,
                "values": values or {},
            }
        )

    time_allowed = (
        bool(state.get("trade_allowed", True))
        and not bool(state.get("risk_locked", False))
        and not bool(state.get("weekend_blocked", False))
        and not bool(state.get("rollover_blocked", False))
    )
    add(
        "G01",
        "PASS" if time_allowed else "FAIL",
        "交易时段允许开仓" if time_allowed else "交易时段或风险锁阻止开仓",
    )

    allow_buy = bool(m15.get("allow_buy"))
    allow_sell = bool(m15.get("allow_sell"))
    direction = str(structure.get("deterministic_direction") or "NONE")
    if direction == "BUY" and allow_buy:
        add("G03", "PASS", f"M15主结构放行多头（{m15.get('primary_state')}）")
    elif direction == "SELL" and allow_sell:
        add("G03", "PASS", f"M15主结构放行空头（{m15.get('primary_state')}）")
    else:
        add(
            "G03",
            "FAIL",
            f"当前没有可放行的 M15 主结构方向（{m15.get('primary_state')}/{m15.get('local_phase')}）",
            {"allow_buy": allow_buy, "allow_sell": allow_sell},
        )

    # 推动幅度必须用"该方向自己的"口径：做多看多头推动，做空看空头推动。
    move_atr, move_label = _directional_move_atr(m15, structure.get("deterministic_direction"))
    minimum = float(parameters.get("min_impulse_atr", 1.5) or 1.5)
    add(
        "G04",
        "PASS" if move_atr >= minimum else "FAIL",
        f"{move_label}推动{move_atr:.2f}ATR，要求≥{minimum}ATR"
        if move_atr >= minimum
        else f"{move_label}推动{move_atr:.2f}ATR，不足{minimum}ATR",
        {"move_atr": move_atr, "minimum": minimum, "direction_scope": move_label},
    )

    location = m5_state.get("location") or {}
    retracement = float(m5_state.get("retracement") or 0.0)
    fib_min = float(parameters.get("fib_min", 38.2) or 38.2)
    fib_max = float(parameters.get("fib_max", 61.8) or 61.8)
    context_ok = bool(location.get("valid")) and fib_min <= retracement <= fib_max
    add(
        "G05",
        "PASS" if context_ok else "FAIL",
        f"回调{retracement:.1f}%位于{fib_min}-{fib_max}%有效区且触及有效位置"
        if context_ok
        else f"回调{retracement:.1f}%，或未触及Fib/EMA/突破回踩有效位置",
        {"retracement": retracement, "location": location},
    )

    route = str(pa.get("route") or "NONE")
    route_ok = route in ("FIB_PA", "EMA_H23", "EMA_L23", "EMA_COMPRESSION")
    add(
        "G06",
        "PASS" if route_ok else "FAIL",
        f"入场路径={route_label_cn(route)}（{pa.get('signal_type')}）"
        if route_ok
        else "四条入场路径均未成立",
        {"route": route, "signal_type": pa.get("signal_type")},
    )

    signal_range = float(m5_state.get("signal_bar_range") or 0.0)
    maximum_bar = float(parameters.get("max_signal_bar_usd", 10.0) or 10.0)
    add(
        "G07",
        "PASS" if 0.0 <= signal_range < maximum_bar else "FAIL",
        f"信号K长度{signal_range:.2f}USD，小于{maximum_bar:.2f}USD"
        if signal_range < maximum_bar
        else f"信号K长度{signal_range:.2f}USD，超过{maximum_bar:.2f}USD",
        {"signal_range": signal_range, "maximum": maximum_bar},
    )

    reference = _reference_values(independent_input, facts, broker_min_distance=0.0)
    add(
        "G08",
        "PASS" if reference["risk"] > 0 else "FAIL",
        f"止损距离{reference['risk']:.2f}USD（{reference['risk_atr']:.2f}ATR）"
        if reference["risk"] > 0
        else f"止损无效：{reference['plan_reason']}",
        {
            "entry": reference["entry"],
            "sl": reference["sl"],
            "risk": reference["risk"],
            "risk_atr": reference["risk_atr"],
            "stop_source": reference["stop_source"],
            "plan_reason": reference["plan_reason"],
        },
    )
    add(
        "G09",
        "PASS" if reference["target_r"] >= V3109_TARGET_SPACE_R else "FAIL",
        f"结构目标空间{reference['target_r']:.2f}R，要求≥{V3109_TARGET_SPACE_R:.1f}R"
        if reference["target_r"] >= V3109_TARGET_SPACE_R
        else f"结构目标空间仅{reference['target_r']:.2f}R，不足{V3109_TARGET_SPACE_R:.1f}R",
        {"tp1": reference["tp1"], "target_r": reference["target_r"]},
    )

    spread = float(market.get("spread") or 0.0)
    spread_ratio = spread / m5_atr if m5_atr > 0 else float("inf")
    max_ratio = float(parameters.get("max_spread_atr_ratio", 0.08) or 0.08)
    absolute_limit = int(parameters.get("absolute_max_spread_points", 0) or 0)
    spread_ok = spread_ratio <= max_ratio and (
        absolute_limit <= 0 or (spread / tick if tick > 0 else 0.0) <= absolute_limit
    )
    add(
        "G10",
        "PASS" if spread_ok else "FAIL",
        f"点差{spread:.2f}USD，合格" if spread_ok else f"点差{spread:.2f}USD，超过限制",
        {"spread": spread, "spread_atr_ratio": spread_ratio, "maximum": max_ratio},
    )
    return gates


def _reference_values(
    independent_input: dict[str, Any],
    facts: dict[str, Any],
    broker_min_distance: float = 0.0,
    replay_anchor: int = 0,
    m15_replay_anchor: int = 0,
) -> dict[str, Any]:
    """独立参考计划的 Entry/SL/TP（隐藏层，绝不进入 AI 载荷）。"""
    bars = independent_input["bars"]
    m15_bars = independent_input.get("m15_bars") or []
    parameters = independent_input.get("parameters") or {}
    tick = float(independent_input.get("tick_size") or 0.0)
    bid = float(independent_input.get("bid") or 0.0)
    ask = float(independent_input.get("ask") or 0.0)
    history = default_m15_history(str(independent_input.get("symbol") or "XAUUSD.s"))
    contexts = _cached_contexts(history, ask - bid, tick, m15_replay_anchor) if history else None
    sim = simulate(
        bars,
        m15_bars,
        parameters,
        bid,
        ask,
        tick,
        broker_min_distance=broker_min_distance,
        m15_history=history,
        m15_contexts=contexts,
        m5_history=default_m5_history(str(independent_input.get("symbol") or "XAUUSD.s")),
        replay_anchor=replay_anchor,
        m15_replay_anchor=m15_replay_anchor,
    )
    decision = sim.decision
    atr = float(facts.get("indicators", {}).get("atr14") or sim.indicators.get("atr14") or 0.0)
    risk = float(decision.risk or 0.0)
    return {
        "action": decision.action,
        "direction": decision.direction,
        "route": decision.route,
        "entry": float(decision.entry or 0.0),
        "sl": float(decision.sl or 0.0),
        "tp1": float(decision.tp1 or 0.0),
        "tp2": float(decision.tp2 or 0.0),
        "risk": risk,
        "risk_atr": risk / atr if atr > 0 else float("inf"),
        "target_r": float(decision.rr or 0.0),
        "stop_source": decision.stop_source,
        "plan_reason": decision.reason,
        "signal_type": decision.signal_type,
    }


def build_reference_plan(
    independent_input: dict[str, Any],
    m15_history: list[dict[str, Any]] | None = None,
    broker_min_distance: float = 0.0,
    replay_anchor: int = 0,
    m15_replay_anchor: int = 0,
) -> dict[str, Any]:
    """隐藏的确定性参考计划；只用于对账与合规，不进入 AI 载荷。"""
    facts = calculate_independent_facts(
        independent_input,
        m15_history,
        broker_min_distance,
        replay_anchor=replay_anchor,
        m15_replay_anchor=m15_replay_anchor,
    )
    reference = _reference_values(
        independent_input,
        facts,
        broker_min_distance,
        replay_anchor=replay_anchor,
        m15_replay_anchor=m15_replay_anchor,
    )
    passed = reference["action"] == "OPEN"
    return {
        "action": reference["action"],
        "direction": reference["direction"] if passed else "NONE",
        "route": reference["route"] if passed else "NONE",
        "entry": reference["entry"] if passed else None,
        "sl": reference["sl"] if passed else None,
        "selected_sl": reference["sl"] if passed else None,
        "protective_risk": reference["risk"] if passed else None,
        "tp1": reference["tp1"] if passed else None,
        "tp2": reference["tp2"] if passed else None,
        "rr_to_tp1": reference["target_r"],
        "signal_type": reference["signal_type"],
        "stop_source": reference["stop_source"],
        "plan_reason": reference["plan_reason"],
        "gates": evaluate_gate_facts(independent_input, facts),
        "facts": facts,
    }


def evaluate_ai_execution_compliance(
    independent_input: dict[str, Any],
    ai_decision: dict[str, Any],
    ai_existing_exposure: bool,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trader B 自己的硬规则底线（G01-G10），只决定是否允许真实下单。"""
    if facts is None:
        facts = calculate_independent_facts(independent_input)
    parameters = independent_input.get("parameters") or {}
    state = independent_input.get("account_state") or {}
    direction = str(ai_decision.get("direction", "")).strip().upper()
    route = str(ai_decision.get("route", "")).strip().upper()
    entry = float(ai_decision.get("entry") or 0.0)
    sl = float(ai_decision.get("sl") or 0.0)
    tick = float(facts.get("market", {}).get("tick_size") or independent_input.get("tick_size") or 0.0)
    atr = float(facts.get("indicators", {}).get("atr14") or 0.0)
    m15 = facts.get("m15_trend", {})
    pa = facts.get("price_action", {})
    structure = facts.get("structure", {})
    m5_state = facts.get("m5_state", {})
    market = facts.get("market", {})
    gates: list[dict[str, Any]] = []

    def add(gate_id: str, result: str, reason: str, values: dict[str, Any] | None = None) -> None:
        gates.append(
            {
                "id": gate_id,
                "name": GATE_NAME_CN.get(gate_id, gate_id),
                "result": result,
                "reason": reason,
                "values": values or {},
            }
        )

    time_allowed = (
        bool(state.get("trade_allowed", True))
        and not bool(state.get("risk_locked", False))
        and not bool(state.get("weekend_blocked", False))
        and not bool(state.get("rollover_blocked", False))
    )
    add(
        "G01",
        "PASS" if time_allowed else "FAIL",
        "允许新开仓" if time_allowed else "交易时段或风险锁阻止开仓",
    )
    add(
        "G02",
        "PASS" if not ai_existing_exposure else "FAIL",
        "本AI无持仓/挂单" if not ai_existing_exposure else "本AI已有持仓或挂单",
    )

    det_direction = str(structure.get("deterministic_direction") or "NONE").upper()
    det_route = str(pa.get("route") or "NONE").upper()
    allow_side = bool(m15.get("allow_buy")) if direction == "BUY" else bool(m15.get("allow_sell"))
    if direction not in ("BUY", "SELL"):
        add("G03", "FAIL", f"AI方向无效：{direction or '空'}")
    elif not allow_side:
        add(
            "G03",
            "FAIL",
            f"M15主结构未放行{direction}（{m15.get('primary_state')}/{m15.get('local_phase')}）",
            {"primary_state": m15.get("primary_state"), "local_phase": m15.get("local_phase")},
        )
    elif det_direction not in ("NONE", direction):
        add(
            "G03",
            "FAIL",
            f"AI方向={direction}，但独立计算方向={det_direction}",
            {"ai_direction": direction, "deterministic_direction": det_direction},
        )
    else:
        add("G03", "PASS", f"M15主结构放行{direction}，方向一致")

    # 推动幅度按 AI 申报的方向取口径（做空看空头推动，做多看多头推动）
    move_atr, move_label = _directional_move_atr(m15, direction)
    minimum = float(parameters.get("min_impulse_atr", 1.5) or 1.5)
    add(
        "G04",
        "PASS" if move_atr >= minimum else "FAIL",
        f"{move_label}推动{move_atr:.2f}ATR，要求≥{minimum}ATR",
        {"move_atr": move_atr, "minimum": minimum, "direction_scope": move_label},
    )

    location = m5_state.get("location") or {}
    retracement = float(m5_state.get("retracement") or 0.0)
    fib_min = float(parameters.get("fib_min", 38.2) or 38.2)
    fib_max = float(parameters.get("fib_max", 61.8) or 61.8)
    context_ok = bool(location.get("valid")) and fib_min <= retracement <= fib_max
    add(
        "G05",
        "PASS" if context_ok else "FAIL",
        f"回调{retracement:.1f}%处于有效区"
        if context_ok
        else f"回调{retracement:.1f}%不在{fib_min}-{fib_max}%有效区或未触及有效位置",
        {"retracement": retracement},
    )

    fib_path = bool(pa.get("fib_path_valid"))
    ema_path = bool(pa.get("ema_path_valid"))
    compression_path = bool(pa.get("compression_path_valid"))
    if route == "FIB_PA" and fib_path:
        route_ok, route_reason = True, "Fib+PA路径真实有效"
    elif route in ("EMA_H23", "EMA_L23") and ema_path:
        route_ok, route_reason = True, "EMA回调恢复路径真实有效"
    elif route == "EMA_COMPRESSION" and compression_path:
        route_ok, route_reason = True, "EMA压缩突破路径真实有效"
    elif route == "BOTH" and fib_path and ema_path:
        route_ok, route_reason = True, "Fib与EMA路径同时成立"
    else:
        route_ok = False
        route_reason = f"独立计算路径={route_label_cn(det_route)}，AI声称={route_label_cn(route)}，不一致"
    add(
        "G06",
        "PASS" if route_ok else "FAIL",
        route_reason,
        {"ai_route": route, "deterministic_route": det_route},
    )

    signal_range = float(m5_state.get("signal_bar_range") or 0.0)
    maximum_bar = float(parameters.get("max_signal_bar_usd", 10.0) or 10.0)
    add(
        "G07",
        "PASS" if 0.0 <= signal_range < maximum_bar else "FAIL",
        f"信号K={signal_range:.2f}USD，小于上限{maximum_bar:.2f}USD"
        if signal_range < maximum_bar
        else f"信号K={signal_range:.2f}USD，超过上限{maximum_bar:.2f}USD",
        {"signal_range": signal_range, "maximum": maximum_bar},
    )

    risk = abs(entry - sl)
    sl_side_ok = (direction == "BUY" and sl < entry) or (direction == "SELL" and sl > entry)
    risk_atr = risk / atr if atr > 0 else float("inf")
    max_sl_atr = float(parameters.get("max_sl_atr", 2.0) or 2.0)
    if entry <= 0.0 or sl <= 0.0 or not sl_side_ok:
        g08 = False
        g08_reason = "AI止损方向或数值无效"
    elif risk_atr <= max_sl_atr:
        g08 = True
        g08_reason = f"止损{risk_atr:.2f}ATR，不超过{max_sl_atr:.2f}ATR"
    else:
        g08 = False
        g08_reason = f"止损{risk_atr:.2f}ATR，超过{max_sl_atr:.2f}ATR"
    add(
        "G08",
        "PASS" if g08 else "FAIL",
        g08_reason,
        {"entry": entry, "sl": sl, "risk": risk, "risk_atr": risk_atr, "maximum": max_sl_atr},
    )

    target = float(structure.get("structure_target") or 0.0)
    if target <= 0.0:
        target = float(facts.get("structure", {}).get("structure_target") or 0.0)
    target_distance = abs(target - entry) if target > 0.0 and entry > 0.0 else 0.0
    target_r = target_distance / risk if risk > 0.0 and target_distance > 0.0 else 0.0
    add(
        "G09",
        "PASS" if target_r >= V3109_TARGET_SPACE_R else "FAIL",
        f"结构目标{target_r:.2f}R，要求≥{V3109_TARGET_SPACE_R:.1f}R"
        if target_r >= V3109_TARGET_SPACE_R
        else "结构目标缺失或空间不足2R",
        {"structure_target": target, "target_r": target_r},
    )

    spread = float(market.get("spread") or 0.0)
    spread_ratio = spread / atr if atr > 0 else float("inf")
    max_ratio = float(parameters.get("max_spread_atr_ratio", 0.08) or 0.08)
    absolute_limit = int(parameters.get("absolute_max_spread_points", 0) or 0)
    spread_ok = spread_ratio <= max_ratio and (
        absolute_limit <= 0 or (spread / tick if tick > 0 else 0.0) <= absolute_limit
    )
    add(
        "G10",
        "PASS" if spread_ok else "FAIL",
        f"点差{spread:.2f}USD，合格" if spread_ok else f"点差{spread:.2f}USD，超过限制",
        {"spread": spread, "spread_atr_ratio": spread_ratio, "maximum": max_ratio},
    )

    failed = [gate for gate in gates if gate["result"] == "FAIL"]
    return {
        "compliance": "PASS" if not failed else "BLOCKED",
        "gates": gates,
        "block_gate_ids": [gate["id"] for gate in failed],
        "block_reasons": [f"{gate_cn(gate['id'])}：{gate['reason']}" for gate in failed],
    }
