from __future__ import annotations

from tools.parallel_ai_calculator import gate_cn

import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from tools.trade_lifecycle import build_open_trade_explanation


# Lark卡片markdown不支持 --- 分隔线，使用连续横线作为视觉分隔符；
# HTML预览脚本会把整行横线渲染成真正的CSS分隔线，不显示文字。
_VISUAL_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any, digits: int = 2) -> str:
    numeric = _float(value)
    return "—" if numeric is None else f"{numeric:.{digits}f}"


def _signed(value: Any, suffix: str = "") -> str:
    numeric = _float(value)
    if numeric is None:
        return "—"
    rounded = Decimal(str(numeric)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:+.2f}{suffix}"


def _time(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{2}:\d{2})(?::\d{2})?$", text)
    return match.group(1) if match else (text or "—")


def _time_seconds(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{2}:\d{2}:\d{2})$", text)
    return match.group(1) if match else (_time(text) if text else "—")


def _server_time_to_beijing(value: Any) -> str:
    """把 EA 输出的服务器时间（UTC+3）转成北京时间（UTC+8），保持原格式。"""
    text = str(value or "").strip()
    if not text:
        return text
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return (dt + timedelta(hours=5)).strftime(fmt)
        except ValueError:
            continue
    return text


def _status_text(value: Any) -> str:
    status = str(value or "").strip()
    if status == "closed_cross_day":
        return "跨日结算"
    if status.startswith("closed"):
        return "已结算"
    if status == "open_at_day_end":
        return "收盘持仓"
    return status or "状态不完整"


def _done(value: Any) -> str:
    return "已执行" if bool(value) else "未执行"


def _runner_text(value: Any, is_open: bool = False) -> str:
    if is_open:
        return "仍在持仓中"
    return "已执行" if bool(value) else "未进入后续管理"


def _trade_profit_label(value: float) -> str:
    if value > 0:
        return "净盈利"
    if value < 0:
        return "净亏损"
    return "净盈亏"


def _r_explanation(final_r: float, initial_risk: float) -> str:
    return f"{final_r:+.2f}R（1R≈{initial_risk:.2f} USD）"


def _route_text(value: Any, direction: str = "") -> str:
    route = str(value or "").upper()
    if route == "FIB_PA":
        return "Fib + PA 路径"
    if route == "EMA_H23":
        return "EMA H2/H3 路径"
    if route == "EMA_L23":
        return "EMA L2/L3 路径"
    if route == "BOTH":
        ema_label = "EMA H2/H3" if direction == "BUY" else "EMA L2/L3"
        return f"Fib + PA 与 {ema_label} 双路径"
    if route in {"EMA_RECOVERY_BUY", "EMA_RECOVERY_SELL"}:
        return "EMA回调恢复"
    if route == "STRONG_BEAR_BAR":
        return "强空头K线"
    if route == "STRONG_BULL_BAR":
        return "强多头K线"
    return "当前数据未提供"


def _candidate_route_text(value: Any, direction: str = "") -> str:
    label = _route_text(value, direction)
    return label[:-3] if label.endswith(" 路径") else label


_DEAL_REASON_CN = {
    "DEAL_REASON_CLIENT": "桌面MT5客户端请求平仓",
    "DEAL_REASON_MOBILE": "手机端请求平仓",
    "DEAL_REASON_WEB": "网页端请求平仓",
    "DEAL_REASON_EXPERT": "EA主动平仓",
    "DEAL_REASON_SL": "止损平仓",
    "DEAL_REASON_TP": "止盈平仓",
    "DEAL_REASON_SLTP": "止损/止盈平仓",
    "DEAL_REASON_SO": "账户强制平仓",
    "DEAL_REASON_EXPIRED": "挂单到期失效",
    "DEAL_REASON_BOOK": "交易簿执行",
    "DEAL_REASON_GATE": "网关订单执行",
    "DEAL_REASON_SPLIT": "仓位分拆",
    "DEAL_REASON_ROLLOVER": "换日展期",
    "DEAL_REASON_VWAP": "VWAP执行",
    "DEAL_REASON_CLOSEBY": "反向对冲平仓",
}


def _close_reason_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    mapped = _DEAL_REASON_CN.get(raw.upper())
    return mapped if mapped else raw


def _close_exit_text(row: dict[str, Any]) -> str:
    """平仓方式按真实退出过程描述，分阶段止盈的订单不再统一写“触及止盈平仓”。"""
    raw = str(row.get("close_reason") or "").strip()
    if not raw:
        return ""
    reason = raw.upper()
    staged = [
        leg
        for flag, leg in (
            (bool(row.get("tp1_done")), "TP1"),
            (bool(row.get("tp2_done")), "TP2"),
            (bool(row.get("runner_done")), "剩余仓位"),
        )
        if flag
    ]
    if reason == "DEAL_REASON_TP" and len(staged) >= 2:
        base = "分阶段止盈退出（" + " → ".join(staged) + "）"
    elif reason == "DEAL_REASON_SL" and staged:
        # 部分止盈后的止损是“剩余仓位”的止损，不是整仓原始止损；若成交价贴近
        # 开仓价则是保本止损，不能笼统写成“止损平仓”。
        entry = _float(row.get("entry_price"))
        exit_price = _float(row.get("exit_price"))
        initial_sl = _float(row.get("initial_sl"))
        risk = abs(entry - initial_sl) if entry and initial_sl else 0.0
        near_entry = (
            risk > 0 and exit_price is not None
            and abs(exit_price - entry) < risk * 0.5
        )
        base = (
            "部分止盈后剩余仓位保本止损"
            if near_entry
            else "部分止盈后剩余仓位止损"
        )
    else:
        base = _close_reason_text(raw)
        if staged:
            base = f"{base}（此前已完成{'、'.join(staged)}退出）"
    exit_price = _float(row.get("exit_price"))
    slippage = _float(row.get("sl_slippage"))
    if reason in {"DEAL_REASON_SL", "DEAL_REASON_TP", "DEAL_REASON_SLTP"} and exit_price is not None:
        parts = [base]
        if exit_price > 0:
            parts.append(f"实际{exit_price:.2f}")
        if slippage is not None and slippage > 0:
            parts.append(f"滑点{slippage:.2f} USD")
        return "｜".join(parts)
    return base


def _candidate_status(row: dict[str, Any]) -> str:
    return "已成交" if row.get("outcome") == "filled" else "未成交"


def _entry_explanation(
    candidate: dict[str, Any],
    direction: str,
    filled: bool,
    facts_override: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return (入场路径, 开仓理由) in the same style as the open-notification card."""
    facts = facts_override if facts_override is not None else candidate.get("entry_facts")
    if not isinstance(facts, dict) or not str(facts.get("signal_route") or "").strip():
        route = _route_text(candidate.get("route"), direction)
        return route, "当前历史日报未保存该候选的完整开仓事实，无法按开仓通知格式还原。"
    explanation = build_open_trade_explanation(facts)
    reason = explanation["reason"]
    if not filled and "因此成交" in reason:
        reason = reason.replace("因此成交", "因此形成正式候选")
    route_label = explanation["route"]
    if "路径记录缺失" in route_label:
        known_route = _route_text(facts.get("signal_route") or candidate.get("route"), direction)
        if known_route != "当前数据未提供":
            route_label = known_route
    return route_label, reason


def _candidate_ai_text(row: dict[str, Any]) -> str:
    status = str(row.get("ai_status") or "undecided")
    if status == "allow":
        label = "允许"
    elif status == "reject":
        label = "拒绝"
    elif status == "error":
        label = "异常·降级放行" if row.get("ai_allow_trade") else "异常·未放行"
    else:
        return "未见审核记录"
    confidence = row.get("ai_confidence")
    score = (
        f"｜{int(confidence)}分"
        if confidence is not None and status != "error"
        else ""
    )
    when = _time_seconds(_server_time_to_beijing(row.get("ai_time")))
    return f"{label}{score}｜{when}" if when else f"{label}{score}"


_FIRST_TOUCH_LABEL = {
    "SL": "原止损位",
    "0.8R": "0.8R目标",
    "1R": "1R目标",
    "2R": "2R目标",
}

_FIRST_TOUCH_VERB = {
    "SL": "先触发止损",
    "0.8R": "先达到0.8R目标",
    "1R": "先达到1R目标",
    "2R": "先达到2R目标",
}

_RESOLUTION_TEXT = {
    "M5": "M5",
    "M1": "M1",
    "tick": "历史Tick逐笔回放",
    "insufficient": "Tick历史数据不完整",
}


def _r_value_text(value: Any) -> str:
    numeric = _float(value)
    # 最大有利走势统一最低为 0.00R，不允许出现负数。
    return "—" if numeric is None else f"{max(numeric, 0.0):+.2f}R"


def _path_known(item: dict[str, Any]) -> bool:
    """Whether the SL-vs-target order has actually been resolved."""
    if bool(item.get("order_unconfirmed")) or bool(item.get("final_insufficient")):
        return False
    return bool(str(item.get("first_touch") or ""))


def _post_expiry_conclusion(item: dict[str, Any]) -> str:
    if bool(item.get("final_insufficient")):
        return (
            "该候选失效后可用行情不足，无法确认后续价格路径，本次不纳入机会错失统计。"
        )
    if not _entry_retouched(item):
        return (
            "价格后来没有再回到原计划入场价，因此即使当时想做这笔交易，"
            "也没有重新入场的机会，属于正常未成交。"
        )
    if bool(item.get("order_unconfirmed")):
        return (
            "价格后来回到了原计划入场价，但历史Tick数据不足，"
            "无法可靠判断是先止损还是先达到盈利目标。"
        )
    first_touch = str(item.get("first_touch") or "")
    if first_touch == "SL":
        return (
            "如果当时重新入场，这笔交易会先触发原计划止损。之后行情即使上涨，"
            "也发生在止损之后，不属于这笔交易实际能够获得的盈利。"
        )
    if first_touch in {"0.8R", "1R", "2R"}:
        level = _FIRST_TOUCH_LABEL.get(first_touch, first_touch)
        return (
            f"如果当时重新入场，这笔交易会先达到{level}，存在实际盈利机会。"
        )
    return (
        "价格后来回到了原计划入场价，但之后没有触及止损或任一盈利目标，"
        "属于正常未成交。"
    )


def _entry_retouched(item: dict[str, Any]) -> bool:
    """Whether the market re-touched the planned entry after expiry, i.e. a
    virtual fill exists. Legacy rows without the field fall back to the
    first-touch path implying a fill."""
    value = item.get("entry_retouched")
    if value is not None:
        return bool(value)
    return bool(item.get("entry_retouch_time")) or bool(item.get("first_touch"))


def _display_judgment(item: dict[str, Any]) -> str:
    """Human-readable unfilled judgment (4 fixed Chinese categories).

    New rows carry ``judgment_cn`` directly. Legacy rows only have the internal
    ``classification`` machine code, so derive the display value from it plus
    the fixed ``reason_code``.
    """
    value = item.get("judgment_cn")
    if value:
        return str(value)
    classification = str(item.get("classification") or "")
    if classification == "正常未成交":
        return "正常未成交"
    if classification == "无法判断":
        return "暂时无法判断"
    if classification.endswith("执行型错失"):
        return (
            "AI拒绝后错过机会"
            if str(item.get("reason_code") or "") == "AI拒绝"
            else "挂单后错过机会"
        )
    return "暂时无法判断"


def _summary_judgment(item: dict[str, Any]) -> str:
    """Concise summary-line judgment for the candidate summary table."""
    if bool(item.get("order_unconfirmed")):
        return "Tick数据不足"
    if bool(item.get("final_insufficient")):
        return "数据不足"
    return _display_judgment(item)


def _issue_conclusion_text(item: dict[str, Any]) -> str:
    """Main-card conclusion for an unfilled candidate (R/path evidence stays
    on the candidate card)."""
    judgment = _display_judgment(item)
    if bool(item.get("order_unconfirmed")):
        return "后来价格回到原计划入场价，但无法可靠判定先止损还是先达目标"
    if judgment == "正常未成交":
        if not _entry_retouched(item):
            return "经事后走势验证判定为正常未成交，后续仍无入场机会"
        return "经事后走势验证判定为正常未成交，不属于有效机会错失"
    if judgment == "AI拒绝后错过机会":
        return "经事后走势验证判定为AI拒绝后错过机会"
    if judgment == "挂单后错过机会":
        return "经事后走势验证判定为挂单后错过机会"
    return "失效后行情数据不足，无法判定后续是否错失"


def _final_judgment(item: dict[str, Any]) -> str:
    judgment = _display_judgment(item)
    if bool(item.get("order_unconfirmed")) or bool(item.get("final_insufficient")):
        return "数据不足，暂不判断是否属于错过机会。"
    if judgment == "正常未成交":
        return "正常未成交。"
    level = _FIRST_TOUCH_LABEL.get(str(item.get("first_touch") or ""), "")
    level_suffix = f"，并先达到{level}" if level else ""
    if judgment == "AI拒绝后错过机会":
        return (
            f"AI拒绝后错过机会：DeepSeek当时拒绝了该信号，但价格后来回到原计划入场价"
            f"{level_suffix}，说明存在实际盈利机会。"
        )
    if judgment == "挂单后错过机会":
        return (
            f"挂单后错过机会：该信号已通过AI审核并挂单，但最终未成交；"
            f"价格后来回到原计划入场价{level_suffix}。"
        )
    return "数据不足，暂不判断是否属于错过机会。"


def _render_unfilled_verification(item: dict[str, Any]) -> str:
    retouched = _entry_retouched(item)
    retouch_time = _time_seconds(item.get("entry_retouch_time"))
    insufficient30 = "（观察窗口不足）" if bool(item.get("window30_insufficient")) else ""
    insufficient60 = "（观察窗口不足）" if bool(item.get("window60_insufficient")) else ""
    first_touch = str(item.get("first_touch") or "")
    resolution = str(item.get("replay_resolution") or "")
    resolution_text = _RESOLUTION_TEXT.get(resolution, resolution or "—")
    virtual_entry_time = _time_seconds(item.get("virtual_entry_time"))
    first_hit_time = _time_seconds(item.get("first_touch_time"))
    path_known = _path_known(item)

    lines = ["## 未成交后的走势回看", ""]
    retouch_line = f"**后来是否重新回到原计划入场价：** {'是' if retouched else '否'}"
    if retouched and retouch_time:
        retouch_line += f"｜{retouch_time}"
    lines.append(retouch_line)
    lines.append("")
    if retouched:
        lines.append(f"**如果当时重新入场：** {virtual_entry_time or retouch_time}")
        lines.append("")
    lines.append(
        f"**之后30分钟：** 最多向有利方向走了 {_r_value_text(item.get('r30'))}{insufficient30}"
    )
    lines.append(
        f"**之后1小时：** 最多向有利方向走了 {_r_value_text(item.get('r60'))}{insufficient60}"
    )
    if path_known:
        lines.append(f"**交易有效期间最大顺向：** {_r_value_text(item.get('r_max'))}")
        after_exit = _float(item.get("r_max_after_exit"))
        if after_exit is not None and after_exit > 0:
            lines.append(f"**止损后后续走势：** {_r_value_text(after_exit)}")
    else:
        lines.append(
            f"**后续观察期间：** 最多曾向有利方向走到 {_r_value_text(item.get('r_max'))}"
        )
    lines.append("")
    if path_known:
        key_result = _FIRST_TOUCH_VERB.get(first_touch, first_touch)
        if first_hit_time:
            key_result += f"｜{first_hit_time}"
    else:
        key_result = "暂时无法确认是先止损，还是先达到盈利目标"
    lines.append(f"**关键结果：** {key_result}")
    lines.append(f"**判断依据：** {resolution_text}")
    if not path_known and retouched:
        lines.append("")
        lines.append(
            "**原因：** 程序已按M5 → M1 → Tick逐级回放，并重新检查MT5连接、"
            "时间转换和Tick历史；自动补载后该时段Tick仍不完整，"
            "因此无法可靠还原真实价格先后顺序。"
        )
        missing = str(item.get("tick_missing_ranges") or "").strip()
        if missing:
            lines.append(f"**Tick数据情况：** 部分缺失｜缺失区间：{missing}")
        else:
            lines.append(
                "**Tick数据情况：** 历史数据不完整，具体缺失区间无法确定"
            )
    lines.append("")
    lines.append("**事后走势结论：**")
    lines.append(_post_expiry_conclusion(item))
    lines.append("")
    lines.append(f"**最终判定：** {_final_judgment(item)}")
    return "\n".join(lines)


_NO_FILL_REASON_BY_OUTCOME = {
    "ai_rejected": "DeepSeek审核拒绝",
    "ai_error": "AI审核异常",
    "wait_missed": "距离不足，未创建真实挂单；等待期间原入场价被市场触达，信号作废",
    "execution_blocked": "挂单执行失败",
    "expired": "挂单超过有效时间后被撤销",
    "cancelled": "信号失效后挂单被撤销",
    "pending_untriggered": "暂未定位到最终未成交原因",
    "status_incomplete": "暂未定位到最终未成交原因",
}


def format_candidate_no_fill_reason(row: dict[str, Any]) -> str:
    """Return the concise direct reason why a candidate was not filled.

    This is the single source of truth for the "未成交原因" line across daily
    cards, backfill, offline rebuild, HTML preview and Lark candidate cards.
    """
    outcome = str(row.get("outcome") or "")
    if (outcome == "cancelled"
            and str(row.get("outcome_reason") or "").strip()
            == "V3102_CANCEL_SIGNAL_HIGH_BROKEN"):
        return "信号高点被突破，挂单已撤销"
    if outcome in _NO_FILL_REASON_BY_OUTCOME:
        return _NO_FILL_REASON_BY_OUTCOME[outcome]
    reason = str(row.get("outcome_reason") or "").strip()
    return reason or "暂未定位到最终未成交原因"


def _order_terminal_action(outcome: Any) -> str:
    """Concise human label for a pending order's final lifecycle action."""
    return {
        "cancelled": "信号失效撤销",
        "expired": "到期撤销",
        "execution_blocked": "执行失败",
        "pending_untriggered": "状态未确认",
        "wait_missed": "作废（未追价）",
        "status_incomplete": "状态未确认",
    }.get(str(outcome or ""), "终止")


def humanize_review_reason(raw: Any) -> str:
    """Translate DeepSeek review program fields into human-readable Chinese.

    Only used for human-facing "拒绝依据" text; it never changes the underlying
    AI allow/reject decision or any trading result.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    replacements = [
        ("fib_path_valid=false", "Fib路径无效"),
        ("fib_path_valid=true", "Fib路径有效"),
        ("fib_zone=INVALID", "Fib回调超出有效区间"),
        ("fib_zone=VALID", "Fib回调处于有效区间"),
        ("fib_zone=DEEP", "Fib回调深度偏大"),
        ("fib_zone=SHALLOW", "Fib回调深度偏小"),
        ("three_bar=false", "三根推进条件未满足"),
        ("three_bar=true", "三根推进条件满足"),
        ("EMA_H23", "EMA H2/H3"),
        ("FIB_PA", "Fib + PA"),
        ("HH_HL", "HH/HL（高点抬高/低点抬高）"),
        ("LL_LH", "LL/LH（低点降低/高点降低）"),
        ("DEEP区间", "偏深区间"),
        ("fib回撤", "Fib回调"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)
    # RR=1.13 → 盈亏空间1.13R；RR仅0.81 → 盈亏空间仅0.81R
    text = re.sub(
        r"RR\s*=\s*([0-9]+(?:\.[0-9]+)?)",
        lambda m: f"盈亏空间{m.group(1)}R",
        text,
    )
    text = re.sub(
        r"RR仅([0-9]+(?:\.[0-9]+)?)",
        lambda m: f"盈亏空间仅{m.group(1)}R",
        text,
    )
    text = re.sub(r"\bRR\b", "盈亏空间", text)
    return text


def _render_candidate_card_block(
    row: dict[str, Any],
    unfilled_item: dict[str, Any] | None = None,
) -> str:
    number = _int(row.get("sequence"))
    direction = str(row.get("direction") or "—").upper()
    filled = str(row.get("outcome") or "") == "filled"
    lines = [f"## 候选#{number}｜{direction}｜{_candidate_status(row)}", ""]
    lines.append(
        f"信号K线：{_time(_server_time_to_beijing(row.get('signal_bar_time')))}｜"
        f"候选形成：{_time_seconds(_server_time_to_beijing(row.get('candidate_time')))}"
    )
    lines.append(f"入场路径：**{_candidate_route_text(row.get('route'), direction)}**")
    lines.append(f"计划入场价：{_number(row.get('planned_entry'))}")
    lines.append(f"计划止损：{_number(row.get('planned_sl'))}")
    lines.append(f"第一目标：{_number(row.get('planned_tp1'))}")
    lines.append(f"DeepSeek审核：**{_candidate_ai_text(row)}**")
    if filled:
        position_id = str(row.get("position_id") or "").strip()
        suffix = f"｜对应交易：**#{position_id}**" if position_id else ""
        lines.append(f"最终结果：**已成交**{suffix}")
    else:
        lines.append("最终结果：**未成交**")
        outcome_time = _time_seconds(_server_time_to_beijing(row.get("outcome_time")))
        no_fill_reason = format_candidate_no_fill_reason(row)
        lines.append(f"未成交原因：{outcome_time}｜{no_fill_reason}")
        order_ticket = str(row.get("order_ticket") or "").strip()
        order_created_at = _time_seconds(_server_time_to_beijing(row.get("order_created_at")))
        if order_ticket and order_ticket != "0" and order_created_at:
            lines.append("")
            lines.append("**挂单过程：**")
            lines.append(f"{order_created_at} 创建成功")
            if outcome_time:
                lines.append(
                    f"→ {outcome_time} {_order_terminal_action(row.get('outcome'))}"
                )
            lines.append(f"订单Ticket：#{order_ticket}")
        if row.get("ai_status") == "reject":
            reject_reason = humanize_review_reason(row.get("ai_reason"))
            if reject_reason:
                lines.append(f"拒绝依据：{reject_reason}")
        elif row.get("ai_status") == "error":
            error_info = str(row.get("ai_reason") or "").strip()
            if not error_info:
                error_info = str(row.get("outcome_reason") or "").strip()
            if error_info:
                lines.append(f"异常信息：{error_info}")

    block = "\n".join(lines)
    if not filled and unfilled_item:
        block += "\n\n" + _render_unfilled_verification(unfilled_item)
    return block


def _ai_plan_status_text(value: Any) -> str:
    mapping = {
        "VALID": "计划可以执行",
        "INVALID": "计划不符合下单要求",
        "STALE": "机会已过期",
    }
    return mapping.get(str(value or "").upper(), str(value or "") or "—")


def _ai_execution_status_text(value: Any) -> str:
    mapping = {
        "PENDING_ACTIVE": "挂单等待成交",
        "FILLED": "已成交",
        "NOT_SENT": "未发送订单",
        "BLOCKED": "敞口限制拦截",
        "STALE": "机会已过期（行情已穿越入场价）",
        "PRECHECK_FAIL": "下单预检未通过",
        "SERVER_REJECTED": "交易服务器拒绝",
        "ORDER_SEND_ERROR": "下单错误",
        "EXECUTION_ERROR": "执行异常",
        "UNAVAILABLE": "AI本次无法完成判断",
    }
    return mapping.get(str(value or "").upper(), str(value or "") or "未执行")


def _ai_final_state_text(value: Any) -> str:
    v = str(value or "").upper()
    mapping = {
        "FILLED": "已成交",
        "EXPIRED": "已过期",
        "CANCELLED": "已撤销",
        "PENDING_ACTIVE": "挂单等待成交",
        "PENDING": "挂单等待成交",
        "CLOSED": "已平仓",
        "RULE_BLOCKED": "规则检查拦截",
    }
    if v.startswith("ORDER_STATE_"):
        return "已平仓"
    return mapping.get(v, str(value or "") or "未执行")


def _ai_plan_state_text(detail: dict[str, Any]) -> str:
    """Human-readable state for the AI单开仓理由 list."""
    state = str(detail.get("state", "") or "").strip().lower()
    remaining = _float(detail.get("remaining"))
    if state == "filled":
        return "持仓中" if remaining > 0 else "已成交"
    if state in {"closed", "order_state_4"}:
        return "已平仓"
    if state == "rule_blocked":
        return "规则拦截（未下单）"
    if state == "expired":
        return "未成交（过期）"
    if state == "cancelled":
        return "未成交（撤销）"
    if state in {"pending", "pending_active", "order_accepted", "accepted"}:
        return "挂单中"
    if state.startswith("order_state_"):
        return "已平仓"
    return str(detail.get("state", "") or "挂单中")


def _render_ai_candidate_block(row: dict[str, Any]) -> str:
    """Render one Parallel AI (Trader B) candidate in the EA candidate format."""
    number = _int(row.get("sequence"))
    direction = str(row.get("direction") or "—").upper()
    execution = str(row.get("execution_status") or "").upper()
    final_state = str(row.get("final_state") or "").upper()
    status = _ai_lifecycle_status_text(final_state or execution)
    lines = [f"## AI候选#{number}｜{direction}｜{status}", ""]
    lines.append(f"信号K线：{_time(row.get('signal_bar_time'))}")
    lines.append(f"入场路径：**{_candidate_route_text(row.get('route'), direction)}**")
    lines.append(f"计划入场价：{_number(row.get('planned_entry'))}")
    lines.append(f"计划止损：{_number(row.get('planned_sl'))}")
    lines.append(f"第一目标：{_number(row.get('planned_tp1'))}")
    lines.append(
        f"AI主观判断：**想开仓｜信心 {_int(row.get('confidence'))}**"
    )
    lines.append(f"计划状态：{_ai_plan_status_text(row.get('plan_status'))}")
    if final_state:
        lines.append(f"最终状态：{_ai_final_state_text(final_state)}")
    else:
        lines.append(f"执行状态：{_ai_execution_status_text(row.get('execution_status'))}")
    if final_state == "RULE_BLOCKED" or str(row.get("rule_compliance") or "").upper() == "BLOCKED":
        block_gates = [gate_cn(x) for x in (row.get("block_gate_ids") or []) if gate_cn(x)]
        lines.append("Python确定性核验：规则未通过，本次不下单")
        if block_gates:
            lines.append("未通过项：" + "、".join(block_gates))
    reason = str(row.get("reason") or "").strip()
    if reason:
        lines.append(f"AI理由：{reason}")
    return "\n".join(lines)


def _ai_lifecycle_status_text(value: Any) -> str:
    """One consistent lifecycle label for an AI candidate header."""
    v = str(value or "").upper()
    if v in {"FILLED"}:
        return "已成交"
    if v == "CLOSED":
        return "已成交｜已平仓"
    if v == "RULE_BLOCKED":
        return "规则检查拦截"
    if v == "EXPIRED":
        return "已过期"
    if v == "CANCELLED":
        return "已撤销"
    if v in {"PENDING", "PENDING_ACTIVE"}:
        return "挂单等待成交"
    if v == "NOT_SENT":
        return "未发送订单"
    if v == "INVALID":
        return "计划不符合要求"
    if v in {"PRECHECK_FAIL", "SERVER_REJECTED", "ORDER_SEND_ERROR"}:
        return "执行异常"
    if v == "STALE":
        return "机会已过期"
    return str(value or "") or "未执行"


def _trade_source_label(row: dict[str, Any]) -> str:
    magic = str(row.get("magic") or "").strip()
    if magic == "2026072902":
        return "AI单"
    if magic == "2026072901":
        return "EA单"
    if magic:
        return "人工单"
    return ""


def _trade_group(ownership: dict[str, str], row: dict[str, Any]) -> str:
    """Classify one trade row. Ownership (MT5 open-deal magic) wins, then magic."""
    position_id = str(row.get("position_id") or row.get("trade_id") or "").strip()
    if position_id and position_id in ownership:
        return ownership[position_id]
    magic = str(row.get("magic") or "").strip()
    if magic == "2026072902":
        return "ai"
    if magic == "2026072903":
        return "e2e"
    return "ea"


def _render_trade(
    row: dict[str, Any],
    index: int = 1,
    candidate: dict[str, Any] | None = None,
) -> str:
    position_id = str(row.get("position_id") or row.get("trade_id") or "").strip()
    direction = str(row.get("direction") or "—").upper()
    status = str(row.get("status") or "")
    is_open = status == "open_at_day_end"
    identity = f"#{position_id}" if position_id else "未记录交易编号"
    source = _trade_source_label(row)
    source_prefix = f"{source}｜" if source else ""
    lines = [f"**实际成交#{index}｜{source_prefix}交易{identity}｜{direction}｜{_status_text(status)}**"]
    lines.append(
        f"开仓时间：{_time(row.get('open_time'))}｜"
        f"平仓时间：{_time(row.get('close_time')) if status.startswith('closed') else '—'}"
    )
    lines.append(
        f"成交价：{_number(row.get('entry_price'))}｜"
        f"手数：{_number(row.get('initial_volume'))}"
    )
    initial_risk = _float(row.get("initial_risk"))
    if initial_risk is not None and initial_risk > 0:
        lines.append(
            f"止损：{_number(row.get('initial_sl'))}｜"
            f"初始风险：{initial_risk:.2f} USD（1R）"
        )
    else:
        lines.append(f"止损：{_number(row.get('initial_sl'))}｜初始风险：无法计算")

    tp1 = _float(row.get("tp1_price"))
    tp2 = _float(row.get("tp2_price"))
    if tp1 is not None and tp1 > 0:
        lines.append(f"第一目标：{tp1:.2f}｜1R目标｜**{_done(row.get('tp1_done'))}**")
    if tp2 is not None and tp2 > 0:
        lines.append(f"第二目标：{tp2:.2f}｜2R目标｜**{_done(row.get('tp2_done'))}**")
    has_target_prices = (tp1 is not None and tp1 > 0) or (tp2 is not None and tp2 > 0)
    if not has_target_prices:
        lines.append(
            f"TP1：**{_done(row.get('tp1_done'))}**｜TP2：**{_done(row.get('tp2_done'))}**｜"
            f"剩余仓位：**{_runner_text(row.get('runner_done'), is_open)}**"
        )
    else:
        lines.append(f"剩余仓位：**{_runner_text(row.get('runner_done'), is_open)}**")

    if candidate:
        trade_facts = dict(candidate.get("entry_facts") or {})
        trade_facts["direction"] = direction
        if _float(row.get("tp1_price")) and _float(row.get("tp1_price")) > 0:
            trade_facts["tp1_price"] = _float(row.get("tp1_price"))
        if _float(row.get("tp2_price")) and _float(row.get("tp2_price")) > 0:
            trade_facts["tp2_price"] = _float(row.get("tp2_price"))
        route_label, reason = _entry_explanation(
            candidate, direction, filled=True, facts_override=trade_facts
        )
        lines.append(f"**入场路径：** {route_label}")
        reason_lines = [
            f"- {part.strip().rstrip('。')}"
            for part in reason.split("；")
            if part.strip()
        ]
        lines.append("**开仓理由：**\n" + "\n".join(reason_lines))

    whole_net = _float(row.get("whole_trade_net", row.get("net_profit", 0))) or 0.0
    today_net = _float(row.get("today_net", row.get("net_profit", whole_net))) or 0.0
    if status == "closed_cross_day":
        lines.append(
            f"今日实现：**{today_net:+.2f} USD**｜整笔累计：**{whole_net:+.2f} USD**"
        )
    elif is_open:
        lines.append(f"截至收盘已实现：**{today_net:+.2f} USD**（剩余持仓未结算）")
        floating_profit = _float(row.get("floating_profit"))
        if floating_profit is None:
            lines.append("剩余持仓浮动盈亏：未记录")
        else:
            lines.append(f"剩余持仓浮动盈亏：**{floating_profit:+.2f} USD**")
    else:
        lines.append(f"{_trade_profit_label(whole_net)}：**{whole_net:+.2f} USD**")

    max_fav = _float(row.get("max_favorable_r"))
    if max_fav is not None:
        lines.append(f"最大顺向：**{max(max_fav, 0.0):+.2f}R**")
    mfe = _float(row.get("mfe_r"))
    mae = _float(row.get("mae_r"))
    if mfe is not None or mae is not None:
        mfe_text = f"开仓后最大浮盈：**{mfe:+.2f}R**" if mfe is not None else ""
        mae_text = f"开仓后最大浮亏：**{mae:+.2f}R**" if mae is not None else ""
        lines.append("｜".join(part for part in (mfe_text, mae_text) if part))

    final_r = _float(row.get("final_r"))
    if is_open:
        floating_profit = _float(row.get("floating_profit"))
        if floating_profit is not None and initial_risk is not None and initial_risk > 0:
            current_r = (today_net + floating_profit) / initial_risk
            lines.append(
                f"未结算｜已实现加持仓浮动约 **{current_r:+.2f}R**（1R≈{initial_risk:.2f} USD）"
            )
        else:
            lines.append("未结算｜当前R：无法计算（缺少收盘浮动盈亏）")
    elif final_r is None or initial_risk is None or initial_risk <= 0:
        lines.append("整笔收益：无法计算")
    else:
        lines.append(f"整笔收益：**{final_r:+.2f}R**（1R≈{initial_risk:.2f} USD）")

    close_reason = str(row.get("close_reason") or "").strip()
    if close_reason:
        lines.append(f"平仓方式：**{_close_exit_text(row)}**")
    return "\n".join(lines)


def _render_ai_review_effect(
    candidates_rows: list[dict[str, Any]],
    unfilled_items: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    allowed_total: int | None = None,
    rejected_total: int | None = None,
    error_total: int | None = None,
) -> str:
    """Deterministic allow/reject outcome summary (no standalone title)."""
    allowed = [row for row in candidates_rows if row.get("ai_status") == "allow"]
    rejected = [row for row in candidates_rows if row.get("ai_status") == "reject"]
    error = [row for row in candidates_rows if row.get("ai_status") == "error"]

    allowed_filled = [row for row in allowed if row.get("outcome") == "filled"]
    allowed_unfilled = [row for row in allowed if row.get("outcome") != "filled"]
    position_ids = {str(row.get("position_id") or "") for row in allowed_filled if row.get("position_id")}
    wins = losses = 0
    for trade in trades:
        if str(trade.get("position_id") or "") in position_ids:
            net = _float(trade.get("whole_trade_net", trade.get("net_profit", 0))) or 0.0
            if net > 0:
                wins += 1
            elif net < 0:
                losses += 1

    unfilled_by_signal = {
        str(item.get("signal_id") or ""): item for item in unfilled_items if item.get("signal_id")
    }
    unfilled_by_sequence = {_int(item.get("sequence")): item for item in unfilled_items}

    def classification_for(row: dict[str, Any]) -> str:
        item = unfilled_by_signal.get(str(row.get("signal_id") or "")) or unfilled_by_sequence.get(_int(row.get("sequence")))
        return _summary_judgment(item or {})

    allowed_unfilled_counts: dict[str, int] = {}
    for row in allowed_unfilled:
        cls = classification_for(row)
        allowed_unfilled_counts[cls] = allowed_unfilled_counts.get(cls, 0) + 1
    rejected_counts: dict[str, int] = {}
    for row in rejected:
        cls = classification_for(row)
        rejected_counts[cls] = rejected_counts.get(cls, 0) + 1

    allowed_count = len(allowed) if allowed_total is None else allowed_total
    rejected_count = len(rejected) if rejected_total is None else rejected_total
    error_count = len(error) if error_total is None else error_total
    lines = [f"**AI允许：** {allowed_count}个"]
    if allowed_filled:
        lines.append(f"- 已成交：{len(allowed_filled)}个｜{wins}盈{losses}亏")
    if allowed_unfilled:
        lines.append(f"- 未成交：{len(allowed_unfilled)}个")
        reason_counts: dict[str, int] = {}
        for row in allowed_unfilled:
            reason = format_candidate_no_fill_reason(row)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if reason_counts:
            for reason, count in sorted(reason_counts.items()):
                lines.append(f"  - {reason}：{count}个")
        if allowed_unfilled_counts:
            detail = "、".join(f"{k} {v}个" for k, v in sorted(allowed_unfilled_counts.items()))
            lines.append(f"- 事后走势：{detail}")
    lines.append(f"**AI拒绝：** {rejected_count}个")
    if rejected_counts:
        for name, count in sorted(rejected_counts.items()):
            lines.append(f"- {name}：{count}个")
    else:
        lines.append("- 暂无明确结果")
    if error_count:
        grace = sum(1 for row in error if row.get("ai_allow_trade"))
        blocked = error_count - grace
        parts = []
        if blocked:
            parts.append(f"拦截 {blocked}次")
        if grace:
            parts.append(f"降级放行 {grace}次")
        lines.append(f"**AI异常：** {error_count}个")
        lines.append("- " + ("、".join(parts) if parts else "已记录"))
    return "\n".join(lines)


def _render_market_review_section(payload: dict[str, Any], response: dict[str, Any]) -> str:
    """第 2 段：当日行情回顾 + 该开单的地方有没有开出来（事实在前，AI解释在后）。"""
    from tools.daily_market_review import render_market_review_lines

    review = payload.get("market_review") or {}
    playbook = dict(response.get("market_playbook") or {})
    trade_count = (payload.get("statistics") or {}).get("trade_count")
    lines = render_market_review_lines(
        review, actual_trade_count=_int(trade_count) if trade_count is not None else None
    ) if review else [
        "## 2. 今日行情回顾与开单准确性",
        "",
        "当日行情回顾数据不足，暂不做行情性质与开单准确性判断。",
    ]
    windows = payload.get("four_hour_reviews") or []
    if windows:
        lines.extend(["", "**分时段行情与参与情况（MT5服务器时间）：**"])
        for window in windows:
            start = str(window.get("start") or "")[11:16]
            end = str(window.get("end") or "")[11:16]
            metrics = window.get("metrics") or {}
            segment = window.get("key_trend") or {}
            if not metrics.get("available"):
                lines.append(f"- {start}–{end}：行情数据不足，暂不判断。")
                continue
            tail = "（收盘尾段，只纳入日报）" if window.get("daily_only") else ""
            data_note = "（数据部分缺失）" if window.get("data_status") != "完整" else ""
            trend_text = (
                f"；内含{segment['start'][11:16]}–{segment['end'][11:16]}"
                f"{segment['direction']}{segment['net_move_usd']:.2f}美元的强趋势段"
                if segment else ""
            )
            direction_text = (
                f"；该趋势段顺势成交{window.get('trend_aligned_fills', 0)}笔、"
                f"逆势成交{window.get('trend_opposite_fills', 0)}笔"
                if segment else ""
            )
            lines.append(
                f"- {start}–{end}{tail}{data_note}：整体{window['trend']['label']}，"
                f"{metrics['direction']}{metrics['net_move_usd']:.2f}美元{trend_text}；"
                f"候选{window.get('formal_candidates', 0)}，成交{window.get('filled_trades', 0)}，"
                f"{window.get('participation', '证据不足')}{direction_text}。"
            )
            pro_observation = str((window.get("pro_analysis") or {}).get("observation") or "").strip()
            if pro_observation:
                lines.append(f"  - Pro观察：{pro_observation}")
    ai_bits: list[str] = []
    for key, label in (
        ("trend_verdict", "行情性质说明"),
        ("entry_accuracy", "该开单位置的执行情况"),
        ("missing_reason", "没开出来的原因"),
        ("actionable_note", "观察建议"),
    ):
        text = str(playbook.get(key) or "").strip()
        if text:
            ai_bits.append(f"**{label}：** {text}")
    lines.append("")
    if ai_bits:
        lines.extend(ai_bits)
    else:
        lines.append("**AI分析：** 暂不可用。")
    return "\n".join(lines)


def build_daily_review_sections(
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    api_counts: dict[str, int] | None = None,
    daily_limit: int = 1,
    monitor_limit: int = 29,
) -> list[str]:
    stats = dict(payload.get("statistics") or response.get("trade_statistics") or {})
    metrics = dict(payload.get("market_metrics") or {})
    trades = [dict(row) for row in payload.get("actual_trade_rows") or []]
    ownership = dict(payload.get("trade_ownership") or {})
    # EA主卡只保留正式 EA Trader A，彻底排除 Parallel AI / 测试 / 未知来源。
    trades = [row for row in trades if _trade_group(ownership, row) == "ea"]
    candidates_rows = [dict(row) for row in payload.get("candidate_rows") or []]
    market_section = "\n".join(
        [
            "## 1. 今日行情",
            "",
            f"**开 / 高 / 低 / 收：** {_number(metrics.get('open'))} / "
            f"{_number(metrics.get('high'))} / {_number(metrics.get('low'))} / "
            f"{_number(metrics.get('close'))}",
            f"**全天振幅：** {_number(metrics.get('range_usd'))} USD",
        ]
    )
    market_review_section = _render_market_review_section(payload, response)

    candidates = len(candidates_rows) if candidates_rows else _int(stats.get("candidate_count"))
    if candidates_rows:
        allowed = sum(row.get("ai_status") == "allow" for row in candidates_rows)
        rejected = sum(row.get("ai_status") == "reject" for row in candidates_rows)
        error = sum(row.get("ai_status") == "error" for row in candidates_rows)
        grace_error = sum(
            row.get("ai_status") == "error" and row.get("ai_allow_trade")
            for row in candidates_rows
        )
        blocked_error = error - grace_error
        undecided = sum(row.get("ai_status") == "undecided" for row in candidates_rows)
        candidate_fills = sum(row.get("outcome") == "filled" for row in candidates_rows)
        untriggered = candidates - candidate_fills
    else:
        allowed = _int(stats.get("ai_allow_count"))
        rejected = _int(stats.get("ai_reject_count"))
        error = _int(stats.get("ai_error_count"))
        grace_error = None
        blocked_error = None
        undecided = max(candidates - allowed - rejected - error, 0)
        untriggered = max(candidates - _int(stats.get("trade_count")), 0)
    filled = len(trades) if trades else _int(stats.get("trade_count"))
    closed_rows = [
        row for row in trades if str(row.get("status") or "").startswith("closed")
    ]
    open_count = sum(row.get("status") == "open_at_day_end" for row in trades)
    closed_profits = [
        _float(row.get("whole_trade_net", row.get("net_profit", 0))) or 0.0
        for row in closed_rows
    ]
    profits = [value for value in closed_profits if value > 0]
    losses = [value for value in closed_profits if value < 0]
    win_rate = (len(profits) / len(closed_profits) * 100.0) if closed_profits else None
    if closed_profits and losses:
        average_profit = sum(profits) / len(profits) if profits else None
        average_loss = sum(losses) / len(losses) if losses else None
        profit_factor = sum(profits) / abs(sum(losses)) if profits else 0.0
        payoff = (
            average_profit / abs(average_loss)
            if average_profit is not None and average_loss
            else 0.0
        )
        profit_factor_text = f"{profit_factor:.2f}"
        payoff_text = f"{payoff:.2f} : 1"
    else:
        profit_factor_text = "—"
        payoff_text = "—"

    r_values: list[float] = []
    for row in closed_rows:
        final_r = _float(row.get("final_r"))
        initial_risk = _float(row.get("initial_risk"))
        if final_r is not None and initial_risk is not None and initial_risk > 0:
            r_values.append(final_r)
    total_r_text = f"{_signed(sum(r_values))}R" if r_values else "—"
    average_r_text = f"{_signed(sum(r_values) / len(r_values))}R" if r_values else "—"
    # EA主卡净盈亏只用已过滤的EA交易（含持仓部分已实现），不能复用混入AI的整体 statistics。
    realized = [
        _float(row.get("today_net", row.get("net_profit", 0))) or 0.0
        for row in trades
    ]
    net_profit = sum(realized) if realized else _float(stats.get("net_profit"))
    net_profit_text = _signed(net_profit, " USD") if net_profit is not None else "—"

    win_rate_text = f"{win_rate:.2f}%" if win_rate is not None else "—"
    unfilled_review_early = payload.get("unfilled_candidate_review") or {}
    unfilled_items_early = [dict(row) for row in unfilled_review_early.get("items") or []]
    trade_lines = [
                "## 3. 今日交易",
        "",
        f"**当日开单：** {filled}笔",
        f"**盈利 / 亏损：** {len(profits)} / {len(losses)}",
        f"**胜率：** {win_rate_text}",
        f"**已实现净盈亏：** **{net_profit_text}**",
        "",
        f"**盈利因子：** {profit_factor_text}",
        f"**平均盈亏比：** {payoff_text}",
        f"**全日总R：** **{total_r_text}**｜**平均R：** {average_r_text}",
        "",
        f"**正式候选：** {candidates}｜**实际成交：** {filled}｜**未成交：** {untriggered}",
        "",
        _render_ai_review_effect(
            candidates_rows,
            unfilled_items_early,
            trades,
            allowed_total=allowed,
            rejected_total=rejected,
            error_total=error,
        ),
        "",
        "**【🔵 实际成交逐笔明细】**",
        "",
    ]
    if trades:
        candidate_by_position = {
            str(row.get("position_id") or ""): row
            for row in candidates_rows
            if row.get("position_id")
        }
        trade_lines.append(
            ("\n\n" + _VISUAL_DIVIDER + "\n\n").join(
                _render_trade(
                    row,
                    index,
                    candidate_by_position.get(str(row.get("position_id") or "")),
                )
                for index, row in enumerate(trades, start=1)
            )
        )
    else:
        trade_lines.append("本交易日没有实际成交。")
    trade_section = "\n".join(trade_lines)

    unfilled_review = payload.get("unfilled_candidate_review") or {}
    missed_summary = payload.get("missed_candidate_summary") or {}
    unfilled_items = [dict(row) for row in unfilled_review.get("items") or []]
    validation_met = bool(missed_summary.get("validation_met"))

    issue_number = 4
    issue_lines = [f"## {issue_number}. 今日问题", ""]

    program_issues = list(payload.get("program_issues") or [])
    unclear_unfilled = [
        it for it in unfilled_items
        if str(it.get("reason_code") or "") != "AI拒绝"
        and "暂未定位" in str(it.get("reason_text") or "")
    ]

    # 【策略观察】
    issue_lines.append("**【策略观察】**")
    if filled:
        if losses and not profits:
            issue_lines.append(
                f"当日EA成交{filled}笔，{len(profits)}盈{len(losses)}亏；单日样本不足以确认策略缺陷，继续累计观察。"
            )
        else:
            issue_lines.append(f"当日EA成交{filled}笔，{len(profits)}盈{len(losses)}亏。")
    else:
        issue_lines.append("当日无实际成交。")

    # 计算 AI 放行/拒绝统计（供下一步和 DeepSeek Pro 上下文使用，不单独成栏）
    ai_allow_filled = [
        row for row in candidates_rows
        if row.get("ai_status") == "allow" and row.get("outcome") == "filled"
    ]
    ai_allow_positions = {str(row.get("position_id") or "") for row in ai_allow_filled if row.get("position_id")}
    ai_wins = ai_losses = 0
    for trade in trades:
        if str(trade.get("position_id") or "") in ai_allow_positions:
            net = _float(trade.get("whole_trade_net", trade.get("net_profit", 0))) or 0.0
            if net > 0:
                ai_wins += 1
            elif net < 0:
                ai_losses += 1
    ai_reject_miss = [
        it for it in unfilled_items
        if str(it.get("reason_code") or "") == "AI拒绝"
        and _display_judgment(it) == "AI拒绝后错过机会"
    ]

    # 【程序问题】
    issue_lines.append("")
    issue_lines.append("**【程序问题】**")
    program_lines = list(program_issues)
    for trade in trades:
        inconsistency = str(trade.get("data_inconsistency") or "").strip()
        if inconsistency:
            program_lines.append(f"交易#{trade.get('position_id')}｜数据一致性异常｜{inconsistency}")
    for it in unclear_unfilled:
        seq = _int(it.get("sequence"))
        direction = str(it.get("direction") or "—").upper()
        program_lines.append(
            f"候选#{seq}｜{direction}｜未成交原因未定位，需确认挂单最终是未触价、失效撤单还是其他执行原因。"
        )
    if program_lines:
        issue_lines.extend(f"- {line}" for line in program_lines)
    else:
        issue_lines.append("当日未发现明确程序异常。")
    issue_section = "\n".join(issue_lines)

    counts = api_counts or {}
    conclusion_lines = [f"## {issue_number + 1}. 下一步", ""]
    next_lines = ["1. 暂不修改交易参数（Fib、ATR、G01-G10等），继续累计样本观察。"]
    step = 2
    if validation_met:
        next_lines.append(f"{step}. 同类未成交问题已达到专项验证条件，由人工决定是否启动专项验证。")
        step += 1
    for line in program_lines:
        next_lines.append(f"{step}. {line}")
        step += 1
    if ai_allow_filled or ai_reject_miss:
        next_lines.append(f"{step}. 继续累计AI允许/拒绝后的实际表现，暂不根据单日结果修改DeepSeek审核规则。")
        step += 1
    conclusion_lines.extend(next_lines)
    conclusion_lines.extend(
        [
            "",
            f"**API使用：** 盯盘 {_int(counts.get('monitor'))} / {monitor_limit}｜日报 {_int(counts.get('daily'))} / {daily_limit}",
            "**安全校验：** EA复盘",
        ]
    )
    summary_section = "\n".join(conclusion_lines)
    sections = [market_section, market_review_section, trade_section]
    sections.extend([issue_section, summary_section])
    return sections


def build_comparison_card_markdown(
    review_day: str, payload: dict[str, Any], response: dict[str, Any],
) -> str:
    """卡3：EA / Parallel AI / 人工三组对照，只做横向比较。"""
    groups = payload.get("trade_groups") or {}
    lines: list[str] = []
    if isinstance(groups, dict) and any(
        _int(groups.get(key, {}).get(metric)) > 0
        for key in ("ea", "ai", "manual", "unknown")
        for metric in ("count", "open")
    ):
        for key, label in (
            ("ea", "EA"),
            ("ai", "Parallel AI"),
            ("manual", "人工"),
            ("unknown", "未归类"),
        ):
            group = groups.get(key) or {}
            count = _int(group.get("count"))
            win = _int(group.get("win"))
            loss = _int(group.get("loss"))
            net = float(group.get("net") or 0.0)
            open_count = _int(group.get("open"))
            if count == 0 and open_count == 0:
                lines.append(f"**{label}：** 0笔")
                continue
            count_text = (
                f"已平仓 {count}笔 + 持仓 {open_count}笔"
                if open_count else f"已平仓 {count}笔"
            )
            line = f"**{label}：** {count_text}｜{win}盈{loss}亏｜净盈亏 {net:+.2f} USD"
            if key == "ai":
                samples = _int(group.get("r_samples"))
                average_r = f"{float(group.get('r_total') or 0.0) / samples:+.2f}R" if samples else "—"
                line += f"｜平均R {average_r}"
            lines.append(line)
        lines.append("")
        lines.append("（优先依据EA生命周期确认EA归属，其他交易按订单魔术号归类；退出规则一致，便于横向对比。）")
        facts = [dict(f) for f in (payload.get("trade_review_facts") or [])]
        review = response.get("trade_review")
        if isinstance(review, dict) and review.get("per_trade_reviews"):
            lines.append("")
            lines.append(_render_trade_review(facts, review))
        else:
            deep_parts: list[str] = []
            for text in (
                response.get("trade_group_analysis"),
                response.get("issue_judgment"),
                response.get("conclusion_summary"),
            ):
                cleaned = str(text or "").strip()
                if cleaned and cleaned not in deep_parts:
                    deep_parts.append(cleaned)
            if deep_parts:
                lines.extend(["", "**【DeepSeek Pro深度分析】**"])
                lines.extend(deep_parts)
    else:
        lines.append("今日无正式EA/AI/人工交易。")
    lines.append("")
    lines.append("**安全校验：** EA复盘")
    return "\n".join(lines)


def _render_trade_review(
    facts: list[dict[str, Any]], review: dict[str, Any],
) -> str:
    """渲染三组对照卡的逐笔点评 + 分组总结 + 综合结论 + 对EA策略启示。"""
    lines: list[str] = []
    reviews = review.get("per_trade_reviews") or []
    review_by_id = {
        str(r.get("position_id") or ""): str(r.get("review") or "")
        for r in reviews if isinstance(r, dict)
    }
    if reviews:
        lines.append("## 二、逐笔交易点评")
        lines.append("")
        for fact in facts:
            pid = str(fact.get("position_id") or "")
            direction = str(fact.get("direction") or "—").upper()
            net = fact.get("net_profit_usd")
            r_value = fact.get("final_r")
            net_text = f"{net:+.2f} USD" if net is not None else "—"
            r_text = f"{r_value:+.2f}R" if r_value is not None else "—"
            header = (
                f"**【交易#{pid}｜{fact.get('trader_group')}｜{direction}｜"
                f"{net_text}｜{r_text}】**"
            )
            body = review_by_id.get(pid, "")
            lines.append(header)
            lines.append(body)
            lines.append("")
    lines.append("## 三、分组总结")
    lines.append("")
    lines.append(f"**EA组：** {review.get('group_ea') or ''}")
    lines.append(f"**Parallel AI组：** {review.get('group_ai') or ''}")
    lines.append(f"**人工组：** {review.get('group_manual') or ''}")
    lines.append("")
    lines.append("## 四、DeepSeek Pro综合结论")
    lines.append("")
    lines.append(review.get("overall") or "")
    lines.append("")
    lines.append("## 五、对EA策略的启示")
    lines.append("")
    for label, key in (
        ("已有规则得到支持", "insights_supported"),
        ("需要继续观察", "insights_observe"),
        ("暂不建议修改", "insights_avoid"),
    ):
        text = str(review.get(key) or "").strip()
        if text and text != "无":
            lines.append(f"【{label}】")
            lines.append(text)
            lines.append("")
    return "\n".join(lines)


def build_parallel_ai_card_markdown(
    review_day: str, payload: dict[str, Any], response: dict[str, Any],
) -> str:
    """卡2：Parallel AI 每日复盘，只统计 Trader B（magic 2026072902）。"""
    ai_candidates = [dict(row) for row in (payload.get("ai_candidate_rows") or [])]
    ai_trades = [dict(row) for row in (payload.get("ai_trade_rows") or [])]
    ai_details = [dict(row) for row in (payload.get("ai_trade_details") or [])]

    total_open = len(ai_candidates)
    plan_invalid = sum(
        1 for row in ai_candidates
        if str(row.get("plan_status") or "").upper() == "INVALID"
    )
    expired = sum(
        1 for row in ai_candidates
        if str(row.get("final_state") or "").upper() in {"EXPIRED", "CANCELLED", "STALE"}
        or str(row.get("execution_status") or "").upper() == "STALE"
        or str(row.get("plan_status") or "").upper() == "STALE"
    )
    rule_blocked = sum(
        1 for row in ai_candidates
        if str(row.get("final_state") or "").upper() == "RULE_BLOCKED"
        or str(row.get("execution_status") or "").upper() == "NOT_SENT"
    )
    low_confidence = [
        row for row in ai_candidates if _int(row.get("confidence")) < 50
    ]

    closed_trades = [t for t in ai_trades if str(t.get("status") or "").startswith("closed")]
    open_trades = [t for t in ai_trades if str(t.get("status") or "") == "open_at_day_end"]
    wins = [t for t in closed_trades if _float(t.get("whole_trade_net")) > 0]
    losses = [t for t in closed_trades if _float(t.get("whole_trade_net")) < 0]
    realized = sum(_float(t.get("whole_trade_net")) or 0.0 for t in closed_trades)

    lines = ["## 1. 今日AI判断", ""]
    lines.append(f"**AI想开仓：** {total_open}个")
    lines.append(f"**计划不符合下单要求：** {plan_invalid}个")
    lines.append(f"**机会已过期：** {expired}个")
    lines.append(f"**规则检查拦截：** {rule_blocked}个")
    lines.append(f"**低信心开仓判断：** {len(low_confidence)}个")
    for row in low_confidence[:5]:
        lines.append(f"- {row.get('signal_id')}｜信心 {_int(row.get('confidence'))}")
    lines.append("")
    lines.extend(["## 2. 今日AI实际交易", ""])
    lines.append(f"**已平仓：** {len(closed_trades)}笔｜**持仓中：** {len(open_trades)}笔")
    if closed_trades:
        lines.append(f"**盈利 / 亏损：** {len(wins)} / {len(losses)}")
        lines.append(f"**已实现净盈亏：** {realized:+.2f} USD")
    else:
        lines.append("今日无已平仓AI交易。")

    if ai_trades:
        lines.append("")
        lines.append("**【AI实际成交明细】**")
        lines.append("")
        lines.append(
            ("\n\n" + _VISUAL_DIVIDER + "\n\n").join(
                _render_trade(row, index) for index, row in enumerate(ai_trades, start=1)
            )
        )

    lines.append("")
    lines.append("**安全校验：** EA复盘")
    return "\n".join(lines)


def render_daily_review_markdown(
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    api_counts: dict[str, int] | None = None,
    backfill: bool = False,
    daily_limit: int = 1,
    monitor_limit: int = 29,
) -> str:
    sections = build_daily_review_sections(
        review_day,
        payload,
        response,
        api_counts,
        daily_limit=daily_limit,
        monitor_limit=monitor_limit,
    )
    title = f"# 每日复盘（补发）｜{review_day}" if backfill else f"# 每日复盘｜{review_day}"
    return title + "\n\n" + "\n\n".join(sections) + "\n"


def _ai_candidate_is_rule_blocked(row: dict[str, Any]) -> bool:
    final_state = str(row.get("final_state") or "").upper()
    execution_status = str(row.get("execution_status") or "").upper()
    rule_compliance = str(row.get("rule_compliance") or "").upper()
    return (
        final_state == "RULE_BLOCKED"
        or execution_status == "NOT_SENT"
        or rule_compliance == "BLOCKED"
    )


def _display_ai_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """候选明细里只展示未被规则拦截的 AI 机会；规则拦截不展示。"""
    return [
        dict(row)
        for row in (payload.get("ai_candidate_rows") or [])
        if not _ai_candidate_is_rule_blocked(row)
    ]


def _render_ai_candidates_section(payload: dict[str, Any]) -> str:
    """AI候选段：只展示未被规则拦截的AI机会。"""
    ai_candidates = _display_ai_candidates(payload)
    if not ai_candidates:
        return ""
    important = [
        row for row in ai_candidates
        if str(row.get("plan_status") or "").upper() == "INVALID"
        or str(row.get("execution_status") or "").upper() in {
            "PRECHECK_FAIL", "SERVER_REJECTED", "ORDER_SEND_ERROR", "EXECUTION_ERROR",
        }
        or _int(row.get("confidence")) < 50
    ]
    normal = len(ai_candidates) - len(important)
    lines = ["## Parallel AI候选（Trader B）", ""]
    if important:
        lines.append(
            ("\n\n" + _VISUAL_DIVIDER + "\n\n").join(
                _render_ai_candidate_block(row) for row in important
            )
        )
    else:
        lines.append("今日无规则拦截 / 计划异常 / 低信心的AI机会。")
    if normal > 0:
        lines.append("")
        lines.append(f"其余正常未成交/过期AI机会：{normal}个（完整明细见CSV/HTML开发文件）。")
    return "\n".join(lines)


def render_candidate_card_markdown(
    review_day: str,
    payload: dict[str, Any],
    api_counts: dict[str, int] | None = None,
    include_title: bool = True,
    backfill: bool = False,
) -> str:
    """Render the second Lark card: formal candidate order details.

    include_title=False 用于Lark卡片正文：卡片header已显示标题，正文不再重复。
    """
    rows = [dict(row) for row in payload.get("candidate_rows") or []]
    title_text = (
        f"# 候选订单明细（补发）｜{review_day}"
        if backfill
        else f"# 候选订单明细｜{review_day}"
    )
    title = title_text + "\n\n" if include_title else ""
    ai_candidates = _display_ai_candidates(payload)
    if not rows and not ai_candidates:
        return title + "今日没有形成正式候选，因此不发送本卡片。\n"

    sections: list[str] = []
    unfilled_items = {
        str(item.get("signal_id") or ""): item
        for item in (payload.get("unfilled_candidate_review") or {}).get("items") or []
        if item.get("signal_id")
    }
    unfilled_by_sequence = {
        _int(item.get("sequence")): item
        for item in (payload.get("unfilled_candidate_review") or {}).get("items") or []
    }
    if rows:
        blocks: list[str] = []
        for row in rows:
            signal_id = str(row.get("signal_id") or "")
            unfilled_item = unfilled_items.get(signal_id)
            if unfilled_item is None:
                unfilled_item = unfilled_by_sequence.get(_int(row.get("sequence")))
            blocks.append(_render_candidate_card_block(row, unfilled_item))
        sections.append(("\n\n" + _VISUAL_DIVIDER + "\n\n").join(blocks))
    ai_section = _render_ai_candidates_section(payload)
    if ai_section:
        sections.append(ai_section)
    body = ("\n\n" + _VISUAL_DIVIDER + "\n\n").join(sections)

    total = len(rows)
    allowed = sum(row.get("ai_status") == "allow" for row in rows)
    rejected = sum(row.get("ai_status") == "reject" for row in rows)
    filled_count = sum(row.get("outcome") == "filled" for row in rows)
    unfilled_count = total - filled_count
    summary_lines = ["## 今日候选汇总", ""]
    summary_lines.append(f"**正式候选：** {total}个")
    if ai_candidates:
        summary_lines.append(f"**Parallel AI候选：** {len(ai_candidates)}个")
    if rows:
        summary_lines.append(f"**AI允许：** {allowed}个｜**AI拒绝：** {rejected}个")
        summary_lines.append(f"**已成交：** {filled_count}个｜**未成交：** {unfilled_count}个")
    summary_lines.append("")
    for row in rows:
        number = _int(row.get("sequence"))
        direction = str(row.get("direction") or "—").upper()
        if str(row.get("outcome") or "") == "filled":
            position_id = str(row.get("position_id") or "").strip()
            suffix = f"｜交易#{position_id}" if position_id else ""
            summary_lines.append(f"候选#{number}｜{direction}｜已成交{suffix}")
        else:
            signal_id = str(row.get("signal_id") or "")
            item = unfilled_items.get(signal_id) or unfilled_by_sequence.get(number)
            judgment = _summary_judgment(item or {})
            if judgment:
                summary_lines.append(f"候选#{number}｜{direction}｜未成交｜{judgment}")
            else:
                summary_lines.append(f"候选#{number}｜{direction}｜未成交")
    return title + body + "\n\n" + _VISUAL_DIVIDER + "\n\n" + "\n".join(summary_lines) + "\n"
