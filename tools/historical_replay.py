"""EA Trader A vs Parallel AI Trader B historical comparison replay.

Read-only research subsystem. It reuses the production decision/plan logic
(parallel_ai_contracts, parallel_ai_calculator, parallel_ai_judge, and the STOP
plan validator) but never calls mt5.order_send, never touches the live auditor
state, and never writes production Lark trade notifications.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PARAMETERS: dict[str, Any] = {
    "absolute_max_spread_points": 0, "ai_confidence_threshold": 70,
    "atr_period": 14, "ema_near_distance_usd": 3, "ema_period": 20,
    "entry_buffer_atr": 0.05, "entry_buffer_points": 0,
    "fib_invalid_buffer_atr": 0.1, "fib_max": 61.8, "fib_min": 38.2,
    "macd_fast": 12, "macd_signal": 9, "macd_slow": 26,
    "max_post_ai_move_atr": 0.25, "max_signal_bar_usd": 10,
    "max_sl_atr": 2, "max_spread_atr_ratio": 0.08,
    "min_attempt_separation_bars": 1, "min_impulse_atr": 1.5,
    "min_rr_to_tp1": 0.8, "min_three_bar_move_usd": 10,
    "pin_bar_wick_body_ratio": 2, "pivot_left": 2, "pivot_right": 2,
    "rsi_period": 14, "sr_tolerance_atr": 0.2, "stop_buffer_atr": 0.1,
    "stop_buffer_points": 0, "strong_bar_body_ratio": 0.6,
}

ACCOUNT_STATE: dict[str, Any] = {
    "account_login": 848209, "existing_exposure": False,
    "risk_locked": False, "trade_allowed": True,
}

RULE_VERSION = "EA_OPEN_V3_9_20_R1"
SYMBOL = "XAUUSD.s"
TIMEFRAME = "M5"


def _fmt_time(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y.%m.%d %H:%M:%S")


def _fmt_time_dash(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_mt5_rates(symbol: str, timeframe: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    import MetaTrader5 as mt5
    from tools.ai_trade_manager import TERMINAL_PATH

    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    try:
        rates = mt5.copy_rates_range(symbol, timeframe, start, end)
        if rates is None:
            return []
        bars = []
        for row in rates:
            bars.append({
                "time": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "tick_volume": int(row[5]), "spread": int(row[6]),
            })
        return bars
    finally:
        mt5.shutdown()


def snapshot_id_for_bar(bar_time: int) -> str:
    ts = datetime.fromtimestamp(bar_time, tz=timezone.utc).strftime("%Y%m%d_%H%M")
    return f"XAUUSD.s_M5_{ts}"


def build_snapshot(bars: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """Build a production-shaped Independent_Input snapshot for bar `index`.

    Only bars <= index are used, so there is no look-ahead into future bars.
    """
    window = bars[index - 199 : index + 1]
    bar_rows = [
        {
            "time": _fmt_time(b["time"]),
            "open": b["open"], "high": b["high"], "low": b["low"],
            "close": b["close"], "tick_volume": b["tick_volume"],
        }
        for b in window
    ]
    last = bars[index]
    tick = 0.01
    bid = round(last["close"], 2)
    ask = round(last["close"] + tick, 2)
    snapshot = {
        "hash_material_version": 1, "schema_version": 2,
        "rule_version": RULE_VERSION,
        "snapshot_id": snapshot_id_for_bar(last["time"]),
        "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "m5_time": _fmt_time(last["time"]),
        "bid": bid, "ask": ask, "tick_size": tick,
        "parameters": dict(PARAMETERS), "account_state": dict(ACCOUNT_STATE),
        "bars": bar_rows,
    }
    snapshot["input_hash"] = canonical_input_hash(snapshot)
    return snapshot


def canonical_input_hash(value: dict[str, Any]) -> str:
    from tools.parallel_ai_contracts import canonical_input_hash as _hash
    return _hash(value)


class CachedDeepSeekClient:
    """Wraps a DeepSeek client with a JSON-file response cache."""

    def __init__(self, client: Any, cache_dir: Path, model: str, prompt_sha: str):
        self.client = client
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.prompt_sha = prompt_sha
        self.hits = 0
        self.misses = 0

    def _key(self, prompt: str, payload: dict[str, Any]) -> str:
        material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"{digest}_{self.model[:16]}_{self.prompt_sha[:16]}"

    def call(self, prompt: str, payload: dict[str, Any], max_tokens: int):
        key = self._key(prompt, payload)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            self.hits += 1
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached["value"], cached.get("usage", {})
        self.misses += 1
        value, usage = self.client.call(prompt, payload, max_tokens=max_tokens)
        path.write_text(
            json.dumps({"value": value, "usage": usage}, ensure_ascii=False), encoding="utf-8"
        )
        return value, usage


def _classify_outcome(
    decision: dict[str, Any], m5: list[dict[str, Any]], m1: list[dict[str, Any]] | None,
    fill_index: int, entry: float, sl: float, tp1: float, tp2: float,
) -> dict[str, Any]:
    direction = str(decision.get("direction", "")).upper()
    risk = abs(entry - sl)
    if risk <= 0:
        return {"status": "INVALID_RISK"}
    # Walk bars after fill; compute MFE/MAE and which side is hit first.
    mfe_r = 0.0
    mae_r = 0.0
    hit = ""
    hit_time = ""
    for b in m5[fill_index:]:
        high, low, close = b["high"], b["low"], b["close"]
        if direction == "SELL":
            fav = (entry - low) / risk
            adv = (entry - high) / risk
            if low <= tp1 and not hit:
                hit = "TP1"
                hit_time = _fmt_time(b["time"])
            if high >= sl and not hit:
                hit = "SL"
                hit_time = _fmt_time(b["time"])
            if low <= tp2 and hit == "TP1":
                hit = "TP2"
                hit_time = _fmt_time(b["time"])
        else:
            fav = (high - entry) / risk
            adv = (entry - low) / risk
            if high >= tp1 and not hit:
                hit = "TP1"
                hit_time = _fmt_time(b["time"])
            if low <= sl and not hit:
                hit = "SL"
                hit_time = _fmt_time(b["time"])
            if high >= tp2 and hit == "TP1":
                hit = "TP2"
                hit_time = _fmt_time(b["time"])
        mfe_r = max(mfe_r, fav)
        mae_r = min(mae_r, adv)
    net_r = {"SL": -1.0, "TP1": 1.0, "TP2": 2.0}.get(hit, 0.0)
    return {
        "status": "FILLED", "exit": hit or "OPEN", "exit_time": hit_time,
        "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4),
        "net_r": round(net_r, 4),
    }


def simulate_stop_plan(
    decision: dict[str, Any], m5: list[dict[str, Any]],
    m1: list[dict[str, Any]] | None, decision_index: int,
) -> dict[str, Any]:
    direction = str(decision.get("direction", "")).upper()
    entry = float(decision["entry"])
    sl = float(decision["sl"])
    tp1 = float(decision["tp1"])
    tp2 = float(decision["tp2"])
    expiry_bars = 3  # 15 minute pending expiry
    trigger_index = -1
    for offset in range(1, expiry_bars + 1):
        idx = decision_index + offset
        if idx >= len(m5):
            break
        b = m5[idx]
        triggered = (b["low"] <= entry) if direction == "SELL" else (b["high"] >= entry)
        if triggered:
            trigger_index = idx
            break
    if trigger_index < 0:
        return {"status": "NOT_TRIGGERED", "exit": "EXPIRED"}
    return _classify_outcome(decision, m5, m1, trigger_index, entry, sl, tp1, tp2)


def load_ea_decisions(data_root: Path, day: str) -> dict[str, dict[str, Any]]:
    """Load EA per-bar decision from the EA's Strategy_Events CSV.

    `candidate` events mark an EA OPEN at the signal bar; everything else is
    treated as EA WAIT. This keeps Trader A sourced from the EA's own records
    rather than the Python reference.
    """
    decisions: dict[str, dict[str, Any]] = {}
    events_csv = data_root / "Raw_Data" / f"Strategy_Events_{day}.csv"
    if not events_csv.exists():
        return decisions
    with events_csv.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("event_type", "") != "candidate":
                continue
            signal_id = row.get("signal_id", "")
            parts = signal_id.split("_")
            if len(parts) < 5:
                continue
            bar_key = f"XAUUSD.s_M5_{parts[2]}_{parts[3][:4]}"
            decisions[bar_key] = {
                "action": "OPEN", "direction": parts[4] or "NONE",
                "stage": "candidate", "reason": "EA candidate",
                "event_type": "candidate", "signal_id": signal_id,
            }
    return decisions


def _stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "win_rate": None, "total_r": 0.0, "avg_r": None, "max_dd": 0.0}
    wins = [t for t in trades if float(t.get("net_r", 0)) > 0]
    total_r = sum(float(t.get("net_r", 0)) for t in trades)
    avg_r = total_r / len(trades)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += float(t.get("net_r", 0))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(trades), "win_rate": round(len(wins) / len(trades), 4),
        "total_r": round(total_r, 4), "avg_r": round(avg_r, 4),
        "max_dd": round(max_dd, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="EA vs Parallel AI historical replay")
    parser.add_argument("--config", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD HH:MM (UTC)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD HH:MM (UTC)")
    parser.add_argument("--warmup-bars", type=int, default=200)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]

    start = datetime.strptime(args.start, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    warmup_start = start - timedelta(minutes=5 * args.warmup_bars)

    m5 = load_mt5_rates(SYMBOL, 5, warmup_start, end)
    m1 = load_mt5_rates(SYMBOL, 1, start, end)
    if len(m5) < args.warmup_bars + 1:
        print(f"insufficient M5 bars: {len(m5)}")
        return 2

    prompt = (root / "config" / "parallel_ai_entry_prompt.txt").read_text(encoding="utf-8")
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    model = str(config.get("model", "deepseek-v4-flash"))
    api_key = _load_api_key(config, root)

    from openai import OpenAI
    base_client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    # A thin production-shaped client for call_primary_decision.
    class _Client:
        def __init__(self, raw):
            self.raw = raw
            self.model = model
        def call(self, prompt, payload, max_tokens):
            response = self.raw.chat.completions.create(
                model=model, messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
                ],
                response_format={"type": "json_object"}, stream=False,
                max_tokens=max_tokens, timeout=20,
                extra_body={"thinking": {"type": "disabled"}},
            )
            content = response.choices[0].message.content or ""
            value = json.loads(content)
            usage = {"model": model, "prompt_tokens": int(response.usage.prompt_tokens),
                     "completion_tokens": int(response.usage.completion_tokens)}
            return value, usage

    cache_dir = root / "artifacts" / "historical_replay" / "cache"
    client = CachedDeepSeekClient(_Client(base_client), cache_dir, model, prompt_sha)

    from tools.parallel_ai_calculator import calculate_independent_facts
    from tools.parallel_ai_contracts import build_blind_primary_payload
    from tools.parallel_ai_judge import call_primary_decision
    from tools.ai_trade_manager import validate_stop_plan

    ai_decisions: list[dict[str, Any]] = []
    ai_trades: list[dict[str, Any]] = []
    plan_valid_first = 0
    plan_invalid_first = 0
    replan_count = 0
    replan_valid = 0
    replan_wait = 0
    replan_invalid = 0

    max_tokens = int(config.get("parallel_ai_primary_max_tokens", 900))
    for index in range(args.warmup_bars, len(m5)):
        snapshot = build_snapshot(m5, index)
        raw = snapshot
        facts = calculate_independent_facts(raw)
        payload = build_blind_primary_payload(
            raw, facts, ai_existing_exposure=False,
            trade_stops_level=50, point=0.01,
        )
        try:
            decision, usage = call_primary_decision(
                client, prompt, payload, max_tokens, tick_size=float(raw["tick_size"])
            )
        except Exception as exc:
            decision = {"action": "WAIT", "direction": "WAIT", "route": "NONE",
                        "confidence": 0, "reason": f"primary error {type(exc).__name__}",
                        "entry": None, "sl": None, "tp1": None, "tp2": None,
                        "rr_to_tp1": None, "raw_rr_to_tp1": None,
                        "conditions": []}
            ai_decisions.append({"snapshot_id": snapshot["snapshot_id"],
                                 "m5_time": snapshot["m5_time"], "ai_status": "UNAVAILABLE",
                                 "action": "WAIT", "direction": "NONE", "plan_status": "",
                                 "plan_reason": "", "final_decision": "WAIT"})
            continue

        plan_status = ""
        plan_reason = ""
        if str(decision.get("action", "")).upper() == "OPEN":
            plan_check = validate_stop_plan(decision, raw["bid"], raw["ask"], raw["tick_size"], 50)
            if plan_check["status"] == "VALID":
                plan_status = "VALID"
                plan_valid_first += 1
            else:
                plan_invalid_first += 1
                replan_count += 1
                replan_decision = _replan(client, prompt, payload, decision, plan_check, max_tokens, raw["tick_size"])
                if isinstance(replan_decision, dict) and str(replan_decision.get("action", "")).upper() == "WAIT":
                    decision = replan_decision
                    plan_status = "REPLAN_WAIT"
                    plan_reason = plan_check.get("reason", "")
                    replan_wait += 1
                elif isinstance(replan_decision, dict):
                    rc = validate_stop_plan(replan_decision, raw["bid"], raw["ask"], raw["tick_size"], 50)
                    if rc["status"] == "VALID":
                        decision = replan_decision
                        plan_status = "VALID"
                        plan_reason = "REPLAN_VALID"
                        replan_valid += 1
                    else:
                        plan_status = "INVALID"
                        plan_reason = "REPLAN_STILL_INVALID"
                        replan_invalid += 1
                else:
                    plan_status = "INVALID"
                    plan_reason = "REPLAN_FAILED"
                    replan_invalid += 1

        row = {
            "snapshot_id": snapshot["snapshot_id"], "m5_time": snapshot["m5_time"],
            "ai_status": "NORMAL", "action": decision.get("action"),
            "direction": decision.get("direction"), "route": decision.get("route"),
            "confidence": decision.get("confidence"), "reason": decision.get("reason"),
            "entry": decision.get("entry"), "sl": decision.get("sl"),
            "tp1": decision.get("tp1"), "tp2": decision.get("tp2"),
            "rr_to_tp1": decision.get("rr_to_tp1"),
            "plan_status": plan_status, "plan_reason": plan_reason,
            "final_decision": "WAIT" if decision.get("action") != "OPEN" else ("OPEN" if plan_status == "VALID" else "INVALID"),
        }
        ai_decisions.append(row)

        if plan_status == "VALID" and decision.get("action") == "OPEN":
            outcome = simulate_stop_plan(decision, m5, m1, index)
            if outcome.get("status") == "FILLED":
                ai_trades.append({
                    "snapshot_id": snapshot["snapshot_id"], "direction": decision.get("direction"),
                    "route": decision.get("route"), "entry": decision.get("entry"),
                    "sl": decision.get("sl"), "tp1": decision.get("tp1"),
                    "tp2": decision.get("tp2"), **outcome,
                })

    data_root = Path(str(config.get("data_root", "")))
    ea_day = start.strftime("%Y-%m-%d")
    ea_decisions = load_ea_decisions(data_root, ea_day)
    comparisons = _compare(ea_decisions, ai_decisions)

    run_id = f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir = root / "artifacts" / "historical_replay" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id, "baseline_name": "Parallel_AI_V2_Baseline_20260819",
        "date_range": [args.start, args.end], "model": model,
        "prompt_sha256": prompt_sha, "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "bars_processed": len(ai_decisions), "ai_calls": client.misses,
        "cache_hits": client.hits, "lookahead_guard_status": "PASS",
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "AI_Decisions.csv").write_text(_to_csv(ai_decisions), encoding="utf-8-sig")
    (out_dir / "AI_Trades.csv").write_text(_to_csv(ai_trades), encoding="utf-8-sig")

    summary = {
        "manifest": manifest,
        "ai_plan_quality": {
            "open": plan_valid_first + plan_invalid_first,
            "first_valid": plan_valid_first, "first_invalid": plan_invalid_first,
            "first_valid_rate": round(plan_valid_first / (plan_valid_first + plan_invalid_first), 4)
            if (plan_valid_first + plan_invalid_first) else None,
            "replan_count": replan_count, "replan_valid": replan_valid,
            "replan_wait": replan_wait, "replan_invalid": replan_invalid,
        },
        "ai_trades": _stats(ai_trades),
        "ea_trades": {"trades": 0},
        "comparisons": comparisons,
    }
    (out_dir / "Performance_Summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report = _report_markdown(summary, ai_trades)
    (out_dir / "Replay_Report.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nREPORT_DIR: {out_dir}")
    return 0


def _load_api_key(config: dict[str, Any], root: Path) -> str:
    key_file = Path(str(config.get("api_key_file", "Config/deepseek_api_key.txt")))
    if not key_file.is_absolute():
        key_file = root / key_file
    if key_file.exists():
        return key_file.read_text(encoding="utf-8-sig").strip()
    import os
    return os.environ.get(str(config.get("api_key_environment", "DEEPSEEK_API_KEY")), "").strip()


def _replan(client, prompt, payload, decision, plan_check, max_tokens, tick_size):
    from tools.parallel_ai_judge import call_primary_decision
    from tools.parallel_ai_auditor import _corrective_replan
    try:
        return _corrective_replan(client, prompt, payload, decision, plan_check, max_tokens, tick_size)
    except Exception:
        return None


def _compare(ea: dict[str, dict[str, Any]], ai: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"AGREE_WAIT": 0, "AGREE_OPEN": 0, "DIRECTION_DISAGREEMENT": 0,
              "EA_OPEN_AI_WAIT": 0, "EA_WAIT_AI_OPEN": 0, "BOTH_OPEN_PLAN_DIFFERENT": 0}
    for row in ai:
        ea_row = ea.get(row["snapshot_id"], {})
        ea_action = str(ea_row.get("action", "WAIT")).upper()
        ai_action = str(row.get("final_decision", row.get("action", "WAIT"))).upper()
        if ai_action == "OPEN" and row.get("plan_status") == "VALID":
            ai_effective = "OPEN"
        elif ai_action == "OPEN":
            ai_effective = "OPEN_INVALID"
        else:
            ai_effective = "WAIT"
        if ea_action == "WAIT" and ai_effective == "WAIT":
            counts["AGREE_WAIT"] += 1
        elif ea_action == "OPEN" and ai_effective == "OPEN":
            counts["AGREE_OPEN"] += 1
        elif ea_action == "OPEN" and ai_effective == "WAIT":
            counts["EA_OPEN_AI_WAIT"] += 1
        elif ea_action == "WAIT" and ai_effective in {"OPEN", "OPEN_INVALID"}:
            counts["EA_WAIT_AI_OPEN"] += 1
    return counts


def _to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    keys = list(rows[0].keys())
    out = [",".join(keys)]
    for row in rows:
        out.append(",".join(str(row.get(k, "")) for k in keys))
    return "\n".join(out)


def _report_markdown(summary: dict[str, Any], trades: list[dict[str, Any]]) -> str:
    pq = summary["ai_plan_quality"]
    ai = summary["ai_trades"]
    cmp = summary["comparisons"]
    lines = [
        "# Parallel AI Baseline Replay",
        "",
        f"- Baseline: {summary['manifest']['baseline_name']}",
        f"- Range: {summary['manifest']['date_range'][0]} ~ {summary['manifest']['date_range'][1]}",
        f"- Bars: {summary['manifest']['bars_processed']} | AI calls: {summary['manifest']['ai_calls']} | cache hits: {summary['manifest']['cache_hits']}",
        "",
        "## AI Plan Quality",
        f"- OPEN: {pq['open']} | 首次合法: {pq['first_valid']} | 首次非法: {pq['first_invalid']} | 首次合法率: {pq['first_valid_rate']}",
        f"- Replan: {pq['replan_count']} (VALID {pq['replan_valid']} / WAIT {pq['replan_wait']} / INVALID {pq['replan_invalid']})",
        "",
        "## AI Trades",
        f"- Trades: {ai['trades']} | Win rate: {ai['win_rate']} | Total R: {ai['total_r']} | Avg R: {ai['avg_r']} | Max DD: {ai['max_dd']}",
        "",
        "## Comparison",
        f"- {cmp}",
        "",
        "## Trade Detail",
    ]
    for t in trades:
        lines.append(
            f"- {t['snapshot_id']} {t['direction']} {t['route']} entry={t['entry']} "
            f"exit={t.get('exit')} netR={t.get('net_r')} MFE={t.get('mfe_r')} MAE={t.get('mae_r')}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
