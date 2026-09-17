from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tools.independent_ai_observer as observer


def make_snapshots(
    count: int,
    last_m5: str = "2026-08-11 16:30:00",
) -> list[dict[str, str]]:
    end = datetime.strptime(last_m5, "%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, str]] = []
    for offset in range(count - 1, -1, -1):
        stamp = end - timedelta(minutes=5 * offset)
        row = {
            "server_time": (stamp + timedelta(minutes=5)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "beijing_time": (stamp + timedelta(hours=5, minutes=5)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "symbol": "XAUUSD.s",
            "spread": "0.16",
            "m5_time": stamp.strftime("%Y-%m-%d %H:%M:%S"),
            "m5_open": "4390.00",
            "m5_high": "4400.00",
            "m5_low": "4388.00",
            "m5_close": "4398.00",
            "m5_tick_volume": "800",
            "m5_ema20": "4392.00",
            "m5_atr14": "6.00",
            "m5_rsi14": "58.00",
            "m5_macd_hist": "0.20",
            "last_scan_reason": "must not reach AI",
            "signal_id": "must not reach AI",
            "ea_state": "idle",
            "order_ticket": "123",
        }
        for prefix, context_time in (
            ("m15", stamp - timedelta(minutes=15)),
            ("h1", stamp - timedelta(hours=1)),
            ("h4", stamp - timedelta(hours=4)),
        ):
            row.update(
                {
                    f"{prefix}_time": context_time.strftime("%Y-%m-%d %H:%M:%S"),
                    f"{prefix}_open": "4380.00",
                    f"{prefix}_high": "4410.00",
                    f"{prefix}_low": "4370.00",
                    f"{prefix}_close": "4398.00",
                    f"{prefix}_tick_volume": "2000",
                    f"{prefix}_ema20": "4385.00",
                    f"{prefix}_atr14": "12.00",
                    f"{prefix}_rsi14": "55.00",
                    f"{prefix}_macd_hist": "0.50",
                }
            )
        rows.append(row)
    return rows


def test_task_id_uses_broker_m5_time():
    assert (
        observer.task_id_for_m5("2026-08-11 16:35:00")
        == "INDEPENDENT_AI_20260811_1635"
    )


def test_market_payload_has_13_closed_m5_and_no_ea_fields():
    rows = make_snapshots(13, last_m5="2026-08-11 16:35:00")

    payload = observer.build_market_only_payload(rows, "2026-08-11 16:35:00")

    assert payload is not None
    encoded = json.dumps(payload, ensure_ascii=False)
    assert len(payload["m5_bars"]) == 13
    assert payload["m5_bars"][-1]["time"] == "2026-08-11 16:35:00"
    assert set(payload["context"]) == {"M15", "H1", "H4"}
    assert "last_scan_reason" not in encoded
    assert "signal_id" not in encoded
    assert "pending_created" not in encoded
    assert "ea_state" not in encoded
    assert "order_ticket" not in encoded


def test_market_payload_deduplicates_m5_time_and_keeps_latest_snapshot():
    rows = make_snapshots(13)
    duplicate = dict(rows[-1])
    duplicate["m5_close"] = "4401.25"
    duplicate["server_time"] = "2026-08-11 16:35:01"

    payload = observer.build_market_only_payload(
        rows + [duplicate], "2026-08-11 16:30:00"
    )

    assert payload is not None
    assert len(payload["m5_bars"]) == 13
    assert payload["m5_bars"][-1]["close"] == 4401.25


def test_payload_returns_none_when_fewer_than_13_closed_m5():
    assert (
        observer.build_market_only_payload(
            make_snapshots(12), "2026-08-11 16:30:00"
        )
        is None
    )


@pytest.mark.parametrize(
    ("decision", "confidence", "effective"),
    [
        ("BUY", 69, "WAIT"),
        ("BUY", 70, "BUY"),
        ("SELL", 70, "SELL"),
        ("WAIT", 99, "WAIT"),
    ],
)
def test_independent_response_confidence_boundary(
    decision: str,
    confidence: int,
    effective: str,
):
    result = observer.validate_independent_response(
        {
            "decision": decision,
            "confidence": confidence,
            "reason": "只观察已收盘行情。",
        }
    )

    assert result["effective_decision"] == effective
    assert result["raw_decision"] == decision


@pytest.mark.parametrize(
    "response",
    [
        {"decision": "HOLD", "confidence": 70, "reason": "无效方向"},
        {"decision": "BUY", "confidence": -1, "reason": "无效置信度"},
        {"decision": "BUY", "confidence": 101, "reason": "无效置信度"},
        {"decision": "BUY", "confidence": True, "reason": "布尔值不是整数"},
        {"decision": "BUY", "confidence": 70, "reason": ""},
    ],
)
def test_independent_response_rejects_invalid_values(response: dict):
    with pytest.raises(ValueError):
        observer.validate_independent_response(response)


def test_event_bar_matches_details_or_signal_id():
    assert observer.event_m5_time(
        {
            "details": "[扫描] TF=M5 | Bar=2026.08.11 16:35 | REJECT",
            "signal_id": "",
        }
    ) == ("2026-08-11 16:35:00", "details_bar")
    assert observer.event_m5_time(
        {
            "details": "expiry=2026-08-11 16:50:00",
            "signal_id": "XAUUSD.s_M5_20260811_163500_BUY_PINBAR_FIB_PA",
        }
    ) == ("2026-08-11 16:35:00", "signal_id")


def test_events_for_m5_keeps_only_same_bar_and_audits_match_method():
    events = [
        {
            "server_time": "2026-08-11 16:40:00",
            "event_type": "local_reject",
            "details": "Bar=2026.08.11 16:35 | REJECT",
            "signal_id": "",
        },
        {
            "server_time": "2026-08-11 16:45:00",
            "event_type": "local_reject",
            "details": "Bar=2026.08.11 16:40 | REJECT",
            "signal_id": "",
        },
    ]

    matched = observer.events_for_m5(events, "2026-08-11 16:35:00")

    assert len(matched) == 1
    assert matched[0]["_m5_match_method"] == "details_bar"


def test_ea_summary_uses_earliest_valid_pending_and_flags_duplicates():
    events = [
        {
            "server_time": "2026-08-11 16:40:02",
            "event_type": "pending_created",
            "direction": "SELL",
            "reason": "第二个挂单",
            "signal_id": "SIG_2",
            "order_ticket": "222",
            "stage": "order",
        },
        {
            "server_time": "2026-08-11 16:40:01",
            "event_type": "pending_created",
            "direction": "BUY",
            "reason": "挂单创建成功",
            "signal_id": "SIG_1",
            "order_ticket": "111",
            "stage": "order",
        },
    ]

    result = observer.summarize_ea_decision(events, {})

    assert result == {
        "decision": "TRADE",
        "direction": "BUY",
        "reason": "挂单创建成功",
        "stage": "order",
        "signal_id": "SIG_1",
        "order_ticket": "111",
        "multiple_pending_created": True,
    }


def test_ea_wait_summary_prefers_latest_reject_then_snapshot_fallback():
    events = [
        {
            "server_time": "2026-08-11 16:40:00",
            "event_type": "local_reject",
            "reason": "第一次拒绝",
            "stage": "price_action",
        },
        {
            "server_time": "2026-08-11 16:40:01",
            "event_type": "ai_reject",
            "reason": "最终AI未放行",
            "stage": "ai",
        },
    ]
    snapshot = {
        "last_scan_reason": "快照拒绝原因",
        "last_scan_stage": "structure",
    }

    result = observer.summarize_ea_decision(events, snapshot)
    fallback = observer.summarize_ea_decision([], snapshot)

    assert result["reason"] == "最终AI未放行"
    assert result["stage"] == "ai"
    assert fallback["reason"] == "快照拒绝原因"
    assert fallback["stage"] == "structure"


@pytest.mark.parametrize(
    ("ai_direction", "ea_direction", "expected"),
    [
        ("WAIT", "WAIT", "AGREE_WAIT"),
        ("BUY", "BUY", "AGREE_DIRECTION"),
        ("SELL", "SELL", "AGREE_DIRECTION"),
        ("WAIT", "BUY", "EA_TRADE_AI_WAIT"),
        ("BUY", "WAIT", "AI_TRADE_EA_WAIT"),
        ("BUY", "SELL", "OPPOSITE_DIRECTION"),
        ("SELL", "BUY", "OPPOSITE_DIRECTION"),
    ],
)
def test_comparison_matrix(
    ai_direction: str,
    ea_direction: str,
    expected: str,
):
    assert (
        observer.classify_comparison(
            {"effective_decision": ai_direction},
            {
                "decision": "TRADE" if ea_direction != "WAIT" else "WAIT",
                "direction": ea_direction,
            },
        )
        == expected
    )


def test_comparison_rejects_impossible_direction():
    with pytest.raises(ValueError):
        observer.classify_comparison(
            {"effective_decision": "HOLD"},
            {"decision": "WAIT", "direction": "WAIT"},
        )


def make_completed_record() -> dict:
    return {
        "task_id": "INDEPENDENT_AI_20260811_1635",
        "m5_server_time": "2026-08-11 16:35:00",
        "beijing_time": "2026-08-11 21:40:00",
        "close_price": 4399.56,
        "ai_raw_decision": "BUY",
        "ai_effective_decision": "BUY",
        "ai_confidence": 75,
        "ai_reason": "上涨动能仍在。",
        "ea_decision": "WAIT",
        "ea_direction": "WAIT",
        "ea_reason": "结构条件没有满足。",
        "comparison_class": "AI_TRADE_EA_WAIT",
        "divergence_explanation": "AI看重动能，EA要求的结构没有形成。",
        "lark_notification_id": "AI_EA_DIVERGENCE_20260811_1635",
        "lark_status": "queued",
        "status": "COMPLETED",
        "error": "",
    }


def test_observer_state_round_trip_is_atomic(tmp_path: Path):
    state = {
        "schema_version": 1,
        "cursor_m5_time": "2026-08-11 16:35:00",
        "active_task": None,
        "primary_counts_by_server_date": {"2026-08-11": 2},
        "consecutive_primary_failures": 0,
        "failure_episode": None,
    }

    observer.save_observer_state(tmp_path, state)

    assert observer.load_observer_state(tmp_path) == state


def test_queue_divergence_card_is_idempotent_across_all_outbox_states(
    tmp_path: Path,
):
    notification_id = "AI_EA_DIVERGENCE_20260811_1635"
    card = {
        "msg_type": "interactive",
        "card": {"header": {"title": {"content": "EA复盘"}}},
    }

    assert (
        observer.queue_observer_card(
            tmp_path, notification_id, "ai_ea_divergence", card, {}
        )
        == "queued"
    )
    assert (
        observer.queue_observer_card(
            tmp_path, notification_id, "ai_ea_divergence", card, {}
        )
        == "queued"
    )
    pending = tmp_path / "Lark_Outbox" / "Pending" / f"{notification_id}.json"
    sent = tmp_path / "Lark_Outbox" / "Sent" / pending.name
    sent.parent.mkdir(parents=True)
    pending.replace(sent)
    assert (
        observer.queue_observer_card(
            tmp_path, notification_id, "ai_ea_divergence", card, {}
        )
        == "already_sent"
    )


def test_record_writer_upserts_csv_and_jsonl_without_secrets(tmp_path: Path):
    record = {
        **make_completed_record(),
        "raw": {
            "api_key": "must-not-survive",
            "webhook": "must-not-survive",
            "nested": {"authorization": "must-not-survive", "safe": "kept"},
        },
    }

    observer.write_comparison_record(tmp_path, record)
    observer.write_comparison_record(tmp_path, {**record, "lark_status": "sent"})

    csv_path = tmp_path / "Independent_AI" / "Comparisons_2026-08-11.csv"
    jsonl_path = tmp_path / "Independent_AI" / "Records_2026-08-11.jsonl"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    text = jsonl_path.read_text(encoding="utf-8")
    assert len(csv_rows) == 1
    assert csv_rows[0]["lark_status"] == "sent"
    assert "must-not-survive" not in text
    assert '"safe": "kept"' in text


def test_divergence_card_is_readable_and_contains_no_debug_fields():
    record = make_completed_record()

    card = observer.build_divergence_card(record)
    encoded = json.dumps(card, ensure_ascii=False)

    assert card["card"]["header"]["title"]["content"] == (
        "独立AI与EA分歧｜XAUUSD.s M5"
    )
    assert "2026-08-11 16:35:00" in encoded
    assert "4399.56" in encoded
    assert "BUY" in encoded
    assert "75" in encoded
    assert "结构条件没有满足" in encoded
    assert "仅用于观察对比，不影响EA交易" in encoded
    assert "HTTP" not in encoded
    assert "api_key" not in encoded
    assert "file_path" not in encoded


def test_observer_system_card_uses_supplied_title_without_security_keyword():
    card = observer._system_card("独立AI接口已恢复", "观察服务恢复正常。", "green")

    assert card["card"]["header"]["title"]["content"] == "独立AI接口已恢复"
    assert "EA复盘" not in json.dumps(card, ensure_ascii=False)


def test_fallback_divergence_explanation_mentions_both_sides():
    explanation = observer.fallback_divergence_explanation(
        {"effective_decision": "BUY", "reason": "短线动能转强"},
        {"direction": "WAIT", "reason": "结构确认不足"},
        "AI_TRADE_EA_WAIT",
    )

    assert "短线动能转强" in explanation
    assert "结构确认不足" in explanation


class FakeClient:
    def __init__(self, replies: list[dict | Exception]):
        self.replies = list(replies)
        self.calls: list[tuple[str, dict, int]] = []

    def call(self, prompt: str, payload: dict, max_tokens: int):
        self.calls.append((prompt, payload, max_tokens))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, {"model": "fake", "total_tokens": 1}


def observer_config(**overrides) -> dict:
    config = {
        "independent_ai_enabled": True,
        "independent_ai_daily_limit": 300,
        "independent_ai_min_confidence": 70,
        "independent_ai_ea_wait_seconds": 120,
        "independent_ai_m5_bars": 13,
        "independent_ai_max_tokens": 350,
        "independent_ai_explanation_max_tokens": 220,
    }
    config.update(overrides)
    return config


def run_observer_cycle(
    tmp_path: Path,
    now_beijing: datetime,
    snapshots: list[dict[str, str]],
    events: list[dict[str, str]],
    client: FakeClient,
):
    return observer.process_independent_ai_cycle(
        config=observer_config(),
        root=tmp_path,
        now_beijing=now_beijing,
        snapshots=snapshots,
        events=events,
        client=client,
        primary_prompt="primary market-only prompt",
        explanation_prompt="explanation prompt",
    )


def test_cycle_bootstraps_then_waits_120_seconds_and_completes_once(tmp_path: Path):
    now = datetime(2026, 8, 11, 21, 35, tzinfo=timezone(timedelta(hours=8)))
    initial = make_snapshots(13, "2026-08-11 16:30:00")
    client = FakeClient([])

    bootstrap = run_observer_cycle(tmp_path, now, initial, [], client)

    assert bootstrap[-1]["status"] == "BOOTSTRAPPED"
    assert client.calls == []
    assert observer.load_observer_state(tmp_path)["cursor_m5_time"] == "2026-08-11 16:30:00"

    newer = make_snapshots(14, "2026-08-11 16:35:00")
    client.replies.append(
        {"decision": "BUY", "confidence": 75, "reason": "短线动能向上。"}
    )
    started = run_observer_cycle(tmp_path, now + timedelta(minutes=5), newer, [], client)
    assert started[-1]["status"] == "WAITING_EA"
    assert len(client.calls) == 1
    assert "last_scan_reason" not in json.dumps(client.calls[0][1])

    too_early = run_observer_cycle(
        tmp_path, now + timedelta(minutes=6, seconds=59), newer, [], client
    )
    assert too_early[-1]["status"] == "WAITING_EA"
    assert len(client.calls) == 1

    events = [
        {
            "server_time": "2026-08-11 16:40:01",
            "event_type": "pending_created",
            "signal_id": "XAUUSD.s_M5_20260811_163500_BUY_PINBAR_FIB_PA",
            "direction": "BUY",
            "reason": "挂单创建成功",
            "stage": "order",
            "order_ticket": "777",
            "details": "expiry=2026-08-11 16:55:01",
        }
    ]
    completed = run_observer_cycle(
        tmp_path, now + timedelta(minutes=7), newer, events, client
    )
    repeated = run_observer_cycle(
        tmp_path, now + timedelta(minutes=8), newer, events, client
    )

    assert completed[-1]["status"] == "COMPLETED"
    assert completed[-1]["comparison_class"] == "AGREE_DIRECTION"
    assert repeated == []
    assert len(client.calls) == 1
    assert not list((tmp_path / "Lark_Outbox" / "Pending").glob("AI_EA_DIVERGENCE_*.json"))


def test_three_primary_failures_alert_once_and_success_recovers_once(tmp_path: Path):
    tz = timezone(timedelta(hours=8))
    initial = make_snapshots(13, "2026-08-11 16:30:00")
    observer.save_observer_state(
        tmp_path,
        {
            **observer.DEFAULT_OBSERVER_STATE,
            "cursor_m5_time": "2026-08-11 16:30:00",
        },
    )
    client = FakeClient(
        [
            RuntimeError("down 1"),
            RuntimeError("down 2"),
            RuntimeError("down 3"),
            RuntimeError("down 4"),
            {"decision": "WAIT", "confidence": 80, "reason": "方向不清晰。"},
        ]
    )

    for index, minute in enumerate((35, 40, 45, 50), 1):
        rows = make_snapshots(13 + index, f"2026-08-11 16:{minute:02d}:00")
        result = run_observer_cycle(
            tmp_path,
            datetime(2026, 8, 11, 21, minute, tzinfo=tz),
            rows,
            [],
            client,
        )
        assert result[-1]["status"] == "AI_FAILED"

    down_cards = list(
        (tmp_path / "Lark_Outbox" / "Pending").glob("INDEPENDENT_AI_API_DOWN_*.json")
    )
    assert len(down_cards) == 1
    assert observer.load_observer_state(tmp_path)["consecutive_primary_failures"] == 4

    rows = make_snapshots(18, "2026-08-11 16:55:00")
    recovered = run_observer_cycle(
        tmp_path,
        datetime(2026, 8, 11, 21, 55, tzinfo=tz),
        rows,
        [],
        client,
    )
    recovery_cards = list(
        (tmp_path / "Lark_Outbox" / "Pending").glob(
            "INDEPENDENT_AI_API_RECOVERED_*.json"
        )
    )
    state = observer.load_observer_state(tmp_path)
    assert recovered[-1]["status"] == "WAITING_EA"
    assert len(recovery_cards) == 1
    assert state["consecutive_primary_failures"] == 0
    assert state["failure_episode"] is None


def test_daily_limit_records_terminal_status_without_calling_client(tmp_path: Path):
    state = {
        **observer.DEFAULT_OBSERVER_STATE,
        "cursor_m5_time": "2026-08-11 16:30:00",
        "primary_counts_by_server_date": {"2026-08-11": 300},
    }
    observer.save_observer_state(tmp_path, state)
    client = FakeClient([])

    result = run_observer_cycle(
        tmp_path,
        datetime(2026, 8, 11, 21, 40, tzinfo=timezone(timedelta(hours=8))),
        make_snapshots(14, "2026-08-11 16:35:00"),
        [],
        client,
    )

    assert result[-1]["status"] == "DAILY_LIMIT_REACHED"
    assert client.calls == []
    assert observer.load_observer_state(tmp_path)["cursor_m5_time"] == "2026-08-11 16:35:00"


def test_explanation_failure_uses_fallback_and_still_queues_divergence(tmp_path: Path):
    tz = timezone(timedelta(hours=8))
    snapshots = make_snapshots(13, "2026-08-11 16:35:00")
    observer.save_observer_state(
        tmp_path,
        {
            **observer.DEFAULT_OBSERVER_STATE,
            "cursor_m5_time": "2026-08-11 16:30:00",
            "active_task": {
                "task_id": "INDEPENDENT_AI_20260811_1635",
                "m5_server_time": "2026-08-11 16:35:00",
                "first_seen_beijing": "2026-08-11 21:35:00",
                "deadline_beijing": "2026-08-11 21:37:00",
                "market_payload": observer.build_market_only_payload(
                    snapshots, "2026-08-11 16:35:00"
                ),
                "ai": {
                    "raw_decision": "BUY",
                    "effective_decision": "BUY",
                    "confidence": 76,
                    "reason": "短线动能转强",
                },
                "primary_usage": {"model": "fake", "total_tokens": 1},
                "status": "WAITING_EA",
            },
        },
    )
    client = FakeClient([RuntimeError("explanation unavailable")])

    result = run_observer_cycle(
        tmp_path,
        datetime(2026, 8, 11, 21, 37, tzinfo=tz),
        snapshots,
        [],
        client,
    )

    assert result[-1]["comparison_class"] == "AI_TRADE_EA_WAIT"
    assert result[-1]["status"] == "COMPLETED"
    cards = list(
        (tmp_path / "Lark_Outbox" / "Pending").glob("AI_EA_DIVERGENCE_*.json")
    )
    assert len(cards) == 1
    saved = json.loads(cards[0].read_text(encoding="utf-8"))
    assert "短线动能转强" in json.dumps(saved, ensure_ascii=False)
    assert "explanation unavailable" not in json.dumps(saved, ensure_ascii=False)
