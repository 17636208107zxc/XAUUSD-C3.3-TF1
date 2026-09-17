import copy
import json
from datetime import datetime, timedelta

import pytest

from tools.parallel_ai_contracts import (
    RULE_VERSION,
    build_blind_primary_payload,
    canonical_input_hash,
    canonical_input_material,
    snapshot_id_for_m5,
    validate_ea_trace,
    validate_independent_input,
)


def make_independent_input():
    end = datetime(2026, 8, 12, 10, 35)
    bars = []
    for index in range(200):
        when = end - timedelta(minutes=5 * (199 - index))
        price = 3300.0 + index * 0.1
        bars.append({
            "time": when.strftime("%Y-%m-%d %H:%M:%S"),
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price + 0.25,
            "tick_volume": 1000 + index,
        })
    raw = {
        "hash_material_version": 1,
        "schema_version": 2,
        "rule_version": RULE_VERSION,
        "snapshot_id": "XAUUSD.s_M5_20260812_1035",
        "symbol": "XAUUSD.s",
        "timeframe": "M5",
        "m5_time": "2026-08-12 10:35:00",
        "bid": 3319.9,
        "ask": 3320.1,
        "tick_size": 0.01,
        "parameters": {"atr_period": 14, "fib_min": 0.382},
        "account_state": {"positions": 0, "trade_allowed": True},
        "bars": bars,
    }
    raw["input_hash"] = canonical_input_hash(raw)
    return raw


def make_calculation_facts():
    return {
        "indicators": {"ema20": 3318.25, "atr14": 2.1},
        "gates": {"G01": {"passed": True, "reason": "closed M5"}},
        "plans": [],
    }


def test_snapshot_id_is_stable_for_broker_m5():
    assert snapshot_id_for_m5("XAUUSD.s", "M5", "2026-08-12 10:35:00") == (
        "XAUUSD.s_M5_20260812_1035"
    )


def test_snapshot_id_accepts_mt5_dot_time_format():
    assert snapshot_id_for_m5("XAUUSD.s", "M5", "2026.08.12 10:35:00") == (
        "XAUUSD.s_M5_20260812_1035"
    )


def test_independent_input_accepts_mt5_dot_time_format():
    raw = make_independent_input()
    raw["m5_time"] = "2026.08.12 10:35:00"
    for bar in raw["bars"]:
        bar["time"] = bar["time"].replace("-", ".")
    raw["input_hash"] = canonical_input_hash(raw)
    validated = validate_independent_input(raw)
    assert validated["m5_time"] == "2026.08.12 10:35:00"


def test_independent_input_allows_broker_session_gap():
    raw = make_independent_input()
    # Simulate a 65-minute rollover break (13 x 5min) inside the history:
    # bars after index 19 are shifted, so the whole span grows like the real
    # broker feed, and m5_time follows the new final closed bar.
    for bar in raw["bars"][20:]:
        parsed = datetime.strptime(bar["time"], "%Y-%m-%d %H:%M:%S")
        bar["time"] = (parsed + timedelta(minutes=65)).strftime("%Y-%m-%d %H:%M:%S")
    raw["m5_time"] = raw["bars"][-1]["time"]
    raw["snapshot_id"] = snapshot_id_for_m5(raw["symbol"], raw["timeframe"], raw["m5_time"])
    raw["input_hash"] = canonical_input_hash(raw)
    validated = validate_independent_input(raw)
    assert validated["m5_time"] == raw["m5_time"]
    assert validated["input_hash"] == raw["input_hash"]


def test_primary_payload_cannot_contain_ea_trace_fields():
    payload = build_blind_primary_payload(make_independent_input(), make_calculation_facts())
    encoded = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in (
        "ea_trace", "ea_decision", "ea_reason", "local_reject",
        "candidate_created", "ai_allow", "ai_reject", "pending_created",
        "reference_action", "reference_gates", "reference_plan",
    ):
        assert forbidden not in encoded


def test_hash_changes_when_any_raw_bar_changes():
    first = make_independent_input()
    second = copy.deepcopy(first)
    second["bars"][17]["close"] += 0.01
    assert canonical_input_hash(first) != canonical_input_hash(second)


def test_canonical_material_has_fixed_cross_language_order():
    lines = canonical_input_material(make_independent_input()).splitlines()
    assert lines[:4] == [
        "hash_material_version=1",
        "schema_version=2",
        "rule_version=EA_OPEN_V3_9_20_R1",
        "snapshot_id=XAUUSD.s_M5_20260812_1035",
    ]
    assert lines[-1].startswith("bar.199=2026-08-12 10:35:00|")


def test_independent_input_rejects_forming_or_missing_bars_and_hash_mismatch():
    assert validate_independent_input(make_independent_input())["snapshot_id"].endswith("1035")
    too_short = make_independent_input()
    too_short["bars"].pop()
    with pytest.raises(ValueError, match="exactly 200"):
        validate_independent_input(too_short)
    wrong_hash = make_independent_input()
    wrong_hash["input_hash"] = "0" * 64
    with pytest.raises(ValueError, match="input_hash"):
        validate_independent_input(wrong_hash)


def test_ea_trace_requires_comparison_sections():
    raw = make_independent_input()
    trace = {
        key: raw[key]
        for key in ("schema_version", "rule_version", "snapshot_id", "symbol", "timeframe", "m5_time", "input_hash")
    }
    trace.update({
        "calculation": {}, "gates": {}, "local_candidate": {},
        "ea_ai_review": {}, "execution": {},
    })
    assert validate_ea_trace(trace)["input_hash"] == raw["input_hash"]
    del trace["execution"]
    with pytest.raises(ValueError, match="execution"):
        validate_ea_trace(trace)
