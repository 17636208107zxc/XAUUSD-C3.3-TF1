"""周度复盘：日报看单笔，周报看重复模式。

数据口径：
- EA / Parallel AI / 人工 分别统计，不再混成"总开单"。
- R 从每一笔真实交易重新汇总；缺失显示"数据不足"，不用 0 代替。
- V14 / V20 按平仓时间与 V20 Baseline 分开统计。
- 用户可见内容全部中文大白话。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# V20 正式 Baseline（阶段7切换完成时间）
V20_BASELINE = datetime(2026, 8, 21, 10, 1, 11)

EA_MAGIC = "2026072901"
AI_MAGIC = "2026072902"
E2E_MAGIC = "2026072903"

# Gate 中文名（与 parallel_ai_calculator 保持一致，编号仅作辅助）
GATE_CN = {
    "G01": "交易环境",
    "G02": "本AI自身敞口",
    "G03": "趋势结构",
    "G04": "推动幅度",
    "G05": "回调上下文",
    "G06": "入场路径",
    "G07": "信号K长度",
    "G08": "止损距离",
    "G09": "结构目标空间",
    "G10": "点差",
}

CLOSE_REASON_CN = {
    "DEAL_REASON_CLIENT": "客户端平仓",
    "DEAL_REASON_SL": "触及止损平仓",
    "DEAL_REASON_TP": "触及止盈平仓",
    "DEAL_REASON_SO": "强制平仓",
    "AI exit": "AI主动平仓",
}

ROUTE_CN = {
    "FIB_PA": "Fib + PA",
    "EMA_H23": "EMA H2/H3",
    "EMA_L23": "EMA L2/L3",
    "BOTH": "Fib + EMA 双路径",
}

MANUAL_NOTE = "（人工目前有数量/胜负/净盈亏统计，但部分订单身份和R数据尚未完成重建，仅供参考，不参与正式优劣判断）"


def _num(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number or number in (float("inf"), float("-inf")):
        return "—"
    return f"{number:.{digits}f}"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_time(text: Any) -> datetime | None:
    if text is None:
        return None
    value = str(text).strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            continue
    return None


def _version_of(close_text: Any) -> str:
    """按平仓时间判断 V14 / V20。无法判断时默认归 V14 并标记。"""
    t = _parse_time(close_text)
    if t is None:
        return "V14"
    return "V20" if t >= V20_BASELINE else "V14"


def _group_of_magic(magic: Any) -> str | None:
    value = str(magic or "").strip()
    if value == AI_MAGIC:
        return "ai"
    if value == E2E_MAGIC:
        return None
    if value == EA_MAGIC or value == "":
        return "ea"
    if value == "0":
        return "unknown"
    return "manual"


def _close_reason_cn(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return "待确认"
    if text in CLOSE_REASON_CN:
        return CLOSE_REASON_CN[text]
    if text.startswith("DEAL_REASON_"):
        return text.replace("DEAL_REASON_", "")
    return text


def _route_of(trade: dict[str, Any], candidates_by_pos: dict[str, dict[str, Any]]) -> str:
    pos = str(trade.get("position_id") or "").strip()
    if pos and pos in candidates_by_pos:
        route = str(candidates_by_pos[pos].get("route") or "").strip()
        if route:
            return route
    route = str(trade.get("route") or "").strip()
    return route if route else "未知"


def last_completed_week(now_beijing: datetime) -> tuple[str, str]:
    """返回最近一个已完成交易周的 (周一, 周五)。"""
    today = now_beijing.date()
    monday_this_week = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday = monday_this_week
    else:
        monday = monday_this_week - timedelta(days=7)
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


def _read_daily_data(root: Path, day: str) -> dict[str, Any] | None:
    path = root / "Daily_Review" / day / f"Daily_Data_{day}.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_daily_review(root: Path, day: str) -> dict[str, Any] | None:
    path = root / "Daily_Review" / day / f"Daily_Review_{day}.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _empty_group_stats() -> dict[str, Any]:
    return {
        "open_count": 0,
        "closed_count": 0,
        "cross_day_count": 0,
        "still_open": 0,
        "win": 0,
        "loss": 0,
        "breakeven": 0,
        "net": 0.0,
        "total_r": 0.0,
        "r_samples": 0,
        "avg_r": None,
        "win_rate": None,
        "profit_factor": None,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "by_direction": {},
        "by_route": {},
        "by_version": {},
        "loss_mfe_buckets": {
            "开仓后最大浮盈<0.2R": 0,
            "开仓后最大浮盈0.2~0.5R": 0,
            "开仓后最大浮盈0.5~0.8R": 0,
            "开仓后最大浮盈>=0.8R": 0,
            "数据不足": 0,
        },
        "win_retrace": [],
        "trades": [],
    }


def _bucket_mfe(mfe: float | None) -> str:
    if mfe is None:
        return "数据不足"
    if mfe < 0.2:
        return "开仓后最大浮盈<0.2R"
    if mfe < 0.5:
        return "开仓后最大浮盈0.2~0.5R"
    if mfe < 0.8:
        return "开仓后最大浮盈0.5~0.8R"
    return "开仓后最大浮盈>=0.8R"


def _build_group_stats(trades: list[dict[str, Any]], candidates_by_pos: dict[str, dict[str, Any]]) -> dict[str, Any]:
    st = _empty_group_stats()
    for trade in trades:
        status = str(trade.get("status") or "")
        net = _safe_float(trade.get("whole_trade_net", trade.get("net_profit", trade.get("today_net"))))
        raw_r = trade.get("final_r")
        try:
            r = float(raw_r) if raw_r not in (None, "") else None
        except (TypeError, ValueError):
            r = None
        if r is None and net != 0:
            risk = _safe_float(trade.get("initial_risk"))
            if risk > 0:
                r = net / risk
        close_text = str(trade.get("close_time") or "")
        is_open = status == "open_at_day_end" or not close_text

        direction = str(trade.get("direction") or "未知").upper()
        route = _route_of(trade, candidates_by_pos)
        version = _version_of(close_text)
        close_cn = _close_reason_cn(trade.get("close_reason"))
        mfe = trade.get("mfe_r")
        mae = trade.get("mae_r")
        try:
            mfe_v = float(mfe) if mfe not in (None, "") else None
        except (TypeError, ValueError):
            mfe_v = None
        try:
            mae_v = float(mae) if mae not in (None, "") else None
        except (TypeError, ValueError):
            mae_v = None

        if is_open:
            st["open_count"] += 1
            st["still_open"] += 1
        else:
            st["closed_count"] += 1
            if "cross_day" in status:
                st["cross_day_count"] += 1
            st["net"] += net
            if r is not None:
                st["total_r"] += r
                st["r_samples"] += 1
            if net > 0:
                st["win"] += 1
                st["gross_profit"] += net
            elif net < 0:
                st["loss"] += 1
                st["gross_loss"] += -net
            else:
                st["breakeven"] += 1

        # 方向
        d = st["by_direction"].setdefault(
            direction,
            {"count": 0, "win": 0, "loss": 0, "net": 0.0, "total_r": 0.0, "r_samples": 0, "avg_r": None},
        )
        d["count"] += 1
        if not is_open:
            d["net"] += net
            if r is not None:
                d["total_r"] += r
                d["r_samples"] += 1
            if net > 0:
                d["win"] += 1
            elif net < 0:
                d["loss"] += 1

        # 路径
        rk = ROUTE_CN.get(route, route)
        rt = st["by_route"].setdefault(
            rk,
            {"count": 0, "win": 0, "loss": 0, "net": 0.0, "total_r": 0.0, "r_samples": 0, "avg_r": None},
        )
        rt["count"] += 1
        if not is_open:
            rt["net"] += net
            if r is not None:
                rt["total_r"] += r
                rt["r_samples"] += 1
            if net > 0:
                rt["win"] += 1
            elif net < 0:
                rt["loss"] += 1

        # 版本
        v = st["by_version"].setdefault(
            version,
            {"count": 0, "win": 0, "loss": 0, "net": 0.0, "total_r": 0.0, "r_samples": 0, "avg_r": None},
        )
        v["count"] += 1
        if not is_open:
            v["net"] += net
            if r is not None:
                v["total_r"] += r
                v["r_samples"] += 1
            if net > 0:
                v["win"] += 1
            elif net < 0:
                v["loss"] += 1

        # 亏损单 MFE 分布 / 盈利单回吐
        if not is_open:
            if net < 0:
                st["loss_mfe_buckets"][_bucket_mfe(mfe_v)] += 1
            elif net > 0 and mfe_v is not None:
                st["win_retrace"].append(
                    {"mfe_r": round(mfe_v, 2), "final_r": round(r, 2) if r is not None else None}
                )

        st["trades"].append(
            {
                "position_id": str(trade.get("position_id") or ""),
                "direction": direction,
                "route": rk,
                "entry": _safe_float(trade.get("entry_price")),
                "sl": _safe_float(trade.get("initial_sl")),
                "net": round(net, 2),
                "final_r": round(r, 2) if r is not None else None,
                "mfe_r": round(mfe_v, 2) if mfe_v is not None else None,
                "mae_r": round(mae_v, 2) if mae_v is not None else None,
                "close_reason": close_cn,
                "version": version,
                "open_time": str(trade.get("open_time") or ""),
                "close_time": close_text,
                "status": status,
            }
        )

    # 汇总
    total = st["win"] + st["loss"] + st["breakeven"]
    st["win_rate"] = round(st["win"] / total * 100.0, 2) if total else None
    if st["gross_loss"] > 0:
        st["profit_factor"] = round(st["gross_profit"] / st["gross_loss"], 2)
    elif st["gross_profit"] > 0:
        st["profit_factor"] = "∞"
    else:
        st["profit_factor"] = None
    st["avg_win"] = round(st["gross_profit"] / st["win"], 2) if st["win"] else 0.0
    st["avg_loss"] = round(-st["gross_loss"] / st["loss"], 2) if st["loss"] else 0.0
    st["avg_r"] = round(st["total_r"] / st["r_samples"], 2) if st["r_samples"] else None
    for bucket in st["by_direction"].values():
        bucket["avg_r"] = round(bucket["total_r"] / bucket["r_samples"], 2) if bucket["r_samples"] else None
    for bucket in st["by_route"].values():
        bucket["avg_r"] = round(bucket["total_r"] / bucket["r_samples"], 2) if bucket["r_samples"] else None
    for bucket in st["by_version"].values():
        bucket["avg_r"] = round(bucket["total_r"] / bucket["r_samples"], 2) if bucket["r_samples"] else None
    return st


def _translate_block_reason(reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return "未知原因"
    return text


def _translate_program_issue(item: Any) -> str:
    """把程序问题里的英文事件名翻译成人话。"""
    text = str(item or "").strip()
    if "Lark通知状态未知" in text:
        return "有1条Lark通知送达状态未知（历史发送回执缺失）。"
    if "LOCAL_ENTRY_DISAGREEMENT" in text:
        return "有1条EA与Parallel AI判断不同的Lark通知，送达状态暂未确认。"
    if "Lark通知状态不确定" in text:
        if "LOCAL_ENTRY_DISAGREEMENT" in text:
            return "有1条EA与Parallel AI判断不同的Lark通知，送达状态暂未确认。"
        return "有1条Lark通知送达状态暂未确认。"
    if "Uncertain" in text and "Lark" in text:
        return "有1条Lark通知送达状态暂未确认。"
    return text


def _select_typical_trades(ea_stats: dict[str, Any]) -> list[dict[str, Any]]:
    """从 EA 已结算交易里选最多 3 笔代表性交易（不强行凑数）。"""
    trades = ea_stats.get("trades") or []
    closed = [t for t in trades if t.get("final_r") is not None]
    losses = [t for t in closed if t.get("final_r", 0) < 0]
    wins = [t for t in closed if t.get("final_r", 0) > 0]
    selected: list[dict[str, Any]] = []

    # A. 最典型的正常策略亏损：曾有过不错浮盈但最终止损（趋势延续不足）
    normal_losses = [t for t in losses if (t.get("mfe_r") or 0) >= 0.3]
    if normal_losses:
        picked = max(normal_losses, key=lambda t: (t.get("mfe_r") or 0))
        selected.append(
            {
                "type": "最典型的正常策略亏损",
                "position_id": picked["position_id"],
                "direction": picked["direction"],
                "route": picked["route"],
                "final_r": picked["final_r"],
                "mfe_r": picked.get("mfe_r"),
                "why": "开仓后曾达到明显浮盈但最终止损，属于趋势延续不足而非入场立即失败",
            }
        )

    # B. 最值得继续研究的问题单：入场后最大浮盈不足 0.2R 直接止损
    problem_trades = [t for t in losses if (t.get("mfe_r") or 0) < 0.2]
    if problem_trades:
        picked = min(problem_trades, key=lambda t: (t.get("mfe_r") or 0))
        selected.append(
            {
                "type": "最值得继续研究的问题单",
                "position_id": picked["position_id"],
                "direction": picked["direction"],
                "route": picked["route"],
                "final_r": picked["final_r"],
                "mfe_r": picked.get("mfe_r"),
                "why": "入场后几乎未获得顺向空间即止损，需观察是否入场时机或趋势延续性问题",
            }
        )

    # C. 最有代表性的盈利单
    if wins:
        picked = max(wins, key=lambda t: (t.get("final_r") or 0))
        selected.append(
            {
                "type": "最有代表性的盈利单",
                "position_id": picked["position_id"],
                "direction": picked["direction"],
                "route": picked["route"],
                "final_r": picked["final_r"],
                "mfe_r": picked.get("mfe_r"),
                "why": "本周盈利最多的交易，可对照看成功交易的入场与持有特征",
            }
        )

    return selected[:3]


def aggregate_weekly_facts(
    root: Path,
    monday: str,
    friday: str,
    incomplete: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """聚合整周客观事实，供周报渲染与 DeepSeek 冻结事实包使用。"""
    monday_date = date.fromisoformat(monday)
    days: list[str] = []
    market_opens: list[float] = []
    market_closes: list[float] = []
    market_highs: list[float] = []
    market_lows: list[float] = []

    all_trades: list[dict[str, Any]] = []
    seen_positions: dict[str, dict[str, Any]] = {}
    all_candidates: list[dict[str, Any]] = []
    all_ai_candidates: list[dict[str, Any]] = []
    unfilled_items: list[dict[str, Any]] = []
    issue_lists: dict[str, list[str]] = {"strategy": [], "execution": [], "program": []}
    daily_context: list[dict[str, str]] = []
    ai_allow_filled: list[dict[str, Any]] = []
    ai_reject_candidates: list[dict[str, Any]] = []
    block_gate_counter: dict[str, int] = {}
    manual_day_agg: dict[str, float] = {
        "count": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0,
    }
    unknown_day_agg: dict[str, float] = {
        "count": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0,
    }

    for offset in range(5):
        day = (monday_date + timedelta(days=offset)).isoformat()
        data = _read_daily_data(root, day)
        if data is None:
            continue
        days.append(day)

        metrics = data.get("market_metrics") or {}
        for bucket, key in (
            (market_opens, "open"),
            (market_closes, "close"),
            (market_highs, "high"),
            (market_lows, "low"),
        ):
            value = metrics.get(key)
            if value is not None:
                bucket.append(_safe_float(value))

        # 交易（跨日优先保留“已平仓”的那份，避免把跨日单算成持仓）
        for row in data.get("actual_trade_rows") or []:
            pid = str(row.get("position_id") or "").strip()
            if not pid:
                continue
            existing = seen_positions.get(pid)
            if existing is None:
                seen_positions[pid] = dict(row)
            elif not str(existing.get("close_time") or "") and str(row.get("close_time") or ""):
                seen_positions[pid] = dict(row)

        # 候选
        for row in data.get("candidate_rows") or []:
            all_candidates.append(dict(row))
        for row in data.get("ai_candidate_rows") or []:
            all_ai_candidates.append(dict(row))
        for item in (data.get("unfilled_candidate_review") or {}).get("items") or []:
            unfilled_items.append(dict(item))

        # 问题
        for field, target in (
            ("strategy_issues", "strategy"),
            ("execution_issues", "execution"),
            ("program_issues", "program"),
        ):
            for item in data.get(field) or []:
                text = _translate_program_issue(item) if field == "program_issues" else str(item)
                if text and text not in issue_lists[target]:
                    issue_lists[target].append(text)

        # 每日上下文
        review = _read_daily_review(root, day) or {}
        day_rows = data.get("actual_trade_rows") or []
        ea_day = [
            r for r in day_rows if _group_of_magic(r.get("magic")) == "ea"
        ]
        ea_closed_day = [r for r in ea_day if str(r.get("status") or "").startswith("closed")]
        ea_day_net = sum(
            _safe_float(r.get("whole_trade_net", r.get("net_profit", r.get("today_net"))))
            for r in ea_closed_day
        )
        ea_day_win = sum(1 for r in ea_closed_day if _safe_float(r.get("whole_trade_net", r.get("net_profit", r.get("today_net")))) > 0)
        ea_day_loss = sum(1 for r in ea_closed_day if _safe_float(r.get("whole_trade_net", r.get("net_profit", r.get("today_net")))) < 0)
        ea_day_buy = sum(1 for r in ea_closed_day if str(r.get("direction") or "").upper() == "BUY")
        ea_day_sell = sum(1 for r in ea_closed_day if str(r.get("direction") or "").upper() == "SELL")
        ea_day_r_total = 0.0
        for r in ea_closed_day:
            raw_r = r.get("final_r")
            try:
                rv = float(raw_r) if raw_r not in (None, "") else None
            except (TypeError, ValueError):
                rv = None
            if rv is None:
                net_r = _safe_float(r.get("whole_trade_net", r.get("net_profit", r.get("today_net"))))
                risk_r = _safe_float(r.get("initial_risk"))
                rv = net_r / risk_r if risk_r > 0 else None
            if rv is not None:
                ea_day_r_total += rv
        daily_context.append(
            {
                "date": day,
                "ea_trade_count": len(ea_closed_day),
                "ea_win": ea_day_win,
                "ea_loss": ea_day_loss,
                "ea_buy": ea_day_buy,
                "ea_sell": ea_day_sell,
                "ea_net": round(ea_day_net, 2),
                "ea_total_r": round(ea_day_r_total, 2),
                "market_regime": str(review.get("market_regime", "")),
                "conclusion": str(review.get("conclusion_summary", "")),
            }
        )

        # AI 允许/拒绝候选
        for row in data.get("candidate_rows") or []:
            ai_status = str(row.get("ai_status") or "")
            outcome = str(row.get("outcome") or "")
            if ai_status == "allow":
                ai_allow_filled.append(
                    {
                        "signal_id": str(row.get("signal_id") or ""),
                        "direction": str(row.get("direction") or ""),
                        "route": str(row.get("route") or ""),
                        "outcome": outcome,
                        "position_id": str(row.get("position_id") or ""),
                    }
                )
            elif ai_status == "reject":
                ai_reject_candidates.append(
                    {
                        "signal_id": str(row.get("signal_id") or ""),
                        "direction": str(row.get("direction") or ""),
                        "route": str(row.get("route") or ""),
                    }
                )

        # Python 确定性规则拦截（Parallel AI 候选）
        for row in data.get("ai_candidate_rows") or []:
            for gate in row.get("block_gate_ids") or []:
                key = str(gate or "").strip().upper()
                if not key:
                    continue
                block_gate_counter[key] = block_gate_counter.get(key, 0) + 1

        # 人工交易（生命周期 CSV 不含人工，只有每日 trade_groups 有）
        day_groups = data.get("trade_groups") or {}
        manual_entry = day_groups.get("manual") or {}
        manual_day_agg["count"] += _int(manual_entry.get("count"))
        manual_day_agg["win"] += _int(manual_entry.get("win"))
        manual_day_agg["loss"] += _int(manual_entry.get("loss"))
        manual_day_agg["net"] += _safe_float(manual_entry.get("net"))
        manual_day_agg["r_total"] += _safe_float(manual_entry.get("r_total"))
        manual_day_agg["r_samples"] += _int(manual_entry.get("r_samples"))
        unknown_entry = day_groups.get("unknown") or {}
        unknown_day_agg["count"] += _int(unknown_entry.get("count"))
        unknown_day_agg["win"] += _int(unknown_entry.get("win"))
        unknown_day_agg["loss"] += _int(unknown_entry.get("loss"))
        unknown_day_agg["net"] += _safe_float(unknown_entry.get("net"))
        unknown_day_agg["r_total"] += _safe_float(unknown_entry.get("r_total"))
        unknown_day_agg["r_samples"] += _int(unknown_entry.get("r_samples"))

    # 补齐缺失交易日，保证每天一行（含尚未有结算数据的 08-21 等）
    existing_days = {str(ctx.get("date")): ctx for ctx in daily_context}
    for offset in range(5):
        day = (monday_date + timedelta(days=offset)).isoformat()
        if day in existing_days:
            continue
        daily_context.append(
            {
                "date": day,
                "ea_trade_count": 0,
                "ea_win": 0,
                "ea_loss": 0,
                "ea_buy": 0,
                "ea_sell": 0,
                "ea_net": 0.0,
                "ea_total_r": 0.0,
                "market_regime": "",
                "conclusion": "",
            }
        )
    daily_context.sort(key=lambda ctx: str(ctx.get("date") or ""))

    # 按 Magic 分组
    all_trades = list(seen_positions.values())
    candidates_by_pos = {
        str(c.get("position_id") or ""): c for c in all_candidates if c.get("position_id")
    }
    groups_raw: dict[str, list[dict[str, Any]]] = {
        "ea": [], "ai": [], "manual": [], "unknown": [],
    }
    for trade in all_trades:
        group = _group_of_magic(trade.get("magic"))
        if group is None:
            continue
        groups_raw[group].append(trade)

    groups = {
        "ea": _build_group_stats(groups_raw["ea"], candidates_by_pos),
        "ai": _build_group_stats(groups_raw["ai"], candidates_by_pos),
        "manual": _build_group_stats(groups_raw["manual"], candidates_by_pos),
        "unknown": _build_group_stats(groups_raw["unknown"], candidates_by_pos),
    }
    # 用每日 trade_groups 补齐人工统计（生命周期无人工数据）
    manual_stats = groups["manual"]
    manual_stats["closed_count"] = _int(manual_day_agg["count"])
    manual_stats["win"] = _int(manual_day_agg["win"])
    manual_stats["loss"] = _int(manual_day_agg["loss"])
    manual_stats["net"] = round(manual_day_agg["net"], 2)
    manual_stats["total_r"] = round(manual_day_agg["r_total"], 2)
    manual_stats["r_samples"] = _int(manual_day_agg["r_samples"])
    manual_stats["avg_r"] = (
        round(manual_day_agg["r_total"] / manual_day_agg["r_samples"], 2)
        if manual_day_agg["r_samples"]
        else None
    )
    unknown_stats = groups["unknown"]
    unknown_stats["closed_count"] = _int(unknown_day_agg["count"])
    unknown_stats["win"] = _int(unknown_day_agg["win"])
    unknown_stats["loss"] = _int(unknown_day_agg["loss"])
    unknown_stats["net"] = round(unknown_day_agg["net"], 2)
    unknown_stats["total_r"] = round(unknown_day_agg["r_total"], 2)
    unknown_stats["r_samples"] = _int(unknown_day_agg["r_samples"])
    unknown_stats["avg_r"] = (
        round(unknown_day_agg["r_total"] / unknown_day_agg["r_samples"], 2)
        if unknown_day_agg["r_samples"]
        else None
    )

    # 行情
    market: dict[str, Any] = {
        "open": market_opens[0] if market_opens else 0.0,
        "close": market_closes[-1] if market_closes else 0.0,
        "high": max(market_highs) if market_highs else 0.0,
        "low": min(market_lows) if market_lows else 0.0,
    }
    market["range_usd"] = round(market["high"] - market["low"], 2)
    market["net_change_usd"] = round(market["close"] - market["open"], 2)
    market["net_change_pct"] = round(
        (market["net_change_usd"] / market["open"] * 100.0) if market["open"] else 0.0, 2
    )

    # 候选汇总
    formal_total = len(all_candidates)
    ai_allow = sum(1 for c in all_candidates if str(c.get("ai_status") or "") == "allow")
    ai_reject = sum(1 for c in all_candidates if str(c.get("ai_status") or "") == "reject")
    filled = sum(1 for c in all_candidates if str(c.get("outcome") or "") == "filled")
    unfilled = formal_total - filled

    # AI 允许后结果
    allowed_filled = [c for c in ai_allow_filled if c["outcome"] == "filled"]
    allowed_win = 0
    allowed_loss = 0
    allowed_r_total = 0.0
    allowed_r_samples = 0
    for c in allowed_filled:
        trade = next(
            (t for t in groups["ea"]["trades"] + groups["ai"]["trades"] if t["position_id"] == c["position_id"]),
            None,
        )
        if trade:
            if trade["final_r"] is not None:
                allowed_r_total += trade["final_r"]
                allowed_r_samples += 1
            if trade["net"] > 0:
                allowed_win += 1
            elif trade["net"] < 0:
                allowed_loss += 1
    allowed_missed = sum(
        1 for c in ai_allow_filled if c["outcome"] != "filled"
    )

    # 未成交候选分类（沿用每日 unfilled_candidate_review）
    unfilled_cls: dict[str, int] = {}
    for item in unfilled_items:
        cls = str(item.get("classification") or str(item.get("judgment_cn") or "无法判断"))
        unfilled_cls[cls] = unfilled_cls.get(cls, 0) + 1

    # AI 拒绝后的事后结果（由候选事后事实计算，不由 DeepSeek 推断）
    ai_rejected_results: dict[str, Any] = {
        "total": len(ai_reject_candidates),
        "effective_filter": 0,
        "missed": 0,
        "unable_to_confirm": 0,
        "details": [],
    }
    unfilled_by_signal = {str(item.get("signal_id") or ""): item for item in unfilled_items}
    for rejected in ai_reject_candidates:
        sid = str(rejected.get("signal_id") or "")
        item = unfilled_by_signal.get(sid)
        classification = "无法判断"
        r_max = None
        hit_1r = None
        if item is not None:
            classification = str(item.get("classification") or str(item.get("judgment_cn") or "无法判断"))
            r_max = item.get("r_max")
            hit_1r = item.get("hit_1r")
        if "错失" in classification or "错过" in classification:
            ai_rejected_results["missed"] += 1
        elif "正常未成交" in classification or "先触SL" in classification or "先触及止损" in classification or "有效过滤" in classification:
            ai_rejected_results["effective_filter"] += 1
        else:
            ai_rejected_results["unable_to_confirm"] += 1
        ai_rejected_results["details"].append(
            {
                "signal_id": sid,
                "direction": str(rejected.get("direction") or ""),
                "route": str(rejected.get("route") or ""),
                "classification": classification,
                "r_max": r_max,
                "hit_1r": hit_1r,
            }
        )

    # 本周典型交易对照（最多 3 笔，不强行凑数）
    typical_trades = _select_typical_trades(groups["ea"])

    # 规则拦截（中文）
    rule_blocks_cn = {
        f"{GATE_CN.get(g, g)}（{g}）": count
        for g, count in sorted(block_gate_counter.items(), key=lambda kv: -kv[1])
    }

    # V14 / V20 版本汇总
    version_split: dict[str, dict[str, Any]] = {}
    for group_name, group_stats in groups.items():
        for ver, bucket in group_stats["by_version"].items():
            vs = version_split.setdefault(
                ver,
                {"ea_count": 0, "ai_count": 0, "manual_count": 0, "net": 0.0, "total_r": 0.0, "r_samples": 0, "avg_r": None},
            )
            vs[f"{group_name}_count"] += bucket["count"]
            vs["net"] += bucket["net"]
            vs["total_r"] += bucket["total_r"]
            vs["r_samples"] += bucket["r_samples"]
    for vs in version_split.values():
        vs["avg_r"] = round(vs["total_r"] / vs["r_samples"], 2) if vs["r_samples"] else None

    # 兼容旧字段（统计 / 三组对照），供外部引用
    statistics = {
        "candidate_count": formal_total,
        "local_reject_count": 0,
        "ai_allow_count": ai_allow,
        "ai_reject_count": ai_reject,
        "ai_error_count": 0,
        "pending_count": 0,
        "trade_count": groups["ea"]["closed_count"] + groups["ea"]["open_count"],
        "win_count": groups["ea"]["win"],
        "loss_count": groups["ea"]["loss"],
        "net_profit": round(groups["ea"]["net"], 2),
        "win_rate": groups["ea"]["win_rate"],
        "profit_factor": groups["ea"]["profit_factor"],
        "average_r": groups["ea"]["avg_r"],
        "gross_profit": round(groups["ea"]["gross_profit"], 2),
        "gross_loss": round(groups["ea"]["gross_loss"], 2),
        "block_reason_counts": {},
    }
    trade_groups = {
        name: {
            "count": st["closed_count"] + st["open_count"],
            "open": st["open_count"],
            "win": st["win"],
            "loss": st["loss"],
            "net": round(st["net"], 2),
            "r_total": round(st["total_r"], 2),
            "r_samples": st["r_samples"],
            "average_r": st["avg_r"],
        }
        for name, st in groups.items()
    }

    facts: dict[str, Any] = {
        "week_start": monday,
        "week_end": friday,
        "incomplete": incomplete,
        "trading_days": len(days),
        "days": days,
        "market": market,
        "groups": groups,
        "candidates": {
            "formal_total": formal_total,
            "ai_allow": ai_allow,
            "ai_reject": ai_reject,
            "filled": filled,
            "unfilled": unfilled,
            "ai_allowed_results": {
                "filled": len(allowed_filled),
                "win": allowed_win,
                "loss": allowed_loss,
                "avg_r": round(allowed_r_total / allowed_r_samples, 2) if allowed_r_samples else None,
                "total_r": round(allowed_r_total, 2),
                "unfilled": allowed_missed,
            },
            "ai_rejected_results": ai_rejected_results,
            "unfilled_classification": unfilled_cls,
        },
        "typical_trades": typical_trades,
        "rule_blocks_cn": rule_blocks_cn,
        "version_split": version_split,
        "daily_context": daily_context,
        "issues": issue_lists,
        "statistics": statistics,
        "trade_groups": trade_groups,
    }
    return facts, days


def _section(title: str, body: str) -> str:
    return f"**{title}**\n{body}".strip()


def _r_text(value: Any) -> str:
    if value is None:
        return "数据不足，暂无法计算"
    return f"{value:+.2f}R" if isinstance(value, (int, float)) else str(value)


def _group_summary_line(label: str, st: dict[str, Any]) -> str:
    open_text = f"（另有持仓 {st['open_count']} 笔）" if st["open_count"] else ""
    breakeven_text = f"/{st['breakeven']}保本" if st["breakeven"] else ""
    return (
        f"{label}：开仓 {st['open_count'] + st['closed_count']} 笔{open_text}｜"
        f"已结算 {st['closed_count']} 笔｜{st['win']}盈{st['loss']}亏"
        f"{breakeven_text}｜"
        f"净盈亏 {_num(st['net'])} USD｜平均 {_r_text(st['avg_r'])}"
    )


def _render_direction(by_direction: dict[str, Any]) -> str:
    lines: list[str] = []
    for direction in ("BUY", "SELL"):
        d = by_direction.get(direction)
        if not d or d["count"] == 0:
            continue
        label = "BUY（做多）" if direction == "BUY" else "SELL（做空）"
        lines.append(
            f"{label}：{d['count']}笔｜{d['win']}盈{d['loss']}亏｜"
            f"净盈亏 {_num(d['net'])} USD｜平均 {_r_text(d['avg_r'])}"
        )
    if not lines:
        return "本周无已结算交易。"
    return "\n".join(lines)


def _render_route(by_route: dict[str, Any]) -> str:
    if not by_route:
        return "本周无已结算交易。"
    lines: list[str] = []
    for route, d in by_route.items():
        lines.append(
            f"{route}：{d['count']}笔｜{d['win']}盈{d['loss']}亏｜"
            f"净盈亏 {_num(d['net'])} USD｜平均 {_r_text(d['avg_r'])}"
        )
    return "\n".join(lines)


def _render_mfe_mae(st: dict[str, Any]) -> str:
    loss_total = sum(st["loss_mfe_buckets"].values())
    if loss_total == 0:
        loss_line = "本周无已结算亏损单。"
    else:
        buckets = "；".join(
            f"{k} {v} 笔" for k, v in st["loss_mfe_buckets"].items() if v
        )
        loss_line = f"亏损单 {loss_total} 笔的开仓后最大浮盈分布：{buckets}。"
    if st["win_retrace"]:
        retrace = "；".join(
            f"最大浮盈 {item['mfe_r']}R / 最终 {_r_text(item['final_r'])}" for item in st["win_retrace"][:5]
        )
        win_line = f"盈利单回吐观察：{retrace}"
    else:
        win_count = int(st.get("win") or 0)
        if win_count > 0:
            win_line = f"本周{win_count}笔盈利EA交易的最大浮盈数据均缺失，暂无法分析盈利后的回吐情况。"
        else:
            win_line = "本周无盈利EA交易。"
    return loss_line + "\n" + win_line


def _render_version_split(ea_stats: dict[str, Any]) -> str:
    """按 V14 / V20 显示 EA 正式交易拆分。"""
    by_version = ea_stats.get("by_version") or {}
    lines: list[str] = []
    for ver in ("V14", "V20"):
        bucket = by_version.get(ver)
        if not bucket or bucket["count"] == 0:
            if ver == "V20":
                lines.append("**V20：** 截至当前暂无已结算正式交易，样本不足，暂不评价策略表现。")
            continue
        r_text = _r_text(bucket["avg_r"])
        total_r_text = f"{_num(bucket['total_r'])}R" if bucket["r_samples"] else "数据不足"
        lines.append(
            f"**{ver}：** {bucket['count']} 笔｜{bucket['win']}盈{bucket['loss']}亏｜"
            f"净盈亏 {_num(bucket['net'])} USD｜总R {total_r_text}｜平均R {r_text}"
        )
    if not lines:
        return "本周暂无已结算正式交易。"
    return "\n".join(lines)


def _render_typical_trades(typical_trades: list[dict[str, Any]]) -> str:
    """本周典型交易对照（最多 3 笔）。"""
    if not typical_trades:
        return "本周数据不足，未选出典型交易对照。"
    lines: list[str] = []
    for idx, trade in enumerate(typical_trades, 1):
        mfe_text = f"｜最大浮盈 {_num(trade.get('mfe_r'))}R" if trade.get("mfe_r") is not None else ""
        lines.append(
            f"{idx}. **{trade['type']}**｜交易#{trade['position_id']}｜"
            f"{'做多' if trade['direction'] == 'BUY' else '做空' if trade['direction'] == 'SELL' else trade['direction']}"
            f"｜{trade['route']}｜最终 {_r_text(trade['final_r'])}{mfe_text}\n"
            f"   选它原因：{trade['why']}。\n"
            f"   能说明什么：当前仅 1 笔，暂不足以据此修改策略，继续观察同类样本。"
        )
    return "\n".join(lines)


def _guard_mfe_claims(text: str, ea_stats: dict[str, Any]) -> str:
    """MFE 数据覆盖率不足时，禁止把部分样本写成“大部分/多数/普遍”。"""
    if not text:
        return text
    buckets = ea_stats.get("loss_mfe_buckets") or {}
    missing = _int(buckets.get("数据不足"))
    has_data = sum(_int(v) for k, v in buckets.items() if k != "数据不足")
    if missing > 0 and has_data > 0:
        if "大部分" in text or "多数" in text or "普遍" in text:
            text = text.replace("大部分亏损单", "有数据的亏损单")
            text = text.replace("大部分", "有数据的样本")
            text = text.replace("多数", "有数据的样本")
            if "其余数据缺失" not in text and "数据缺失" not in text:
                text = text.rstrip("。") + "；其余数据缺失，暂不能判断是否普遍。"
    return text


def _guard_win_mfe_claim(text: str, ea_stats: dict[str, Any]) -> str:
    """盈利单 MFE 数据缺失时，删除 DeepSeek 自行捏造的“盈利单有数据/回吐”表述。"""
    if not text:
        return text
    if (ea_stats.get("win_retrace") or []):
        return text
    kept: list[str] = []
    for sentence in re.split(r"(?<=[。])", text):
        if ("盈利单" in sentence or "盈利交易" in sentence) and (
            "有数据" in sentence or "浮盈" in sentence or "回吐" in sentence
        ):
            continue
        kept.append(sentence)
    return "".join(kept).strip("。；， \n")


def _guard_group_count(text: str, facts: dict[str, Any]) -> str:
    """修正 DeepSeek 叙述里与冻结事实矛盾的三组笔数。"""
    if not text:
        return text
    groups = facts.get("groups") or {}
    ea_n = groups.get("ea", {}).get("closed_count", 0) + groups.get("ea", {}).get("open_count", 0)
    ai_n = groups.get("ai", {}).get("closed_count", 0) + groups.get("ai", {}).get("open_count", 0)
    manual_n = groups.get("manual", {}).get("closed_count", 0) + groups.get("manual", {}).get("open_count", 0)
    # “均为X笔”在 EA≠AI 时改为“分别为X笔和Y笔”
    if ea_n != ai_n:
        text = re.sub(
            rf"均为\s*{ea_n}\s*笔",
            f"分别为 {ea_n} 笔和 {ai_n} 笔",
            text,
        )
    # “AI……EA笔数笔”误写
    if ea_n != ai_n:
        def _fix_ai_count(match: re.Match[str]) -> str:
            return match.group(0).replace(str(ea_n), str(ai_n))
        text = re.sub(rf"AI[^。；\n]*?{ea_n}\s*笔", _fix_ai_count, text)
    return text


def _guard_ai_filter_claims(text: str) -> str:
    """修正 AI 过滤相关叙述里的“多数/大部分”式扩大表述。"""
    if not text:
        return text
    if "AI过滤掉的多数信号未出现大行情" in text:
        text = text.replace(
            "AI过滤掉的多数信号未出现大行情",
            "被过滤信号样本仅 2 个，其中 1 个事后走出大行情，暂不能判断过滤是否普遍有效",
        )
    if "AI过滤掉的多数" in text:
        text = text.replace("AI过滤掉的多数", "被过滤的信号样本较少，暂不能判断是否普遍")
    return text


def _guard_ea_only(text: str) -> str:
    """EA 综合分析里禁止混入 Parallel AI / 人工交易事实。"""
    if not text:
        return text
    if "AI交易" in text or "AI单" in text or "人工交易" in text or "人工单" in text:
        return "本周 EA 最大浮盈/最大浮亏数据覆盖率不足，暂不做跨组比较。"
    return text


def _guard_manual_claims(text: str) -> str:
    """人工组有数据，禁止写成“无有效成交记录”。"""
    if not text:
        return text
    for bad in ("人工无有效成交记录", "人工无成交记录", "人工组无有效", "人工无有效成交", "人工无成交"):
        if bad in text:
            return "人工组目前记录了成交数量和净盈亏，但部分订单身份和 R 数据尚未完成重建，因此仅供参考，不参与正式优劣判断。"
    return text


def _guard_filter_rating(text: str) -> str:
    """删除无样本支撑的过滤规则定性评级。"""
    if not text:
        return text
    for bad in ("整体过滤效果中等", "过滤效果中等", "规则已经得到明显支持", "过滤规则有效", "规则得到稳定支持"):
        if bad in text:
            return "当前没有足够证据评价 AI 拒绝规则整体有效或整体过严，继续累计样本。"
    return text


def _strip_ai_allowed_narrative(text: str) -> str:
    """去掉 DeepSeek 里与客观“AI 允许后结果”重复、且数字可能不一致的叙述句。"""
    if not text:
        return text
    kept: list[str] = []
    for sentence in re.split(r"(?<=[。])", text):
        if "AI允许后成交" in sentence or "AI允许后" in sentence:
            continue
        kept.append(sentence)
    return "".join(kept).strip("。；， \n")


def _strip_supported_rules_claim(text: str) -> str:
    """去掉 DeepSeek 自行给出的“已有规则得到支持/规则有效”结论句。"""
    if not text:
        return text
    kept: list[str] = []
    for sentence in re.split(r"(?<=[。])", text):
        if "已有规则得到支持" in sentence or "规则得到支持" in sentence or "过滤规则有效" in sentence:
            continue
        kept.append(sentence)
    return "".join(kept).strip("。；， \n")


def _guard_risk_control_claim(text: str, facts: dict[str, Any]) -> str:
    """删除无依据的“Parallel AI 单笔风险控制更紧/更好”定性。"""
    if not text:
        return text
    unsupported = (
        "单笔风险控制更紧",
        "风险控制更紧",
        "风险控制更好",
        "风险控制优于",
        "亏损控制稍佳",
        "风险控制稍佳",
        "表现略好",
        "表现稍好",
        "表现略稳",
        "表现稍稳",
        "表现更好",
        "表现更佳",
        "表现优于",
    )
    if any(phrase in text for phrase in unsupported):
        return (
            "Parallel AI本周平均R高于EA，但两组样本数量、交易机会和交易管理方式不同，"
            "目前不能据此判断Parallel AI优于EA或风险控制更好。"
        )
    return text


def _ai_rejected_conclusion(facts: dict[str, Any]) -> str:
    """AI 拒绝结论（确定性文本，由候选事后事实生成）。"""
    rej = facts.get("candidates", {}).get("ai_rejected_results") or {}
    total = _int(rej.get("total"))
    eff = _int(rej.get("effective_filter"))
    missed = _int(rej.get("missed"))
    unable = _int(rej.get("unable_to_confirm"))
    eff_text = f"确认有效过滤{eff}个" if eff > 0 else "目前没有确认属于有效过滤的样本"
    return (
        f"本周AI拒绝样本只有{total}个，{eff_text}，{missed}个属于潜在错过，"
        f"{unable}个无法确认，当前不足以判断AI拒绝是否有效。"
    )


def _filter_rule_observation(facts: dict[str, Any]) -> str:
    """过滤规则值得继续观察（确定性文本，由候选事后事实生成）。"""
    rej = facts.get("candidates", {}).get("ai_rejected_results") or {}
    details = rej.get("details") or []
    total = _int(rej.get("total"))
    if total == 0:
        return "本周无AI拒绝样本，暂无法观察过滤规则。"
    missed = [d for d in details if "错失" in str(d.get("classification") or "") or "错过" in str(d.get("classification") or "")]
    unable = [d for d in details if "无法" in str(d.get("classification") or "")]
    body_parts: list[str] = []
    if missed:
        rmax = missed[0].get("r_max")
        body_parts.append(f"1个事后走出{_num(rmax)}R空间" if rmax is not None else "1个被分类为潜在执行型错失")
    if unable:
        rmax = unable[0].get("r_max")
        body_parts.append(f"另1个最大顺向{_num(rmax)}R但先后顺序无法确认" if rmax is not None else "另1个先后顺序无法确认")
    body = ("，其中" + "，".join(body_parts)) if body_parts else ""
    return (
        f"本周AI拒绝{total}个{body}。提示过滤可能存在错过机会，但不足以判断规则整体过严。"
    )


def build_weekly_fallback_response(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "week_start": facts["week_start"],
        "week_end": facts["week_end"],
        "incomplete": bool(facts.get("incomplete")),
        "data_status": "部分缺失",
        "market_summary": "AI周度分析暂不可用，仅展示本地汇总数据。",
        "market_regime": "无法判断",
        "core_metrics_summary": "AI总结暂不可用，请以本地汇总数字为准。",
        "daily_observations": [],
        "ea_analysis": {
            "direction_summary": "",
            "route_summary": "",
            "loss_classification": "",
            "mfe_mae_summary": "",
        },
        "candidate_filter_analysis": "",
        "group_analysis": "",
        "issues": {
            "normal_strategy_losses": "",
            "repeated_observations": "",
            "program_data_issues": "",
            "supported_rules": "",
            "strategy_insights": "",
        },
        "conclusion": "AI周度分析暂不可用，仅展示本地客观汇总。",
        "next_week_suggestions": [],
    }


WEEKLY_RESPONSE_FIELDS = {
    "week_start",
    "week_end",
    "incomplete",
    "data_status",
    "market_summary",
    "market_regime",
    "core_metrics_summary",
    "daily_observations",
    "ea_analysis",
    "candidate_filter_analysis",
    "group_analysis",
    "issues",
    "conclusion",
    "next_week_suggestions",
}


def validate_weekly_response(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("weekly response must be a JSON object")
    result: dict[str, Any] = {}
    for field in WEEKLY_RESPONSE_FIELDS:
        if field in raw:
            result[field] = raw[field]
    result["week_start"] = str(raw.get("week_start", ""))
    result["week_end"] = str(raw.get("week_end", ""))
    result["incomplete"] = bool(raw.get("incomplete", False))
    return result


def build_weekly_lark_card(
    week_key: str,
    facts: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    """卡：8 段固定结构。"""
    response = response or {}
    market = facts["market"]
    groups = facts["groups"]
    candidates = facts["candidates"]
    net = _safe_float(groups["ea"]["net"])
    header = "green" if net > 0 else "red" if net < 0 else "blue"

    title_suffix = "｜截至当前" if facts.get("incomplete") else ""
    title = f"📊 周度复盘｜{facts['week_start']} ~ {facts['week_end']}{title_suffix}"

    # 1 本周行情
    regime = str(response.get("market_regime") or "—")
    s1 = (
        f"**行情类型：** {regime}\n"
        f"**开 / 高 / 低 / 收：** {_num(market['open'])} / {_num(market['high'])} / "
        f"{_num(market['low'])} / {_num(market['close'])}\n"
        f"**周振幅：** {_num(market['range_usd'])} USD　**周净变化：** "
        f"{_num(market['net_change_usd'])} USD（{_num(market['net_change_pct'])}%）\n\n"
        f"{response.get('market_summary', '')}"
    )

    # 2 本周总体成绩（EA 为主）
    ea = groups["ea"]
    open_extra = f"｜持仓中 {ea['open_count']} 笔" if ea["open_count"] else ""
    breakeven_extra = f"｜{ea['breakeven']} 保本" if ea["breakeven"] else ""
    r_extra = f"（{_num(ea['total_r'])}R / {ea['r_samples']} 笔）" if ea["r_samples"] else ""
    s2 = (
        f"**EA 正式交易**\n"
        f"开仓 {ea['open_count'] + ea['closed_count']} 笔｜已结算 {ea['closed_count']} 笔"
        f"{open_extra}\n"
        f"{ea['win']} 盈 {ea['loss']} 亏"
        f"{breakeven_extra}\n"
        f"净盈亏 {_num(ea['net'])} USD｜平均R {_r_text(ea['avg_r'])}"
        f"{r_extra}\n"
        f"胜率 {_num(ea['win_rate'])}%｜盈利因子 {_num(ea['profit_factor'])}｜"
        f"平均盈利 {_num(ea['avg_win'])} USD｜平均亏损 {_num(ea['avg_loss'])} USD\n\n"
        f"**Parallel AI：** {_group_summary_line('Parallel AI', groups['ai']).replace('Parallel AI：', '')}\n"
        f"**人工：** {_group_summary_line('人工', groups['manual']).replace('人工：', '')}{MANUAL_NOTE}\n\n"
        + (
            f"**未归类：** {_group_summary_line('未归类', groups['unknown']).replace('未归类：', '')}\n\n"
            if (groups['unknown']['closed_count'] + groups['unknown']['open_count']) > 0
            else ""
        )
        + f"**【版本拆分】**\n{_render_version_split(ea)}\n\n"
        f"{response.get('core_metrics_summary', '')}"
    )

    # 3 每日表现回顾
    obs_by_date = {
        str(o.get("date") or ""): str(o.get("note") or "")
        for o in (response.get("daily_observations") or [])
    }
    daily_lines: list[str] = []
    for ctx in facts.get("daily_context") or []:
        d = str(ctx.get("date") or "")
        short_d = d[-5:]
        # 只用周报 AI 生成的“当天特点”，不用日报结论（避免“无需人工干预”等套话）
        note = obs_by_date.get(d) or ""
        if note and ("无需人工干预" in note or "无需立即人工决策" in note or "继续观察累计数据" in note):
            note = ""
        for phrase in ("全为做多", "全为做空", "全部为做多", "全部为做空", "全部BUY", "全部SELL", "均做多", "均做空"):
            note = note.replace(phrase, "")
        note = re.sub(r"[，。；、\s]+$", "", note)
        if ctx.get("ea_trade_count"):
            buy = _int(ctx.get("ea_buy"))
            sell = _int(ctx.get("ea_sell"))
            dir_text = f"（BUY {buy}/SELL {sell}）" if (buy or sell) else ""
            r_text_day = f"总R {_num(ctx.get('ea_total_r'))}R" if ctx.get("ea_total_r") else "总R 数据不足"
            daily_lines.append(
                f"{short_d}｜EA {ctx['ea_trade_count']} 笔{dir_text}｜{ctx['ea_win']}盈{ctx['ea_loss']}亏｜"
                f"{_num(ctx['ea_net'])} USD｜{r_text_day}｜{note}" if note else
                f"{short_d}｜EA {ctx['ea_trade_count']} 笔{dir_text}｜{ctx['ea_win']}盈{ctx['ea_loss']}亏｜"
                f"{_num(ctx['ea_net'])} USD｜{r_text_day}"
            )
        else:
            daily_lines.append(f"{short_d}｜当日无正式EA结算交易。")
    s3 = "\n".join(daily_lines) if daily_lines else "本周无完整交易日数据。"

    # 4 EA 本周交易综合分析
    ea_analysis = response.get("ea_analysis") or {}
    direction_summary = _guard_ea_only(str(ea_analysis.get("direction_summary", "")))
    route_summary = _guard_ea_only(str(ea_analysis.get("route_summary", "")))
    loss_classification = _guard_mfe_claims(
        _guard_ea_only(str(ea_analysis.get("loss_classification", "待分析"))), ea
    )
    mfe_summary = _guard_mfe_claims(
        _guard_ea_only(str(ea_analysis.get("mfe_mae_summary", ""))), ea
    )
    mfe_summary = _guard_win_mfe_claim(mfe_summary, ea)
    s4 = (
        f"**A. 按方向分析**\n{_render_direction(ea['by_direction'])}\n\n"
        f"{direction_summary}\n\n"
        f"**B. 按入场路径分析**\n{_render_route(ea['by_route'])}\n\n"
        f"{route_summary}\n\n"
        f"**C. 亏损单分类**\n{loss_classification}\n\n"
        f"**D. 最大浮盈 / 最大浮亏**\n{_render_mfe_mae(ea)}\n\n"
        f"{mfe_summary}"
    )

    # 5 候选与 AI 过滤效果
    cand = candidates
    ai_allowed = cand.get("ai_allowed_results") or {}
    ai_rejected = cand.get("ai_rejected_results") or {}
    candidate_filter_analysis = _guard_group_count(
        str(response.get("candidate_filter_analysis", "")), facts
    )
    candidate_filter_analysis = _guard_filter_rating(candidate_filter_analysis)
    candidate_filter_analysis = _strip_ai_allowed_narrative(candidate_filter_analysis)
    # 去掉包含“保护作用”或“整体看AI过滤”结论的整句，避免与确定性结论重复
    kept_sentences: list[str] = []
    for sentence in re.split(r"(?<=[。])", candidate_filter_analysis):
        if "保护作用" in sentence or ("整体看" in sentence and "AI过滤" in sentence):
            continue
        kept_sentences.append(sentence)
    candidate_filter_analysis = "".join(kept_sentences).strip("。；， \n")
    rejected_detail_lines: list[str] = []
    for d in ai_rejected.get("details") or []:
        rmax = d.get("r_max")
        rmax_text = f"｜事后最大顺向 {_num(rmax)}R" if rmax is not None else ""
        dir_cn = "做多" if str(d.get("direction")) == "BUY" else "做空" if str(d.get("direction")) == "SELL" else str(d.get("direction") or "")
        rejected_detail_lines.append(
            f"- {d.get('signal_id')}（{dir_cn}｜{d.get('route')}）：{d.get('classification')}{rmax_text}"
        )
    s5 = (
        f"**正式候选：** {cand['formal_total']}｜AI允许 {cand['ai_allow']}｜AI拒绝 {cand['ai_reject']}\n"
        f"**实际成交：** {cand['filled']}｜未成交：{cand['unfilled']}\n\n"
        f"**AI 允许后结果**\n"
        f"成交 {ai_allowed.get('filled', 0)} 笔｜盈利 {ai_allowed.get('win', 0)} 笔｜"
        f"亏损 {ai_allowed.get('loss', 0)} 笔｜平均 {_r_text(ai_allowed.get('avg_r'))}\n"
        f"未成交 {ai_allowed.get('unfilled', 0)} 笔（事后结果见候选卡）\n\n"
        f"**AI 拒绝后结果**\n"
        f"AI拒绝 {ai_rejected.get('total', 0)} 个｜有效过滤 {ai_rejected.get('effective_filter', 0)} 个｜"
        f"错过机会 {ai_rejected.get('missed', 0)} 个｜无法确认 {ai_rejected.get('unable_to_confirm', 0)} 个\n"
        + ("\n".join(rejected_detail_lines) if rejected_detail_lines else "")
        + "\n\n"
        f"**未成交候选分类**\n"
        + ("\n".join(f"- {k}：{v} 个" for k, v in cand.get("unfilled_classification", {}).items()) if cand.get("unfilled_classification") else "本周无未成交候选。")
        + f"\n\n{candidate_filter_analysis}\n\n{_ai_rejected_conclusion(facts)}"
    )

    # 6 EA / Parallel AI / 人工深度对照
    rule_blocks = facts.get("rule_blocks_cn") or {}
    group_analysis = _guard_group_count(
        str(response.get("group_analysis", "")), facts
    )
    group_analysis = _guard_manual_claims(group_analysis)
    group_analysis = _guard_risk_control_claim(group_analysis, facts)
    risk_conclusion = (
        "Parallel AI本周平均R高于EA，但两组样本数量、交易机会和交易管理方式不同，"
        "目前不能据此判断Parallel AI优于EA或风险控制更好。"
    )
    ai_avg_r = groups["ai"].get("avg_r")
    ea_avg_r = groups["ea"].get("avg_r")
    if (
        ai_avg_r is not None
        and ea_avg_r is not None
        and ai_avg_r > ea_avg_r
        and "不能据此判断Parallel AI优于EA或风险控制更好" not in group_analysis
    ):
        group_analysis = (
            group_analysis.rstrip("。；， \n") + "。" + risk_conclusion
            if group_analysis
            else risk_conclusion
        )
    if "亏损控制稍佳" in group_analysis or "风险控制稍佳" in group_analysis:
        ai_r = groups["ai"]["avg_r"]
        ea_r = groups["ea"]["avg_r"]
        group_analysis = (
            f"从本周结果看，Parallel AI平均R {_r_text(ai_r)}，高于EA的{_r_text(ea_r)}；"
            "但两组交易数量、入场机会和管理方式不同，且样本较少，因此只能记录本周结果差异，"
            "暂不能判断Parallel AI风险控制优于EA。"
        )
    s6 = (
        f"{_group_summary_line('EA', groups['ea'])}\n"
        f"{_group_summary_line('Parallel AI', groups['ai'])}\n"
        f"{_group_summary_line('人工', groups['manual'])}{MANUAL_NOTE}\n\n"
        + (
            f"{_group_summary_line('未归类', groups['unknown'])}\n\n"
            if (groups['unknown']['closed_count'] + groups['unknown']['open_count']) > 0
            else ""
        )
        + f"**Parallel AI 规则拦截原因（失败项累计，一笔候选可能同时触发多项）**\n"
        + ("\n".join(f"- {k}：{v} 次" for k, v in rule_blocks.items()) if rule_blocks else "本周无规则拦截记录。")
        + f"\n\n{group_analysis}"
    )

    # 7 本周问题与对 EA 策略的启示
    issues = response.get("issues") or {}
    strategy_insights = _guard_ai_filter_claims(
        str(issues.get("strategy_insights", "待分析"))
    )
    strategy_insights = _guard_filter_rating(strategy_insights)
    strategy_insights = _guard_manual_claims(strategy_insights)
    strategy_insights = _strip_supported_rules_claim(strategy_insights)
    rej = facts.get("candidates", {}).get("ai_rejected_results") or {}
    rej_total = _int(rej.get("total"))
    rej_eff = _int(rej.get("effective_filter"))
    rej_missed = _int(rej.get("missed"))
    rej_unable = _int(rej.get("unable_to_confirm"))
    supported_rules = (
        f"当前AI拒绝样本只有{rej_total}个，{rej_eff}个有效过滤、{rej_missed}个潜在错过、"
        f"{rej_unable}个无法确认，结果分散，暂不足以评价过滤规则整体有效或整体过严。"
    )
    filter_observation = _filter_rule_observation(facts)
    if "结合更大的时间框架" in strategy_insights or "降低交易频率" in strategy_insights:
        strategy_insights = (
            "后续可以事后对照更高周期趋势状态，检查连续亏损是否集中发生在大周期与M5方向不一致"
            "或趋势减弱阶段。目前只统计，不新增高周期过滤条件。"
        )
    program_issues = facts.get("issues", {}).get("program") or []
    program_items = list(program_issues)
    ai_program_text = str(issues.get("program_data_issues") or "").strip()
    if ai_program_text:
        # 避免与本地程序问题重复显示
        is_dup = any(
            item and (
                item in ai_program_text
                or ai_program_text in item
                or (
                    "Lark" in item
                    and "Lark" in ai_program_text
                    and "送达" in item
                    and "送达" in ai_program_text
                )
                or (
                    ("判断不同" in item or "判断不一致" in item)
                    and ("判断不同" in ai_program_text or "判断不一致" in ai_program_text)
                    and "送达" in item
                    and "送达" in ai_program_text
                )
            )
            for item in program_items
        )
        if not is_dup:
            program_items.append(ai_program_text)
    s7 = (
        f"**【正常策略亏损】**\n{_guard_mfe_claims(str(issues.get('normal_strategy_losses', '待分析')), ea)}\n\n"
        f"**【重复出现、值得继续观察】**\n{_guard_mfe_claims(str(issues.get('repeated_observations', '待分析')), ea)}\n\n"
        f"**【程序 / 数据问题】**\n"
        + ("\n".join(f"- {i}" for i in program_items) if program_items else "无。")
        + "\n\n"
        f"**【规则证据暂不足】**\n{supported_rules}\n\n"
        f"**【过滤规则值得继续观察】**\n{filter_observation}\n\n"
        f"**【对 EA 策略的启示】**\n{strategy_insights}"
    )

    # 8 下周重点观察
    suggestions = [str(s) for s in (response.get("next_week_suggestions") or [])]
    rebuilt: list[str] = []
    for s in suggestions:
        if "暂停交易" in s and "情绪化" in s:
            rebuilt.append(
                "统计连续亏损发生时的共同市场特征，重点检查是否集中在震荡、快速反转或趋势切换阶段。"
                "暂不直接增加连续亏损暂停规则，先验证这种限制是否真的有统计价值。"
            )
        elif "通知不一致的原因" in s or ("EA与AI" in s and "通知" in s and "原因" in s):
            rebuilt.append("确认那1条EA与Parallel AI判断不同的Lark通知是否成功送达，排查通知送达状态。")
        else:
            rebuilt.append(s)
    suggestions = rebuilt
    # 兜底：如果 DeepSeek 没有写这两条，就不强行追加（不增加新模块）
    s8 = "\n".join(f"{i}. {item}" for i, item in enumerate(suggestions, 1)) if suggestions else "下周保持现有规则继续观察。"

    # 9 本周典型交易对照（新增）
    s9 = _render_typical_trades(facts.get("typical_trades") or [])

    sections = [
        _section("1. 本周行情", s1),
        _section("2. 本周总体成绩", s2),
        _section("3. 每日表现回顾", s3),
        _section("4. EA 本周交易综合分析", s4),
        _section("5. 候选与 AI 过滤效果", s5),
        _section("6. EA / Parallel AI / 人工深度对照", s6),
        _section("7. 本周问题与对 EA 策略的启示", s7),
        _section("8. 本周典型交易对照", s9),
        _section("9. 下周重点观察", s8),
    ]

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": header,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {
                "elements": [{"tag": "markdown", "content": section} for section in sections]
            },
        },
    }


def build_weekly_payload(facts: dict[str, Any]) -> dict[str, Any]:
    """给 DeepSeek Pro 的周度冻结事实包。"""
    return {
        "week_start": facts["week_start"],
        "week_end": facts["week_end"],
        "incomplete": facts.get("incomplete", False),
        "trading_days": facts["trading_days"],
        "days": facts["days"],
        "market": facts["market"],
        "groups": facts["groups"],
        "candidates": facts["candidates"],
        "rule_blocks_cn": facts["rule_blocks_cn"],
        "version_split": facts["version_split"],
        "daily_context": facts["daily_context"],
        "issues": facts["issues"],
    }


def write_weekly_output(
    root: Path, week_key: str, facts: dict[str, Any], response: dict[str, Any]
) -> None:
    out_dir = root / "Weekly_Review" / week_key
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"Weekly_Data_{week_key}.json").write_text(
        json.dumps(build_weekly_payload(facts), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"Weekly_Review_{week_key}.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8"
    )
