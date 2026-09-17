"""Four-layer comparison for locked parallel AI and EA decisions."""

from __future__ import annotations

from math import isfinite
from typing import Any


CALCULATION_FIELDS = (
    ("C01", "EMA20", ("indicators", "ema20"), "ema20", "tick"),
    ("C02", "ATR14", ("indicators", "atr14"), "atr14", "tick"),
    ("C03", "RSI14", ("indicators", "rsi14"), "rsi14", 0.10),
    ("C04", "MACD柱", ("indicators", "macd_hist"), "macd_hist", 0.0001),
    ("C05", "Fib回调", ("fib", "retracement"), "fib_retracement", 0.10),
)

NORMAL_EXECUTION_BLOCKS = {
    "SESSION_CHANGED", "SPREAD_CHANGED", "EXPOSURE_EXISTS", "ACCOUNT_LOCKED",
    "PRICE_MOVED", "ENTRY_PASSED", "MARKET_CLOSED", "NO_PERMISSION",
    "INSUFFICIENT_MARGIN", "BROKER_RESTRICTION", "MT5_RETCODE",
}


def _result(classification: str, difference_id: str = "", *, alertable: bool = False,
            reason: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "classification": classification,
        "difference_id": difference_id,
        "alertable": alertable,
        "reason": reason,
        "details": details or {},
    }


def _nested(value: dict[str, Any], path: tuple[str, str]) -> Any:
    section = value.get(path[0])
    return section.get(path[1]) if isinstance(section, dict) else None


_GATE_STATUS_SYNONYMS = {
    "NOT_EVALUATED": "NOT_REACHED",
    "SKIPPED": "NOT_REACHED",
    "N/A": "NOT_REACHED",
    "NA": "NOT_REACHED",
}


def _normalize_gate_status(status: str) -> str:
    value = status.strip().upper()
    return _GATE_STATUS_SYNONYMS.get(value, value)


def _gate_map_ai(ai: dict[str, Any]) -> dict[str, str]:
    rows = ai.get("conditions")
    if not isinstance(rows, list):
        return {}
    return {str(row.get("id", "")): _normalize_gate_status(str(row.get("result", ""))) for row in rows if isinstance(row, dict)}


def _gate_map_ea(trace: dict[str, Any]) -> dict[str, str]:
    gates = trace.get("gates")
    if not isinstance(gates, dict):
        return {}
    return {str(key): _normalize_gate_status(str(value.get("status", ""))) for key, value in gates.items() if isinstance(value, dict)}


def _gate_map_reference(reference: dict[str, Any]) -> dict[str, str]:
    gates = reference.get("gates")
    if not isinstance(gates, list):
        return {}
    return {
        str(gate.get("id", "")): _normalize_gate_status(str(gate.get("result", "")))
        for gate in gates if isinstance(gate, dict)
    }


def _calculation_streak(history: list[dict[str, Any]], difference_id: str) -> int:
    streak = 1
    for row in reversed(history):
        if row.get("classification") != "CALCULATION_ANOMALY" or row.get("difference_id") != difference_id:
            break
        streak += 1
    return streak


def _input_identity_error(raw: dict[str, Any], trace: dict[str, Any]) -> str:
    for field in ("snapshot_id", "input_hash", "rule_version", "m5_time"):
        if str(raw.get(field, "")) != str(trace.get(field, "")):
            return field
    return ""


def compare_locked_results(
    independent_input: dict[str, Any], independent_facts: dict[str, Any],
    reference: dict[str, Any], ai_decision: dict[str, Any], ea_trace: dict[str, Any],
    history: list[dict[str, Any]],
    ai_status: str = "NORMAL",
) -> dict[str, Any]:
    """Separate deterministic rule audit (EA vs Reference) from trade view (EA vs DeepSeek)."""
    if not all(isinstance(value, dict) for value in (independent_input, independent_facts, reference, ea_trace)):
        return _result("UNCOMPARABLE", reason="结果不完整")
    identity_error = _input_identity_error(independent_input, ea_trace)
    if identity_error:
        return _result("DATA_SYNC_ERROR", f"D_{identity_error.upper()}", reason=f"双方{identity_error}不一致")

    tick = float(independent_input.get("tick_size", 0.0) or 0.0)
    calculation = ea_trace.get("calculation", {})
    if not isinstance(calculation, dict):
        return _result("UNCOMPARABLE", reason="EA计算轨迹缺失")
    # 规则审计：Reference 与 EA 的 G01-G10 差异（确定性，非 DeepSeek 输出）。
    ref_gates, ea_gates = _gate_map_reference(reference), _gate_map_ea(ea_trace)
    # 只有 EA 真的写出了 G01-G10（gates 非空）时才比对；V3.10.9 的 EA 在非候选K线上
    # 不写 gates，若强行比对会把每一根都误判成“G01 不一致”。
    gate_difference = ""
    if ref_gates and ea_gates:
        gate_difference = next(
            (
                gate
                for gate in (f"G{i:02d}" for i in range(1, 11))
                if ref_gates.get(gate) != ea_gates.get(gate)
            ),
            "",
        )

    ai_available = ai_status == "NORMAL" and isinstance(ai_decision, dict)
    action_flip = False
    direction_flip = False
    if ai_available:
        action_flip = str(ai_decision.get("action")) != str(ea_trace.get("local_candidate", {}).get("action"))
        direction_flip = str(ai_decision.get("direction")) not in {str(ea_trace.get("local_candidate", {}).get("direction")), "WAIT"}

    for difference_id, label, fact_path, trace_field, tolerance_value in CALCULATION_FIELDS:
        left, right = _nested(independent_facts, fact_path), calculation.get(trace_field)
        if left is None or right is None:
            continue
        try:
            left_number, right_number = float(left), float(right)
        except (TypeError, ValueError):
            return _result("UNCOMPARABLE", difference_id, reason=f"{label}不是数字")
        tolerance = tick if tolerance_value == "tick" else float(tolerance_value)
        if not isfinite(left_number) or not isfinite(right_number):
            return _result("UNCOMPARABLE", difference_id, reason=f"{label}无法计算")
        if abs(left_number - right_number) > tolerance + 1e-12:
            streak = _calculation_streak(history, difference_id)
            immediate = bool(gate_difference or action_flip or direction_flip)
            result = _result(
                "CALCULATION_ANOMALY", difference_id,
                alertable=immediate or streak >= 2,
                reason=f"同样输入，但{label}计算不同",
                details={"independent": left_number, "ea": right_number,
                         "tolerance": tolerance, "streak": streak,
                         "immediate_impact": immediate},
            )
            result["ai_status"] = ai_status
            result["rule_audit"] = _rule_audit(gate_difference)
            result["trade_view"] = _trade_view("CALCULATION_ANOMALY", difference_id, "计算层差异")
            return result

    local = ea_trace.get("local_candidate")
    if not isinstance(local, dict):
        return _result("UNCOMPARABLE", reason="EA本地结论缺失")

    if not ai_available:
        result = _result(
            "GATE_DIFFERENCE" if gate_difference else "AI_UNAVAILABLE",
            gate_difference,
            alertable=bool(gate_difference),
            reason=(f"Reference与EA在{gate_difference}不一致" if gate_difference else "平行AI不可用"),
        )
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("AI_UNAVAILABLE", "", "平行AI不可用")
        return result

    # 交易观点对比：EA 与 DeepSeek。
    ai_action = str(ai_decision.get("action", "")).upper()
    ea_action = str(local.get("action", "")).upper()
    if ai_action not in {"OPEN", "WAIT"} or ea_action not in {"OPEN", "WAIT"}:
        return _result("UNCOMPARABLE", reason="开仓结论无效")
    if ai_action != ea_action:
        result = _result("LOCAL_ENTRY_DISAGREEMENT", "ACTION", alertable=True,
                         reason=(f"独立AI={ai_action}，EA本地={ea_action}"))
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("LOCAL_ENTRY_DISAGREEMENT", "ACTION", result["reason"])
        return result
    if ai_action == "WAIT":
        result = _result("AGREE_WAIT", reason="双方都认为暂不开仓")
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("AGREE_WAIT", "", result["reason"])
        return result

    ea_direction = str(local.get("direction", "")).upper()
    ai_direction = str(ai_decision.get("direction", "")).upper()
    if ai_direction != ea_direction:
        result = _result("DIRECTION_DISAGREEMENT", "DIRECTION", alertable=True,
                         reason=f"独立AI={ai_direction}，EA={ea_direction}")
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("DIRECTION_DISAGREEMENT", "DIRECTION", result["reason"])
        return result
    ea_route = str(local.get("route", "")).upper()
    ai_route = str(ai_decision.get("route", "")).upper()
    if ai_route != ea_route:
        result = _result("LOCAL_ENTRY_DISAGREEMENT", "ROUTE", alertable=True,
                         reason=f"入场路径不同：AI={ai_route}，EA={ea_route}")
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("LOCAL_ENTRY_DISAGREEMENT", "ROUTE", result["reason"])
        return result

    review = ea_trace.get("ea_ai_review", {})
    review_status = str(review.get("status", "NOT_RUN")).upper()
    if review_status in {"FAIL", "REJECT", "ERROR"}:
        result = _result("AI_REVIEW_DISAGREEMENT", "EA_AI_REVIEW", alertable=True,
                         reason="本地条件通过，但EA自己的AI审核未放行")
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("AI_REVIEW_DISAGREEMENT", "EA_AI_REVIEW", result["reason"])
        return result

    for field in ("entry", "sl", "tp1", "tp2"):
        ai_value, ea_value = ai_decision.get(field), calculation.get(field)
        if ai_value is None or ea_value is None:
            return _result("UNCOMPARABLE", field.upper(), reason=f"{field}缺失")
        if abs(float(ai_value) - float(ea_value)) > tick + 1e-12:
            result = _result("PLAN_DISAGREEMENT", field.upper(), alertable=True,
                             reason=f"{field}相差超过1个最小跳动",
                             details={"independent": ai_value, "ea": ea_value, "tolerance": tick})
            result["ai_status"] = ai_status
            result["rule_audit"] = _rule_audit(gate_difference)
            result["trade_view"] = _trade_view("PLAN_DISAGREEMENT", field.upper(), result["reason"])
            return result
    ai_rr, ea_rr = ai_decision.get("rr_to_tp1"), calculation.get("rr")
    if ai_rr is None or ea_rr is None:
        return _result("UNCOMPARABLE", "RR", reason="R值缺失")
    if abs(float(ai_rr) - float(ea_rr)) > 0.01 + 1e-12:
        result = _result("PLAN_DISAGREEMENT", "RR", alertable=True, reason="R值相差超过0.01R")
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("PLAN_DISAGREEMENT", "RR", result["reason"])
        return result

    execution = ea_trace.get("execution", {})
    normal_block = str(execution.get("normal_block", "")).upper()
    pending_created = bool(execution.get("pending_created", False)) or str(execution.get("status", "")).upper() == "PENDING_CREATED"
    if not pending_created:
        if normal_block in NORMAL_EXECUTION_BLOCKS or int(execution.get("mt5_retcode", 0) or 0) != 0:
            result = _result("AGREE_OPEN", reason=f"方向一致，执行被正常阻止：{normal_block or 'MT5_RETCODE'}")
            result["ai_status"] = ai_status
            result["rule_audit"] = _rule_audit(gate_difference)
            result["trade_view"] = _trade_view("AGREE_OPEN", "", result["reason"])
            return result
        if bool(execution.get("prechecks_passed", False)) and review_status == "PASS":
            result = _result("EXECUTION_SUSPECT", "EXECUTION", alertable=True,
                             reason="全部检查通过，却没有挂单且找不到正常原因")
            result["ai_status"] = ai_status
            result["rule_audit"] = _rule_audit(gate_difference)
            result["trade_view"] = _trade_view("EXECUTION_SUSPECT", "EXECUTION", result["reason"])
            return result
    if pending_created and bool(execution.get("entry_touched", False)) and not bool(execution.get("fill_event", False)):
        result = _result("PENDING_FILL_ANOMALY", "FILL", alertable=True,
                         reason="价格触及挂单，但没有成交记录")
        result["ai_status"] = ai_status
        result["rule_audit"] = _rule_audit(gate_difference)
        result["trade_view"] = _trade_view("PENDING_FILL_ANOMALY", "FILL", result["reason"])
        return result
    result = _result("AGREE_OPEN", reason="双方开仓方向、路径和价格计划一致")
    result["ai_status"] = ai_status
    result["rule_audit"] = _rule_audit(gate_difference)
    result["trade_view"] = _trade_view("AGREE_OPEN", "", result["reason"])
    return result


def _rule_audit(gate_difference: str) -> dict[str, Any]:
    if gate_difference:
        return {
            "classification": "GATE_DIFFERENCE",
            "difference_id": gate_difference,
            "reason": f"Reference与EA在{gate_difference}不一致",
        }
    return {"classification": "AGREE", "difference_id": "", "reason": "Reference与EA规则一致"}


def _trade_view(classification: str, difference_id: str, reason: str) -> dict[str, Any]:
    return {"classification": classification, "difference_id": difference_id, "reason": reason}


def is_alertable(comparison: dict[str, Any], mode: str) -> bool:
    classification = str(comparison.get("classification", ""))
    if mode == "off":
        return False
    if mode == "shadow":
        if classification == "DIRECTION_DISAGREEMENT":
            return True
        if classification == "LOCAL_ENTRY_DISAGREEMENT":
            return True
        if classification == "CALCULATION_ANOMALY":
            return bool(comparison.get("details", {}).get("immediate_impact"))
        return False
    if mode in {"full", "active"}:
        return bool(comparison.get("alertable", False)) and classification not in {
            "AGREE_WAIT", "AGREE_OPEN", "DATA_SYNC_ERROR", "UNCOMPARABLE"
        }
    raise ValueError("mode must be off, shadow, active, or full")
