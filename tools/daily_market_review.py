"""每日「行情回顾 + 开单准确性」的确定性事实层（只读，不调用 AI）。

设计原则（与项目其他复盘一致）：

* 所有数字都由 Python 从 EA 已写出的冻结数据算出，DeepSeek 只能“解释”，
  不能自己推断数字。
* “该开单的地方”以**平行 AI 的独立参考判定**为准（每根 M5 一条记录，
  reference_decision.action == OPEN），再和 EA 实际结果对照。
* “为什么没开出来”优先引用 EA 自己写下的扫描原因，并翻译成中文大白话，
  不向用户暴露程序状态码。

数据来源：
    Raw_Data/Market_Snapshots_<日期>.csv        当日 M5 行情 + EA 扫描结论
    Parallel_AI_V2/Records/Comparisons_*.jsonl  每根 M5 的 参考/EA/AI 三方结论
    Daily_Review/<日期>/Daily_Candidates_*.csv  当日候选与未成交原因（可选）
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BEIJING_TZ = timezone(timedelta(hours=8))

# 判定口径（写进卡片，方便人工复核与将来调整）
TREND_STRONG_NET_OVER_RANGE = 0.45
TREND_STRONG_EFFICIENCY = 0.35
TREND_STRONG_SAME_DIRECTION = 0.55
TREND_STRONG_MAX_PULLBACK = 0.45
TREND_RANGE_EFFICIENCY = 0.20
TREND_RANGE_NET_OVER_RANGE = 0.25
MAX_OPPORTUNITY_ROWS = 6
# “最近这波”的观察窗口（M5 根数）：24 根 = 2 小时
RECENT_WINDOW_BARS = 24

# EA 内部原因码 -> 中文大白话（用户可见内容不得出现代码）
REASON_TEXT = {
    "C21_PRIMARY_RANGE": "M15 主结构处于区间状态，没有放行方向",
    "C21_PRIMARY_BULL": "M15 主结构为多头",
    "C21_PRIMARY_BEAR": "M15 主结构为空头",
    "C21_BLOCK_BUY_PRIMARY_BEAR": "M15 主结构为空头，未放行做多",
    "C21_BLOCK_SELL_PRIMARY_BULL": "M15 主结构为多头，未放行做空",
    "C21_LOCAL_EXHAUSTED_BULL": "M15 多头已进入局部衰竭",
    "C21_LOCAL_EXHAUSTED_BEAR": "M15 空头已进入局部衰竭",
    "C21_PRIMARY_TRANSITION": "M15 正处于方向切换期，暂不放行",
    "M15_BLOCK_BULL_CONTEXT": "M15 还不具备多头环境",
    "M15_BLOCK_BEAR_CONTEXT": "M15 还不具备空头环境",
    "M15_ALLOW_BULL_CONTINUATION_PAUSE": "M15 多头中途休整，继续放行",
    "M15_ALLOW_BEAR_CONTINUATION_PAUSE": "M15 空头中途休整，继续放行",
    "PULLBACK_IDLE": "还没有出现符合规则的回调结构",
    "PULLBACK_STARTED": "回调刚开始，等待结构走完",
    "PULLBACK_LOCK_H3": "回调已经走到第 3 次尝试，按规则不再开仓",
    "PULLBACK_LOCK_RANGE_LIKE": "回调走成了横盘，按规则锁定不开仓",
    "H2_SIGNAL_INVALID": "第 2 次回调的信号 K 不合格",
    "H2_SIGNAL_CONFIRMED": "第 2 次回调已确认",
    "PLAN_REJECT_TARGET_SPACE_LT_2R": "结构目标空间不足 2R",
    "PLAN_REJECT_BROKER_STOP_DISTANCE": "止损距离小于券商最小要求",
    "PLAN_REJECT_NONPOSITIVE_R": "止损与入场价方向不成立",
    "PLAN_REJECT_NO_STRUCTURE_STOP": "找不到可用的结构止损位",
    "PLAN_REJECT_IMMEDIATE_M5_RESISTANCE_LT_1R": "前方立刻有压力位，空间不足 1R",
    "PLAN_REJECT_A2_TIGHT_PREVIOUS_BAR_STOP": "第 2 次尝试的止损太近，不可靠",
    "PLAN_VALID": "计划有效",
    "PLAN_ARMED_WAIT_DISTANCE": "计划有效，但距离不足，先等待",
    "NO_ROUTE": "入场路径未成立",
    "LOCATION_INVALID": "价格不在 Fib / EMA / 突破回踩的有效位置",
}

STAGE_TEXT = {
    "trend": "趋势过滤",
    "structure": "结构",
    "fib": "Fib 回调",
    "location": "入场位置",
    "price_action": "K 线形态",
    "swing": "摆动结构",
    "risk": "风控",
    "spread": "点差",
    "exposure": "已有持仓/挂单",
    "ai": "AI 审核",
    "candidate": "候选",
}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _server_day_start(review_day: str) -> datetime:
    day = datetime.strptime(review_day, "%Y-%m-%d")
    return day.replace(tzinfo=BEIJING_TZ) - timedelta(hours=5)


def humanize_reason(text: str) -> str:
    """把 EA 的扫描原因翻译成大白话；未知代码一律丢掉，不外泄。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    parts: list[str] = []
    for chunk in raw.split("|"):
        item = chunk.strip()
        if not item:
            continue
        if "=" in item:
            item = item.split("=", 1)[0].strip()
        if item in REASON_TEXT:
            parts.append(REASON_TEXT[item])
            continue
        if item.upper() in REASON_TEXT:
            parts.append(REASON_TEXT[item.upper()])
            continue
        if item.upper() in STAGE_TEXT:
            continue
        # 已经是中文的原因（EA 或本地生成）直接保留
        if any("\u4e00" <= ch <= "\u9fff" for ch in item):
            parts.append(item)
    deduped: list[str] = []
    for item in parts:
        if item not in deduped:
            deduped.append(item)
    return "；".join(deduped)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def _snapshot_rows(root: Path, review_day: str) -> list[dict[str, str]]:
    folder = root / "Raw_Data"
    if not folder.exists():
        return []
    # 快照文件按北京时间命名；个别情况按服务器日期命名，两者都试。
    day = datetime.strptime(review_day, "%Y-%m-%d") if review_day else None
    candidates = [review_day]
    if day is not None:
        candidates.append((day - timedelta(days=1)).strftime("%Y-%m-%d"))
        candidates.append((day + timedelta(days=1)).strftime("%Y-%m-%d"))
    rows: list[dict[str, str]] = []
    for key in candidates:
        rows.extend(_read_rows(folder / f"Market_Snapshots_{key}.csv"))
    # 只保留属于该北京日的工作时段
    filtered = [
        row for row in rows
        if str(row.get("beijing_time", "")).startswith(review_day)
    ]
    return filtered or rows


def _market_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    bars: list[dict[str, float]] = []
    for row in rows:
        open_ = _float(row.get("m5_open"))
        high = _float(row.get("m5_high"))
        low = _float(row.get("m5_low"))
        close = _float(row.get("m5_close"))
        if None in (open_, high, low, close):
            continue
        if high <= 0 or low <= 0:
            continue
        bars.append({"open": open_, "high": high, "low": low, "close": close})
    if len(bars) < 5:
        return {"bars": len(bars), "available": False}

    opens = bars[0]["open"]
    close = bars[-1]["close"]
    high = max(bar["high"] for bar in bars)
    low = min(bar["low"] for bar in bars)
    rng = high - low
    net = abs(close - opens)
    path = sum(abs(bars[i]["close"] - bars[i - 1]["close"]) for i in range(1, len(bars)))
    efficiency = net / path if path > 0 else 0.0
    net_over_range = net / rng if rng > 0 else 0.0

    direction = "up" if close >= opens else "down"
    same_dir = 0
    for bar in bars:
        if direction == "up" and bar["close"] > bar["open"]:
            same_dir += 1
        elif direction == "down" and bar["close"] < bar["open"]:
            same_dir += 1
    same_dir_ratio = same_dir / len(bars)

    # 与主方向相反的最大回撤，占全天净位移比例
    running_extreme = opens
    max_adverse = 0.0
    for bar in bars:
        if direction == "up":
            running_extreme = max(running_extreme, bar["high"])
            max_adverse = max(max_adverse, running_extreme - bar["low"])
        else:
            running_extreme = min(running_extreme, bar["low"])
            max_adverse = max(max_adverse, bar["high"] - running_extreme)
    pullback_ratio = max_adverse / net if net > 0 else 1.0

    # 当日 M5 ATR（简单平均真实波幅）
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1]["close"] if index else bar["open"]
        true_ranges.append(
            max(
                bar["high"] - bar["low"],
                abs(bar["high"] - previous_close),
                abs(bar["low"] - previous_close),
            )
        )
    atr = sum(true_ranges[-14:]) / min(14, len(true_ranges))
    net_atr = net / atr if atr > 0 else 0.0

    return {
        "available": True,
        "bars": len(bars),
        "open": round(opens, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "close": round(close, 2),
        "range_usd": round(rng, 2),
        "net_move_usd": round(net, 2),
        "direction": "上涨" if direction == "up" else "下跌",
        "net_over_range": round(net_over_range, 3),
        "direction_efficiency": round(efficiency, 3),
        "same_direction_ratio": round(same_dir_ratio, 3),
        "max_pullback_usd": round(max_adverse, 2),
        "max_pullback_ratio": round(pullback_ratio, 3),
        "m5_atr14": round(atr, 2),
        "net_move_atr": round(net_atr, 2),
    }


def classify_trend(metrics: dict[str, Any], scope: str = "全天") -> dict[str, Any]:
    """按固定口径给出“强趋势 / 弱势趋势 / 区间震荡”，并列出依据数字。

    scope 只影响文字表述（全天 / 这波），判定规则完全一致。
    """
    if not metrics.get("available"):
        return {"label": "数据不足", "reason": "当日 M5 数据不足，暂不判定。"}
    strong = (
        float(metrics["net_over_range"]) >= TREND_STRONG_NET_OVER_RANGE
        and float(metrics["direction_efficiency"]) >= TREND_STRONG_EFFICIENCY
        and float(metrics["same_direction_ratio"]) >= TREND_STRONG_SAME_DIRECTION
        and float(metrics["max_pullback_ratio"]) <= TREND_STRONG_MAX_PULLBACK
    )
    ranging = (
        float(metrics["direction_efficiency"]) < TREND_RANGE_EFFICIENCY
        or float(metrics["net_over_range"]) < TREND_RANGE_NET_OVER_RANGE
    )
    label = "强趋势" if strong else ("区间震荡" if ranging else "弱势趋势")
    if float(metrics["net_move_usd"]) >= float(metrics["m5_atr14"]):
        pullback_text = (
            f"最大反向回撤 {metrics['max_pullback_usd']} 美元"
            f"（约净位移的 {metrics['max_pullback_ratio']:.0%}）"
        )
    else:
        pullback_text = (
            f"最大反向回撤 {metrics['max_pullback_usd']} 美元，已经超过{scope}净位移"
        )
    reason = (
        f"{scope}{metrics['direction']} {metrics['net_move_usd']} 美元"
        f"（相当于 {metrics['net_move_atr']} 个 M5 ATR），"
        f"净位移占振幅 {metrics['net_over_range']:.0%}，"
        f"路径效率 {metrics['direction_efficiency']:.2f}，"
        f"同向K线占比 {metrics['same_direction_ratio']:.0%}，"
        f"{pullback_text}。"
    )
    return {
        "label": label,
        "reason": reason,
        "rule": (
            f"强趋势需同时满足：净位移/振幅≥{TREND_STRONG_NET_OVER_RANGE}、"
            f"路径效率≥{TREND_STRONG_EFFICIENCY}、同向K线≥{TREND_STRONG_SAME_DIRECTION}、"
            f"最大反向回撤≤{TREND_STRONG_MAX_PULLBACK}；"
            f"路径效率<{TREND_RANGE_EFFICIENCY} 或 净位移/振幅<{TREND_RANGE_NET_OVER_RANGE} 记为区间震荡。"
        ),
    }


def _comparison_records(root: Path, review_day: str) -> list[dict[str, Any]]:
    folder = root / "Parallel_AI_V2" / "Records"
    if not folder.exists():
        return []
    records: list[dict[str, Any]] = []
    day = datetime.strptime(review_day, "%Y-%m-%d") if review_day else None
    keys = [review_day]
    if day is not None:
        keys.append((day - timedelta(days=1)).strftime("%Y-%m-%d"))
        keys.append((day + timedelta(days=1)).strftime("%Y-%m-%d"))
    for key in keys:
        path = folder / f"Comparisons_{key}.jsonl"
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
        except OSError:
            continue
    return records


def _beijing_day_of(m5_time: str) -> str:
    text = str(m5_time or "").strip().replace(".", "-")
    try:
        server = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    return (server + timedelta(hours=5)).strftime("%Y-%m-%d")


def _candidate_index(candidates: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in candidates or []:
        if not isinstance(row, dict):
            continue
        for key in ("signal_time", "created_time", "server_time", "candidate_time"):
            value = _normalise_time(str(row.get(key) or ""))
            if value:
                index[value] = row
    return index


def _snapshot_index(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        m5_time = _normalise_time(str(row.get("m5_time") or ""))
        if m5_time:
            index[m5_time] = row
    return index


def _normalise_time(value: str) -> str:
    """把 2026.09.14 09:35:00 与 2026-09-14 09:35:00 统一成同一把钥匙。"""
    text = str(value or "").strip().replace(".", "-").replace("/", "-")
    return text[:16]


def _opportunity_reason(
    record: dict[str, Any], snapshot: dict[str, str] | None, candidate: dict[str, Any] | None
) -> str:
    if candidate:
        ai_status = str(candidate.get("ai_status") or "").lower()
        outcome = str(candidate.get("outcome") or "").lower()
        reason = humanize_reason(str(candidate.get("reason") or candidate.get("no_fill_reason") or ""))
        if ai_status == "reject":
            return "被 AI 审核拒绝" + (f"（{reason}）" if reason else "")
        if outcome in {"untriggered", "pending", "expired", "cancelled"}:
            return reason or "挂单未成交"
    if snapshot:
        stage = STAGE_TEXT.get(str(snapshot.get("last_scan_stage") or "").lower(), "")
        reason = humanize_reason(str(snapshot.get("last_scan_reason") or ""))
        if reason:
            return f"{stage}：{reason}" if stage else reason
    rule_audit = record.get("rule_audit") or {}
    if isinstance(rule_audit, dict) and rule_audit.get("reason"):
        return _rule_audit_text(str(rule_audit.get("difference_id") or ""))
    return "EA 未形成候选，原因未记录"


def _gate_name_cn(gate_id: str) -> str:
    try:
        from tools.parallel_ai_calculator import GATE_NAME_CN

        return GATE_NAME_CN.get(str(gate_id).upper(), str(gate_id))
    except Exception:  # noqa: BLE001 - 名称表不可用时退回编号
        return str(gate_id)


def _rule_audit_text(difference_id: str) -> str:
    """独立参考与 EA 逐项不一致时的中文说明（不暴露程序代码）。"""
    gate = str(difference_id or "").strip().upper()
    if not gate:
        return "平行AI与 EA 的逐项判断不同，当日 EA 没有形成候选。"
    return (
        f"平行AI认为这里可以开仓，但 EA 在“{_gate_name_cn(gate)}”这一项判定不同，"
        "因此没有形成候选。"
    )


def build_market_review(
    root: Path,
    review_day: str,
    candidates: list[dict[str, Any]] | None = None,
    supplemental_root: Path | None = None,
    snapshot_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """返回当日行情画像 + “该开单的地方是否开出来”的对照事实。"""
    rows = list(snapshot_rows) if snapshot_rows is not None else _snapshot_rows(root, review_day)
    if snapshot_rows is None and supplemental_root is not None and supplemental_root != root:
        rows += _snapshot_rows(supplemental_root, review_day)
        rows = list({str(row.get("m5_time") or row.get("server_time")): row
                     for row in reversed(rows)}.values())
        rows.sort(key=lambda row: str(row.get("m5_time") or row.get("server_time") or ""))
    metrics = _market_metrics(rows)
    trend = classify_trend(metrics)
    # 全天往往是“震荡”，但最近一两小时可能是一段清晰趋势；
    # 两个口径分开给出，避免用全天结论否定眼前这波行情。
    recent_rows = rows[-RECENT_WINDOW_BARS:] if len(rows) > RECENT_WINDOW_BARS else rows
    recent_metrics = _market_metrics(recent_rows)
    recent_trend = classify_trend(recent_metrics, scope="这波")
    snapshots = _snapshot_index(rows)
    candidates_index = _candidate_index(candidates)
    records = _comparison_records(root, review_day)
    if supplemental_root is not None and supplemental_root != root:
        records += _comparison_records(supplemental_root, review_day)

    opportunities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(records, key=lambda item: str(item.get("m5_time") or "")):
        m5_time = str(record.get("m5_time") or "")
        if not m5_time or _beijing_day_of(m5_time) != review_day:
            continue
        reference = record.get("reference_decision") or {}
        if str(reference.get("action") or "").upper() != "OPEN":
            continue
        key = m5_time[:16]
        if key in seen:
            continue
        seen.add(key)
        ea = record.get("ea") or {}
        ai = record.get("ai") or {}
        candidate = candidates_index.get(key)
        opened = str(ea.get("action") or "").upper() == "OPEN"
        reason = "" if opened else _opportunity_reason(record, snapshots.get(key), candidate)
        opportunities.append(
            {
                "time": m5_time,
                "direction": "做多" if str(reference.get("direction")).upper() == "BUY" else "做空",
                "route": str(reference.get("route") or ""),
                "entry": reference.get("entry"),
                "sl": reference.get("sl"),
                "tp1": reference.get("tp1"),
                "rr": reference.get("rr_to_tp1"),
                "ea_opened": opened,
                "ai_action": str(ai.get("action") or ""),
                "status": "已开出" if opened else "未开出",
                "reason": reason,
            }
        )

    opened_count = sum(1 for item in opportunities if item["ea_opened"])
    return {
        "review_day": review_day,
        "metrics": metrics,
        "trend": trend,
        "metrics_recent": recent_metrics,
        "trend_recent": recent_trend,
        "recent_window_bars": len(recent_rows),
        "opportunities": opportunities[:MAX_OPPORTUNITY_ROWS],
        "opportunity_total": len(opportunities),
        "opportunity_opened": opened_count,
        "opportunity_missed": len(opportunities) - opened_count,
        "records_scanned": len(records),
    }


def render_market_review_lines(
    review: dict[str, Any], actual_trade_count: int | None = None
) -> list[str]:
    """把事实层渲染成日报段落（中文大白话，不含程序代码）。"""
    metrics = review.get("metrics") or {}
    trend = review.get("trend") or {}
    lines: list[str] = ["## 2. 今日行情回顾与开单准确性", ""]
    if actual_trade_count is not None:
        lines.append(f"**EA当日实际开单：** {actual_trade_count}笔（与下方规则机会扫描分开统计）。")
    if not metrics.get("available"):
        lines.append("当日 M5 数据不足，暂不做行情判定。")
        return lines
    lines.append(f"**行情性质：** {trend.get('label', '数据不足')}")
    lines.append(f"**判定依据：** {trend.get('reason', '')}")
    lines.append(
        f"**口径：** {trend.get('rule', '')}"
    )
    recent = review.get("trend_recent") or {}
    recent_bars = int(review.get("recent_window_bars") or 0)
    if recent and recent_bars:
        hours = recent_bars * 5 / 60.0
        lines.append(
            f"**最近这波（近{hours:.0f}小时）：** {recent.get('label', '数据不足')}"
            f"｜{recent.get('reason', '')}"
        )
    total = int(review.get("opportunity_total") or 0)
    opened = int(review.get("opportunity_opened") or 0)
    missed = int(review.get("opportunity_missed") or 0)
    lines.append("")
    opportunities = review.get("opportunities") or []
    if not opportunities:
        lines.append(
            "**规则机会扫描：** 当前扫描未识别可验证的规则应开位置；"
            "这不代表EA当日没有开单或不存在其他行情机会。"
        )
        return lines
    lines.append(
        f"**该开单的位置（仅当前可验证规则机会）：** 共 {total} 处；"
        f"其中EA已开出 {opened} 处；未开出 {missed} 处。"
    )
    for item in opportunities:
        time_text = str(item.get("time") or "")[11:16]
        entry = _float(item.get("entry"))
        sl = _float(item.get("sl"))
        rr = _float(item.get("rr"))
        detail = f"{time_text}｜{item.get('direction')}｜{item.get('route')}"
        if entry is not None:
            detail += f"｜入场 {entry:.2f}"
        if sl is not None:
            detail += f"｜止损 {sl:.2f}"
        if rr is not None:
            detail += f"｜目标 {rr:.2f}R"
        if item.get("ea_opened"):
            lines.append(f"- ✅ {detail}｜**已按规则开出**")
        else:
            lines.append(f"- ⚠️ {detail}｜**未开出**｜原因：{item.get('reason') or '原因未记录'}")
    if total > len(opportunities):
        lines.append(f"- 其余 {total - len(opportunities)} 处同类机会已省略（完整明细见 CSV）。")
    return lines
