from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.ai_review_service import (
    LarkWebhookClient,
    collect_notification_issues,
    process_lark_outbox,
)
from tools.ai_trade_manager import _trade_group_for_magic
from tools.review_core import _snapshot_time_at_or_before, analyze_unfilled_candidates
from tools.review_core import collect_server_sl_snapshots, parse_server_sl_details
from tools.daily_review_renderer import render_candidate_card_markdown
from tools.trade_lifecycle import _mfe_mae_from_bars, _mfe_mae_from_ticks


class _LarkResponse:
    status = 200

    def __init__(self, body: str = '{"code":0,"msg":"success"}'):
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self._body


def test_send_card_returns_structured_receipt():
    client = LarkWebhookClient(
        "https://open.larksuite.com/open-apis/bot/v2/hook/test",
        retries=0,
    )
    client.opener = lambda request, timeout: _LarkResponse()
    receipt = client.send_card({"msg_type": "interactive"})
    assert receipt["success"] is True
    assert receipt["http_status"] == 200
    assert receipt["lark_code"] == 0
    assert receipt["attempt"] == 1
    assert receipt["sent_at"]


def test_process_lark_outbox_persists_structured_receipt(tmp_path: Path):
    webhook_file = tmp_path / "Config" / "lark_webhook.txt"
    webhook_file.parent.mkdir(parents=True)
    webhook_file.write_text("https://open.larksuite.com/open-apis/bot/v2/hook/test", encoding="utf-8")
    pending = tmp_path / "Lark_Outbox" / "Pending" / "TEST.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "notification_id": "TEST",
                "notification_type": "parallel_ai_difference",
                "card": {
                    "msg_type": "interactive",
                    "card": {
                        "schema": "2.0",
                        "header": {"title": {"tag": "plain_text", "content": "测试"}},
                        "body": {"elements": [{"tag": "markdown", "content": "测试"}]},
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def opener(request, timeout):
        return _LarkResponse()

    process_lark_outbox(
        {
            "lark_outbox_enabled": True,
            "lark_webhook_environment": "TEST_UNUSED_LARK_WEBHOOK",
            "lark_webhook_file": "Config/lark_webhook.txt",
            "parallel_ai_lark_webhook": "https://open.larksuite.com/open-apis/bot/v2/hook/backup",
            "lark_retries": 0,
        },
        tmp_path,
        opener=opener,
        sleeper=lambda _: None,
    )
    sent = json.loads(
        (tmp_path / "Lark_Outbox" / "Sent" / "TEST.json").read_text(encoding="utf-8")
    )
    assert sent["delivery_state"] == "sent"
    assert sent["http_status"] == 200
    assert sent["lark_code"] == 0
    assert sent["retry_count"] == 0
    assert sent["target_group"] == "parallel_ai_backup"


def test_collect_notification_issues_marks_unknown(tmp_path: Path):
    uncertain = tmp_path / "Lark_Outbox" / "Uncertain"
    uncertain.mkdir(parents=True)
    (uncertain / "OLD_EVENT.json").write_text(
        json.dumps(
            {
                "notification_id": "OLD_EVENT",
                "delivery_state": "unknown",
                "note": "历史发送回执缺失",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    issues = collect_notification_issues(tmp_path)
    assert any("状态未知" in issue and "历史发送回执缺失" in issue for issue in issues)


def test_mfe_mae_from_bars_buy_sell():
    buy_bars = [
        (100.0, 99.0, 103.0, 98.0, 98.0, 0, 0, 0),
        (120.0, 99.0, 105.0, 97.0, 97.0, 0, 0, 0),
    ]
    buy = _mfe_mae_from_bars(buy_bars, "BUY", 100.0, 4.0)
    assert buy["mfe_r"] == pytest.approx(1.25)
    assert buy["mae_r"] == pytest.approx(-0.75)
    sell_bars = [
        (100.0, 99.0, 103.0, 98.0, 98.0, 0, 0, 0),
        (120.0, 99.0, 105.0, 96.0, 96.0, 0, 0, 0),
    ]
    sell = _mfe_mae_from_bars(sell_bars, "SELL", 100.0, 4.0)
    assert sell["mfe_r"] == pytest.approx(1.0)
    assert sell["mae_r"] == pytest.approx(-1.25)


def test_mfe_mae_from_ticks_buy_sell():
    ticks = [
        {"time": 100.0, "bid": 99.0, "ask": 99.2},
        {"time": 120.0, "bid": 104.0, "ask": 104.2},
    ]
    buy = _mfe_mae_from_ticks(ticks, "BUY", 100.0, 4.0)
    assert buy["mfe_r"] == pytest.approx(1.0)
    assert buy["mae_r"] == pytest.approx(-0.25)
    sell = _mfe_mae_from_ticks(ticks, "SELL", 100.0, 4.0)
    assert sell["mfe_r"] == pytest.approx(0.2)
    assert sell["mae_r"] == pytest.approx(-1.05)


def test_trade_group_magic_zero_counts_as_manual():
    """桌面手动下单的 magic=0 现在按“人工”归类（2026-09-15 用户确认方案A）。"""
    assert _trade_group_for_magic(0, 2026072902, 2026072903) == "manual"
    assert _trade_group_for_magic(2026072901, 2026072902, 2026072903) == "ea"
    assert _trade_group_for_magic(2026072902, 2026072902, 2026072903) == "ai"
    assert _trade_group_for_magic(2026072903, 2026072902, 2026072903) is None


def test_snapshot_time_at_or_before_contains_fill_bar():
    bars = [
        {"beijing_time": "2026-08-21 15:15:00"},
        {"beijing_time": "2026-08-21 15:20:00"},
    ]
    from datetime import datetime

    dt = datetime(2026, 8, 21, 15, 15, 9)
    assert _snapshot_time_at_or_before(bars, dt) == "2026-08-21 15:15:00"


def test_wait_missed_replay_starts_at_containing_bar(tmp_path: Path):
    def event(kind, ts, reason="", details=""):
        return {
            "server_time": ts,
            "beijing_time": ts,
            "event_type": kind,
            "signal_id": "S1",
            "direction": "SELL",
            "reason": reason,
            "details": details,
        }

    events = [
        event(
            "candidate",
            "2026-08-12 09:55:00",
            details=json.dumps({"trade_plan": {"entry": 100.0, "sl": 102.0}}),
        ),
        event("ai_allow", "2026-08-12 09:55:05"),
        event("pending_created", "2026-08-12 09:55:10"),
        event("pending_wait_started", "2026-08-12 10:00:00", reason="距离入场位过近"),
        event("pending_wait_missed", "2026-08-12 10:05:09", reason="价格越过原入场位"),
    ]
    bars = [
        {"beijing_time": "2026-08-12 10:05:00", "m5_high": 104.0, "m5_low": 96.0},
        {"beijing_time": "2026-08-12 10:10:00", "m5_high": 100.0, "m5_low": 99.0},
    ]
    m1_bars = [
        {"beijing_time": "2026-08-12 10:01:00", "m1_high": 102.5, "m1_low": 99.0},
        {"beijing_time": "2026-08-12 10:02:00", "m1_high": 100.0, "m1_low": 96.0},
    ]
    result = analyze_unfilled_candidates(
        events, bars, m1_loader=lambda start, end: m1_bars
    )
    item = result["items"][0]
    assert item["replay_resolution"] == "M1"
    assert item["first_touch"] == "SL"


def test_parse_server_sl_details():
    details = (
        "source=AFTER_FILL | planned_initial_sl=4394.98 | submitted_sl=4394.98 "
        "| server_sl_before_modify=0.00 | requested_new_sl=0.00 "
        "| server_sl_after_modify=4394.98 | retcode=0 | retcode_description="
    )
    parsed = parse_server_sl_details(details)
    assert parsed["source"] == "AFTER_FILL"
    assert parsed["planned_initial_sl"] == "4394.98"


def test_collect_server_sl_snapshots_parses_csv(tmp_path: Path):
    raw = tmp_path / "Raw_Data"
    raw.mkdir(parents=True)
    (raw / "Strategy_Events_2026-08-24.csv").write_text(
        "server_time,beijing_time,event_type,signal_id,stage,outcome,reason,details,order_ticket,position_id,deal_ticket,direction,volume,price,profit\n"
        '2026-08-24 10:00:00,2026-08-24 15:00:00,server_sl_snapshot,SIG1,position,pass,AFTER_FILL,"source=AFTER_FILL | planned_initial_sl=4394.98 | submitted_sl=4394.98 | server_sl_before_modify=0.00 | requested_new_sl=0.00 | server_sl_after_modify=4394.98 | retcode=0 | retcode_description=",0,738218235,0,SELL,0.11,0.00,0.00\n',
        encoding="utf-8",
    )
    snapshots = collect_server_sl_snapshots(
        tmp_path, "2026.08.24 00:00:00", "2026.08.24 23:59:59"
    )
    assert len(snapshots) == 1
    assert snapshots[0]["position_id"] == "738218235"
    assert snapshots[0]["server_sl_after_modify"] == 4394.98


def test_server_sl_modify_success_and_failure_flags():
    success = parse_server_sl_details(
        "source=TP1_BREAKEVEN | requested_new_sl=4390.42 "
        "| server_sl_after_modify=4390.42 | modify_success=true | retcode=0"
    )
    failure = parse_server_sl_details(
        "source=TP1_BREAKEVEN | requested_new_sl=4390.42 "
        "| server_sl_after_modify=4394.98 | modify_success=false | retcode=10015"
    )
    legacy = parse_server_sl_details("source=AFTER_FILL | planned_initial_sl=4394.98")
    assert success["modify_success"] == "true"
    assert failure["modify_success"] == "false"
    assert failure["requested_new_sl"] == "4390.42"
    assert failure["server_sl_after_modify"] == "4394.98"
    assert "modify_success" not in legacy


def test_candidate_card_hides_rule_blocked_ai_candidates():
    payload = {
        "candidate_rows": [],
        "ai_candidate_rows": [
            {"signal_id": "SIG_BLOCKED", "final_state": "rule_blocked", "execution_status": "NOT_SENT"},
            {"signal_id": "SIG_KEEP", "final_state": "expired", "execution_status": "PENDING_ACTIVE"},
        ],
    }
    markdown = render_candidate_card_markdown("2026-08-24", payload)
    assert "SIG_BLOCKED" not in markdown
    assert "Parallel AI候选：** 1个" in markdown
