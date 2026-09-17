"""V20 规则计算一致性对账：EA V20（AI_Candidates.csv） vs Python V20 Reference。

只验证 Entry/SL/TP1/RR/Route 的计算一致性，不评价盈亏、胜率、回撤，
也不据此修改任何策略参数。

用法：
    python tools/verify_v20_consistency.py \
        --csv <AI_Candidates.csv路径> \
        --snapshots <Independent_Input/Pending目录> \
        [--tolerance 0.02]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.parallel_ai_calculator as calc

# 只读对账：绕开 input_hash（快照里可能是 V14 的 hash，而我们要强制用 V20 重算）。
calc.validate_independent_input = lambda v: v


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gate_values(gates: list[dict[str, Any]]) -> dict[str, Any]:
    return {g["id"]: g for g in gates}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--snapshots", required=True)
    parser.add_argument("--tolerance", type=float, default=0.02)
    args = parser.parse_args()

    snap_dir = Path(args.snapshots)
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig")))

    # 找到每个候选对应的 snapshot 文件
    snap_index: dict[str, Path] = {}
    for path in snap_dir.glob("*.json"):
        snap_index[path.stem] = path

    results: list[dict[str, Any]] = []
    matched = 0
    for row in rows:
        signal_id = str(row.get("SignalID") or "").strip()
        if not signal_id:
            continue
        # SignalID = XAUUSD.s_M5_YYYYMMDD_HHMMSS_...；快照名 = XAUUSD.s_M5_YYYYMMDD_HHMM
        parts = signal_id.split("_")
        if len(parts) < 4:
            continue
        snapshot_id = f"{parts[0]}_M5_{parts[2]}_{parts[3][:4]}"
        snap_path = snap_index.get(snapshot_id)
        if snap_path is None:
            continue
        matched += 1
        try:
            raw = json.loads(snap_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        raw["rule_version"] = "EA_OPEN_V3_9_20_R1"
        # 历史快照由 V14 生成，缺少 V16+ 才引入的 EMA 止损参数；对账必须补回
        # 与 V20 EA 输入默认值完全一致的“同一输入”，否则 EMA 路径会按 0 算错 SL。
        params = raw.setdefault("parameters", {})
        params.setdefault("ema_signal_bar_stop_usd", 2.0)
        params.setdefault("ema_max_stop_expansion_ratio", 1.50)
        # 候选的 Entry/SL/TP 只依赖行情事实，不依赖 EA 的已有敞口；把 G02 的
        # existing_exposure 置 False，让门链推进到 G08/G09 以取出计算值。
        if isinstance(raw.get("account_state"), dict):
            raw["account_state"]["existing_exposure"] = False
        try:
            facts = calc.calculate_independent_facts(raw)
            gates = calc.evaluate_gate_facts(raw, facts)
        except Exception as exc:
            results.append({"signal_id": signal_id, "error": type(exc).__name__})
            continue
        gmap = _gate_values(gates)
        g08 = gmap.get("G08", {}).get("values", {})
        g09 = gmap.get("G09", {}).get("values", {})
        py_entry = _float(g08.get("entry"))
        py_sl = _float(g08.get("sl"))
        py_tp1 = _float(g09.get("tp1"))
        py_rr = _float(g09.get("rr_to_tp1"))
        py_route = facts.get("price_action", {}).get("route", "NONE")

        ea_entry = _float(row.get("Entry"))
        ea_sl = _float(row.get("SL"))
        ea_tp1 = _float(row.get("TP1"))
        ea_rr = _float(row.get("RR"))
        ea_route = _csv_route(row)

        def diff(a, b):
            if a is None or b is None:
                return None
            return abs(a - b)

        results.append({
            "signal_id": signal_id,
            "direction": row.get("Direction", ""),
            "route_ea": ea_route,
            "route_py": py_route,
            "entry_ea": ea_entry,
            "entry_py": py_entry,
            "sl_ea": ea_sl,
            "sl_py": py_sl,
            "tp1_ea": ea_tp1,
            "tp1_py": py_tp1,
            "rr_ea": ea_rr,
            "rr_py": py_rr,
            "entry_diff": diff(ea_entry, py_entry),
            "sl_diff": diff(ea_sl, py_sl),
            "tp1_diff": diff(ea_tp1, py_tp1),
            "rr_diff": diff(ea_rr, py_rr),
        })

    tol = args.tolerance
    # 计算每行是否 PASS，以及最大误差
    max_diff = 0.0
    all_pass = True
    for r in results:
        if "error" in r:
            r["pass"] = False
            all_pass = False
            continue
        diffs = [d for d in (r["entry_diff"], r["sl_diff"], r["tp1_diff"], r["rr_diff"]) if d is not None]
        r["pass"] = all(d <= tol for d in diffs) and r["route_ea"] == r["route_py"]
        if not r["pass"]:
            all_pass = False
        if diffs:
            max_diff = max(max_diff, max(diffs))

    print(f"匹配候选样本：{matched}，有效对比：{len([r for r in results if 'error' not in r])}")
    print(f"容差：±{tol}")
    print(f"最大误差：{max_diff:.4f}")
    print(f"结果：{'全部 PASS' if all_pass else '存在不一致'}")
    print()
    print("SignalID | 方向 | 路径EA/PY | Entry EA/PY | SL EA/PY | TP1 EA/PY | RR EA/PY | 是否一致")
    for r in results:
        if "error" in r:
            print(f"{r['signal_id']} | 错误:{r['error']}")
            continue
        print(
            f"{r['signal_id']} | {r['direction']} | {r['route_ea']}/{r['route_py']} | "
            f"{r['entry_ea']}/{r['entry_py']} | {r['sl_ea']}/{r['sl_py']} | "
            f"{r['tp1_ea']}/{r['tp1_py']} | {r['rr_ea']}/{r['rr_py']} | "
            f"{'PASS' if r['pass'] else 'FAIL'}"
        )
    return 0 if all_pass else 1


def _csv_route(row: dict[str, str]) -> str:
    route = str(row.get("SignalRoute") or "").strip()
    if route:
        return route
    sig = str(row.get("SignalID") or "")
    if "FIB_PA" in sig:
        return "FIB_PA"
    if "EMA_H23" in sig:
        return "EMA_H23"
    if "EMA_L23" in sig:
        return "EMA_L23"
    if "BOTH" in sig:
        return "BOTH"
    return "NONE"


if __name__ == "__main__":
    raise SystemExit(main())
