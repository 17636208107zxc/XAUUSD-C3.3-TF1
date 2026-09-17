"""Broker-server four-hour market/participation facts; never makes trade decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from tools.daily_market_review import _market_metrics, classify_trend, humanize_reason
from tools.review_core import rows_for_server_window

BEIJING = timezone(timedelta(hours=8))
FULL_END_HOURS = (5, 9, 13, 17, 21)


def due_full_window(now_beijing: datetime, server_offset_hours: int = 5) -> dict[str, str] | None:
    """Only the five complete windows qualify; no late backfill/duplicate tail alert."""
    server = now_beijing.astimezone(BEIJING).replace(tzinfo=None) - timedelta(hours=server_offset_hours)
    if server.hour not in FULL_END_HOURS or not 5 <= server.minute < 15:
        return None
    end = server.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=4)
    return {"start": start.strftime("%Y-%m-%d %H:%M:%S"), "end": end.strftime("%Y-%m-%d %H:%M:%S")}


def session_four_hour_windows(server_open: str, server_close: str) -> list[dict[str, Any]]:
    """The final incomplete 21:00-close window belongs only in the daily report."""
    open_time = datetime.strptime(server_open.replace(".", "-"), "%Y-%m-%d %H:%M:%S")
    close_time = datetime.strptime(server_close.replace(".", "-"), "%Y-%m-%d %H:%M:%S")
    day = open_time.date().isoformat()
    windows = [
        {"start": f"{day} {hour - 4:02d}:00:00", "end": f"{day} {hour:02d}:00:00", "daily_only": False}
        for hour in FULL_END_HOURS
        if open_time < datetime.strptime(f"{day} {hour:02d}:00:00", "%Y-%m-%d %H:%M:%S")
        and close_time >= datetime.strptime(f"{day} {hour:02d}:00:00", "%Y-%m-%d %H:%M:%S")
    ]
    if close_time > datetime.strptime(f"{day} 21:00:00", "%Y-%m-%d %H:%M:%S"):
        windows.append({"start": f"{day} 21:00:00", "end": close_time.strftime("%Y-%m-%d %H:%M:%S"), "daily_only": True})
    return windows


def _window_rows(
    root: Path, prefix: str, start: str, end: str, legacy_root: Path | None = None,
    key: tuple[str, ...] = ("m5_time",),
) -> list[dict[str, str]]:
    # New version wins on overlap. The legacy root only fills the migration gap.
    inclusive_end = (datetime.strptime(end, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    timestamp_field = "m5_time" if prefix == "Market_Snapshots" else "server_time"
    rows = rows_for_server_window(root, prefix, start, inclusive_end,
                                  timestamp_field=timestamp_field)
    if legacy_root is not None and legacy_root != root:
        rows += rows_for_server_window(legacy_root, prefix, start, inclusive_end,
                                       timestamp_field=timestamp_field)
    unique: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        identity = tuple(str(row.get(part) or "") for part in key)
        if identity not in unique:
            unique[identity] = row
    return sorted(unique.values(), key=lambda row: str(row.get(timestamp_field) or ""))


def _strongest_segment(rows: list[dict[str, str]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for width in (12, 18, 24):
        for index in range(len(rows) - width + 1):
            segment = rows[index:index + width]
            bar_times = [datetime.strptime(str(row.get("m5_time") or "").replace(".", "-"),
                                           "%Y-%m-%d %H:%M:%S") for row in segment]
            if any(later - earlier != timedelta(minutes=5)
                   for earlier, later in zip(bar_times, bar_times[1:])):
                continue
            metrics = _market_metrics(segment)
            if not metrics.get("available") or classify_trend(metrics)["label"] != "强趋势":
                continue
            if float(metrics["net_move_atr"]) < 3:
                continue
            if best is None or float(metrics["net_move_usd"]) > float(best["net_move_usd"]):
                best = {
                    "start": str(segment[0].get("m5_time") or "")[:16],
                    "end": str(segment[-1].get("m5_time") or "")[:16],
                    "direction": metrics["direction"],
                    "net_move_usd": metrics["net_move_usd"],
                    "high": metrics["high"],
                    "low": metrics["low"],
                    "blocked_reason": "",
                }
                reasons = [
                    humanize_reason(str(row.get("last_scan_reason") or ""))
                    for row in segment if str(row.get("last_scan_stage") or "").lower() == "trend"
                ]
                if reasons:
                    best["blocked_reason"] = max(set(reasons), key=reasons.count)
    return best


def build_four_hour_review(
    root: Path, start: str, end: str, *, legacy_root: Path | None = None,
) -> dict[str, Any]:
    snapshots = _window_rows(root, "Market_Snapshots", start, end, legacy_root)
    events = _window_rows(
        root, "Strategy_Events", start, end, legacy_root,
        key=("server_time", "event_type", "signal_id", "deal_ticket"),
    )
    metrics = _market_metrics(snapshots)
    trend = classify_trend(metrics, scope="本段")
    key_trend = _strongest_segment(snapshots)
    candidates = {str(row.get("signal_id") or row.get("server_time"))
        for row in events if row.get("event_type") == "candidate"}
    fills = {
        str(row.get("position_id") or row.get("deal_ticket") or row.get("signal_id") or row.get("server_time")): row
        for row in events if row.get("event_type") == "pending_filled"
    }
    segment_fills: list[dict[str, str]] = []
    if key_trend:
        segment_start = datetime.strptime(key_trend["start"], "%Y-%m-%d %H:%M")
        segment_end = datetime.strptime(key_trend["end"], "%Y-%m-%d %H:%M") + timedelta(minutes=5)
        segment_fills = [row for row in fills.values()
                         if segment_start <= datetime.strptime(
                             str(row.get("server_time") or "").replace(".", "-"),
                             "%Y-%m-%d %H:%M:%S",
                         ) < segment_end]
    aligned_direction = "BUY" if key_trend and key_trend["direction"] == "上涨" else "SELL"
    aligned_fills = sum(str(row.get("direction") or "").upper() == aligned_direction for row in segment_fills)
    opposite_fills = sum(str(row.get("direction") or "").upper() in {"BUY", "SELL"}
                         and str(row.get("direction") or "").upper() != aligned_direction
                         for row in segment_fills)
    expected = int((datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
                    - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).total_seconds() / 300)
    start_time = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    observed_times = {str(row.get("m5_time") or "").replace(".", "-") for row in snapshots}
    missing_bars = [(start_time + timedelta(minutes=5 * index)).strftime("%H:%M")
                    for index in range(expected)
                    if (start_time + timedelta(minutes=5 * index)).strftime("%Y-%m-%d %H:%M:%S")
                    not in observed_times]
    complete = not missing_bars
    if key_trend and not segment_fills:
        participation = "明显趋势段未参与"
    elif opposite_fills:
        participation = "趋势段有逆势成交，需逐单核查"
    elif key_trend:
        participation = "趋势段有成交，入场位置与风险仍需逐单核查"
    else:
        participation = "未检出符合强趋势口径的连续片段"
    return {
        "start": start, "end": end, "observed_bars": len(snapshots),
        "expected_bars": expected, "data_status": "完整" if complete else "部分缺失",
        "missing_bars": missing_bars,
        "metrics": metrics, "trend": trend, "key_trend": key_trend,
        "formal_candidates": len(candidates), "filled_trades": len(fills),
        "trend_aligned_fills": aligned_fills, "trend_opposite_fills": opposite_fills,
        "participation": participation,
    }


def build_four_hour_pro_payload(
    root: Path, report: dict[str, Any], *, legacy_root: Path | None = None,
) -> dict[str, Any]:
    """Only observed market/gate facts go to Pro; it cannot rewrite local counts."""
    start, end = report["start"], report["end"]
    snapshots = _window_rows(root, "Market_Snapshots", start, end, legacy_root)
    events = _window_rows(
        root, "Strategy_Events", start, end, legacy_root,
        key=("server_time", "event_type", "signal_id", "deal_ticket"),
    )
    bar_fields = ("m5_time", "m5_open", "m5_high", "m5_low", "m5_close",
                  "m5_ema20", "m5_atr14", "m15_time", "m15_open", "m15_high",
                  "m15_low", "m15_close", "m15_ema20", "m15_atr14",
                  "last_scan_outcome", "last_scan_stage", "last_scan_reason",
                  "spread", "weekend_guard", "rollover_guard")
    event_fields = ("server_time", "event_type", "stage", "outcome", "reason",
                    "direction", "price")
    return {
        "local_facts": report,
        "observed_m5_bars": [
            {field: row[field] for field in bar_fields if row.get(field) not in (None, "")}
            for row in snapshots
        ],
        "strategy_events": [
            {field: row[field] for field in event_fields if row.get(field) not in (None, "")}
            for row in events
        ],
    }


def validate_four_hour_pro_analysis(value: dict[str, Any]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("four-hour Pro response must be an object")
    fields = ("market_explanation", "entry_assessment", "missing_reason", "observation")
    output: dict[str, str] = {}
    for field in fields:
        item = value.get(field)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"four-hour Pro response missing {field}")
        text = item.strip()[:220]
        if re.search(r"规则上.{0,6}(?:应|该)开|本应开|必开|确定漏单|肯定漏单", text):
            raise ValueError("four-hour Pro asserted an unverified required trade")
        output[field] = text
    return output


def render_four_hour_card(
    report: dict[str, Any], server_beijing_offset_hours: int = 5
) -> str:
    start, end = report["start"], report["end"]
    metrics = report.get("metrics") or {}
    trend = report.get("trend") or {}
    analysis = report.get("pro_analysis") or {}
    beijing_start = datetime.strptime(start, "%Y-%m-%d %H:%M:%S") + timedelta(hours=server_beijing_offset_hours)
    beijing_end = datetime.strptime(end, "%Y-%m-%d %H:%M:%S") + timedelta(hours=server_beijing_offset_hours)
    lines = [f"**MT5 服务器时间 {start[11:16]}–{end[11:16]}"
             f"（北京时间 {beijing_start:%H:%M}–{beijing_end:%H:%M}）**"]
    if metrics.get("available"):
        lines.extend([f"**行情性质：** {trend['label']}",
                      f"**判定依据：** {trend['reason']}",
                      f"**口径：** {trend['rule']}"])
    else:
        lines.extend(["**行情性质：** 数据不足", "**判定依据：** M5数据不足，不能判断本段性质。"])
    segment = report.get("key_trend")
    if segment:
        lines.append(f"**段内主要行情：** {segment['start'][11:16]}–{segment['end'][11:16]}"
                     f" {segment['direction']}{segment['net_move_usd']:.2f}美元，属于强趋势片段。")
    else:
        lines.append("**段内主要行情：** 未检出符合当前强趋势口径的连续片段。")
    lines.append(f"**正式候选与成交：** 候选{report['formal_candidates']}处，实际成交"
                 f"{report['filled_trades']}笔；不能把候选数等同于规则应开位置数。")
    if segment and report["filled_trades"]:
        lines.append(f"**趋势片段成交方向：** 顺势成交{report['trend_aligned_fills']}笔、"
                     f"逆势成交{report['trend_opposite_fills']}笔；方向吻合不代表进场位置或止损合格。")
    local_market = (f"四小时整体{trend.get('label', '数据不足')}；"
                    + (f"其中{segment['start'][11:16]}–{segment['end'][11:16]}有明显"
                       f"{segment['direction']}段。" if segment else "未检出连续强趋势段。"))
    local_entry = f"{report['participation']}；正式候选数只代表EA已记录的候选。"
    local_reason = "本段缺少可核实的规则应开位置，不能断定漏单。"
    if segment and report["trend_aligned_fills"] + report["trend_opposite_fills"] == 0:
        reason = segment.get("blocked_reason") or "具体门禁证据不足"
        candidate_note = "没有形成正式候选" if not report["formal_candidates"] else "本片段未见成交"
        local_reason = (f"{reason}；{candidate_note}，不能据此断定某一笔合格单漏开。")
    if report.get("pro_status") == "unavailable":
        lines.append("**Pro分析状态：** Pro分析暂不可用，以下解释仅依据本地记录。")
    lines.extend([f"**行情性质说明：** {analysis.get('market_explanation') or local_market}",
                  f"**开单准确性说明：** {analysis.get('entry_assessment') or local_entry}",
                  f"**没开出来的原因：** {analysis.get('missing_reason') or local_reason}",
                  f"**观察建议：** {analysis.get('observation') or '继续观察同类行情是否反复被同一门槛拦截，不据单段行情修改参数。'}"])
    missing = report.get("missing_bars") or []
    missing_note = f"；缺少{', '.join(missing)}的M5快照" if missing else ""
    lines.append(f"**数据状态：** {report['data_status']}（{report['observed_bars']}/{report['expected_bars']}根M5{missing_note}）。")
    if missing:
        lines.append("缺失时段无法判断是否产生过合格信号；上述结论仅限已有记录。")
    return "\n".join(lines)
