"""V3.10.9 C3.2-B1 parallel-AI contracts (cross-language identity).

This mirrors the EA exactly.  Source of truth:
    XAUUSD_M15_M5_V3_10_9_C32B_TREND_RANGE_PARTICIPATION.mq5
        * ParallelAuditNumber()
        * ParallelAuditParametersMaterial()
        * ParallelAuditAccountStateMaterial()
        * BuildParallelCanonicalMaterial()
        * BuildParallelIndependentInputJson()

The V3.10.9 hash material differs from the V3.9.20 material in two ways, so the
two rule versions must never share one canonicalizer:

    1. an extra ``parameter_version=...`` header line,
    2. 100 ``m15bar.NNN=...`` lines appended after the 200 M5 ``bar.NNN`` lines.

Everything here is read-only and deterministic; no outcome data is accepted.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Any


RULE_VERSION = "EA_OPEN_V3_10_9_C32B1_PERCENT_RISK_R1"
PARAMETER_VERSION = "M15_PRIMARY_M5_MULTI_ENTRY_3SLOT_C32B1_PERCENT_RISK"
SCHEMA_VERSION = 2
HASH_MATERIAL_VERSION = 1

M5_BAR_COUNT = 200
M15_BAR_COUNT = 100

# Exact order used by ParallelAuditParametersMaterial().
PARAMETER_ORDER = (
    "absolute_max_spread_points",
    "ai_confidence_threshold",
    "atr_period",
    "ema_max_stop_expansion_ratio",
    "ema_near_distance_usd",
    "ema_period",
    "ema_signal_bar_stop_usd",
    "entry_buffer_atr",
    "entry_buffer_points",
    "fib_invalid_buffer_atr",
    "fib_max",
    "fib_min",
    "location_tolerance_atr",
    "macd_fast",
    "macd_signal",
    "macd_slow",
    "max_post_ai_move_atr",
    "max_signal_bar_usd",
    "max_sl_atr",
    "max_spread_atr_ratio",
    "min_attempt_separation_bars",
    "min_impulse_atr",
    "min_rr_to_tp1",
    "min_three_bar_move_usd",
    "pin_bar_wick_body_ratio",
    "pivot_left",
    "pivot_right",
    "rsi_period",
    "sr_tolerance_atr",
    "stop_buffer_atr",
    "stop_buffer_points",
    "strong_bar_body_ratio",
)

# Exact order used by ParallelAuditAccountStateMaterial().
ACCOUNT_STATE_ORDER = ("account_login", "existing_exposure", "risk_locked", "trade_allowed")

_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S")
_SNAPSHOT_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def audit_number(value: Any) -> str:
    """Reproduce MQL5 DoubleToString(value, 8) plus the trailing-zero trim."""
    if isinstance(value, bool):
        raise ValueError("booleans are not audit numbers")
    number = float(value)
    text = f"{number:.8f}"
    if "." in text:
        text = text.rstrip("0")
        if text.endswith("."):
            text = text[:-1]
    if text in ("", "-0"):
        return "0"
    return text


def audit_bool(value: Any) -> str:
    return "true" if bool(value) else "false"


def _parse_time(value: Any, field: str) -> datetime:
    text = str(value)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
    raise ValueError(f"{field} must use YYYY-MM-DD HH:MM:SS or YYYY.MM.DD HH:MM:SS")


def snapshot_id_for_m5(symbol: str, timeframe: str, m5_time: str) -> str:
    when = _parse_time(m5_time, "m5_time")
    if str(timeframe).upper() != "M5":
        raise ValueError("timeframe must be M5")
    clean_symbol = _SNAPSHOT_SAFE.sub("_", str(symbol).strip())
    if not clean_symbol:
        raise ValueError("symbol must not be empty")
    return f"{clean_symbol}_M5_{when:%Y%m%d_%H%M}"


def _mq_time(value: Any, field: str) -> str:
    return _parse_time(value, field).strftime("%Y.%m.%d %H:%M:%S")


def _bar_line(prefix: str, index: int, bar: dict[str, Any]) -> str:
    if not isinstance(bar, dict):
        raise ValueError(f"{prefix} bar {index} must be an object")
    volume = bar.get("tick_volume")
    if isinstance(volume, bool) or not isinstance(volume, int):
        raise ValueError(f"{prefix} bar {index} tick_volume must be an integer")
    fields = (
        _mq_time(bar.get("time"), f"{prefix} bar {index} time"),
        audit_number(bar.get("open")),
        audit_number(bar.get("high")),
        audit_number(bar.get("low")),
        audit_number(bar.get("close")),
        str(volume),
    )
    return f"{prefix}.{index:03d}=" + "|".join(fields)


def canonical_input_material(value: dict[str, Any]) -> str:
    """Reproduce BuildParallelCanonicalMaterial() byte for byte."""
    if not isinstance(value, dict):
        raise ValueError("independent input must be an object")
    parameters = value.get("parameters")
    account_state = value.get("account_state")
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    if not isinstance(account_state, dict):
        raise ValueError("account_state must be an object")

    header = [
        f"hash_material_version={value.get('hash_material_version')}",
        f"schema_version={value.get('schema_version')}",
        f"rule_version={value.get('rule_version', '')}",
        f"parameter_version={value.get('parameter_version', '')}",
        f"snapshot_id={value.get('snapshot_id', '')}",
        f"symbol={value.get('symbol', '')}",
        f"timeframe={value.get('timeframe', '')}",
        f"m5_time={_mq_time(value.get('m5_time'), 'm5_time')}",
        f"bid={audit_number(value.get('bid'))}",
        f"ask={audit_number(value.get('ask'))}",
        f"tick_size={audit_number(value.get('tick_size'))}",
    ]
    for key in PARAMETER_ORDER:
        if key not in parameters:
            raise ValueError(f"parameters missing {key}")
        header.append(f"param.{key}={_audit_scalar(parameters[key], key)}")
    for key in ACCOUNT_STATE_ORDER:
        if key not in account_state:
            raise ValueError(f"account_state missing {key}")
        header.append(f"state.{key}={_audit_scalar(account_state[key], key)}")

    bars = value.get("bars")
    m15_bars = value.get("m15_bars")
    if not isinstance(bars, list) or len(bars) != M5_BAR_COUNT:
        raise ValueError(f"bars must contain exactly {M5_BAR_COUNT} closed M5 bars")
    if not isinstance(m15_bars, list) or len(m15_bars) != M15_BAR_COUNT:
        raise ValueError(f"m15_bars must contain exactly {M15_BAR_COUNT} closed M15 bars")

    material = "\n".join(header) + "\n"
    for index, bar in enumerate(bars):
        material += _bar_line("bar", index, bar) + "\n"
    material += "\n".join(_bar_line("m15bar", index, bar) for index, bar in enumerate(m15_bars))
    return material


def _audit_scalar(value: Any, field: str) -> str:
    if isinstance(value, bool):
        return audit_bool(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return audit_number(value)
    if isinstance(value, str):
        return value
    raise ValueError(f"parameters/account_state field {field} must be a JSON scalar")


def canonical_input_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_input_material(value).encode("utf-8")).hexdigest()


def validate_independent_input(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("independent input must be an object")
    if value.get("hash_material_version") != HASH_MATERIAL_VERSION:
        raise ValueError(f"hash_material_version must be {HASH_MATERIAL_VERSION}")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    if value.get("rule_version") != RULE_VERSION:
        raise ValueError(f"rule_version must be {RULE_VERSION}")
    for key in ("parameter_version", "snapshot_id", "symbol", "timeframe", "m5_time", "input_hash"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"{key} must not be empty")
    expected_id = snapshot_id_for_m5(value["symbol"], value["timeframe"], value["m5_time"])
    if value["snapshot_id"] != expected_id:
        raise ValueError("snapshot_id does not match market identity")
    if float(value.get("tick_size", 0.0)) <= 0.0:
        raise ValueError("tick_size must be positive")
    if float(value.get("ask", 0.0)) < float(value.get("bid", 0.0)):
        raise ValueError("ask must not be below bid")
    bars = value.get("bars")
    if not isinstance(bars, list) or len(bars) != M5_BAR_COUNT:
        raise ValueError(f"bars must contain exactly {M5_BAR_COUNT} closed M5 bars")
    previous = None
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise ValueError(f"bar {index} must be an object")
        when = _parse_time(bar.get("time"), f"bar {index} time")
        if previous is not None:
            delta = (when - previous).total_seconds()
            if delta <= 0 or delta % 300 != 0:
                raise ValueError("bars must be strictly increasing closed M5 bars")
        previous = when
    if _mq_time(bars[-1]["time"], "bars[-1].time") != _mq_time(value["m5_time"], "m5_time"):
        raise ValueError("m5_time must equal the final closed M5 bar time")
    m15_bars = value.get("m15_bars")
    if not isinstance(m15_bars, list) or len(m15_bars) != M15_BAR_COUNT:
        raise ValueError(f"m15_bars must contain exactly {M15_BAR_COUNT} closed M15 bars")
    expected_hash = canonical_input_hash(value)
    if not re.fullmatch(r"[0-9a-f]{64}", str(value["input_hash"])) or value["input_hash"] != expected_hash:
        raise ValueError("input_hash does not match canonical input")
    return value


def validate_ea_trace(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("EA trace must be an object")
    for key in ("schema_version", "rule_version", "snapshot_id", "symbol", "timeframe", "m5_time", "input_hash"):
        if key not in value:
            raise ValueError(f"EA trace missing {key}")
    if value["schema_version"] != SCHEMA_VERSION or value["rule_version"] != RULE_VERSION:
        raise ValueError("EA trace schema/rule version mismatch")
    if value["snapshot_id"] != snapshot_id_for_m5(value["symbol"], value["timeframe"], value["m5_time"]):
        raise ValueError("EA trace snapshot_id mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value["input_hash"])):
        raise ValueError("EA trace input_hash is invalid")
    for section in ("calculation", "local_candidate", "ea_ai_review", "execution"):
        if not isinstance(value.get(section), dict):
            raise ValueError(f"EA trace missing {section} object")
    return value
