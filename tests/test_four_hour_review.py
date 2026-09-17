from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.four_hour_review import (
    build_four_hour_review,
    due_full_window,
    render_four_hour_card,
    session_four_hour_windows,
)


BEIJING = timezone(timedelta(hours=8))


def test_only_five_full_windows_are_due_and_final_partial_window_is_daily_only():
    morning = due_full_window(datetime(2026, 9, 16, 10, 5, tzinfo=BEIJING))
    assert (morning["start"], morning["end"]) == (
        "2026-09-16 01:00:00", "2026-09-16 05:00:00"
    )
    assert due_full_window(datetime(2026, 9, 16, 14, 4, tzinfo=BEIJING)) is None
    assert due_full_window(datetime(2026, 9, 16, 14, 15, tzinfo=BEIJING)) is None
    late = due_full_window(datetime(2026, 9, 17, 2, 5, tzinfo=BEIJING))
    assert (late["start"], late["end"]) == (
        "2026-09-16 17:00:00", "2026-09-16 21:00:00"
    )
    assert due_full_window(datetime(2026, 9, 17, 5, 5, tzinfo=BEIJING)) is None
    windows = session_four_hour_windows("2026-09-16 01:02:00", "2026-09-16 23:58:00")
    assert len(windows) == 6
    assert windows[-1] == {
        "start": "2026-09-16 21:00:00",
        "end": "2026-09-16 23:58:00",
        "daily_only": True,
    }


def _write_snapshots(root: Path, start_hour: int, end_hour: int, prices: list[float]) -> None:
    import csv

    raw = root / "Raw_Data"
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / "Market_Snapshots_2026-09-16.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        fields = ["server_time", "m5_time", "m5_open", "m5_high", "m5_low", "m5_close", "m5_atr14", "last_scan_reason", "last_scan_stage", "last_scan_outcome"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for i in range(start_hour * 12, end_hour * 12):
            index = i - start_hour * 12
            opening, closing = prices[index], prices[index + 1]
            hour, minute = divmod(i * 5, 60)
            clock = f"2026-09-16 {hour:02d}:{minute:02d}:00"
            writer.writerow({"server_time": clock, "m5_time": clock, "m5_open": opening,
                "m5_high": max(opening, closing) + .2, "m5_low": min(opening, closing) - .2,
                "m5_close": closing, "m5_atr14": 1.3,
                "last_scan_reason": "M15定方向未放行 | C21_BLOCK_BUY_PRIMARY_BEAR",
                "last_scan_stage": "trend", "last_scan_outcome": "reject"})


def test_four_hour_report_detects_strong_unparticipated_uptrend_even_without_candidates(tmp_path):
    # 05:00-06:30 每根上涨 2 美元，随后 2.5 小时横盘；四小时整体并非单边。
    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(tmp_path, 5, 9, prices)
    report = build_four_hour_review(tmp_path, "2026-09-16 05:00:00", "2026-09-16 09:00:00")
    assert report["data_status"] == "完整"
    assert report["formal_candidates"] == 0
    assert report["filled_trades"] == 0
    assert report["key_trend"]["direction"] == "上涨"
    assert report["key_trend"]["net_move_usd"] >= 35
    assert report["participation"] == "明显趋势段未参与"
    card = render_four_hour_card(report)
    assert "没有形成正式候选" in card
    assert "不能据此断定某一笔合格单漏开" in card
    assert "上涨" in card


def test_four_hour_card_uses_server_and_beijing_window_as_body_heading(tmp_path):
    prices = [4282 + i for i in range(49)]
    _write_snapshots(tmp_path, 5, 9, prices)
    report = build_four_hour_review(tmp_path, "2026-09-16 05:00:00", "2026-09-16 09:00:00")

    card = render_four_hour_card(report)

    assert card.splitlines()[0] == "**MT5 服务器时间 05:00–09:00（北京时间 10:00–14:00）**"
    assert "四小时行情回顾与开单准确性" not in card


def test_four_hour_card_beijing_window_uses_configured_offset(tmp_path):
    prices = [4282 + i for i in range(49)]
    _write_snapshots(tmp_path, 5, 9, prices)
    report = build_four_hour_review(tmp_path, "2026-09-16 05:00:00", "2026-09-16 09:00:00")

    card = render_four_hour_card(report, server_beijing_offset_hours=6)

    assert card.splitlines()[0] == "**MT5 服务器时间 05:00–09:00（北京时间 11:00–15:00）**"


def test_previous_root_fills_early_gap_without_double_counting_new_root(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    prices = [4282 + i for i in range(49)]
    _write_snapshots(old, 5, 9, prices)
    _write_snapshots(new, 5, 9, prices)
    report = build_four_hour_review(new, "2026-09-16 05:00:00", "2026-09-16 09:00:00", legacy_root=old)
    assert report["observed_bars"] == 48


def test_due_notification_is_saved_and_queued_once_but_dry_run_never_writes(tmp_path):
    from tools.ai_review_service import process_due_four_hour_review

    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(tmp_path, 5, 9, prices)
    now = datetime(2026, 9, 16, 14, 5, tzinfo=BEIJING)
    config = {"four_hour_review_enabled": True}
    preview = process_due_four_hour_review(config, tmp_path, now, dry_run=True)
    assert preview["status"] == "preview"
    assert not (tmp_path / "Four_Hour_Review").exists()
    assert not (tmp_path / "Lark_Outbox").exists()

    first = process_due_four_hour_review(config, tmp_path, now)
    second = process_due_four_hour_review(config, tmp_path, now)
    assert first["status"] == "queued"
    assert second["status"] == "already_queued"
    report = tmp_path / "Four_Hour_Review" / "2026-09-16" / "FOUR_HOUR_2026-09-16_05-09.json"
    notification = tmp_path / "Lark_Outbox" / "Pending" / "FOUR_HOUR_2026-09-16_05-09.json"
    assert report.exists() and notification.exists()
    assert "上涨" in notification.read_text(encoding="utf-8")
    assert len(list((tmp_path / "Lark_Outbox" / "Pending").glob("*.json"))) == 1


def test_four_hour_reads_separate_ea_root_but_queues_on_service_root(tmp_path):
    from tools.ai_review_service import process_due_four_hour_review

    service, ea = tmp_path / "service", tmp_path / "ea"
    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(ea, 5, 9, prices)
    config = {"four_hour_review_enabled": True, "four_hour_data_root": str(ea)}
    result = process_due_four_hour_review(
        config, service, datetime(2026, 9, 16, 14, 5, tzinfo=BEIJING)
    )
    assert result["report"]["observed_bars"] == 48
    assert (service / "Lark_Outbox" / "Pending" / "FOUR_HOUR_2026-09-16_05-09.json").exists()
    assert not (ea / "Lark_Outbox").exists()


def test_daily_section_carries_full_windows_and_daily_only_tail(tmp_path):
    from tools.ai_review_service import build_session_four_hour_facts
    from tools.daily_review_renderer import _render_market_review_section

    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(tmp_path, 5, 9, prices)
    facts = build_session_four_hour_facts(
        tmp_path, "2026-09-16 01:02:00", "2026-09-16 23:58:00"
    )
    assert len(facts) == 6
    section = _render_market_review_section(
        {"four_hour_reviews": facts}, {"market_playbook": {}}
    )
    assert "05:00–09:00" in section
    assert "21:00–23:58" in section
    assert "上涨" in section
    assert "明显趋势段未参与" in section


def test_daily_market_review_uses_complete_broker_session_rows(tmp_path):
    from tools.daily_market_review import build_market_review

    prices = [4282 + i for i in range(49)]
    _write_snapshots(tmp_path, 5, 9, prices)
    from tools.four_hour_review import _window_rows
    rows = _window_rows(tmp_path, "Market_Snapshots", "2026-09-16 05:00:00", "2026-09-16 09:00:00")
    result = build_market_review(tmp_path, "2026-09-16", snapshot_rows=rows)
    assert result["metrics"]["bars"] == 48


def test_four_hour_report_counts_with_and_against_trend_fills(tmp_path):
    import csv

    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(tmp_path, 5, 9, prices)
    path = tmp_path / "Raw_Data" / "Strategy_Events_2026-09-16.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["server_time", "event_type", "signal_id", "deal_ticket", "direction"])
        writer.writeheader()
        writer.writerow({"server_time": "2026-09-16 05:25:00", "event_type": "pending_filled", "signal_id": "1", "deal_ticket": "1", "direction": "BUY"})
        writer.writerow({"server_time": "2026-09-16 05:30:00", "event_type": "pending_filled", "signal_id": "2", "deal_ticket": "2", "direction": "SELL"})
    report = build_four_hour_review(tmp_path, "2026-09-16 05:00:00", "2026-09-16 09:00:00")
    assert report["trend_aligned_fills"] == 1
    assert report["trend_opposite_fills"] == 1
    assert "逆势成交1笔" in render_four_hour_card(report)


def test_snapshot_window_uses_m5_bar_time_not_later_write_time(tmp_path):
    import csv

    raw = tmp_path / "Raw_Data"
    raw.mkdir()
    path = raw / "Market_Snapshots_2026-09-16.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["server_time", "m5_time", "m5_open", "m5_high", "m5_low", "m5_close"])
        writer.writeheader()
        for index in range(-1, 48):
            bar = datetime(2026, 9, 16, 5) + timedelta(minutes=5 * index)
            recorded = bar + timedelta(minutes=5)
            writer.writerow({"server_time": recorded.strftime("%Y-%m-%d %H:%M:%S"),
                "m5_time": bar.strftime("%Y-%m-%d %H:%M:%S"),
                "m5_open": 4300 + index, "m5_high": 4301 + index,
                "m5_low": 4299 + index, "m5_close": 4300.5 + index})
    report = build_four_hour_review(tmp_path, "2026-09-16 05:00:00", "2026-09-16 09:00:00")
    assert report["observed_bars"] == 48
    assert report["missing_bars"] == []
    assert report["data_status"] == "完整"


def test_missing_m5_bars_are_reported_even_if_only_two_of_48(tmp_path):
    import csv

    raw = tmp_path / "Raw_Data"
    raw.mkdir()
    with (raw / "Market_Snapshots_2026-09-16.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["server_time", "m5_time", "m5_open", "m5_high", "m5_low", "m5_close"])
        writer.writeheader()
        for index in range(48):
            bar = datetime(2026, 9, 16, 5) + timedelta(minutes=5 * index)
            if bar.strftime("%H:%M") in {"06:40", "06:45"}:
                continue
            writer.writerow({"server_time": (bar + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "m5_time": bar.strftime("%Y-%m-%d %H:%M:%S"),
                "m5_open": 4300 + index, "m5_high": 4301 + index,
                "m5_low": 4299 + index, "m5_close": 4300.5 + index})
    report = build_four_hour_review(tmp_path, "2026-09-16 05:00:00", "2026-09-16 09:00:00")
    assert report["observed_bars"] == 46
    assert report["missing_bars"] == ["06:40", "06:45"]
    assert report["data_status"] == "部分缺失"


def test_card_uses_daily_style_and_keeps_pro_commentary_separate_from_facts(tmp_path):
    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(tmp_path, 5, 9, prices)
    report = build_four_hour_review(tmp_path, "2026-09-16 05:00:00", "2026-09-16 09:00:00")
    report["pro_analysis"] = {"market_explanation": "早段上涨很快，后段转为横盘。",
        "entry_assessment": "未见成交，需核对趋势放行时点。",
        "missing_reason": "记录显示M15方向门槛未放行，不能确定是否漏单。",
        "observation": "后续观察同类趋势是否反复被同一门槛挡住。"}
    card = render_four_hour_card(report)
    for label in ("行情性质：", "判定依据：", "口径：", "正式候选与成交：", "行情性质说明：", "开单准确性说明：", "没开出来的原因：", "观察建议："):
        assert label in card
    assert "早段上涨很快" in card
    assert "规则上应该开单的位置为0" not in card


def test_pro_analysis_is_saved_with_one_notification_per_window(tmp_path):
    import json
    from tools.ai_review_service import process_due_four_hour_review

    class ProClient:
        calls = 0

        def call(self, _prompt, _payload, max_tokens):
            self.calls += 1
            assert max_tokens > 0
            return ({"market_explanation": "上涨段持续推进。",
                     "entry_assessment": "没有成交，趋势参与不足。",
                     "missing_reason": "M15门槛未放行。",
                     "observation": "核查趋势放行是否滞后。"},
                    {"model": "deepseek-v4-pro", "prompt_tokens": 20,
                     "completion_tokens": 30, "total_tokens": 50,
                     "response_time_ms": 1, "attempts": 1})

    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(tmp_path, 5, 9, prices)
    client = ProClient()
    config = {"four_hour_review_enabled": True, "four_hour_pro_enabled": True}
    now = datetime(2026, 9, 16, 14, 5, tzinfo=BEIJING)
    first = process_due_four_hour_review(config, tmp_path, now, pro_client=client, pro_prompt="review")
    second = process_due_four_hour_review(config, tmp_path, now, pro_client=client, pro_prompt="review")
    saved = json.loads((tmp_path / "Four_Hour_Review" / "2026-09-16" / "FOUR_HOUR_2026-09-16_05-09.json").read_text(encoding="utf-8"))
    assert first["status"] == "queued" and second["status"] == "already_queued"
    assert client.calls == 1
    assert saved["pro_status"] == "ok"
    assert saved["pro_analysis"]["market_explanation"] == "上涨段持续推进。"
    assert "上涨段持续推进" in (tmp_path / "Lark_Outbox" / "Pending" / "FOUR_HOUR_2026-09-16_05-09.json").read_text(encoding="utf-8")


def test_pro_failure_still_queues_local_facts(tmp_path):
    from tools.ai_review_service import process_due_four_hour_review

    class FailingPro:
        def call(self, _prompt, _payload, max_tokens):
            raise RuntimeError("network down")

    prices = [4282 + i for i in range(49)]
    _write_snapshots(tmp_path, 5, 9, prices)
    result = process_due_four_hour_review(
        {"four_hour_review_enabled": True, "four_hour_pro_enabled": True},
        tmp_path, datetime(2026, 9, 16, 14, 5, tzinfo=BEIJING),
        pro_client=FailingPro(), pro_prompt="review",
    )
    assert result["status"] == "queued"
    assert result["report"]["pro_status"] == "unavailable"
    assert "Pro分析暂不可用" in (tmp_path / "Four_Hour_Review" / "2026-09-16" / "FOUR_HOUR_2026-09-16_05-09.md").read_text(encoding="utf-8")


def test_pro_cannot_turn_zero_candidates_into_certain_required_trades(tmp_path):
    from tools.ai_review_service import process_due_four_hour_review

    class OverconfidentPro:
        def call(self, _prompt, _payload, max_tokens):
            return {"market_explanation": "上涨明显。",
                    "entry_assessment": "规则上应该开3单，确定漏单。",
                    "missing_reason": "EA没有开。",
                    "observation": "检查日志。"}, {}

    prices = [4282 + i for i in range(49)]
    _write_snapshots(tmp_path, 5, 9, prices)
    result = process_due_four_hour_review(
        {"four_hour_review_enabled": True, "four_hour_pro_enabled": True},
        tmp_path, datetime(2026, 9, 16, 14, 5, tzinfo=BEIJING),
        pro_client=OverconfidentPro(), pro_prompt="review",
    )
    assert result["report"]["pro_status"] == "unavailable"
    assert "规则上应该开3单" not in (tmp_path / "Four_Hour_Review" / "2026-09-16" / "FOUR_HOUR_2026-09-16_05-09.md").read_text(encoding="utf-8")


def test_service_cycle_uses_configured_pro_for_due_four_hour_review(tmp_path, monkeypatch):
    import json
    from tools.ai_review_service import run_once

    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    root = tmp_path / "account"
    _write_snapshots(root, 5, 9, prices)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in ("monitor.txt", "daily.txt", "four_hour.txt"):
        (config_dir / name).write_text("只返回JSON", encoding="utf-8")
    config_path = config_dir / "review.json"
    config_path.write_text(json.dumps({
        "data_root": str(root), "api_key_environment": "TEST_FOUR_HOUR_KEY",
        "monitor_prompt_file": "monitor.txt", "daily_prompt_file": "daily.txt",
        "four_hour_prompt_file": "four_hour.txt", "daily_review_model": "deepseek-v4-pro",
        "four_hour_review_enabled": True, "four_hour_pro_enabled": True,
        "lark_outbox_enabled": False, "monitor_daily_limit": 0, "daily_review_limit": 0,
        "independent_ai_enabled": False, "parallel_ai_enabled": False,
    }), encoding="utf-8")
    monkeypatch.setenv("TEST_FOUR_HOUR_KEY", "test-key")
    monkeypatch.setattr("tools.ai_trade_manager.snapshot_manual_open_positions", lambda *_args: None)
    calls = []

    class FakeClient:
        def __init__(self, _key, model, _timeout, _retries):
            self.model = model

        def call(self, _prompt, _payload, max_tokens):
            calls.append(self.model)
            return {"market_explanation": "上涨持续推进。",
                    "entry_assessment": "趋势段未见成交。",
                    "missing_reason": "M15门槛未放行。",
                    "observation": "观察门槛是否持续滞后。"}, {}

    monkeypatch.setattr("tools.ai_review_service.DeepSeekJsonClient", FakeClient)
    result = run_once(config_path, now=datetime(2026, 9, 16, 6, 5, tzinfo=timezone.utc))
    assert result["four_hour"][0]["report"]["pro_status"] == "ok"
    assert calls.count("deepseek-v4-pro") == 1


def test_daily_summary_reuses_saved_four_hour_pro_observation(tmp_path):
    import json
    from tools.ai_review_service import build_session_four_hour_facts
    from tools.daily_review_renderer import _render_market_review_section

    source, output = tmp_path / "source", tmp_path / "output"
    prices = [4282 + 2 * i for i in range(19)] + [4318 + (i % 2) * .3 for i in range(30)]
    _write_snapshots(source, 5, 9, prices)
    saved_dir = output / "Four_Hour_Review" / "2026-09-16"
    saved_dir.mkdir(parents=True)
    (saved_dir / "FOUR_HOUR_2026-09-16_05-09.json").write_text(json.dumps({
        "start": "2026-09-16 05:00:00", "end": "2026-09-16 09:00:00",
        "pro_status": "ok", "pro_analysis": {
            "market_explanation": "早段趋势值得重点观察。",
            "entry_assessment": "未见正式候选，需核对趋势门槛。",
            "missing_reason": "记录显示M15未放行。",
            "observation": "持续核查强趋势与M15放行的时差。",
        },
    }), encoding="utf-8")
    facts = build_session_four_hour_facts(
        source, "2026-09-16 01:02:00", "2026-09-16 23:58:00",
        output_root=output,
    )
    text = _render_market_review_section({"four_hour_reviews": facts}, {"market_playbook": {}})
    assert "持续核查强趋势与M15放行的时差" in text
