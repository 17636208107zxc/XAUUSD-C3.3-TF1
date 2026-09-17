"""生成全套每日复盘双卡（主卡 + 候选卡）本地预览，覆盖主要业务情形。

场景A/B/C（build_two_card_preview.py 原有）：
  A 2个候选全部成交（1盈1亏）
  B 未成交候选重新触及Entry后先触SL（正常未成交）
  C 未成交候选从未重新触及Entry（R 显示 —）

本脚本新增场景 D-I：
  D 挂单距离不足导致高价值执行型错失 + 历史累计达到专项验证条件
  E 同根K线路径顺序无法确认（无Tick，标记无法判断）
  F 空仓日（无候选、无成交）
  G wait_missed（价格越过Entry）明显执行型错失
  H 混合日：2笔成交 + 1个AI拒绝候选
  I 靠近收盘候选，30分钟/1小时观察窗口不足

输出目录：D:\\Backup\\Documents\\EAAI\\artifacts\\daily-review-two-card-preview\\
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_two_card_preview import (
    OUTPUT_ROOT,
    _response,
    _scenario_a_payload,
    _scenario_b_payload,
    _scenario_c_payload,
    write_scenario,
)


def _scenario_d_payload() -> dict:
    """场景D：挂单距离不足 → 重新触及Entry后先触2R（高价值执行型错失），
    同类问题历史累计已达专项验证条件。"""
    return {
        "review_date": "2026-08-16",
        "market_metrics": {
            "open": 4405.20,
            "high": 4440.30,
            "low": 4370.50,
            "close": 4398.60,
            "range_usd": 69.80,
            "net_change_usd": -6.60,
            "net_change_pct": -0.15,
        },
        "statistics": {
            "candidate_count": 2,
            "local_reject_count": 0,
            "ai_allow_count": 2,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 2,
            "trade_count": 1,
            "win_count": 1,
            "loss_count": 0,
            "net_profit": 142.35,
        },
        "actual_trade_rows": [
            {
                "position_id": "730118324",
                "direction": "BUY",
                "status": "closed",
                "open_time": "2026.08.16 05:40:01",
                "close_time": "2026.08.16 08:55:00",
                "entry_price": 4398.50,
                "initial_volume": 0.10,
                "initial_sl": 4393.10,
                "initial_risk": 54.00,
                "tp1_price": 4403.90,
                "tp2_price": 4409.30,
                "tp1_done": True,
                "tp2_done": True,
                "runner_done": True,
                "whole_trade_net": 142.35,
                "today_net": 142.35,
                "final_r": 2.64,
                "max_favorable_r": 2.90,
                "close_reason": "DEAL_REASON_TP",
            }
        ],
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026-08-16 05:35:00",
                "candidate_time": "2026-08-16 05:39:57",
                "direction": "BUY",
                "route": "FIB_PA",
                "planned_entry": 4398.50,
                "planned_sl": 4393.10,
                "planned_tp1": 4403.90,
                "ai_status": "allow",
                "ai_time": "2026-08-16 05:39:59",
                "ai_confidence": 82,
                "outcome": "filled",
                "outcome_time": "2026-08-16 05:40:01",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "730118324",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "BUY",
                    "context_loaded": True,
                    "fib_retracement": 61.8,
                    "pattern": "pin bar",
                    "initial_sl": 4393.10,
                    "tp1_price": 4403.90,
                    "tp2_price": 4409.30,
                    "ai_allow_trade": True,
                    "initial_risk": 54.00,
                },
            },
            {
                "sequence": 2,
                "signal_id": "S2",
                "signal_bar_time": "2026-08-16 10:15:00",
                "candidate_time": "2026-08-16 10:19:57",
                "direction": "SELL",
                "route": "FIB_PA",
                "planned_entry": 4420.00,
                "planned_sl": 4424.60,
                "planned_tp1": 4416.40,
                "ai_status": "allow",
                "ai_time": "2026-08-16 10:19:59",
                "ai_confidence": 74,
                "outcome": "wait_missed",
                "outcome_time": "2026-08-16 10:22:17",
                "outcome_reason": "价格越过原入场位，因挂单距离不足未成交",
                "position_id": "",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "SELL",
                    "context_loaded": True,
                    "fib_retracement": 61.8,
                    "pattern": "strong reversal",
                    "initial_sl": 4424.60,
                    "tp1_price": 4416.40,
                    "tp2_price": 4412.80,
                    "ai_allow_trade": True,
                    "initial_risk": 46.00,
                },
            },
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S2",
                    "sequence": 2,
                    "direction": "SELL",
                    "reason_code": "挂单距离不足",
                    "reason_text": "价格越过原入场位，因挂单距离不足未成交",
                    "entry": 4420.00,
                    "sl": 4424.60,
                    "entry_retouched": True,
                    "entry_retouch_time": "2026-08-16 10:22:17",
                    "r30": 1.20,
                    "r60": 1.85,
                    "r_max": 2.40,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": "2R",
                    "order_unconfirmed": False,
                    "classification": "高价值执行型错失",
                    "hit_0_8": True,
                    "hit_1r": True,
                    "hit_2r": True,
                }
            ]
        },
        "missed_candidate_summary": {
            "validation_met": True,
            "by_reason_code": {
                "挂单距离不足": {
                    "total": 5,
                    "normal": 2,
                    "hit_0_8": 4,
                    "hit_1r": 4,
                    "hit_2r": 3,
                    "miss_high_value": 3,
                    "insufficient": 0,
                },
                "挂单到期": {
                    "total": 2,
                    "normal": 2,
                    "hit_0_8": 0,
                    "hit_1r": 0,
                    "hit_2r": 0,
                    "miss_high_value": 0,
                    "insufficient": 0,
                },
            },
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_e_payload() -> dict:
    """场景E：未成交候选重新触及Entry，但SL与目标在同一根K线内被触及、
    无Tick可解析先后，标记路径顺序无法确认。"""
    return {
        "review_date": "2026-08-17",
        "market_metrics": {
            "open": 4402.00,
            "high": 4438.60,
            "low": 4378.10,
            "close": 4395.40,
            "range_usd": 60.50,
            "net_change_usd": -6.60,
            "net_change_pct": -0.15,
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
                "signal_bar_time": "2026-08-17 09:15:00",
                "candidate_time": "2026-08-17 09:19:57",
                "direction": "SELL",
                "route": "FIB_PA",
                "planned_entry": 4418.40,
                "planned_sl": 4422.20,
                "planned_tp1": 4414.60,
                "ai_status": "allow",
                "ai_time": "2026-08-17 09:19:59",
                "ai_confidence": 75,
                "outcome": "expired",
                "outcome_time": "2026-08-17 09:34:59",
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
                    "entry_retouch_time": "2026-08-17 09:37:05",
                    "r30": 2.10,
                    "r60": 3.20,
                    "r_max": 3.50,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": None,
                    "order_unconfirmed": True,
                    "classification": "无法判断",
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


def _scenario_f_payload() -> dict:
    """场景F：空仓日——没有形成正式候选，也没有实际成交。"""
    return {
        "review_date": "2026-08-18",
        "market_metrics": {
            "open": 4399.80,
            "high": 4430.40,
            "low": 4388.20,
            "close": 4405.10,
            "range_usd": 42.20,
            "net_change_usd": 5.30,
            "net_change_pct": 0.12,
        },
        "statistics": {
            "candidate_count": 0,
            "local_reject_count": 0,
            "ai_allow_count": 0,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_profit": 0.0,
        },
        "actual_trade_rows": [],
        "candidate_rows": [],
        "unfilled_candidate_review": {"items": []},
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {},
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_g_payload() -> dict:
    """场景G：wait_missed——价格已越过原入场位，因挂单距离不足未成交，
    虚拟成交后先触1R，判定为明显执行型错失。"""
    return {
        "review_date": "2026-08-19",
        "market_metrics": {
            "open": 4401.30,
            "high": 4445.70,
            "low": 4385.40,
            "close": 4392.80,
            "range_usd": 60.30,
            "net_change_usd": -8.50,
            "net_change_pct": -0.19,
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
                "signal_bar_time": "2026-08-19 14:55:00",
                "candidate_time": "2026-08-19 14:59:57",
                "direction": "SELL",
                "route": "FIB_PA",
                "planned_entry": 4428.40,
                "planned_sl": 4432.80,
                "planned_tp1": 4425.00,
                "ai_status": "allow",
                "ai_time": "2026-08-19 14:59:59",
                "ai_confidence": 73,
                "outcome": "wait_missed",
                "outcome_time": "2026-08-19 15:02:11",
                "outcome_reason": "价格越过原入场位，因挂单距离不足未成交",
                "position_id": "",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "SELL",
                    "context_loaded": True,
                    "fib_retracement": 50.0,
                    "pattern": "engulfing",
                    "initial_sl": 4432.80,
                    "tp1_price": 4425.00,
                    "tp2_price": 4421.60,
                    "ai_allow_trade": True,
                    "initial_risk": 44.00,
                },
            }
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S1",
                    "sequence": 1,
                    "direction": "SELL",
                    "reason_code": "挂单距离不足",
                    "reason_text": "价格越过原入场位，因挂单距离不足未成交",
                    "entry": 4428.40,
                    "sl": 4432.80,
                    "entry_retouched": True,
                    "entry_retouch_time": "2026-08-19 15:02:11",
                    "r30": 0.92,
                    "r60": 1.25,
                    "r_max": 1.55,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": "1R",
                    "order_unconfirmed": False,
                    "classification": "明显执行型错失",
                    "hit_0_8": True,
                    "hit_1r": True,
                    "hit_2r": False,
                }
            ]
        },
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {
                "挂单距离不足": {
                    "total": 1,
                    "normal": 0,
                    "hit_0_8": 1,
                    "hit_1r": 1,
                    "hit_2r": 0,
                    "miss_high_value": 1,
                    "insufficient": 0,
                }
            },
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_h_payload() -> dict:
    """场景H：混合日——2笔实际成交（1盈1亏），另1个候选被DeepSeek拒绝。"""
    return {
        "review_date": "2026-08-20",
        "market_metrics": {
            "open": 4396.50,
            "high": 4436.20,
            "low": 4368.80,
            "close": 4408.90,
            "range_usd": 67.40,
            "net_change_usd": 12.40,
            "net_change_pct": 0.28,
        },
        "statistics": {
            "candidate_count": 3,
            "local_reject_count": 0,
            "ai_allow_count": 2,
            "ai_reject_count": 1,
            "ai_error_count": 0,
            "pending_count": 1,
            "trade_count": 2,
            "win_count": 1,
            "loss_count": 1,
            "net_profit": 22.75,
        },
        "actual_trade_rows": [
            {
                "position_id": "731205618",
                "direction": "BUY",
                "status": "closed",
                "open_time": "2026.08.20 06:15:02",
                "close_time": "2026.08.20 09:05:00",
                "entry_price": 4392.60,
                "initial_volume": 0.10,
                "initial_sl": 4387.80,
                "initial_risk": 48.00,
                "tp1_price": 4397.20,
                "tp2_price": 4401.80,
                "tp1_done": True,
                "tp2_done": True,
                "runner_done": True,
                "whole_trade_net": 96.75,
                "today_net": 96.75,
                "final_r": 2.02,
                "max_favorable_r": 2.35,
                "close_reason": "DEAL_REASON_TP",
            },
            {
                "position_id": "731556903",
                "direction": "SELL",
                "status": "closed",
                "open_time": "2026.08.20 14:10:03",
                "close_time": "2026.08.20 15:05:00",
                "entry_price": 4424.90,
                "initial_volume": 0.10,
                "initial_sl": 4429.60,
                "initial_risk": 47.00,
                "tp1_price": 4420.60,
                "tp2_price": 4416.30,
                "tp1_done": False,
                "tp2_done": False,
                "runner_done": False,
                "whole_trade_net": -74.00,
                "today_net": -74.00,
                "final_r": -1.57,
                "max_favorable_r": 0.42,
                "close_reason": "DEAL_REASON_SL",
            },
        ],
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026-08-20 06:10:00",
                "candidate_time": "2026-08-20 06:14:57",
                "direction": "BUY",
                "route": "FIB_PA",
                "planned_entry": 4392.60,
                "planned_sl": 4387.80,
                "planned_tp1": 4397.20,
                "ai_status": "allow",
                "ai_time": "2026-08-20 06:14:59",
                "ai_confidence": 81,
                "outcome": "filled",
                "outcome_time": "2026-08-20 06:15:02",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "731205618",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "BUY",
                    "context_loaded": True,
                    "fib_retracement": 61.8,
                    "pattern": "pin bar",
                    "initial_sl": 4387.80,
                    "tp1_price": 4397.20,
                    "tp2_price": 4401.80,
                    "ai_allow_trade": True,
                    "initial_risk": 48.00,
                },
            },
            {
                "sequence": 2,
                "signal_id": "S2",
                "signal_bar_time": "2026-08-20 14:05:00",
                "candidate_time": "2026-08-20 14:09:57",
                "direction": "SELL",
                "route": "EMA_L23",
                "planned_entry": 4424.90,
                "planned_sl": 4429.60,
                "planned_tp1": 4420.60,
                "ai_status": "allow",
                "ai_time": "2026-08-20 14:09:59",
                "ai_confidence": 69,
                "outcome": "filled",
                "outcome_time": "2026-08-20 14:10:03",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "731556903",
                "entry_facts": {
                    "signal_route": "EMA_L23",
                    "direction": "SELL",
                    "context_loaded": True,
                    "ema_attempt": 3,
                    "ema_distance_usd": 0.75,
                    "pattern": "bearish engulfing",
                    "initial_sl": 4429.60,
                    "tp1_price": 4420.60,
                    "tp2_price": 4416.30,
                    "ai_allow_trade": True,
                    "initial_risk": 47.00,
                },
            },
            {
                "sequence": 3,
                "signal_id": "S3",
                "signal_bar_time": "2026-08-20 16:40:00",
                "candidate_time": "2026-08-20 16:44:57",
                "direction": "BUY",
                "route": "FIB_PA",
                "planned_entry": 4405.20,
                "planned_sl": 4400.40,
                "planned_tp1": 4409.60,
                "ai_status": "reject",
                "ai_time": "2026-08-20 16:44:59",
                "ai_confidence": 30,
                "ai_reason": "DeepSeek审核拒绝：H4偏多与H1向下区间矛盾，方向证据不足",
                "outcome": "ai_rejected",
                "outcome_time": "2026-08-20 16:45:00",
                "outcome_reason": "DeepSeek审核拒绝，未进入挂单阶段",
                "position_id": "",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "BUY",
                    "context_loaded": True,
                    "fib_retracement": 61.8,
                    "pattern": "pin bar",
                    "initial_sl": 4400.40,
                    "tp1_price": 4409.60,
                    "tp2_price": 4414.00,
                    "ai_allow_trade": False,
                    "initial_risk": 48.00,
                },
            },
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S3",
                    "sequence": 3,
                    "direction": "BUY",
                    "reason_code": "AI拒绝",
                    "reason_text": "DeepSeek审核拒绝",
                    "entry": 4405.20,
                    "sl": 4400.40,
                    "entry_retouched": False,
                    "entry_retouch_time": "",
                    "r30": None,
                    "r60": None,
                    "r_max": None,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": None,
                    "order_unconfirmed": False,
                    "classification": "正常未成交",
                    "hit_0_8": False,
                    "hit_1r": False,
                    "hit_2r": False,
                }
            ]
        },
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {
                "AI拒绝": {
                    "total": 1,
                    "normal": 0,
                    "hit_0_8": 0,
                    "hit_1r": 0,
                    "hit_2r": 0,
                    "miss_high_value": 0,
                    "insufficient": 0,
                }
            },
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_i_payload() -> dict:
    """场景I：靠近收盘的未成交候选，30分钟/1小时观察窗口不足。"""
    return {
        "review_date": "2026-08-21",
        "market_metrics": {
            "open": 4404.10,
            "high": 4432.60,
            "low": 4390.00,
            "close": 4410.30,
            "range_usd": 42.60,
            "net_change_usd": 6.20,
            "net_change_pct": 0.14,
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
                "signal_bar_time": "2026-08-21 03:40:00",
                "candidate_time": "2026-08-21 03:44:57",
                "direction": "BUY",
                "route": "EMA_H23",
                "planned_entry": 4394.80,
                "planned_sl": 4390.20,
                "planned_tp1": 4399.20,
                "ai_status": "allow",
                "ai_time": "2026-08-21 03:44:59",
                "ai_confidence": 78,
                "outcome": "expired",
                "outcome_time": "2026-08-21 03:59:59",
                "outcome_reason": "市场未在有效期内触发入场价，挂单正常到期",
                "position_id": "",
                "entry_facts": {
                    "signal_route": "EMA_H23",
                    "direction": "BUY",
                    "context_loaded": True,
                    "ema_attempt": 2,
                    "ema_distance_usd": 0.55,
                    "pattern": "bullish engulfing",
                    "initial_sl": 4390.20,
                    "tp1_price": 4399.20,
                    "tp2_price": 4403.60,
                    "ai_allow_trade": True,
                    "initial_risk": 46.00,
                },
            }
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S1",
                    "sequence": 1,
                    "direction": "BUY",
                    "reason_code": "挂单到期",
                    "reason_text": "市场未在有效期内触发入场价，挂单正常到期",
                    "entry": 4394.80,
                    "sl": 4390.20,
                    "entry_retouched": True,
                    "entry_retouch_time": "2026-08-21 04:02:40",
                    "r30": 0.62,
                    "r60": 0.85,
                    "r_max": 0.85,
                    "window30_insufficient": True,
                    "window60_insufficient": True,
                    "final_insufficient": False,
                    "first_touch": "0.8R",
                    "order_unconfirmed": False,
                    "classification": "潜在执行型错失",
                    "hit_0_8": True,
                    "hit_1r": False,
                    "hit_2r": False,
                }
            ]
        },
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {
                "挂单到期": {
                    "total": 1,
                    "normal": 0,
                    "hit_0_8": 1,
                    "hit_1r": 0,
                    "hit_2r": 0,
                    "miss_high_value": 0,
                    "insufficient": 0,
                }
            },
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_j_payload() -> dict:
    """场景J：跨日持仓日——1笔昨日开仓今日平仓（跨日结算），
    另1笔今日开仓收盘仍未平（收盘持仓）。"""
    return {
        "review_date": "2026-08-22",
        "market_metrics": {
            "open": 4400.10,
            "high": 4434.80,
            "low": 4375.60,
            "close": 4414.20,
            "range_usd": 59.20,
            "net_change_usd": 14.10,
            "net_change_pct": 0.32,
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
            "loss_count": 0,
            "net_profit": 62.40,
        },
        "actual_trade_rows": [
            {
                "position_id": "732001455",
                "direction": "BUY",
                "status": "closed_cross_day",
                "open_time": "2026.08.21 20:15:02",
                "close_time": "2026.08.22 04:50:00",
                "entry_price": 4391.20,
                "initial_volume": 0.10,
                "initial_sl": 4386.30,
                "initial_risk": 49.00,
                "tp1_price": 4396.00,
                "tp2_price": 4400.80,
                "tp1_done": True,
                "tp2_done": True,
                "runner_done": True,
                "whole_trade_net": 82.40,
                "today_net": 62.40,
                "final_r": 1.68,
                "max_favorable_r": 2.05,
                "close_reason": "DEAL_REASON_TP",
            },
            {
                "position_id": "732118770",
                "direction": "SELL",
                "status": "open_at_day_end",
                "open_time": "2026.08.22 14:30:03",
                "close_time": "",
                "entry_price": 4429.40,
                "initial_volume": 0.10,
                "initial_sl": 4434.20,
                "initial_risk": 48.00,
                "tp1_price": 4425.60,
                "tp2_price": 4421.80,
                "tp1_done": True,
                "tp2_done": False,
                "runner_done": False,
                "whole_trade_net": 18.30,
                "today_net": 18.30,
                "final_r": None,
                "max_favorable_r": 0.55,
                "close_reason": "",
            },
        ],
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026-08-21 20:10:00",
                "candidate_time": "2026-08-21 20:14:57",
                "direction": "BUY",
                "route": "FIB_PA",
                "planned_entry": 4391.20,
                "planned_sl": 4386.30,
                "planned_tp1": 4396.00,
                "ai_status": "allow",
                "ai_time": "2026-08-21 20:14:59",
                "ai_confidence": 80,
                "outcome": "filled",
                "outcome_time": "2026-08-21 20:15:02",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "732001455",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "BUY",
                    "context_loaded": True,
                    "fib_retracement": 61.8,
                    "pattern": "pin bar",
                    "initial_sl": 4386.30,
                    "tp1_price": 4396.00,
                    "tp2_price": 4400.80,
                    "ai_allow_trade": True,
                    "initial_risk": 49.00,
                },
            },
            {
                "sequence": 2,
                "signal_id": "S2",
                "signal_bar_time": "2026-08-22 14:25:00",
                "candidate_time": "2026-08-22 14:29:57",
                "direction": "SELL",
                "route": "FIB_PA",
                "planned_entry": 4429.40,
                "planned_sl": 4434.20,
                "planned_tp1": 4425.60,
                "ai_status": "allow",
                "ai_time": "2026-08-22 14:29:59",
                "ai_confidence": 76,
                "outcome": "filled",
                "outcome_time": "2026-08-22 14:30:03",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "732118770",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "SELL",
                    "context_loaded": True,
                    "fib_retracement": 50.0,
                    "pattern": "engulfing",
                    "initial_sl": 4434.20,
                    "tp1_price": 4425.60,
                    "tp2_price": 4421.80,
                    "ai_allow_trade": True,
                    "initial_risk": 48.00,
                },
            },
        ],
        "unfilled_candidate_review": {"items": []},
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {},
        },
        "strategy_issues": [],
        "program_issues": [],
    }


def _scenario_k_payload() -> dict:
    """场景K：同日混合——2笔实际成交（1盈1亏），
    另有1个挂单到期未触发的未成交候选（正常未成交）。"""
    return {
        "review_date": "2026-08-23",
        "market_metrics": {
            "open": 4403.60,
            "high": 4442.10,
            "low": 4372.40,
            "close": 4399.80,
            "range_usd": 69.70,
            "net_change_usd": -3.80,
            "net_change_pct": -0.09,
        },
        "statistics": {
            "candidate_count": 3,
            "local_reject_count": 0,
            "ai_allow_count": 3,
            "ai_reject_count": 0,
            "ai_error_count": 0,
            "pending_count": 1,
            "trade_count": 2,
            "win_count": 1,
            "loss_count": 1,
            "net_profit": 21.40,
        },
        "actual_trade_rows": [
            {
                "position_id": "732331140",
                "direction": "BUY",
                "status": "closed",
                "open_time": "2026.08.23 06:20:02",
                "close_time": "2026.08.23 09:10:00",
                "entry_price": 4394.10,
                "initial_volume": 0.10,
                "initial_sl": 4388.90,
                "initial_risk": 52.00,
                "tp1_price": 4399.00,
                "tp2_price": 4403.90,
                "tp1_done": True,
                "tp2_done": True,
                "runner_done": True,
                "whole_trade_net": 98.40,
                "today_net": 98.40,
                "final_r": 1.89,
                "max_favorable_r": 2.10,
                "close_reason": "DEAL_REASON_TP",
            },
            {
                "position_id": "732410277",
                "direction": "SELL",
                "status": "closed",
                "open_time": "2026.08.23 13:45:03",
                "close_time": "2026.08.23 14:35:00",
                "entry_price": 4426.30,
                "initial_volume": 0.10,
                "initial_sl": 4431.10,
                "initial_risk": 48.00,
                "tp1_price": 4422.00,
                "tp2_price": 4417.70,
                "tp1_done": False,
                "tp2_done": False,
                "runner_done": False,
                "whole_trade_net": -77.00,
                "today_net": -77.00,
                "final_r": -1.60,
                "max_favorable_r": 0.35,
                "close_reason": "DEAL_REASON_SL",
            },
        ],
        "candidate_rows": [
            {
                "sequence": 1,
                "signal_id": "S1",
                "signal_bar_time": "2026-08-23 06:15:00",
                "candidate_time": "2026-08-23 06:19:57",
                "direction": "BUY",
                "route": "FIB_PA",
                "planned_entry": 4394.10,
                "planned_sl": 4388.90,
                "planned_tp1": 4399.00,
                "ai_status": "allow",
                "ai_time": "2026-08-23 06:19:59",
                "ai_confidence": 81,
                "outcome": "filled",
                "outcome_time": "2026-08-23 06:20:02",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "732331140",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "BUY",
                    "context_loaded": True,
                    "fib_retracement": 61.8,
                    "pattern": "pin bar",
                    "initial_sl": 4388.90,
                    "tp1_price": 4399.00,
                    "tp2_price": 4403.90,
                    "ai_allow_trade": True,
                    "initial_risk": 52.00,
                },
            },
            {
                "sequence": 2,
                "signal_id": "S2",
                "signal_bar_time": "2026-08-23 13:40:00",
                "candidate_time": "2026-08-23 13:44:57",
                "direction": "SELL",
                "route": "EMA_L23",
                "planned_entry": 4426.30,
                "planned_sl": 4431.10,
                "planned_tp1": 4422.00,
                "ai_status": "allow",
                "ai_time": "2026-08-23 13:44:59",
                "ai_confidence": 70,
                "outcome": "filled",
                "outcome_time": "2026-08-23 13:45:03",
                "outcome_reason": "价格触发，挂单成功成交",
                "position_id": "732410277",
                "entry_facts": {
                    "signal_route": "EMA_L23",
                    "direction": "SELL",
                    "context_loaded": True,
                    "ema_attempt": 3,
                    "ema_distance_usd": 0.80,
                    "pattern": "bearish engulfing",
                    "initial_sl": 4431.10,
                    "tp1_price": 4422.00,
                    "tp2_price": 4417.70,
                    "ai_allow_trade": True,
                    "initial_risk": 48.00,
                },
            },
            {
                "sequence": 3,
                "signal_id": "S3",
                "signal_bar_time": "2026-08-23 15:40:00",
                "candidate_time": "2026-08-23 15:44:57",
                "direction": "SELL",
                "route": "FIB_PA",
                "planned_entry": 4430.20,
                "planned_sl": 4434.60,
                "planned_tp1": 4426.80,
                "ai_status": "allow",
                "ai_time": "2026-08-23 15:44:59",
                "ai_confidence": 72,
                "outcome": "expired",
                "outcome_time": "2026-08-23 15:59:59",
                "outcome_reason": "市场未在有效期内触发入场价，挂单正常到期",
                "position_id": "",
                "entry_facts": {
                    "signal_route": "FIB_PA",
                    "direction": "SELL",
                    "context_loaded": True,
                    "fib_retracement": 56.0,
                    "pattern": "engulfing",
                    "initial_sl": 4434.60,
                    "tp1_price": 4426.80,
                    "tp2_price": 4423.40,
                    "ai_allow_trade": True,
                    "initial_risk": 44.00,
                },
            },
        ],
        "unfilled_candidate_review": {
            "items": [
                {
                    "signal_id": "S3",
                    "sequence": 3,
                    "direction": "SELL",
                    "reason_code": "挂单到期",
                    "reason_text": "市场未在有效期内触发入场价，挂单正常到期",
                    "entry": 4430.20,
                    "sl": 4434.60,
                    "entry_retouched": False,
                    "entry_retouch_time": "",
                    "r30": None,
                    "r60": None,
                    "r_max": None,
                    "window30_insufficient": False,
                    "window60_insufficient": False,
                    "final_insufficient": False,
                    "first_touch": None,
                    "order_unconfirmed": False,
                    "classification": "正常未成交",
                    "hit_0_8": False,
                    "hit_1r": False,
                    "hit_2r": False,
                }
            ]
        },
        "missed_candidate_summary": {
            "validation_met": False,
            "by_reason_code": {
                "挂单到期": {
                    "total": 1,
                    "normal": 1,
                    "hit_0_8": 0,
                    "hit_1r": 0,
                    "hit_2r": 0,
                    "miss_high_value": 0,
                    "insufficient": 0,
                }
            },
        },
        "strategy_issues": [],
        "program_issues": [],
    }


SCENARIOS = [
    (
        "Scenario_A_Two_Filled",
        "2026-08-13",
        _scenario_a_payload(),
        "A｜有成交日：2个候选全部成交（1盈1亏）",
        _response(
            "2026-08-13",
            "今日2笔成交1盈1亏，盈利单沿趋势运行并完成TP1/TP2退出，"
            "亏损单止损正常触发；策略过滤与执行环节暂未发现明确异常。",
            "AI结论：样本量仍不足以修改EA，继续按现有规则运行。",
        ),
    ),
    (
        "Scenario_B_One_Unfilled_SL_First",
        "2026-08-14",
        _scenario_b_payload(),
        "B｜未成交候选：重新触及Entry后先触SL（正常未成交）",
        _response(
            "2026-08-14",
            "今日1个SELL正式候选未成交，经事后走势验证判定为正常未成交，"
            "不属于有效机会错失。本次未成交暂未发现策略或程序异常，"
            "也没有证据支持调整当前挂单执行规则。",
            "AI结论：未成交候选按虚拟成交路径判定正常，暂未发现需要调整执行机制的证据。",
        ),
    ),
    (
        "Scenario_C_Never_Retouched_No_Entry",
        "2026-08-15",
        _scenario_c_payload(),
        "C｜未成交候选：从未重新触及Entry（R 显示 —）",
        _response(
            "2026-08-15",
            "今日1个SELL正式候选未成交，失效后价格始终未重新触及原入场价，"
            "未形成虚拟成交，判定为正常未成交、后续仍无入场机会。"
            "本次未成交暂未发现策略或程序异常。",
            "AI结论：未形成可成交机会，暂未发现需要调整执行机制的证据。",
        ),
    ),
    (
        "Scenario_D_HighValue_Miss_Validation_Met",
        "2026-08-16",
        _scenario_d_payload(),
        "D｜高价值执行型错失：先触2R + 同类问题累计达到验证条件",
        _response(
            "2026-08-16",
            "今日1个SELL正式候选因挂单距离不足未成交，失效后重新触及原入场价"
            "并先触及2R目标，判定为高价值执行型错失。同类「挂单距离不足」累计"
            "已达5次（最近10个交易日），其中3次判定为执行型错失、3次后续达到2R，"
            "问题具有重复性，主要集中在订单执行环节。当前未发现Fib/PA/趋势等策略过滤异常。",
            "同类执行型错失已重复出现，样本量达到专项验证条件，建议人工确认"
            "是否启动挂单执行机制专项验证。",
        ),
    ),
    (
        "Scenario_E_Path_Order_Unconfirmed",
        "2026-08-17",
        _scenario_e_payload(),
        "E｜同根K线顺序无法确认：无Tick → 路径顺序无法确认（无法判断）",
        _response(
            "2026-08-17",
            "今日1个SELL正式候选未成交，失效后重新触及原入场价，但原止损位与"
            "目标位在同一根K线内被触及且先后顺序无法确认（当前行情快照无Tick数据），"
            "按规则标记为无法判断，暂不作为机会错失样本。",
            "路径顺序无法确认属于数据分辨率限制，不构成策略或程序异常证据，"
            "继续按现有规则运行。",
        ),
    ),
    (
        "Scenario_F_No_Candidates_No_Trades",
        "2026-08-18",
        _scenario_f_payload(),
        "F｜空仓日：无候选、无成交",
        _response(
            "2026-08-18",
            "今日未形成正式候选，全天没有实际成交；暂未发现策略或程序异常。",
            "空仓日无需人工干预，继续按现有规则运行。",
        ),
    ),
    (
        "Scenario_G_Wait_Missed_Distance_Miss",
        "2026-08-19",
        _scenario_g_payload(),
        "G｜wait_missed：价格越过Entry但挂单距离不足 → 先触1R（明显执行型错失）",
        _response(
            "2026-08-19",
            "今日1个SELL正式候选因挂单距离不足未成交；价格已越过原入场位，"
            "虚拟成交后先触及1R目标，判定为明显执行型错失。同类问题首次出现，"
            "样本量不足，暂不修改执行规则。",
            "建议继续观察同类挂单距离不足问题，累计达到验证条件后再由人工决定是否优化。",
        ),
    ),
    (
        "Scenario_H_Mixed_With_AI_Reject",
        "2026-08-20",
        _scenario_h_payload(),
        "H｜混合日：2笔成交（1盈1亏）+ 1个AI拒绝候选",
        _response(
            "2026-08-20",
            "今日2笔成交1盈1亏；1个候选被DeepSeek拒绝（H4/H1方向矛盾），"
            "属正常过滤结果，不计入执行错失。当前未发现策略或程序异常。",
            "AI拒绝样本继续保留观察，暂无需调整。",
        ),
    ),
    (
        "Scenario_I_Window_Insufficient",
        "2026-08-21",
        _scenario_i_payload(),
        "I｜观察窗口不足：候选靠近当日收盘（30分钟/1小时不足）",
        _response(
            "2026-08-21",
            "今日1个BUY正式候选未成交，失效后重新触及原入场价并先触及0.8R目标，"
            "判定为潜在执行型错失；但候选靠近当日收盘，30分钟/1小时观察窗口不足，"
            "样本不进入对应窗口的汇总统计。",
            "观察窗口不足样本保留实际数据，待完整窗口样本累计后再评估。",
        ),
    ),
    (
        "Scenario_J_Cross_Day_Open_Position",
        "2026-08-22",
        _scenario_j_payload(),
        "J｜跨日持仓：1笔跨日结算 + 1笔收盘未平（收盘持仓）",
        _response(
            "2026-08-22",
            "今日1笔昨日开仓的BUY完成跨日结算（今日实现+62.40 USD，整笔+82.40 USD）；"
            "另1笔SELL今日开仓、收盘仍未平仓，当前浮盈+18.30 USD，"
            "最终结果以次日结算为准。当前未发现策略或程序异常。",
            "收盘持仓的盈亏不计入今日已实现净盈亏，继续按现有规则运行。",
        ),
    ),
    (
        "Scenario_K_Filled_Plus_Normal_Unfilled",
        "2026-08-23",
        _scenario_k_payload(),
        "K｜同日混合：2笔成交（1盈1亏）+ 1个挂单到期未触发（正常未成交）",
        _response(
            "2026-08-23",
            "今日2笔成交1盈1亏；另有1个SELL正式候选挂单到期未触发，"
            "失效后未再触及入场价，判定为正常未成交、不属于机会错失。"
            "当前未发现策略或程序异常。",
            "未成交候选按虚拟成交路径判定正常，样本量不足以修改EA，继续按现有规则运行。",
        ),
    ),
]


def _write_index() -> None:
    cards = []
    for name, day, _payload, title, _resp in SCENARIOS:
        main_link = f"{name}/Main_Card_{day}.html"
        cand_link = f"{name}/Candidate_Card_{day}.html"
        cards.append(
            f"<tr><td>{title}</td>"
            f"<td><a href='{main_link}' target='_blank'>主卡</a></td>"
            f"<td><a href='{cand_link}' target='_blank'>候选卡</a></td></tr>"
        )
    html = (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        "<title>每日复盘双卡预览｜全场景</title>"
        "<style>"
        "body{font-family:'Microsoft YaHei',sans-serif;margin:32px;background:#f5f6f8;}"
        "h1{font-size:22px;}table{border-collapse:collapse;width:100%;background:#fff;"
        "box-shadow:0 1px 4px rgba(0,0,0,.12);}"
        "th,td{border:1px solid #e2e4e8;padding:10px 14px;text-align:left;}"
        "th{background:#f0f2f5;}a{color:#3370ff;text-decoration:none;}"
        "</style></head><body>"
        "<h1>每日复盘双卡预览｜全场景</h1>"
        "<p>每个场景包含「主卡（每日复盘）」与「候选卡（正式候选订单明细）」两张卡片。</p>"
        "<table><tr><th>场景</th><th>主卡</th><th>候选卡</th></tr>"
        + "".join(cards)
        + "</table></body></html>"
    )
    (OUTPUT_ROOT / "index.html").write_text(html, encoding="utf-8")
    print(f"[Index] 已生成: {OUTPUT_ROOT / 'index.html'}")


def main() -> int:
    for name, day, payload, _title, response in SCENARIOS:
        write_scenario(name, day, payload, response)
    _write_index()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
