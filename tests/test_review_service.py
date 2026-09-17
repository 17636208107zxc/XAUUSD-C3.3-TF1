from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tools.ai_review_service as review_service

from tools.review_core import (
    ApiBudget,
    EventMergeQueue,
    beijing_date_key,
    is_scheduled_monitor_due,
    list_missing_review_dates,
    compact_monitor_payload,
    build_daily_payload,
    apply_local_daily_facts,
    rows_for_server_window,
    validate_daily_response,
    validate_monitor_response,
    build_candidate_lifecycle_rows,
    merge_daily_trade_rows,
)
from tools.ai_review_service import run_once

UTC = timezone.utc


def test_daily_prompt_separates_rule_scan_from_actual_ea_trades() -> None:
    prompt = (Path(__file__).resolve().parents[1] / "config" / "daily_review_prompt.txt").read_text(encoding="utf-8")
    assert "opportunity_opened 不是 EA 当日实际开单笔数" in prompt
    assert "EA实际开单以 statistics.trade_count" in prompt
    assert "EA生命周期" in prompt


def _candidate_event(
    signal_id: str,
    server_time: str,
    direction: str = "SELL",
    route: str = "FIB_PA",
) -> dict[str, str]:
    return {
        "server_time": server_time,
        "beijing_time": server_time,
        "event_type": "candidate",
        "signal_id": signal_id,
        "direction": direction,
        "order_ticket": "0",
        "position_id": "0",
        "deal_ticket": "0",
        "reason": "本地候选已形成",
        "details": json.dumps(
            {
                "signal_route": route,
                "trade_plan": {"entry": 4382.05, "sl": 4386.14, "tp1": 4377.96, "rr_to_tp1": 1.0},
                "bars": [{"time": "2026.08.11 19:45:00"}],
            },
            ensure_ascii=False,
        ),
    }


def test_mixed_trade_sources_keep_legacy_missing_positions_and_prefer_lifecycle_rows():
    events = [
        {
            "server_time": "2026-08-11 06:46:38",
            "beijing_time": "2026-08-11 11:46:38",
            "event_type": "pending_filled",
            "signal_id": "OLD",
            "position_id": "718041351",
            "direction": "SELL",
            "volume": "0.07",
            "price": "4415.34",
            "profit": "0",
        },
        {
            "server_time": "2026-08-11 11:07:23",
            "beijing_time": "2026-08-11 16:07:23",
            "event_type": "position_closed",
            "signal_id": "OLD",
            "position_id": "718041351",
            "direction": "SELL",
            "volume": "0.03",
            "price": "4389.70",
            "profit": "179.49",
            "reason": "DEAL_REASON_CLIENT",
        },
        {
            "server_time": "2026-08-11 07:42:30",
            "beijing_time": "2026-08-11 12:42:30",
            "event_type": "tp1_reached",
            "signal_id": "OLD",
            "position_id": "718041351",
        },
        {
            "server_time": "2026-08-11 08:11:38",
            "beijing_time": "2026-08-11 13:11:38",
            "event_type": "tp2_reached",
            "signal_id": "OLD",
            "position_id": "718041351",
        },
        {
            "server_time": "2026-08-11 08:11:38",
            "beijing_time": "2026-08-11 13:11:38",
            "event_type": "runner_started",
            "signal_id": "OLD",
            "position_id": "718041351",
        },
        {
            "server_time": "2026-08-11 19:50:33",
            "beijing_time": "2026-08-12 00:50:33",
            "event_type": "pending_filled",
            "signal_id": "NEW",
            "position_id": "720560839",
            "direction": "SELL",
            "volume": "0.12",
            "price": "4382.05",
            "profit": "0",
        },
    ]
    lifecycle = [
        {
            "position_id": "720560839",
            "direction": "SELL",
            "open_time": "2026.08.11 19:50:34",
            "close_time": "2026.08.11 21:00:00",
            "whole_trade_net": 88.20,
            "today_net": 88.20,
            "net_profit": 88.20,
            "status": "closed",
            "initial_risk": 49.08,
            "data_source": "lifecycle",
        }
    ]

    merged = merge_daily_trade_rows(events, lifecycle)

    assert [row["position_id"] for row in merged] == ["718041351", "720560839"]
    assert merged[0]["open_time"] == "2026-08-11 06:46:38"
    assert merged[0]["whole_trade_net"] == 179.49
    assert merged[0]["data_source"] == "legacy_event"
    assert merged[0]["tp1_done"] is True
    assert merged[0]["tp2_done"] is True
    assert merged[0]["runner_done"] is True
    assert merged[1]["whole_trade_net"] == 88.20
    assert merged[1]["data_source"] == "lifecycle"

    payload = build_daily_payload("2026-08-11", [], events, [], lifecycle_trades=lifecycle)
    assert payload["statistics"]["trade_count"] == 2
    assert payload["statistics"]["win_count"] == 2
    assert payload["statistics"]["net_profit"] == 267.69


def test_candidate_lifecycle_reports_exact_terminal_outcomes_without_generic_reason():
    filled = _candidate_event("FILLED", "2026-08-11 06:45:00")
    expired = _candidate_event("EXPIRED", "2026-08-11 19:19:59")
    missed = _candidate_event("MISSED", "2026-08-11 23:24:59", direction="BUY")
    incomplete = _candidate_event("INCOMPLETE", "2026-08-11 23:30:00")
    events = [
        filled,
        expired,
        missed,
        incomplete,
        {**filled, "server_time": "2026-08-11 06:45:01", "event_type": "ai_allow", "details": "allow=true | confidence=78 | threshold=70", "reason": "审核通过"},
        {**filled, "server_time": "2026-08-11 06:45:01", "event_type": "pending_created", "order_ticket": "718041351", "details": "expiry=2026-08-11 07:00:01", "reason": "挂单创建成功"},
        {**filled, "server_time": "2026-08-11 06:46:38", "event_type": "pending_filled", "order_ticket": "718041351", "position_id": "718041351", "deal_ticket": "420152031", "reason": "DEAL_REASON_CLIENT", "details": "挂单已成交并进入持仓管理"},
        {**expired, "server_time": "2026-08-11 19:20:00", "event_type": "ai_allow", "details": "allow=true | confidence=76 | threshold=70", "reason": "审核通过"},
        {**expired, "server_time": "2026-08-11 19:20:00", "event_type": "pending_created", "order_ticket": "720516374", "details": "expiry=2026-08-11 19:35:00", "reason": "挂单创建成功"},
        {**expired, "server_time": "2026-08-11 19:34:59", "event_type": "pending_closed", "order_ticket": "720516374", "reason": "ORDER_STATE_EXPIRED", "details": "挂单到期"},
        {**missed, "server_time": "2026-08-11 23:25:00", "event_type": "ai_allow", "details": "allow=true | confidence=76 | threshold=70", "reason": "审核通过"},
        {**missed, "server_time": "2026-08-11 23:25:00", "event_type": "pending_wait_started", "reason": "挂单距离不足，等待经纪商最小距离满足", "details": "未创建真实订单"},
        {**missed, "server_time": "2026-08-11 23:25:04", "event_type": "pending_wait_missed", "reason": "买入挂单价已被Ask达到或越过", "details": "原入场价已被市场触达，信号作废，未追价"},
    ]

    rows = build_candidate_lifecycle_rows(events)

    assert [row["outcome"] for row in rows] == [
        "filled",
        "expired",
        "wait_missed",
        "status_incomplete",
    ]
    assert rows[0]["position_id"] == "718041351"
    assert rows[0]["ai_confidence"] == 78
    assert rows[1]["outcome_time"] == "2026-08-11 19:34:59"
    assert rows[2]["outcome_reason"] == (
        "挂单距离不足，等待经纪商最小距离满足；随后买入挂单价已被Ask达到或越过，"
        "信号作废且未追价"
    )
    assert rows[3]["outcome_reason"] == "缺少候选后续审核、挂单或失效事件，无法判断最终状态"
    assert all(row["outcome_reason"] != "其他原因" for row in rows)


def test_broker_window_accepts_mt5_dotted_bounds_for_hyphenated_csv_times(tmp_path: Path):
    raw = tmp_path / "Raw_Data"
    raw.mkdir()
    (raw / "Market_Snapshots_2026-08-10.csv").write_text(
        "server_time,m5_close\n"
        "2026-08-10 00:59:59,4342.40\n"
        "2026-08-10 01:00:00,4343.10\n"
        "2026-08-10 23:58:00,4389.36\n"
        "2026-08-10 23:58:01,4389.10\n",
        encoding="utf-8-sig",
    )

    rows = rows_for_server_window(
        tmp_path,
        "Market_Snapshots",
        "2026.08.10 01:00:00",
        "2026.08.10 23:58:00",
    )

    assert [row["server_time"] for row in rows] == [
        "2026-08-10 01:00:00",
        "2026-08-10 23:58:00",
    ]


def test_daily_facts_do_not_describe_partial_realized_profit_as_floating_profit():
    response = {
        "trade_statistics": {},
        "block_reason_counts": {},
        "strategy_market_fit": "第二笔收盘浮盈45.33美元并跨日持有。",
        "representative_trades": [
            {
                "trade_id": "716669486",
                "type": "最大盈利",
                "reason": "收盘前浮盈45.33美元，持仓跨日。",
                "execution_status": "正常",
            }
        ],
        "markdown_report": "未平仓浮盈抵消了部分亏损。",
    }
    payload = {
        "statistics": {},
        "actual_trade_rows": [
            {
                "position_id": "716669486",
                "status": "open_at_day_end",
                "net_profit": 45.33,
            }
        ],
    }

    result = apply_local_daily_facts(response, payload)

    assert "浮盈" not in json.dumps(result, ensure_ascii=False)
    assert result["representative_trades"][0]["type"] == "其他典型"
    assert result["representative_trades"][0]["reason"] == (
        "该交易在经纪商收盘时仍有剩余仓位；截至收盘，"
        "已平仓部分实现净盈利45.33 USD，整笔交易结果尚未确定。"
    )


class _LarkResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return b'{"code":0,"msg":"success"}'


def _write_lark_outbox_item(root: Path, notification_id: str = "OPEN_417765103") -> Path:
    path = root / "Lark_Outbox" / "Pending" / f"{notification_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "notification_id": notification_id,
                "notification_type": "open_trade",
                "created_server": "2026-08-10 16:31:04",
                "deal_ticket": "417765103",
                "attempts": 0,
                "card": {
                    "msg_type": "interactive",
                    "card": {
                        "header": {"title": {"content": "open"}},
                        "body": {
                            "elements": [
                                {"tag": "markdown", "content": "开仓测试内容"}
                            ]
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_security_keyword_is_footer_not_title_and_is_idempotent():
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "EA复盘｜交易#1｜BUY｜开仓",
                }
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "**EA复盘｜开仓通知**\n交易内容",
                    }
                ]
            },
        },
    }

    first = review_service.ensure_lark_security_keyword(card, "EA复盘")
    snapshot = copy.deepcopy(first)
    second = review_service.ensure_lark_security_keyword(first, "EA复盘")

    assert first["card"]["header"]["title"]["content"] == "交易#1｜BUY｜开仓"
    assert first["card"]["body"]["elements"][0]["content"].startswith(
        "**开仓通知**"
    )
    footers = [
        element
        for element in first["card"]["body"]["elements"]
        if element.get("content") == "安全校验：EA复盘"
    ]
    assert len(footers) == 1
    assert second == snapshot


def test_empty_security_keyword_does_not_change_card():
    original = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"content": "交易#1｜BUY｜开仓"}},
            "body": {"elements": [{"tag": "markdown", "content": "交易内容"}]},
        },
    }

    assert review_service.ensure_lark_security_keyword(copy.deepcopy(original), "") == original


def test_security_keyword_cleanup_preserves_later_business_text():
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"content": "EA复盘 | 每日复盘｜2026-08-11"}},
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "**EA复盘 | 每日复盘**\n备注：EA复盘数据已核对。",
                    }
                ]
            },
        },
    }

    result = review_service.ensure_lark_security_keyword(card, "EA复盘")

    assert result["card"]["header"]["title"]["content"] == "每日复盘｜2026-08-11"
    assert result["card"]["body"]["elements"][0]["content"] == (
        "**每日复盘**\n备注：EA复盘数据已核对。"
    )


def test_lark_outbox_success_moves_item_to_sent_and_does_not_resend(tmp_path: Path):
    pending = _write_lark_outbox_item(tmp_path)
    calls: list[str] = []
    sent_cards: list[dict] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        sent_cards.append(json.loads(request.data.decode("utf-8")))
        return _LarkResponse()

    config = {
        "lark_outbox_enabled": True,
        "lark_webhook_environment": "TEST_UNUSED_LARK_WEBHOOK",
        "lark_webhook_file": "Config/lark_webhook.txt",
        "lark_retries": 0,
    }
    webhook_file = tmp_path / "Config" / "lark_webhook.txt"
    webhook_file.parent.mkdir(parents=True)
    webhook_file.write_text("https://open.larksuite.com/open-apis/bot/v2/hook/test", encoding="utf-8")

    first = review_service.process_lark_outbox(config, tmp_path, opener=opener, sleeper=lambda _: None)
    second = review_service.process_lark_outbox(config, tmp_path, opener=opener, sleeper=lambda _: None)

    assert first == [{"notification_id": "OPEN_417765103", "status": "sent"}]
    assert second == []
    assert calls == ["https://open.larksuite.com/open-apis/bot/v2/hook/test"]
    assert "EA复盘" in json.dumps(sent_cards[0], ensure_ascii=False)
    assert not pending.exists()
    assert (tmp_path / "Lark_Outbox" / "Sent" / pending.name).exists()


def test_lark_outbox_failure_keeps_pending_item_and_records_attempt(tmp_path: Path):
    pending = _write_lark_outbox_item(tmp_path)

    def opener(request, timeout):
        response = _LarkResponse()
        response.read = lambda: b'{"code":19024,"msg":"keyword rejected"}'
        return response

    config = {
        "lark_outbox_enabled": True,
        "lark_webhook_environment": "TEST_UNUSED_LARK_WEBHOOK",
        "lark_webhook_file": "Config/lark_webhook.txt",
        "lark_retries": 1,
        "lark_retry_delay_seconds": 0,
    }
    webhook_file = tmp_path / "Config" / "lark_webhook.txt"
    webhook_file.parent.mkdir(parents=True)
    webhook_file.write_text("https://open.larksuite.com/open-apis/bot/v2/hook/test", encoding="utf-8")

    result = review_service.process_lark_outbox(config, tmp_path, opener=opener, sleeper=lambda _: None)
    saved = json.loads(pending.read_text(encoding="utf-8"))

    assert result == [{"notification_id": "OPEN_417765103", "status": "failed"}]
    assert saved["attempts"] == 1
    assert "business error 19024" in saved["last_error"]


def test_parallel_ai_outbox_routes_to_backup_webhook(tmp_path: Path):
    para = _write_lark_outbox_item(tmp_path, "PARALLEL_AI_V2_TEST")
    para_data = json.loads(para.read_text(encoding="utf-8"))
    para_data["notification_type"] = "parallel_ai_difference"
    para.write_text(json.dumps(para_data, ensure_ascii=False), encoding="utf-8")
    _write_lark_outbox_item(tmp_path, "OPEN_417765103")

    urls: list[str] = []

    def opener(request, timeout):
        urls.append(request.full_url)
        return _LarkResponse()

    config = {
        "lark_outbox_enabled": True,
        "lark_webhook_environment": "TEST_UNUSED_LARK_WEBHOOK",
        "lark_webhook_file": "Config/lark_webhook.txt",
        "parallel_ai_lark_webhook": "https://open.larksuite.com/open-apis/bot/v2/hook/backup",
        "lark_retries": 0,
    }
    webhook_file = tmp_path / "Config" / "lark_webhook.txt"
    webhook_file.parent.mkdir(parents=True)
    webhook_file.write_text("https://open.larksuite.com/open-apis/bot/v2/hook/prod", encoding="utf-8")

    review_service.process_lark_outbox(config, tmp_path, opener=opener, sleeper=lambda _: None)

    assert "https://open.larksuite.com/open-apis/bot/v2/hook/backup" in urls
    assert "https://open.larksuite.com/open-apis/bot/v2/hook/prod" in urls
    assert len(urls) == 2


def test_ambiguous_transport_failure_moves_item_to_uncertain(tmp_path: Path):
    pending = _write_lark_outbox_item(tmp_path, "716669486_OPEN")

    def opener(request, timeout):
        raise TimeoutError("response timeout after request may have been accepted")

    config = {
        "lark_outbox_enabled": True,
        "lark_webhook_environment": "TEST_UNUSED_LARK_WEBHOOK",
        "lark_webhook_file": "Config/lark_webhook.txt",
        "lark_retries": 0,
    }
    webhook_file = tmp_path / "Config" / "lark_webhook.txt"
    webhook_file.parent.mkdir(parents=True)
    webhook_file.write_text(
        "https://open.larksuite.com/open-apis/bot/v2/hook/test", encoding="utf-8"
    )

    result = review_service.process_lark_outbox(
        config, tmp_path, opener=opener, sleeper=lambda _: None
    )

    assert result == [{"notification_id": "716669486_OPEN", "status": "uncertain"}]
    assert not pending.exists()
    saved = tmp_path / "Lark_Outbox" / "Uncertain" / pending.name
    assert saved.exists()
    assert "response timeout" in json.loads(saved.read_text(encoding="utf-8"))["last_error"]


class _SuccessfulCommentaryClient:
    def call(self, system_prompt, payload, max_tokens):
        assert system_prompt == "strict prompt"
        assert payload["event"] == "open_trade_facts"
        assert max_tokens == 220
        return (
            {
                "reason": "价格处于上涨方向，回调后获得反转确认。",
                "comment": "满足既定条件后按计划开仓。",
            },
            {"prompt_tokens": 10, "completion_tokens": 8},
        )


class _FailingCommentaryClient:
    def call(self, system_prompt, payload, max_tokens):
        raise RuntimeError("DeepSeek unavailable")


def test_trade_commentary_does_not_consume_monitor_or_daily_budget(tmp_path: Path):
    budget = ApiBudget(tmp_path / "usage.json", monitor_limit=29, daily_limit=1)
    before = budget.counts("2026-08-11")

    result = review_service.generate_trade_commentary(
        "open_trade_facts",
        {"direction": "BUY"},
        _SuccessfulCommentaryClient(),
        "strict prompt",
    )

    assert result["degraded"] is False
    assert result["reason"].startswith("价格处于上涨")
    assert budget.counts("2026-08-11") == before


def test_trade_commentary_failure_uses_local_fallback():
    result = review_service.generate_trade_commentary(
        "final_trade_facts",
        {"direction": "SELL", "net_profit": -46.83, "close_reason": "初始止损"},
        _FailingCommentaryClient(),
        "strict prompt",
    )

    assert result["degraded"] is True
    assert result["reason"]
    assert result["comment"]


def test_trade_commentary_failure_keeps_rich_local_open_comment() -> None:
    result = review_service.generate_trade_commentary(
        "open_trade_facts",
        {
            "direction": "SELL",
            "signal_route": "BOTH",
            "context_loaded": True,
            "fib_retracement": 56.0351,
            "h_attempt": 2,
            "ema_distance_usd": 2.6093,
            "ma_confluence": True,
            "pattern": "Engulfing + Strong Reversal",
            "initial_sl": 4386.14,
            "initial_risk": 49.08,
            "tp1_price": 4377.96,
            "tp2_price": 4373.87,
            "ai_allow_trade": True,
        },
        _FailingCommentaryClient(),
        "strict prompt",
    )

    assert result["degraded"] is True
    assert "Fib回调56.0%" in result["reason"]
    assert "双路径" in result["comment"]
    assert "49.08 USD" in result["comment"]
    assert "第一目标1R，第二目标2R" in result["comment"]


def test_schema2_trade_facts_are_enriched_rendered_and_sent_once(tmp_path: Path):
    pending = tmp_path / "Lark_Outbox" / "Pending" / "718041351_OPEN.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "notification_id": "718041351_OPEN",
                "notification_type": "open_trade_facts",
                "position_id": "718041351",
                "deal_ticket": "417765103",
                "attempts": 0,
                "facts": {
                    "position_id": "718041351",
                    "direction": "SELL",
                    "open_time": "2026-08-11 06:46:00",
                    "entry_price": 4415.34,
                    "initial_volume": 0.07,
                    "initial_sl": 4422.03,
                    "initial_risk": 46.83,
                    "tp1_price": 4408.65,
                    "tp2_price": 4401.96,
                    "ai_allow_trade": True,
                    "ai_confidence": 78,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    webhook_file = tmp_path / "Config" / "lark_webhook.txt"
    webhook_file.parent.mkdir(parents=True)
    webhook_file.write_text(
        "https://open.larksuite.com/open-apis/bot/v2/hook/test", encoding="utf-8"
    )
    sent_cards: list[dict] = []

    def opener(request, timeout):
        sent_cards.append(json.loads(request.data.decode("utf-8")))
        return _LarkResponse()

    result = review_service.process_lark_outbox(
        {
            "lark_outbox_enabled": True,
            "lark_webhook_environment": "TEST_UNUSED_LARK_WEBHOOK",
            "lark_webhook_file": "Config/lark_webhook.txt",
            "lark_retries": 0,
            "trade_commentary_enabled": True,
        },
        tmp_path,
        opener=opener,
        sleeper=lambda _: None,
        commentary_client=_SuccessfulCommentaryClient(),
        commentary_prompt="strict prompt",
    )

    assert result == [{"notification_id": "718041351_OPEN", "status": "sent"}]
    rendered = json.dumps(sent_cards[0], ensure_ascii=False)
    assert "交易#718041351｜SELL｜开仓" in rendered
    assert "EA已生成SELL方向候选" in rendered
    assert "初始风险 46.83 USD（1R）" in rendered
    assert "2R ≈ 93.66 USD" in rendered
    assert "满足既定条件后按计划开仓。" in rendered
    assert "价格处于上涨方向，回调后获得反转确认。" not in rendered
    assert "Order" not in rendered
    assert "Deal" not in rendered
    assert (tmp_path / "Lark_Outbox" / "Sent" / pending.name).exists()


def test_daily_lark_card_template_uses_business_title_without_security_keyword():
    card = review_service.build_daily_lark_card(
        "2026-08-11",
        {"statistics": {}, "market_metrics": {}, "ai_reject_analysis": {}},
        {},
    )

    assert card["card"]["header"]["title"]["content"] == "每日复盘｜2026-08-11"
    first_content = card["card"]["body"]["elements"][0]["content"]
    assert first_content.startswith("## 1. 今日行情")
    rendered = json.dumps(card, ensure_ascii=False)
    assert "EA复盘" not in card["card"]["header"]["title"]["content"]
    assert rendered.count("**安全校验：** EA复盘") == 1


def test_daily_lark_card_distinguishes_closed_trades_from_positions_open_at_close():
    card = review_service.build_daily_lark_card(
        "2026-08-10",
        {
            "statistics": {"trade_count": 2, "win_count": 0, "loss_count": 1, "net_profit": -9.27},
            "market_metrics": {},
            "ai_reject_analysis": {},
            "actual_trade_rows": [
                {"status": "closed", "net_profit": -53.37},
                {"status": "open_at_day_end", "net_profit": 44.10},
            ],
        },
        {},
    )
    rendered = json.dumps(card, ensure_ascii=False)

    assert "**当日开单：** 2笔" in rendered
    assert "**盈利 / 亏损：** 0 / 1" in rendered
    assert "已实现净盈亏：** **-9.27 USD**" in rendered


def test_cross_day_close_shows_today_and_whole_trade_without_double_counting():
    trades = [
        {
            "position_id": "716669486",
            "direction": "BUY",
            "open_server": "2026-08-10 20:50:16",
            "close_server": "2026-08-11 02:31:21",
            "today_net": 6.97,
            "whole_trade_net": 52.30,
            "final_r": 1.12,
            "status": "closed_cross_day",
            "tp1_done": True,
            "tp2_done": True,
            "runner_done": True,
        }
    ]

    payload = build_daily_payload(
        "2026-08-11", [], [], [], lifecycle_trades=trades
    )

    assert payload["statistics"]["net_profit"] == 6.97
    assert payload["actual_trade_rows"][0]["whole_trade_net"] == 52.30
    assert payload["actual_trade_rows"][0]["status"] == "closed_cross_day"


def test_daily_card_has_four_sections_and_every_trade_id():
    payload = {
        "statistics": {
            "candidate_count": 3,
            "ai_allow_count": 3,
            "ai_reject_count": 0,
            "trade_count": 3,
            "win_count": 2,
            "loss_count": 1,
            "net_profit": 12.50,
        },
        "market_metrics": {
            "open": 4342.40,
            "high": 4395.15,
            "low": 4313.46,
            "close": 4389.36,
            "net_change_usd": 46.96,
        },
        "ai_reject_analysis": {
            "total_rejected": 0,
            "effective_filter_count": 0,
            "missed_opportunity_count": 0,
            "unresolved_count": 0,
            "effective_filter_rate": 0.0,
        },
        "actual_trade_rows": [
            {
                "position_id": trade_id,
                "direction": "BUY",
                "status": "closed",
                "whole_trade_net": 4.17,
                "today_net": 4.17,
                "final_r": 0.10,
                "tp1_done": True,
                "tp2_done": False,
                "runner_done": False,
            }
            for trade_id in ("715135227", "716669486", "718041351")
        ],
        "program_issues": [],
        "strategy_issues": [],
    }
    response = {
        "market_regime": "单边上涨",
        "market_summary": "黄金先跌后涨，整体偏强。",
        "ai_filter_assessment": "当天没有已决拒绝样本。",
        "strategy_market_fit": "交易方向与盘面基本一致。",
        "markdown_report": "继续积累样本，并检查通知与数据采集稳定性。",
    }

    card = review_service.build_daily_lark_card(
        "2026-08-11", payload, response, {}
    )
    rendered = json.dumps(card, ensure_ascii=False)

    for heading in (
            "1. 今日行情",
            "2. 今日行情回顾与开单准确性",
            "3. 今日交易",
            "4. 今日问题",
            "5. 下一步",
    ):
        assert heading in rendered
    assert "信号审核情况" not in rendered
    for trade_id in ("715135227", "716669486", "718041351"):
        assert f"#{trade_id}" in rendered


def test_candidate_lark_card_renders_candidate_details_and_summary():
    payload = {
        "statistics": {
            "candidate_count": 2,
            "ai_allow_count": 2,
            "ai_reject_count": 0,
            "trade_count": 1,
            "win_count": 1,
            "loss_count": 0,
            "net_profit": 84.20,
        },
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026-08-13 05:35:00",
                "candidate_time": "2026-08-13 05:39:57",
                "direction": "BUY",
                "route": "FIB_PA",
                "planned_entry": 4408.20,
                "planned_sl": 4403.60,
                "planned_tp1": 4412.80,
                "ai_status": "allow",
                "ai_time": "2026-08-13 05:39:59",
                "ai_confidence": 82,
                "outcome": "filled",
                "outcome_time": "2026-08-13 05:40:01",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "728461351",
                "entry_facts": {},
            },
            {
                "sequence": 2,
                "signal_id": "S2",
                "signal_bar_time": "2026-08-13 11:20:00",
                "candidate_time": "2026-08-13 11:24:56",
                "direction": "SELL",
                "route": "BOTH",
                "planned_entry": 4427.40,
                "planned_sl": 4431.10,
                "planned_tp1": 4423.70,
                "ai_status": "allow",
                "ai_time": "2026-08-13 11:24:58",
                "ai_confidence": 74,
                "outcome": "expired",
                "outcome_time": "2026-08-13 11:39:00",
                "outcome_reason": "市场未在有效期内触发入场价，挂单正常到期",
                "position_id": "",
                "entry_facts": {},
            },
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S2",
                    "sequence": 2,
                    "direction": "SELL",
                    "reason_code": "挂单到期",
                    "reason_text": "市场未在有效期内触发入场价，挂单正常到期",
                    "entry": 4427.40,
                    "sl": 4431.10,
                    "entry_retouched": True,
                    "entry_retouch_time": "2026-08-13 11:44:00",
                    "r30": 0.42,
                    "r60": 0.68,
                    "r_max": 1.12,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": "1R",
                    "classification": "明显执行型错失",
                    "hit_0_8": True,
                    "hit_1r": True,
                    "hit_2r": False,
                }
            ]
        },
        "actual_trade_rows": [],
        "program_issues": [],
        "strategy_issues": [],
    }

    card = review_service.build_candidate_lark_card("2026-08-13", payload)
    rendered = json.dumps(card, ensure_ascii=False)

    assert card["card"]["header"]["title"]["content"] == "候选订单明细｜2026-08-13"
    assert rendered.count("候选订单明细｜2026-08-13") == 1
    assert "候选#1｜BUY｜已成交" in rendered
    assert "DeepSeek审核：**允许｜82分｜10:39:59**" in rendered
    assert "最终结果：**已成交**｜对应交易：**#728461351**" in rendered
    assert "候选#2｜SELL｜未成交" in rendered
    assert "━━━━━━━━━━━━━━━━━━━━" in rendered
    assert "\n---\n" not in rendered
    assert "## 未成交后的走势回看" in rendered
    assert "**后来是否重新回到原计划入场价：** 是｜11:44:00" in rendered
    assert "**之后30分钟：** 最多向有利方向走了 +0.42R" in rendered
    assert "**关键结果：** 先达到1R目标" in rendered
    assert "**最终判定：** 挂单后错过机会：该信号已通过AI审核并挂单，但最终未成交；价格后来回到原计划入场价，并先达到1R目标。" in rendered
    assert "## 今日候选汇总" in rendered
    assert "候选#2｜SELL｜未成交｜挂单后错过机会" in rendered


def test_daily_card_uses_deterministic_outbox_id(tmp_path: Path):
    card = {
        "msg_type": "interactive",
        "card": {"header": {"title": {"content": "EA复盘"}}},
    }

    status = review_service.queue_daily_lark_card(
        tmp_path, "2026-08-11", card
    )

    assert status == "queued"
    pending = tmp_path / "Lark_Outbox" / "Pending" / "DAILY_2026-08-11.json"
    saved = json.loads(pending.read_text(encoding="utf-8"))
    assert saved["notification_id"] == "DAILY_2026-08-11"
    assert saved["notification_type"] == "daily_review"


def test_keyword_footer_test_card_is_explicit_and_idempotent(tmp_path: Path):
    assert review_service.queue_lark_keyword_test_card(tmp_path) == "queued"
    assert review_service.queue_lark_keyword_test_card(tmp_path) == "queued"

    pending = (
        tmp_path
        / "Lark_Outbox"
        / "Pending"
        / "TEST_LARK_KEYWORD_FOOTER_20260811.json"
    )
    item = json.loads(pending.read_text(encoding="utf-8"))
    title = item["card"]["card"]["header"]["title"]["content"]
    body = json.dumps(item["card"], ensure_ascii=False)

    assert title == "【测试】Lark通知格式与安全词验证"
    assert "不代表开仓、平仓或交易信号" in body


def test_daily_program_issues_include_uncertain_and_failed_pending_notifications(tmp_path: Path):
    uncertain = tmp_path / "Lark_Outbox" / "Uncertain"
    pending = tmp_path / "Lark_Outbox" / "Pending"
    uncertain.mkdir(parents=True)
    pending.mkdir(parents=True)
    (uncertain / "718041351_FINAL.json").write_text("{}", encoding="utf-8")
    (pending / "718041352_OPEN.json").write_text(
        json.dumps({"last_error": "explicit reject"}), encoding="utf-8"
    )

    issues = review_service.collect_notification_issues(tmp_path)

    assert any("718041351_FINAL" in issue for issue in issues)
    assert any("718041352_OPEN" in issue for issue in issues)


def test_beijing_date_key_uses_utc_plus_8_without_dst():
    assert beijing_date_key(datetime(2026, 7, 30, 15, 59, tzinfo=UTC)) == "2026-07-30"
    assert beijing_date_key(datetime(2026, 7, 30, 16, 0, tzinfo=UTC)) == "2026-07-31"


def test_api_budget_supports_higher_monitor_and_daily_limits(tmp_path: Path):
    budget = ApiBudget(tmp_path / "usage.json", monitor_limit=60, daily_limit=5)
    for _ in range(60):
        assert budget.consume_monitor("2026-07-30") is True
    assert budget.consume_monitor("2026-07-30") is False
    for _ in range(5):
        assert budget.consume_daily("2026-07-30") is True
    assert budget.consume_daily("2026-07-30") is False
    assert budget.counts("2026-07-30") == {"monitor": 60, "daily": 5}


def test_scheduled_monitor_runs_only_at_minute_05_once_per_hour():
    assert is_scheduled_monitor_due(datetime(2026, 7, 30, 1, 5, tzinfo=UTC), None)
    assert not is_scheduled_monitor_due(datetime(2026, 7, 30, 1, 4, tzinfo=UTC), None)
    assert not is_scheduled_monitor_due(
        datetime(2026, 7, 30, 1, 5, tzinfo=UTC), "2026-07-30T09"
    )


def test_event_merge_queue_merges_related_events_inside_ten_minutes():
    queue = EventMergeQueue(window_minutes=10)
    queue.add({"beijing_time": "2026-07-30 09:55:00", "event_type": "candidate", "signal_id": "S1"})
    queue.add({"beijing_time": "2026-07-30 10:01:00", "event_type": "pending_created", "signal_id": "S1"})
    groups = queue.flush_ready(datetime(2026, 7, 30, 2, 12, tzinfo=UTC), force=True)
    assert len(groups) == 1
    assert [event["event_type"] for event in groups[0]] == ["candidate", "pending_created"]


def test_compact_monitor_payload_suppresses_unchanged_repeated_scan_reasons():
    rows = [
        {"beijing_time": "2026-07-30 09:00:00", "event_type": "scan_result", "reason": "Fib未满足"},
        {"beijing_time": "2026-07-30 09:05:00", "event_type": "scan_result", "reason": "Fib未满足"},
        {"beijing_time": "2026-07-30 09:10:00", "event_type": "scan_result", "reason": "Fib未满足"},
    ]
    payload = compact_monitor_payload(rows, snapshots=[], calendar=[])
    assert payload["block_reason_counts"]["Fib未满足"] == 3
    assert len(payload["recent_events"]) == 1


def test_list_missing_review_dates_skips_existing_reports_and_current_day(tmp_path: Path):
    daily = tmp_path / "Daily_Review"
    (daily / "2026-07-28").mkdir(parents=True)
    (daily / "2026-07-28" / "Daily_Review_2026-07-28.md").write_text("ok", encoding="utf-8")
    raw = tmp_path / "Raw_Data"
    for day in ("2026-07-28", "2026-07-29", "2026-07-30"):
        raw.mkdir(exist_ok=True)
        (raw / f"Market_Snapshots_{day}.csv").write_text("h\n", encoding="utf-8")
    missing = list_missing_review_dates(tmp_path, current_beijing_date="2026-07-30")
    assert missing == ["2026-07-29"]


def test_validate_monitor_response_requires_exact_schema_and_summary_length():
    response = {
        "beijing_time": "2026-07-30 10:05:00",
        "trigger_type": "scheduled",
        "market_state": "震荡",
        "trend_alignment": "部分一致",
        "ea_state": "无信号",
        "main_reason": "Fib回调未形成",
        "condition_summary": {
            "trend": "满足",
            "swing": "满足",
            "fib": "不满足",
            "price_action": "接近",
            "risk_reward": "数据不足",
            "spread_and_time_guard": "正常",
        },
        "economic_event_note": "无",
        "execution_status": "正常",
        "alert_level": "normal",
        "summary": "当前黄金处于震荡整理，M5虽保持局部方向，但回调尚未进入有效Fib区域，EA未形成候选信号。点差与时间风控正常，多周期仅部分一致，继续记录后续结构变化。",
    }
    assert validate_monitor_response(response)["ea_state"] == "无信号"
    bad = dict(response)
    bad["extra"] = 1
    with pytest.raises(ValueError, match="exact fields"):
        validate_monitor_response(bad)


def test_validate_daily_response_rejects_markdown_over_800_chinese_chars():
    base = {
        "review_date": "2026-07-30",
        "data_status": "完整",
        "ea_runtime_status": "正常",
        "market_summary": "震荡",
        "market_regime": "震荡",
        "strategy_market_fit": "条件匹配",
        "ai_filter_assessment": "无AI拒绝样本",
        "trade_statistics": {
            "candidate_count": 0,
            "local_reject_count": 0,
            "ai_allow_count": 0,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0,
            "max_profit_trade": 0,
            "max_loss_trade": 0,
        },
        "opened_trade_reason": "无",
        "no_trade_main_reason": "无有效回调",
        "block_reason_counts": {
            "trend_not_ready": 0,
            "swing_not_ready": 0,
            "fib_not_ready": 1,
            "price_action_not_ready": 0,
            "risk_reward_not_ready": 0,
            "spread_or_time_guard": 0,
            "other": 0,
        },
        "representative_trades": [],
        "economic_event_summary": "无",
        "execution_issues": [],
        "observation_items": [],
        "validation_issues": [],
        "backtest_suggestions": [],
        "markdown_report": "正常复盘",
        "issue_judgment": "暂无问题。",
        "conclusion_summary": "整体正常。",
        "trade_group_analysis": "",
        "market_playbook": {
            "trend_verdict": "当天属于区间震荡。",
            "entry_accuracy": "规则上共0处应该开单。",
            "missing_reason": "当天没有出现规则上应该开单却未开出的位置。",
            "actionable_note": "继续观察。",
        },
    }
    assert validate_daily_response(base)["review_date"] == "2026-07-30"
    # 模型偶尔漏掉新增的行情回顾字段时，应按“空说明”容忍，而不是整段回退
    without_playbook = json.loads(json.dumps(base, ensure_ascii=False))
    without_playbook.pop("market_playbook")
    filled = validate_daily_response(without_playbook)["market_playbook"]
    assert set(filled) == {"trend_verdict", "entry_accuracy", "missing_reason", "actionable_note"}
    assert all(value == "" for value in filled.values())
    bad = json.loads(json.dumps(base, ensure_ascii=False))
    bad["markdown_report"] = "测" * 801
    with pytest.raises(ValueError, match="800"):
        validate_daily_response(bad)



def test_daily_payload_includes_all_trades_up_to_five_and_only_three_when_more():
    base = {
        "beijing_time": "2026-07-30 12:00:00",
        "event_type": "position_closed",
        "signal_id": "S",
        "profit": "1",
    }
    four = [{**base, "signal_id": f"S{i}", "profit": str(i)} for i in range(1, 5)]
    payload_four = build_daily_payload("2026-07-30", [], four, [])
    assert len(payload_four["representative_trade_rows"]) == 4

    six = [{**base, "signal_id": f"S{i}", "profit": str(i)} for i in range(1, 7)]
    payload_six = build_daily_payload("2026-07-30", [], six, [])
    assert len(payload_six["representative_trade_rows"]) == 3
    assert [row["signal_id"] for row in payload_six["representative_trade_rows"]] == [
        "S6",
        "S5",
        "S4",
    ]


def test_daily_payload_counts_partial_realized_profit_for_position_open_at_close():
    events = [
        {
            "beijing_time": "2026-08-11 01:50:16",
            "event_type": "pending_filled",
            "signal_id": "S_OPEN",
            "position_id": "716669486",
            "direction": "BUY",
            "price": "4361.17",
            "volume": "0.13",
            "profit": "0",
        },
        {
            "beijing_time": "2026-08-11 02:05:55",
            "event_type": "position_update",
            "outcome": "partial_exit",
            "position_id": "716669486",
            "profit": "22.14",
        },
        {
            "beijing_time": "2026-08-11 02:31:21",
            "event_type": "position_update",
            "outcome": "partial_exit",
            "position_id": "716669486",
            "profit": "21.96",
        },
    ]

    payload = build_daily_payload("2026-08-10", [], events, [])

    assert payload["statistics"]["trade_count"] == 1
    assert payload["statistics"]["win_count"] == 0
    assert payload["statistics"]["loss_count"] == 0
    assert payload["statistics"]["net_profit"] == 44.10
    assert payload["actual_trade_rows"] == [
        {
            "signal_id": "S_OPEN",
            "position_id": "716669486",
            "direction": "BUY",
            "open_time": "2026-08-11 01:50:16",
            "close_time": "",
                "entry_price": 4361.17,
                "exit_price": 0.0,
                "initial_volume": 0.13,
                "initial_sl": 0.0,
                "net_profit": 44.10,
                "whole_trade_net": 44.10,
                "today_net": 44.10,
                "close_reason": "",
                "tp1_done": False,
                "tp2_done": False,
                "runner_done": False,
                "data_source": "legacy_event",
                "status": "open_at_day_end",
        }
    ]


def _write_review_test_config(config_path: Path, root: Path) -> None:
    config_path.write_text(
        json.dumps(
            {
                "data_root": str(root),
                "monitor_prompt_file": "monitor.txt",
                "daily_prompt_file": "daily.txt",
                "monitor_daily_limit": 60,
                "daily_review_limit": 5,
                "max_daily_reviews_per_run": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_review_day(raw: Path, day: str) -> None:
    server_day = day.replace("-", ".")
    header = "server_time,beijing_time,m5_close,m5_ema20,m5_atr14,m5_rsi14,m5_macd_hist\n"
    (raw / f"Market_Snapshots_{day}.csv").write_text(
        header + f"{server_day} 12:00:00,{day} 18:00:00,3300,3299,5,50,0.1\n",
        encoding="utf-8-sig",
    )
    (raw / f"Strategy_Events_{day}.csv").write_text(
        "server_time,beijing_time,event_type,signal_id,stage,reason,profit\n",
        encoding="utf-8-sig",
    )


def _write_close_trigger(
    root: Path,
    review_key: str,
    session_open: str,
    session_close: str,
) -> Path:
    path = root / "Session_Close_Triggers" / "Pending" / f"CLOSE_{review_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trigger_id": f"CLOSE_{review_key}",
                "review_key": review_key,
                "symbol": "XAUUSD.s",
                "session_open_server": session_open,
                "session_close_server": session_close,
                "ready_server": session_close,
                "used_fallback": False,
                "reason": "broker session",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_rows_for_server_window_reads_across_beijing_csv_date_boundaries(tmp_path: Path):
    raw = tmp_path / "Raw_Data"
    raw.mkdir()
    (raw / "Market_Snapshots_2026-08-10.csv").write_text(
        "server_time,beijing_time,m5_close\n"
        "2026.08.10 23:49:59,2026-08-11 04:49:59,1\n"
        "2026.08.10 23:50:00,2026-08-11 04:50:00,2\n",
        encoding="utf-8-sig",
    )
    (raw / "Market_Snapshots_2026-08-11.csv").write_text(
        "server_time,beijing_time,m5_close\n"
        "2026.08.11 00:05:00,2026-08-11 05:05:00,3\n"
        "2026.08.11 00:05:01,2026-08-11 05:05:01,4\n",
        encoding="utf-8-sig",
    )

    rows = review_service.rows_for_server_window(
        tmp_path,
        "Market_Snapshots",
        "2026.08.10 23:50:00",
        "2026.08.11 00:05:00",
    )

    assert [row["m5_close"] for row in rows] == ["2", "3"]


def test_run_once_without_broker_close_trigger_never_generates_daily_review(tmp_path: Path):
    root = tmp_path / "account"
    raw = root / "Raw_Data"
    raw.mkdir(parents=True)
    _write_review_day(raw, "2026-07-29")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "monitor.txt").write_text("只返回JSON", encoding="utf-8")
    (config_dir / "daily.txt").write_text("只返回JSON", encoding="utf-8")
    config_path = config_dir / "review.json"
    _write_review_test_config(config_path, root)

    result = run_once(
        config_path,
        now=datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
        dry_run=True,
    )
    assert result["daily"] == []


def test_run_once_uses_broker_close_trigger_and_exact_session_window(tmp_path: Path):
    root = tmp_path / "account"
    raw = root / "Raw_Data"
    raw.mkdir(parents=True)
    _write_review_day(raw, "2026-07-30")
    _write_close_trigger(
        root,
        "2026-07-30",
        "2026.07.30 01:30:00",
        "2026.07.30 23:55:00",
    )

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "monitor.txt").write_text("只返回JSON", encoding="utf-8")
    (config_dir / "daily.txt").write_text("只返回JSON", encoding="utf-8")
    config_path = config_dir / "review.json"
    _write_review_test_config(config_path, root)

    result = run_once(
        config_path,
        now=datetime(2026, 7, 30, 22, 0, tzinfo=UTC),
        dry_run=True,
    )
    assert [item["date"] for item in result["daily"]] == ["2026-07-30"]
    payload = result["daily"][0]["payload"]
    assert payload["session_window"] == {
        "basis": "broker_session_close",
        "server_open": "2026.07.30 01:30:00",
        "server_close": "2026.07.30 23:55:00",
        "used_fallback": False,
    }
    assert payload["source_counts"]["snapshots"] == 1


def test_dry_run_never_consumes_broker_close_trigger(tmp_path: Path):
    root = tmp_path / "account"
    raw = root / "Raw_Data"
    raw.mkdir(parents=True)
    _write_review_day(raw, "2026-07-30")
    trigger = _write_close_trigger(
        root,
        "2026-07-30",
        "2026.07.30 01:30:00",
        "2026.07.30 23:55:00",
    )
    report_dir = root / "Daily_Review" / "2026-07-30"
    report_dir.mkdir(parents=True)
    (report_dir / "Daily_Review_2026-07-30.md").write_text("saved", encoding="utf-8")
    (report_dir / "Lark_Sent.flag").write_text("sent", encoding="utf-8")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "monitor.txt").write_text("只返回JSON", encoding="utf-8")
    (config_dir / "daily.txt").write_text("只返回JSON", encoding="utf-8")
    config_path = config_dir / "review.json"
    _write_review_test_config(config_path, root)

    run_once(config_path, now=datetime(2026, 7, 30, 22, 0, tzinfo=UTC), dry_run=True)

    assert trigger.exists()


def test_daily_review_ai_uses_pro_model_and_queues_both_cards(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "account"
    raw = root / "Raw_Data"
    raw.mkdir(parents=True)
    day = "2026-07-30"
    _write_review_day(raw, day)
    candidate_details = json.dumps(
        {
            "signal_route": "FIB_PA",
            "trade_plan": {
                "entry": 4382.05,
                "sl": 4386.14,
                "tp1": 4377.96,
                "rr_to_tp1": 1.0,
            },
            "bars": [{"time": "2026.07.30 11:55:00"}],
        },
        ensure_ascii=False,
    )
    (raw / f"Strategy_Events_{day}.csv").write_text(
        "server_time,beijing_time,event_type,signal_id,direction,order_ticket,position_id,deal_ticket,reason,details\n"
        f"2026.07.30 12:00:00,{day} 18:00:00,candidate,S1,SELL,0,0,0,本地候选已形成,{candidate_details}\n",
        encoding="utf-8-sig",
    )
    _write_close_trigger(
        root,
        day,
        "2026.07.30 01:30:00",
        "2026.07.30 23:55:00",
    )

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "monitor.txt").write_text("只返回JSON", encoding="utf-8")
    (config_dir / "daily.txt").write_text("只返回JSON", encoding="utf-8")
    config_path = config_dir / "review.json"
    config_path.write_text(
        json.dumps(
            {
                "data_root": str(root),
                "model": "deepseek-v4-flash",
                "daily_review_model": "deepseek-v4-pro",
                "api_key_environment": "TEST_DEEPSEEK_KEY",
                "monitor_prompt_file": "monitor.txt",
                "daily_prompt_file": "daily.txt",
                "monitor_daily_limit": 60,
                "daily_review_limit": 5,
                "max_daily_reviews_per_run": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "test-key")

    calls: list[tuple[str, int]] = []

    class RecordingClient:
        def __init__(
            self, api_key: str, model: str, timeout_seconds: float, retries: int
        ) -> None:
            self.model = model

        def call(self, prompt: str, payload: dict, max_tokens: int = 0):
            calls.append((self.model, max_tokens))
            usage = {
                "model": self.model,
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "response_time_ms": 1,
                "attempts": 1,
            }
            return _valid_daily_response(day), usage

    monkeypatch.setattr(
        "tools.ai_review_service.DeepSeekJsonClient", RecordingClient
    )

    result = run_once(
        config_path,
        now=datetime(2026, 7, 30, 22, 0, tzinfo=UTC),
        dry_run=False,
    )

    assert [item["date"] for item in result["daily"]] == [day]
    # 每日复盘AI分析必须使用Pro模型（max_tokens=1600是日报调用）。
    assert ("deepseek-v4-pro", 1600) in calls
    assert not any(
        model == "deepseek-v4-flash" and max_tokens == 1600
        for model, max_tokens in calls
    )
    # 两张卡片必须同时入队：主卡（DAILY_…）在前，候选卡（…_CANDIDATES）在后。
    pending = root / "Lark_Outbox" / "Pending"
    main_path = pending / f"DAILY_{day}.json"
    candidate_path = pending / f"DAILY_{day}_CANDIDATES.json"
    assert main_path.exists()
    assert candidate_path.exists()
    assert main_path.stat().st_mtime_ns <= candidate_path.stat().st_mtime_ns


def _valid_daily_response(review_date: str) -> dict:
    return {
        "review_date": review_date,
        "data_status": "完整",
        "ea_runtime_status": "正常",
        "market_summary": "震荡",
        "market_regime": "震荡",
        "strategy_market_fit": "条件匹配",
        "ai_filter_assessment": "无AI拒绝样本",
        "trade_statistics": {
            "candidate_count": 0,
            "local_reject_count": 0,
            "ai_allow_count": 0,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0,
            "max_profit_trade": 0,
            "max_loss_trade": 0,
        },
        "opened_trade_reason": "无",
        "no_trade_main_reason": "无有效回调",
        "block_reason_counts": {
            "trend_not_ready": 0,
            "swing_not_ready": 0,
            "fib_not_ready": 1,
            "price_action_not_ready": 0,
            "risk_reward_not_ready": 0,
            "spread_or_time_guard": 0,
            "other": 0,
        },
        "representative_trades": [],
        "economic_event_summary": "无",
        "execution_issues": [],
        "observation_items": [],
        "validation_issues": [],
        "backtest_suggestions": [],
        "markdown_report": "正常复盘",
        "issue_judgment": "暂无问题。",
        "conclusion_summary": "整体正常。",
    }



def test_monitor_failure_counts_against_quota_and_uses_cooldown(tmp_path: Path, monkeypatch):
    root = tmp_path / "account"
    raw = root / "Raw_Data"
    raw.mkdir(parents=True)
    day = "2026-07-30"
    (raw / f"Market_Snapshots_{day}.csv").write_text(
        "beijing_time,ea_state,last_scan_stage,last_scan_reason,m5_close,m5_ema20,m5_atr14,m5_rsi14,m5_macd_hist,m15_close,m15_ema20,m15_rsi14,m15_macd_hist,h1_close,h1_ema20,h1_rsi14,h1_macd_hist,h4_close,h4_ema20,h4_rsi14,h4_macd_hist,spread\n"
        "2026-07-30 09:55:00,idle,fib,Fib未满足,3300,3299,5,50,0.1,3300,3299,50,0.1,3300,3299,50,0.1,3300,3299,50,0.1,0.2\n",
        encoding="utf-8-sig",
    )
    (raw / f"Strategy_Events_{day}.csv").write_text(
        "beijing_time,event_type,signal_id,stage,reason,deal_ticket,order_ticket\n"
        "2026-07-30 09:55:00,candidate,S1,candidate,候选形成,,\n",
        encoding="utf-8-sig",
    )

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "monitor.txt").write_text("只返回JSON", encoding="utf-8")
    (config_dir / "daily.txt").write_text("只返回JSON", encoding="utf-8")
    config_path = config_dir / "review.json"
    config_path.write_text(
        json.dumps(
            {
                "data_root": str(root),
                "api_key_environment": "TEST_DEEPSEEK_KEY",
                "monitor_prompt_file": "monitor.txt",
                "daily_prompt_file": "daily.txt",
                "monitor_daily_limit": 29,
                "daily_review_limit": 1,
                "event_merge_minutes": 10,
                "failure_cooldown_minutes": 30,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "test-key")

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def call(self, *args, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr("tools.ai_review_service.DeepSeekJsonClient", FailingClient)

    first = run_once(config_path, now=datetime(2026, 7, 30, 2, 5, tzinfo=UTC))
    usage = json.loads((root / "Logs" / "api_usage.json").read_text(encoding="utf-8"))
    assert usage[day]["monitor"] == 1
    state = json.loads((root / "Logs" / "review_service_state.json").read_text(encoding="utf-8"))
    assert state["monitor_retry_after"] == "2026-07-30 10:35:00"

    second = run_once(config_path, now=datetime(2026, 7, 30, 2, 6, tzinfo=UTC))
    usage_again = json.loads((root / "Logs" / "api_usage.json").read_text(encoding="utf-8"))
    assert usage_again[day]["monitor"] == 1
    assert second["monitor"] == [{"trigger": "candidate", "status": "cooldown"}]


def test_run_once_integrates_independent_observer_without_budget_coupling(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "account"
    (root / "Raw_Data").mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("monitor.txt", "daily.txt", "independent.txt", "divergence.txt"):
        (config_dir / name).write_text("strict prompt", encoding="utf-8")
    config_path = config_dir / "review.json"
    config_path.write_text(
        json.dumps(
            {
                "data_root": str(root),
                "monitor_prompt_file": "monitor.txt",
                "daily_prompt_file": "daily.txt",
                "independent_ai_enabled": True,
                "independent_ai_prompt_file": "independent.txt",
                "independent_ai_divergence_prompt_file": "divergence.txt",
                "monitor_daily_limit": 29,
                "daily_review_limit": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    snapshots = [{"m5_time": "2026-08-11 16:35:00", "beijing_time": "2026-08-11 21:40:00"}]
    captured: dict = {}

    def fake_collect(*args, **kwargs):
        return snapshots, [], []

    def fake_process(**kwargs):
        captured.update(kwargs)
        return [{"task_id": "INDEPENDENT_AI_20260811_1635", "status": "DRY_RUN"}]

    monkeypatch.setattr(review_service, "collect_recent_data", fake_collect)
    monkeypatch.setattr(
        review_service, "process_independent_ai_cycle", fake_process, raising=False
    )

    result = run_once(
        config_path,
        now=datetime(2026, 8, 11, 13, 40, tzinfo=UTC),
        dry_run=True,
    )

    assert result["independent_ai"] == [
        {"task_id": "INDEPENDENT_AI_20260811_1635", "status": "DRY_RUN"}
    ]
    assert captured["snapshots"] is snapshots
    assert captured["events"] == []
    assert captured["primary_prompt"] == "strict prompt"
    assert captured["explanation_prompt"] == "strict prompt"
    assert not (root / "Logs" / "api_usage.json").exists()


def test_run_once_uses_parallel_auditor_and_not_legacy_observer(tmp_path: Path, monkeypatch):
    root = tmp_path / "account"
    (root / "Raw_Data").mkdir(parents=True)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("monitor.txt", "daily.txt", "parallel.txt", "difference.txt"):
        (config_dir / name).write_text("strict prompt", encoding="utf-8")
    config_path = config_dir / "review.json"
    config_path.write_text(json.dumps({
        "data_root": str(root), "monitor_prompt_file": "monitor.txt",
        "daily_prompt_file": "daily.txt", "independent_ai_enabled": False,
        "parallel_ai_enabled": True, "parallel_ai_entry_prompt_file": "parallel.txt",
        "parallel_ai_difference_prompt_file": "difference.txt",
    }), encoding="utf-8")
    captured = {}

    def fake_parallel(**kwargs):
        captured.update(kwargs)
        return [{"snapshot_id": "S", "status": "COMPLETED"}]

    def forbidden_legacy(**kwargs):
        raise AssertionError("legacy observer must not run")

    monkeypatch.setattr(review_service, "process_parallel_ai_cycle", fake_parallel)
    monkeypatch.setattr(review_service, "process_independent_ai_cycle", forbidden_legacy)
    result = run_once(config_path, now=datetime(2026, 8, 12, 3, 0, tzinfo=UTC), dry_run=True)
    assert result["parallel_ai"] == [{"snapshot_id": "S", "status": "COMPLETED"}]
    assert "independent_ai" not in result
    assert captured["primary_prompt"] == "strict prompt"
    assert captured["difference_prompt"] == "strict prompt"


def test_ai_error_event_is_classified_as_error_and_not_reject():
    candidate = _candidate_event("ERR_SIG", "2026-08-13 02:04:59", direction="SELL", route="EMA_L23")
    events = [
        candidate,
        {
            **candidate,
            "server_time": "2026-08-13 02:05:00",
            "beijing_time": "2026-08-13 07:05:00",
            "event_type": "ai_error",
            "outcome": "error",
            "reason": "DeepSeek请求失败，HTTP=404，MT5错误=0（降级放行）",
            "details": "allow=true | confidence=0 | threshold=70 | error=AI_HTTP_ERROR",
        },
    ]
    rows = build_candidate_lifecycle_rows(events)
    assert len(rows) == 1
    assert rows[0]["ai_status"] == "error"
    assert rows[0]["outcome"] == "ai_error"
    assert rows[0]["ai_allow_trade"] is True
    assert "降级放行" in rows[0]["outcome_reason"]

    payload = build_daily_payload("2026-08-13", [], events, [])
    assert payload["statistics"]["ai_error_count"] == 1
    assert payload["statistics"]["ai_reject_count"] == 0
    assert payload["statistics"]["ai_allow_count"] == 0
    assert payload["ai_reject_analysis"]["total_rejected"] == 0


def test_legacy_ai_reject_with_failure_reason_is_reclassified_as_error():
    candidate = _candidate_event("LEGACY_ERR", "2026-08-13 02:04:59", direction="SELL", route="EMA_L23")
    events = [
        candidate,
        {
            **candidate,
            "server_time": "2026-08-13 02:05:00",
            "beijing_time": "2026-08-13 07:05:00",
            "event_type": "ai_reject",
            "outcome": "reject",
            "reason": "DeepSeek请求失败，HTTP=404，MT5错误=0",
            "details": "allow=false | confidence=0 | threshold=70",
        },
    ]
    rows = build_candidate_lifecycle_rows(events)
    assert rows[0]["ai_status"] == "error"
    assert rows[0]["outcome"] == "ai_error"
    assert rows[0]["ai_allow_trade"] is False
    assert rows[0]["ai_reason"].startswith("DeepSeek请求失败")
    assert "AI审核异常·未放行" in rows[0]["outcome_reason"]

    payload = build_daily_payload("2026-08-13", [], events, [])
    assert payload["statistics"]["ai_error_count"] == 1
    assert payload["statistics"]["ai_reject_count"] == 0
    assert payload["ai_reject_analysis"]["total_rejected"] == 0


def test_real_ai_reject_still_classified_as_reject():
    candidate = _candidate_event("REAL_REJ", "2026-08-13 02:04:59", direction="SELL", route="EMA_L23")
    events = [
        candidate,
        {
            **candidate,
            "server_time": "2026-08-13 02:05:00",
            "beijing_time": "2026-08-13 07:05:00",
            "event_type": "ai_reject",
            "outcome": "reject",
            "reason": "结构证据不足，拒绝开单",
            "details": "allow=false | confidence=62 | threshold=70",
        },
    ]
    rows = build_candidate_lifecycle_rows(events)
    assert rows[0]["ai_status"] == "reject"
    assert rows[0]["outcome"] == "ai_rejected"

    payload = build_daily_payload("2026-08-13", [], events, [])
    assert payload["statistics"]["ai_reject_count"] == 1
    assert payload["statistics"]["ai_error_count"] == 0
    assert payload["ai_reject_analysis"]["total_rejected"] == 1
