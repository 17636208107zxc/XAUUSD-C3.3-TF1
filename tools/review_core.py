from __future__ import annotations

import csv
import copy
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def to_beijing(value: datetime) -> datetime:
    return ensure_aware(value).astimezone(BEIJING_TZ)


def beijing_date_key(value: datetime) -> str:
    return to_beijing(value).strftime("%Y-%m-%d")


def beijing_hour_key(value: datetime) -> str:
    return to_beijing(value).strftime("%Y-%m-%dT%H")


def parse_beijing_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING_TZ)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


class ApiBudget:
    def __init__(self, path: Path, monitor_limit: int = 29, daily_limit: int = 1) -> None:
        if monitor_limit < 0 or daily_limit < 0:
            raise ValueError("API limits must be non-negative")
        self.path = Path(path)
        self.monitor_limit = monitor_limit
        self.daily_limit = daily_limit

    def _load(self) -> dict[str, dict[str, int]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        result: dict[str, dict[str, int]] = {}
        for key, counts in value.items():
            if isinstance(counts, dict):
                result[str(key)] = {
                    "monitor": max(0, int(counts.get("monitor", 0))),
                    "daily": max(0, int(counts.get("daily", 0))),
                }
        return result

    def _consume(self, day: str, bucket: str, limit: int) -> bool:
        data = self._load()
        counts = data.setdefault(day, {"monitor": 0, "daily": 0})
        if counts[bucket] >= limit:
            return False
        counts[bucket] += 1
        # Keep the ledger compact while preserving recent audit history.
        for old_day in sorted(data)[:-45]:
            data.pop(old_day, None)
        atomic_write_json(self.path, data)
        return True

    def consume_monitor(self, day: str) -> bool:
        return self._consume(day, "monitor", self.monitor_limit)

    def consume_daily(self, day: str) -> bool:
        return self._consume(day, "daily", self.daily_limit)

    def _refund(self, day: str, bucket: str) -> None:
        data = self._load()
        counts = data.setdefault(day, {"monitor": 0, "daily": 0})
        counts[bucket] = max(0, counts.get(bucket, 0) - 1)
        atomic_write_json(self.path, data)

    def refund_monitor(self, day: str) -> None:
        self._refund(day, "monitor")

    def refund_daily(self, day: str) -> None:
        self._refund(day, "daily")

    def counts(self, day: str) -> dict[str, int]:
        return self._load().get(day, {"monitor": 0, "daily": 0})


def is_scheduled_monitor_due(now: datetime, last_hour_key: str | None) -> bool:
    current = to_beijing(now)
    key = current.strftime("%Y-%m-%dT%H")
    return current.minute == 5 and key != last_hour_key


@dataclass
class EventMergeQueue:
    window_minutes: int = 10
    events: list[dict[str, str]] = field(default_factory=list)

    def add(self, event: dict[str, str]) -> None:
        if not event.get("beijing_time"):
            raise ValueError("event requires beijing_time")
        self.events.append(dict(event))
        self.events.sort(key=lambda row: row.get("beijing_time", ""))

    def flush_ready(self, now: datetime, force: bool = False) -> list[list[dict[str, str]]]:
        if not self.events:
            return []
        groups: list[list[dict[str, str]]] = []
        current: list[dict[str, str]] = []
        current_key = ""
        previous_time: datetime | None = None
        cutoff = to_beijing(now) - timedelta(minutes=self.window_minutes)

        for event in self.events:
            event_time = parse_beijing_datetime(event["beijing_time"])
            event_key = event.get("signal_id") or "GLOBAL"
            can_join = (
                current
                and event_key == current_key
                and previous_time is not None
                and event_time - previous_time <= timedelta(minutes=self.window_minutes)
            )
            if not can_join and current:
                groups.append(current)
                current = []
            if not current:
                current_key = event_key
            current.append(event)
            previous_time = event_time
        if current:
            groups.append(current)

        ready: list[list[dict[str, str]]] = []
        pending: list[dict[str, str]] = []
        for group in groups:
            last_time = parse_beijing_datetime(group[-1]["beijing_time"])
            if force or last_time <= cutoff:
                ready.append(group)
            else:
                pending.extend(group)
        self.events = pending
        return ready


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def rows_for_server_window(
    root: Path,
    prefix: str,
    server_open: str,
    server_close: str,
    timestamp_field: str = "server_time",
) -> list[dict[str, str]]:
    def normalized_timestamp(value: str) -> str:
        value = str(value).strip()
        if len(value) >= 10:
            value = f"{value[:4]}-{value[5:7]}-{value[8:]}"
        return value

    normalized_open = normalized_timestamp(server_open)
    normalized_close = normalized_timestamp(server_close)
    if not normalized_open or not normalized_close or normalized_open > normalized_close:
        raise ValueError("invalid broker session server-time window")
    rows: list[dict[str, str]] = []
    raw = Path(root) / "Raw_Data"
    for path in sorted(raw.glob(f"{prefix}_*.csv")):
        for row in read_csv_rows(path):
            stamp = str(row.get(timestamp_field, "")).strip()
            normalized_stamp = normalized_timestamp(stamp)
            if normalized_open <= normalized_stamp <= normalized_close:
                rows.append(row)
    rows.sort(key=lambda row: normalized_timestamp(str(row.get(timestamp_field, ""))))
    return rows


def deduplicate_rows(rows: Iterable[dict[str, str]], keys: tuple[str, ...]) -> list[dict[str, str]]:
    seen: set[tuple[str, ...]] = set()
    output: list[dict[str, str]] = []
    for row in rows:
        signature = tuple(row.get(key, "") for key in keys)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(row)
    return output


def compact_monitor_payload(
    rows: list[dict[str, str]],
    snapshots: list[dict[str, str]],
    calendar: list[dict[str, str]],
) -> dict[str, Any]:
    reason_counts = Counter(
        row.get("reason", "").strip()
        for row in rows
        if row.get("reason", "").strip()
    )
    unique_events: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in reversed(rows):
        signature = (
            row.get("event_type", ""),
            row.get("reason", ""),
            row.get("signal_id", ""),
        )
        if signature in seen:
            continue
        seen.add(signature)
        unique_events.append(row)
        if len(unique_events) >= 12:
            break
    unique_events.reverse()
    return {
        "latest_snapshot": snapshots[-1] if snapshots else {},
        "recent_snapshots": snapshots[-12:],
        "recent_events": unique_events,
        "block_reason_counts": dict(reason_counts),
        "calendar_events": calendar[-12:],
        "data_completeness": {
            "snapshot_rows": len(snapshots),
            "event_rows": len(rows),
            "calendar_rows": len(calendar),
        },
    }


def list_missing_review_dates(root: Path, current_beijing_date: str) -> list[str]:
    raw = Path(root) / "Raw_Data"
    daily = Path(root) / "Daily_Review"
    dates: set[str] = set()
    if raw.exists():
        for path in raw.glob("Market_Snapshots_*.csv"):
            stamp = path.stem.removeprefix("Market_Snapshots_")
            if len(stamp) == 10 and stamp < current_beijing_date:
                dates.add(stamp)
    missing = [
        stamp
        for stamp in dates
        if not (daily / stamp / f"Daily_Review_{stamp}.md").exists()
    ]
    return sorted(missing)


MONITOR_FIELDS = {
    "beijing_time",
    "trigger_type",
    "market_state",
    "trend_alignment",
    "ea_state",
    "main_reason",
    "condition_summary",
    "economic_event_note",
    "execution_status",
    "alert_level",
    "summary",
}
MONITOR_CONDITION_FIELDS = {
    "trend",
    "swing",
    "fib",
    "price_action",
    "risk_reward",
    "spread_and_time_guard",
}
DAILY_FIELDS = {
    "review_date",
    "data_status",
    "ea_runtime_status",
    "market_summary",
    "market_regime",
    "strategy_market_fit",
    "ai_filter_assessment",
    "trade_statistics",
    "opened_trade_reason",
    "no_trade_main_reason",
    "block_reason_counts",
    "representative_trades",
    "economic_event_summary",
    "execution_issues",
    "observation_items",
    "validation_issues",
    "backtest_suggestions",
    "markdown_report",
    "issue_judgment",
    "conclusion_summary",
    "trade_group_analysis",
    "market_playbook",
}

# 行情回顾段（“今天是不是强趋势 / 该开单的地方有没有开出来”）
DAILY_PLAYBOOK_FIELDS = {
    "trend_verdict",
    "entry_accuracy",
    "missing_reason",
    "actionable_note",
}
DAILY_TRADE_FIELDS = {
    "candidate_count",
    "local_reject_count",
    "ai_allow_count",
    "ai_reject_count",
    "ai_error_count",
    "pending_count",
    "trade_count",
    "win_count",
    "loss_count",
    "net_profit",
    "max_profit_trade",
    "max_loss_trade",
}
DAILY_BLOCK_FIELDS = {
    "trend_not_ready",
    "swing_not_ready",
    "fib_not_ready",
    "price_action_not_ready",
    "risk_reward_not_ready",
    "spread_or_time_guard",
    "other",
}


def _require_exact_dict(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} must contain exact fields")
    return value


def validate_monitor_response(value: Any) -> dict[str, Any]:
    result = _require_exact_dict(value, MONITOR_FIELDS, "monitor response")
    _require_exact_dict(result["condition_summary"], MONITOR_CONDITION_FIELDS, "condition_summary")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise ValueError("summary must be non-empty")
    if len(result["summary"].strip()) > 400:
        raise ValueError("summary exceeds compact monitor limit")
    return result


def validate_daily_response(value: Any) -> dict[str, Any]:
    # market_playbook 是新增字段：老版本/模型偶尔漏字段时按“空说明”处理，
    # 避免整段 AI 分析被判定失败而回退成模板文字。
    if isinstance(value, dict) and "market_playbook" not in value:
        value = {**value, "market_playbook": {key: "" for key in DAILY_PLAYBOOK_FIELDS}}
    result = _require_exact_dict(value, DAILY_FIELDS, "daily response")
    _require_exact_dict(result["trade_statistics"], DAILY_TRADE_FIELDS, "trade_statistics")
    _require_exact_dict(result["block_reason_counts"], DAILY_BLOCK_FIELDS, "block_reason_counts")
    _require_exact_dict(result["market_playbook"], DAILY_PLAYBOOK_FIELDS, "market_playbook")
    for field_name in sorted(DAILY_PLAYBOOK_FIELDS):
        if not isinstance(result["market_playbook"][field_name], str):
            raise ValueError(f"market_playbook.{field_name} must be string")
    if not isinstance(result["markdown_report"], str):
        raise ValueError("markdown_report must be string")
    if len(result["markdown_report"]) > 800:
        raise ValueError("markdown_report exceeds 800 characters")
    for field_name in (
        "representative_trades",
        "execution_issues",
        "observation_items",
        "validation_issues",
        "backtest_suggestions",
    ):
        if not isinstance(result[field_name], list):
            raise ValueError(f"{field_name} must be list")
    return result


def build_daily_fallback_response(payload: dict[str, Any], review_day: str) -> dict[str, Any]:
    """Build a valid daily response from local facts when DeepSeek is unavailable.

    Every DAILY_FIELDS key is present; AI-authored text is emptied so the renderer
    marks the AI summary as unavailable while objective local data still renders.
    """
    statistics = dict(payload.get("statistics") or {})
    block_counts = dict(statistics.pop("block_reason_counts", {}) or {})
    marker = "AI" + "\u603b\u7ed3\u6682\u4e0d\u53ef\u7528\u3002"
    return {
        "review_date": str(review_day),
        "data_status": "\u5b8c\u6574",
        "ea_runtime_status": "\u6b63\u5e38",
        "market_summary": marker,
        "market_regime": "\u6570\u636e\u4e0d\u8db3",
        "strategy_market_fit": marker,
        "ai_filter_assessment": marker,
        "trade_statistics": {
            key: statistics.get(key, 0) for key in DAILY_TRADE_FIELDS
        },
        "opened_trade_reason": "",
        "no_trade_main_reason": "",
        "block_reason_counts": {
            key: block_counts.get(key, 0) for key in DAILY_BLOCK_FIELDS
        },
        "representative_trades": [],
        "economic_event_summary": "",
        "execution_issues": [],
        "observation_items": [],
        "validation_issues": [],
        "backtest_suggestions": [],
        "markdown_report": "",
        "issue_judgment": "",
        "conclusion_summary": "",
        "trade_group_analysis": "",
        "market_playbook": {key: "" for key in DAILY_PLAYBOOK_FIELDS},
    }


def apply_local_daily_facts(response: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(response)
    if not str(result.get("trade_group_analysis") or "").strip():
        # Only fall back to the deterministic summary when DeepSeek Pro did not
        # produce the three-group analysis (e.g. model unavailable).
        result["trade_group_analysis"] = _local_trade_group_analysis(payload)
    statistics = dict(payload.get("statistics", {}))
    block_counts = dict(statistics.pop("block_reason_counts", {}))
    result["trade_statistics"] = {
        key: statistics.get(key, 0) for key in DAILY_TRADE_FIELDS
    }
    result["block_reason_counts"] = {
        key: block_counts.get(key, 0) for key in DAILY_BLOCK_FIELDS
    }

    open_rows = {
        str(row.get("position_id", "")): row
        for row in payload.get("actual_trade_rows", [])
        if row.get("status") == "open_at_day_end"
    }
    if open_rows:
        def replace_floating_language(value: Any) -> Any:
            if isinstance(value, str):
                return (
                    value.replace("未平仓浮盈", "未完全平仓交易的阶段已实现盈利")
                    .replace("未平仓浮亏", "未完全平仓交易的阶段已实现亏损")
                    .replace("收盘前浮盈", "截至收盘阶段已实现盈利")
                    .replace("收盘前浮亏", "截至收盘阶段已实现亏损")
                    .replace("收盘浮盈", "截至收盘阶段已实现盈利")
                    .replace("收盘浮亏", "截至收盘阶段已实现亏损")
                    .replace("浮盈", "阶段已实现盈利")
                    .replace("浮亏", "阶段已实现亏损")
                )
            if isinstance(value, list):
                return [replace_floating_language(item) for item in value]
            if isinstance(value, dict):
                return {key: replace_floating_language(item) for key, item in value.items()}
            return value

        result = replace_floating_language(result)
        for trade in result.get("representative_trades", []):
            position_id = str(trade.get("trade_id", ""))
            row = open_rows.get(position_id)
            if row is None:
                continue
            if trade.get("type") in {"最大盈利", "最大亏损"}:
                trade["type"] = "其他典型"
            realized = float(row.get("net_profit", 0) or 0)
            if realized > 0:
                realized_text = f"净盈利{realized:.2f} USD"
            elif realized < 0:
                realized_text = f"净亏损{abs(realized):.2f} USD"
            else:
                realized_text = "净盈亏0.00 USD"
            trade["reason"] = (
                "该交易在经纪商收盘时仍有剩余仓位；截至收盘，"
                f"已平仓部分实现{realized_text}，整笔交易结果尚未确定。"
            )
    return result


def _local_trade_group_analysis(payload: dict[str, Any]) -> str:
    """Deterministic three-group summary; never lets the AI text contradict local facts."""
    groups = payload.get("trade_groups") or {}
    parts: list[str] = []
    for key, label in (("ea", "EA单"), ("ai", "AI单"), ("manual", "人工单")):
        group = groups.get(key) or {}
        count = int(group.get("count", 0) or 0)
        win = int(group.get("win", 0) or 0)
        loss = int(group.get("loss", 0) or 0)
        net = float(group.get("net", 0) or 0)
        if count <= 0:
            parts.append(f"{label}无成交")
        else:
            parts.append(f"{label}{count}笔（{win}盈{loss}亏，净{net:+.2f} USD）")
    return "；".join(parts) + "。"


def classify_block_reason(stage: str, reason: str) -> str:
    combined = f"{stage} {reason}".lower()
    if "trend" in combined or "趋势" in combined or "hh/hl" in combined or "ll/lh" in combined:
        return "trend_not_ready"
    if "swing" in combined or "结构" in combined or "推动" in combined:
        return "swing_not_ready"
    if "fib" in combined or "回调" in combined:
        return "fib_not_ready"
    if "price_action" in combined or "pin" in combined or "engulf" in combined or "形态" in combined or "h2" in combined or "h3" in combined or "l2" in combined or "l3" in combined:
        return "price_action_not_ready"
    if "rr" in combined or "盈亏比" in combined or "止损" in combined:
        return "risk_reward_not_ready"
    if "spread" in combined or "点差" in combined or "换日" in combined or "周末" in combined or "禁开" in combined:
        return "spread_or_time_guard"
    return "other"


def safe_float(value: str | None) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_market_metrics(snapshots: list[dict[str, str]]) -> dict[str, Any]:
    valid = [
        row
        for row in snapshots
        if safe_float(row.get("m5_high")) > 0
        and safe_float(row.get("m5_low")) > 0
    ]
    if not valid:
        return {
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
            "range_usd": 0.0,
            "net_change_usd": 0.0,
            "net_change_pct": 0.0,
            "average_atr14": 0.0,
            "snapshot_count": 0,
        }
    valid = sorted(valid, key=lambda row: row.get("beijing_time", ""))
    open_price = safe_float(valid[0].get("m5_open"))
    close_price = safe_float(valid[-1].get("m5_close"))
    high_price = max(safe_float(row.get("m5_high")) for row in valid)
    low_price = min(safe_float(row.get("m5_low")) for row in valid)
    atr_values = [safe_float(row.get("m5_atr14")) for row in valid]
    atr_values = [value for value in atr_values if value > 0]
    net_change = close_price - open_price if open_price > 0 and close_price > 0 else 0.0
    return {
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "range_usd": round(high_price - low_price, 8),
        "net_change_usd": round(net_change, 8),
        "net_change_pct": (net_change / open_price * 100.0) if open_price > 0 else 0.0,
        "average_atr14": round(sum(atr_values) / len(atr_values), 8) if atr_values else 0.0,
        "snapshot_count": len(valid),
    }


def _candidate_trade_plan(row: dict[str, str]) -> dict[str, float] | None:
    details = row.get("details", "").strip()
    if not details:
        return None
    try:
        value = json.loads(details)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return _candidate_plan_from_details(value)


def _snapshot_after(snapshots: list[dict[str, str]], when: str) -> list[dict[str, str]]:
    return sorted(
        [row for row in snapshots if row.get("beijing_time", "") >= when],
        key=lambda row: row.get("beijing_time", ""),
    )


def analyze_ai_rejections(
    events: list[dict[str, str]], snapshots: list[dict[str, str]]
) -> dict[str, Any]:
    candidates = {
        row.get("signal_id", ""): row
        for row in events
        if row.get("event_type") == "candidate" and row.get("signal_id")
    }
    rejects = [
        row
        for row in events
        if row.get("event_type") == "ai_reject"
        and row.get("signal_id")
        and not _is_ai_failure_reason(row.get("reason"))
    ]
    signals: list[dict[str, Any]] = []
    for reject in rejects:
        signal_id = reject.get("signal_id", "")
        candidate = candidates.get(signal_id)
        direction = (candidate or reject).get("direction", "").upper()
        plan = _candidate_trade_plan(candidate or {})
        item: dict[str, Any] = {
            "signal_id": signal_id,
            "time": reject.get("beijing_time", ""),
            "direction": direction,
            "reason": reject.get("reason", ""),
            "entry": 0.0,
            "sl": 0.0,
            "one_r": 0.0,
            "entry_triggered": False,
            "outcome": "unresolved",
            "resolution": "missing_candidate_plan" if plan is None else "not_triggered",
        }
        if plan is None or direction not in {"BUY", "SELL"}:
            signals.append(item)
            continue
        entry = plan["entry"]
        sl = plan["sl"]
        risk = abs(entry - sl)
        one_r = entry + risk if direction == "BUY" else entry - risk
        item.update({"entry": entry, "sl": sl, "one_r": one_r})
        entry_seen = False
        for bar in _snapshot_after(snapshots, reject.get("beijing_time", "")):
            high = safe_float(bar.get("m5_high"))
            low = safe_float(bar.get("m5_low"))
            if high <= 0 or low <= 0:
                continue
            entry_touch = high >= entry if direction == "BUY" else low <= entry
            if not entry_seen:
                if not entry_touch:
                    continue
                entry_seen = True
                item["entry_triggered"] = True
            sl_touch = low <= sl if direction == "BUY" else high >= sl
            one_r_touch = high >= one_r if direction == "BUY" else low <= one_r
            if sl_touch and one_r_touch:
                item["resolution"] = "ambiguous_same_bar"
                break
            if one_r_touch:
                item["outcome"] = "missed_opportunity"
                item["resolution"] = "one_r_first"
                item["resolved_time"] = bar.get("beijing_time", "")
                break
            if sl_touch:
                item["outcome"] = "effective_filter"
                item["resolution"] = "sl_first"
                item["resolved_time"] = bar.get("beijing_time", "")
                break
        signals.append(item)

    effective = sum(row["outcome"] == "effective_filter" for row in signals)
    missed = sum(row["outcome"] == "missed_opportunity" for row in signals)
    resolved = effective + missed
    unresolved = len(signals) - resolved
    return {
        "total_rejected": len(signals),
        "effective_filter_count": effective,
        "missed_opportunity_count": missed,
        "unresolved_count": unresolved,
        "resolved_count": resolved,
        "effective_filter_rate": round(effective / resolved * 100.0, 2) if resolved else 0.0,
        "signals": signals,
    }


def build_actual_trade_rows(events: list[dict[str, str]]) -> list[dict[str, Any]]:
    fills: dict[str, dict[str, str]] = {}
    closes: dict[str, dict[str, str]] = {}
    partial_realized: Counter[str] = Counter()
    event_types: defaultdict[str, set[str]] = defaultdict(set)
    candidate_facts = {
        str(row.get("signal_id") or ""): _json_object(row.get("details"))
        for row in events
        if row.get("event_type") == "candidate" and row.get("signal_id")
    }
    for row in events:
        position_id = row.get("position_id", "").strip()
        if not position_id:
            continue
        event_types[position_id].add(str(row.get("event_type") or ""))
        if row.get("event_type") == "pending_filled" and position_id not in fills:
            fills[position_id] = row
        elif row.get("event_type") == "position_closed":
            closes[position_id] = row
        elif (
            row.get("event_type") == "position_update"
            and row.get("outcome") == "partial_exit"
        ):
            partial_realized[position_id] += safe_float(row.get("profit"))
    output: list[dict[str, Any]] = []
    for position_id, fill in sorted(fills.items(), key=lambda pair: pair[1].get("beijing_time", "")):
        close = closes.get(position_id)
        facts = candidate_facts.get(str(fill.get("signal_id") or ""), {})
        plan = facts.get("trade_plan") if isinstance(facts.get("trade_plan"), dict) else {}
        output.append(
            {
                "signal_id": fill.get("signal_id", ""),
                "position_id": position_id,
                "direction": fill.get("direction", ""),
                "open_time": fill.get("server_time") or fill.get("beijing_time", ""),
                "close_time": (
                    close.get("server_time") or close.get("beijing_time", "")
                    if close
                    else ""
                ),
                "entry_price": safe_float(fill.get("price")),
                "exit_price": safe_float(close.get("price")) if close else 0.0,
                "initial_volume": safe_float(fill.get("volume")),
                "initial_sl": safe_float(str(plan.get("sl") or "")),
                "net_profit": round(
                    safe_float(close.get("profit"))
                    if close
                    else partial_realized[position_id],
                    2,
                ),
                "whole_trade_net": round(
                    safe_float(close.get("profit"))
                    if close
                    else partial_realized[position_id],
                    2,
                ),
                "today_net": round(
                    safe_float(close.get("profit"))
                    if close
                    else partial_realized[position_id],
                    2,
                ),
                "close_reason": close.get("reason", "") if close else "",
                "tp1_done": "tp1_reached" in event_types[position_id],
                "tp2_done": "tp2_reached" in event_types[position_id],
                "runner_done": (
                    close is not None and "runner_started" in event_types[position_id]
                ),
                "data_source": "legacy_event",
                "status": "closed" if close else "open_at_day_end",
            }
        )
    return output


def merge_daily_trade_rows(
    events: list[dict[str, str]],
    lifecycle_trades: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge mixed-version trade facts, preferring lifecycle rows per position."""
    merged: dict[str, dict[str, Any]] = {}
    for row in build_actual_trade_rows(events):
        position_id = str(row.get("position_id") or "").strip()
        if position_id and position_id != "0":
            merged[position_id] = dict(row)
    for source in lifecycle_trades or []:
        position_id = str(source.get("position_id") or "").strip()
        if not position_id or position_id == "0":
            continue
        row = dict(source)
        row["position_id"] = position_id
        row["data_source"] = "lifecycle"
        merged[position_id] = row
    return sorted(merged.values(), key=lambda row: str(row.get("open_time") or ""))


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _event_time(row: dict[str, str]) -> str:
    return str(row.get("server_time") or row.get("beijing_time") or "")


def _confidence_from_event(row: dict[str, str]) -> int | None:
    text = str(row.get("details") or "")
    match = re.search(r"(?:^|\|)\s*confidence=(\d+)", text)
    return int(match.group(1)) if match else None


def _is_ai_failure_reason(reason: Any) -> bool:
    """True for legacy ai_reject rows that were actually request failures."""
    text = str(reason or "").strip()
    return text.startswith("DeepSeek请求失败") or (
        "HTTP=" in text and "MT5错误" in text
    )


def _ai_allow_from_event(row: dict[str, str] | None) -> bool:
    """Read the allow= flag from an AI event's details field."""
    if not row:
        return False
    text = str(row.get("details") or "")
    match = re.search(r"(?:^|\|)\s*allow=(true|false)", text)
    if match:
        return match.group(1) == "true"
    return False


def _entry_facts_from_details(
    facts: dict[str, Any], direction: str, ai_status: str
) -> dict[str, Any]:
    """Extract the structured facts used by the open-notification card wording."""
    trend = facts.get("trend") if isinstance(facts.get("trend"), dict) else {}
    pullback = facts.get("pullback") if isinstance(facts.get("pullback"), dict) else {}
    confluence = facts.get("confluence") if isinstance(facts.get("confluence"), dict) else {}
    price_action = (
        facts.get("price_action") if isinstance(facts.get("price_action"), dict) else {}
    )
    plan = facts.get("trade_plan") if isinstance(facts.get("trade_plan"), dict) else {}
    candidate_plan = _candidate_plan_from_details(facts) or {}
    pattern_parts: list[str] = []
    if price_action.get("pin_bar"):
        pattern_parts.append("Pin Bar")
    if price_action.get("hammer"):
        pattern_parts.append("Hammer")
    if price_action.get("engulfing"):
        pattern_parts.append("Engulfing")
    if price_action.get("strong_reversal_bar"):
        pattern_parts.append("Strong Reversal")
    ema_near = bool(confluence.get("ema20_near"))
    return {
        "direction": str(direction).upper(),
        "signal_route": str(facts.get("signal_route") or facts.get("signal_type") or "").upper(),
        "context_loaded": bool(
            trend.get("major_structure_valid")
            and trend.get("direction_consistent_with_structure")
        ),
        "fib_retracement": safe_float(str(pullback.get("fib_retracement") or "")),
        "ema_distance_usd": (
            safe_float(str(price_action.get("ema_distance_usd") or "")) if ema_near else -1.0
        ),
        "ma_confluence": ema_near,
        "h_attempt": int(safe_float(str(price_action.get("h_attempt") or 0))),
        "pattern": " + ".join(pattern_parts),
        "initial_sl": candidate_plan.get("sl", 0.0),
        "tp1_price": safe_float(str(plan.get("tp1") or "")),
        "tp2_price": 0.0,
        "ai_allow_trade": ai_status == "allow",
    }


def build_candidate_lifecycle_rows(events: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Build one evidence-backed terminal lifecycle row for each formal candidate."""
    candidates = [
        row
        for row in events
        if row.get("event_type") == "candidate" and row.get("signal_id")
    ]
    related: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    ticket_related: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in events:
        signal_id = str(row.get("signal_id") or "").strip()
        if signal_id:
            related[signal_id].append(row)
        order_ticket = str(row.get("order_ticket") or "").strip()
        if order_ticket and order_ticket != "0":
            ticket_related[order_ticket].append(row)

    output: list[dict[str, Any]] = []
    for sequence, candidate in enumerate(
        sorted(candidates, key=_event_time), start=1
    ):
        signal_id = str(candidate.get("signal_id") or "")
        facts = _json_object(candidate.get("details"))
        plan = facts.get("trade_plan") if isinstance(facts.get("trade_plan"), dict) else {}
        candidate_plan = _candidate_plan_from_details(facts) or {}
        direction = str(candidate.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            direction = str(facts.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            direction = ""
        bars = facts.get("bars") if isinstance(facts.get("bars"), list) else []
        last_bar = bars[-1] if bars and isinstance(bars[-1], dict) else {}
        events_for_signal = sorted(related[signal_id], key=_event_time)
        pending = next(
            (row for row in events_for_signal if row.get("event_type") == "pending_created"),
            None,
        )
        # Link by order_ticket: some pending_closed / pending_filled rows are
        # written with an empty signal_id, so ticket linkage recovers the true
        # terminal state instead of falling back to "unknown".
        pending_ticket = str((pending or {}).get("order_ticket") or "").strip()
        if pending_ticket and pending_ticket != "0":
            existing_ids = {id(row) for row in events_for_signal}
            for row in sorted(ticket_related.get(pending_ticket, []), key=_event_time):
                if id(row) not in existing_ids:
                    events_for_signal.append(row)
                    existing_ids.add(id(row))
            events_for_signal.sort(key=_event_time)
        ai_events = [
            row
            for row in events_for_signal
            if row.get("event_type") in {"ai_allow", "ai_reject", "ai_error"}
        ]
        ai_event = None
        for priority in ("ai_allow", "ai_error", "ai_reject"):
            if any(row.get("event_type") == priority for row in ai_events):
                ai_event = next(
                    row for row in ai_events if row.get("event_type") == priority
                )
                break
        ai_status = (
            "allow"
            if ai_event and ai_event.get("event_type") == "ai_allow"
            else "error"
            if ai_event and ai_event.get("event_type") == "ai_error"
            else "error"
            if ai_event
            and ai_event.get("event_type") == "ai_reject"
            and _is_ai_failure_reason(ai_event.get("reason"))
            else "reject"
            if ai_event and ai_event.get("event_type") == "ai_reject"
            else "undecided"
        )
        entry_facts = _entry_facts_from_details(
            facts, direction, ai_status
        )
        filled = next(
            (row for row in events_for_signal if row.get("event_type") == "pending_filled"),
            None,
        )
        wait_missed = next(
            (row for row in events_for_signal if row.get("event_type") == "pending_wait_missed"),
            None,
        )
        wait_started = next(
            (
                row
                for row in events_for_signal
                if row.get("event_type") == "pending_wait_started"
            ),
            None,
        )
        execution = next(
            (
                row
                for row in events_for_signal
                if row.get("event_type") in {"execution_error", "execution_blocked"}
            ),
            None,
        )
        closed_events = [
            row for row in events_for_signal if row.get("event_type") == "pending_closed"
        ]
        pending_canceled = next(
            (row for row in events_for_signal if row.get("event_type") == "pending_canceled"),
            None,
        )
        expired = next(
            (row for row in closed_events if row.get("reason") == "ORDER_STATE_EXPIRED"),
            None,
        )
        cancelled = next(
            (
                row
                for row in closed_events
                if row.get("reason")
                and row.get("reason") not in {"ORDER_STATE_FILLED", "ORDER_STATE_EXPIRED"}
            ),
            None,
        )

        terminal: dict[str, str] | None
        if filled:
            outcome, terminal = "filled", filled
            default_reason = "价格触发，挂单成功成交"
        elif ai_status == "reject":
            outcome, terminal = "ai_rejected", ai_event
            default_reason = "DeepSeek审核拒绝"
        elif ai_status == "error":
            outcome, terminal = "ai_error", ai_event
            if _ai_allow_from_event(ai_event):
                default_reason = "AI审核异常·降级放行（DeepSeek请求失败后按本地信号继续）"
            else:
                default_reason = "AI审核异常·未放行（DeepSeek请求失败，信号被拦截）"
        elif wait_missed:
            outcome, terminal = "wait_missed", wait_missed
            default_reason = "等待期间价格越过原入场位，信号按规则作废且未追价"
        elif execution:
            outcome, terminal = "execution_blocked", execution
            default_reason = "执行阶段被明确阻止"
        elif expired:
            outcome, terminal = "expired", expired
            default_reason = "市场未在有效期内触发入场价，挂单正常到期"
        elif pending_canceled:
            outcome, terminal = "cancelled", pending_canceled
            default_reason = "信号失效后挂单被撤销"
        elif cancelled:
            outcome, terminal = "cancelled", cancelled
            default_reason = "挂单被正常撤销"
        elif pending:
            outcome, terminal = "pending_untriggered", pending
            default_reason = "已创建挂单，但复盘窗口内没有成交或终止记录，暂未定位到最终未成交原因"
        else:
            outcome, terminal = "status_incomplete", None
            default_reason = "缺少候选后续审核、挂单或失效事件，无法判断最终状态"

        terminal_reason = str((terminal or {}).get("reason") or "").strip()
        if outcome == "wait_missed" and wait_started:
            started_reason = str(wait_started.get("reason") or "").strip()
            missed_reason = terminal_reason or "市场达到或越过原入场位"
            terminal_reason = (
                f"{started_reason}；随后{missed_reason}，信号作废且未追价"
                if started_reason
                else f"{missed_reason}，信号作废且未追价"
            )
        if outcome in {"filled", "expired", "ai_error", "pending_untriggered", "ai_rejected"}:
            terminal_reason = default_reason
        elif not terminal_reason:
            terminal_reason = default_reason
        output.append(
            {
                "sequence": sequence,
                "signal_id": signal_id,
                "signal_bar_time": str(last_bar.get("time") or ""),
                "candidate_time": _event_time(candidate),
                "direction": direction,
                "route": str(facts.get("signal_route") or facts.get("signal_type") or ""),
                "planned_entry": candidate_plan.get("entry"),
                "planned_sl": candidate_plan.get("sl"),
                "planned_tp1": safe_float(str(plan.get("tp1"))) if plan.get("tp1") not in (None, "") else None,
                "planned_rr": safe_float(str(plan.get("rr_to_tp1"))) if plan.get("rr_to_tp1") not in (None, "") else None,
                "ai_status": ai_status,
                "ai_allow_trade": _ai_allow_from_event(ai_event),
                "ai_time": _event_time(ai_event or {}),
                "ai_confidence": _confidence_from_event(ai_event or {}),
                "ai_reason": str((ai_event or {}).get("reason") or ""),
                "outcome": outcome,
                "outcome_time": _event_time(terminal or {}),
                "outcome_reason": terminal_reason,
                "order_ticket": str((pending or terminal or {}).get("order_ticket") or ""),
                "order_created_at": _event_time(pending or {}),
                "position_id": str((filled or {}).get("position_id") or ""),
                "deal_ticket": str((filled or {}).get("deal_ticket") or ""),
                "entry_facts": entry_facts,
            }
        )
    return output


def collect_ai_candidate_rows(root, server_open: str, server_close: str) -> list[dict[str, Any]]:
    """Collect Parallel AI Trader B OPEN decisions for the broker session.

    Source is the Parallel AI comparison records (not the Python reference and
    not the EA). A row is emitted for every bar where the AI decided OPEN.
    """
    import json as _json

    rows: list[dict[str, Any]] = []
    day = str(server_open)[:10].replace(".", "-")
    jsonl_path = Path(root) / "Parallel_AI_V2" / "Records" / f"Comparisons_{day}.jsonl"
    if not jsonl_path.exists():
        return rows
    trade_state_dir = Path(root) / "Parallel_AI_V2" / "AI_Trade_State"
    sequence = 0
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        ai = rec.get("ai") or {}
        if not isinstance(ai, dict) or str(ai.get("action", "")).upper() != "OPEN":
            continue
        sequence += 1
        signal_id = str(rec.get("snapshot_id") or "")
        final_state = ""
        rule_compliance = ""
        rule_block_reason = ""
        block_gate_ids: list[str] = []
        plan_path = trade_state_dir / f"{signal_id}.json"
        if plan_path.exists():
            try:
                plan = _json.loads(plan_path.read_text(encoding="utf-8"))
                final_state = str(plan.get("state") or "")
                rule_compliance = str(plan.get("rule_compliance") or "")
                rule_block_reason = str(plan.get("rule_block_reason") or "")
                block_gate_ids = [str(x) for x in (plan.get("block_gate_ids") or [])]
            except (OSError, _json.JSONDecodeError):
                final_state = ""
        rows.append({
            "sequence": sequence,
            "signal_id": signal_id,
            "signal_bar_time": str(rec.get("m5_time") or ""),
            "candidate_time": str(rec.get("completed_beijing") or ""),
            "direction": str(ai.get("direction") or "").upper(),
            "route": str(ai.get("route") or ""),
            "planned_entry": ai.get("entry"),
            "planned_sl": ai.get("sl"),
            "planned_tp1": ai.get("tp1"),
            "planned_tp2": ai.get("tp2"),
            "confidence": ai.get("confidence"),
            "reason": str(ai.get("reason") or ""),
            "rr_to_tp1": ai.get("rr_to_tp1"),
            "plan_status": str(rec.get("ai_plan_status") or ""),
            "plan_reason": str(rec.get("ai_plan_reason") or ""),
            "execution_status": str(rec.get("ai_execution_status") or ""),
            "final_state": final_state,
            "rule_compliance": rule_compliance,
            "rule_block_reason": rule_block_reason,
            "block_gate_ids": block_gate_ids,
        })
    return rows


def parse_server_sl_details(details: str) -> dict[str, str]:
    """解析 MQ5 写入的 server_sl_snapshot details 文本。"""
    result: dict[str, str] = {}
    for part in str(details or "").split(" | "):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def collect_server_sl_snapshots(
    root, server_open: str, server_close: str
) -> list[dict[str, Any]]:
    """按 Position 读取服务器SL生命周期快照（只读，来自 Strategy_Events）。"""
    rows = rows_for_server_window(root, "Strategy_Events", server_open, server_close)
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("event_type") or "") != "server_sl_snapshot":
            continue
        details = parse_server_sl_details(str(row.get("details") or ""))
        snapshots.append(
            {
                "server_time": str(row.get("server_time") or ""),
                "beijing_time": str(row.get("beijing_time") or ""),
                "position_id": str(row.get("position_id") or ""),
                "source": details.get("source", ""),
                "planned_initial_sl": safe_float(details.get("planned_initial_sl")),
                "submitted_sl": safe_float(details.get("submitted_sl")),
                "server_sl_before_modify": safe_float(
                    details.get("server_sl_before_modify")
                ),
                "requested_new_sl": safe_float(details.get("requested_new_sl")),
                "server_sl_after_modify": safe_float(
                    details.get("server_sl_after_modify")
                ),
                "modify_success": (
                    str(details.get("modify_success", "")).lower() == "true"
                    if "modify_success" in details
                    else None
                ),
                "retcode": str(details.get("retcode", "")),
                "retcode_description": details.get("retcode_description", ""),
                "signal_id": str(row.get("signal_id") or ""),
            }
        )
    return snapshots


def build_daily_payload(
    review_date: str,
    snapshots: list[dict[str, str]],
    events: list[dict[str, str]],
    calendar: list[dict[str, str]],
    prior_issue_history: list[dict[str, Any]] | None = None,
    lifecycle_trades: list[dict[str, Any]] | None = None,
    missed_history_records: list[dict[str, Any]] | None = None,
    m1_loader=None,
    tick_loader=None,
    tick_diagnostics=None,
) -> dict[str, Any]:
    event_counts = Counter(row.get("event_type", "") for row in events)
    block_counts = Counter({key: 0 for key in DAILY_BLOCK_FIELDS})
    for row in events:
        if row.get("event_type") in {"local_reject", "risk_block", "scan_result"}:
            block_counts[classify_block_reason(row.get("stage", ""), row.get("reason", ""))] += 1

    closed = [row for row in events if row.get("event_type") == "position_closed"]
    candidate_rows = build_candidate_lifecycle_rows(events)
    unfilled_review = analyze_unfilled_candidates(
        events,
        snapshots,
        m1_loader=m1_loader,
        tick_loader=tick_loader,
        tick_diagnostics=tick_diagnostics,
    )
    if missed_history_records:
        summary_records = list(missed_history_records)
        seen = {(str(r.get("review_date") or ""), str(r.get("signal_id") or "")) for r in summary_records}
        for item in unfilled_review.get("items", []):
            key = (review_date, str(item.get("signal_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            summary_records.append({
                "review_date": review_date,
                "signal_id": str(item.get("signal_id") or ""),
                "direction": str(item.get("direction") or ""),
                "outcome": str(item.get("outcome") or ""),
                "reason_code": str(item.get("reason_code") or ""),
                "reason_text": str(item.get("reason_text") or ""),
                "entry": item.get("entry"),
                "sl": item.get("sl"),
                "r30": item.get("r30"),
                "r60": item.get("r60"),
                "r_max": item.get("r_max"),
                "window30_insufficient": bool(item.get("window30_insufficient")),
                "window60_insufficient": bool(item.get("window60_insufficient")),
                "final_insufficient": bool(item.get("final_insufficient")),
                "first_touch": item.get("first_touch"),
                "classification": str(item.get("classification") or ""),
                "hit_0_8": bool(item.get("hit_0_8")),
                "hit_1r": bool(item.get("hit_1r")),
                "hit_2r": bool(item.get("hit_2r")),
            })
    else:
        summary_records = []
    missed_summary = summarize_missed_candidate_history(summary_records)
    if lifecycle_trades is None:
        actual_trade_rows = build_actual_trade_rows(events)
        closed_profits = [safe_float(row.get("profit")) for row in closed]
        realized_profits = [
            safe_float(str(row.get("net_profit", 0))) for row in actual_trade_rows
        ]
        trade_count = (
            event_counts["pending_filled"]
            if event_counts["pending_filled"] > 0
            else len(closed)
        )
        representative_source: list[dict[str, Any]] = list(closed)
    else:
        actual_trade_rows = merge_daily_trade_rows(events, lifecycle_trades)
        closed_rows = [
            row
            for row in actual_trade_rows
            if str(row.get("status", "")).startswith("closed")
        ]
        closed_profits = [
            safe_float(str(row.get("whole_trade_net", row.get("net_profit", 0))))
            for row in closed_rows
        ]
        realized_profits = [
            safe_float(str(row.get("today_net", 0))) for row in actual_trade_rows
        ]
        trade_count = len(actual_trade_rows)
        representative_source = actual_trade_rows
    wins = sum(value > 0 for value in closed_profits)
    losses = sum(value < 0 for value in closed_profits)
    if len(representative_source) <= 5:
        representative = list(representative_source)
    else:
        representative = sorted(
            representative_source,
            key=lambda row: abs(
                safe_float(
                    str(
                        row.get(
                            "whole_trade_net", row.get("net_profit", row.get("profit", 0))
                        )
                    )
                )
            ),
            reverse=True,
        )[:3]

    market_metrics = compute_market_metrics(snapshots)
    reject_analysis = analyze_ai_rejections(events, snapshots)

    return {
        "review_date": review_date,
        "data_completeness": {
            "snapshot_count": len(snapshots),
            "event_count": len(events),
            "calendar_count": len(calendar),
            "first_snapshot": snapshots[0].get("beijing_time", "") if snapshots else "",
            "last_snapshot": snapshots[-1].get("beijing_time", "") if snapshots else "",
        },
        "market": {
            "first_snapshot": snapshots[0] if snapshots else {},
            "last_snapshot": snapshots[-1] if snapshots else {},
            "hourly_samples": snapshots[::12] if snapshots else [],
        },
        "market_metrics": market_metrics,
        "ai_reject_analysis": reject_analysis,
        "actual_trade_rows": actual_trade_rows,
        "candidate_rows": candidate_rows,
        "unfilled_candidate_review": unfilled_review,
        "missed_candidate_summary": missed_summary,
        "statistics": {
            "candidate_count": event_counts["candidate"],
            "local_reject_count": event_counts["local_reject"],
            "ai_allow_count": event_counts["ai_allow"],
            "ai_reject_count": max(
                event_counts["ai_reject"]
                - sum(
                    1
                    for row in events
                    if row.get("event_type") == "ai_reject"
                    and _is_ai_failure_reason(row.get("reason"))
                ),
                0,
            ),
            "ai_error_count": event_counts["ai_error"]
            + sum(
                1
                for row in events
                if row.get("event_type") == "ai_reject"
                and _is_ai_failure_reason(row.get("reason"))
            ),
            "pending_count": event_counts["pending_created"],
            "trade_count": trade_count,
            "win_count": wins,
            "loss_count": losses,
            "net_profit": round(sum(realized_profits), 2),
            "max_profit_trade": max(closed_profits, default=0),
            "max_loss_trade": min(closed_profits, default=0),
            "block_reason_counts": dict(block_counts),
        },
        "representative_trade_rows": representative,
        "economic_calendar": calendar,
        "execution_events": [
            row for row in events if row.get("event_type") == "execution_error"
        ],
        "prior_issue_history": prior_issue_history or [],
        "rules": {
            "max_trades_for_full_detail": 5,
            "max_representative_trades": 3,
            "markdown_character_limit": 800,
            "advice_policy": "single-day issue is observe only; validation requires 3 consecutive days or 5 of 10 days",
        },
    }


# ---------------------------------------------------------------------------
# 未成交候选失效后行情复盘（仅读行情与事件数据，不参与交易决策）
# ---------------------------------------------------------------------------

MISSED_REASON_CODES = [
    "挂单距离不足",
    "价格越过Entry",
    "挂单到期",
    "信号失效",
    "AI拒绝",
    "其他执行原因",
]


def _beijing_time(row):
    """Prefer Beijing time (snapshot/event CSV timestamps) for window math."""
    return str(row.get("beijing_time") or row.get("server_time") or "").strip()


def _parse_ts(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _ts_key(value):
    parsed = _parse_ts(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else str(value or "")


def map_missed_reason_code(outcome, reason, events_for_signal=None):
    """Map a candidate's terminal state to a fixed reason_code."""
    outcome_text = str(outcome or "").strip().lower()
    reason_text = str(reason or "").strip()
    if outcome_text in {"ai_rejected", "ai_error"}:
        return "AI拒绝"
    if outcome_text == "wait_missed":
        if events_for_signal:
            for ev in events_for_signal:
                if str(ev.get("event_type") or "") == "pending_wait_started" and (
                    "距离不足" in str(ev.get("reason") or "")
                    or "distance" in str(ev.get("reason") or "").lower()
                ):
                    return "挂单距离不足"
        if "距离不足" in reason_text:
            return "挂单距离不足"
        return "价格越过Entry"
    if outcome_text == "expired":
        return "挂单到期"
    if outcome_text == "cancelled":
        if "距离不足" in reason_text:
            return "挂单距离不足"
        return "信号失效"
    return "其他执行原因"


def _candidate_plan_from_details(details):
    if not isinstance(details, dict):
        return None
    plan = details.get("trade_plan")
    if isinstance(plan, dict):
        entry = safe_float(str(plan.get("entry") or ""))
        sl = safe_float(str(plan.get("sl") or ""))
    else:
        entry = safe_float(str(details.get("planned_entry") or ""))
        sl = safe_float(str(details.get("final_stop") or ""))
    if entry <= 0 or sl <= 0 or abs(entry - sl) < 1e-9:
        return None
    return {"entry": entry, "sl": sl}


def _target_price(entry, sl, direction, multiple):
    risk = abs(entry - sl)
    return entry + risk * multiple if direction == "BUY" else entry - risk * multiple


def _first_entry_touch(bars, direction, entry):
    """Return the first bar where the market re-touches the planned entry.

    The EA uses stop orders: a BUY STOP fills when price rises to the entry,
    a SELL STOP fills when price falls to the entry.
    """
    for bar in bars:
        high = safe_float(bar.get("m5_high"))
        low = safe_float(bar.get("m5_low"))
        if high <= 0 or low <= 0:
            continue
        if direction == "BUY" and high >= entry:
            return bar
        if direction == "SELL" and low <= entry:
            return bar
    return None


def _bar_ticks(bar: dict[str, Any]) -> list[float] | None:
    """Return a chronological tick-price sequence for the bar, if available.

    Supported shapes: a list of numeric prices, or a list of dicts carrying
    "price"/"bid"/"ask". Returns None when no usable tick data exists.
    """
    raw = bar.get("ticks")
    if not isinstance(raw, list) or not raw:
        return None
    prices: list[float] = []
    for item in raw:
        if isinstance(item, (int, float)):
            value = float(item)
        elif isinstance(item, dict):
            value = safe_float(str(item.get("price") or item.get("bid") or ""))
        else:
            value = safe_float(str(item))
        if value > 0:
            prices.append(value)
    return prices or None


def _tick_exit_price(tick, direction: str) -> float:
    """Price side used for SL/TP exits: bid for BUY, ask for SELL."""
    if isinstance(tick, (tuple, list)) and len(tick) >= 2:
        return float(tick[1] if direction == "SELL" else tick[0])
    return float(tick)


def _tick_entry_price(tick, direction: str) -> float:
    """Price side used for entry fills: ask for BUY, bid for SELL."""
    if isinstance(tick, (tuple, list)) and len(tick) >= 2:
        return float(tick[1] if direction == "BUY" else tick[0])
    return float(tick)


def _resolve_first_tick_level(
    ticks: list[float],
    direction: str,
    entry: float,
    sl: float,
    labels: list[str],
) -> str | None:
    """Return the first level label touched by the tick sequence, or None."""
    for tick in ticks:
        price = _tick_exit_price(tick, direction)
        for label in labels:
            if label == "SL":
                hit = price >= sl if direction == "SELL" else price <= sl
            else:
                multiple = float(label[:-1])
                target = _target_price(entry, sl, direction, multiple)
                hit = price <= target if direction == "SELL" else price >= target
            if hit:
                return label
    return None


def _level_price(entry, sl, direction, label):
    if label == "SL":
        return sl
    return _target_price(entry, sl, direction, float(label[:-1]))


def _bar_touched_levels(high, low, direction, entry, sl, labels):
    """Return the level labels touched by a bar's [low, high] range."""
    touched = []
    for label in labels:
        price = _level_price(entry, sl, direction, label)
        if label == "SL":
            hit = (low <= price) if direction == "BUY" else (high >= price)
        else:
            hit = (high >= price) if direction == "BUY" else (low <= price)
        if hit:
            touched.append(label)
    return touched


def _load_m1_bars(symbol, start_dt, end_dt):
    """M1 bars loader (injectable). Default: no M1 data available."""
    return []


def _load_ticks(symbol, start_dt, end_dt):
    """Tick loader (injectable). Default: no tick data available."""
    return []


def _beijing_dt_to_utc(value):
    """Convert a (naive) Beijing-time datetime to an aware UTC datetime.

    EA CSV ``beijing_time`` is UTC+8; MetaTrader5 Python historical reads need
    UTC. The existing ``server_time`` is the broker server timezone (UTC+3 for
    Doo Singapore), which must NOT be passed directly as UTC.
    """
    if isinstance(value, str):
        value = _parse_ts(value)
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING_TZ)
    return value.astimezone(timezone.utc)


def build_mt5_m1_loader(symbol, terminal_path, diagnostics=None):
    """Return a ``m1_loader(start_dt, end_dt) -> list[dict]`` for candidate replay.

    start_dt/end_dt are Beijing naive datetimes (the same convention used by
    ``_first_touch``). Returned bars carry ``beijing_time``, ``m1_high``,
    ``m1_low`` for ``_resolve_ohlc_sequence``.
    """
    diagnostics = diagnostics if diagnostics is not None else {}

    def loader(start_dt, end_dt):
        import MetaTrader5 as mt5

        utc_start = _beijing_dt_to_utc(start_dt)
        utc_end = _beijing_dt_to_utc(end_dt)
        if utc_start is None or utc_end is None:
            diagnostics["m1_fetch_status"] = "time_conversion_failed"
            return []
        if not mt5.initialize(terminal_path, timeout=5000):
            diagnostics["m1_fetch_status"] = "mt5_not_connected"
            return []
        try:
            if not mt5.symbol_select(symbol, True):
                diagnostics["m1_fetch_status"] = "symbol_select_failed"
                return []
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, utc_start, utc_end)
            if rates is None:
                diagnostics["m1_fetch_status"] = "empty"
                return []
            bars = []
            for r in rates:
                t = datetime.fromtimestamp(float(r[0]), tz=timezone.utc).astimezone(BEIJING_TZ)
                bars.append(
                    {
                        "beijing_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                        "m1_high": float(r[2]),
                        "m1_low": float(r[3]),
                    }
                )
            diagnostics["m1_fetch_status"] = "complete" if bars else "empty"
            diagnostics["m1_count"] = len(bars)
            return bars
        finally:
            mt5.shutdown()

    return loader


def build_mt5_tick_loader(
    symbol,
    terminal_path,
    diagnostics=None,
    max_attempts=3,
    expand_seconds=60,
):
    """Return a ``tick_loader(start_dt, end_dt) -> list[float]`` that fetches
    historical bid ticks from MT5 with auto-diagnosis and range-expansion.

    ``diagnostics`` (optional dict) is populated in-place with the last fetch's
    status so the review can surface why ticks were incomplete.
    """
    diagnostics = diagnostics if diagnostics is not None else {}

    def loader(start_dt, end_dt):
        import MetaTrader5 as mt5

        utc_start = _beijing_dt_to_utc(start_dt)
        utc_end = _beijing_dt_to_utc(end_dt)
        diagnostics.update(
            {
                "tick_timezone_source": "beijing_to_utc",
                "tick_utc_from": utc_start.strftime("%Y-%m-%d %H:%M:%S") if utc_start else "",
                "tick_utc_to": utc_end.strftime("%Y-%m-%d %H:%M:%S") if utc_end else "",
            }
        )
        if utc_start is None or utc_end is None:
            diagnostics.update({"tick_fetch_status": "time_conversion_failed"})
            return []

        last_error = ""
        prices: list[float] = []
        ticks: list[Any] = []
        for attempt in range(1, max_attempts + 1):
            diagnostics["tick_fetch_attempts"] = attempt
            if not mt5.initialize(terminal_path, timeout=5000):
                last_error = f"MT5终端未连接（last_error={mt5.last_error()}）"
                diagnostics.update({"tick_fetch_status": "mt5_not_connected", "tick_fetch_error": last_error})
                return []
            try:
                if not mt5.symbol_select(symbol, True):
                    last_error = f"交易品种{symbol}未选中（last_error={mt5.last_error()}）"
                    diagnostics.update({"tick_fetch_status": "symbol_select_failed", "tick_fetch_error": last_error})
                    return []
                span = timedelta(seconds=expand_seconds * (attempt - 1))
                req_from = utc_start - span
                req_to = utc_end + span
                diagnostics["tick_request_from"] = req_from.strftime("%Y-%m-%d %H:%M:%S")
                diagnostics["tick_request_to"] = req_to.strftime("%Y-%m-%d %H:%M:%S")
                raw_ticks = mt5.copy_ticks_range(symbol, req_from, req_to, mt5.COPY_TICKS_ALL)
                ticks = list(raw_ticks) if raw_ticks is not None else []
                last_error = f"last_error={mt5.last_error()}" if mt5.last_error() else ""
                prices = [
                    (float(t["bid"]), float(t["ask"]))
                    for t in ticks
                    if float(t["bid"] or 0) > 0 and float(t["ask"] or 0) > 0
                ]
                diagnostics["tick_count"] = len(prices)
                diagnostics["tick_first_time"] = (
                    datetime.fromtimestamp(float(ticks[0]["time"]), tz=timezone.utc)
                    .astimezone(BEIJING_TZ)
                    .strftime("%Y-%m-%d %H:%M:%S")
                    if prices else ""
                )
                diagnostics["tick_last_time"] = (
                    datetime.fromtimestamp(float(ticks[-1]["time"]), tz=timezone.utc)
                    .astimezone(BEIJING_TZ)
                    .strftime("%Y-%m-%d %H:%M:%S")
                    if prices else ""
                )
                diagnostics["tick_fetch_error"] = last_error
                if _ticks_cover_window(prices, ticks, req_from, req_to, utc_start, utc_end):
                    diagnostics["tick_fetch_status"] = "complete"
                    diagnostics["tick_missing_ranges"] = ""
                    return prices
                diagnostics["tick_fetch_status"] = "incomplete_retrying"
            finally:
                mt5.shutdown()
        diagnostics["tick_fetch_status"] = "incomplete_after_backfill"
        diagnostics["tick_fetch_error"] = last_error
        diagnostics["tick_missing_ranges"] = _missing_tick_ranges(ticks, utc_start, utc_end)
        return prices

    return loader


def _ticks_cover_window(prices, ticks, req_from, req_to, utc_start, utc_end):
    """Heuristic: the returned ticks reasonably cover the needed window."""
    if not prices or not ticks:
        return False
    first = float(ticks[0]["time"])
    last = float(ticks[-1]["time"])
    start_epoch = utc_start.timestamp()
    end_epoch = utc_end.timestamp()
    # Allow small boundary skew; require ticks to reach near both ends.
    return first <= start_epoch + 5 and last >= end_epoch - 5


def _missing_tick_ranges(ticks, utc_start, utc_end):
    """Return a human-readable missing range, or empty when not locatable."""
    if not ticks:
        return f"{utc_start.strftime('%H:%M:%S')} - {utc_end.strftime('%H:%M:%S')}（整段缺失）"
    first = float(ticks[0]["time"])
    last = float(ticks[-1]["time"])
    start_epoch = utc_start.timestamp()
    end_epoch = utc_end.timestamp()
    if first > start_epoch + 5:
        return (
            f"{utc_start.strftime('%H:%M:%S')} - "
            f"{datetime.fromtimestamp(first, tz=timezone.utc).astimezone(BEIJING_TZ).strftime('%H:%M:%S')}"
        )
    if last < end_epoch - 5:
        return (
            f"{datetime.fromtimestamp(last, tz=timezone.utc).astimezone(BEIJING_TZ).strftime('%H:%M:%S')} - "
            f"{utc_end.strftime('%H:%M:%S')}"
        )
    return ""


def _resolve_ohlc_sequence(
    ohlc_bars,
    direction,
    entry,
    sl,
    touched,
    high_key="m1_high",
    low_key="m1_low",
):
    """Walk OHLC bars chronologically and resolve the first touched level.

    Returns ``(label, bar_time)`` when a single level is hit first, or
    ``(None, bar_time)`` when one bar hits several levels and needs a finer
    granularity, or ``(None, "")`` when no bar in the sequence hits any level.
    """
    for bar in ohlc_bars:
        high = safe_float(bar.get(high_key))
        low = safe_float(bar.get(low_key))
        if high <= 0 or low <= 0:
            continue
        hit = _bar_touched_levels(high, low, direction, entry, sl, touched)
        if len(hit) == 1:
            return hit[0], str(bar.get("beijing_time") or bar.get("server_time") or "")
        if len(hit) > 1:
            return None, str(bar.get("beijing_time") or bar.get("server_time") or "")
    return None, ""


def _resolve_after_fill_from_m1(m1_bars, direction, entry, sl, touched):
    """用 M1 序列还原首根 M5 内成交后先触发的级别。"""
    if not m1_bars:
        return None, ""
    filled = False
    after_fill: list[dict[str, Any]] = []
    for bar in m1_bars:
        high = safe_float(bar.get("m1_high"))
        low = safe_float(bar.get("m1_low"))
        if high <= 0 or low <= 0:
            continue
        if not filled:
            hit_entry = high >= entry if direction == "BUY" else low <= entry
            if hit_entry:
                filled = True
                after_fill.append(bar)
            continue
        after_fill.append(bar)
    if not filled:
        return None, ""
    return _resolve_ohlc_sequence(
        after_fill, direction, entry, sl, touched,
    )


def _first_touch(
    bars,
    direction,
    entry,
    sl,
    entry_inside_first_bar=False,
    m1_loader=None,
    tick_loader=None,
):
    """Resolve the first touched level after a virtual fill, drilling M5 -> M1
    -> Tick before declaring the path order undeterminable.

    Returns a dict with:
      first_touch      - "SL" / "0.8R" / "1R" / "2R" or None
      time             - the M5 bar time (kept for backwards compatibility)
      order_unconfirmed - True only when no finer data could resolve the order
      resolution       - "M5" / "M1" / "tick" / "insufficient"
    """
    levels = ["SL", "2R", "1R", "0.8R"]
    for index, bar in enumerate(bars):
        high = safe_float(bar.get("m5_high"))
        low = safe_float(bar.get("m5_low"))
        if high <= 0 or low <= 0:
            continue
        touched = _bar_touched_levels(high, low, direction, entry, sl, levels)
        if not touched:
            continue
        bar_time = str(bar.get("beijing_time") or bar.get("server_time") or "")
        bar_time_dt = _parse_ts(bar_time)
        bar_open_dt = (bar_time_dt - timedelta(minutes=5)) if bar_time_dt else None
        ticks = _bar_ticks(bar)

        if index == 0 and entry_inside_first_bar:
            def _resolve_after_fill(price_seq):
                fill_index = None
                for i, tick in enumerate(price_seq):
                    price = _tick_entry_price(tick, direction)
                    if price >= entry if direction == "BUY" else price <= entry:
                        fill_index = i
                        break
                if fill_index is not None:
                    resolved = _resolve_first_tick_level(
                        price_seq[fill_index + 1:], direction, entry, sl, touched
                    )
                    if resolved is not None:
                        return {
                            "first_touch": resolved,
                            "time": bar_time,
                            "order_unconfirmed": False,
                            "resolution": "tick",
                        }
                    return None
                return None

            if ticks is not None:
                resolved = _resolve_after_fill(ticks)
                if resolved is not None:
                    return resolved
            if m1_loader is not None and bar_open_dt is not None:
                m1_bars = m1_loader(bar_open_dt, bar_open_dt + timedelta(minutes=5))
                label, m1_time = _resolve_after_fill_from_m1(
                    m1_bars, direction, entry, sl, touched
                )
                if label is not None:
                    return {
                        "first_touch": label,
                        "time": m1_time or bar_time,
                        "order_unconfirmed": False,
                        "resolution": "M1",
                    }
            if tick_loader is not None:
                start = bar_open_dt
                external_ticks = tick_loader(start, start + timedelta(minutes=5)) if start else []
                resolved = _resolve_after_fill(external_ticks)
                if resolved is not None:
                    return resolved
            return {
                "first_touch": None,
                "time": bar_time,
                "order_unconfirmed": True,
                "resolution": "insufficient",
            }

        if len(touched) == 1:
            return {
                "first_touch": touched[0],
                "time": bar_time,
                "order_unconfirmed": False,
                "resolution": "M5",
            }

        # One M5 bar covers several levels: drill into M1 then Tick.
        if m1_loader is not None:
            start = bar_open_dt
            m1_bars = m1_loader(start, start + timedelta(minutes=5)) if start else []
            label, m1_time = _resolve_ohlc_sequence(
                m1_bars, direction, entry, sl, touched
            )
            if label is not None:
                return {
                    "first_touch": label,
                    "time": m1_time or bar_time,
                    "order_unconfirmed": False,
                    "resolution": "M1",
                }

        if ticks is not None:
            resolved = _resolve_first_tick_level(
                ticks, direction, entry, sl, touched
            )
            if resolved is not None:
                return {
                    "first_touch": resolved,
                    "time": bar_time,
                    "order_unconfirmed": False,
                    "resolution": "tick",
                }

        if tick_loader is not None:
            start = bar_open_dt
            external_ticks = tick_loader(start, start + timedelta(minutes=5)) if start else []
            resolved = _resolve_first_tick_level(
                external_ticks, direction, entry, sl, touched
            )
            if resolved is not None:
                return {
                    "first_touch": resolved,
                    "time": bar_time,
                    "order_unconfirmed": False,
                    "resolution": "tick",
                }

        return {
            "first_touch": None,
            "time": bar_time,
            "order_unconfirmed": True,
            "resolution": "insufficient",
        }
    return {
        "first_touch": None,
        "time": "",
        "order_unconfirmed": False,
        "resolution": "M5",
    }


def _max_favorable_r(bars, direction, entry, sl):
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    best = 0.0
    for bar in bars:
        high = safe_float(bar.get("m5_high"))
        low = safe_float(bar.get("m5_low"))
        if high <= 0 or low <= 0:
            continue
        if direction == "BUY" and high > entry:
            best = max(best, (high - entry) / risk)
        elif direction == "SELL" and low > 0 and low < entry:
            best = max(best, (entry - low) / risk)
    return round(best, 4)


def _virtual_lifecycle_bars(bars, first, first_touch_time):
    """Limit virtual bars to the trade's lifecycle before a stop-out.

    A favorable target does not terminate the favorable-R measurement: the
    staged-exit trade keeps running after TP1/TP2. Only SL ends the virtual
    trade, so bars after the stop-out bar must not inflate ``r_max``.
    """
    if first != "SL" or not first_touch_time or not bars:
        return bars
    cutoff = _ts_key(first_touch_time)
    return [bar for bar in bars if _ts_key(_beijing_time(bar)) < cutoff]


def _virtual_after_exit_bars(bars, first, first_touch_time):
    """Return virtual bars after the stop-out (for post-exit observation)."""
    if first != "SL" or not first_touch_time or not bars:
        return []
    cutoff = _ts_key(first_touch_time)
    return [bar for bar in bars if _ts_key(_beijing_time(bar)) > cutoff]


def _snapshot_time_at_or_before(snapshots_sorted, dt):
    """Return the last snapshot time <= ``dt`` (the bar containing ``dt``)."""
    key = dt.strftime("%Y-%m-%d %H:%M:%S")
    match = ""
    for row in snapshots_sorted:
        row_key = _ts_key(_beijing_time(row))
        if row_key <= key:
            match = row_key
            continue
        break
    return match or key


def _window_bars(snapshots, start_ts, minutes):
    start_key = start_ts.strftime("%Y-%m-%d %H:%M:%S")
    end_ts = start_ts + timedelta(minutes=minutes)
    end_key = end_ts.strftime("%Y-%m-%d %H:%M:%S")
    return [
        row for row in snapshots
        if start_key <= _ts_key(_beijing_time(row)) < end_key
    ]


def _candidate_terminal_event(
    events_by_signal,
    signal_id,
    outcome,
    ai_status,
    events_by_ticket=None,
    order_ticket="",
):
    """Find the terminal event for an unfilled candidate and prefer its
    Beijing time (event rows carry both server_time and beijing_time; market
    snapshots are keyed by beijing_time)."""
    related = list(events_by_signal.get(signal_id, []))
    if events_by_ticket and order_ticket and order_ticket != "0":
        seen = {id(row) for row in related}
        for row in events_by_ticket.get(order_ticket, []):
            if id(row) not in seen:
                related.append(row)
                seen.add(id(row))
    related.sort(key=_event_time)
    if outcome == "wait_missed":
        return next(
            (ev for ev in related if ev.get("event_type") == "pending_wait_missed"),
            None,
        )
    if outcome in {"expired", "cancelled"}:
        if outcome == "cancelled":
            canceled = next(
                (ev for ev in related if ev.get("event_type") == "pending_canceled"),
                None,
            )
            if canceled:
                return canceled
        closed = [ev for ev in related if ev.get("event_type") == "pending_closed"]
        if outcome == "expired":
            return next(
                (ev for ev in closed if ev.get("reason") == "ORDER_STATE_EXPIRED"),
                None,
            )
        return next(
            (
                ev for ev in closed
                if ev.get("reason")
                and ev.get("reason") not in {"ORDER_STATE_FILLED", "ORDER_STATE_EXPIRED"}
            ),
            None,
        )
    if outcome in {"ai_rejected", "ai_error"}:
        for ev in related:
            if ev.get("event_type") == "ai_error":
                return ev
        return next(
            (ev for ev in related if ev.get("event_type") == "ai_reject"),
            None,
        )
    if outcome == "execution_blocked":
        return next(
            (
                ev for ev in related
                if ev.get("event_type") in {"execution_error", "execution_blocked"}
            ),
            None,
        )
    if outcome == "pending_untriggered":
        return next(
            (ev for ev in related if ev.get("event_type") == "pending_created"),
            None,
        )
    return None


def analyze_unfilled_candidates(
    events,
    snapshots,
    m1_loader=None,
    tick_loader=None,
    tick_diagnostics=None,
):
    """Compute post-expiry favorable-R evidence for every unfilled candidate.

    Virtual-fill rule:
      - wait_missed: the market actually reached the planned entry, so the
        virtual trade starts at the miss time.
      - expired / pending_untriggered / cancelled and other outcomes: the
        market must re-touch the planned entry after expiry to form a virtual
        fill. If it never re-touches the entry, the candidate is normal with
        no entry opportunity.
    Windows (from the virtual fill start):
      - 30 minutes and 1 hour are fixed observation windows from the fill time.
      - final window runs from the fill time to the last snapshot of the
        review day (broker-session close); it never crosses to the next day.
    Classification uses the first level touched on the virtual M5 price path:
      SL first  -> 正常未成交
      0.8R first-> 潜在执行型错失
      1R first  -> 明显执行型错失
      2R first  -> 高价值执行型错失
      insufficient data -> 无法判断
    """
    rows = build_candidate_lifecycle_rows(events)
    snapshots_sorted = sorted(snapshots, key=lambda row: _ts_key(_beijing_time(row)))
    last_snapshot_key = (
        _ts_key(_beijing_time(snapshots_sorted[-1])) if snapshots_sorted else ""
    )

    events_by_signal = defaultdict(list)
    events_by_ticket = defaultdict(list)
    for ev in events:
        sid = str(ev.get("signal_id") or "").strip()
        if sid:
            events_by_signal[sid].append(ev)
        ticket = str(ev.get("order_ticket") or "").strip()
        if ticket and ticket != "0":
            events_by_ticket[ticket].append(ev)

    items = []
    for row in rows:
        if str(row.get("outcome") or "") == "filled":
            continue
        signal_id = str(row.get("signal_id") or "")
        direction = str(row.get("direction") or "").upper()
        outcome = str(row.get("outcome") or "")
        terminal = _candidate_terminal_event(
            events_by_signal,
            signal_id,
            outcome,
            str(row.get("ai_status") or ""),
            events_by_ticket=events_by_ticket,
            order_ticket=str(row.get("order_ticket") or ""),
        )
        expiry_text = _beijing_time(terminal or {})
        expiry_dt = _parse_ts(expiry_text)
        details = _json_object(
            next(
                (ev.get("details") for ev in events_by_signal.get(signal_id, [])
                 if ev.get("event_type") == "candidate"),
                "",
            )
        )
        plan = _candidate_plan_from_details(details)
        if plan is None:
            items.append({
                "signal_id": signal_id,
                "sequence": int(row.get("sequence") or 0),
                "direction": direction,
                "outcome": outcome,
                "reason_code": map_missed_reason_code(
                    outcome, row.get("outcome_reason"),
                    events_by_signal.get(signal_id),
                ),
                "reason_text": str(row.get("outcome_reason") or ""),
                "expiry_time": expiry_text,
                "entry": 0.0,
                "sl": 0.0,
                "one_r": 0.0,
                "r30": None,
                "r60": None,
                "r_max": None,
                "window30_insufficient": True,
                "window60_insufficient": True,
                "final_insufficient": True,
                "first_touch": None,
                "first_touch_time": "",
                "entry_retouched": False,
                "entry_retouch_time": "",
                "order_unconfirmed": False,
                "replay_resolution": "insufficient",
                "classification": "无法判断",
                "judgment_cn": "暂时无法判断",
                "hit_0_8": False,
                "hit_1r": False,
                "hit_2r": False,
            })
            continue

        entry = plan["entry"]
        sl = plan["sl"]
        bars_after = []
        if expiry_dt is not None:
            start_key = expiry_dt.strftime("%Y-%m-%d %H:%M:%S")
            bars_after = [
                bar for bar in snapshots_sorted
                if _ts_key(_beijing_time(bar)) >= start_key
            ]

        # Virtual-fill start: wait_missed candidates already reached the entry,
        # so the virtual trade starts at the miss time. For every other
        # unfilled outcome the market must re-touch the planned entry after
        # expiry; until that happens there is no fillable entry and no virtual
        # trade (therefore no opportunity to lose).
        if str(outcome or "").lower() == "wait_missed":
            entry_retouched = True
            entry_retouch_time = expiry_text
        else:
            retouch_bar = _first_entry_touch(bars_after, direction, entry)
            entry_retouched = retouch_bar is not None
            entry_retouch_time = _beijing_time(retouch_bar) if retouch_bar else ""
        fill_dt = _parse_ts(entry_retouch_time) if entry_retouched else None
        if fill_dt is not None:
            # 回放起点必须是“包含真实成交/重新触达时间的那根 M5 快照”，
            # 不能跳到下一根 M5 的 bar open，否则会查错 Tick 窗口。
            fill_key = _snapshot_time_at_or_before(snapshots_sorted, fill_dt)
            virtual_bars = [
                bar for bar in snapshots_sorted
                if _ts_key(_beijing_time(bar)) >= fill_key
            ]
        else:
            virtual_bars = []

        touch = (
            _first_touch(
                virtual_bars,
                direction,
                entry,
                sl,
                entry_inside_first_bar=str(outcome or "").lower() != "wait_missed",
                m1_loader=m1_loader,
                tick_loader=tick_loader,
            )
            if virtual_bars
            else {
                "first_touch": None,
                "time": "",
                "order_unconfirmed": False,
                "resolution": "M5",
            }
        )
        first = touch["first_touch"]
        order_unconfirmed = bool(touch.get("order_unconfirmed"))
        resolution = str(touch.get("resolution") or "M5")

        b30 = _window_bars(snapshots_sorted, fill_dt, 30) if fill_dt else []
        b60 = _window_bars(snapshots_sorted, fill_dt, 60) if fill_dt else []
        r30 = _max_favorable_r(b30, direction, entry, sl) if b30 else None
        r60 = _max_favorable_r(b60, direction, entry, sl) if b60 else None
        # The virtual trade terminates on its first exit (SL or first target),
        # so post-exit favorable moves must not inflate the lifecycle max.
        lifecycle_bars = _virtual_lifecycle_bars(virtual_bars, first, touch["time"])
        r_max = (
            _max_favorable_r(lifecycle_bars, direction, entry, sl)
            if lifecycle_bars else (0.0 if virtual_bars else None)
        )
        r_max_after_exit = (
            _max_favorable_r(_virtual_after_exit_bars(virtual_bars, first, touch["time"]), direction, entry, sl)
            if virtual_bars and first == "SL" else None
        )

        final_insufficient = (not bars_after) or (
            expiry_dt is not None
            and last_snapshot_key <= expiry_dt.strftime("%Y-%m-%d %H:%M:%S")
        )
        window30_insufficient = bool(
            entry_retouched
            and (
                (not b30)
                or (
                    fill_dt is not None
                    and last_snapshot_key
                    < (fill_dt + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                )
            )
        )
        window60_insufficient = bool(
            entry_retouched
            and (
                (not b60)
                or (
                    fill_dt is not None
                    and last_snapshot_key
                    < (fill_dt + timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
                )
            )
        )

        reason_code = map_missed_reason_code(
            outcome, row.get("outcome_reason"), events_by_signal.get(signal_id),
        )
        if final_insufficient or not bars_after:
            classification = "无法判断"
        elif not entry_retouched:
            classification = "正常未成交"
        elif order_unconfirmed:
            classification = "无法判断"
        elif first is None:
            classification = "正常未成交"
        elif first == "SL":
            classification = "正常未成交"
        elif first == "0.8R":
            classification = "潜在执行型错失"
        elif first == "1R":
            classification = "明显执行型错失"
        elif first == "2R":
            classification = "高价值执行型错失"
        else:
            classification = "无法判断"

        # Human-readable display judgment (4 fixed categories). The internal
        # ``classification`` above keeps the machine codes used by history and
        # aggregate statistics; ``judgment_cn`` is what the daily cards print.
        if classification == "正常未成交":
            judgment_cn = "正常未成交"
        elif classification == "无法判断":
            judgment_cn = "暂时无法判断"
        else:
            judgment_cn = (
                "AI拒绝后错过机会" if reason_code == "AI拒绝" else "挂单后错过机会"
            )

        # R evidence and hit flags describe the virtual trade only: they count
        # from the virtual fill start and stop at the trade's first exit.
        hit_0_8 = r_max is not None and r_max >= 0.8
        hit_1r = r_max is not None and r_max >= 1.0
        hit_2r = r_max is not None and r_max >= 2.0

        items.append({
            "signal_id": signal_id,
            "sequence": int(row.get("sequence") or 0),
            "direction": direction,
            "outcome": outcome,
            "reason_code": reason_code,
            "reason_text": str(row.get("outcome_reason") or ""),
            "expiry_time": expiry_text,
            "entry": round(entry, 4),
            "sl": round(sl, 4),
            "one_r": round(abs(entry - sl), 4),
            "r30": r30,
            "r60": r60,
            "r_max": r_max,
            "r_max_after_exit": r_max_after_exit,
            "window30_insufficient": bool(window30_insufficient),
            "window60_insufficient": bool(window60_insufficient),
            "final_insufficient": bool(final_insufficient),
            "first_touch": first,
            "first_touch_time": touch["time"],
            "replay_resolution": resolution,
            "entry_retouched": entry_retouched,
            "entry_retouch_time": entry_retouch_time,
            "virtual_entry_time": entry_retouch_time if entry_retouched else "",
            "order_unconfirmed": order_unconfirmed,
            "classification": classification,
            "judgment_cn": judgment_cn,
            "hit_0_8": hit_0_8,
            "hit_1r": hit_1r,
            "hit_2r": hit_2r,
            "tick_fetch_status": str((tick_diagnostics or {}).get("tick_fetch_status") or ""),
            "tick_fetch_attempts": (tick_diagnostics or {}).get("tick_fetch_attempts"),
            "tick_fetch_error": str((tick_diagnostics or {}).get("tick_fetch_error") or ""),
            "tick_missing_ranges": str((tick_diagnostics or {}).get("tick_missing_ranges") or ""),
            "tick_utc_from": str((tick_diagnostics or {}).get("tick_utc_from") or ""),
            "tick_utc_to": str((tick_diagnostics or {}).get("tick_utc_to") or ""),
        })

    valid = [
        it for it in items
        if (
            not it["final_insufficient"]
            and not it["order_unconfirmed"]
            and it["reason_code"] != "AI拒绝"
        )
    ]
    effective_miss = [
        it for it in valid if it["classification"].endswith("执行型错失")
    ]
    return {
        "items": items,
        "total_unfilled": len(items),
        "valid_samples": len(valid),
        "miss_count": len(effective_miss),
        "miss_rate": (
            round(len(effective_miss) / len(valid) * 100.0, 2) if valid else None
        ),
        "reason_code_counts": dict(Counter(it["reason_code"] for it in items)),
        "classification_counts": dict(
            Counter(it["classification"] for it in items)
        ),
    }


def _history_path(root):
    return Path(root) / "Daily_Review" / "Missed_Candidates_History.json"


def load_missed_candidate_history(root):
    path = _history_path(root)
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(value, dict):
        records = value.get("records")
        if isinstance(records, list):
            return records
    return []


def append_missed_candidate_history(root, review_date, unfilled_review):
    """Idempotently append today's unfilled-candidate records to the long-term
    history file and return the updated summary keyed by reason_code."""
    path = _history_path(root)
    records = load_missed_candidate_history(root)
    seen = set()
    for rec in records:
        seen.add((str(rec.get("review_date") or ""), str(rec.get("signal_id") or "")))
    added = 0
    for item in unfilled_review.get("items", []):
        key = (review_date, str(item.get("signal_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "review_date": review_date,
            "signal_id": str(item.get("signal_id") or ""),
            "direction": str(item.get("direction") or ""),
            "outcome": str(item.get("outcome") or ""),
            "reason_code": str(item.get("reason_code") or ""),
            "reason_text": str(item.get("reason_text") or ""),
            "entry": item.get("entry"),
            "sl": item.get("sl"),
            "entry_retouched": bool(item.get("entry_retouched")),
            "entry_retouch_time": str(item.get("entry_retouch_time") or ""),
            "r30": item.get("r30"),
            "r60": item.get("r60"),
            "r_max": item.get("r_max"),
            "window30_insufficient": bool(item.get("window30_insufficient")),
            "window60_insufficient": bool(item.get("window60_insufficient")),
            "final_insufficient": bool(item.get("final_insufficient")),
            "first_touch": item.get("first_touch"),
            "order_unconfirmed": bool(item.get("order_unconfirmed")),
            "classification": str(item.get("classification") or ""),
            "hit_0_8": bool(item.get("hit_0_8")),
            "hit_1r": bool(item.get("hit_1r")),
            "hit_2r": bool(item.get("hit_2r")),
        })
        added += 1
    if added:
        atomic_write_json(path, {"schema_version": 1, "records": records})
    return summarize_missed_candidate_history(records)


def summarize_missed_candidate_history(records):
    """Aggregate long-term stats by fixed reason_code."""
    by_code = {}
    for code in MISSED_REASON_CODES:
        by_code[code] = {
            "total": 0,
            "normal": 0,
            "hit_0_8": 0,
            "hit_1r": 0,
            "hit_2r": 0,
            "miss_high_value": 0,
            "insufficient": 0,
        }
    for rec in records:
        code = str(rec.get("reason_code") or "")
        if code not in by_code:
            code = "其他执行原因"
        bucket = by_code[code]
        bucket["total"] += 1
        if code == "AI拒绝":
            # Filter results are preserved in history but are excluded from
            # execution-miss aggregates.
            continue
        classification = str(rec.get("classification") or "")
        if classification == "正常未成交":
            bucket["normal"] += 1
        if classification.endswith("执行型错失"):
            bucket["miss_high_value"] += 1
        if rec.get("hit_0_8"):
            bucket["hit_0_8"] += 1
        if rec.get("hit_1r"):
            bucket["hit_1r"] += 1
        if rec.get("hit_2r"):
            bucket["hit_2r"] += 1
        if rec.get("final_insufficient"):
            bucket["insufficient"] += 1
    summary = {
        "by_reason_code": by_code,
        "last_10_days": _recent_missed_summary(records, 10),
        "last_3_days": _recent_missed_summary(records, 3),
    }
    summary["validation_met"] = validation_condition_met(summary)
    return summary


def _is_consecutive_trading_day(a, b):
    """A gap of up to 3 calendar days is treated as consecutive trading days
    (Friday -> Monday and a single holiday still count as adjacent sessions)."""
    try:
        gap = (
            datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")
        ).days
    except ValueError:
        return False
    return 0 < gap <= 3


def _recent_missed_summary(records, window_days):
    """Aggregate per-code evidence over the most recent distinct trading days.

    - by_reason_code counts actual occurrences (records) inside the window.
    - max_consecutive_by_reason_code is the current run of consecutive
      trading days on which that reason_code appeared inside the window.
    """
    if not records:
        return {
            "consecutive_days": 0,
            "total": 0,
            "by_reason_code": {},
            "max_consecutive_by_reason_code": {},
            "days": [],
        }
    day_code_count: Counter = Counter()
    by_date: dict[str, set[str]] = defaultdict(set)
    for rec in records:
        day = str(rec.get("review_date") or "")
        code = str(rec.get("reason_code") or "")
        if not code or code == "AI拒绝":
            continue
        day_code_count[(day, code)] += 1
        by_date[day].add(code)
    dates = sorted(by_date)
    recent_dates = dates[-window_days:] if dates else []
    recent_set = set(recent_dates)
    counts: Counter = Counter()
    for (day, code), number in day_code_count.items():
        if day in recent_set:
            counts[code] += number
    max_consecutive = {}
    for code in set(code for _, code in day_code_count):
        ordered = sorted(
            day for day in by_date if code in by_date[day] and day in recent_set
        )
        streak = 0
        previous = None
        for day in ordered:
            if previous is not None and _is_consecutive_trading_day(previous, day):
                streak += 1
            else:
                streak = 1
            previous = day
        max_consecutive[code] = streak
    return {
        "window_days": window_days,
        "total": sum(counts.values()),
        "by_reason_code": dict(counts),
        "consecutive_days": max(max_consecutive.values(), default=0),
        "max_consecutive_by_reason_code": max_consecutive,
        "days": recent_dates,
    }


def validation_condition_met(summary):
    """Rule: the same reason_code appears on 3 consecutive trading days, or
    occurs at least 5 times in the last 10 trading days."""
    last10 = summary.get("last_10_days") or {}
    last3 = summary.get("last_3_days") or {}
    by_code_10 = last10.get("by_reason_code") or {}
    streak_3 = last3.get("max_consecutive_by_reason_code") or {}
    return bool(
        any(value >= 5 for value in by_code_10.values())
        or any(value >= 3 for value in streak_3.values())
    )
