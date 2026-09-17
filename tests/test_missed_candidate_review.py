from __future__ import annotations

import json

import pytest

from tools.daily_review_renderer import (
    _final_judgment,
    _issue_conclusion_text,
    _post_expiry_conclusion,
    _render_unfilled_verification,
    build_daily_review_sections,
    render_candidate_card_markdown,
)
from tools.review_core import (
    DAILY_FIELDS,
    _beijing_dt_to_utc,
    analyze_unfilled_candidates,
    append_missed_candidate_history,
    build_candidate_lifecycle_rows,
    build_daily_fallback_response,
    load_missed_candidate_history,
    map_missed_reason_code,
    summarize_missed_candidate_history,
    validation_condition_met,
)

AI_REJECT = "AI\u62d2\u7edd"
DISTANCE = "\u6302\u5355\u8ddd\u79bb\u4e0d\u8db3"
PASS_ENTRY = "\u4ef7\u683c\u8d8a\u8fc7Entry"
EXPIRED = "\u6302\u5355\u5230\u671f"
CANCELLED = "\u4fe1\u53f7\u5931\u6548"
OTHER = "\u5176\u4ed6\u6267\u884c\u539f\u56e0"
NORMAL = "\u6b63\u5e38\u672a\u6210\u4ea4"
POTENTIAL = "\u6f5c\u5728\u6267\u884c\u578b\u9519\u5931"
OBVIOUS = "\u660e\u663e\u6267\u884c\u578b\u9519\u5931"
HIGH_VALUE = "\u9ad8\u4ef7\u503c\u6267\u884c\u578b\u9519\u5931"
UNDECIDED = "\u65e0\u6cd5\u5224\u65ad"
AI_MARKER = "AI\u603b\u7ed3\u6682\u4e0d\u53ef\u7528"
WINDOW_SHORT = "\u89c2\u5bdf\u7a97\u53e3\u4e0d\u8db3"
FIRST_TOUCH_LABEL = "\u771f\u5b9e\u8def\u5f84\u9996\u5148\u89e6\u53ca"


def _candidate_event(signal_id, direction, entry, sl, ts):
    return {
        "server_time": ts,
        "beijing_time": ts,
        "event_type": "candidate",
        "signal_id": signal_id,
        "direction": direction,
        "stage": "candidate",
        "outcome": "",
        "reason": "",
        "details": json.dumps(
            {
                "signal_route": "FIB_PA",
                "trade_plan": {"entry": entry, "sl": sl},
            }
        ),
    }


def _c33_sell_candidate():
    return {
        "server_time": "2026-09-16 17:54:59",
        "beijing_time": "2026-09-16 22:54:59",
        "event_type": "candidate",
        "signal_id": "V310_C3_ALIGNED_SELL_A1_1789581000",
        "direction": "NONE",
        "stage": "candidate",
        "outcome": "",
        "reason": "V3.10.7 SELL H2 deterministic candidate",
        "details": json.dumps({
            "direction": "SELL",
            "signal_type": "EMA_RECOVERY_SELL",
            "planned_entry": 4348.22,
            "final_stop": 4352.49,
            "planned_r": 4.27,
        }),
    }


def test_c33_candidate_uses_structured_direction_and_flat_plan():
    row = build_candidate_lifecycle_rows([_c33_sell_candidate()])[0]

    assert row["direction"] == "SELL"
    assert row["route"] == "EMA_RECOVERY_SELL"
    assert row["planned_entry"] == 4348.22
    assert row["planned_sl"] == 4352.49
    assert row["planned_tp1"] is None
    assert row["entry_facts"]["direction"] == "SELL"
    assert row["entry_facts"]["initial_sl"] == 4352.49


def test_c33_pending_canceled_is_terminal_and_replay_uses_real_plan():
    candidate = _c33_sell_candidate()
    canceled = {
        **candidate,
        "event_type": "pending_canceled",
        "server_time": "2026-09-16 18:05:00",
        "beijing_time": "2026-09-16 23:05:00",
        "reason": "V3102_CANCEL_SIGNAL_HIGH_BROKEN",
        "details": "slot=0",
    }

    row = build_candidate_lifecycle_rows([candidate, canceled])[0]
    replay = analyze_unfilled_candidates([candidate, canceled], [])["items"][0]

    assert row["outcome"] == "cancelled"
    assert row["outcome_time"] == "2026-09-16 18:05:00"
    assert row["outcome_reason"] == "V3102_CANCEL_SIGNAL_HIGH_BROKEN"
    assert replay["direction"] == "SELL"
    assert replay["entry"] == 4348.22
    assert replay["sl"] == 4352.49
    assert replay["expiry_time"] == "2026-09-16 23:05:00"


def test_c33_candidate_card_shows_known_values_without_fake_zeroes():
    candidate = _c33_sell_candidate()
    canceled = {**candidate, "event_type": "pending_canceled",
                "server_time": "2026-09-16 18:05:00",
                "beijing_time": "2026-09-16 23:05:00",
                "reason": "V3102_CANCEL_SIGNAL_HIGH_BROKEN", "details": "slot=0"}
    row = build_candidate_lifecycle_rows([candidate, canceled])[0]

    card = render_candidate_card_markdown(
        "2026-09-16", {"candidate_rows": [row], "unfilled_candidate_review": {"items": []}}
    )

    assert "候选#1｜SELL｜未成交" in card
    assert "入场路径：**EMA回调恢复**" in card
    assert "计划入场价：4348.22" in card
    assert "计划止损：4352.49" in card
    assert "第一目标：—" in card
    assert "信号高点被突破，挂单已撤销" in card
    assert "NONE" not in card


def _ai_allow(signal_id, ts):
    return {
        "server_time": ts,
        "beijing_time": ts,
        "event_type": "ai_allow",
        "signal_id": signal_id,
        "details": json.dumps({"decision": "allow", "confidence": 76}),
    }


def _ai_reject(signal_id, ts):
    return {
        "server_time": ts,
        "beijing_time": ts,
        "event_type": "ai_reject",
        "signal_id": signal_id,
        "reason": "DeepSeek\u5ba1\u6838\u62d2\u7edd",
        "details": json.dumps({"decision": "reject", "confidence": 30}),
    }


def _pending_created(signal_id, ts):
    return {
        "server_time": ts,
        "beijing_time": ts,
        "event_type": "pending_created",
        "signal_id": signal_id,
    }


def _wait_started(signal_id, ts):
    return {
        "server_time": ts,
        "beijing_time": ts,
        "event_type": "pending_wait_started",
        "signal_id": signal_id,
        "reason": DISTANCE + "\uff08\u8ddd\u79bb\u5165\u573a\u4f4d\u8fc7\u8fd1\uff09",
    }


def _wait_missed(signal_id, ts):
    return {
        "server_time": ts,
        "beijing_time": ts,
        "event_type": "pending_wait_missed",
        "signal_id": signal_id,
        "reason": "\u4ef7\u683c\u8d8a\u8fc7\u539f\u5165\u573a\u4f4d",
    }


def _expired(signal_id, ts):
    return {
        "server_time": ts,
        "beijing_time": ts,
        "event_type": "pending_closed",
        "signal_id": signal_id,
        "reason": "ORDER_STATE_EXPIRED",
    }


def _bar(ts, high, low):
    bar = {"beijing_time": ts, "m5_high": high, "m5_low": low}
    return bar


def _bar_with_ticks(ts, high, low, ticks):
    bar = _bar(ts, high, low)
    bar["ticks"] = ticks
    return bar


_WAIT_MISSED_EVENTS = [
    _candidate_event("S1", "SELL", 100.0, 102.0, "2026-08-12 09:55:00"),
    _ai_allow("S1", "2026-08-12 09:55:05"),
    _pending_created("S1", "2026-08-12 09:55:10"),
    _wait_started("S1", "2026-08-12 10:00:00"),
    _wait_missed("S1", "2026-08-12 10:05:00"),
]


def _long_bars(first_bar):
    return [
        first_bar,
        _bar("2026-08-12 10:10:00", 96.2, 95.5),
        _bar("2026-08-12 10:15:00", 99.0, 98.0),
        _bar("2026-08-12 10:20:00", 99.5, 98.8),
        _bar("2026-08-12 10:25:00", 100.0, 99.2),
        _bar("2026-08-12 10:30:00", 100.2, 99.5),
        _bar("2026-08-12 10:35:00", 100.4, 99.6),
        _bar("2026-08-12 11:05:00", 101.0, 100.2),
        _bar("2026-08-12 12:00:00", 101.5, 100.8),
    ]


def test_map_missed_reason_code_uses_fixed_buckets():
    assert map_missed_reason_code("ai_rejected", "") == AI_REJECT
    assert map_missed_reason_code("ai_error", "") == AI_REJECT
    assert map_missed_reason_code("wait_missed", "", []) == PASS_ENTRY
    assert (
        map_missed_reason_code(
            "wait_missed",
            "",
            [{"event_type": "pending_wait_started", "reason": DISTANCE}],
        )
        == DISTANCE
    )
    assert map_missed_reason_code("expired", "") == EXPIRED
    assert map_missed_reason_code("cancelled", "") == CANCELLED
    assert map_missed_reason_code("status_incomplete", "") == OTHER


def test_sl_first_then_2r_is_not_execution_miss_and_not_counted():
    bars = _long_bars(_bar("2026-08-12 10:05:00", 102.4, 101.5))
    result = analyze_unfilled_candidates(_WAIT_MISSED_EVENTS, bars)
    assert result["total_unfilled"] == 1
    item = result["items"][0]
    assert item["reason_code"] == DISTANCE
    assert item["first_touch"] == "SL"
    assert item["classification"] == NORMAL
    assert item["entry_retouched"] is True
    assert item["entry_retouch_time"] == "2026-08-12 10:05:00"
    assert item["r30"] == pytest.approx(2.25)
    assert item["r60"] == pytest.approx(2.25)
    assert item["r_max"] == pytest.approx(0.0)
    assert item["hit_0_8"] is False
    assert item["hit_1r"] is False
    assert item["hit_2r"] is False
    assert item["window30_insufficient"] is False
    assert item["window60_insufficient"] is False
    assert item["final_insufficient"] is False
    assert result["valid_samples"] == 1
    assert result["miss_count"] == 0
    assert result["miss_rate"] == 0.0


def test_first_touch_0_8r_classified_potential_but_2r_hit_counted():
    bars = _long_bars(_bar("2026-08-12 10:05:00", 101.0, 98.2))
    result = analyze_unfilled_candidates(_WAIT_MISSED_EVENTS, bars)
    item = result["items"][0]
    assert item["first_touch"] == "0.8R"
    assert item["classification"] == POTENTIAL
    assert item["hit_0_8"] is True
    assert item["hit_1r"] is True
    assert item["hit_2r"] is True
    assert result["miss_count"] == 1
    assert result["miss_rate"] == 100.0


def test_same_bar_no_ticks_marks_path_order_unconfirmed():
    bars = [
        _bar("2026-08-12 10:05:00", 103.0, 95.0),  # SL + all targets, no ticks
        _bar("2026-08-12 11:05:00", 100.0, 99.0),
        _bar("2026-08-12 12:00:00", 100.5, 99.5),
    ]
    result = analyze_unfilled_candidates(_WAIT_MISSED_EVENTS, bars)
    item = result["items"][0]
    assert item["first_touch"] is None
    assert item["order_unconfirmed"] is True
    assert item["classification"] == UNDECIDED
    assert item["hit_2r"] is True
    assert result["valid_samples"] == 0
    assert result["miss_count"] == 0
    assert result["miss_rate"] is None


def test_same_bar_ticks_resolve_target_first():
    bars = [
        _bar_with_ticks("2026-08-12 10:05:00", 103.0, 95.0, [100.5, 96.0]),
        _bar("2026-08-12 11:05:00", 100.0, 99.0),
        _bar("2026-08-12 12:00:00", 100.5, 99.5),
    ]
    result = analyze_unfilled_candidates(_WAIT_MISSED_EVENTS, bars)
    item = result["items"][0]
    assert item["first_touch"] == "2R"
    assert item["order_unconfirmed"] is False
    assert item["classification"] == HIGH_VALUE
    assert item["hit_2r"] is True
    assert result["miss_count"] == 1


def test_same_bar_ticks_resolve_sl_first():
    bars = [
        _bar_with_ticks("2026-08-12 10:05:00", 104.0, 95.0, [100.5, 102.0]),
        _bar("2026-08-12 11:05:00", 100.0, 99.0),
        _bar("2026-08-12 12:00:00", 100.5, 99.5),
    ]
    result = analyze_unfilled_candidates(_WAIT_MISSED_EVENTS, bars)
    item = result["items"][0]
    assert item["first_touch"] == "SL"
    assert item["order_unconfirmed"] is False
    assert item["classification"] == NORMAL
    assert item["hit_2r"] is False
    assert result["miss_count"] == 0


def test_unconfirmed_path_renderer_texts():
    item = {
        "direction": "SELL",
        "classification": UNDECIDED,
        "entry_retouched": True,
        "entry_retouch_time": "2026-08-12 10:05:00",
        "first_touch": None,
        "order_unconfirmed": True,
        "final_insufficient": False,
        "window30_insufficient": False,
        "window60_insufficient": False,
        "r30": 2.5,
        "r60": 2.5,
        "r_max": 2.5,
    }
    assert "数据不足，暂不判断是否属于错过机会" in _final_judgment(item)
    assert "无法可靠判断是先止损还是先达到盈利目标" in _post_expiry_conclusion(item)
    assert "暂时无法确认是先止损，还是先达到盈利目标" in _render_unfilled_verification(item)
    assert "无法可靠判定先止损还是先达目标" in _issue_conclusion_text(item)


def test_expired_fill_bar_touch_without_ticks_is_unconfirmed():
    events = [
        _candidate_event("S5", "SELL", 100.0, 102.0, "2026-08-12 09:55:00"),
        _ai_allow("S5", "2026-08-12 09:55:05"),
        _pending_created("S5", "2026-08-12 09:55:10"),
        _expired("S5", "2026-08-12 10:05:00"),
    ]
    bars = [
        _bar("2026-08-12 10:05:00", 103.0, 95.0),  # entry re-touch + SL/targets
        _bar("2026-08-12 11:05:00", 100.0, 99.0),
        _bar("2026-08-12 12:00:00", 100.5, 99.5),
    ]
    result = analyze_unfilled_candidates(events, bars)
    item = result["items"][0]
    assert item["entry_retouched"] is True
    assert item["entry_retouch_time"] == "2026-08-12 10:05:00"
    assert item["first_touch"] is None
    assert item["order_unconfirmed"] is True
    assert item["classification"] == UNDECIDED
    assert item["hit_2r"] is True
    assert result["valid_samples"] == 0
    assert result["miss_count"] == 0
    assert result["miss_rate"] is None


def test_expired_r_windows_start_at_first_entry_retouch_not_expiry():
    events = [
        _candidate_event("S6", "SELL", 100.0, 102.0, "2026-08-12 09:55:00"),
        _ai_allow("S6", "2026-08-12 09:55:05"),
        _pending_created("S6", "2026-08-12 09:55:10"),
        _expired("S6", "2026-08-12 10:05:00"),
    ]
    bars = [
        # Above the entry until price falls back to 100 at 10:15.
        _bar("2026-08-12 10:05:00", 103.0, 101.0),
        _bar("2026-08-12 10:10:00", 102.0, 100.8),
        _bar("2026-08-12 10:15:00", 100.5, 99.5),
        _bar("2026-08-12 10:20:00", 103.0, 102.0),
        _bar("2026-08-12 10:25:00", 100.0, 98.0),
    ]
    result = analyze_unfilled_candidates(events, bars)
    item = result["items"][0]
    assert item["entry_retouched"] is True
    assert item["entry_retouch_time"] == "2026-08-12 10:15:00"
    assert item["first_touch"] == "SL"
    assert item["order_unconfirmed"] is False
    assert item["classification"] == NORMAL
    assert item["r30"] == pytest.approx(1.0)
    assert item["r60"] == pytest.approx(1.0)
    assert item["r_max"] == pytest.approx(0.25)
    assert item["hit_0_8"] is False
    assert item["hit_1r"] is False
    assert item["hit_2r"] is False
    assert item["window30_insufficient"] is True
    assert item["final_insufficient"] is False
    assert result["miss_count"] == 0


def test_ai_reject_preserved_but_excluded_from_miss_rate():
    events = [
        _candidate_event("S2", "SELL", 200.0, 202.0, "2026-08-12 13:54:55"),
        _ai_reject("S2", "2026-08-12 13:55:00"),
    ]
    bars = [
        _bar_with_ticks(
            "2026-08-12 13:55:00", 201.0, 196.0, [201.0, 199.0, 196.0]
        ),  # ticks resolve 2R = 196 first
        _bar("2026-08-12 14:00:00", 200.0, 197.0),
        _bar("2026-08-12 15:00:00", 200.5, 198.0),
    ]
    result = analyze_unfilled_candidates(events, bars)
    assert result["total_unfilled"] == 1
    item = result["items"][0]
    assert item["reason_code"] == AI_REJECT
    assert item["classification"] == HIGH_VALUE
    assert item["order_unconfirmed"] is False
    assert item["entry_retouched"] is True
    assert item["entry_retouch_time"] == "2026-08-12 13:55:00"
    assert item["hit_2r"] is True
    assert result["valid_samples"] == 0
    assert result["miss_count"] == 0
    assert result["miss_rate"] is None
    assert result["classification_counts"][HIGH_VALUE] == 1


def test_window_insufficient_near_close_marked():
    bars = [
        _bar("2026-08-12 10:05:00", 102.4, 101.5),
        _bar("2026-08-12 10:10:00", 96.2, 95.5),
        _bar("2026-08-12 10:15:00", 99.0, 98.0),
        _bar("2026-08-12 10:20:00", 99.5, 98.8),
    ]
    result = analyze_unfilled_candidates(_WAIT_MISSED_EVENTS, bars)
    item = result["items"][0]
    assert item["window30_insufficient"] is True
    assert item["window60_insufficient"] is True
    assert item["final_insufficient"] is False
    assert item["r30"] == pytest.approx(2.25)
    assert item["r_max"] == pytest.approx(0.0)
    assert result["valid_samples"] == 1


def test_no_bar_after_expiry_marked_undecided():
    bars = [_bar("2026-08-12 10:05:00", 100.0, 99.0)]
    result = analyze_unfilled_candidates(_WAIT_MISSED_EVENTS, bars)
    item = result["items"][0]
    assert item["final_insufficient"] is True
    assert item["classification"] == UNDECIDED
    assert result["valid_samples"] == 0
    assert result["miss_rate"] is None


def test_expired_never_retouches_entry_is_normal_no_opportunity():
    events = [
        _candidate_event("S3", "SELL", 100.0, 102.0, "2026-08-12 09:55:00"),
        _ai_allow("S3", "2026-08-12 09:55:05"),
        _pending_created("S3", "2026-08-12 09:55:10"),
        _expired("S3", "2026-08-12 10:05:00"),
    ]
    bars = [
        _bar("2026-08-12 10:05:00", 103.0, 101.5),
        _bar("2026-08-12 10:10:00", 102.0, 100.8),
        _bar("2026-08-12 10:15:00", 101.5, 100.3),
        _bar("2026-08-12 10:20:00", 102.0, 100.6),
        _bar("2026-08-12 11:00:00", 101.0, 100.2),
    ]
    result = analyze_unfilled_candidates(events, bars)
    assert result["total_unfilled"] == 1
    item = result["items"][0]
    assert item["entry_retouched"] is False
    assert item["entry_retouch_time"] == ""
    assert item["first_touch"] is None
    assert item["classification"] == NORMAL
    assert item["r30"] is None
    assert item["r60"] is None
    assert item["r_max"] is None
    assert item["window30_insufficient"] is False
    assert item["window60_insufficient"] is False
    assert item["final_insufficient"] is False
    assert item["hit_0_8"] is False
    assert item["hit_1r"] is False
    assert item["hit_2r"] is False
    assert result["valid_samples"] == 1
    assert result["miss_count"] == 0


def test_expired_retouches_entry_then_sl_first_is_normal():
    events = [
        _candidate_event("S4", "SELL", 100.0, 102.0, "2026-08-12 09:55:00"),
        _ai_allow("S4", "2026-08-12 09:55:05"),
        _pending_created("S4", "2026-08-12 09:55:10"),
        _expired("S4", "2026-08-12 10:05:00"),
    ]
    bars = [
        _bar("2026-08-12 10:05:00", 103.0, 101.5),
        _bar("2026-08-12 10:10:00", 100.5, 99.8),
        _bar("2026-08-12 10:15:00", 102.4, 101.0),
        _bar("2026-08-12 10:20:00", 99.0, 97.0),
        _bar("2026-08-12 11:15:00", 100.0, 99.0),
    ]
    result = analyze_unfilled_candidates(events, bars)
    item = result["items"][0]
    assert item["entry_retouched"] is True
    assert item["entry_retouch_time"] == "2026-08-12 10:10:00"
    assert item["first_touch"] == "SL"
    assert item["classification"] == NORMAL
    assert item["r30"] == pytest.approx(1.5)
    assert item["r60"] == pytest.approx(1.5)
    assert item["r_max"] == pytest.approx(0.1)
    assert item["hit_0_8"] is False
    assert item["hit_1r"] is False
    assert item["hit_2r"] is False
    assert result["miss_count"] == 0


def test_append_history_is_idempotent_and_ai_reject_not_in_miss_stats(tmp_path):
    items = [
        {
            "signal_id": "S1",
            "direction": "SELL",
            "outcome": "wait_missed",
            "reason_code": DISTANCE,
            "reason_text": "\u6302\u5355\u8ddd\u79bb\u4e0d\u8db3",
            "entry": 100.0,
            "sl": 102.0,
            "r30": 2.25,
            "r60": 2.25,
            "r_max": 2.25,
            "window30_insufficient": False,
            "window60_insufficient": False,
            "final_insufficient": False,
            "first_touch": "2R",
            "classification": HIGH_VALUE,
            "hit_0_8": True,
            "hit_1r": True,
            "hit_2r": True,
        },
        {
            "signal_id": "S2",
            "direction": "BUY",
            "outcome": "ai_rejected",
            "reason_code": AI_REJECT,
            "reason_text": "DeepSeek\u5ba1\u6838\u62d2\u7edd",
            "entry": 300.0,
            "sl": 298.0,
            "r30": 0.5,
            "r60": 0.8,
            "r_max": 1.2,
            "window30_insufficient": False,
            "window60_insufficient": False,
            "final_insufficient": False,
            "first_touch": "1R",
            "classification": OBVIOUS,
            "hit_0_8": True,
            "hit_1r": True,
            "hit_2r": False,
        },
    ]
    first = append_missed_candidate_history(tmp_path, "2026-08-12", {"items": items})
    second = append_missed_candidate_history(tmp_path, "2026-08-12", {"items": items})
    records = load_missed_candidate_history(tmp_path)
    assert len(records) == 2
    assert first["by_reason_code"][DISTANCE]["total"] == 1
    assert first["by_reason_code"][DISTANCE]["miss_high_value"] == 1
    assert first["by_reason_code"][DISTANCE]["hit_2r"] == 1
    assert first["by_reason_code"][AI_REJECT]["total"] == 1
    assert first["by_reason_code"][AI_REJECT]["miss_high_value"] == 0
    assert first["by_reason_code"][AI_REJECT]["hit_1r"] == 0
    assert first["validation_met"] is False


def _record(review_date, signal_id, reason_code=DISTANCE, classification=HIGH_VALUE):
    return {
        "review_date": review_date,
        "signal_id": signal_id,
        "direction": "SELL",
        "outcome": "wait_missed",
        "reason_code": reason_code,
        "reason_text": "",
        "entry": 100.0,
        "sl": 102.0,
        "r30": 1.2,
        "r60": 1.2,
        "r_max": 2.2,
        "window30_insufficient": False,
        "window60_insufficient": False,
        "final_insufficient": False,
        "first_touch": "2R",
        "classification": classification,
        "hit_0_8": True,
        "hit_1r": True,
        "hit_2r": True,
    }


def test_validation_condition_met_three_consecutive_same_code():
    records = [
        _record("2026-08-10", "a"),
        _record("2026-08-11", "b"),
        _record("2026-08-12", "c"),
    ]
    summary = summarize_missed_candidate_history(records)
    assert summary["validation_met"] is True


def test_validation_condition_met_five_occurrences_in_ten_days():
    records = [
        _record("2026-08-01", f"d{i}")
        for i in range(5)
    ]
    summary = summarize_missed_candidate_history(records)
    assert summary["validation_met"] is True


def test_validation_not_met_when_different_codes_alternate():
    records = [
        _record("2026-08-10", "a", reason_code=DISTANCE),
        _record("2026-08-11", "b", reason_code=CANCELLED),
        _record("2026-08-12", "c", reason_code=DISTANCE),
    ]
    summary = summarize_missed_candidate_history(records)
    assert summary["validation_met"] is False


def test_fallback_response_renders_with_ai_marker_and_exact_fields():
    payload = {
        "review_date": "2026-08-12",
        "statistics": {
            "candidate_count": 1,
            "local_reject_count": 3,
            "block_reason_counts": {"fib_not_ready": 1, "other": 2},
        },
        "unfilled_candidate_review": {
            "items": [
                {
                    "sequence": 1,
                    "direction": "SELL",
                    "reason_code": DISTANCE,
                    "reason_text": "\u6302\u5355\u8ddd\u79bb\u4e0d\u8db3",
                    "entry": 4399.60,
                    "sl": 4402.53,
                    "entry_retouched": True,
                    "entry_retouch_time": "2026-08-12 14:12:00",
                    "r30": 0.9,
                    "r60": 1.4,
                    "r_max": 2.2,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": "2R",
                    "classification": HIGH_VALUE,
                    "hit_0_8": True,
                    "hit_1r": True,
                    "hit_2r": True,
                }
            ]
        },
        "missed_candidate_summary": {
            "by_reason_code": {
                DISTANCE: {
                    "total": 1,
                    "normal": 0,
                    "hit_0_8": 1,
                    "hit_1r": 1,
                    "hit_2r": 1,
                    "miss_high_value": 1,
                    "insufficient": 0,
                }
            },
            "validation_met": False,
        },
    }
    response = build_daily_fallback_response(payload, "2026-08-12")
    assert set(response) == DAILY_FIELDS
    assert response["issue_judgment"] == ""
    assert response["conclusion_summary"] == ""
    assert response["market_summary"].startswith(AI_MARKER)

    sections = build_daily_review_sections(
        "2026-08-12", payload, response, {"monitor": 5, "daily": 1}
    )
    body = "\n\n".join(sections)
    assert "【策略观察】" in body
    assert "【程序问题】" in body
    assert "高价值执行型错失" not in body
    assert "## 4. \u4eca\u65e5\u95ee\u9898" in body
    assert "## 5. \u4e0b\u4e00\u6b65" in body


def test_pending_closed_without_signal_id_links_by_order_ticket():
    """A pending_closed row written with an empty signal_id must still be
    resolved to the candidate via the pending_created order_ticket."""
    events = [
        _candidate_event("S9", "BUY", 100.0, 99.0, "2026-08-12 09:55:00"),
        _ai_allow("S9", "2026-08-12 09:55:05"),
        {
            "server_time": "2026-08-12 09:55:10",
            "beijing_time": "2026-08-12 09:55:10",
            "event_type": "pending_created",
            "signal_id": "S9",
            "order_ticket": "734334524",
        },
        {
            "server_time": "2026-08-12 10:00:00",
            "beijing_time": "2026-08-12 10:00:00",
            "event_type": "pending_closed",
            "signal_id": "",
            "order_ticket": "734334524",
            "reason": "ORDER_STATE_CANCELED",
        },
    ]
    rows = build_candidate_lifecycle_rows(events)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "cancelled"
    assert rows[0]["order_ticket"] == "734334524"


def test_m1_loader_resolves_ambiguous_m5_bar_sl_first():
    bars = [
        _bar("2026-08-12 10:05:00", 104.0, 96.0),  # SL(102) + 2R(96) same M5 bar
        _bar("2026-08-12 11:05:00", 100.0, 99.0),
    ]
    m1_bars = [
        {"beijing_time": "2026-08-12 10:01:00", "m1_high": 102.5, "m1_low": 99.0},
        {"beijing_time": "2026-08-12 10:02:00", "m1_high": 100.0, "m1_low": 96.0},
    ]
    result = analyze_unfilled_candidates(
        _WAIT_MISSED_EVENTS, bars, m1_loader=lambda start, end: m1_bars
    )
    item = result["items"][0]
    assert item["first_touch"] == "SL"
    assert item["replay_resolution"] == "M1"
    assert item["classification"] == NORMAL


def test_beijing_to_utc_conversion_for_tick_reads():
    from datetime import datetime

    utc = _beijing_dt_to_utc(datetime(2026, 8, 17, 16, 45, 0))
    assert utc.isoformat() == "2026-08-17T08:45:00+00:00"
