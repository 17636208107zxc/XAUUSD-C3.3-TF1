import json
from datetime import datetime, timedelta, timezone

import pytest

from tools.parallel_ai_acceptance import acceptance_ready, build_acceptance_report
from tools.parallel_ai_compare import is_alertable


def passing_report():
    return {
        "gates": {
            "minimum_window": True, "complete_broker_session": True,
            "time_alignment": True, "input_alignment": True,
            "unexplained_calculation_drift": True, "last_50_ai_valid": True,
            "replay_suite": True,
        },
        "requires_user_confirmation": True,
    }


@pytest.mark.parametrize("failed_field", [
    "minimum_window", "complete_broker_session", "time_alignment", "input_alignment",
    "unexplained_calculation_drift", "last_50_ai_valid", "replay_suite",
])
def test_any_failed_gate_keeps_shadow_mode(failed_field):
    report = passing_report()
    report["gates"][failed_field] = False
    assert acceptance_ready(report) is False


def test_passing_report_requires_confirmation_and_never_promotes_config():
    report = passing_report()
    assert acceptance_ready(report) is True
    assert report["requires_user_confirmation"] is True


def test_report_is_written_but_does_not_edit_review_config(tmp_path):
    config = tmp_path / "review_config.json"
    config.write_text(json.dumps({"parallel_ai_mode": "shadow"}), encoding="utf-8")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    report = build_acceptance_report(tmp_path, now)
    assert report["ready_for_user_review"] is False
    assert json.loads(config.read_text(encoding="utf-8"))["parallel_ai_mode"] == "shadow"
    assert list((tmp_path / "Parallel_AI_V2" / "Acceptance").glob("acceptance_*.json"))
    assert list((tmp_path / "Parallel_AI_V2" / "Acceptance").glob("acceptance_*.md"))


def test_shadow_and_active_notification_boundaries():
    direction = {"classification": "DIRECTION_DISAGREEMENT", "alertable": True, "difference_id": "DIRECTION", "details": {}}
    gate_flip = {"classification": "LOCAL_ENTRY_DISAGREEMENT", "alertable": True, "difference_id": "G05", "details": {}}
    drift = {"classification": "CALCULATION_ANOMALY", "alertable": True, "difference_id": "C01", "details": {"immediate_impact": False}}
    plan = {"classification": "PLAN_DISAGREEMENT", "alertable": True, "difference_id": "ENTRY", "details": {}}
    execution = {"classification": "EXECUTION_SUSPECT", "alertable": True, "difference_id": "EXECUTION", "details": {}}
    assert is_alertable(direction, "shadow") is True
    assert is_alertable(gate_flip, "shadow") is True
    assert is_alertable(drift, "shadow") is False
    assert is_alertable(plan, "shadow") is False
    assert is_alertable(plan, "active") is True
    assert is_alertable(execution, "active") is True
