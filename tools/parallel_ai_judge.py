"""Strict DeepSeek boundary for the blind parallel entry decision."""

from __future__ import annotations

import time
from math import isfinite
from typing import Any


GATE_IDS = tuple(f"G{index:02d}" for index in range(1, 11))
LEGAL_ROUTES = {"FIB_PA", "EMA_H23", "EMA_L23", "BOTH"}


def _text(value: Any, field: str, maximum: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > maximum:
        raise ValueError(f"{field} is too long")
    return text


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not isfinite(number) or number <= 0.0:
        raise ValueError(f"{field} must be positive and finite")
    return number


def _aligned(value: float, tick_size: float) -> bool:
    return abs(value / tick_size - round(value / tick_size)) <= 1e-6


def validate_primary_decision(value: Any, tick_size: float) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("primary AI response must be a JSON object")
    if tick_size <= 0 or not isfinite(tick_size):
        raise ValueError("tick_size must be positive")
    action = str(value.get("action", "")).strip().upper()
    direction = str(value.get("direction", "")).strip().upper()
    route = str(value.get("route", "")).strip().upper()
    confidence = value.get("confidence")
    reason = _text(value.get("reason"), "reason")
    if action not in {"OPEN", "WAIT"}:
        raise ValueError("action must be OPEN or WAIT")
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ValueError("confidence must be an integer from 0 to 100")
    conditions = value.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != 10:
        raise ValueError("conditions must contain G01 to G10")
    normalized_conditions = []
    for expected, condition in zip(GATE_IDS, conditions):
        if not isinstance(condition, dict) or str(condition.get("id", "")).upper() != expected:
            raise ValueError(f"condition order must contain {expected}")
        result = str(condition.get("result", "")).strip().upper()
        if result not in {"PASS", "FAIL", "NOT_REACHED"}:
            raise ValueError(f"{expected} result is invalid")
        normalized_conditions.append({
            "id": expected, "result": result,
            "reason": _text(condition.get("reason"), f"{expected}.reason", 160),
        })
    raw_rr_to_tp1: Any = None
    if action == "WAIT":
        if direction != "WAIT" or route != "NONE":
            raise ValueError("WAIT requires direction=WAIT and route=NONE")
        for field in ("entry", "sl", "tp1", "tp2", "rr_to_tp1"):
            if value.get(field) is not None:
                raise ValueError(f"WAIT requires {field}=null")
        plan = {field: None for field in ("entry", "sl", "tp1", "tp2", "rr_to_tp1")}
    else:
        if direction not in {"BUY", "SELL"} or route not in LEGAL_ROUTES:
            raise ValueError("OPEN requires a legal direction and route")
        plan = {field: _number(value.get(field), field) for field in ("entry", "sl", "tp1", "tp2")}
        for field in ("entry", "sl", "tp1", "tp2"):
            if not _aligned(plan[field], tick_size):
                raise ValueError(f"{field} is not aligned to tick_size")
        entry, sl, tp1, tp2 = plan["entry"], plan["sl"], plan["tp1"], plan["tp2"]
        if direction == "BUY" and not (sl < entry < tp1 <= tp2):
            raise ValueError("BUY requires SL < Entry < TP1 <= TP2")
        if direction == "SELL" and not (sl > entry > tp1 >= tp2):
            raise ValueError("SELL requires SL > Entry > TP1 >= TP2")
        # RR is derived by Python from the already-validated Entry/SL/TP1.
        # The model's own rr_to_tp1 is informational only and must not drive
        # ordering, comparison, or daily-review facts.
        plan["rr_to_tp1"] = abs(tp1 - entry) / abs(entry - sl)
        raw_rr_to_tp1 = value.get("rr_to_tp1")
    return {
        "action": action, "direction": direction, "route": route,
        "confidence": confidence, "reason": reason, **plan,
        "raw_rr_to_tp1": raw_rr_to_tp1,
        "conditions": normalized_conditions,
    }


def validate_difference_explanation(value: Any) -> str:
    if not isinstance(value, dict) or set(value) != {"conclusion", "reason", "difference"}:
        raise ValueError("difference response must contain exactly conclusion/reason/difference")
    conclusion = _text(value["conclusion"], "conclusion", 50)
    reason = _text(value["reason"], "reason", 80)
    difference = _text(value["difference"], "difference", 80)
    return f"结论：{conclusion}\n原因：{reason}\n差异：{difference}"


class PrimaryDecisionInvalid(RuntimeError):
    """A primary decision that stayed invalid after every repair attempt."""

    def __init__(self, attempts: int, cause: Exception | None) -> None:
        super().__init__(f"DeepSeek primary decision invalid after {attempts} attempts")
        self.attempts = attempts
        self.cause = cause


def call_primary_decision(
    client: Any, prompt: str, payload: dict[str, Any], max_tokens: int,
    *, tick_size: float, retries: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error: Exception | None = None
    attempts = 0
    current_prompt = prompt
    for attempt in range(retries + 1):
        attempts = attempt + 1
        try:
            raw, usage = client.call(current_prompt, payload, max_tokens=max_tokens)
            return validate_primary_decision(raw, tick_size), usage
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                current_prompt = (
                    prompt
                    + f"\n\n[修正提示] 上次返回被拒绝：{type(exc).__name__}: {exc}。"
                    "请修正后只返回合法JSON，不要Markdown。"
                )
                time.sleep(min(2 ** attempt, 8))
    raise PrimaryDecisionInvalid(attempts, last_error)
