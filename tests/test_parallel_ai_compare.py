import copy

import pytest

from tools.parallel_ai_compare import compare_locked_results, is_alertable


def raw():
    return {
        "snapshot_id": "XAUUSD.s_M5_20260812_1035", "input_hash": "a" * 64,
        "rule_version": "EA_OPEN_V3_9_14_R1", "m5_time": "2026-08-12 10:35:00",
        "tick_size": 0.01, "parameters": {}, "bid": 4377.9, "ask": 4378.1,
    }


def facts(ema20=4379.0):
    return {"indicators": {"ema20": ema20, "atr14": 3.0, "rsi14": 45.0, "macd_hist": -0.2},
            "fib": {"retracement": 50.0}}


def reference(failed=None):
    """Deterministic reference plan (Python)."""
    gates = []
    reached = True
    for i in range(1, 11):
        gate = f"G{i:02d}"
        status = "PASS" if reached else "NOT_REACHED"
        if gate == failed:
            status, reached = "FAIL", False
        gates.append({"id": gate, "result": status})
    return {"action": "OPEN" if failed is None else "WAIT", "gates": gates}


def conditions(failed=None):
    result = []
    reached = True
    for i in range(1, 11):
        gate = f"G{i:02d}"
        status = "PASS" if reached else "NOT_REACHED"
        if gate == failed:
            status, reached = "FAIL", False
        result.append({"id": gate, "result": status, "reason": gate})
    return result


def ai_open(direction="SELL", entry=4377.94):
    risk = 2.0
    return {"action": "OPEN", "direction": direction, "route": "FIB_PA",
            "entry": entry, "sl": entry + risk if direction == "SELL" else entry - risk,
            "tp1": entry - risk if direction == "SELL" else entry + risk,
            "tp2": entry - 2 * risk if direction == "SELL" else entry + 2 * risk,
            "rr_to_tp1": 1.0, "conditions": conditions()}


def ai_wait(failed="G05"):
    return {"action": "WAIT", "direction": "WAIT", "route": "NONE",
            "entry": None, "sl": None, "tp1": None, "tp2": None,
            "rr_to_tp1": None, "conditions": conditions(failed)}


def ea_trace(action="OPEN", direction="SELL", entry=4377.94, ai_status="PASS"):
    return {
        "schema_version": 2, "snapshot_id": raw()["snapshot_id"],
        "input_hash": raw()["input_hash"], "rule_version": raw()["rule_version"],
        "m5_time": raw()["m5_time"],
        "calculation": {"ema20": 4379.0, "atr14": 3.0, "rsi14": 45.0,
                        "macd_hist": -0.2, "fib_retracement": 50.0,
                        "entry": entry, "sl": entry + 2.0, "tp1": entry - 2.0,
                        "tp2": entry - 4.0, "rr": 1.0},
        "gates": {f"G{i:02d}": {"status": "PASS"} for i in range(1, 11)},
        "local_candidate": {"action": action, "direction": direction, "route": "FIB_PA"},
        "ea_ai_review": {"status": ai_status, "reason": ""},
        "execution": {"status": "PENDING_CREATED", "normal_block": "", "mt5_retcode": 0,
                      "prechecks_passed": True, "pending_created": True},
    }


def cmp(ai_value, trace, ref=None, status="NORMAL"):
    return compare_locked_results(raw(), facts(), ref if ref is not None else reference(), ai_value, trace, history=[], ai_status=status)


def test_indicator_result_difference_is_rule_audit_calculation_anomaly():
    trace = ea_trace()
    trace["calculation"]["ema20"] = 4381.0
    result = cmp(ai_open(), trace)
    assert result["classification"] == "CALCULATION_ANOMALY"
    assert result["difference_id"] == "C01"
    assert result["rule_audit"]["classification"] == "AGREE"


def test_raw_bar_hash_mismatch_stops_rule_comparison():
    trace = ea_trace()
    trace["input_hash"] = "b" * 64
    result = cmp(ai_open(), trace)
    assert result["classification"] == "DATA_SYNC_ERROR"
    assert result["alertable"] is False


@pytest.mark.parametrize(
    "ai_value,trace,expected",
    [
        (ai_wait(), ea_trace(action="WAIT", direction="NONE", ai_status="NOT_RUN"), "AGREE_WAIT"),
        (ai_open(), ea_trace(), "AGREE_OPEN"),
        (ai_open(), ea_trace(action="WAIT", direction="NONE", ai_status="NOT_RUN"), "LOCAL_ENTRY_DISAGREEMENT"),
        (ai_wait(), ea_trace(), "LOCAL_ENTRY_DISAGREEMENT"),
        (ai_open("BUY"), ea_trace(), "DIRECTION_DISAGREEMENT"),
        (ai_open(), ea_trace(ai_status="FAIL"), "AI_REVIEW_DISAGREEMENT"),
        (ai_open(entry=4377.94), ea_trace(entry=4377.96), "PLAN_DISAGREEMENT"),
    ],
)
def test_disagreement_matrix(ai_value, trace, expected):
    if trace["local_candidate"]["action"] == "WAIT":
        trace["gates"]["G05"]["status"] = "FAIL"
        for i in range(6, 11):
            trace["gates"][f"G{i:02d}"]["status"] = "NOT_REACHED"
    result = cmp(ai_value, trace)
    assert result["classification"] == expected


def test_gate_difference_between_reference_and_ea_is_recorded_as_rule_audit():
    """Reference fails G05, EA passes G05, but AI and EA agree on trade view."""
    trace = ea_trace(action="OPEN", direction="SELL")
    result = cmp(ai_open(), trace, ref=reference(failed="G05"))
    assert result["classification"] == "AGREE_OPEN"
    assert result["rule_audit"]["classification"] == "GATE_DIFFERENCE"
    assert result["rule_audit"]["difference_id"] == "G05"


def test_ai_gate_disagreement_does_not_change_trade_view():
    """Case 1: Reference and EA both OPEN SELL, DeepSeek marks G04 FAIL but still OPEN SELL."""
    ai = ai_open("SELL")
    ai["conditions"][3] = {"id": "G04", "result": "FAIL", "reason": "推动偏弱"}
    result = cmp(ai, ea_trace())
    assert result["classification"] == "AGREE_OPEN"
    assert result["rule_audit"]["classification"] == "AGREE"


def test_ai_unavailable_keeps_rule_audit_but_skips_trade_view():
    """Case 6: DeepSeek unavailable; EA vs Reference audit still completes."""
    trace = ea_trace(action="OPEN", direction="SELL")
    result = cmp({}, trace, status="UNAVAILABLE")
    assert result["ai_status"] == "UNAVAILABLE"
    assert result["trade_view"]["classification"] == "AI_UNAVAILABLE"
    assert result["rule_audit"]["classification"] == "AGREE"


def test_ai_unavailable_with_gate_difference_still_reports_rule_audit():
    trace = ea_trace(action="OPEN", direction="SELL")
    result = cmp({}, trace, ref=reference(failed="G05"), status="UNAVAILABLE")
    assert result["classification"] == "GATE_DIFFERENCE"
    assert result["rule_audit"]["difference_id"] == "G05"


def test_one_tick_plan_difference_is_tolerated_but_two_ticks_is_not():
    assert cmp(ai_open(entry=4377.94), ea_trace(entry=4377.95))["classification"] == "AGREE_OPEN"
    assert cmp(ai_open(entry=4377.94), ea_trace(entry=4377.96))["classification"] == "PLAN_DISAGREEMENT"


@pytest.mark.parametrize("normal_block", [
    "SESSION_CHANGED", "SPREAD_CHANGED", "EXPOSURE_EXISTS", "ACCOUNT_LOCKED",
    "PRICE_MOVED", "ENTRY_PASSED", "MARKET_CLOSED", "NO_PERMISSION",
    "INSUFFICIENT_MARGIN", "BROKER_RESTRICTION", "MT5_RETCODE",
])
def test_explained_execution_stop_is_not_suspect(normal_block):
    trace = ea_trace()
    trace["execution"].update(status="STOPPED", normal_block=normal_block, pending_created=False)
    result = cmp(ai_open(), trace)
    assert result["classification"] != "EXECUTION_SUSPECT"
    assert result["alertable"] is False


def test_unexplained_stop_after_all_prechecks_is_execution_suspect():
    trace = ea_trace()
    trace["execution"].update(status="STOPPED", normal_block="", pending_created=False)
    result = cmp(ai_open(), trace)
    assert result["classification"] == "EXECUTION_SUSPECT"
    assert is_alertable(result, "shadow") is False
    assert is_alertable(result, "full") is True


def test_skipped_gate_vocabulary_not_reached_is_not_a_gate_difference():
    trace = ea_trace(action="WAIT", direction="NONE", ai_status="NOT_RUN")
    trace["gates"]["G06"]["status"] = "FAIL"
    for i in range(7, 11):
        trace["gates"][f"G{i:02d}"]["status"] = "NOT_EVALUATED"
    ref = reference(failed="G06")
    result = cmp(ai_wait(failed="G06"), trace, ref=ref)
    assert result["rule_audit"]["classification"] == "AGREE"
    assert result["classification"] == "AGREE_WAIT"


def test_calc_mismatch_without_gate_difference_needs_two_confirmations():
    trace = ea_trace(action="WAIT", direction="NONE", ai_status="NOT_RUN")
    trace["gates"]["G05"]["status"] = "FAIL"
    for i in range(6, 11):
        trace["gates"][f"G{i:02d}"]["status"] = "NOT_EVALUATED"
    trace["calculation"]["atr14"] = 3.3
    result = cmp(ai_wait(failed="G05"), trace, ref=reference(failed="G05"))
    assert result["classification"] == "CALCULATION_ANOMALY"
    assert result["difference_id"] == "C02"
    assert result["details"]["immediate_impact"] is False
    assert is_alertable(result, "shadow") is False
