"""Pure, read-only translation of the V3.9.14 closed-M5 entry calculations."""

from __future__ import annotations

from copy import deepcopy
from math import ceil, floor, isfinite
from typing import Any

from .parallel_ai_contracts import validate_independent_input

# V3.10.9 C3.2-B1 有独立的规则实现（M15 主结构 + M5 微周期 + 四路径）。
V3109_RULE_VERSION = "EA_OPEN_V3_10_9_C32B1_PERCENT_RISK_R1"
_V3109_CALCULATOR: Any = None


def _v3109() -> Any:
    global _V3109_CALCULATOR
    if _V3109_CALCULATOR is None:
        from tools import v3109_calculator as module

        _V3109_CALCULATOR = module
    return _V3109_CALCULATOR


def _is_v3109(value: Any) -> bool:
    return isinstance(value, dict) and str(value.get("rule_version")) == V3109_RULE_VERSION


# 统一的 Gate 中文名，供所有用户可见卡片/复盘使用。内部日志仍可保留 G01-G10 编号。
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


def gate_cn(gate_id: str) -> str:
    """返回 Gate 的中文名（带编号辅助），例如 '入场路径（G06）'。"""
    gid = str(gate_id or "").strip().upper()
    name = GATE_NAME_CN.get(gid, "")
    return f"{name}（{gid}）" if name else gid


def ema_series(values: list[float], period: int) -> list[float]:
    if not values or period < 1:
        raise ValueError("EMA requires values and a positive period")
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def sma_series(values: list[float], period: int) -> list[float]:
    if not values or period < 1:
        raise ValueError("SMA requires values and a positive period")
    result: list[float] = []
    running_total = 0.0
    for index, value in enumerate(values):
        running_total += float(value)
        if index >= period:
            running_total -= float(values[index - period])
        result.append(running_total / min(period, index + 1))
    return result


def atr_series(bars: list[dict[str, Any]], period: int) -> list[float]:
    if not bars or period < 1:
        raise ValueError("ATR requires bars and a positive period")
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        high, low = float(bar["high"]), float(bar["low"])
        if index == 0:
            value = high - low
        else:
            previous_close = float(bars[index - 1]["close"])
            value = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(value)
    # MT5's iATR is the simple moving average of True Range over period bars
    # (not Wilder's RMA). The last value must equal the plain average of the
    # final period true ranges so it matches the EA's reported atr14.
    result = []
    running_total = 0.0
    for index, value in enumerate(true_ranges):
        running_total += value
        if index >= period:
            running_total -= true_ranges[index - period]
        result.append(running_total / min(period, index + 1))
    return result


def rsi_series(values: list[float], period: int) -> list[float]:
    if not values or period < 1:
        raise ValueError("RSI requires values and a positive period")
    result = [50.0]
    average_gain = 0.0
    average_loss = 0.0
    for index in range(1, len(values)):
        change = float(values[index]) - float(values[index - 1])
        gain, loss = max(change, 0.0), max(-change, 0.0)
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        if average_loss == 0.0:
            rsi = 100.0 if average_gain > 0.0 else 50.0
        else:
            rsi = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
        result.append(rsi)
    return result


def macd_histogram(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> list[float]:
    fast_values = ema_series(values, fast)
    slow_values = ema_series(values, slow)
    main = [first - second for first, second in zip(fast_values, slow_values)]
    # MT5's built-in iMACD uses a SIMPLE moving average for the signal line
    # (not an EMA). Using EMA here produced a systematic ~0.1-0.2 offset that
    # the auditor flagged as C04 calculation anomalies on every bar.
    signal_values = sma_series(main, signal)
    return [value - baseline for value, baseline in zip(main, signal_values)]


def find_pivots(bars: list[dict[str, Any]], left: int, right: int) -> list[dict[str, Any]]:
    if left < 1 or right < 1:
        raise ValueError("pivot windows must be positive")
    pivots: list[dict[str, Any]] = []
    for center in range(left, len(bars) - right):
        high = float(bars[center]["high"])
        low = float(bars[center]["low"])
        if all(high > float(bars[center - i]["high"]) for i in range(1, left + 1)) and all(
            high > float(bars[center + i]["high"]) for i in range(1, right + 1)
        ):
            pivots.append({"index": center, "time": bars[center]["time"], "kind": "HIGH", "price": high})
        if all(low < float(bars[center - i]["low"]) for i in range(1, left + 1)) and all(
            low < float(bars[center + i]["low"]) for i in range(1, right + 1)
        ):
            pivots.append({"index": center, "time": bars[center]["time"], "kind": "LOW", "price": low})
    return pivots


def _classify_strict(pivots: list[dict[str, Any]], end: int) -> dict[str, Any] | None:
    selected = pivots[: end + 1]
    highs = [item for item in reversed(selected) if item["kind"] == "HIGH"][:2]
    lows = [item for item in reversed(selected) if item["kind"] == "LOW"][:2]
    if len(highs) < 2 or len(lows) < 2:
        return None
    last_high, previous_high = highs
    last_low, previous_low = lows
    buy = last_high["price"] > previous_high["price"] and last_low["price"] > previous_low["price"]
    sell = last_high["price"] < previous_high["price"] and last_low["price"] < previous_low["price"]
    result: dict[str, Any] = {
        "valid": True,
        "buy_major_valid": buy,
        "sell_major_valid": sell,
        "major_structure_valid": buy or sell,
        "direction": "BUY" if buy else "SELL" if sell else "NONE",
        "label": "HH_HL" if buy else "LL_LH" if sell else "MIXED",
        "last_high": last_high,
        "previous_high": previous_high,
        "last_low": last_low,
        "previous_low": previous_low,
        "impulse_high": None,
        "impulse_low": None,
        "pullback_high": None,
        "pullback_low": None,
    }
    if buy:
        result["impulse_high"] = last_high
        result["impulse_low"] = next(
            (item for item in reversed(selected) if item["kind"] == "LOW" and item["index"] < last_high["index"]), None
        )
        result["pullback_low"] = next(
            (item for item in reversed(selected) if item["kind"] == "LOW" and item["index"] > last_high["index"]), None
        )
    elif sell:
        result["impulse_low"] = last_low
        result["impulse_high"] = next(
            (item for item in reversed(selected) if item["kind"] == "HIGH" and item["index"] < last_low["index"]), None
        )
        result["pullback_high"] = next(
            (item for item in reversed(selected) if item["kind"] == "HIGH" and item["index"] > last_low["index"]), None
        )
    return result


def _protection_broken(direction: str, bars: list[dict[str, Any]], protective: dict[str, Any]) -> bool:
    price = float(protective["price"])
    for bar in bars[int(protective["index"]) + 1 :]:
        close = float(bar["close"])
        if (direction == "BUY" and close < price) or (direction == "SELL" and close > price):
            return True
    return False


def analyze_structure(bars: list[dict[str, Any]], left: int, right: int) -> dict[str, Any]:
    pivots = find_pivots(bars, left, right)
    current = _classify_strict(pivots, len(pivots) - 1) if pivots else None
    if current is None:
        return {"valid": False, "major_structure_valid": False, "direction": "NONE", "label": "INSUFFICIENT", "pivots": pivots}
    if current["major_structure_valid"]:
        current["pivots"] = pivots
        return current
    for end in range(len(pivots) - 2, -1, -1):
        prior = _classify_strict(pivots, end)
        if not prior or not prior["major_structure_valid"]:
            continue
        direction = prior["direction"]
        protective = prior["last_low"] if direction == "BUY" else prior["last_high"]
        latest = current["last_low"] if direction == "BUY" else current["last_high"]
        if latest["index"] > protective["index"]:
            protective = latest
        if _protection_broken(direction, bars, protective):
            break
        held = deepcopy(current)
        held.update({
            "buy_major_valid": direction == "BUY",
            "sell_major_valid": direction == "SELL",
            "major_structure_valid": True,
            "direction": direction,
            "label": "HH_HL_HOLD" if direction == "BUY" else "LL_LH_HOLD",
            "impulse_low": prior["impulse_low"],
            "impulse_high": prior["impulse_high"],
        })
        if direction == "BUY":
            held["pullback_low"] = next((p for p in reversed(pivots) if p["kind"] == "LOW" and p["index"] > held["impulse_high"]["index"]), None)
        else:
            held["pullback_high"] = next((p for p in reversed(pivots) if p["kind"] == "HIGH" and p["index"] > held["impulse_low"]["index"]), None)
        held["pivots"] = pivots
        return held
    current["pivots"] = pivots
    return current


def analyze_fib(
    bars: list[dict[str, Any]], structure: dict[str, Any], direction: str,
    atr: float, parameters: dict[str, Any], ema: float,
) -> dict[str, Any]:
    failed = {"context_valid": False, "valid": False, "invalidated": False, "result": "FAIL", "reason": "主要结构无效"}
    if not structure.get("major_structure_valid") or direction not in ("BUY", "SELL") or atr <= 0:
        return failed
    low_anchor, high_anchor = structure.get("impulse_low"), structure.get("impulse_high")
    if not low_anchor or not high_anchor:
        return {**failed, "reason": "推动锚点不足"}
    low, high = float(low_anchor["price"]), float(high_anchor["price"])
    span = high - low
    anchor_index = int(high_anchor["index"] if direction == "BUY" else low_anchor["index"])
    order_ok = int(low_anchor["index"]) < int(high_anchor["index"]) if direction == "BUY" else int(high_anchor["index"]) < int(low_anchor["index"])
    if span <= 0 or not order_ok or anchor_index >= len(bars) - 1:
        return {**failed, "reason": "推动波段无效或尚无回调"}
    impulse_atr = span / atr
    if impulse_atr < float(parameters["min_impulse_atr"]):
        return {**failed, "reason": "推动幅度不足", "impulse_atr": impulse_atr}
    pullback_bars = bars[anchor_index + 1 :]
    if direction == "BUY":
        extreme = min(float(bar["low"]) for bar in pullback_bars)
        retracement = (high - extreme) / span * 100.0
        invalid_level = high - span * float(parameters["fib_max"]) / 100.0 - float(parameters["fib_invalid_buffer_atr"]) * atr
        invalidated = extreme < invalid_level
        structure_invalidated = extreme <= low
        depth = abs(high - extreme) / atr
    else:
        extreme = max(float(bar["high"]) for bar in pullback_bars)
        retracement = (extreme - low) / span * 100.0
        invalid_level = low + span * float(parameters["fib_max"]) / 100.0 + float(parameters["fib_invalid_buffer_atr"]) * atr
        invalidated = extreme > invalid_level
        structure_invalidated = extreme >= high
        depth = abs(low - extreme) / atr
    valid = not structure_invalidated and not invalidated and float(parameters["fib_min"]) <= retracement <= float(parameters["fib_max"])
    zone = "SHALLOW" if retracement < 38.2 else "NORMAL" if retracement < 45 else "OPTIMAL" if retracement <= 55 else "DEEP" if retracement <= 61.8 else "INVALID"
    return {
        "context_valid": not structure_invalidated,
        "valid": valid,
        "invalidated": invalidated,
        "structure_invalidated": structure_invalidated,
        "result": "PASS" if valid else "FAIL",
        "reason": "通过" if valid else "Fib不在有效区或超过缓冲",
        "swing_low": low, "swing_high": high, "anchor_index": anchor_index,
        "pullback_extreme": extreme, "retracement": retracement, "zone": zone,
        "impulse_atr": impulse_atr, "pullback_depth_atr": depth,
        "ma_confluence": abs(extreme - ema) <= float(parameters["ema_near_distance_usd"]),
    }


def _directional_patterns(signal: dict[str, Any], previous: dict[str, Any], direction: str, pin_ratio: float, strong_ratio: float) -> dict[str, bool]:
    open_, high, low, close = map(float, (signal["open"], signal["high"], signal["low"], signal["close"]))
    p_open, p_close = float(previous["open"]), float(previous["close"])
    span, body = high - low, abs(close - open_)
    lower, upper = min(open_, close) - low, high - max(open_, close)
    if span <= 0 or body <= 0:
        pin = hammer = strong = False
    elif direction == "BUY":
        pin = lower >= body * pin_ratio and lower > upper and close >= low + span * 0.5
        hammer = lower >= body * 2 and upper <= span * 0.25 and close >= low + span * 0.5
        strong = close > open_ and body / span >= strong_ratio
    else:
        pin = upper >= body * pin_ratio and upper > lower and close <= high - span * 0.5
        hammer = upper >= body * 2 and lower <= span * 0.25 and close <= high - span * 0.5
        strong = close < open_ and body / span >= strong_ratio
    engulfing = (p_close < p_open and close > open_ and open_ <= p_close and close >= p_open) if direction == "BUY" else (p_close > p_open and close < open_ and open_ >= p_close and close <= p_open)
    return {"pin_bar": pin, "hammer": hammer, "engulfing": engulfing, "strong_reversal": strong}


def analyze_price_action(bars: list[dict[str, Any]], parameters: dict[str, Any]) -> dict[str, Any]:
    signal, previous = bars[-1], bars[-2]
    bullish = _directional_patterns(signal, previous, "BUY", float(parameters["pin_bar_wick_body_ratio"]), float(parameters["strong_bar_body_ratio"]))
    bearish = _directional_patterns(signal, previous, "SELL", float(parameters["pin_bar_wick_body_ratio"]), float(parameters["strong_bar_body_ratio"]))
    bullish["directional"] = any(bullish.values())
    bearish["directional"] = any(bearish.values())
    return {"bullish": bullish, "bearish": bearish}


def _reconstruct_attempt(
    direction: str, bars: list[dict[str, Any]], ema: list[float], start: int,
    minimum_separation: int, maximum_ema_distance: float,
) -> dict[str, Any]:
    """Replay the EA's persistent H/L attempt state over the current impulse."""
    count = 0
    bars_since = 0
    rearmed = False
    rearm_near = False
    rearm_distance = 0.0
    latest_attempt = 0
    for index in range(max(start, 1), len(bars)):
        signal, previous = bars[index], bars[index - 1]
        breakout = False
        if direction == "BUY":
            if float(signal["low"]) < float(previous["low"]):
                bars_since += 1
                rearmed = True
                rearm_distance = abs(float(signal["low"]) - ema[index])
                rearm_near = rearm_distance <= maximum_ema_distance
            breakout = float(signal["high"]) > float(previous["high"])
        else:
            if float(signal["high"]) > float(previous["high"]):
                bars_since += 1
                rearmed = True
                rearm_distance = abs(float(signal["high"]) - ema[index])
                rearm_near = rearm_distance <= maximum_ema_distance
            breakout = float(signal["low"]) < float(previous["low"])
        latest_attempt = 0
        if breakout and (count == 0 or (rearmed and bars_since >= minimum_separation)):
            count = min(count + 1, 3)
            latest_attempt = count
            bars_since = 0
            rearmed = False
    return {
        "attempt": latest_attempt,
        "near_ema": latest_attempt in (2, 3) and rearm_near,
        "ema_distance": rearm_distance,
    }


def _three_bar_impulse(direction: str, bars: list[dict[str, Any]], structure: dict[str, Any], ema: list[float], minimum: float) -> dict[str, Any]:
    low_anchor, high_anchor = structure.get("impulse_low"), structure.get("impulse_high")
    if not low_anchor or not high_anchor:
        return {"valid": False, "move": 0.0}
    start = int(low_anchor["index"] if direction == "BUY" else high_anchor["index"])
    end = int(high_anchor["index"] if direction == "BUY" else low_anchor["index"])
    found = {"valid": False, "move": 0.0}
    for index in range(start, end - 1):
        first, second, third = bars[index : index + 3]
        if direction == "BUY":
            valid = all(float(bar["low"]) > ema[index + offset] for offset, bar in enumerate((first, second, third))) and float(second["high"]) > float(first["high"]) and float(third["high"]) > float(second["high"]) and float(second["low"]) > float(first["low"]) and float(third["low"]) > float(second["low"])
            move = float(third["high"]) - float(first["low"])
        else:
            valid = all(float(bar["high"]) < ema[index + offset] for offset, bar in enumerate((first, second, third))) and float(second["high"]) < float(first["high"]) and float(third["high"]) < float(second["high"]) and float(second["low"]) < float(first["low"]) and float(third["low"]) < float(second["low"])
            move = float(first["high"]) - float(third["low"])
        if valid and move >= minimum:
            found = {"valid": True, "move": move, "first_index": index, "third_index": index + 2}
    return found


def calculate_independent_facts(independent_input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    if _is_v3109(independent_input):
        return _v3109().calculate_independent_facts(independent_input, **kwargs)
    raw = validate_independent_input(independent_input)
    bars = deepcopy(raw["bars"])
    parameters = raw["parameters"]
    closes = [float(bar["close"]) for bar in bars]
    ema = ema_series(closes, int(parameters["ema_period"]))
    atr = atr_series(bars, int(parameters["atr_period"]))
    rsi = rsi_series(closes, int(parameters["rsi_period"]))
    macd = macd_histogram(closes, int(parameters["macd_fast"]), int(parameters["macd_slow"]), int(parameters["macd_signal"]))
    if not all(isfinite(series[-1]) for series in (ema, atr, rsi, macd)):
        raise ValueError("independent indicators are not finite")
    structure = analyze_structure(bars, int(parameters["pivot_left"]), int(parameters["pivot_right"]))
    direction = structure.get("direction", "NONE")
    fib = analyze_fib(bars, structure, direction, atr[-1], parameters, ema[-1])
    price_action = analyze_price_action(bars, parameters)
    directional_pa = price_action["bullish" if direction == "BUY" else "bearish"] if direction in ("BUY", "SELL") else {}
    anchor = fib.get("anchor_index", len(bars) - 2)
    attempt = _reconstruct_attempt(
        direction, bars, ema, int(anchor) + 1,
        int(parameters["min_attempt_separation_bars"]),
        float(parameters["ema_near_distance_usd"]),
    ) if direction in ("BUY", "SELL") else {"attempt": 0, "near_ema": False, "ema_distance": 0.0}
    fib_path = bool(fib.get("valid") and directional_pa.get("directional"))
    ema_path = bool(attempt["attempt"] in (2, 3) and attempt["near_ema"])
    route = "BOTH" if fib_path and ema_path else "FIB_PA" if fib_path else ("EMA_H23" if direction == "BUY" else "EMA_L23") if ema_path else "NONE"
    price_action.update({"route": route, "directional": directional_pa, "attempt": attempt,
                         "fib_path_valid": fib_path, "ema_h23_path_valid": ema_path})
    impulse = _three_bar_impulse(direction, bars, structure, ema, float(parameters["min_three_bar_move_usd"])) if direction in ("BUY", "SELL") else {"valid": False, "move": 0.0}
    return {
        "indicators": {"ema20": ema[-1], "atr14": atr[-1], "rsi14": rsi[-1], "macd_hist": macd[-1]},
        "structure": structure,
        "fib": fib,
        "price_action": price_action,
        "three_bar_impulse": impulse,
        "market": {"bid": raw["bid"], "ask": raw["ask"], "tick_size": raw["tick_size"]},
        "series": {"ema": ema, "atr": atr, "rsi": rsi, "macd_hist": macd},
    }


def _gate(gate_id: str, name: str, result: str, reason: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": gate_id, "name": name, "result": result, "reason": reason, "values": values or {}}


def _valid_number(value: Any) -> bool:
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _align_up(value: float, tick: float) -> float:
    return ceil((value - tick * 1e-12) / tick) * tick


def _align_down(value: float, tick: float) -> float:
    return floor((value + tick * 1e-12) / tick) * tick


def evaluate_gate_facts(independent_input: dict[str, Any], facts: dict[str, Any]) -> list[dict[str, Any]]:
    if _is_v3109(independent_input):
        return _v3109().evaluate_gate_facts(independent_input, facts)
    """Evaluate G01-G10 in EA order; later gates are never silently passed."""
    raw = validate_independent_input(independent_input)
    parameters, state, bars = raw["parameters"], raw["account_state"], raw["bars"]
    gates: list[dict[str, Any]] = []
    reached = True

    def add(gate_id: str, name: str, passed: bool, reason: str, values: dict[str, Any] | None = None) -> None:
        nonlocal reached
        if not reached:
            gates.append(_gate(gate_id, name, "NOT_REACHED", "前一项未通过"))
            return
        result = "PASS" if passed else "FAIL"
        gates.append(_gate(gate_id, name, result, reason, values))
        if not passed:
            reached = False

    def add_error(gate_id: str, name: str, reason: str, values: dict[str, Any] | None = None) -> None:
        nonlocal reached
        if not reached:
            gates.append(_gate(gate_id, name, "NOT_REACHED", "前一项未通过"))
            return
        gates.append(_gate(gate_id, name, "ERROR", reason, values))
        reached = False

    time_allowed = not bool(state.get("rollover_blocked", False)) and not bool(state.get("weekend_blocked", False)) and bool(state.get("trade_allowed", True)) and not bool(state.get("risk_locked", False))
    add("G01", "交易环境", time_allowed, "允许新开仓" if time_allowed else "交易时段或风险锁阻止")
    exposure_ok = not bool(state.get("existing_exposure", False))
    add("G02", "已有敞口", exposure_ok, "没有本EA敞口" if exposure_ok else "已有持仓或挂单")
    structure = facts.get("structure", {})
    structure_ok = bool(structure.get("major_structure_valid")) and structure.get("direction") in ("BUY", "SELL")
    add("G03", "趋势结构", structure_ok, "结构方向有效" if structure_ok else "未形成有效HH/HL或LL/LH")
    fib = facts.get("fib", {})
    impulse_atr = fib.get("impulse_atr")
    min_impulse = parameters["min_impulse_atr"]
    if not _valid_number(impulse_atr) or not _valid_number(min_impulse):
        add_error("G04", "推动幅度", "推动ATR数据缺失或无效",
                  {"impulse_atr": impulse_atr, "minimum": min_impulse})
    else:
        impulse_ok = float(impulse_atr) >= float(min_impulse)
        add("G04", "推动幅度", impulse_ok, "推动幅度达到要求" if impulse_ok else "推动幅度不足",
            {"impulse_atr": impulse_atr, "minimum": min_impulse})
    fib_context_ok = bool(fib.get("context_valid")) and not bool(fib.get("structure_invalidated"))
    add("G05", "回调上下文", fib_context_ok, "回调未破坏推动结构" if fib_context_ok else "回调已破坏推动结构",
        {"retracement": fib.get("retracement"), "invalidated": fib.get("invalidated")})
    route = facts.get("price_action", {}).get("route", "NONE")
    route_ok = route in ("FIB_PA", "EMA_H23", "EMA_L23", "BOTH")
    add("G06", "入场路径", route_ok, f"路径={route}" if route_ok else "Fib+形态和EMA二/三次尝试均未满足")
    signal_range = float(bars[-1]["high"]) - float(bars[-1]["low"])
    signal_ok = 0.0 <= signal_range < float(parameters["max_signal_bar_usd"])
    add("G07", "信号K长度", signal_ok, "长度合格" if signal_ok else "信号K必须严格小于上限",
        {"signal_range": signal_range, "maximum": parameters["max_signal_bar_usd"]})

    direction = structure.get("direction", "NONE")
    atr = float(facts.get("indicators", {}).get("atr14", 0.0))
    point = float(raw["tick_size"])
    entry_buffer = max(float(parameters["entry_buffer_points"]) * point, atr * float(parameters["entry_buffer_atr"]))
    stop_buffer = max(float(parameters["stop_buffer_points"]) * point, atr * float(parameters["stop_buffer_atr"]))
    rule_version = str(raw.get("rule_version") or "")
    route = str(facts.get("price_action", {}).get("route") or "NONE")
    is_ema_route = route in ("EMA_H23", "EMA_L23", "BOTH")
    use_ema_stop = rule_version in ("EA_OPEN_V3_9_16_R1", "EA_OPEN_V3_9_17_R1") and is_ema_route
    is_v18 = rule_version in ("EA_OPEN_V3_9_18_R1", "EA_OPEN_V3_9_19_R1", "EA_OPEN_V3_9_20_R1")
    ema_stop_usd = float(parameters.get("ema_signal_bar_stop_usd", 0.0) or 0.0)
    max_expansion = float(parameters.get("ema_max_stop_expansion_ratio", 1.50) or 1.50)
    recent = bars[-5:]
    signal_bar = bars[-1]

    legacy_sl_raw = 0.0
    signal_sl_raw = 0.0
    raw_entry = 0.0
    target = 0.0
    if direction == "BUY":
        raw_entry = float(signal_bar["high"]) + entry_buffer
        legacy_sl_raw = min(float(bar["low"]) for bar in recent) - stop_buffer
        signal_sl_raw = float(signal_bar["low"]) - ema_stop_usd if is_ema_route else 0.0
        target = float(fib.get("swing_high", 0.0))
    elif direction == "SELL":
        raw_entry = float(signal_bar["low"]) - entry_buffer
        legacy_sl_raw = max(float(bar["high"]) for bar in recent) + stop_buffer
        signal_sl_raw = float(signal_bar["high"]) + ema_stop_usd if is_ema_route else 0.0
        target = float(fib.get("swing_low", 0.0))

    if direction in ("BUY", "SELL"):
        entry = _align_up(raw_entry, point) if direction == "BUY" else _align_down(raw_entry, point)
        legacy_sl = _align_down(legacy_sl_raw, point) if direction == "BUY" else _align_up(legacy_sl_raw, point)
        signal_sl = (_align_down(signal_sl_raw, point) if direction == "BUY" else _align_up(signal_sl_raw, point)) \
            if signal_sl_raw > 0 else 0.0
        target_distance = target - entry if direction == "BUY" else entry - target
    else:
        entry = legacy_sl = signal_sl = target = target_distance = 0.0

    legacy_risk = abs(entry - legacy_sl) if entry > 0 and legacy_sl > 0 else 0.0
    signal_risk = abs(entry - signal_sl) if signal_sl > 0 else 0.0
    legacy_rr = target_distance / legacy_risk if legacy_risk > 0 and target_distance > 0 else 0.0
    signal_rr = target_distance / signal_risk if signal_risk > 0 and target_distance > 0 else 0.0

    stop_mode = "LEGACY_ONLY"
    fallback_reason = "NONE"
    expansion_ratio = 0.0
    if is_v18 and is_ema_route and signal_sl > 0:
        expansion_ratio = signal_risk / legacy_risk if legacy_risk > 0 else 0.0
        signal_atr = signal_risk / atr if atr > 0 else float("inf")
        if signal_risk <= legacy_risk:
            sl, risk, stop_mode, fallback_reason = legacy_sl, legacy_risk, "LEGACY_FALLBACK", "SIGNAL_NOT_WIDER"
        elif expansion_ratio > max_expansion:
            sl, risk, stop_mode, fallback_reason = legacy_sl, legacy_risk, "LEGACY_FALLBACK", "EXPANSION_TOO_LARGE"
        elif signal_atr > float(parameters["max_sl_atr"]):
            sl, risk, stop_mode, fallback_reason = legacy_sl, legacy_risk, "LEGACY_FALLBACK", "SIGNAL_GT_MAX_SL_ATR"
        elif signal_rr < float(parameters["min_rr_to_tp1"]):
            sl, risk, stop_mode, fallback_reason = legacy_sl, legacy_risk, "LEGACY_FALLBACK", "SIGNAL_RR_TOO_LOW"
        else:
            sl, risk, stop_mode, fallback_reason = signal_sl, signal_risk, "EMA_SIGNAL_EXPANDED", "NONE"
    else:
        sl = signal_sl if (use_ema_stop and signal_sl > 0) else legacy_sl
        risk = abs(entry - sl) if entry > 0 and sl > 0 else 0.0
        stop_mode = "EMA_SIGNAL_EXPANDED" if (use_ema_stop and signal_sl > 0) else "LEGACY_ONLY"

    # V3.9.19：出场距离模式只由 SelectedSL 决定；V3.9.20 所有路径统一冻结 Legacy ExitUnit。
    if rule_version == "EA_OPEN_V3_9_20_R1":
        exit_distance_mode = "LEGACY_FROZEN"
    else:
        exit_distance_mode = "LEGACY_FROZEN" if stop_mode == "EMA_SIGNAL_EXPANDED" else "ACTUAL_ENTRY_RISK"
    exit_unit = abs(entry - legacy_sl) if ((use_ema_stop or is_v18) and is_ema_route and legacy_sl > 0) else risk
    sl_atr = risk / atr if atr > 0 else float("inf")
    sl_ok = entry > 0 and sl > 0 and risk > 0 and sl_atr <= float(parameters["max_sl_atr"])
    add("G08", "止损距离", sl_ok, "止损距离合格" if sl_ok else "止损无效或超过ATR上限",
        {"entry": entry, "sl": sl, "risk": risk, "sl_atr": sl_atr, "maximum": parameters["max_sl_atr"],
         "legacy_sl": legacy_sl, "signal_sl": signal_sl, "exit_unit": exit_unit,
         "stop_mode": stop_mode, "exit_distance_mode": exit_distance_mode,
         "fallback_reason": fallback_reason, "expansion_ratio": expansion_ratio})
    rr = target_distance / risk if risk > 0 and target_distance > 0 else 0.0
    rr_ok = rr >= float(parameters["min_rr_to_tp1"])
    add("G09", "结构目标空间", rr_ok, "目标空间合格" if rr_ok else "结构目标方向错误或不足0.8R",
        {"tp1": target, "rr_to_tp1": rr, "minimum": parameters["min_rr_to_tp1"]})
    spread = float(raw["ask"]) - float(raw["bid"])
    spread_ratio = spread / atr if atr > 0 else float("inf")
    absolute_limit = int(parameters["absolute_max_spread_points"])
    spread_ok = spread_ratio <= float(parameters["max_spread_atr_ratio"]) and (absolute_limit <= 0 or spread / point <= absolute_limit)
    add("G10", "点差", spread_ok, "点差合格" if spread_ok else "点差超过限制",
        {"spread": spread, "spread_atr_ratio": spread_ratio, "maximum": parameters["max_spread_atr_ratio"]})
    return gates


def build_reference_plan(independent_input: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Hidden deterministic reference.  Never include this object in AI payloads."""
    if _is_v3109(independent_input):
        return _v3109().build_reference_plan(independent_input, **kwargs)
    facts = calculate_independent_facts(independent_input)
    gates = evaluate_gate_facts(independent_input, facts)
    has_error = any(gate["result"] == "ERROR" for gate in gates)
    passed = all(gate["result"] == "PASS" for gate in gates)
    action = "OPEN" if passed else ("UNAVAILABLE" if has_error else "WAIT")
    structure, fib = facts["structure"], facts["fib"]
    g08 = next(gate for gate in gates if gate["id"] == "G08")
    entry = float(g08["values"].get("entry", 0.0))
    sl = float(g08["values"].get("sl", 0.0))
    risk = abs(entry - sl)
    exit_unit = float(g08["values"].get("exit_unit", risk) or risk)
    legacy_sl = float(g08["values"].get("legacy_sl", 0.0) or 0.0)
    signal_sl = float(g08["values"].get("signal_sl", 0.0) or 0.0)
    stop_mode = str(g08["values"].get("stop_mode", "LEGACY_ONLY") or "LEGACY_ONLY")
    exit_distance_mode = str(g08["values"].get("exit_distance_mode", "ACTUAL_ENTRY_RISK") or "ACTUAL_ENTRY_RISK")
    fallback_reason = str(g08["values"].get("fallback_reason", "NONE") or "NONE")
    expansion_ratio = float(g08["values"].get("expansion_ratio", 0.0) or 0.0)
    rule_version = str(independent_input.get("rule_version") or "")
    route = facts["price_action"].get("route", "NONE")
    is_v19 = rule_version == "EA_OPEN_V3_9_19_R1"
    is_v20 = rule_version == "EA_OPEN_V3_9_20_R1"
    if is_v20:
        # V20：所有路径统一冻结 Legacy ExitUnit。
        use_exit_unit = True
    elif is_v19:
        # V19 只由 SelectedSL 决定，不再由 rule_version/route 决定。
        use_exit_unit = exit_distance_mode == "LEGACY_FROZEN"
    else:
        use_exit_unit = rule_version in ("EA_OPEN_V3_9_17_R1", "EA_OPEN_V3_9_18_R1") and route in ("EMA_H23", "EMA_L23", "BOTH")
    tp_multiple_base = exit_unit if use_exit_unit else risk
    direction = structure.get("direction", "NONE")
    # V20 的 TP 锚点是 ActualEntry（候选阶段未知），因此最终 TP 只能在成交后确定。
    tp_finalized_after_fill = is_v20 or (is_v19 and not use_exit_unit)
    if passed:
        tp1 = fib.get("swing_high") if direction == "BUY" else fib.get("swing_low")
        tp2 = ("TP_FINALIZED_AFTER_FILL" if tp_finalized_after_fill
               else (entry + 2 * tp_multiple_base if direction == "BUY" else entry - 2 * tp_multiple_base))
    else:
        tp1 = None
        tp2 = None
    return {
        "action": action,
        "direction": direction if passed else "NONE",
        "route": facts["price_action"].get("route", "NONE") if passed else "NONE",
        "entry": entry if passed else None,
        "sl": sl if passed else None,
        "selected_sl": sl if passed else None,
        "protective_risk": risk if passed else None,
        "legacy_sl": legacy_sl if passed else None,
        "signal_sl": signal_sl if passed else None,
        "exit_unit": exit_unit if passed else None,
        "stop_mode": stop_mode if passed else None,
        "exit_distance_mode": exit_distance_mode if passed else None,
        "fallback_reason": fallback_reason if passed else None,
        "expansion_ratio": expansion_ratio if passed else None,
        "tp_finalized_after_fill": tp_finalized_after_fill if passed else False,
        "tp1": tp1,
        "tp2": tp2,
        "rr_to_tp1": next(gate for gate in gates if gate["id"] == "G09")["values"].get("rr_to_tp1"),
        "gates": gates,
        "facts": facts,
    }


def compute_setup_group_id(reference: dict[str, Any], direction: str) -> str:
    """Stable id for one market opportunity (direction + swing high/low).

    Used only by the exposure risk gate, never to change the AI decision.
    """
    fib = (reference.get("facts") or {}).get("fib") or {}
    swing_high = fib.get("swing_high")
    swing_low = fib.get("swing_low")
    def _fmt(value: Any) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return "X"
    return f"{direction}_{_fmt(swing_high)}_{_fmt(swing_low)}"


def evaluate_ai_execution_compliance(
    independent_input: dict[str, Any],
    ai_decision: dict[str, Any],
    ai_existing_exposure: bool,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic G01-G10 rule-compliance check for one AI OPEN decision.

    This is Trader B's own hard-rule floor, not the EA reference and not the
    AI's self-reported opinion.  Semantics that differ from the EA reference:

    * G02 uses ``ai_existing_exposure`` (parallel_ai_magic positions/orders),
      never the EA's ``existing_exposure``.
    * G03 additionally requires the AI direction to match the deterministic
      structure direction.
    * G06 validates the AI's *claimed* route against deterministic Fib/PA/EMA
      facts (an AI that says FIB_PA while ``fib_path_valid`` is False fails).
    * G08 validates the AI's actual Entry/SL distance, not a locally rebuilt
      plan.
    * G09 validates the deterministic structure target (swing high/low) R,
      not the AI's own TP1.

    It never mutates the AI decision.  The result is only used to grant or
    withhold real-order execution permission.
    """
    if _is_v3109(independent_input):
        return _v3109().evaluate_ai_execution_compliance(
            independent_input, ai_decision, ai_existing_exposure, facts
        )
    raw = validate_independent_input(independent_input)
    if facts is None:
        facts = calculate_independent_facts(raw)
    parameters = raw["parameters"]
    state = raw["account_state"]
    direction = str(ai_decision.get("direction", "")).strip().upper()
    route = str(ai_decision.get("route", "")).strip().upper()
    entry = float(ai_decision.get("entry") or 0.0)
    sl = float(ai_decision.get("sl") or 0.0)
    tp1 = float(ai_decision.get("tp1") or 0.0)

    gates: list[dict[str, Any]] = []

    def add(gate_id: str, name: str, result: str, reason: str,
            values: dict[str, Any] | None = None) -> None:
        gates.append({"id": gate_id, "name": name, "result": result,
                      "reason": reason, "values": values or {}})

    # G01 交易环境
    time_allowed = (
        not bool(state.get("rollover_blocked", False))
        and not bool(state.get("weekend_blocked", False))
        and bool(state.get("trade_allowed", True))
        and not bool(state.get("risk_locked", False))
    )
    add("G01", "交易环境", "PASS" if time_allowed else "FAIL",
        "允许新开仓" if time_allowed else "交易时段或风险锁阻止")

    # G02 本AI自身敞口（Trader B，只看 parallel_ai_magic）
    add("G02", "本AI敞口", "PASS" if not ai_existing_exposure else "FAIL",
        "本AI无持仓/挂单" if not ai_existing_exposure else "本AI已有持仓或挂单")

    # G03 趋势结构（方向必须与确定性结构一致）
    structure = facts.get("structure", {})
    det_direction = str(structure.get("direction", "NONE")).upper()
    structure_ok = bool(structure.get("major_structure_valid")) and det_direction in ("BUY", "SELL")
    direction_ok = direction == det_direction
    if structure_ok and direction_ok:
        add("G03", "趋势结构", "PASS", f"结构方向一致（{det_direction}）")
    elif not structure_ok:
        add("G03", "趋势结构", "FAIL", "未形成有效HH/HL或LL/LH结构")
    else:
        add("G03", "趋势结构", "FAIL",
            f"AI方向={direction}，但确定性结构方向={det_direction}",
            {"ai_direction": direction, "deterministic_direction": det_direction})

    # G04 推动幅度 >= 1.5 ATR
    fib = facts.get("fib", {})
    impulse_atr = fib.get("impulse_atr")
    min_impulse = parameters["min_impulse_atr"]
    impulse_ok = _valid_number(impulse_atr) and float(impulse_atr) >= float(min_impulse)
    impulse_text = f"{float(impulse_atr):.2f}" if _valid_number(impulse_atr) else "缺失"
    add("G04", "推动幅度", "PASS" if impulse_ok else "FAIL",
        f"推动{impulse_text}ATR，要求≥{min_impulse}ATR" if impulse_ok else f"推动{impulse_text}ATR，不足{min_impulse}ATR",
        {"impulse_atr": impulse_atr, "minimum": min_impulse})

    # G05 回调上下文
    fib_context_ok = bool(fib.get("context_valid")) and not bool(fib.get("structure_invalidated"))
    add("G05", "回调上下文", "PASS" if fib_context_ok else "FAIL",
        "回调未破坏推动结构" if fib_context_ok else "回调已破坏推动结构",
        {"retracement": fib.get("retracement"), "invalidated": fib.get("invalidated")})

    # G06 入场路径（对 AI 声称的 route 做真实核验）
    pa = facts.get("price_action", {})
    fib_path = bool(pa.get("fib_path_valid"))
    ema_path = bool(pa.get("ema_h23_path_valid"))
    det_route = str(pa.get("route", "NONE")).upper()
    if route == "FIB_PA" and fib_path:
        route_ok, route_reason = True, "Fib+方向形态路径真实有效"
    elif route in ("EMA_H23", "EMA_L23") and ema_path:
        route_ok, route_reason = True, "EMA第2/3次突破路径真实有效"
    elif route == "BOTH" and fib_path and ema_path:
        route_ok, route_reason = True, "Fib与EMA路径均真实有效"
    else:
        route_ok = False
        fib_retrace = fib.get("retracement")
        if route == "BOTH" and not (fib_path and ema_path):
            route_reason = "BOTH要求Fib路径与EMA路径同时成立，但确定性事实不满足"
        elif route == "FIB_PA" and not fib_path:
            retr = f"{float(fib_retrace):.1f}%" if _valid_number(fib_retrace) else "缺失"
            route_reason = f"Fib路径不成立（回调{retr}，方向形态未确认）"
        elif route in ("EMA_H23", "EMA_L23") and not ema_path:
            route_reason = "EMA第2/3次突破路径不成立"
        else:
            route_reason = f"确定性路径={det_route}，AI声称路径={route}，不一致"
    add("G06", "入场路径", "PASS" if route_ok else "FAIL", route_reason,
        {"ai_route": route, "deterministic_route": det_route,
         "fib_path_valid": fib_path, "ema_h23_path_valid": ema_path,
         "retracement": fib.get("retracement"), "zone": fib.get("zone")})

    # G07 信号K长度 < 10 USD
    bars = raw["bars"]
    signal_range = float(bars[-1]["high"]) - float(bars[-1]["low"])
    signal_ok = 0.0 <= signal_range < float(parameters["max_signal_bar_usd"])
    add("G07", "信号K长度", "PASS" if signal_ok else "FAIL",
        f"信号K={signal_range:.2f}USD，小于{parameters['max_signal_bar_usd']}USD" if signal_ok else f"信号K={signal_range:.2f}USD，超过{parameters['max_signal_bar_usd']}USD",
        {"signal_range": signal_range, "maximum": parameters["max_signal_bar_usd"]})

    # G08 止损距离 <= 2 ATR（用 AI 自己的 Entry/SL）
    atr = float(facts.get("indicators", {}).get("atr14", 0.0))
    risk = abs(entry - sl)
    sl_side_ok = (direction == "BUY" and sl < entry) or (direction == "SELL" and sl > entry)
    sl_atr = risk / atr if atr > 0 else float("inf")
    max_sl_atr = float(parameters["max_sl_atr"])
    if entry <= 0 or sl <= 0 or not sl_side_ok:
        sl_ok, sl_reason = False, "AI止损方向或数值无效"
    elif sl_atr <= max_sl_atr:
        sl_ok, sl_reason = True, f"止损{sl_atr:.2f}ATR，≤{max_sl_atr}ATR"
    else:
        sl_ok, sl_reason = False, f"止损{sl_atr:.2f}ATR，超过{max_sl_atr}ATR"
    add("G08", "止损距离", "PASS" if sl_ok else "FAIL", sl_reason,
        {"entry": entry, "sl": sl, "risk": risk, "sl_atr": sl_atr, "maximum": max_sl_atr})

    # G09 结构目标空间 >= 0.8R（确定性 swing 目标）
    target = float(fib.get("swing_high", 0.0)) if direction == "BUY" else float(fib.get("swing_low", 0.0))
    target_distance = abs(target - entry) if target > 0 and entry > 0 else 0.0
    rr = target_distance / risk if risk > 0 and target_distance > 0 else 0.0
    min_rr = float(parameters["min_rr_to_tp1"])
    if rr >= min_rr:
        rr_ok, rr_reason = True, f"结构目标{rr:.2f}R，≥{min_rr}R"
    elif target <= 0:
        rr_ok, rr_reason = False, "结构目标缺失"
    else:
        rr_ok, rr_reason = False, f"结构目标仅{rr:.2f}R，不足{min_rr}R"
    add("G09", "结构目标空间", "PASS" if rr_ok else "FAIL", rr_reason,
        {"swing_target": target, "tp1": tp1, "rr_to_tp1": rr, "minimum": min_rr})

    # G10 点差
    spread = float(raw["ask"]) - float(raw["bid"])
    spread_ratio = spread / atr if atr > 0 else float("inf")
    point = float(raw["tick_size"])
    absolute_limit = int(parameters["absolute_max_spread_points"])
    spread_ok = spread_ratio <= float(parameters["max_spread_atr_ratio"]) and (
        absolute_limit <= 0 or spread / point <= absolute_limit
    )
    add("G10", "点差", "PASS" if spread_ok else "FAIL",
        f"点差{spread:.2f}USD，合格" if spread_ok else f"点差{spread:.2f}USD，超限",
        {"spread": spread, "spread_atr_ratio": spread_ratio,
         "maximum": parameters["max_spread_atr_ratio"]})

    failed = [gate for gate in gates if gate["result"] == "FAIL"]
    return {
        "compliance": "PASS" if not failed else "BLOCKED",
        "gates": gates,
        "block_gate_ids": [gate["id"] for gate in failed],
        "block_reasons": [f"{gate_cn(gate['id'])}：{gate['reason']}" for gate in failed],
    }
