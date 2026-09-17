"""Structured per-trade facts for the three-group DeepSeek Pro deep review."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _trade_group(ownership: dict[str, str], row: dict[str, Any]) -> str:
    position_id = str(row.get("position_id") or "").strip()
    if position_id and position_id in ownership:
        return ownership[position_id]
    magic = str(row.get("magic") or "").strip()
    if magic == "2026072902":
        return "ai"
    if magic == "2026072903":
        return "e2e"
    return "ea"


def _match_ai_candidate(
    row: dict[str, Any], ai_candidates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    position_id = str(row.get("position_id") or "")
    # AI candidate rows carry signal_id; the AI trade row position_id is the
    # MT5 position ticket which usually equals the order/position ticket in the
    # plan. Try direct match on any candidate whose planned entry is close.
    entry = _float(row.get("entry_price"))
    for candidate in ai_candidates.values():
        planned = _float(candidate.get("planned_entry"))
        if entry is not None and planned is not None and abs(planned - entry) < 0.05:
            if str(candidate.get("direction") or "").upper() == str(row.get("direction") or "").upper():
                return candidate
    return {}


def _server_epoch(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()
        except (TypeError, ValueError):
            continue
    return None


def _ai_rule_compliance(
    root: Path | None, row: dict[str, Any], ai_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recompute deterministic rule compliance for one AI position from the
    stored M5 snapshot, with Trader B G02 (AI own exposure) reconstructed from
    the other AI positions' open/close times."""
    if root is None:
        return {"status": "unavailable"}
    try:
        from tools.parallel_ai_calculator import evaluate_ai_execution_compliance
    except Exception:
        return {"status": "unavailable"}

    position_id = str(row.get("position_id") or "")
    # Locate the snapshot: the AI plan stores signal_id keyed by position/order
    # ticket. Fall back to scanning the pending input folder by position.
    snapshot_path: Path | None = None
    from tools.ai_trade_manager import load_plans

    for plan in load_plans(root):
        pid = int(plan.get("position_ticket", 0) or plan.get("order_ticket", 0) or 0)
        if str(pid) == position_id:
            sid = str(plan.get("signal_id") or "")
            candidate = root / "Parallel_AI_V2" / "Independent_Input" / "Pending" / f"{sid}.json"
            if candidate.exists():
                snapshot_path = candidate
                break
    if snapshot_path is None:
        return {"status": "unavailable"}
    try:
        raw = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unavailable"}

    # Decision time is the closed M5 bar time, not the fill time. G02 must be
    # evaluated against positions already open at that decision moment.
    decision_epoch = _server_epoch(raw.get("m5_time")) or _server_epoch(row.get("open_time"))
    ai_existing_exposure = False
    for other in ai_rows:
        if str(other.get("position_id") or "") == str(row.get("position_id") or ""):
            continue
        other_open = _server_epoch(other.get("open_time"))
        other_close = _server_epoch(other.get("close_time"))
        if other_open is not None and other_close is not None:
            if other_open < decision_epoch < other_close:
                ai_existing_exposure = True
                break

    decision = {
        "action": "OPEN",
        "direction": row.get("direction"),
        "route": row.get("route"),
        "entry": row.get("entry_price"),
        "sl": row.get("initial_sl"),
        "tp1": row.get("tp1_price"),
        "tp2": row.get("tp2_price"),
    }
    result = evaluate_ai_execution_compliance(
        raw, decision, ai_existing_exposure
    )
    return {
        "status": result.get("compliance"),
        "block_gates": result.get("block_gate_ids"),
        "reasons": result.get("block_reasons"),
    }


def _fact_from_row(
    trader_group: str, row: dict[str, Any], route: str = "",
    reason: str = "", confidence: Any = None,
    rule_compliance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(row.get("status") or "")
    is_open = status == "open_at_day_end"
    return {
        "trader_group": trader_group,
        "position_id": str(row.get("position_id") or ""),
        "direction": str(row.get("direction") or "").upper(),
        "route": str(route or row.get("route") or ""),
        "open_time": str(row.get("open_time") or ""),
        "close_time": str(row.get("close_time") or ""),
        "entry": _float(row.get("entry_price")),
        "initial_sl": _float(row.get("initial_sl")),
        "tp1": _float(row.get("tp1_price")),
        "tp2": _float(row.get("tp2_price")),
        "initial_volume": _float(row.get("initial_volume")),
        "initial_risk_usd": _float(row.get("initial_risk")),
        "net_profit_usd": _float(row.get("whole_trade_net", row.get("net_profit"))),
        "final_r": _float(row.get("final_r")),
        "mfe_r": _float(row.get("mfe_r")),
        "mae_r": _float(row.get("mae_r")),
        "exit_reason": str(row.get("close_reason") or "") or None,
        "tp1_executed": bool(row.get("tp1_done")),
        "tp2_executed": bool(row.get("tp2_done")),
        "runner_result": bool(row.get("runner_done")),
        "status": "open" if is_open else "closed",
        "confidence": confidence,
        "ai_reason": reason,
        "rule_compliance": rule_compliance or {"status": "unavailable"},
    }


def build_trade_review_facts(
    payload: dict[str, Any], root: Path | None = None,
) -> list[dict[str, Any]]:
    """Build structured per-trade facts for the Pro deep review.

    Only settled (closed / cross-day settled) formal trades are included; E2E
    test magic (2026072903) and unknown-source trades are excluded.
    """
    ownership = {str(k): v for k, v in (payload.get("trade_ownership") or {}).items()}
    facts: list[dict[str, Any]] = []

    ea_candidates = {
        str(c.get("position_id") or ""): c
        for c in (payload.get("candidate_rows") or [])
        if c.get("position_id")
    }
    for row in (payload.get("actual_trade_rows") or []):
        group = _trade_group(ownership, dict(row))
        if group != "ea":
            continue
        if not str(row.get("status") or "").startswith("closed"):
            continue
        candidate = ea_candidates.get(str(row.get("position_id") or ""), {})
        facts.append(
            _fact_from_row(
                "EA", dict(row),
                route=str(candidate.get("route") or ""),
                reason=str(candidate.get("ai_reason") or ""),
                confidence=candidate.get("ai_confidence"),
            )
        )

    ai_candidates = {
        str(c.get("signal_id") or ""): c
        for c in (payload.get("ai_candidate_rows") or [])
    }
    ai_rows = [dict(r) for r in (payload.get("ai_trade_rows") or [])]
    for row in ai_rows:
        if not str(row.get("status") or "").startswith("closed"):
            continue
        candidate = _match_ai_candidate(row, ai_candidates)
        rule_compliance = _ai_rule_compliance(root, row, ai_rows)
        facts.append(
            _fact_from_row(
                "Parallel AI", row,
                route=str(row.get("route") or candidate.get("route") or ""),
                reason=str(candidate.get("reason") or ""),
                confidence=candidate.get("confidence"),
                rule_compliance=rule_compliance,
            )
        )

    facts.sort(key=lambda f: (f["trader_group"], f["open_time"] or f["close_time"]))
    return facts


TRADE_REVIEW_FIELDS = {
    "per_trade_reviews",
    "group_ea",
    "group_ai",
    "group_manual",
    "overall",
    "insights_supported",
    "insights_observe",
    "insights_avoid",
}


def validate_trade_review(value: Any, facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate the Pro deep-review output against the frozen Python facts.

    The per-trade review count and position ids must exactly match the input
    facts; otherwise the review is INVALID and may be retried once with the
    concrete error.
    """
    if not isinstance(value, dict) or set(value) != TRADE_REVIEW_FIELDS:
        raise ValueError("trade review must contain exact fields")
    reviews = value.get("per_trade_reviews")
    if not isinstance(reviews, list) or len(reviews) != len(facts):
        raise ValueError(
            f"per-trade review count mismatch: expected {len(facts)}, got "
            f"{len(reviews) if isinstance(reviews, list) else 'non-list'}"
        )
    fact_ids = {str(f["position_id"]) for f in facts}
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("per-trade review must be an object")
        pid = str(review.get("position_id") or "").strip()
        if pid not in fact_ids:
            raise ValueError(f"per-trade review position_id not in facts: {pid}")
        if not str(review.get("review") or "").strip():
            raise ValueError(f"per-trade review empty for {pid}")
    result = dict(value)
    for field in (
        "group_ea", "group_ai", "group_manual", "overall",
        "insights_supported", "insights_observe", "insights_avoid",
    ):
        raw = value.get(field)
        if raw is None:
            result[field] = ""
        elif isinstance(raw, str):
            result[field] = raw
        elif isinstance(raw, list):
            result[field] = "\n".join(str(item).strip() for item in raw if str(item).strip())
        else:
            raise ValueError(f"{field} must be string or list")
    return result


def generate_trade_review(
    client: Any, prompt: str, facts: list[dict[str, Any]], max_tokens: int = 2400,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call DeepSeek Pro for the deep review; retry once on validation failure."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            raw, usage = client.call(
                prompt, {"trade_review_facts": facts}, max_tokens=max_tokens
            )
            return validate_trade_review(raw, facts), usage
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                prompt = prompt + f"\n\n[修正提示] 上次返回被拒绝：{type(exc).__name__}: {exc}。请修正后只返回合法JSON。"
    raise ValueError(f"trade review invalid after retry: {last_error}")
