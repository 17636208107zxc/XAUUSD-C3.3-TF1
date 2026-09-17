"""周度复盘升级后的验收测试。

覆盖：三组不混淆、开平仓/盈亏对得上、R 不用 0 代替缺失、方向/路径统计、
MFE/MAE 汇总、AI 允许/拒绝、未成交分类、V14/V20 拆分、截至当前标题、
不出现英文内部状态、冻结事实包。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tools.weekly_review import (
    V20_BASELINE,
    aggregate_weekly_facts,
    build_weekly_fallback_response,
    build_weekly_lark_card,
    build_weekly_payload,
    last_completed_week,
    _guard_ea_only,
    _guard_manual_claims,
    _guard_filter_rating,
    _strip_supported_rules_claim,
    _guard_risk_control_claim,
    _guard_win_mfe_claim,
)


def _write_daily(root: Path, day: str, data: dict) -> None:
    out = root / "Daily_Review" / day
    out.mkdir(parents=True, exist_ok=True)
    (out / f"Daily_Data_{day}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _trade(position_id, magic, direction, route, net, final_r, mfe, mae, open_t, close_t, status="closed", close_reason="DEAL_REASON_SL"):
    return {
        "position_id": position_id,
        "magic": magic,
        "direction": direction,
        "route": route,
        "entry_price": 4500.0,
        "initial_sl": 4505.0,
        "initial_risk": 50.0,
        "whole_trade_net": net,
        "net_profit": net,
        "final_r": final_r,
        "mfe_r": mfe,
        "mae_r": mae,
        "close_reason": close_reason,
        "status": status,
        "open_time": open_t,
        "close_time": close_t,
    }


def _build_week_root(tmp_path: Path) -> Path:
    """构造一周数据：EA 3 笔、AI 2 笔、人工 1 笔（仅 trade_groups），覆盖 V14+V20。"""
    root = tmp_path / "data"
    ea_trades = [
        _trade("p1", "2026072901", "BUY", "FIB_PA", 100.0, 1.0, 1.8, -0.4, "2026-08-17 10:00:00", "2026-08-17 11:00:00"),
        _trade("p2", "2026072901", "SELL", "EMA_L23", -50.0, -1.0, 0.1, -1.2, "2026-08-17 12:00:00", "2026-08-17 13:00:00"),
        _trade("p3", "2026072901", "BUY", "EMA_H23", -100.0, -2.0, None, None, "2026-08-21 10:30:00", "2026-08-21 11:30:00"),
    ]
    ai_trades = [
        _trade("a1", "2026072902", "SELL", "FIB_PA", 30.0, 0.6, 1.0, -0.3, "2026-08-18 10:00:00", "2026-08-18 11:00:00"),
        _trade("a2", "2026072902", "BUY", "BOTH", -25.0, -0.5, 0.4, -1.0, "2026-08-19 10:00:00", "2026-08-19 11:00:00"),
    ]
    # p3 在 V20 Baseline 之后平仓 → V20
    data = {
        "market_metrics": {"open": 4500.0, "high": 4520.0, "low": 4490.0, "close": 4510.0},
        "actual_trade_rows": ea_trades + ai_trades,
        "candidate_rows": [
            {
                "signal_id": "s1",
                "direction": "BUY",
                "route": "FIB_PA",
                "ai_status": "allow",
                "outcome": "filled",
                "position_id": "p1",
            },
            {
                "signal_id": "s2",
                "direction": "BUY",
                "route": "EMA_H23",
                "ai_status": "reject",
                "outcome": "unfilled",
                "position_id": "",
            },
        ],
        "ai_candidate_rows": [
            {"signal_id": "x1", "block_gate_ids": ["G06"]},
            {"signal_id": "x2", "block_gate_ids": ["G08"]},
            {"signal_id": "x3", "block_gate_ids": ["G06"]},
        ],
        "unfilled_candidate_review": {
            "items": [{"signal_id": "u1", "classification": "潜在执行型错失"}]
        },
        "trade_groups": {
            "ea": {"count": 3, "win": 1, "loss": 2, "net": -50.0, "r_total": -2.0, "r_samples": 3},
            "ai": {"count": 2, "win": 1, "loss": 1, "net": 5.0, "r_total": 0.1, "r_samples": 2},
            "manual": {"count": 1, "win": 1, "loss": 0, "net": 20.0, "r_total": 1.0, "r_samples": 1},
        },
        "statistics": {"trade_count": 6, "win_count": 2, "loss_count": 4, "net_profit": -120.0},
        "program_issues": [],
        "strategy_issues": [],
        "execution_issues": [],
    }
    # 只写一天（周一），但让 EA/AI 交易横跨整周，方便验证
    _write_daily(root, "2026-08-17", data)
    # 再写一天放 V20 交易
    data2 = {
        "market_metrics": {"open": 4510.0, "high": 4520.0, "low": 4500.0, "close": 4512.0},
        "actual_trade_rows": [_trade("p3", "2026072901", "BUY", "EMA_H23", -100.0, -2.0, None, None, "2026-08-21 10:30:00", "2026-08-21 11:30:00")],
        "candidate_rows": [],
        "ai_candidate_rows": [],
        "unfilled_candidate_review": {"items": []},
        "trade_groups": {},
        "statistics": {},
        "program_issues": [],
        "strategy_issues": [],
        "execution_issues": [],
    }
    _write_daily(root, "2026-08-21", data2)
    return root


def test_groups_not_mixed(tmp_path):
    root = _build_week_root(tmp_path)
    facts, days = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    assert facts["groups"]["ea"]["closed_count"] == 3
    assert facts["groups"]["ai"]["closed_count"] == 2
    assert facts["groups"]["manual"]["closed_count"] == 1


def test_open_close_win_loss_add_up(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    ea = facts["groups"]["ea"]
    assert ea["closed_count"] == ea["win"] + ea["loss"] + ea["breakeven"]
    # 3 笔 EA：1 盈 2 亏
    assert ea["win"] == 1
    assert ea["loss"] == 2


def test_r_not_zero_when_missing(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    ea = facts["groups"]["ea"]
    # 有 final_r 的算真实平均；不是 0 占位
    assert ea["avg_r"] is not None
    assert ea["avg_r"] != 0.0


def test_direction_and_route_stats(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    ea = facts["groups"]["ea"]
    assert ea["by_direction"]["BUY"]["count"] == 2
    assert ea["by_direction"]["SELL"]["count"] == 1
    assert ea["by_route"]["Fib + PA"]["count"] == 1
    assert ea["by_route"]["EMA H2/H3"]["count"] == 1
    assert ea["by_route"]["EMA L2/L3"]["count"] == 1


def test_mfe_mae_buckets(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    ea = facts["groups"]["ea"]
    # 亏损单 p2：mfe 0.1 → <0.2R；p3：mfe 缺失 → 数据不足
    assert ea["loss_mfe_buckets"]["开仓后最大浮盈<0.2R"] == 1
    assert ea["loss_mfe_buckets"]["数据不足"] == 1
    # 盈利单 p1：mfe 1.8 / final 1.0 → 回吐记录
    assert ea["win_retrace"] and ea["win_retrace"][0]["mfe_r"] == 1.8


def test_ai_allow_reject_and_unfilled(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    cand = facts["candidates"]
    assert cand["ai_allow"] == 1
    assert cand["ai_reject"] == 1
    assert cand["ai_allowed_results"]["win"] == 1
    assert cand["unfilled_classification"].get("潜在执行型错失") == 1


def test_v14_v20_split(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    vs = facts["version_split"]
    assert vs["V14"]["ea_count"] == 2
    assert vs["V20"]["ea_count"] == 1
    # V20 Baseline 常量正确
    assert V20_BASELINE == datetime(2026, 8, 21, 10, 1, 11)


def test_incomplete_title_and_frozen_payload(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21", incomplete=True)
    resp = build_weekly_fallback_response(facts)
    card = build_weekly_lark_card("2026-08-17", facts, resp)
    assert "截至当前" in card["card"]["header"]["title"]["content"]
    payload = build_weekly_payload(facts)
    assert "groups" in payload and "candidates" in payload


def test_no_english_internal_states_in_card(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    resp = build_weekly_fallback_response(facts)
    card = build_weekly_lark_card("2026-08-17", facts, resp)
    text = "\n".join(
        str(e.get("content", "")) for e in card["card"]["body"]["elements"]
    )
    for token in ("OPEN", "WAIT", "PASS", "FAIL", "MFE", "MAE", "DEAL_REASON", "RULE_BLOCKED"):
        assert token not in text


def test_last_completed_week():
    monday, friday = last_completed_week(datetime(2026, 8, 21, 12, 0))
    assert monday == "2026-08-10"
    assert friday == "2026-08-14"


def test_ai_rejected_results_quantified(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    rej = facts["candidates"]["ai_rejected_results"]
    assert rej["total"] == 1
    assert rej["effective_filter"] + rej["missed"] + rej["unable_to_confirm"] == rej["total"]


def test_version_split_ea(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    ea = facts["groups"]["ea"]
    assert ea["by_version"]["V14"]["count"] == 2
    assert ea["by_version"]["V20"]["count"] == 1
    # 版本拆分里必须有胜负，不能只给数字
    assert "win" in ea["by_version"]["V14"]
    assert "loss" in ea["by_version"]["V14"]


def test_typical_trades_not_forced(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21")
    typical = facts["typical_trades"]
    assert len(typical) <= 3


def test_daily_context_covers_all_weekdays(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21", incomplete=True)
    dates = [c["date"] for c in facts["daily_context"]]
    assert "2026-08-17" in dates
    assert "2026-08-21" in dates
    assert len(dates) == len(set(dates))


def test_daily_section_has_no_boilerplate(tmp_path):
    root = _build_week_root(tmp_path)
    facts, _ = aggregate_weekly_facts(root, "2026-08-17", "2026-08-21", incomplete=True)
    resp = build_weekly_fallback_response(facts)
    card = build_weekly_lark_card("2026-08-17", facts, resp)
    text = "\n".join(str(e.get("content", "")) for e in card["card"]["body"]["elements"])
    assert "无需人工干预" not in text


def test_ea_only_guard_strips_ai_trade_reference():
    assert "AI交易" not in _guard_ea_only("EA盈利单仅有1笔AI交易记录最大浮盈1.08R")
    assert _guard_ea_only("EA按方向分析正常") == "EA按方向分析正常"


def test_manual_claims_guard():
    assert "无有效成交记录" not in _guard_manual_claims("人工组无有效成交记录，无法比较")


def test_filter_rating_guard():
    assert "过滤效果中等" not in _guard_filter_rating("整体过滤效果中等")
    assert "足够证据" in _guard_filter_rating("整体过滤效果中等")


def test_strip_supported_rules_claim():
    text = "已有规则得到支持：AI拒绝的部分信号事后未达目标。需要继续观察：连续止损日是否与市场状态相关。"
    out = _strip_supported_rules_claim(text)
    assert "已有规则得到支持" not in out
    assert "需要继续观察" in out


def test_guard_risk_control_claim():
    facts = {"groups": {"ai": {"avg_r": 0.03}, "ea": {"avg_r": -0.41}}}
    out = _guard_risk_control_claim("Parallel AI的亏损比EA低，主要因为交易频率低且单笔风险控制更紧。", facts)
    assert "单笔风险控制更紧" not in out
    assert "不能据此判断Parallel AI优于EA或风险控制更好" in out


def test_guard_risk_control_claim_catches_softer_comparison():
    facts = {"groups": {"ai": {"avg_r": 0.03}, "ea": {"avg_r": -0.41}}}
    out = _guard_risk_control_claim("Parallel AI 5笔交易胜率40%，表现稍稳。", facts)
    assert "表现稍稳" not in out
    assert "不能据此判断Parallel AI优于EA或风险控制更好" in out


def test_guard_win_mfe_claim_removes_fabricated_data():
    ea_stats = {"win_retrace": [], "win": 3}
    text = "亏损单数据不足。盈利单仅两个SELL有数据，其中一个曾浮盈1.08R最终只赚0.68R。"
    out = _guard_win_mfe_claim(text, ea_stats)
    assert "盈利单仅两个SELL有数据" not in out
    assert "亏损单数据不足" in out
