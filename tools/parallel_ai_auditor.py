"""Durable per-M5 state machine for the blind parallel entry audit."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .parallel_ai_calculator import (
    build_reference_plan,
    calculate_independent_facts,
    compute_setup_group_id,
    evaluate_ai_execution_compliance,
)
from .parallel_ai_cards import (
    build_parallel_ai_card,
    fallback_short_explanation,
    notification_id_for_comparison,
)
from .parallel_ai_compare import compare_locked_results, is_alertable
from .parallel_ai_contracts import (
    build_blind_primary_payload,
    validate_ea_trace,
    validate_independent_input,
)
from .parallel_ai_judge import (
    call_primary_decision,
    validate_difference_explanation,
)
from .review_core import atomic_write_json, atomic_write_text


STATE_VERSION = 2


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_VERSION,
        "active": None,
        "completed": [],
        "primary_counts_by_broker_date": {},
        "primary_failure_streak": 0,
        "primary_failure_episode_alerted": False,
        "difference_episode": None,
        "last_input_error": "",
        "last_primary_error": "",
        "primary_rule_retries": 0,
    }


def _state_path(root: Path) -> Path:
    return root / "Parallel_AI_V2" / "State" / "auditor_state.json"


def _load_state(root: Path) -> dict[str, Any]:
    state = _default_state()
    path = _state_path(root)
    if not path.exists():
        return state
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return state
    if isinstance(loaded, dict):
        state.update(loaded)
    if not isinstance(state.get("completed"), list):
        state["completed"] = []
    return state


def _save_state(root: Path, state: dict[str, Any]) -> None:
    atomic_write_json(_state_path(root), state)


def _source_fingerprint() -> dict[str, Any]:
    """Fingerprint the running Parallel AI source + process so stale processes
    can be detected after a code change without a restart."""
    source_dir = Path(__file__).resolve().parent
    files = (
        "parallel_ai_auditor.py",
        "parallel_ai_judge.py",
        "parallel_ai_calculator.py",
        "parallel_ai_compare.py",
        "parallel_ai_contracts.py",
        "parallel_ai_cards.py",
        "ai_trade_manager.py",
    )
    digest = hashlib.sha256()
    for name in files:
        path = source_dir / name
        if path.exists():
            digest.update(path.read_bytes())
    return {
        "service_name": "parallel_ai_v2",
        "source_path": str(source_dir),
        "source_sha256": digest.hexdigest(),
        "pid": os.getpid(),
        "python_exe": sys.executable,
        "startup_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "command_line": " ".join(sys.argv),
    }


def _now_text(now: datetime) -> str:
    return now.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _parse_local(value: str, template: datetime) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=template.tzinfo)


def _pending_inputs(root: Path, completed: set[str], active_id: str) -> list[Path]:
    directory = root / "Parallel_AI_V2" / "Independent_Input" / "Pending"
    if not directory.exists():
        return []
    paths = []
    for path in directory.glob("*.json"):
        if path.stem not in completed and path.stem != active_id:
            paths.append(path)
    return sorted(paths, key=lambda item: item.name)


def _ea_state_anchors(root: Path, m5_time: str) -> tuple[int, int]:
    """读取当前快照之前最近的一根 EA Trace，得到 (M5锚点, M15锚点)。

    EA 重挂/重启或"新一轮冲动"会把微周期记忆清零；独立重放若不知道这一点，
    就会带着旧周期往前走，和 EA 判断脱节（2026-09-15 下午就是这样）。
    这里读出 EA 自己公布的"记忆为空"状态，交给重放作为起点。
    """
    from tools.v3109_calculator import (
        bar_time,
        m15_anchor_from_ea_trace,
        replay_anchor_from_ea_trace,
    )

    trace_dir = root / "Parallel_AI_V2" / "EA_Trace"
    current = bar_time({"time": m5_time})
    newest_time = 0
    newest_trace: dict[str, Any] | None = None
    for path in trace_dir.glob("*/*.json"):
        try:
            trace = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(trace, dict):
            continue
        when = bar_time({"time": str(trace.get("m5_time") or "")})
        if not when or (current and when >= current):
            continue
        if when > newest_time:
            newest_time, newest_trace = when, trace
    if newest_trace is None:
        return 0, 0
    return replay_anchor_from_ea_trace(newest_trace), m15_anchor_from_ea_trace(newest_trace)


def _queue_card(root: Path, notification_id: str, notification_type: str,
                card: dict[str, Any], facts: dict[str, Any]) -> str:
    outbox = root / "Lark_Outbox"
    filename = f"{notification_id}.json"
    for status, folder in (("already_sent", "Sent"), ("uncertain", "Uncertain"), ("queued", "Pending")):
        if (outbox / folder / filename).exists():
            return status
    atomic_write_json(outbox / "Pending" / filename, {
        "schema_version": 1,
        "notification_id": notification_id,
        "notification_type": notification_type,
        "attempts": 0,
        "facts": facts,
        "card": card,
    })
    return "queued"


def _system_card(title: str, content: str, template: str = "red") -> dict[str, Any]:
    return {"msg_type": "interactive", "card": {"schema": "2.0",
            "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "body": {"elements": [{"tag": "markdown", "content": content + "\n\n只读审计，不影响EA交易。"}]}}}


def _parallel_ai_exposure(config: dict[str, Any]) -> bool:
    """G02（Trader B 自己的策略规则）：本 AI（2026072902）有没有持仓/挂单。

    只看 parallel_ai_magic，绝不含 EA magic。MT5 读取失败时返回 False，
    避免连接问题反过来卡住 AI 的判断。
    """
    try:
        import MetaTrader5 as mt5
        from tools.ai_trade_manager import TERMINAL_PATH

        magic = int(config.get("parallel_ai_magic", 2026072902))
        if not mt5.initialize(TERMINAL_PATH, timeout=5000):
            return False
        try:
            positions = mt5.positions_get() or []
            orders = mt5.orders_get() or []
            return any(int(p.magic) == magic for p in positions) or any(
                int(o.magic) == magic for o in orders
            )
        finally:
            mt5.shutdown()
    except Exception:
        return False


def _save_rule_blocked_plan(
    root: Path, snapshot_id: str, decision: dict[str, Any],
    compliance: dict[str, Any], symbol: str,
) -> None:
    """Persist a virtual plan for a rule-blocked AI OPEN as a research sample.

    The AI opinion is kept intact; only execution is withheld.  The plan is
    written into the same AI_Trade_State folder the daily review already reads,
    so ``collect_ai_candidate_rows`` reports it as ``rule_blocked``.
    """
    from tools.ai_trade_manager import save_plan

    plan = {
        "signal_id": snapshot_id,
        "symbol": str(symbol or "XAUUSD.s"),
        "direction": str(decision.get("direction", "")).upper(),
        "route": str(decision.get("route", "")),
        "reason": str(decision.get("reason", "")),
        "confidence": int(decision.get("confidence", 0) or 0),
        "entry": decision.get("entry"),
        "sl": decision.get("sl"),
        "tp1": decision.get("tp1"),
        "tp2": decision.get("tp2"),
        "state": "rule_blocked",
        "rule_compliance": "BLOCKED",
        "rule_block_reason": "\n".join(compliance.get("block_reasons", [])),
        "block_gate_ids": list(compliance.get("block_gate_ids", [])),
        "conditions": decision.get("conditions", []),
        "created_beijing": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_plan(root, plan)


def _notify_trade_disabled(
    root: Path, state: dict[str, Any], config: dict[str, Any], dry_run: bool,
) -> None:
    """Notify once when Parallel AI keeps judging but real trading is off."""
    if bool(config.get("parallel_ai_trade_enabled", False)):
        return
    if state.get("trade_disabled_notified"):
        return
    state["trade_disabled_notified"] = True
    _save_state(root, state)
    if dry_run:
        return
    _queue_card(
        root,
        "PARALLEL_AI_V2_TRADE_DISABLED",
        "parallel_ai_system",
        _system_card(
            "🟡 AI下单功能未启用",
            "Parallel AI仍在独立判断OPEN/WAIT并记录分歧，但真实下单功能已关闭"
            "（parallel_ai_trade_enabled=false）。\nAI只保留观点和对照样本，不会发送真实订单。",
            "yellow",
        ),
        {"parallel_ai_trade_enabled": False},
    )


def _ea_open_ai_unavailable_card(content: str) -> dict[str, Any]:
    return {"msg_type": "interactive", "card": {"schema": "2.0",
            "header": {"template": "yellow",
                       "title": {"tag": "plain_text", "content": "🟡 EA已产生交易机会，但平行AI本根不可用"}},
            "body": {"elements": [{"tag": "markdown",
                                   "content": content + "\n\n该故障会造成EA/AI对照样本缺失。"}]}}}


def _queue_ea_open_ai_unavailable(
    root: Path, state: dict[str, Any], active: dict[str, Any],
    trace: dict[str, Any], dry_run: bool,
) -> str:
    snapshot_id = str(active.get("snapshot_id", "") or trace.get("snapshot_id", ""))
    m5_time = str(trace.get("m5_time", "") or active.get("m5_time", ""))
    local = trace.get("local_candidate", {}) if isinstance(trace, dict) else {}
    direction = str(local.get("direction", "NONE")).upper()
    streak = int(state.get("primary_failure_streak", 0))
    error = str(state.get("last_primary_error", ""))
    attempts = int(state.get("last_primary_attempts", 0) or 0)
    content = (
        f"时间：{m5_time}\n"
        f"EA方向：{direction}\n"
        f"EA SignalID：{snapshot_id}\n"
        f"Parallel AI状态：UNAVAILABLE\n"
        f"DeepSeek具体错误：{error}\n"
        f"请求次数：{attempts}\n"
        f"当前连续失败：{streak}次"
    )
    if dry_run:
        return "dry_run"
    return _queue_card(
        root,
        f"PARALLEL_AI_V2_EA_OPEN_AI_UNAVAILABLE_{snapshot_id}",
        "parallel_ai_system",
        _ea_open_ai_unavailable_card(content),
        {
            "snapshot_id": snapshot_id,
            "ea_direction": direction,
            "ai_status": "UNAVAILABLE",
            "error": error,
            "attempts": attempts,
            "streak": streak,
        },
    )


def _corrective_replan(
    client: Any, primary_prompt: str, payload: dict[str, Any],
    decision: dict[str, Any], plan_check: dict[str, Any],
    max_tokens: int, tick_size: float,
) -> dict[str, Any] | None:
    """One targeted replan request when an OPEN plan is not executable.

    The AI keeps its own opinion; it is only told the exact STOP-order boundary
    and asked to either produce an executable plan or switch to WAIT.
    """
    direction = str(decision.get("direction", "")).upper()
    constraints = payload.get("stop_order_constraints", {})
    bid = constraints.get("bid", "")
    ask = constraints.get("ask", "")
    minimum = plan_check.get("minimum_pending_distance", 0)
    reason = plan_check.get("reason", "")
    if direction == "SELL":
        boundary = constraints.get("sell_stop_entry_must_be_below", "")
        rule = (
            f"SELL_STOP 的 Entry 必须严格低于当前 Bid，且不得高于 {boundary}"
            f"（当前 Bid={bid}，最低挂单距离 {minimum}）。"
        )
    else:
        boundary = constraints.get("buy_stop_entry_must_be_above", "")
        rule = (
            f"BUY_STOP 的 Entry 必须严格高于当前 Ask，且不得低于 {boundary}"
            f"（当前 Ask={ask}，最低挂单距离 {minimum}）。"
        )
    hint = (
        f"\n\n[计划修正] 你上一轮判断 OPEN {direction}，但给出的 Entry 无法按 STOP 挂单执行"
        f"（原因：{reason}）。\n{rule}\n"
        "请基于同一根 M5 重新评估：如果仍然值得用 STOP 挂单等待入场，请重新给出满足条件的完整"
        " Entry/SL/TP1/TP2；如果现在已经没有合理的 STOP 入场位置，请返回 WAIT。"
        "不要为了满足格式强行生成交易。只返回 JSON。"
    )
    from .parallel_ai_judge import call_primary_decision

    try:
        decision2, _usage2 = call_primary_decision(
            client, primary_prompt + hint, payload, max_tokens,
            tick_size=tick_size, retries=1,
        )
        return decision2
    except Exception:
        return None


def _notify_exec_issue(
    root: Path, state: dict[str, Any], snapshot_id: str, error_type: str,
    title: str, content: str, facts: dict[str, Any], dry_run: bool,
) -> str:
    """Episode-deduped execution-failure notification.

    New error type alerts immediately; the 3rd consecutive same error upgrades
    to red; after that it stays quiet until the error type changes or execution
    recovers.
    """
    episode = state.get("exec_fail_episode") or {}
    prev_type = str(episode.get("error_type", ""))
    count = int(episode.get("count", 0))
    if prev_type != error_type:
        count = 1
        should_alert = True
    else:
        count += 1
        should_alert = count in (1, 3)
    state["exec_fail_episode"] = {"error_type": error_type, "count": count, "alerted": True}
    if not should_alert or dry_run:
        return ""
    level = title if count == 1 else "🔴 AI交易计划持续异常" if error_type == "AI_PLAN_INVALID" else "🔴 平行AI下单持续失败"
    return _queue_card(
        root,
        f"PARALLEL_AI_V2_EXEC_FAILED_{error_type}_{count}",
        "parallel_ai_execution",
        _system_card(level, content),
        facts,
    )


def _notify_exec_recovery(root: Path, state: dict[str, Any], snapshot_id: str, dry_run: bool) -> None:
    episode = state.get("exec_fail_episode") or {}
    if episode.get("alerted") and not dry_run:
        _queue_card(
            root,
            f"PARALLEL_AI_V2_EXEC_RECOVERED_{snapshot_id}",
            "parallel_ai_execution",
            _system_card("🔵 Parallel AI执行恢复", f"执行失败已恢复。\n信号：{snapshot_id}", "green"),
            {"snapshot_id": snapshot_id},
        )
    state["exec_fail_episode"] = None


def _position_direction(position: Any) -> str:
    # POSITION_TYPE_BUY = 0, POSITION_TYPE_SELL = 1
    return "BUY" if int(position.type) == 0 else "SELL"


def _check_exposure_block(config: dict[str, Any], direction: str) -> tuple[bool, str]:
    """Exposure Gate（EA+AI 组合层面的执行安全检查）：EA（2026072901）是否已有同方向持仓。

    职责边界：
    - G02 = 本 AI 自己有没有仓（策略事实，见 evaluate_ai_execution_compliance）。
    - Exposure Gate = EA 有没有同方向敞口（组合安全，默认关闭，不改变 AI 观点）。

    默认关闭（parallel_ai_exposure_gate_enabled=false），保持 Trader B 独立实验。
    只挡真实下单，不改变 AI 的 OPEN/WAIT 判断和对比结果。
    """
    if not bool(config.get("parallel_ai_exposure_gate_enabled", False)):
        return False, ""
    import MetaTrader5 as mt5
    from tools.ai_trade_manager import TERMINAL_PATH, EA_MAGIC

    ea_magic = int(config.get("ea_magic", EA_MAGIC))
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return False, ""
    try:
        positions = mt5.positions_get() or []
        same_direction = [
            p for p in positions
            if int(p.magic) == ea_magic and _position_direction(p) == direction
        ]
        if same_direction:
            return True, "SAME_SETUP_EXPOSURE_LIMIT"
        return False, ""
    finally:
        mt5.shutdown()


def _record(root: Path, record: dict[str, Any]) -> None:
    broker_date = str(record.get("m5_time", ""))[:10].replace(".", "-") or "unknown"
    directory = root / "Parallel_AI_V2" / "Records"
    jsonl_path = directory / f"Comparisons_{broker_date}.jsonl"
    existing = jsonl_path.read_text(encoding="utf-8") if jsonl_path.exists() else ""
    atomic_write_text(jsonl_path, existing + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    csv_path = directory / f"Comparisons_{broker_date}.csv"
    rows = [
        record.get("snapshot_id", ""), record.get("m5_time", ""),
        record.get("classification", ""), record.get("difference_id", ""),
        record.get("reason", ""), record.get("lark_status", ""),
    ]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    if not csv_path.exists():
        writer.writerow(("snapshot_id", "m5_time", "classification", "difference_id", "reason", "lark_status"))
    writer.writerow(rows)
    previous = csv_path.read_text(encoding="utf-8-sig") if csv_path.exists() else ""
    atomic_write_text(csv_path, previous + buffer.getvalue())


_PRIMARY_ALERT_THRESHOLDS = (3, 10, 30, 100)


def _snapshot_broker_date(snapshot_id: str) -> str:
    """Extract YYYY-MM-DD from XAUUSD.s_M5_20260818_1755 style ids."""
    parts = str(snapshot_id).split("_")
    if len(parts) >= 3 and len(parts[-2]) == 8 and parts[-2].isdigit():
        ymd = parts[-2]
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return ""


def _handle_primary_failure(root: Path, state: dict[str, Any], snapshot_id: str,
                            error: Exception, dry_run: bool) -> dict[str, Any]:
    state["primary_failure_streak"] = int(state.get("primary_failure_streak", 0)) + 1
    streak = state["primary_failure_streak"]
    cause = getattr(error, "cause", None)
    attempts = int(getattr(error, "attempts", 0) or 0)
    if cause is not None:
        error_text = f"{type(cause).__name__}: {cause}"
        error_type = type(cause).__name__
    else:
        error_text = f"{type(error).__name__}: {error}"
        error_type = type(error).__name__
    state["last_primary_error"] = error_text
    state["last_primary_attempts"] = attempts
    if streak == 1:
        state["primary_failure_episode_start"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    broker_date = _snapshot_broker_date(snapshot_id)
    last_threshold = int(state.get("primary_failure_last_alert_threshold", 0))
    last_date = str(state.get("primary_failure_last_alerted_date", ""))
    last_error_type = str(state.get("primary_failure_last_error_type", ""))

    should_alert = False
    level = ""
    for threshold in _PRIMARY_ALERT_THRESHOLDS:
        if streak >= threshold and threshold > last_threshold:
            should_alert = True
            level = f"连续失败达到{streak}次"
            last_threshold = threshold
            break
    if broker_date and last_date and broker_date != last_date and streak >= 3:
        should_alert = True
        level = f"跨交易日仍未恢复（{broker_date}）"
    if error_type and last_error_type and error_type != last_error_type:
        should_alert = True
        level = f"故障类型变化：{error_type}"

    lark_status = ""
    if should_alert:
        state["primary_failure_episode_alerted"] = True
        state["primary_failure_last_alert_threshold"] = last_threshold
        state["primary_failure_last_alerted_date"] = broker_date
        state["primary_failure_last_error_type"] = error_type
        if not dry_run:
            lark_status = _queue_card(
                root,
                f"PARALLEL_AI_V2_PRIMARY_UNAVAILABLE_{snapshot_id}_{streak}",
                "parallel_ai_system",
                _system_card(
                    "🔴 平行AI持续异常",
                    f"模块：Primary Judge\n"
                    f"错误：{error_text}\n"
                    f"连续失败：{streak}次\n"
                    f"触发：{level}\n"
                    f"故障开始：{state.get('primary_failure_episode_start', '')}",
                ),
                {"snapshot_id": snapshot_id, "failure_streak": streak, "error": error_text},
            )
    return {"snapshot_id": snapshot_id, "status": "AI_PRIMARY_FAILED",
            "error": error_type, "lark_status": lark_status}


def _handle_primary_recovery(root: Path, state: dict[str, Any], snapshot_id: str, dry_run: bool) -> None:
    streak = int(state.get("primary_failure_streak", 0))
    if state.get("primary_failure_episode_alerted") and not dry_run:
        _queue_card(
            root,
            f"PARALLEL_AI_V2_PRIMARY_RECOVERED_{snapshot_id}",
            "parallel_ai_system",
            _system_card(
                "🔵 平行AI已恢复",
                f"故障持续：{state.get('primary_failure_episode_start', '')} → "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"累计失败：{streak}次\n"
                f"当前状态：AI判断恢复正常",
                "green",
            ),
            {"snapshot_id": snapshot_id, "failure_streak": streak},
        )
    state["primary_failure_streak"] = 0
    state["primary_failure_episode_alerted"] = False
    state["primary_failure_last_alert_threshold"] = 0
    state["primary_failure_last_alerted_date"] = ""
    state["primary_failure_last_error_type"] = ""
    state.pop("primary_failure_episode_start", None)
    state.pop("last_primary_error", None)


def _action_divergence_sendable(comparison: dict[str, Any]) -> bool:
    """Only send the ACTION divergence card when exactly one side actually opened.

    A pure AI=OPEN vs EA=WAIT disagreement is not worth alerting if neither side
    actually placed an order (e.g. the AI entry went stale before execution).
    """
    if comparison.get("classification") != "LOCAL_ENTRY_DISAGREEMENT":
        return True
    if comparison.get("difference_id") != "ACTION":
        return True
    ai = comparison.get("ai") or {}
    ea = comparison.get("ea") or {}
    ai_action = str(ai.get("action", "")).upper()
    ea_action = str(ea.get("action", "")).upper()
    if ai_action == "OPEN":
        return str(comparison.get("ai_execution_status", "")) in {"PENDING_ACTIVE", "FILLED"}
    if ea_action == "OPEN":
        return True
    return False


def _episode_allows_queue(root: Path, state: dict[str, Any], comparison: dict[str, Any],
                          mode: str, dry_run: bool) -> bool:
    classification = comparison["classification"]
    alert = is_alertable(comparison, mode)
    episode = state.get("difference_episode")
    if classification in {"AGREE_WAIT", "AGREE_OPEN"}:
        if isinstance(episode, dict):
            episode["agreement_streak"] = int(episode.get("agreement_streak", 0)) + 1
            if episode["agreement_streak"] >= 2:
                if not dry_run:
                    _queue_card(root, f"PARALLEL_AI_V2_RECOVERED_{episode['key']}", "parallel_ai_recovery",
                                _system_card("平行判断已恢复一致", "连续2根M5未再出现同类分歧。", "green"),
                                {"recovered_episode": episode["key"]})
                state["difference_episode"] = None
        return False
    if not alert:
        return False
    if not _action_divergence_sendable(comparison):
        return False
    key = f"{classification}_{comparison.get('difference_id', '')}"
    if isinstance(episode, dict) and episode.get("key") == key:
        episode["agreement_streak"] = 0
        return False
    state["difference_episode"] = {"key": key, "started_snapshot_id": comparison.get("snapshot_id"), "agreement_streak": 0}
    return True


def process_parallel_ai_cycle(
    config: dict[str, Any], root: Path, now_beijing: datetime, client: Any,
    primary_prompt: str, difference_prompt: str, dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Advance at most one locked snapshot without sleeping or reading EA data early."""
    if not bool(config.get("parallel_ai_enabled", False)):
        return []
    root = Path(root)
    state = _load_state(root)
    fingerprint = _source_fingerprint()
    previous = state.get("service_fingerprint", {})
    if (
        previous.get("source_sha256") != fingerprint["source_sha256"]
        or previous.get("pid") != fingerprint["pid"]
    ):
        state["service_fingerprint"] = fingerprint
        _save_state(root, state)
    _notify_trade_disabled(root, state, config, dry_run)
    active = state.get("active")
    results: list[dict[str, Any]] = []

    if isinstance(active, dict):
        deadline = _parse_local(str(active["deadline_beijing"]), now_beijing)
        if now_beijing < deadline:
            return [{"snapshot_id": active["snapshot_id"], "status": "WAITING_EA_TRACE"}]
        trace_path = root / "Parallel_AI_V2" / "EA_Trace" / "Pending" / f"{active['snapshot_id']}.json"
        if not trace_path.exists():
            return [{"snapshot_id": active["snapshot_id"], "status": "WAITING_EA_TRACE"}]
        try:
            raw = validate_independent_input(json.loads(Path(active["input_path"]).read_text(encoding="utf-8-sig")))
            trace = validate_ea_trace(json.loads(trace_path.read_text(encoding="utf-8-sig")))
            facts = calculate_independent_facts(raw)
            reference = active.get("reference", {})
            decision = active.get("primary_decision")
            ai_status = str(active.get("ai_status", "NORMAL"))
            history = active.get("calculation_history", [])
            comparison = compare_locked_results(raw, facts, reference, decision, trace, history, ai_status)
        except Exception as exc:
            comparison = {"classification": "UNCOMPARABLE", "difference_id": "",
                          "alertable": False, "reason": type(exc).__name__, "details": {}}
            raw = {"m5_time": active.get("m5_time", "")}
            trace = {}
            reference = active.get("reference", {})
            decision = active.get("primary_decision")
            ai_status = str(active.get("ai_status", "NORMAL"))
        comparison.update({"snapshot_id": active["snapshot_id"], "m5_time": active["m5_time"],
                           "ai": decision, "ea": trace.get("local_candidate", {}),
                           "ai_status": ai_status, "reference_decision": reference,
                           "ai_plan_status": active.get("ai_plan_status", ""),
                           "ai_plan_reason": active.get("ai_plan_reason", ""),
                           "ai_execution_status": active.get("ai_execution_status", ""),
                           "ai_rule_compliance": active.get("ai_rule_compliance", ""),
                           "ai_rule_block_reason": active.get("ai_rule_block_reason", "")})
        if (
            comparison.get("classification") == "AI_UNAVAILABLE"
            and isinstance(trace, dict)
            and isinstance(trace.get("local_candidate"), dict)
            and str(trace.get("local_candidate", {}).get("action", "")).upper() == "OPEN"
        ):
            _queue_ea_open_ai_unavailable(root, state, active, trace, dry_run)
        lark_status = ""
        mode = str(config.get("parallel_ai_mode", "shadow")).lower()
        if _episode_allows_queue(root, state, comparison, mode, dry_run):
            explanation: dict[str, str] | str = fallback_short_explanation(comparison)
            try:
                raw_explanation, _usage = client.call(
                    difference_prompt,
                    {"independent_ai_locked": decision, "ea_locked": trace,
                     "comparison": {key: comparison.get(key) for key in ("classification", "difference_id", "reason")}},
                    max_tokens=int(config.get("parallel_ai_difference_max_tokens",
                                               config.get("parallel_ai_explanation_max_tokens", 180))),
                )
                explanation = validate_difference_explanation(raw_explanation)
            except Exception:
                pass
            if not dry_run:
                notification_id = notification_id_for_comparison(comparison)
                lark_status = _queue_card(root, notification_id, "parallel_ai_difference",
                                          build_parallel_ai_card(comparison, explanation),
                                          {"snapshot_id": active["snapshot_id"],
                                           "classification": comparison["classification"],
                                           "difference_id": comparison.get("difference_id", "")})
        record = {
            **comparison,
            "lark_status": lark_status,
            "completed_beijing": _now_text(now_beijing),
        }
        if not dry_run:
            _record(root, record)
            state["completed"] = (state.get("completed", []) + [active["snapshot_id"]])[-5000:]
            state["active"] = None
            _save_state(root, state)
        results.append({"snapshot_id": active["snapshot_id"], "status": "COMPLETED",
                        "classification": comparison["classification"], "lark_status": lark_status})
        return results

    completed = set(str(value) for value in state.get("completed", []))
    inputs = _pending_inputs(root, completed, "")
    if not inputs:
        return []
    input_path = inputs[0]
    snapshot_id = input_path.stem
    try:
        raw = validate_independent_input(json.loads(input_path.read_text(encoding="utf-8-sig")))
        broker_date = str(raw["m5_time"])[:10].replace(".", "-")
        counts = state.setdefault("primary_counts_by_broker_date", {})
        if int(counts.get(broker_date, 0)) >= int(config.get("parallel_ai_daily_limit", 300)):
            state["completed"].append(snapshot_id)
            if not dry_run:
                _save_state(root, state)
            return [{"snapshot_id": snapshot_id, "status": "DAILY_LIMIT_REACHED"}]
        # 按 EA 自己公布的"记忆是否为空"决定重放起点（仅 V3.10.9 需要）。
        m5_anchor, m15_anchor = _ea_state_anchors(root, str(raw.get("m5_time") or ""))
        anchor_kwargs = (
            {"replay_anchor": m5_anchor, "m15_replay_anchor": m15_anchor}
            if (m5_anchor or m15_anchor)
            else {}
        )
        facts = calculate_independent_facts(raw, **anchor_kwargs)
        reference = build_reference_plan(raw, **anchor_kwargs)
        from tools.ai_trade_manager import get_symbol_trade_constraints

        constraints = get_symbol_trade_constraints(str(raw.get("symbol", "XAUUSD.s")))
        ai_existing_exposure = _parallel_ai_exposure(config)
        payload = build_blind_primary_payload(
            raw, facts, ai_existing_exposure=ai_existing_exposure,
            trade_stops_level=constraints["trade_stops_level"],
            point=constraints["point"],
        )
    except Exception as exc:
        # Input parsing / local calculation failure is not an AI outage.
        state["last_input_error"] = f"{type(exc).__name__}: {exc}"
        state["completed"] = (state.get("completed", []) + [snapshot_id])[-5000:]
        if not dry_run:
            _save_state(root, state)
        return [{"snapshot_id": snapshot_id, "status": "INPUT_ERROR",
                 "error": type(exc).__name__, "message": str(exc)}]
    if dry_run:
        return [{"snapshot_id": snapshot_id, "status": "DRY_RUN", "payload": payload}]
    counts[broker_date] = int(counts.get(broker_date, 0)) + 1
    ai_status = "NORMAL"
    decision: dict[str, Any] | None = None
    usage = None
    try:
        decision, usage = call_primary_decision(
            client, primary_prompt, payload,
            int(config.get("parallel_ai_primary_max_tokens", 900)),
            tick_size=float(raw["tick_size"]),
        )
        _handle_primary_recovery(root, state, snapshot_id, dry_run=False)
    except Exception as exc:
        _handle_primary_failure(root, state, snapshot_id, exc, dry_run)
        ai_status = "UNAVAILABLE"
        decision = None
        usage = None
    ai_plan_status = ""
    ai_plan_reason = ""
    ai_execution_status = ""
    ai_rule_compliance = ""
    ai_rule_block_reason = ""
    if (
        not dry_run
        and ai_status == "NORMAL"
        and isinstance(decision, dict)
        and str(decision.get("action", "")).upper() == "OPEN"
    ):
        from tools.ai_trade_manager import validate_stop_plan

        tick_size = float(raw["tick_size"])
        trade_stops_level = float(constraints.get("trade_stops_level", 0.0))
        plan_check = validate_stop_plan(
            decision, float(raw["bid"]), float(raw["ask"]), tick_size, trade_stops_level
        )
        if plan_check["status"] != "VALID":
            ai_plan_status = "INVALID"
            ai_plan_reason = plan_check.get("reason", "")
            state["last_plan_validation"] = plan_check
            replan_decision = _corrective_replan(
                client, primary_prompt, payload, decision, plan_check,
                int(config.get("parallel_ai_primary_max_tokens", 900)), tick_size,
            )
            if isinstance(replan_decision, dict):
                if str(replan_decision.get("action", "")).upper() == "WAIT":
                    decision = replan_decision
                    ai_plan_status = ""
                    ai_plan_reason = "REPLAN_WAIT"
                    ai_execution_status = "NOT_SENT"
                else:
                    replan_check = validate_stop_plan(
                        replan_decision, float(raw["bid"]), float(raw["ask"]),
                        tick_size, trade_stops_level,
                    )
                    if replan_check["status"] == "VALID":
                        decision = replan_decision
                        ai_plan_status = "VALID"
                        ai_plan_reason = "REPLAN_VALID"
                    else:
                        decision = replan_decision
                        ai_plan_status = "INVALID"
                        ai_plan_reason = "REPLAN_STILL_INVALID"
                        ai_execution_status = "NOT_SENT"
            else:
                ai_plan_status = "INVALID"
                ai_plan_reason = "REPLAN_FAILED"
                ai_execution_status = "NOT_SENT"
        else:
            ai_plan_status = "VALID"
    if (
        not dry_run
        and ai_plan_status == "INVALID"
        and bool(config.get("parallel_ai_exec_fail_notify_enabled", False))
    ):
        direction = str(decision.get("direction", "")).upper()
        _notify_exec_issue(
            root, state, snapshot_id, "AI_PLAN_INVALID", "🟡 AI交易计划无效",
            f"信号：{snapshot_id}\n方向：{direction}\n原因：{ai_plan_reason}",
            {"snapshot_id": snapshot_id, "direction": direction, "reason": ai_plan_reason},
            dry_run,
        )
    if (
        not dry_run
        and ai_status == "NORMAL"
        and isinstance(decision, dict)
        and bool(config.get("parallel_ai_trade_enabled", False))
        and str(decision.get("action", "")).upper() == "OPEN"
        and ai_plan_status == "VALID"
    ):
        placed = set(state.get("ai_orders_placed") or [])
        if snapshot_id not in placed:
            direction = str(decision.get("direction", "")).upper()
            setup_group = compute_setup_group_id(reference, direction)
            state["last_execution_setup_group"] = setup_group
            rule_blocked = False
            if bool(config.get("parallel_ai_rule_compliance_gate_enabled", True)):
                compliance = evaluate_ai_execution_compliance(
                    raw, decision, ai_existing_exposure, facts
                )
                ai_rule_compliance = str(compliance["compliance"])
                if compliance["compliance"] == "BLOCKED":
                    ai_rule_block_reason = "\n".join(compliance["block_reasons"])
                    ai_execution_status = "NOT_SENT"
                    state["last_execution_state"] = "RULE_BLOCKED"
                    state["last_rule_block_reason"] = ai_rule_block_reason
                    state["last_rule_block_gates"] = list(compliance["block_gate_ids"])
                    _save_rule_blocked_plan(
                        root, snapshot_id, decision, compliance,
                        str(raw.get("symbol", "XAUUSD.s")),
                    )
                    placed.add(snapshot_id)
                    state["ai_orders_placed"] = sorted(placed)
                    rule_blocked = True
                else:
                    ai_rule_compliance = "PASS"
            else:
                ai_rule_compliance = "DISABLED"
            if not rule_blocked:
                blocked, block_reason = _check_exposure_block(config, direction)
                if blocked:
                    # AI decision stays OPEN; only the real-funds execution is blocked.
                    state["last_execution_state"] = "BLOCKED"
                    state["last_execution_reason"] = block_reason
                    ai_execution_status = "BLOCKED"
                    placed.add(snapshot_id)
                    state["ai_orders_placed"] = sorted(placed)
                else:
                    try:
                        from tools.ai_trade_manager import place_ai_pending

                        trade_result = place_ai_pending(root, config, snapshot_id, decision, raw)
                        state["last_trade_result"] = trade_result
                        state["last_execution_state"] = str(trade_result.get("state", ""))
                        ai_execution_status = str(trade_result.get("state", ""))
                        if trade_result.get("ok"):
                            placed.add(snapshot_id)
                            state["ai_orders_placed"] = sorted(placed)
                            _notify_exec_recovery(root, state, snapshot_id, dry_run)
                        else:
                            state["last_trade_error"] = str(trade_result.get("error", "下单失败"))
                            exec_state = str(trade_result.get("state", ""))
                            if exec_state == "STALE":
                                ai_plan_status = "STALE"
                                ai_plan_reason = trade_result.get("reason", "ENTRY_ALREADY_CROSSED")
                    except Exception as trade_error:
                        state["last_trade_error"] = f"{type(trade_error).__name__}: {trade_error}"
                        ai_execution_status = "EXECUTION_ERROR"
    wait_seconds = max(0, int(config.get("parallel_ai_ea_wait_seconds",
                                         config.get("parallel_ai_wait_seconds", 120))))
    state["active"] = {
        "status": "WAITING_EA_TRACE", "snapshot_id": snapshot_id,
        "m5_time": raw["m5_time"], "input_hash": raw["input_hash"],
        "input_path": str(input_path), "discovered_beijing": _now_text(now_beijing),
        "deadline_beijing": _now_text(now_beijing + timedelta(seconds=wait_seconds)),
        "primary_decision": decision, "primary_usage": usage,
        "ai_status": ai_status, "reference": reference,
        "ai_plan_status": ai_plan_status,
        "ai_plan_reason": ai_plan_reason,
        "ai_execution_status": ai_execution_status,
        "ai_rule_compliance": ai_rule_compliance,
        "ai_rule_block_reason": ai_rule_block_reason,
    }
    _save_state(root, state)
    return [{"snapshot_id": snapshot_id, "status": "WAITING_EA_TRACE"}]
