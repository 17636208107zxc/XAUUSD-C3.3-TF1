"""Stable contracts shared by the parallel EA/AI entry auditor.

This module is deliberately unaware of EA outcomes when constructing the primary
AI request.  Keeping that boundary here makes accidental outcome leakage testable.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any


RULE_VERSION = "EA_OPEN_V3_9_20_R1"

# V3.10.9 C3.2-B1 has its own hash material (extra parameter_version line and
# 100 M15 bars), so its canonicalizer must be used for that rule version.
V3109_RULE_VERSION = "EA_OPEN_V3_10_9_C32B1_PERCENT_RISK_R1"

SUPPORTED_RULE_VERSIONS = {
    "EA_OPEN_V3_9_14_R1",
    "EA_OPEN_V3_9_16_R1",
    "EA_OPEN_V3_9_17_R1",
    "EA_OPEN_V3_9_18_R1",
    "EA_OPEN_V3_9_19_R1",
    "EA_OPEN_V3_9_20_R1",
    V3109_RULE_VERSION,
}
SCHEMA_VERSION = 2
HASH_MATERIAL_VERSION = 1
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S")
_SNAPSHOT_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def snapshot_id_for_m5(symbol: str, timeframe: str, m5_time: str) -> str:
    """Return the cross-language identity for one closed broker-time M5 bar."""
    return _legacy_snapshot_id_for_m5(symbol, timeframe, m5_time)


_V3109_MODULE: Any = None


def _v3109_contracts() -> Any:
    global _V3109_MODULE
    if _V3109_MODULE is None:
        from tools import v3109_contracts as module

        _V3109_MODULE = module
    return _V3109_MODULE


def _is_v3109(value: Any) -> bool:
    return isinstance(value, dict) and str(value.get("rule_version")) == V3109_RULE_VERSION


def _legacy_snapshot_id_for_m5(symbol: str, timeframe: str, m5_time: str) -> str:
    when = _parse_time(m5_time, "m5_time")
    if str(timeframe).upper() != "M5":
        raise ValueError("timeframe must be M5")
    clean_symbol = _SNAPSHOT_SAFE.sub("_", str(symbol).strip())
    if not clean_symbol:
        raise ValueError("symbol must not be empty")
    return f"{clean_symbol}_M5_{when:%Y%m%d_%H%M}"


def _parse_time(value: Any, field: str) -> datetime:
    text = str(value)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
    raise ValueError(f"{field} must use YYYY-MM-DD HH:MM:SS or YYYY.MM.DD HH:MM:SS")


def _number(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    rendered = f"{number:.8f}".rstrip("0").rstrip(".")
    return "0" if rendered in ("", "-0") else rendered


def _json_scalar(value: Any, field: str) -> str:
    if value is None or isinstance(value, (str, bool, int, float)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    raise ValueError(f"{field} must be a JSON scalar")


def canonical_input_material(value: dict[str, Any]) -> str:
    """Serialize raw market input in a deterministic MQ5-compatible order."""
    if not isinstance(value, dict):
        raise ValueError("independent input must be an object")
    lines = [
        f"hash_material_version={value.get('hash_material_version')}",
        f"schema_version={value.get('schema_version')}",
        f"rule_version={value.get('rule_version', '')}",
        f"snapshot_id={value.get('snapshot_id', '')}",
        f"symbol={value.get('symbol', '')}",
        f"timeframe={value.get('timeframe', '')}",
        f"m5_time={value.get('m5_time', '')}",
        f"bid={_number(value.get('bid'), 'bid')}",
        f"ask={_number(value.get('ask'), 'ask')}",
        f"tick_size={_number(value.get('tick_size'), 'tick_size')}",
    ]
    parameters = value.get("parameters")
    account_state = value.get("account_state")
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    if not isinstance(account_state, dict):
        raise ValueError("account_state must be an object")
    for key in sorted(parameters):
        lines.append(f"param.{key}={_json_scalar(parameters[key], f'parameters.{key}')}")
    for key in sorted(account_state):
        lines.append(f"state.{key}={_json_scalar(account_state[key], f'account_state.{key}')}")
    bars = value.get("bars")
    if not isinstance(bars, list):
        raise ValueError("bars must be an array")
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise ValueError(f"bar {index} must be an object")
        volume = bar.get("tick_volume")
        if isinstance(volume, bool) or not isinstance(volume, int):
            raise ValueError(f"bar {index} tick_volume must be an integer")
        fields = (
            str(bar.get("time", "")),
            _number(bar.get("open"), f"bar {index} open"),
            _number(bar.get("high"), f"bar {index} high"),
            _number(bar.get("low"), f"bar {index} low"),
            _number(bar.get("close"), f"bar {index} close"),
            str(volume),
        )
        lines.append(f"bar.{index:03d}=" + "|".join(fields))
    return "\n".join(lines)


def canonical_input_hash(value: dict[str, Any]) -> str:
    if _is_v3109(value):
        return _v3109_contracts().canonical_input_hash(value)
    return hashlib.sha256(canonical_input_material(value).encode("utf-8")).hexdigest()


def validate_independent_input(value: Any) -> dict[str, Any]:
    if _is_v3109(value):
        return _v3109_contracts().validate_independent_input(value)
    if not isinstance(value, dict):
        raise ValueError("independent input must be an object")
    if value.get("hash_material_version") != HASH_MATERIAL_VERSION:
        raise ValueError("hash_material_version must be 1")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema_version must be 2")
    if value.get("rule_version") not in SUPPORTED_RULE_VERSIONS:
        raise ValueError(f"rule_version must be one of {sorted(SUPPORTED_RULE_VERSIONS)}")
    for key in ("snapshot_id", "symbol", "timeframe", "m5_time", "input_hash"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"{key} must not be empty")
    expected_id = snapshot_id_for_m5(value["symbol"], value["timeframe"], value["m5_time"])
    if value["snapshot_id"] != expected_id:
        raise ValueError("snapshot_id does not match market identity")
    bid = Decimal(_number(value.get("bid"), "bid"))
    ask = Decimal(_number(value.get("ask"), "ask"))
    tick = Decimal(_number(value.get("tick_size"), "tick_size"))
    if tick <= 0 or ask < bid:
        raise ValueError("invalid Bid/Ask/tick_size")
    if not isinstance(value.get("parameters"), dict) or not value["parameters"]:
        raise ValueError("parameters must be a non-empty object")
    if not isinstance(value.get("account_state"), dict):
        raise ValueError("account_state must be an object")
    bars = value.get("bars")
    if not isinstance(bars, list) or len(bars) != 200:
        raise ValueError("bars must contain exactly 200 closed M5 bars")
    previous = None
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise ValueError(f"bar {index} must be an object")
        when = _parse_time(bar.get("time"), f"bar {index} time")
        if previous is not None:
            delta_seconds = (when - previous).total_seconds()
            if delta_seconds <= 0 or delta_seconds % 300 != 0:
                raise ValueError("bars must be strictly increasing closed M5 bars")
        previous = when
    if str(bars[-1]["time"]) != value["m5_time"]:
        raise ValueError("m5_time must equal the final closed bar time")
    expected_hash = canonical_input_hash(value)
    if not re.fullmatch(r"[0-9a-f]{64}", value["input_hash"]) or value["input_hash"] != expected_hash:
        raise ValueError("input_hash does not match canonical input")
    return value


def validate_ea_trace(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("EA trace must be an object")
    required_identity = ("schema_version", "rule_version", "snapshot_id", "symbol", "timeframe", "m5_time", "input_hash")
    for key in required_identity:
        if key not in value:
            raise ValueError(f"EA trace missing {key}")
    if value["schema_version"] != SCHEMA_VERSION or value["rule_version"] not in SUPPORTED_RULE_VERSIONS:
        raise ValueError("EA trace schema/rule version mismatch")
    if value["snapshot_id"] != snapshot_id_for_m5(value["symbol"], value["timeframe"], value["m5_time"]):
        raise ValueError("EA trace snapshot_id mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value["input_hash"])):
        raise ValueError("EA trace input_hash is invalid")
    for section in ("calculation", "gates", "local_candidate", "ea_ai_review", "execution"):
        if not isinstance(value.get(section), dict):
            raise ValueError(f"EA trace missing {section} object")
    return value


def build_blind_primary_payload(
    independent_input: dict[str, Any], calculation_facts: dict[str, Any],
    ai_existing_exposure: bool = False,
    trade_stops_level: float | None = None,
    point: float | None = None,
) -> dict[str, Any]:
    """Build the primary request without accepting any EA outcome argument."""
    raw = validate_independent_input(independent_input)
    if not isinstance(calculation_facts, dict):
        raise ValueError("calculation_facts must be an object")
    state = raw.get("account_state", {})
    account_state = {
        "account_login": state.get("account_login"),
        "risk_locked": bool(state.get("risk_locked", False)),
        "trade_allowed": bool(state.get("trade_allowed", True)),
        # Trader B's own exposure only. The EA's existing_exposure is excluded
        # from the AI payload so it cannot bias the parallel AI's independent G02.
        "ai_existing_exposure": bool(ai_existing_exposure),
    }
    bid = float(raw["bid"])
    ask = float(raw["ask"])
    tick = float(raw["tick_size"])
    point_value = float(point) if point is not None else tick
    stops_level = float(trade_stops_level) if trade_stops_level is not None else 0.0
    minimum_distance = max(stops_level * point_value, tick)
    stop_order_constraints = {
        "order_semantics": "BUY_STOP for BUY, SELL_STOP for SELL",
        "point": point_value,
        "trade_stops_level": stops_level,
        "tick_size": tick,
        "minimum_pending_distance": minimum_distance,
        "bid": bid,
        "ask": ask,
        "sell_stop_entry_must_be_below": bid - minimum_distance,
        "buy_stop_entry_must_be_above": ask + minimum_distance,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "rule_version": raw["rule_version"],
        "snapshot_id": raw["snapshot_id"],
        "market": {
            "symbol": raw["symbol"],
            "timeframe": raw["timeframe"],
            "m5_time": raw["m5_time"],
            "bid": bid,
            "ask": ask,
            "tick_size": tick,
        },
        "parameters": raw["parameters"],
        "account_state": account_state,
        "stop_order_constraints": stop_order_constraints,
        "independent_facts": calculation_facts,
    }
