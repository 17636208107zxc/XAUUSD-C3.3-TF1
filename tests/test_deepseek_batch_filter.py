import csv
import json
from pathlib import Path

import pytest

from tools.deepseek_batch_filter import SYSTEM_PROMPT, load_completed_ids, validate_decision


def test_system_prompt_supports_v34_m1_and_m5():
    assert "M1" not in SYSTEM_PROMPT
    assert "M5" in SYSTEM_PROMPT
    assert "V3.8" in SYSTEM_PROMPT
    assert "V3.2" not in SYSTEM_PROMPT


def test_system_prompt_describes_v34_parallel_routes_and_evidence():
    for route in ("FIB_PA", "EMA_H23", "EMA_L23", "BOTH"):
        assert route in SYSTEM_PROMPT
    for field in (
        "signal_route",
        "fib_path_valid",
        "ema_h23_path_valid",
        "three_bar_impulse_valid",
        "three_bar_failure_reason",
    ):
        assert field in SYSTEM_PROMPT


def test_system_prompt_treats_three_bar_and_fib_as_route_aware_evidence():
    assert "three_bar_impulse_valid is soft evidence" in SYSTEM_PROMPT
    assert "fib_path_valid=false is allowed for EMA_H23 and EMA_L23" in SYSTEM_PROMPT
    assert (
        "three_bar_impulse_valid=false alone must not reject a valid candidate"
        in SYSTEM_PROMPT
    )
    assert (
        "Not every candidate has passed all Fib/three-bar/H2H3/second-PA hard rules."
        in SYSTEM_PROMPT
    )
    assert "候选已通过主要结构、Fibonacci和V3.2本地硬规则" not in SYSTEM_PROMPT


def test_system_prompt_requires_exact_json_only_response():
    assert "字段必须且只能是allow_trade、confidence、reason" in SYSTEM_PROMPT
    assert "禁止Markdown、代码块、JSON外文字和附加字段" in SYSTEM_PROMPT


def test_validate_decision_accepts_exact_contract():
    value = validate_decision({"allow_trade": True, "confidence": 76, "reason": "结构有效"})
    assert value == {"allow_trade": True, "confidence": 76, "reason": "结构有效"}


def test_validate_decision_rejects_extra_fields():
    with pytest.raises(ValueError, match="exact fields"):
        validate_decision({"allow_trade": True, "confidence": 76, "reason": "ok", "extra": 1})


def test_load_completed_ids_supports_resume(tmp_path: Path):
    output = tmp_path / "decisions.csv"
    output.write_text("SignalID,AllowTrade,Confidence,Reason\nA,true,76,ok\n", encoding="utf-8")
    assert load_completed_ids(output) == {"A"}
