import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.parallel_ai_auditor import process_parallel_ai_cycle
from tools.parallel_ai_auditor import _action_divergence_sendable, _corrective_replan
from tools.parallel_ai_contracts import RULE_VERSION, canonical_input_hash


TZ = timezone(timedelta(hours=8))


def independent_input(m5_time="2026-08-12 10:35:00"):
    end = datetime.strptime(m5_time, "%Y-%m-%d %H:%M:%S")
    bars = []
    for index in range(200):
        when = end - timedelta(minutes=5 * (199 - index))
        bars.append({"time": when.strftime("%Y-%m-%d %H:%M:%S"), "open": 4300.0,
                     "high": 4301.0, "low": 4299.0, "close": 4300.0,
                     "tick_volume": 1000 + index})
    parameters = {
        "absolute_max_spread_points": 0, "ai_confidence_threshold": 70,
        "atr_period": 14, "ema_near_distance_usd": 3.0, "ema_period": 20,
        "entry_buffer_atr": 0.05, "entry_buffer_points": 0,
        "fib_invalid_buffer_atr": 0.1, "fib_max": 61.8, "fib_min": 38.2,
        "macd_fast": 12, "macd_signal": 9, "macd_slow": 26,
        "max_post_ai_move_atr": 0.25, "max_signal_bar_usd": 10.0,
        "max_sl_atr": 2.0, "max_spread_atr_ratio": 0.08,
        "min_attempt_separation_bars": 1, "min_impulse_atr": 1.5,
        "min_rr_to_tp1": 0.8, "min_three_bar_move_usd": 10.0,
        "pin_bar_wick_body_ratio": 2.0, "pivot_left": 2, "pivot_right": 2,
        "rsi_period": 14, "sr_tolerance_atr": 0.2,
        "stop_buffer_atr": 0.1, "stop_buffer_points": 0,
        "strong_bar_body_ratio": 0.6,
    }
    raw = {"hash_material_version": 1, "schema_version": 2, "rule_version": RULE_VERSION,
           "snapshot_id": f"XAUUSD.s_M5_{end:%Y%m%d_%H%M}", "symbol": "XAUUSD.s",
           "timeframe": "M5", "m5_time": m5_time, "bid": 4299.9, "ask": 4300.1,
           "tick_size": 0.01, "parameters": parameters,
           "account_state": {"account_login": 1, "existing_exposure": False,
                             "risk_locked": False, "trade_allowed": True}, "bars": bars}
    raw["input_hash"] = canonical_input_hash(raw)
    return raw


def wait_reply():
    rows = []
    for index in range(1, 11):
        result = "PASS" if index < 3 else "FAIL" if index == 3 else "NOT_REACHED"
        rows.append({"id": f"G{index:02d}", "result": result, "reason": "结构不足"})
    return {"action": "WAIT", "direction": "WAIT", "route": "NONE", "confidence": 88,
            "reason": "没有形成趋势结构", "entry": None, "sl": None, "tp1": None,
            "tp2": None, "rr_to_tp1": None, "conditions": rows}


def ea_wait_trace(raw):
    gates = {}
    for index in range(1, 11):
        gates[f"G{index:02d}"] = {"status": "PASS" if index < 3 else "FAIL" if index == 3 else "NOT_REACHED"}
    return {"schema_version": 2, "rule_version": RULE_VERSION,
            "snapshot_id": raw["snapshot_id"], "symbol": "XAUUSD.s", "timeframe": "M5",
            "m5_time": raw["m5_time"], "input_hash": raw["input_hash"],
            "calculation": {"ema20": 4300.0, "atr14": 2.0, "rsi14": 50.0, "macd_hist": 0.0},
            "gates": gates, "local_candidate": {"action": "WAIT", "direction": "NONE", "route": "NONE"},
            "ea_ai_review": {"status": "NOT_RUN"}, "execution": {"status": "NOT_RUN"}}


def write_input(root: Path, raw):
    path = root / "Parallel_AI_V2" / "Independent_Input" / "Pending" / f"{raw['snapshot_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")


def write_trace(root: Path, trace):
    path = root / "Parallel_AI_V2" / "EA_Trace" / "Pending" / f"{trace['snapshot_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace), encoding="utf-8")


class RecordingClient:
    def __init__(self, primary=None, fail_difference=False):
        self.primary = primary or wait_reply()
        self.fail_difference = fail_difference
        self.calls = []

    def call(self, prompt, payload, max_tokens):
        self.calls.append({"prompt": prompt, "payload": payload})
        if "three" in prompt and self.fail_difference:
            raise RuntimeError("difference unavailable")
        if "three" in prompt:
            return {"conclusion": "双方意见不同", "reason": "条件结果不同", "difference": "G03不同"}, {}
        return self.primary, {"model": "fake"}


def config():
    return {"parallel_ai_enabled": True, "parallel_ai_mode": "shadow",
            "parallel_ai_wait_seconds": 120, "parallel_ai_daily_limit": 300,
            "parallel_ai_primary_max_tokens": 900, "parallel_ai_explanation_max_tokens": 180,
            "parallel_ai_trade_enabled": True,
            "parallel_ai_rule_compliance_gate_enabled": False}


def test_primary_result_is_locked_before_ea_trace_is_read(tmp_path):
    raw = independent_input()
    write_input(tmp_path, raw)
    client = RecordingClient()
    t0 = datetime(2026, 8, 12, 18, 0, tzinfo=TZ)
    first = process_parallel_ai_cycle(config(), tmp_path, t0, client, "primary", "three")
    assert first[-1]["status"] == "WAITING_EA_TRACE"
    encoded = json.dumps(client.calls[0]["payload"]).lower()
    assert all(field not in encoded for field in ("ea_trace", "local_candidate", "ea_ai_review", "execution"))
    state = json.loads((tmp_path / "Parallel_AI_V2" / "State" / "auditor_state.json").read_text(encoding="utf-8"))
    assert state["active"]["primary_decision"]["action"] == "WAIT"

    write_trace(tmp_path, ea_wait_trace(raw))
    second = process_parallel_ai_cycle(config(), tmp_path, t0 + timedelta(seconds=120), client, "primary", "three")
    assert second[-1]["status"] == "COMPLETED"
    assert second[-1]["classification"] == "AGREE_WAIT"


def test_explanation_failure_still_queues_readable_fallback_card(tmp_path):
    raw = independent_input()
    write_input(tmp_path, raw)
    client = RecordingClient(fail_difference=True)
    t0 = datetime(2026, 8, 12, 18, 0, tzinfo=TZ)
    process_parallel_ai_cycle(config(), tmp_path, t0, client, "primary", "three")
    trace = ea_wait_trace(raw)
    trace["local_candidate"] = {"action": "OPEN", "direction": "SELL", "route": "FIB_PA"}
    trace["gates"] = {f"G{i:02d}": {"status": "PASS"} for i in range(1, 11)}
    write_trace(tmp_path, trace)
    result = process_parallel_ai_cycle(config(), tmp_path, t0 + timedelta(seconds=120), client, "primary", "three")
    assert result[-1]["classification"] == "LOCAL_ENTRY_DISAGREEMENT"
    queued = list((tmp_path / "Lark_Outbox" / "Pending").glob("*.json"))
    assert len(queued) == 1
    assert "只读对比" in queued[0].read_text(encoding="utf-8")


def test_input_parse_error_is_not_counted_as_ai_outage(tmp_path):
    raw = independent_input()
    raw["m5_time"] = "2026-08-12 10:35"  # unsupported format -> local input error
    write_input(tmp_path, raw)
    client = RecordingClient()
    t0 = datetime(2026, 8, 12, 18, 0, tzinfo=TZ)
    result = process_parallel_ai_cycle(config(), tmp_path, t0, client, "primary", "three")

    assert result[-1]["status"] == "INPUT_ERROR"
    assert result[-1]["error"] == "ValueError"
    assert client.calls == []
    state = json.loads((tmp_path / "Parallel_AI_V2" / "State" / "auditor_state.json").read_text(encoding="utf-8"))
    assert state["primary_failure_streak"] == 0
    assert state["primary_counts_by_broker_date"] == {}
    assert "m5_time" in state["last_input_error"]
    assert not list((tmp_path / "Lark_Outbox" / "Pending").glob("*.json"))


def test_auditor_accepts_mt5_dot_time_input(tmp_path):
    raw = independent_input()
    raw["m5_time"] = "2026.08.12 10:35:00"
    for bar in raw["bars"]:
        bar["time"] = bar["time"].replace("-", ".")
    raw["input_hash"] = canonical_input_hash(raw)
    write_input(tmp_path, raw)
    client = RecordingClient()
    t0 = datetime(2026, 8, 12, 18, 0, tzinfo=TZ)

    first = process_parallel_ai_cycle(config(), tmp_path, t0, client, "primary", "three")
    assert first[-1]["status"] == "WAITING_EA_TRACE"
    assert client.calls and "primary" in client.calls[0]["prompt"]

    write_trace(tmp_path, ea_wait_trace(raw))
    second = process_parallel_ai_cycle(config(), tmp_path, t0 + timedelta(seconds=120), client, "primary", "three")
    assert second[-1]["status"] == "COMPLETED"
    assert second[-1]["classification"] == "AGREE_WAIT"
    records = list((tmp_path / "Parallel_AI_V2" / "Records").glob("Comparisons_2026-08-12.jsonl"))
    assert len(records) == 1
    assert "2026.08.12 10:35:00" in records[0].read_text(encoding="utf-8")


class FailingPrimaryClient:
    """DeepSeek API transport failure on the primary call."""

    def call(self, prompt, payload, max_tokens):
        if "three" in prompt:
            return {"conclusion": "双方意见不同", "reason": "条件结果不同", "difference": "G03不同"}, {}
        raise RuntimeError("DeepSeek unavailable")


def test_deepseek_api_failure_marks_unavailable_and_completes_rule_audit(tmp_path):
    raw = independent_input()
    write_input(tmp_path, raw)
    client = FailingPrimaryClient()
    t0 = datetime(2026, 8, 12, 18, 0, tzinfo=TZ)

    first = process_parallel_ai_cycle(config(), tmp_path, t0, client, "primary", "three")
    assert first[-1]["status"] == "WAITING_EA_TRACE"
    state = json.loads(
        (tmp_path / "Parallel_AI_V2" / "State" / "auditor_state.json").read_text(encoding="utf-8")
    )
    assert state["active"]["ai_status"] == "UNAVAILABLE"
    assert state["active"]["primary_decision"] is None

    write_trace(tmp_path, ea_wait_trace(raw))
    second = process_parallel_ai_cycle(config(), tmp_path, t0 + timedelta(seconds=120), client, "primary", "three")
    assert second[-1]["status"] == "COMPLETED"
    assert second[-1]["classification"] == "AI_UNAVAILABLE"
    records = list((tmp_path / "Parallel_AI_V2" / "Records").glob("Comparisons_2026-08-12.jsonl"))
    assert len(records) == 1


def test_corrective_replan_tells_ai_the_stop_boundary():
    prompts = []

    class Client:
        def call(self, prompt, payload, max_tokens):
            prompts.append(prompt)
            return {
                "action": "WAIT", "direction": "WAIT", "route": "NONE", "confidence": 0,
                "reason": "没有合理STOP位置", "entry": None, "sl": None, "tp1": None,
                "tp2": None, "rr_to_tp1": None,
                "conditions": [{"id": f"G{i:02d}", "result": "PASS", "reason": "ok"} for i in range(1, 11)],
            }, {}

    decision = {
        "action": "OPEN", "direction": "SELL", "route": "FIB_PA", "confidence": 70,
        "reason": "x", "entry": 4338.43, "sl": 4340.12, "tp1": 4333.75, "tp2": 4330.5,
        "rr_to_tp1": 2.77,
        "conditions": [{"id": f"G{i:02d}", "result": "PASS", "reason": "ok"} for i in range(1, 11)],
    }
    payload = {"stop_order_constraints": {
        "bid": 4330.42, "ask": 4330.59,
        "sell_stop_entry_must_be_below": 4329.92,
        "buy_stop_entry_must_be_above": 4331.09,
        "minimum_pending_distance": 0.5,
    }}
    plan_check = {"status": "INVALID", "reason": "SELL_STOP_ENTRY_WRONG_SIDE", "minimum_pending_distance": 0.5}
    out = _corrective_replan(Client(), "base", payload, decision, plan_check, 900, 0.01)
    assert out["action"] == "WAIT"
    assert len(prompts) == 1
    assert "计划修正" in prompts[0]
    assert "SELL_STOP" in prompts[0]
    assert "4329.92" in prompts[0]


def test_action_divergence_only_sends_when_one_side_opened():
    def cmp(action, exec_status, ea_action="WAIT", classification="LOCAL_ENTRY_DISAGREEMENT", difference_id="ACTION"):
        return {
            "classification": classification, "difference_id": difference_id,
            "ai": {"action": action}, "ea": {"action": ea_action},
            "ai_execution_status": exec_status,
        }

    assert _action_divergence_sendable(cmp("OPEN", "PENDING_ACTIVE")) is True
    assert _action_divergence_sendable(cmp("OPEN", "FILLED")) is True
    assert _action_divergence_sendable(cmp("OPEN", "STALE")) is False
    assert _action_divergence_sendable(cmp("OPEN", "NOT_SENT")) is False
    assert _action_divergence_sendable(cmp("OPEN", "")) is False
    assert _action_divergence_sendable(cmp("WAIT", "", ea_action="OPEN")) is True
    # non-ACTION divergences are unchanged
    assert _action_divergence_sendable(cmp("OPEN", "STALE", classification="DIRECTION_DISAGREEMENT")) is True
    assert _action_divergence_sendable(cmp("OPEN", "STALE", difference_id="ROUTE")) is True


def test_trade_disabled_notification_queued_once(tmp_path):
    raw = independent_input()
    write_input(tmp_path, raw)
    client = RecordingClient()
    cfg = config()
    cfg["parallel_ai_trade_enabled"] = False
    t0 = datetime(2026, 8, 12, 18, 0, tzinfo=TZ)
    process_parallel_ai_cycle(cfg, tmp_path, t0, client, "primary", "three")
    queued = list((tmp_path / "Lark_Outbox" / "Pending").glob("*.json"))
    assert any("TRADE_DISABLED" in p.name for p in queued)
    # second cycle must not duplicate the notification
    process_parallel_ai_cycle(cfg, tmp_path, t0 + timedelta(seconds=120), client, "primary", "three")
    disabled = [p for p in (tmp_path / "Lark_Outbox" / "Pending").glob("*.json") if "TRADE_DISABLED" in p.name]
    assert len(disabled) == 1
