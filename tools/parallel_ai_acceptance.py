"""Evidence gates for reviewing (never automatically promoting) shadow mode."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .review_core import atomic_write_json, atomic_write_text


GATE_NAMES = (
    "minimum_window", "complete_broker_session", "time_alignment",
    "input_alignment", "unexplained_calculation_drift", "last_50_ai_valid",
    "replay_suite",
)


def acceptance_ready(report: dict[str, Any]) -> bool:
    gates = report.get("gates")
    return (
        isinstance(gates, dict)
        and all(gates.get(name) is True for name in GATE_NAMES)
        and report.get("requires_user_confirmation") is True
    )


def _load_records(root: Path) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    directory = root / "Parallel_AI_V2" / "Records"
    if not directory.exists():
        return []
    for path in sorted(directory.glob("Comparisons_*.jsonl")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("snapshot_id"):
                records[str(value["snapshot_id"])] = value
    return sorted(records.values(), key=lambda value: str(value.get("m5_time", "")))


def _time(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"invalid record time: {value}")


def _has_completed_session(root: Path, first_record_time: datetime | None) -> bool:
    if first_record_time is None:
        return False
    processed = root / "Session_Close_Triggers" / "Processed"
    if not processed.exists():
        return False
    for path in processed.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            session_open = _time(str(value.get("session_open_server", "")).replace(".", "-"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        # A session already in progress at shadow start is not a complete
        # observation session; require a later session whose open was observed.
        if session_open >= first_record_time:
            return True
    return False


def _replay_passed(root: Path) -> bool:
    evidence = root / "Parallel_AI_V2" / "Acceptance" / "replay_suite_passed.json"
    if not evidence.exists():
        return False
    try:
        value = json.loads(evidence.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("passed") is True


def build_acceptance_report(root: Path, now_beijing: datetime) -> dict[str, Any]:
    root = Path(root)
    records = _load_records(root)
    first_time = _time(str(records[0]["m5_time"])) if records else None
    now_naive = now_beijing.replace(tzinfo=None)
    elapsed_hours = max(0.0, (now_naive - first_time).total_seconds() / 3600.0) if first_time else 0.0
    data_errors = [row for row in records if row.get("classification") == "DATA_SYNC_ERROR"]
    time_errors = [row for row in data_errors if "M5" in str(row.get("difference_id", "")) or "SNAPSHOT" in str(row.get("difference_id", ""))]
    input_errors = [row for row in data_errors if "HASH" in str(row.get("difference_id", "")) or "RULE" in str(row.get("difference_id", ""))]
    unexplained_drift = [row for row in records if row.get("classification") == "CALCULATION_ANOMALY" and not row.get("details", {}).get("explained", False)]
    last_50 = records[-50:]
    last_50_valid = len(last_50) == 50 and all(
        row.get("classification") not in {"UNCOMPARABLE", "DATA_SYNC_ERROR"}
        and str(row.get("ai", {}).get("action", "")) in {"OPEN", "WAIT"}
        for row in last_50
    )
    gates = {
        "minimum_window": elapsed_hours >= 24.0,
        "complete_broker_session": _has_completed_session(root, first_time),
        "time_alignment": not time_errors and bool(records),
        "input_alignment": not input_errors and bool(records),
        "unexplained_calculation_drift": not unexplained_drift and bool(records),
        "last_50_ai_valid": last_50_valid,
        "replay_suite": _replay_passed(root),
    }
    report = {
        "schema_version": 1,
        "generated_beijing": now_naive.strftime("%Y-%m-%d %H:%M:%S"),
        "mode_reviewed": "shadow",
        "record_count": len(records),
        "elapsed_hours": round(elapsed_hours, 2),
        "gates": gates,
        "requires_user_confirmation": True,
        "ready_for_user_review": False,
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "note": "即使全部通过，也不会自动开启全部通知；必须由用户明确确认。",
    }
    report["ready_for_user_review"] = acceptance_ready(report)
    directory = root / "Parallel_AI_V2" / "Acceptance"
    stamp = now_naive.strftime("%Y-%m-%d")
    atomic_write_json(directory / f"acceptance_{stamp}.json", report)
    gate_lines = "\n".join(f"- [{'x' if passed else ' '}] {name}" for name, passed in gates.items())
    atomic_write_text(
        directory / f"acceptance_{stamp}.md",
        f"# Parallel AI V2 影子验收\n\n记录数：{len(records)}\n\n观察时长：{elapsed_hours:.2f}小时\n\n{gate_lines}\n\n{report['note']}\n",
    )
    return report
