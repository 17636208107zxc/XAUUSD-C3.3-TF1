from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import tools.ai_review_service as review_service

from tools.daily_review_renderer import (
    build_comparison_card_markdown,
    build_daily_review_sections,
    format_candidate_no_fill_reason,
    humanize_review_reason,
    render_candidate_card_markdown,
    render_daily_review_markdown,
)


@pytest.fixture
def sample_daily_payload() -> dict:
    return {
        "review_date": "2026-08-11",
        "market_metrics": {
            "open": 4389.97,
            "high": 4435.09,
            "low": 4356.76,
            "close": 4367.80,
            "range_usd": 78.33,
            "net_change_usd": -22.17,
            "net_change_pct": -0.51,
        },
        "statistics": {
            "candidate_count": 4,
            "local_reject_count": 162,
            "ai_allow_count": 4,
            "ai_reject_count": 0,
            "pending_count": 3,
            "trade_count": 1,
            "win_count": 1,
            "loss_count": 0,
            "net_profit": 88.20,
            "block_reason_counts": {
                "trend_not_ready": 20,
                "swing_not_ready": 8,
                "fib_not_ready": 83,
                "price_action_not_ready": 31,
                "risk_reward_not_ready": 14,
                "spread_or_time_guard": 5,
                "other": 114,
            },
        },
        "actual_trade_rows": [
            {
                "position_id": "720560839",
                "direction": "SELL",
                "status": "closed",
                "open_time": "2026.08.11 19:50:34",
                "close_time": "2026.08.11 21:00:00",
                "entry_price": 4382.05,
                "initial_sl": 4386.14,
                "initial_risk": 49.08,
                "whole_trade_net": 88.20,
                "today_net": 88.20,
                "final_r": 1.80,
                "tp1_done": True,
                "tp2_done": True,
                "runner_done": True,
                "close_reason": "DEAL_REASON_CLIENT",
            }
        ],
        "strategy_issues": [],
        "program_issues": [],
    }


@pytest.fixture
def sample_daily_response() -> dict:
    return {
        "review_date": "2026-08-11",
        "data_status": "完整",
        "ea_runtime_status": "正常",
        "market_summary": (
            "先涨后跌，午后反转下行，尾段转为震荡。"
            "亚盘延续上涨，午后见顶后持续回落，最终收于4367.80。"
        ),
        "market_regime": "先趋势后震荡",
        "strategy_market_fit": "唯一成交的SELL订单与午后反转下行行情匹配较好。",
        "ai_filter_assessment": "4个候选全部通过AI审核，当天没有AI实际过滤样本。",
        "trade_statistics": {
            "candidate_count": 4,
            "local_reject_count": 162,
            "ai_allow_count": 4,
            "ai_reject_count": 0,
            "pending_count": 3,
            "trade_count": 1,
            "win_count": 1,
            "loss_count": 0,
            "net_profit": 88.20,
            "max_profit_trade": 88.20,
            "max_loss_trade": 88.20,
        },
        "opened_trade_reason": "回调与反转条件成立。",
        "no_trade_main_reason": "不适用",
        "block_reason_counts": {
            "trend_not_ready": 20,
            "swing_not_ready": 8,
            "fib_not_ready": 83,
            "price_action_not_ready": 31,
            "risk_reward_not_ready": 14,
            "spread_or_time_guard": 5,
            "other": 114,
        },
        "representative_trades": [],
        "economic_event_summary": "无",
        "execution_issues": [],
        "observation_items": ["继续跟踪3个未触发候选的最终状态。"],
        "validation_issues": [],
        "backtest_suggestions": [],
        "markdown_report": (
            "## 今日行情\n旧版重复行情。\n\n"
            "## 今日交易\n旧版重复交易。\n\n"
            "## 总结与下一步\n整体运行正常。\n"
            "1. 跟踪3个未触发候选；\n2. 核对客户端平仓来源。"
        ),
        "issue_judgment": "当前问题主要集中在订单执行环节，策略过滤暂未发现明显异常。",
        "conclusion_summary": "AI结论：样本量仍不足以修改EA，继续按现有规则运行。",
        "market_playbook": {
            "trend_verdict": "当天属于先趋势后震荡，不算强趋势。",
            "entry_accuracy": "规则上共2处应该开单，实际开出1处，未开出1处。",
            "missing_reason": "未开出的那处属于入场路径判断不同，属于规则正常过滤。",
            "actionable_note": "继续观察同类路径机会是否反复被同一条规则挡住。",
        },
    }


def test_renderer_builds_authoritative_four_section_review(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    sections = build_daily_review_sections(
        "2026-08-11",
        sample_daily_payload,
        sample_daily_response,
        {"monitor": 5, "daily": 1},
    )
    body = "\n\n".join(sections)

    assert [section.splitlines()[0] for section in sections] == [
            "## 1. 今日行情",
            "## 2. 今日行情回顾与开单准确性",
            "## 3. 今日交易",
            "## 4. 今日问题",
            "## 5. 下一步",
    ]
    assert "行情类型：" not in body
    assert "结构特征：" not in body
    assert "**正式候选：** 4｜**实际成交：** 1｜**未成交：** 3" in body
    assert "**AI允许：** 4个" in body
    assert "信号审核情况" not in body
    assert "Fib：83｜PA：31｜趋势：20" not in body
    assert "实际成交#1｜交易#720560839｜SELL｜已结算" in body
    assert "净盈利：**+88.20 USD**" in body
    assert "整笔收益：**+1.80R**（1R≈49.08 USD）" in body
    assert "TP1：**已执行**｜TP2：**已执行**｜剩余仓位：**已执行**" in body
    assert "平仓方式：**桌面MT5客户端请求平仓（此前已完成TP1、TP2、剩余仓位退出）**" in body
    assert "数据来源" not in body
    assert "旧版重复行情" not in body
    assert "旧版重复交易" not in body
    assert body.count("**安全校验：** EA复盘") == 1


def test_renderer_uses_confirmed_candidate_template_without_repeating_or_guessing(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    payload = copy.deepcopy(sample_daily_payload)
    payload["statistics"].update(
        {
            "trade_count": 2,
            "win_count": 2,
            "loss_count": 0,
            "net_profit": 267.69,
            "pending_count": 2,
        }
    )
    payload["actual_trade_rows"].insert(
        0,
        {
            "position_id": "718041351",
            "direction": "SELL",
            "status": "closed",
            "open_time": "2026.08.11 06:46:38",
            "close_time": "2026.08.11 11:07:23",
            "entry_price": 4415.34,
            "initial_volume": 0.07,
            "initial_sl": 4422.03,
            "whole_trade_net": 179.49,
            "today_net": 179.49,
            "final_r": None,
            "initial_risk": 0,
            "tp1_done": True,
            "tp2_done": True,
            "runner_done": True,
            "close_reason": "DEAL_REASON_CLIENT",
            "data_source": "legacy_event",
        },
    )
    payload["actual_trade_rows"][1]["data_source"] = "lifecycle"
    payload["candidate_rows"] = [
        {
            "sequence": 1,
            "signal_bar_time": "2026.08.11 06:40:00",
            "candidate_time": "2026-08-11 06:45:00",
            "direction": "SELL",
            "route": "FIB_PA",
            "planned_entry": 4415.40,
            "planned_sl": 4422.03,
            "planned_tp1": 4408.01,
            "ai_status": "allow",
            "ai_time": "2026-08-11 06:45:01",
            "ai_confidence": 78,
            "outcome": "filled",
            "outcome_time": "2026-08-11 06:46:38",
            "outcome_reason": "价格触发，挂单成功成交",
            "position_id": "718041351",
        },
        {
            "sequence": 2,
            "signal_bar_time": "2026.08.11 19:15:00",
            "candidate_time": "2026-08-11 19:19:59",
            "direction": "SELL",
            "route": "FIB_PA",
            "planned_entry": 4378.20,
            "planned_sl": 4385.33,
            "planned_tp1": 4369.15,
            "ai_status": "allow",
            "ai_time": "2026-08-11 19:20:00",
            "ai_confidence": 76,
            "outcome": "expired",
            "outcome_time": "2026-08-11 19:34:59",
            "outcome_reason": "市场未在有效期内触发入场价，挂单正常到期",
            "position_id": "",
        },
        {
            "sequence": 3,
            "signal_bar_time": "2026.08.11 19:45:00",
            "candidate_time": "2026-08-11 19:49:59",
            "direction": "SELL",
            "route": "BOTH",
            "planned_entry": 4382.05,
            "planned_sl": 4386.14,
            "planned_tp1": 4369.15,
            "ai_status": "allow",
            "ai_time": "2026-08-11 19:50:00",
            "ai_confidence": 76,
            "outcome": "filled",
            "outcome_time": "2026-08-11 19:50:33",
            "outcome_reason": "价格触发，挂单成功成交",
            "position_id": "720560839",
        },
        {
            "sequence": 4,
            "signal_bar_time": "2026.08.11 23:20:00",
            "candidate_time": "2026-08-11 23:24:59",
            "direction": "BUY",
            "route": "FIB_PA",
            "planned_entry": 4370.08,
            "planned_sl": 4367.33,
            "planned_tp1": 4374.21,
            "ai_status": "allow",
            "ai_time": "2026-08-11 23:25:00",
            "ai_confidence": 76,
            "outcome": "wait_missed",
            "outcome_time": "2026-08-11 23:25:04",
            "outcome_reason": "买入挂单价已被Ask达到或越过",
            "position_id": "",
        },
    ]

    sections = build_daily_review_sections(
        "2026-08-11", payload, sample_daily_response, {"monitor": 5, "daily": 1}
    )
    body = "\n\n".join(sections)

    assert [section.splitlines()[0] for section in sections] == [
        "## 1. 今日行情",
        "## 2. 今日行情回顾与开单准确性",
        "## 3. 今日交易",
        "## 4. 今日问题",
        "## 5. 下一步",
    ]
    assert "**正式候选：** 4｜**实际成交：** 2｜**未成交：** 2" in body
    assert "**AI允许：** 4个" in body
    assert "已实现净盈亏：** **+267.69 USD**" in body
    assert "实际成交#1｜交易#718041351｜SELL｜已结算" in body
    assert "实际成交#2｜交易#720560839｜SELL｜已结算" in body
    assert "数据来源" not in body
    assert "**盈利因子：** —" in body
    assert "**全日总R：** **+1.80R**｜**平均R：** +1.80R" in body
    assert "162次不是162个交易信号" not in body
    assert "候选#" not in body

    from tools.daily_review_renderer import render_candidate_card_markdown

    candidate_card = render_candidate_card_markdown(
        "2026-08-11", payload, {"monitor": 5, "daily": 1}
    )
    assert "## 候选#1｜SELL｜已成交" in candidate_card
    assert "信号K线：11:40｜候选形成：11:45:00" in candidate_card
    assert "## 候选#2｜SELL｜未成交" in candidate_card
    assert "挂单超过有效时间后被撤销" in candidate_card
    assert "## 候选#4｜BUY｜未成交" in candidate_card
    assert "距离不足，未创建真实挂单" in candidate_card
    assert "## 今日候选汇总" in candidate_card
    assert "**正式候选：** 4个" in candidate_card
    assert "候选#4｜BUY｜未成交" in candidate_card


def test_renderer_keeps_trade_when_frozen_risk_is_unavailable(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    payload = copy.deepcopy(sample_daily_payload)
    payload["actual_trade_rows"][0]["initial_risk"] = 0
    payload["actual_trade_rows"][0]["final_r"] = None

    text = render_daily_review_markdown(
        "2026-08-11", payload, sample_daily_response, {}
    )

    assert "#720560839" in text
    assert "整笔收益：无法计算" in text


def test_renderer_does_not_mislabel_partial_realized_profit_as_floating(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    payload = copy.deepcopy(sample_daily_payload)
    payload["actual_trade_rows"] = [
        {
            **payload["actual_trade_rows"][0],
            "position_id": "732118770",
            "direction": "SELL",
            "status": "open_at_day_end",
            "close_time": "",
            "close_reason": "",
            "tp1_done": True,
            "tp2_done": False,
            "runner_done": False,
            "initial_risk": 48.00,
            "whole_trade_net": 18.30,
            "today_net": 18.30,
            "final_r": None,
            "max_favorable_r": 0.55,
        }
    ]

    text = render_daily_review_markdown(
        "2026-08-11", payload, sample_daily_response, {}
    )

    assert "**实际成交#1｜交易#732118770｜SELL｜收盘持仓**" in text
    assert "剩余仓位：**仍在持仓中**" in text
    assert "截至收盘已实现：**+18.30 USD**" in text
    assert "剩余持仓浮动盈亏：未记录" in text
    assert "未结算｜当前R：无法计算（缺少收盘浮动盈亏）" in text
    assert "当前浮动盈亏：**+18.30 USD**" not in text


def test_renderer_rounds_average_r_as_financial_half_up(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    payload = copy.deepcopy(sample_daily_payload)
    payload["actual_trade_rows"] = [
        {**payload["actual_trade_rows"][0], "position_id": "1", "final_r": 3.83},
        {**payload["actual_trade_rows"][0], "position_id": "2", "final_r": 1.80},
    ]
    payload["statistics"].update({"trade_count": 2, "win_count": 2, "net_profit": 267.69})

    text = render_daily_review_markdown("2026-08-11", payload, sample_daily_response, {})

    assert "**全日总R：** **+5.63R**｜**平均R：** +2.82R" in text


def test_renderer_reports_inconsistent_local_reject_breakdown(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    payload = copy.deepcopy(sample_daily_payload)
    payload["statistics"]["local_reject_count"] = 100

    text = render_daily_review_markdown(
        "2026-08-11", payload, sample_daily_response, {}
    )

    assert "分类数据不一致" not in text
    assert "EA本地规则拒绝" not in text
    assert "其他：-34" not in text


def test_renderer_uses_plain_ai_summary_and_drops_legacy_local_reject_claim(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    response = copy.deepcopy(sample_daily_response)
    response["ai_filter_assessment"] = (
        "4个候选全部允许，AI没有阻止有利机会。"
        "盘中162次本地拒绝以Fib和PA为主，有效防止追单。"
    )

    text = render_daily_review_markdown(
        "2026-08-11", sample_daily_payload, response, {}
    )

    assert "**正式候选：** 4｜**实际成交：** 1｜**未成交：** 3" in text
    assert "**AI允许：** 4个" in text
    assert "今天形成的4个正式候选，AI全部同意开单" not in text
    assert "候选没有成交，不代表它被AI拒绝" not in text
    assert "4个候选全部允许，AI没有阻止有利机会。" not in text
    assert "盘中162次本地拒绝" not in text


def test_renderer_localizes_mt5_close_reason_codes() -> None:
    from tools.daily_review_renderer import _close_reason_text

    assert _close_reason_text("DEAL_REASON_CLIENT") == "桌面MT5客户端请求平仓"
    assert _close_reason_text("DEAL_REASON_EXPERT") == "EA主动平仓"
    assert _close_reason_text("DEAL_REASON_TP") == "止盈平仓"
    assert _close_reason_text("DEAL_REASON_SL") == "止损平仓"
    assert _close_reason_text("DEAL_REASON_EXPIRED") == "挂单到期失效"
    assert _close_reason_text("UNKNOWN_CODE") == "UNKNOWN_CODE"
    assert _close_reason_text("") == ""


def test_renderer_no_trade_day_uses_clear_no_candidate_wording() -> None:
    payload = {
        "review_date": "2026-08-01",
        "market_metrics": {
            "open": 4378.00,
            "high": 4392.40,
            "low": 4365.10,
            "close": 4381.30,
            "range_usd": 27.30,
            "net_change_usd": 3.30,
            "net_change_pct": 0.08,
        },
        "statistics": {
            "candidate_count": 0,
            "local_reject_count": 24,
            "ai_allow_count": 0,
            "ai_reject_count": 0,
            "pending_count": 0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0.0,
            "block_reason_counts": {
                "fib_not_ready": 10,
                "price_action_not_ready": 8,
                "trend_not_ready": 4,
                "other": 2,
            },
        },
        "actual_trade_rows": [],
        "strategy_issues": [],
        "program_issues": [],
    }
    response = {
        "review_date": "2026-08-01",
        "data_status": "完整",
        "ea_runtime_status": "正常",
        "market_summary": "全天震荡整理，波动收敛。",
        "market_regime": "震荡",
        "strategy_market_fit": "无实际成交。",
        "ai_filter_assessment": "当天没有正式候选。",
        "trade_statistics": payload["statistics"],
        "opened_trade_reason": "不适用",
        "no_trade_main_reason": "全天没有形成开仓条件。",
        "block_reason_counts": payload["statistics"]["block_reason_counts"],
        "representative_trades": [],
        "economic_event_summary": "无",
        "execution_issues": [],
        "observation_items": [],
        "validation_issues": [],
        "backtest_suggestions": [],
        "markdown_report": "",
        "issue_judgment": "暂无需要判断的问题。",
        "conclusion_summary": "AI结论：当日无交易，整体运行正常。",
        "market_playbook": {
            "trend_verdict": "当天属于区间震荡。",
            "entry_accuracy": "规则上共0处应该开单。",
            "missing_reason": "当天没有出现规则上应该开单却未开出的位置。",
            "actionable_note": "继续观察。",
        },
    }

    sections = build_daily_review_sections(
        "2026-08-01",
        payload,
        response,
        {"monitor": 5, "daily": 1},
    )
    body = "\n\n".join(sections)

    assert "今天没有形成正式候选" not in body
    assert "样本量不足以修改EA" not in body
    assert "其余0个候选的明确去向见上方生命周期。" not in body
    assert "当前历史日报没有保存逐候选生命周期" not in body
    assert "**正式候选：** 0｜**实际成交：** 0｜**未成交：** 0" in body
    assert "**AI允许：** 0个" in body
    assert "本交易日没有实际成交。" in body

    from tools.daily_review_renderer import render_candidate_card_markdown

    candidate_card = render_candidate_card_markdown("2026-08-01", payload)
    assert "今日没有形成正式候选，因此不发送本卡片。" in candidate_card


def test_daily_card_and_local_markdown_share_the_same_sections(
    tmp_path,
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    counts = {"monitor": 5, "daily": 1}
    card = review_service.build_daily_lark_card(
        "2026-08-11", sample_daily_payload, sample_daily_response, counts
    )
    review_service.write_daily_output(
        tmp_path,
        "2026-08-11",
        sample_daily_payload,
        sample_daily_response,
        counts,
    )

    card_sections = [
        element["content"]
        for element in card["card"]["body"]["elements"]
        if element.get("tag") == "markdown"
    ]
    local_text = (
        tmp_path
        / "Daily_Review"
        / "2026-08-11"
        / "Daily_Review_2026-08-11.md"
    ).read_text(encoding="utf-8")

    assert local_text.startswith("# 每日复盘｜2026-08-11\n")
    assert "\n\n".join(card_sections) in local_text
    assert "## 4. 今日问题" in local_text
    assert "## 3. AI过滤" not in local_text
    assert local_text.count("## 1. 今日行情") == 1
    assert local_text.count("**安全校验：** EA复盘") == 1
    assert (
        tmp_path
        / "Daily_Review"
        / "2026-08-11"
        / "Daily_Candidates_2026-08-11.md"
    ).exists()


def test_preview_html_renders_business_title_and_all_sections(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    from tools.render_daily_lark_preview import render_card_html

    card = review_service.build_daily_lark_card(
        "2026-08-11",
        sample_daily_payload,
        sample_daily_response,
        {"monitor": 5, "daily": 1},
    )

    html_text = render_card_html(card)

    assert "每日复盘｜2026-08-11" in html_text
    assert "今日行情" in html_text
    assert "今日问题" in html_text
    assert "<strong>正式候选：</strong> 4" in html_text
    assert "其他：28" not in html_text
    assert "<strong>安全校验：</strong> EA复盘" in html_text
    assert "<h3>📌 正式候选明细</h3>" not in html_text
    assert "<strong>【🔵 实际成交逐笔明细】</strong>" in html_text


def test_preview_script_runs_directly_from_project_root(
    tmp_path: Path,
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    data_path = tmp_path / "daily-data.json"
    response_path = tmp_path / "daily-response.json"
    output_path = tmp_path / "preview.html"
    data_path.write_text(
        json.dumps(sample_daily_payload, ensure_ascii=False), encoding="utf-8"
    )
    response_path.write_text(
        json.dumps(sample_daily_response, ensure_ascii=False), encoding="utf-8"
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/render_daily_lark_preview.py",
            "--day",
            "2026-08-11",
            "--data",
            str(data_path),
            "--response",
            str(response_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output_path.exists()
    assert "<strong>正式候选：</strong>" in output_path.read_text(encoding="utf-8")


def test_renderer_marks_ai_error_as_grace_degraded() -> None:
    payload = {
        "review_date": "2026-08-13",
        "market_metrics": {
            "open": 4400.0,
            "high": 4450.0,
            "low": 4350.0,
            "close": 4380.0,
            "range_usd": 100.0,
            "net_change_usd": -20.0,
            "net_change_pct": -0.45,
        },
        "statistics": {
            "candidate_count": 1,
            "local_reject_count": 0,
            "ai_allow_count": 0,
            "ai_reject_count": 0,
            "ai_error_count": 1,
            "pending_count": 0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0,
            "block_reason_counts": {},
        },
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026.08.13 02:00:00",
                "candidate_time": "2026-08-13 02:04:59",
                "direction": "SELL",
                "route": "EMA_L23",
                "planned_entry": 4401.53,
                "planned_sl": 4404.67,
                "planned_tp1": 4398.36,
                "planned_rr": 1.0,
                "ai_status": "error",
                "ai_allow_trade": True,
                "ai_time": "2026-08-13 02:05:00",
                "ai_confidence": 0,
                "ai_reason": "DeepSeek请求失败，HTTP=404，MT5错误=0（降级放行）",
                "outcome": "ai_error",
                "outcome_time": "2026-08-13 02:05:00",
                "outcome_reason": "AI审核异常·降级放行（DeepSeek请求失败后按本地信号继续）",
                "order_ticket": "",
                "position_id": "",
                "deal_ticket": "",
                "entry_facts": {},
            }
        ],
        "actual_trade_rows": [],
        "ai_reject_analysis": {"total_rejected": 0},
        "program_issues": [],
        "strategy_issues": [],
    }
    response = {
        "review_date": "2026-08-13",
        "data_status": "完整",
        "ea_runtime_status": "正常",
        "market_summary": "行情描述",
        "market_regime": "震荡",
        "strategy_market_fit": "暂无异常",
        "ai_filter_assessment": "1个异常降级",
        "trade_statistics": payload["statistics"],
        "block_reason_counts": {},
        "execution_issues": [],
        "validation_issues": [],
        "markdown_report": "整体运行正常。",
    }

    markdown = render_daily_review_markdown(
        "2026-08-13", payload, response, {"monitor": 0, "daily": 1}
    )

    assert "AI异常：" in markdown
    assert "**AI允许：** 0个" in markdown
    assert "异常·降级放行" not in markdown
    assert "AI审核异常·降级放行" not in markdown
    assert "DeepSeek审核拒绝" not in markdown

    from tools.daily_review_renderer import render_candidate_card_markdown

    candidate_card = render_candidate_card_markdown(
        "2026-08-13", payload, {"monitor": 0, "daily": 1}
    )
    assert "AI审核异常" in candidate_card
    assert "异常信息：DeepSeek请求失败" in candidate_card
    assert "HTTP=404" in candidate_card


def test_humanize_review_reason_translates_program_fields():
    raw = (
        "BUY方向与HH_HL主结构一致，EMA_H23路径有效，"
        "但fib_path_valid=false、fib_zone=INVALID、three_bar=false；"
        "RR=0.81偏低。"
    )
    result = humanize_review_reason(raw)
    assert "fib_path_valid" not in result
    assert "fib_zone=INVALID" not in result
    assert "three_bar=false" not in result
    assert "EMA_H23" not in result
    assert "RR=0.81" not in result
    assert "Fib路径无效" in result
    assert "Fib回调超出有效区间" in result
    assert "三根推进条件未满足" in result
    assert "EMA H2/H3路径" in result
    assert "盈亏空间0.81R" in result
    # 不能出现 "路径路径" 这种重复
    assert "路径路径" not in result


def test_format_candidate_no_fill_reason_returns_direct_reason():
    assert (
        format_candidate_no_fill_reason({"outcome": "ai_rejected"})
        == "DeepSeek审核拒绝"
    )
    assert (
        format_candidate_no_fill_reason({"outcome": "ai_error"})
        == "AI审核异常"
    )
    assert (
        format_candidate_no_fill_reason({"outcome": "expired"})
        == "挂单超过有效时间后被撤销"
    )
    assert (
        format_candidate_no_fill_reason({"outcome": "cancelled"})
        == "信号失效后挂单被撤销"
    )
    assert (
        format_candidate_no_fill_reason({"outcome": "wait_missed"})
        == "距离不足，未创建真实挂单；等待期间原入场价被市场触达，信号作废"
    )


def test_candidate_card_ai_reject_does_not_duplicate_reason():
    from tools.daily_review_renderer import render_candidate_card_markdown

    payload = {
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_bar_time": "2026.08.17 09:20:00",
                "candidate_time": "2026-08-17 09:24:59",
                "direction": "BUY",
                "route": "EMA_L23",
                "planned_entry": 4397.25,
                "planned_sl": 4392.28,
                "planned_tp1": 4401.27,
                "ai_status": "reject",
                "ai_time": "2026-08-17 09:25:01",
                "ai_confidence": 65,
                "ai_reason": (
                    "BUY方向与HH_HL主结构一致，EMA_H23路径有效，"
                    "但fib_path_valid=false、fib_zone=INVALID，综合质量一般。"
                ),
                "outcome": "ai_rejected",
                "outcome_time": "2026-08-17 09:25:01",
                "outcome_reason": "DeepSeek审核拒绝",
                "position_id": "",
            }
        ],
        "unfilled_candidate_review": {"items": []},
    }
    card = render_candidate_card_markdown("2026-08-17", payload, {"monitor": 0, "daily": 1})
    assert "未成交原因：14:25:01｜DeepSeek审核拒绝" in card
    assert "拒绝依据：" in card
    # 未成交原因不应再复制完整 AI 理由
    assert card.count("fib_path_valid") == 0
    assert card.count("fib_zone=INVALID") == 0
    assert "Fib路径无效" in card


def test_renderer_backfill_title_marker(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    text = render_daily_review_markdown(
        "2026-08-11", sample_daily_payload, sample_daily_response, {}, backfill=True
    )
    assert text.startswith("# 每日复盘（补发）｜2026-08-11")

    from tools.daily_review_renderer import render_candidate_card_markdown

    candidate_card = render_candidate_card_markdown(
        "2026-08-11", sample_daily_payload, {}, backfill=True
    )
    assert candidate_card.startswith("# 候选订单明细（补发）｜2026-08-11")


def test_renderer_daily_limit_display_uses_configured_quota(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    text = render_daily_review_markdown(
        "2026-08-11",
        sample_daily_payload,
        sample_daily_response,
        {"monitor": 5, "daily": 1},
        daily_limit=3,
        monitor_limit=29,
    )
    assert "**API使用：** 盯盘 5 / 29｜日报 1 / 3" in text

    card = review_service.build_daily_lark_card(
        "2026-08-11",
        sample_daily_payload,
        sample_daily_response,
        {"monitor": 5, "daily": 1},
        backfill=True,
        daily_limit=3,
        monitor_limit=29,
    )
    assert card["card"]["header"]["title"]["content"] == "每日复盘（补发）｜2026-08-11"


def test_renderer_trade_groups_section(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    payload = copy.deepcopy(sample_daily_payload)
    response = copy.deepcopy(sample_daily_response)
    payload["trade_groups"] = {
        "ea": {"count": 1, "win": 1, "loss": 0, "net": 88.20, "r_total": 0.0, "r_samples": 0},
        "ai": {"count": 1, "win": 0, "loss": 1, "net": -59.13, "r_total": -1.15, "r_samples": 1},
        "manual": {"count": 2, "win": 1, "loss": 1, "net": 10.00, "r_total": 0.0, "r_samples": 0},
    }
    response["trade_group_analysis"] = "AI单当日唯一亏损单与网络异常降级放行相关，人工单盈亏分散，暂未发现系统性偏差。"
    text = render_daily_review_markdown(
        "2026-08-11", payload, response, {}
    )
    # 三组对照已从主卡拆出，主卡不再混入。
    assert "三组对照" not in text
    assert "## 4. 今日问题" in text
    assert "## 5. 下一步" in text

    comparison = build_comparison_card_markdown("2026-08-11", payload, response)
    assert "**EA：** 已平仓 1笔｜1盈0亏｜净盈亏 +88.20 USD" in comparison
    assert "**Parallel AI：** 已平仓 1笔｜0盈1亏｜净盈亏 -59.13 USD｜平均R -1.15R" in comparison
    assert "**人工：** 已平仓 2笔｜1盈1亏｜净盈亏 +10.00 USD" in comparison
    assert "**【DeepSeek Pro深度分析】**" in comparison
    assert "AI单当日唯一亏损单与网络异常降级放行相关" in comparison


def test_comparison_card_counts_open_only_ea_and_explains_lifecycle_attribution(
    sample_daily_payload: dict,
    sample_daily_response: dict,
) -> None:
    payload = copy.deepcopy(sample_daily_payload)
    response = copy.deepcopy(sample_daily_response)
    payload["trade_groups"] = {
        "ea": {"count": 0, "open": 1, "win": 0, "loss": 0, "net": 0.0},
        "ai": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0},
        "manual": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0},
    }
    response["trade_group_analysis"] = ""

    card = build_comparison_card_markdown("2026-09-16", payload, response)

    assert "**EA：** 已平仓 0笔 + 持仓 1笔" in card
    assert "今日无正式EA/AI/人工交易" not in card
    assert "EA生命周期" in card
    assert "只按订单魔术号" not in card


def test_c33_route_and_absent_ai_review_are_not_reported_as_missing() -> None:
    candidate = {
        "sequence": 1,
        "signal_id": "C33-SELL-1",
        "direction": "SELL",
        "route": "EMA_RECOVERY_SELL",
        "ai_status": "undecided",
        "outcome": "filled",
        "position_id": "42",
        "entry_facts": {
            "signal_route": "EMA_RECOVERY_SELL",
            "direction": "SELL",
            "context_loaded": False,
        },
    }
    card = render_candidate_card_markdown("2026-09-16", {"candidate_rows": [candidate]})
    assert "入场路径：**EMA回调恢复**" in card
    assert "DeepSeek审核：**未见审核记录**" in card
    assert "无法判断｜—" not in card

    payload = {
        "actual_trade_rows": [{
            "position_id": "42", "direction": "SELL", "status": "closed",
            "open_time": "2026.09.16 16:25:06", "close_time": "2026.09.16 16:44:58",
            "entry_price": 4353.12, "initial_volume": 0.07,
            "initial_sl": 4359.19, "initial_risk": 42.49,
            "tp1_price": 4347.05, "tp2_price": 4340.98,
            "whole_trade_net": 68.31, "today_net": 68.31,
        }],
        "trade_ownership": {"42": "ea"},
        "candidate_rows": [candidate],
        "statistics": {"trade_count": 1, "net_profit": 68.31},
    }
    daily = render_daily_review_markdown("2026-09-16", payload, {})
    assert "**入场路径：** EMA回调恢复" in daily
    assert "路径记录缺失" not in daily


def test_ea_main_card_excludes_ai_trades(sample_daily_payload, sample_daily_response):
    payload = copy.deepcopy(sample_daily_payload)
    response = copy.deepcopy(sample_daily_response)
    payload["trade_ownership"] = {"100": "ai", "200": "ea"}
    payload["actual_trade_rows"] = [
        {"position_id": "100", "magic": "2026072902", "status": "closed",
         "net_profit": -50.0, "direction": "SELL", "entry_price": 100.0,
         "initial_volume": 0.1, "initial_sl": 102.0},
        {"position_id": "200", "magic": "", "status": "closed",
         "net_profit": 20.0, "direction": "BUY", "entry_price": 100.0,
         "initial_volume": 0.1, "initial_sl": 98.0},
    ]
    text = render_daily_review_markdown("2026-08-11", payload, response, {})
    assert "#100" not in text
    assert "#200" in text
    assert "+20.00 USD" in text


def test_parallel_ai_card_uses_chinese_status(sample_daily_payload, sample_daily_response):
    from tools.daily_review_renderer import _ai_execution_status_text

    assert _ai_execution_status_text("PRECHECK_FAIL") == "下单预检未通过"
    assert _ai_execution_status_text("SERVER_REJECTED") == "交易服务器拒绝"
    assert _ai_execution_status_text("PENDING_ACTIVE") == "挂单等待成交"
    assert "PRECHECK_FAIL" not in _ai_execution_status_text("PRECHECK_FAIL")
