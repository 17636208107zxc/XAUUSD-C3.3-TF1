import json
from pathlib import Path

import pytest

from tools.parallel_ai_judge import (
    call_primary_decision,
    validate_difference_explanation,
    validate_primary_decision,
)


def valid_open_json():
    return {
        "action": "OPEN", "direction": "BUY", "route": "FIB_PA",
        "confidence": 82, "reason": "十项条件均通过",
        "entry": 4301.0, "sl": 4299.0, "tp1": 4303.0, "tp2": 4305.0,
        "rr_to_tp1": 1.0,
        "conditions": [
            {"id": f"G{i:02d}", "result": "PASS", "reason": "通过"}
            for i in range(1, 11)
        ],
    }


def test_valid_open_decision_requires_full_plan_and_g01_to_g10():
    decision = validate_primary_decision(valid_open_json(), tick_size=0.01)
    assert decision["action"] == "OPEN"
    assert [row["id"] for row in decision["conditions"]] == [f"G{i:02d}" for i in range(1, 11)]


@pytest.mark.parametrize("bad", [None, {}, {"action": "SELL"}, {"action": "OPEN", "direction": "BUY"}])
def test_invalid_primary_output_is_error_not_wait(bad):
    with pytest.raises(ValueError):
        validate_primary_decision(bad, tick_size=0.01)


def test_wait_requires_direction_wait_route_none_and_no_plan():
    wait = valid_open_json()
    wait.update(action="WAIT", direction="WAIT", route="NONE", entry=None, sl=None, tp1=None, tp2=None, rr_to_tp1=None)
    decision = validate_primary_decision(wait, 0.01)
    assert decision["action"] == "WAIT"


def test_open_with_gate_disagreement_is_still_valid():
    """Case 1: DeepSeek marks G04 FAIL but still says OPEN SELL."""
    value = {
        "action": "OPEN", "direction": "SELL", "route": "FIB_PA",
        "confidence": 70, "reason": "整体仍值得做空",
        "entry": 4300.0, "sl": 4302.0, "tp1": 4298.0, "tp2": 4296.0,
        "rr_to_tp1": 1.0,
        "conditions": [
            {"id": "G01", "result": "PASS", "reason": "环境允许"},
            {"id": "G02", "result": "PASS", "reason": "无敞口"},
            {"id": "G03", "result": "PASS", "reason": "结构有效"},
            {"id": "G04", "result": "FAIL", "reason": "推动偏弱"},
            {"id": "G05", "result": "PASS", "reason": "回调有效"},
            {"id": "G06", "result": "PASS", "reason": "路径有效"},
            {"id": "G07", "result": "PASS", "reason": "K线合格"},
            {"id": "G08", "result": "PASS", "reason": "止损合格"},
            {"id": "G09", "result": "PASS", "reason": "空间合格"},
            {"id": "G10", "result": "PASS", "reason": "点差合格"},
        ],
    }
    decision = validate_primary_decision(value, 0.01)
    assert decision["action"] == "OPEN"
    assert decision["direction"] == "SELL"


def test_difference_explanation_is_exactly_three_short_readable_fields():
    value = {"conclusion": "双方意见不同", "reason": "AI认为形态通过", "difference": "G06结果不同"}
    assert "G06" in validate_difference_explanation(value)
    with pytest.raises(ValueError):
        validate_difference_explanation({**value, "extra": "not allowed"})


def test_primary_call_validates_client_json_instead_of_coercing_to_wait():
    class Client:
        def call(self, prompt, payload, max_tokens):
            assert "ea_trace" not in json.dumps(payload).lower()
            return valid_open_json(), {"model": "fake"}

    decision, usage = call_primary_decision(Client(), "rules", {"market": {}}, 500, tick_size=0.01)
    assert decision["direction"] == "BUY"
    assert usage["model"] == "fake"


def test_primary_call_retries_on_technically_invalid_json():
    """Case 5: invalid JSON is retried."""
    class Client:
        def __init__(self):
            self.calls = 0

        def call(self, prompt, payload, max_tokens):
            self.calls += 1
            if self.calls == 1:
                return {"action": "NOT_A_VALID_ACTION"}, {"model": "fake"}
            return valid_open_json(), {"model": "fake"}

    client = Client()
    decision, usage = call_primary_decision(client, "rules", {"market": {}}, 500, tick_size=0.01, retries=1)
    assert client.calls == 2
    assert decision["action"] == "OPEN"


def test_prompts_lock_blind_and_ea_rules_framework():
    root = Path(__file__).parents[1]
    primary = (root / "config" / "parallel_ai_entry_prompt.txt").read_text(encoding="utf-8")
    difference = (root / "config" / "parallel_ai_difference_prompt.txt").read_text(encoding="utf-8")
    for phrase in ("平行交易员", "看不到EA结果", "只返回JSON", "EA开单逻辑", "不必与任何外部标准答案完全一致"):
        assert phrase in primary
    for field in ("conclusion", "reason", "difference"):
        assert field in difference
