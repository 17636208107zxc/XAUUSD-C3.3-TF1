from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tools.review_core import atomic_write_json, atomic_write_text


SERVER_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
M5_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "ema20",
    "atr14",
    "rsi14",
    "macd_hist",
)
CONTEXT_TIMEFRAMES = ("m15", "h1", "h4")


def _parse_server_time(value: str) -> datetime:
    return datetime.strptime(str(value).strip(), SERVER_TIME_FORMAT)


def _number(value: Any) -> int | float:
    text = str(value).strip()
    if not text:
        raise ValueError("market value is empty")
    number = float(text)
    if number.is_integer() and "." not in text:
        return int(number)
    return number


def task_id_for_m5(m5_time: str) -> str:
    stamp = _parse_server_time(m5_time)
    return f"INDEPENDENT_AI_{stamp:%Y%m%d_%H%M}"


def ordered_closed_m5(
    snapshots: list[dict[str, str]],
) -> list[dict[str, str]]:
    by_time: dict[str, dict[str, str]] = {}
    for row in snapshots:
        m5_time = str(row.get("m5_time", "")).strip()
        if not m5_time:
            continue
        try:
            _parse_server_time(m5_time)
        except ValueError:
            continue
        previous = by_time.get(m5_time)
        if previous is None or str(row.get("server_time", "")) >= str(
            previous.get("server_time", "")
        ):
            by_time[m5_time] = row
    return [by_time[key] for key in sorted(by_time, key=_parse_server_time)]


def _market_bar(row: dict[str, str], prefix: str) -> dict[str, Any]:
    time_value = str(row.get(f"{prefix}_time", "")).strip()
    _parse_server_time(time_value)
    result: dict[str, Any] = {"time": time_value}
    for field in M5_FIELDS:
        result[field] = _number(row.get(f"{prefix}_{field}", ""))
    return result


def build_market_only_payload(
    snapshots: list[dict[str, str]],
    target_m5_time: str,
    required_m5_bars: int = 13,
) -> dict[str, Any] | None:
    if required_m5_bars < 1:
        raise ValueError("required_m5_bars must be positive")
    ordered = ordered_closed_m5(snapshots)
    target_index = next(
        (
            index
            for index, row in enumerate(ordered)
            if str(row.get("m5_time", "")).strip() == target_m5_time
        ),
        None,
    )
    if target_index is None or target_index + 1 < required_m5_bars:
        return None
    selected = ordered[target_index + 1 - required_m5_bars : target_index + 1]
    target = selected[-1]
    context = {
        prefix.upper(): _market_bar(target, prefix)
        for prefix in CONTEXT_TIMEFRAMES
    }
    return {
        "symbol": str(target.get("symbol", "XAUUSD.s")).strip() or "XAUUSD.s",
        "timeframe": "M5",
        "m5_server_time": target_m5_time,
        "spread": _number(target.get("spread", "")),
        "m5_bars": [_market_bar(row, "m5") for row in selected],
        "context": context,
    }


def validate_independent_response(
    value: Any,
    min_confidence: int = 70,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("independent AI response must be an object")
    decision = str(value.get("decision", "")).strip().upper()
    confidence = value.get("confidence")
    reason = str(value.get("reason", "")).strip()
    if decision not in {"BUY", "SELL", "WAIT"}:
        raise ValueError("decision must be BUY, SELL, or WAIT")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 0 <= confidence <= 100
    ):
        raise ValueError("confidence must be an integer from 0 to 100")
    if not reason:
        raise ValueError("reason is required")
    effective = (
        decision
        if decision in {"BUY", "SELL"} and confidence >= min_confidence
        else "WAIT"
    )
    return {
        "raw_decision": decision,
        "effective_decision": effective,
        "confidence": confidence,
        "reason": reason,
    }


DETAILS_BAR_RE = re.compile(
    r"\bBar=(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}(?::\d{2})?)"
)
SIGNAL_M5_RE = re.compile(r"_M5_(\d{8})_(\d{6})_")


def event_m5_time(event: dict[str, str]) -> tuple[str, str] | None:
    details = str(event.get("details", ""))
    details_match = DETAILS_BAR_RE.search(details)
    if details_match:
        value = details_match.group(1)
        pattern = "%Y.%m.%d %H:%M:%S" if value.count(":") == 2 else "%Y.%m.%d %H:%M"
        stamp = datetime.strptime(value, pattern)
        return stamp.strftime(SERVER_TIME_FORMAT), "details_bar"
    signal_match = SIGNAL_M5_RE.search(str(event.get("signal_id", "")))
    if signal_match:
        stamp = datetime.strptime(
            signal_match.group(1) + signal_match.group(2), "%Y%m%d%H%M%S"
        )
        return stamp.strftime(SERVER_TIME_FORMAT), "signal_id"
    return None


def events_for_m5(
    events: list[dict[str, str]],
    m5_time: str,
) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    for event in events:
        alignment = event_m5_time(event)
        if alignment is None or alignment[0] != m5_time:
            continue
        copied = dict(event)
        copied["_m5_match_method"] = alignment[1]
        matched.append(copied)
    return sorted(matched, key=lambda row: str(row.get("server_time", "")))


def summarize_ea_decision(
    events: list[dict[str, str]],
    snapshot: dict[str, str],
) -> dict[str, Any]:
    pending = sorted(
        (
            event
            for event in events
            if str(event.get("event_type", "")) == "pending_created"
            and str(event.get("direction", "")).strip().upper() in {"BUY", "SELL"}
        ),
        key=lambda row: str(row.get("server_time", "")),
    )
    if pending:
        selected = pending[0]
        return {
            "decision": "TRADE",
            "direction": str(selected.get("direction", "")).strip().upper(),
            "reason": str(selected.get("reason", "")).strip() or "EA已创建挂单。",
            "stage": str(selected.get("stage", "order")).strip() or "order",
            "signal_id": str(selected.get("signal_id", "")).strip(),
            "order_ticket": str(selected.get("order_ticket", "")).strip(),
            "multiple_pending_created": len(pending) > 1,
        }
    rejects = sorted(
        (
            event
            for event in events
            if str(event.get("event_type", "")) in {"local_reject", "ai_reject"}
        ),
        key=lambda row: str(row.get("server_time", "")),
    )
    selected_reject = rejects[-1] if rejects else snapshot
    return {
        "decision": "WAIT",
        "direction": "WAIT",
        "reason": str(
            selected_reject.get(
                "reason",
                selected_reject.get("last_scan_reason", "EA没有创建挂单。"),
            )
        ).strip()
        or "EA没有创建挂单。",
        "stage": str(
            selected_reject.get(
                "stage",
                selected_reject.get("last_scan_stage", "no_pending"),
            )
        ).strip()
        or "no_pending",
        "signal_id": "",
        "order_ticket": "",
        "multiple_pending_created": False,
    }


def classify_comparison(ai: dict[str, Any], ea: dict[str, Any]) -> str:
    ai_direction = str(ai.get("effective_decision", "")).strip().upper()
    ea_direction = str(ea.get("direction", "")).strip().upper()
    if ai_direction not in {"BUY", "SELL", "WAIT"}:
        raise ValueError("invalid effective AI direction")
    if ea_direction not in {"BUY", "SELL", "WAIT"}:
        raise ValueError("invalid EA direction")
    if ai_direction == "WAIT" and ea_direction == "WAIT":
        return "AGREE_WAIT"
    if ai_direction in {"BUY", "SELL"} and ai_direction == ea_direction:
        return "AGREE_DIRECTION"
    if ai_direction == "WAIT":
        return "EA_TRADE_AI_WAIT"
    if ea_direction == "WAIT":
        return "AI_TRADE_EA_WAIT"
    return "OPPOSITE_DIRECTION"


DEFAULT_OBSERVER_STATE: dict[str, Any] = {
    "schema_version": 1,
    "cursor_m5_time": "",
    "active_task": None,
    "primary_counts_by_server_date": {},
    "consecutive_primary_failures": 0,
    "failure_episode": None,
}

CSV_FIELDS = (
    "task_id",
    "m5_server_time",
    "beijing_time",
    "close_price",
    "ai_raw_decision",
    "ai_effective_decision",
    "ai_confidence",
    "ai_reason",
    "ea_decision",
    "ea_direction",
    "ea_reason",
    "comparison_class",
    "divergence_explanation",
    "lark_notification_id",
    "lark_status",
    "status",
    "error",
)

SECRET_KEYS = {"api_key", "webhook", "authorization", "headers"}


def load_observer_state(root: Path) -> dict[str, Any]:
    path = Path(root) / "Independent_AI" / "state.json"
    state = json.loads(json.dumps(DEFAULT_OBSERVER_STATE))
    if not path.exists():
        return state
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid independent AI state: {type(exc).__name__}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("independent AI state must be an object")
    state.update(loaded)
    if not isinstance(state.get("primary_counts_by_server_date"), dict):
        state["primary_counts_by_server_date"] = {}
    return state


def save_observer_state(root: Path, state: dict[str, Any]) -> None:
    atomic_write_json(Path(root) / "Independent_AI" / "state.json", state)


def _without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_secrets(item)
            for key, item in value.items()
            if str(key).strip().lower() not in SECRET_KEYS
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_comparison_record(root: Path, record: dict[str, Any]) -> None:
    cleaned = _without_secrets(record)
    task_id = str(cleaned.get("task_id", "")).strip()
    m5_server_time = str(cleaned.get("m5_server_time", "")).strip()
    if not task_id or len(m5_server_time) < 10:
        raise ValueError("comparison record requires task_id and m5_server_time")
    server_date = m5_server_time[:10]
    output_dir = Path(root) / "Independent_AI"
    jsonl_path = output_dir / f"Records_{server_date}.jsonl"
    records = {
        str(row.get("task_id", "")): row for row in _read_jsonl(jsonl_path)
    }
    records[task_id] = cleaned
    ordered = sorted(records.values(), key=lambda row: str(row.get("m5_server_time", "")))
    jsonl_text = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered
    )
    atomic_write_text(jsonl_path, jsonl_text)

    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=list(CSV_FIELDS), extrasaction="ignore")
    writer.writeheader()
    for row in ordered:
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    atomic_write_text(
        output_dir / f"Comparisons_{server_date}.csv",
        "\ufeff" + csv_buffer.getvalue(),
    )


def fallback_divergence_explanation(
    ai: dict[str, Any],
    ea: dict[str, Any],
    comparison_class: str,
) -> str:
    labels = {
        "EA_TRADE_AI_WAIT": "EA认为条件足以挂单，但独立AI选择等待",
        "AI_TRADE_EA_WAIT": "独立AI看到了方向机会，但EA没有创建挂单",
        "OPPOSITE_DIRECTION": "独立AI与EA对方向的判断相反",
    }
    lead = labels.get(comparison_class, "双方判断依据不同")
    ai_reason = str(ai.get("reason", "独立AI没有提供理由")).strip()
    ea_reason = str(ea.get("reason", "EA没有提供理由")).strip()
    return f"{lead}。独立AI主要依据：{ai_reason}；EA主要依据：{ea_reason}。"


def _comparison_label(value: str) -> str:
    return {
        "EA_TRADE_AI_WAIT": "EA准备交易，独立AI选择等待",
        "AI_TRADE_EA_WAIT": "独立AI建议交易，EA没有挂单",
        "OPPOSITE_DIRECTION": "双方交易方向相反",
    }.get(value, value)


def build_divergence_card(record: dict[str, Any]) -> dict[str, Any]:
    ai_text = (
        f"**独立AI判断**：{record.get('ai_effective_decision', 'WAIT')}\n"
        f"**置信度**：{record.get('ai_confidence', 0)}\n"
        f"**理由**：{record.get('ai_reason', '')}"
    )
    ea_text = (
        f"**EA判断**：{record.get('ea_direction', 'WAIT')}\n"
        f"**理由**：{record.get('ea_reason', '')}"
    )
    summary_text = (
        f"**为什么出现分歧**：{record.get('divergence_explanation', '')}\n\n"
        "仅用于观察对比，不影响EA交易。"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": "orange",
                "title": {
                    "tag": "plain_text",
                    "content": "独立AI与EA分歧｜XAUUSD.s M5",
                },
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            f"**经纪商M5时间**：{record.get('m5_server_time', '')}\n"
                            f"**收盘价**：{record.get('close_price', '')}\n"
                            f"**分歧类型**：{_comparison_label(str(record.get('comparison_class', '')))}"
                        ),
                    },
                    {"tag": "markdown", "content": ai_text},
                    {"tag": "markdown", "content": ea_text},
                    {"tag": "markdown", "content": summary_text},
                ]
            },
        },
    }


def queue_observer_card(
    root: Path,
    notification_id: str,
    notification_type: str,
    card: dict[str, Any],
    facts: dict[str, Any],
) -> str:
    outbox_root = Path(root) / "Lark_Outbox"
    pending = outbox_root / "Pending" / f"{notification_id}.json"
    sent = outbox_root / "Sent" / pending.name
    uncertain = outbox_root / "Uncertain" / pending.name
    if sent.exists():
        return "already_sent"
    if uncertain.exists():
        return "uncertain"
    if pending.exists():
        return "queued"
    atomic_write_json(
        pending,
        {
            "schema_version": 1,
            "notification_id": notification_id,
            "notification_type": notification_type,
            "attempts": 0,
            "created_at_beijing": datetime.now().strftime(SERVER_TIME_FORMAT),
            "facts": _without_secrets(facts),
            "card": card,
        },
    )
    return "queued"


DIVERGENCE_CLASSES = {
    "EA_TRADE_AI_WAIT",
    "AI_TRADE_EA_WAIT",
    "OPPOSITE_DIRECTION",
}


def _system_card(title: str, content: str, template: str = "red") -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": content},
                    {
                        "tag": "markdown",
                        "content": "该异常只影响独立观察，不影响EA交易。",
                    },
                ]
            },
        },
    }


def _terminal_record(
    task_id: str,
    m5_time: str,
    now_beijing: datetime,
    payload: dict[str, Any] | None,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    close_price: Any = ""
    if payload and payload.get("m5_bars"):
        close_price = payload["m5_bars"][-1].get("close", "")
    return {
        "task_id": task_id,
        "m5_server_time": m5_time,
        "beijing_time": now_beijing.strftime(SERVER_TIME_FORMAT),
        "close_price": close_price,
        "ai_raw_decision": "",
        "ai_effective_decision": "",
        "ai_confidence": "",
        "ai_reason": "",
        "ea_decision": "",
        "ea_direction": "",
        "ea_reason": "",
        "comparison_class": "",
        "divergence_explanation": "",
        "lark_notification_id": "",
        "lark_status": "",
        "status": status,
        "error": error,
        "market_payload": payload or {},
    }


def _record_primary_failure(
    root: Path,
    state: dict[str, Any],
    task_id: str,
    m5_time: str,
    now_beijing: datetime,
    payload: dict[str, Any] | None,
    error: Exception | str,
) -> dict[str, Any]:
    error_name = type(error).__name__ if isinstance(error, Exception) else str(error)
    record = _terminal_record(
        task_id,
        m5_time,
        now_beijing,
        payload,
        "AI_FAILED",
        error_name,
    )
    write_comparison_record(root, record)
    state["consecutive_primary_failures"] = int(
        state.get("consecutive_primary_failures", 0) or 0
    ) + 1
    episode = state.get("failure_episode")
    if not isinstance(episode, dict):
        episode = {
            "started_m5_time": m5_time,
            "started_task_id": task_id,
            "alert_notification_id": "",
        }
        state["failure_episode"] = episode
    if (
        state["consecutive_primary_failures"] >= 3
        and not str(episode.get("alert_notification_id", "")).strip()
    ):
        start = _parse_server_time(str(episode["started_m5_time"]))
        notification_id = f"INDEPENDENT_AI_API_DOWN_{start:%Y%m%d_%H%M}"
        queue_observer_card(
            root,
            notification_id,
            "independent_ai_api_down",
            _system_card(
                "独立AI接口连续失败",
                (
                    f"独立AI已连续失败{state['consecutive_primary_failures']}次，"
                    "失败期间不会把缺失判断当成WAIT，也不会产生分歧通知。"
                ),
            ),
            {"started_m5_time": episode["started_m5_time"]},
        )
        episode["alert_notification_id"] = notification_id
    state["cursor_m5_time"] = m5_time
    state["active_task"] = None
    save_observer_state(root, state)
    return record


def _record_recovery_if_needed(
    root: Path,
    state: dict[str, Any],
    m5_time: str,
) -> None:
    episode = state.get("failure_episode")
    if not isinstance(episode, dict):
        state["consecutive_primary_failures"] = 0
        return
    stamp = _parse_server_time(m5_time)
    notification_id = f"INDEPENDENT_AI_API_RECOVERED_{stamp:%Y%m%d_%H%M}"
    queue_observer_card(
        root,
        notification_id,
        "independent_ai_api_recovered",
        _system_card(
            "独立AI接口已恢复",
            f"独立AI已在经纪商M5 {m5_time} 恢复正常判断。",
            template="green",
        ),
        {
            "recovered_m5_time": m5_time,
            "failure_started_m5_time": episode.get("started_m5_time", ""),
        },
    )
    state["consecutive_primary_failures"] = 0
    state["failure_episode"] = None


def _active_task_result(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(task.get("task_id", "")),
        "m5_server_time": str(task.get("m5_server_time", "")),
        "status": str(task.get("status", "WAITING_EA")),
    }


def process_independent_ai_cycle(
    config: dict[str, Any],
    root: Path,
    now_beijing: datetime,
    snapshots: list[dict[str, str]],
    events: list[dict[str, str]],
    client: Any | None,
    primary_prompt: str,
    explanation_prompt: str,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if not bool(config.get("independent_ai_enabled", True)):
        return []
    ordered = ordered_closed_m5(snapshots)
    if not ordered:
        return []
    state = load_observer_state(root)
    results: list[dict[str, Any]] = []
    active = state.get("active_task")
    now_naive = now_beijing.replace(tzinfo=None)

    if isinstance(active, dict):
        if str(active.get("status", "")) != "WAITING_EA":
            record = _record_primary_failure(
                root,
                state,
                str(active.get("task_id", "")),
                str(active.get("m5_server_time", "")),
                now_beijing,
                active.get("market_payload") if isinstance(active.get("market_payload"), dict) else None,
                "interrupted_primary_call",
            )
            results.append({"task_id": record["task_id"], "status": "AI_FAILED"})
            return results
        deadline = datetime.strptime(
            str(active.get("deadline_beijing", "")), SERVER_TIME_FORMAT
        )
        if now_naive < deadline:
            results.append(_active_task_result(active))
            return results
        m5_time = str(active["m5_server_time"])
        target_snapshot = next(
            row for row in ordered if str(row.get("m5_time", "")) == m5_time
        )
        matched_events = events_for_m5(events, m5_time)
        ea = summarize_ea_decision(matched_events, target_snapshot)
        ai = dict(active["ai"])
        comparison_class = classify_comparison(ai, ea)
        explanation = ""
        explanation_usage: dict[str, Any] | None = None
        explanation_error = ""
        if comparison_class in DIVERGENCE_CLASSES:
            try:
                if client is None:
                    raise RuntimeError("DeepSeek client unavailable")
                raw_explanation, explanation_usage = client.call(
                    explanation_prompt,
                    {
                        "comparison_class": comparison_class,
                        "independent_ai": ai,
                        "ea": ea,
                    },
                    max_tokens=int(
                        config.get("independent_ai_explanation_max_tokens", 220)
                    ),
                )
                explanation = str(raw_explanation.get("explanation", "")).strip()
                if not explanation:
                    raise ValueError("divergence explanation is empty")
                results.append(
                    {
                        "usage_type": "independent_ai_explanation",
                        "usage": explanation_usage,
                        "success": True,
                        "error": "",
                    }
                )
            except Exception as exc:
                explanation_error = type(exc).__name__
                explanation = fallback_divergence_explanation(
                    ai, ea, comparison_class
                )
                results.append(
                    {
                        "usage_type": "independent_ai_explanation",
                        "usage": None,
                        "success": False,
                        "error": explanation_error,
                    }
                )
        record = {
            "task_id": str(active["task_id"]),
            "m5_server_time": m5_time,
            "beijing_time": now_beijing.strftime(SERVER_TIME_FORMAT),
            "close_price": active["market_payload"]["m5_bars"][-1]["close"],
            "ai_raw_decision": ai["raw_decision"],
            "ai_effective_decision": ai["effective_decision"],
            "ai_confidence": ai["confidence"],
            "ai_reason": ai["reason"],
            "ea_decision": ea["decision"],
            "ea_direction": ea["direction"],
            "ea_reason": ea["reason"],
            "comparison_class": comparison_class,
            "divergence_explanation": explanation,
            "lark_notification_id": "",
            "lark_status": "",
            "status": "COMPLETED",
            "error": explanation_error,
            "market_payload": active["market_payload"],
            "primary_usage": active.get("primary_usage", {}),
            "matched_events": matched_events,
            "ea_summary": ea,
        }
        if comparison_class in DIVERGENCE_CLASSES:
            stamp = _parse_server_time(m5_time)
            notification_id = f"AI_EA_DIVERGENCE_{stamp:%Y%m%d_%H%M}"
            record["lark_notification_id"] = notification_id
            card = build_divergence_card(record)
            record["lark_status"] = queue_observer_card(
                root,
                notification_id,
                "ai_ea_divergence",
                card,
                {
                    "task_id": record["task_id"],
                    "m5_server_time": m5_time,
                    "comparison_class": comparison_class,
                },
            )
        write_comparison_record(root, record)
        state["cursor_m5_time"] = m5_time
        state["active_task"] = None
        save_observer_state(root, state)
        results.append(
            {
                "task_id": record["task_id"],
                "m5_server_time": m5_time,
                "status": "COMPLETED",
                "comparison_class": comparison_class,
                "lark_status": record["lark_status"],
            }
        )
        return results

    latest_m5_time = str(ordered[-1].get("m5_time", ""))
    cursor = str(state.get("cursor_m5_time", "")).strip()
    if not cursor:
        if not dry_run:
            state["cursor_m5_time"] = latest_m5_time
            save_observer_state(root, state)
        return [
            {
                "task_id": task_id_for_m5(latest_m5_time),
                "m5_server_time": latest_m5_time,
                "status": "BOOTSTRAPPED",
            }
        ]
    pending_rows = [
        row
        for row in ordered
        if _parse_server_time(str(row.get("m5_time", ""))) > _parse_server_time(cursor)
    ]
    if not pending_rows:
        return []
    target = pending_rows[0]
    m5_time = str(target["m5_time"])
    task_id = task_id_for_m5(m5_time)
    required_bars = int(config.get("independent_ai_m5_bars", 13))
    payload = build_market_only_payload(snapshots, m5_time, required_bars)
    if payload is None:
        record = _terminal_record(
            task_id, m5_time, now_beijing, None, "INSUFFICIENT_DATA"
        )
        if not dry_run:
            write_comparison_record(root, record)
            state["cursor_m5_time"] = m5_time
            save_observer_state(root, state)
        return [{"task_id": task_id, "m5_server_time": m5_time, "status": "INSUFFICIENT_DATA"}]
    server_date = m5_time[:10]
    counts = state.setdefault("primary_counts_by_server_date", {})
    used = int(counts.get(server_date, 0) or 0)
    daily_limit = int(config.get("independent_ai_daily_limit", 300))
    if used >= daily_limit:
        record = _terminal_record(
            task_id, m5_time, now_beijing, payload, "DAILY_LIMIT_REACHED"
        )
        if not dry_run:
            write_comparison_record(root, record)
            state["cursor_m5_time"] = m5_time
            save_observer_state(root, state)
        return [{"task_id": task_id, "m5_server_time": m5_time, "status": "DAILY_LIMIT_REACHED"}]
    if dry_run:
        return [
            {
                "task_id": task_id,
                "m5_server_time": m5_time,
                "status": "DRY_RUN",
                "payload": payload,
            }
        ]
    counts[server_date] = used + 1
    state["active_task"] = {
        "task_id": task_id,
        "m5_server_time": m5_time,
        "first_seen_beijing": now_beijing.strftime(SERVER_TIME_FORMAT),
        "market_payload": payload,
        "status": "PRIMARY_CALL_STARTED",
    }
    save_observer_state(root, state)
    try:
        if client is None:
            raise RuntimeError("DeepSeek client unavailable")
        raw_ai, usage = client.call(
            primary_prompt,
            payload,
            max_tokens=int(config.get("independent_ai_max_tokens", 350)),
        )
        ai = validate_independent_response(
            raw_ai,
            min_confidence=int(config.get("independent_ai_min_confidence", 70)),
        )
        _record_recovery_if_needed(root, state, m5_time)
        wait_seconds = int(config.get("independent_ai_ea_wait_seconds", 120))
        state["active_task"] = {
            "task_id": task_id,
            "m5_server_time": m5_time,
            "first_seen_beijing": now_beijing.strftime(SERVER_TIME_FORMAT),
            "deadline_beijing": (
                now_naive.replace(microsecond=0)
                + timedelta(seconds=wait_seconds)
            ).strftime(SERVER_TIME_FORMAT),
            "market_payload": payload,
            "ai": ai,
            "primary_usage": usage,
            "status": "WAITING_EA",
        }
        save_observer_state(root, state)
        results.append(
            {
                "usage_type": "independent_ai_primary",
                "usage": usage,
                "success": True,
                "error": "",
            }
        )
        results.append(_active_task_result(state["active_task"]))
        return results
    except Exception as exc:
        results.append(
            {
                "usage_type": "independent_ai_primary",
                "usage": None,
                "success": False,
                "error": type(exc).__name__,
            }
        )
        record = _record_primary_failure(
            root, state, task_id, m5_time, now_beijing, payload, exc
        )
        results.append(
            {"task_id": task_id, "m5_server_time": m5_time, "status": record["status"]}
        )
        return results
