"""生成两张卡片（每日复盘主卡 + 正式候选订单明细卡）的本地预览。

场景A：2个正式候选全部成交（1盈1亏），用于查看主卡成绩区与成交逐笔明细。
场景B：1个未成交候选，失效后先触及原止损位、随后最大顺向达 +5.85R，
       用于查看「未成交候选事后走势验证」的判定顺序（先触SL不判错失）。

输出目录：D:\\Backup\\Documents\\EAAI\\artifacts\\daily-review-two-card-preview\\
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_review_service import (
    build_candidate_lark_card,
    build_daily_lark_card,
)
from tools.daily_review_renderer import (
    render_candidate_card_markdown,
    render_daily_review_markdown,
)
from tools.render_daily_lark_preview import render_card_html


OUTPUT_ROOT = Path(r"D:\Backup\Documents\EAAI\artifacts\daily-review-two-card-preview")
API_COUNTS = {"monitor": 5, "daily": 1}


def _response(day: str, issue_judgment: str, conclusion: str) -> dict:
    return {
        "review_date": day,
        "data_status": "完整",
        "ea_runtime_status": "正常",
        "market_summary": "模拟行情描述。",
        "market_regime": "先趋势后震荡",
        "strategy_market_fit": "模拟策略匹配描述。",
        "ai_filter_assessment": "模拟AI审核描述。",
        "trade_statistics": {
            "candidate_count": 0,
            "local_reject_count": 0,
            "ai_allow_count": 0,
            "ai_reject_count": 0,
            "pending_count": 0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0.0,
        },
        "opened_trade_reason": "",
        "no_trade_main_reason": "不适用",
        "block_reason_counts": {},
        "representative_trades": [],
        "economic_event_summary": "无",
        "execution_issues": [],
        "observation_items": [],
        "validation_issues": [],
        "backtest_suggestions": [],
        "markdown_report": "",
        "issue_judgment": issue_judgment,
        "conclusion_summary": conclusion,
    }


def _scenario_a_payload() -> dict:
    """场景A：2个候选全部成交（BUY盈利、SELL亏损）。"""
    return {
        "review_date": "2026-08-13",
        "market_metrics": {
            "open": 4392.40,
            "high": 4441.10,
            "low": 4361.25,
            "close": 4405.60,
            "range_usd": 79.85,
            "net_change_usd": 13.20,
            "net_change_pct": 0.30,
        },
        "statistics": {
            "candidate_count": 2,
            "local_reject_count": 0,
            "ai_allow_count": 2,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 0,
            "trade_count": 2,
            "win_count": 1,
            "loss_count": 1,
            "net_profit": 49.08,
        },
        "actual_trade_rows": [
            {
                "position_id": "728461351",
                "direction": "BUY",
                "status": "closed",
                "open_time": "2026.08.13 05:40:01",
                "close_time": "2026.08.13 08:55:00",
                "entry_price": 4408.20,
                "initial_volume": 0.10,
                "initial_sl": 4403.60,
                "initial_risk": 46.00,
                "tp1_price": 4412.80,
                "tp2_price": 4417.40,
                "tp1_done": True,
                "tp2_done": True,
                "runner_done": True,
                "whole_trade_net": 85.56,
                "today_net": 85.56,
                "final_r": 1.86,
                "max_favorable_r": 2.10,
                "close_reason": "DEAL_REASON_TP",
            },
            {
                "position_id": "729102477",
                "direction": "SELL",
                "status": "closed",
                "open_time": "2026.08.13 14:25:03",
                "close_time": "2026.08.13 15:40:00",
                "entry_price": 4432.75,
                "initial_volume": 0.10,
                "initial_sl": 4437.55,
                "initial_risk": 48.00,
                "tp1_price": 4428.35,
                "tp2_price": 4423.95,
                "tp1_done": False,
                "tp2_done": False,
                "runner_done": False,
                "whole_trade_net": -36.48,
                "today_net": -36.48,
                "final_r": -0.76,
                "max_favorable_r": 0.55,
                "close_reason": "DEAL_REASON_SL",
            },
        ],
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
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "BUY",
                    "context_loaded": True,
                    "fib_retracement": 61.8,
                    "pattern": "pin bar",
                    "initial_sl": 4403.60,
                    "tp1_price": 4412.80,
                    "tp2_price": 4417.40,
                    "ai_allow_trade": True,
                    "initial_risk": 46.00,
                },
            },
            {
                "sequence": 2,
                "signal_id": "S2",
                "signal_bar_time": "2026-08-13 14:20:00",
                "candidate_time": "2026-08-13 14:24:55",
                "direction": "SELL",
                "route": "BOTH",
                "planned_entry": 4432.75,
                "planned_sl": 4437.55,
                "planned_tp1": 4428.35,
                "ai_status": "allow",
                "ai_time": "2026-08-13 14:24:58",
                "ai_confidence": 71,
                "outcome": "filled",
                "outcome_time": "2026-08-13 14:25:03",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "729102477",
                "entry_facts": {
                    "signal_route": "BOTH",
                    "direction": "SELL",
                    "context_loaded": True,
                    "fib_retracement": 50.0,
                    "ema_distance_usd": 0.80,
                    "h_attempt": 3,
                    "pattern": "engulfing,strong reversal",
                    "initial_sl": 4437.55,
                    "tp1_price": 4428.35,
                    "tp2_price": 4423.95,
                    "ai_allow_trade": True,
                    "initial_risk": 48.00,
                },
            },
        ],
        "unfilled_candidate_review": {"items": []},
        "missed_candidate_summary": {},
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_b_payload() -> dict:
    """场景B：1个未成交候选，失效后重新触及原入场价，先触原止损位、随后最大顺向 +5.85R。"""
    return {
        "review_date": "2026-08-14",
        "market_metrics": {
            "open": 4402.15,
            "high": 4448.90,
            "low": 4398.30,
            "close": 4412.75,
            "range_usd": 50.60,
            "net_change_usd": 10.60,
            "net_change_pct": 0.24,
        },
        "statistics": {
            "candidate_count": 1,
            "local_reject_count": 0,
            "ai_allow_count": 1,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 1,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0.0,
        },
        "actual_trade_rows": [],
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026-08-14 09:15:00",
                "candidate_time": "2026-08-14 09:19:57",
                "direction": "SELL",
                "route": "FIB_PA",
                "planned_entry": 4418.40,
                "planned_sl": 4422.20,
                "planned_tp1": 4414.60,
                "ai_status": "allow",
                "ai_time": "2026-08-14 09:19:59",
                "ai_confidence": 77,
                "outcome": "expired",
                "outcome_time": "2026-08-14 09:34:59",
                "outcome_reason": "市场未在有效期内触发入场价，挂单正常到期",
                "position_id": "",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "SELL",
                    "context_loaded": True,
                    "fib_retracement": 56.0,
                    "pattern": "engulfing",
                    "initial_sl": 4422.20,
                    "tp1_price": 4414.60,
                    "tp2_price": 4410.80,
                    "ai_allow_trade": True,
                    "initial_risk": 38.00,
                },
            }
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S1",
                    "sequence": 1,
                    "direction": "SELL",
                    "reason_code": "挂单到期",
                    "reason_text": "市场未在有效期内触发入场价，挂单正常到期",
                    "entry": 4418.40,
                    "sl": 4422.20,
                    "entry_retouched": True,
                    "entry_retouch_time": "2026-08-14 09:37:05",
                    "r30": 0.0,
                    "r60": 0.0,
                    "r_max": 5.85,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": "SL",
                    "classification": "正常未成交",
                    "hit_0_8": True,
                    "hit_1r": True,
                    "hit_2r": True,
                }
            ]
        },
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {},
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_c_payload() -> dict:
    """场景C：1个未成交候选，失效后价格始终未重新触及原入场价，
    无虚拟成交，判定为正常未成交、后续仍无入场机会。"""
    return {
        "review_date": "2026-08-15",
        "market_metrics": {
            "open": 4402.15,
            "high": 4448.90,
            "low": 4398.30,
            "close": 4412.75,
            "range_usd": 50.60,
            "net_change_usd": 10.60,
            "net_change_pct": 0.24,
        },
        "statistics": {
            "candidate_count": 1,
            "local_reject_count": 0,
            "ai_allow_count": 1,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 1,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0.0,
        },
        "actual_trade_rows": [],
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026-08-15 09:15:00",
                "candidate_time": "2026-08-15 09:19:57",
                "direction": "SELL",
                "route": "FIB_PA",
                "planned_entry": 4418.40,
                "planned_sl": 4422.20,
                "planned_tp1": 4414.60,
                "ai_status": "allow",
                "ai_time": "2026-08-15 09:19:59",
                "ai_confidence": 75,
                "outcome": "expired",
                "outcome_time": "2026-08-15 09:34:59",
                "outcome_reason": "市场未在有效期内触发入场价，挂单正常到期",
                "position_id": "",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "SELL",
                    "context_loaded": True,
                    "fib_retracement": 56.0,
                    "pattern": "engulfing",
                    "initial_sl": 4422.20,
                    "tp1_price": 4414.60,
                    "tp2_price": 4410.80,
                    "ai_allow_trade": True,
                    "initial_risk": 38.00,
                },
            }
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S1",
                    "sequence": 1,
                    "direction": "SELL",
                    "reason_code": "挂单到期",
                    "reason_text": "市场未在有效期内触发入场价，挂单正常到期",
                    "entry": 4418.40,
                    "sl": 4422.20,
                    "entry_retouched": False,
                    "entry_retouch_time": "",
                    "r30": None,
                    "r60": None,
                    "r_max": None,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": None,
                    "classification": "正常未成交",
                    "hit_0_8": False,
                    "hit_1r": False,
                    "hit_2r": False,
                }
            ]
        },
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {},
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def write_scenario(
    scenario_name: str,
    day: str,
    payload: dict,
    response: dict,
) -> None:
    output_dir = OUTPUT_ROOT / scenario_name
    output_dir.mkdir(parents=True, exist_ok=True)
    main_card = build_daily_lark_card(day, payload, response, API_COUNTS)
    candidate_card = build_candidate_lark_card(day, payload, response, API_COUNTS)
    main_md = render_daily_review_markdown(day, payload, response, API_COUNTS)
    candidate_md = render_candidate_card_markdown(
        day, payload, API_COUNTS, include_title=False
    )

    (output_dir / f"Main_Card_{day}.md").write_text(main_md, encoding="utf-8")
    (output_dir / f"Main_Card_{day}.html").write_text(
        render_card_html(main_card), encoding="utf-8"
    )
    (output_dir / f"Candidate_Card_{day}.md").write_text(candidate_md, encoding="utf-8")
    (output_dir / f"Candidate_Card_{day}.html").write_text(
        render_card_html(candidate_card), encoding="utf-8"
    )
    (output_dir / f"Payload_{day}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[{scenario_name}] 已生成:")
    for path in sorted(output_dir.glob("*")):
        print(f"  {path}")


def main() -> int:
    write_scenario(
        "Scenario_A_Two_Filled",
        "2026-08-13",
        _scenario_a_payload(),
        _response(
            "2026-08-13",
            "今日2笔成交1盈1亏，盈利单沿趋势运行并完成TP1/TP2退出，"
            "亏损单止损正常触发；策略过滤与执行环节暂未发现明确异常。",
            "AI结论：样本量仍不足以修改EA，继续按现有规则运行。",
        ),
    )
    write_scenario(
        "Scenario_B_One_Unfilled_SL_First",
        "2026-08-14",
        _scenario_b_payload(),
        _response(
            "2026-08-14",
            "今日1个SELL正式候选未成交，经事后走势验证判定为正常未成交，"
            "不属于有效机会错失。本次未成交暂未发现策略或程序异常，"
            "也没有证据支持调整当前挂单执行规则。",
            "AI结论：未成交候选按虚拟成交路径判定正常，暂未发现需要调整执行机制的证据。",
        ),
    )
    write_scenario(
        "Scenario_C_Never_Retouched_No_Entry",
        "2026-08-15",
        _scenario_c_payload(),
        _response(
            "2026-08-15",
            "今日1个SELL正式候选未成交，失效后价格始终未重新触及原入场价，"
            "未形成虚拟成交，判定为正常未成交、后续仍无入场机会。"
            "本次未成交暂未发现策略或程序异常。",
            "AI结论：未形成可成交机会，暂未发现需要调整执行机制的证据。",
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
