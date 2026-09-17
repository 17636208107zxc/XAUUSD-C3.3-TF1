from datetime import datetime, timedelta

import pytest

from tools.parallel_ai_calculator import (
    build_reference_plan,
    calculate_independent_facts,
    evaluate_ai_execution_compliance,
    evaluate_gate_facts,
    find_pivots,
    macd_histogram,
)
from tools.parallel_ai_contracts import RULE_VERSION, canonical_input_hash


def make_input(bars, **parameter_overrides):
    parameters = {
        "absolute_max_spread_points": 0,
        "ai_confidence_threshold": 70,
        "atr_period": 14,
        "ema_near_distance_usd": 3.0,
        "ema_period": 20,
        "entry_buffer_atr": 0.05,
        "entry_buffer_points": 0,
        "fib_invalid_buffer_atr": 0.10,
        "fib_max": 61.8,
        "fib_min": 38.2,
        "macd_fast": 12,
        "macd_signal": 9,
        "macd_slow": 26,
        "max_post_ai_move_atr": 0.25,
        "max_signal_bar_usd": 10.0,
        "max_sl_atr": 2.0,
        "max_spread_atr_ratio": 0.08,
        "min_attempt_separation_bars": 1,
        "min_impulse_atr": 1.5,
        "min_rr_to_tp1": 0.8,
        "min_three_bar_move_usd": 10.0,
        "pin_bar_wick_body_ratio": 2.0,
        "pivot_left": 2,
        "pivot_right": 2,
        "rsi_period": 14,
        "sr_tolerance_atr": 0.2,
        "stop_buffer_atr": 0.1,
        "stop_buffer_points": 0,
        "strong_bar_body_ratio": 0.6,
    }
    parameters.update(parameter_overrides)
    end = bars[-1]["time"]
    raw = {
        "hash_material_version": 1,
        "schema_version": 2,
        "rule_version": RULE_VERSION,
        "snapshot_id": "XAUUSD.s_M5_" + end.replace("-", "").replace(":", "").replace(" ", "_")[:13],
        "symbol": "XAUUSD.s", "timeframe": "M5", "m5_time": end,
        "bid": bars[-1]["close"] - 0.1, "ask": bars[-1]["close"] + 0.1,
        "tick_size": 0.01, "parameters": parameters,
        "account_state": {"account_login": 1, "existing_exposure": False,
                          "risk_locked": False, "trade_allowed": True},
        "bars": bars,
    }
    # Use the contract helper to avoid coupling these pure-calculator tests to ID formatting.
    dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
    raw["snapshot_id"] = f"XAUUSD.s_M5_{dt:%Y%m%d_%H%M}"
    raw["input_hash"] = canonical_input_hash(raw)
    return raw


def constant_bars(count=200, price=4300.0, high=4301.0, low=4299.0):
    start = datetime(2026, 8, 11, 18, 0)
    return [{
        "time": (start + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S"),
        "open": price, "high": high, "low": low, "close": price,
        "tick_volume": 1000 + i,
    } for i in range(count)]


def test_constant_market_has_constant_ema_and_zero_directional_momentum():
    facts = calculate_independent_facts(make_input(constant_bars()))
    assert facts["indicators"]["ema20"] == pytest.approx(4300.0, abs=1e-9)
    assert facts["indicators"]["atr14"] == pytest.approx(2.0, abs=1e-9)
    assert facts["indicators"]["macd_hist"] == pytest.approx(0.0, abs=1e-9)
    assert facts["structure"]["direction"] == "NONE"


def test_macd_signal_line_uses_sma_like_mt5_imacd():
    # MT5's built-in iMACD signal line is a simple moving average of the MACD
    # main line. An EMA signal line produced ~0.12-0.20 offset versus the EA's
    # recorded macd_hist (parallel AI C04 calculation anomalies).
    closes = [
        100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
        111, 110, 112, 114, 113, 115, 117, 116, 118, 120,
        119, 121, 123, 122, 124, 126, 125, 127, 129, 128,
        130, 132, 131, 133, 135, 134, 136, 138, 137, 139,
        141, 140, 142, 144, 143, 145,
    ]
    hist = macd_histogram(closes, 12, 26, 9)[-1]
    assert hist == pytest.approx(0.12244932131904296, abs=1e-12)


def test_pivots_use_exact_left_and_right_closed_bar_windows():
    bars = constant_bars(count=17)
    highs = [1, 2, 3, 4, 10, 4, 3, 2, 3, 4, 5, 6, 9, 6, 5, 4, 3]
    lows = [0, -1, -2, -3, -4, -5, -6, -7, -10, -7, -6, -5, -4, -5, -6, -7, -8]
    for bar, high, low in zip(bars, highs, lows):
        bar.update(open=(high + low) / 2, high=high, low=low, close=(high + low) / 2)
    pivots = find_pivots(bars, left=2, right=2)
    assert [(p["index"], p["kind"], p["price"]) for p in pivots] == [
        (4, "HIGH", 10.0), (8, "LOW", -10.0), (12, "HIGH", 9.0)
    ]


def test_directional_price_action_uses_last_closed_bar_only():
    bars = constant_bars()
    bars[-2].update(open=4302.0, close=4301.0, high=4302.5, low=4300.5)
    bars[-1].update(open=4300.8, close=4302.2, high=4302.4, low=4300.6)
    facts = calculate_independent_facts(make_input(bars))
    assert facts["price_action"]["bullish"]["engulfing"] is True
    assert facts["price_action"]["bearish"]["engulfing"] is False


def test_all_facts_include_series_for_calculation_parity_diagnostics():
    facts = calculate_independent_facts(make_input(constant_bars()))
    assert len(facts["series"]["ema"]) == 200
    assert len(facts["series"]["atr"]) == 200
    assert len(facts["series"]["rsi"]) == 200
    assert len(facts["series"]["macd_hist"]) == 200
    assert set(facts) >= {"indicators", "structure", "fib", "price_action", "series"}


def test_gate_order_stops_after_environment_then_exposure_then_structure():
    raw = make_input(constant_bars())
    raw["account_state"]["risk_locked"] = True
    raw["input_hash"] = canonical_input_hash(raw)
    plan = build_reference_plan(raw)
    assert plan["gates"][0]["result"] == "FAIL"
    assert all(gate["result"] == "NOT_REACHED" for gate in plan["gates"][1:])

    raw = make_input(constant_bars())
    raw["account_state"]["existing_exposure"] = True
    raw["input_hash"] = canonical_input_hash(raw)
    plan = build_reference_plan(raw)
    assert [gate["result"] for gate in plan["gates"][:3]] == ["PASS", "FAIL", "NOT_REACHED"]


def _candidate_facts(direction="BUY", target=4310.0):
    return {
        "indicators": {"atr14": 2.0},
        "structure": {"major_structure_valid": True, "direction": direction},
        "fib": {
            "impulse_atr": 5.0, "context_valid": True,
            "structure_invalidated": False, "invalidated": False,
            "swing_high": target if direction == "BUY" else 4310.0,
            "swing_low": 4290.0 if direction == "BUY" else target,
        },
        "price_action": {"route": "FIB_PA"},
    }


def test_signal_bar_at_exact_10_usd_fails_strict_boundary():
    bars = constant_bars()
    bars[-1].update(high=4305.0, low=4295.0, open=4300.0, close=4300.0)
    raw = make_input(bars)
    gates = evaluate_gate_facts(raw, _candidate_facts(target=4320.0))
    assert next(g for g in gates if g["id"] == "G07")["result"] == "FAIL"
    assert next(g for g in gates if g["id"] == "G08")["result"] == "NOT_REACHED"


def test_wrong_side_structure_target_cannot_pass_point_eight_r_filter():
    raw = make_input(constant_bars())
    # BUY entry will be above 4301; a 4300 target is behind the entry.
    gates = evaluate_gate_facts(raw, _candidate_facts(target=4300.0))
    rr_gate = next(g for g in gates if g["id"] == "G09")
    assert rr_gate["result"] == "FAIL"
    assert rr_gate["values"]["rr_to_tp1"] == 0.0
    assert "方向错误" in rr_gate["reason"]


def test_v3916_ema_route_uses_signal_bar_stop_usd():
    from tools.parallel_ai_contracts import canonical_input_hash

    bars = constant_bars()
    raw = make_input(bars, ema_signal_bar_stop_usd=2.0)
    raw["rule_version"] = "EA_OPEN_V3_9_16_R1"
    raw["input_hash"] = canonical_input_hash(raw)

    # SELL EMA_L23：SL = 信号K最高价 + 2美元
    facts_ema = _candidate_facts(direction="SELL", target=4290.0)
    facts_ema["price_action"]["route"] = "EMA_L23"
    gates = evaluate_gate_facts(raw, facts_ema)
    facts = {g["id"]: g for g in gates}
    sl = facts["G08"]["values"]["sl"]
    assert sl == pytest.approx(4303.0, abs=1e-9)  # signal high 4301 + 2.0

    # 旧14版本：SL = 近5根最高价 + stop_buffer
    raw14 = make_input(bars)
    gates14 = evaluate_gate_facts(raw14, _candidate_facts(direction="SELL", target=4290.0))
    facts14 = {g["id"]: g for g in gates14}
    sl14 = facts14["G08"]["values"]["sl"]
    assert sl14 == pytest.approx(4301.2, abs=1e-9)  # 4301 + 0.2


def test_v3917_ema_route_decouples_exit_unit_from_protective_risk():
    from tools.parallel_ai_contracts import canonical_input_hash

    bars = constant_bars()
    raw = make_input(bars, ema_signal_bar_stop_usd=2.0)
    raw["rule_version"] = "EA_OPEN_V3_9_17_R1"
    raw["input_hash"] = canonical_input_hash(raw)

    facts = _candidate_facts(direction="SELL", target=4290.0)
    facts["price_action"]["route"] = "EMA_L23"
    gates = evaluate_gate_facts(raw, facts)
    g08 = {g["id"]: g for g in gates}["G08"]
    # ProtectiveSL = 4301 + 2 = 4303，ProtectiveRisk = 2.0
    assert g08["values"]["sl"] == pytest.approx(4303.0, abs=1e-9)
    # ExitUnit 必须小于 ProtectiveRisk（LegacySL 比 ProtectiveSL 更近）
    assert g08["values"]["exit_unit"] < g08["values"]["risk"]


def test_atr14_matches_mt5_simple_average_of_last_period_true_ranges():
    bars = constant_bars()
    trs = [float(i + 1) for i in range(14)]
    for bar, tr in zip(bars[-14:], trs):
        bar.update(open=4300.0, high=4300.0 + tr / 2.0, low=4300.0 - tr / 2.0, close=4300.0)
    facts = calculate_independent_facts(make_input(bars))
    expected = sum(trs) / 14.0
    assert facts["indicators"]["atr14"] == pytest.approx(expected, abs=1e-9)
    assert facts["series"]["atr"][-1] == pytest.approx(expected, abs=1e-9)


def _v18_gates(direction="BUY", route="EMA_H23", target=4310.0, ema_stop_usd=2.0,
               expansion_limit=1.50):
    from tools.parallel_ai_contracts import canonical_input_hash

    bars = constant_bars()
    raw = make_input(bars, ema_signal_bar_stop_usd=ema_stop_usd,
                     ema_max_stop_expansion_ratio=expansion_limit)
    raw["rule_version"] = "EA_OPEN_V3_9_18_R1"
    raw["input_hash"] = canonical_input_hash(raw)
    facts = _candidate_facts(direction=direction, target=target)
    facts["price_action"]["route"] = route
    gates = evaluate_gate_facts(raw, facts)
    return {g["id"]: g for g in gates}


def test_v3918_fib_pa_stays_legacy_only():
    gates = _v18_gates(direction="BUY", route="FIB_PA")
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_ONLY"
    assert g08["signal_sl"] == 0.0
    assert g08["sl"] == pytest.approx(g08["legacy_sl"], abs=1e-9)


def test_v3918_signal_not_wider_falls_back():
    gates = _v18_gates(direction="BUY", ema_stop_usd=0.2)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["fallback_reason"] == "SIGNAL_NOT_WIDER"
    assert g08["sl"] == pytest.approx(g08["legacy_sl"], abs=1e-9)


def test_v3918_signal_expanded_when_compliant():
    gates = _v18_gates(direction="BUY", ema_stop_usd=0.775)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "EMA_SIGNAL_EXPANDED"
    assert g08["fallback_reason"] == "NONE"
    assert g08["sl"] == pytest.approx(g08["signal_sl"], abs=1e-9)


def test_v3918_expansion_too_large_falls_back():
    gates = _v18_gates(direction="BUY", ema_stop_usd=1.5)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["fallback_reason"] == "EXPANSION_TOO_LARGE"


def test_v3918_signal_over_2atr_falls_back_not_rejects():
    gates = _v18_gates(direction="BUY", ema_stop_usd=3.0, expansion_limit=2.5)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["fallback_reason"] == "SIGNAL_GT_MAX_SL_ATR"
    assert gates["G08"]["result"] == "PASS"


def test_v3918_signal_rr_too_low_falls_back():
    gates = _v18_gates(direction="BUY", target=4303.0, ema_stop_usd=1.0)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["fallback_reason"] == "SIGNAL_RR_TOO_LOW"


def test_v3918_both_route_uses_hybrid():
    gates = _v18_gates(direction="SELL", route="BOTH", target=4290.0, ema_stop_usd=0.775)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "EMA_SIGNAL_EXPANDED"


def _v19_gates(direction="BUY", route="EMA_H23", target=4310.0, ema_stop_usd=2.0,
               expansion_limit=1.50):
    from tools.parallel_ai_contracts import canonical_input_hash

    bars = constant_bars()
    raw = make_input(bars, ema_signal_bar_stop_usd=ema_stop_usd,
                     ema_max_stop_expansion_ratio=expansion_limit)
    raw["rule_version"] = "EA_OPEN_V3_9_19_R1"
    raw["input_hash"] = canonical_input_hash(raw)
    facts = _candidate_facts(direction=direction, target=target)
    facts["price_action"]["route"] = route
    gates = evaluate_gate_facts(raw, facts)
    return raw, {g["id"]: g for g in gates}


def test_v3919_fib_pa_uses_actual_entry_risk():
    _, gates = _v19_gates(direction="BUY", route="FIB_PA")
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_ONLY"
    assert g08["exit_distance_mode"] == "ACTUAL_ENTRY_RISK"


def test_v3919_ema_not_wider_uses_actual_entry_risk():
    _, gates = _v19_gates(direction="BUY", ema_stop_usd=0.2)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["exit_distance_mode"] == "ACTUAL_ENTRY_RISK"


def test_v3919_ema_expansion_too_large_uses_actual_entry_risk():
    _, gates = _v19_gates(direction="BUY", ema_stop_usd=1.5)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["fallback_reason"] == "EXPANSION_TOO_LARGE"
    assert g08["exit_distance_mode"] == "ACTUAL_ENTRY_RISK"


def test_v3919_ema_over_2atr_uses_actual_entry_risk():
    _, gates = _v19_gates(direction="BUY", ema_stop_usd=3.0, expansion_limit=2.5)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["exit_distance_mode"] == "ACTUAL_ENTRY_RISK"


def test_v3919_ema_rr_too_low_uses_actual_entry_risk():
    _, gates = _v19_gates(direction="BUY", target=4303.0, ema_stop_usd=1.0)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["exit_distance_mode"] == "ACTUAL_ENTRY_RISK"


def test_v3919_ema_signal_expanded_uses_legacy_frozen():
    _, gates = _v19_gates(direction="BUY", ema_stop_usd=0.775)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "EMA_SIGNAL_EXPANDED"
    assert g08["exit_distance_mode"] == "LEGACY_FROZEN"


def test_v3919_both_fallback_uses_actual_entry_risk():
    _, gates = _v19_gates(direction="SELL", route="BOTH", target=4290.0, ema_stop_usd=0.2)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["exit_distance_mode"] == "ACTUAL_ENTRY_RISK"


def test_v3919_both_signal_uses_legacy_frozen():
    _, gates = _v19_gates(direction="SELL", route="BOTH", target=4290.0, ema_stop_usd=0.775)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "EMA_SIGNAL_EXPANDED"
    assert g08["exit_distance_mode"] == "LEGACY_FROZEN"


def _v20_gates(direction="BUY", route="EMA_H23", target=4310.0, ema_stop_usd=2.0,
               expansion_limit=1.50):
    from tools.parallel_ai_contracts import canonical_input_hash

    bars = constant_bars()
    raw = make_input(bars, ema_signal_bar_stop_usd=ema_stop_usd,
                     ema_max_stop_expansion_ratio=expansion_limit)
    raw["rule_version"] = "EA_OPEN_V3_9_20_R1"
    raw["input_hash"] = canonical_input_hash(raw)
    facts = _candidate_facts(direction=direction, target=target)
    facts["price_action"]["route"] = route
    gates = evaluate_gate_facts(raw, facts)
    return raw, {g["id"]: g for g in gates}


def test_v3920_fib_pa_uses_frozen_exit_unit():
    _, gates = _v20_gates(direction="BUY", route="FIB_PA")
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_ONLY"
    assert g08["exit_distance_mode"] == "LEGACY_FROZEN"


def test_v3920_ema_signal_expanded_still_selects_signalsl():
    _, gates = _v20_gates(direction="BUY", ema_stop_usd=0.775)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "EMA_SIGNAL_EXPANDED"
    assert g08["sl"] == pytest.approx(g08["signal_sl"], abs=1e-9)
    assert g08["exit_distance_mode"] == "LEGACY_FROZEN"


def test_v3920_ema_sell_signal_expanded():
    _, gates = _v20_gates(direction="SELL", target=4290.0, ema_stop_usd=0.775)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "EMA_SIGNAL_EXPANDED"
    assert g08["exit_distance_mode"] == "LEGACY_FROZEN"


def test_v3920_ema_fallback_uses_frozen_exit_unit():
    _, gates = _v20_gates(direction="BUY", ema_stop_usd=1.5)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["fallback_reason"] == "EXPANSION_TOO_LARGE"
    assert g08["exit_distance_mode"] == "LEGACY_FROZEN"


def test_v3920_signal_not_wider_falls_back_not_rejects():
    _, gates = _v20_gates(direction="BUY", ema_stop_usd=0.2)
    g08 = gates["G08"]["values"]
    assert g08["stop_mode"] == "LEGACY_FALLBACK"
    assert g08["fallback_reason"] == "SIGNAL_NOT_WIDER"
    assert g08["exit_distance_mode"] == "LEGACY_FROZEN"
    assert gates["G08"]["result"] == "PASS"


def test_v3920_exit_unit_equals_planned_entry_minus_legacy_sl():
    _, gates = _v20_gates(direction="BUY", route="FIB_PA")
    g08 = gates["G08"]["values"]
    assert g08["exit_unit"] == pytest.approx(abs(g08["entry"] - g08["legacy_sl"]), abs=1e-9)


def test_v3920_protective_risk_uses_selected_sl_not_exit_unit():
    _, gates = _v20_gates(direction="BUY", ema_stop_usd=0.775)
    g08 = gates["G08"]["values"]
    # risk 必须基于最终 SelectedSL，而不是 exit_unit。
    assert g08["risk"] == pytest.approx(abs(g08["entry"] - g08["sl"]), abs=1e-9)
    assert g08["sl"] == pytest.approx(g08["signal_sl"], abs=1e-9)


def _compliance_facts(direction="BUY", route="FIB_PA", impulse_atr=5.0,
                      fib_path=True, ema_path=False, swing_target=4310.0,
                      atr=2.0, context_valid=True, structure_invalidated=False):
    return {
        "indicators": {"atr14": atr, "ema20": 4300.0},
        "structure": {"major_structure_valid": True, "direction": direction},
        "fib": {
            "impulse_atr": impulse_atr,
            "context_valid": context_valid,
            "structure_invalidated": structure_invalidated,
            "invalidated": False,
            "retracement": 53.5,
            "zone": "OPTIMAL",
            "valid": fib_path,
            "swing_high": swing_target if direction == "BUY" else 4310.0,
            "swing_low": 4290.0 if direction == "BUY" else swing_target,
        },
        "price_action": {
            "route": route,
            "fib_path_valid": fib_path,
            "ema_h23_path_valid": ema_path,
        },
    }


def _ai_open_decision(direction="BUY", route="FIB_PA", entry=4300.0,
                      sl=4297.0, tp1=4304.0, tp2=4310.0):
    return {
        "action": "OPEN", "direction": direction, "route": route,
        "confidence": 80, "reason": "test",
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
        "conditions": [],
    }


def _compliance(raw, decision, ai_exposure, facts):
    return evaluate_ai_execution_compliance(raw, decision, ai_exposure, facts)


def test_compliance_pass_when_all_gates_pass():
    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    facts = _compliance_facts()
    decision = _ai_open_decision()
    result = _compliance(raw, decision, False, facts)
    assert result["compliance"] == "PASS"
    assert result["block_gate_ids"] == []


def test_compliance_blocks_on_g04_impulse_shortfall():
    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    facts = _compliance_facts(impulse_atr=1.2)
    decision = _ai_open_decision()
    result = _compliance(raw, decision, False, facts)
    assert result["compliance"] == "BLOCKED"
    assert "G04" in result["block_gate_ids"]


def test_compliance_blocks_on_g06_route_not_real():
    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    facts = _compliance_facts(fib_path=False, route="FIB_PA")
    decision = _ai_open_decision(route="FIB_PA")
    result = _compliance(raw, decision, False, facts)
    assert result["compliance"] == "BLOCKED"
    assert "G06" in result["block_gate_ids"]


def test_compliance_g02_ignores_ea_exposure():
    from tools.parallel_ai_contracts import canonical_input_hash

    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    raw["account_state"]["existing_exposure"] = True  # EA has a position
    raw["input_hash"] = canonical_input_hash(raw)
    result = _compliance(raw, _ai_open_decision(), False, _compliance_facts())
    g02 = next(g for g in result["gates"] if g["id"] == "G02")
    assert g02["result"] == "PASS"


def test_compliance_g02_fails_on_ai_own_exposure():
    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    result = _compliance(raw, _ai_open_decision(), True, _compliance_facts())
    g02 = next(g for g in result["gates"] if g["id"] == "G02")
    assert g02["result"] == "FAIL"


def test_compliance_blocks_on_g09_structure_r_too_low():
    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    # structure target is only 0.24R away from entry
    facts = _compliance_facts(swing_target=4300.7)
    decision = _ai_open_decision(entry=4300.0, sl=4297.0)
    result = _compliance(raw, decision, False, facts)
    assert result["compliance"] == "BLOCKED"
    assert "G09" in result["block_gate_ids"]


def test_compliance_blocks_on_g08_sl_over_atr_limit():
    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    facts = _compliance_facts()
    # risk 6 with atr 2 -> sl_atr 3.0 > 2.0
    decision = _ai_open_decision(entry=4300.0, sl=4294.0, tp1=4306.0, tp2=4312.0)
    result = _compliance(raw, decision, False, facts)
    assert result["compliance"] == "BLOCKED"
    assert "G08" in result["block_gate_ids"]


def test_compliance_does_not_mutate_ai_decision():
    from copy import deepcopy

    raw = make_input(constant_bars(), max_spread_atr_ratio=0.2)
    decision = _ai_open_decision()
    original = deepcopy(decision)
    _compliance(raw, decision, False, _compliance_facts(fib_path=False))
    assert decision == original
    assert decision["action"] == "OPEN"


def test_save_rule_blocked_plan_writes_research_sample(tmp_path):
    import json

    from tools.parallel_ai_auditor import _save_rule_blocked_plan

    decision = _ai_open_decision()
    compliance = {"block_gate_ids": ["G06"], "block_reasons": ["G06：Fib路径不成立"]}
    _save_rule_blocked_plan(tmp_path, "XAUUSD.s_M5_20260818_2035", decision,
                            compliance, "XAUUSD.s")
    path = tmp_path / "Parallel_AI_V2" / "AI_Trade_State" / "XAUUSD.s_M5_20260818_2035.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert plan["state"] == "rule_blocked"
    assert plan["rule_compliance"] == "BLOCKED"
    assert plan["block_gate_ids"] == ["G06"]
    assert plan["direction"] == "BUY"
