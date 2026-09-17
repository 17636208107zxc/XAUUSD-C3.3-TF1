"""AI-group order executor and staged-exit manager (demo account only).

The parallel AI places its own orders with a dedicated magic number and an
"AI_" comment so the review can compare AI orders vs EA orders vs manual
orders. Staged exits mirror the EA policy: TP1 closes 50% and moves SL to
breakeven, TP2 closes 25% and locks 1R, the runner rides until its stop.
This module NEVER touches EA-magic positions and performs no strategy changes.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TERMINAL_PATH = r"C:\Program Files\Doo Technology MetaTrader 5\terminal64.exe"
DEFAULT_AI_MAGIC = 2026072902
EA_MAGIC = 2026072901
PENDING_EXPIRY_SECONDS = 15 * 60
MAX_RISK_PERCENT = 1.0


def _align(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(price / tick) * tick


def _normalize_volume(volume: float, step: float, minimum: float) -> float:
    if step <= 0:
        step = 0.01
    if minimum <= 0:
        minimum = 0.01
    volume = max(minimum, math.floor(volume / step + 1e-9) * step)
    return round(volume, 8)


def _pick_filling(symbol_info) -> int:
    """Pick a filling mode supported by the symbol (FOK first, then IOC)."""
    import MetaTrader5 as mt5

    mode = int(getattr(symbol_info, "filling_mode", 0) or 0)
    if mode & 1:  # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    if mode & 2:  # SYMBOL_FILLING_IOC
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def precheck_passed(check: Any) -> bool:
    """True when an order_check result allows the request to reach ORDER_SEND.

    MetaTrader5 order_check() reports success as retcode=0 (comment "Done"),
    while order_send() uses TRADE_RETCODE_DONE=10009. Both mean "acceptable"
    here; any other non-zero retcode means the precheck rejected the request.
    """
    if check is None:
        return False
    try:
        return int(getattr(check, "retcode", -1)) in (0, 10009)
    except (TypeError, ValueError):
        return False


def compute_minimum_pending_distance(trade_stops_level: float, point: float) -> float:
    """Minimum price distance a STOP order must keep from the current quote.

    trade_stops_level is expressed in symbol points; convert to price and make
    sure it is never smaller than one point.
    """
    return max(float(trade_stops_level) * float(point), float(point))


def validate_stop_plan(
    decision: dict[str, Any], bid: float, ask: float,
    tick_size: float, trade_stops_level: float,
) -> dict[str, Any]:
    """Validate an AI OPEN plan against one Bid/Ask snapshot.

    SELL is executed as SELL_STOP: entry must be strictly below Bid by at least
    the minimum pending distance. BUY is executed as BUY_STOP: entry must be
    strictly above Ask by the same minimum. This never moves the AI's entry; it
    only classifies VALID / INVALID.
    """
    direction = str(decision.get("direction", "")).upper()
    try:
        entry = float(decision.get("entry"))
    except (TypeError, ValueError):
        return {"status": "INVALID", "reason": "ENTRY_NOT_NUMERIC",
                "minimum_pending_distance": compute_minimum_pending_distance(trade_stops_level, tick_size)}
    minimum = compute_minimum_pending_distance(trade_stops_level, tick_size)
    if direction == "SELL":
        if entry >= bid:
            return {"status": "INVALID", "reason": "SELL_STOP_ENTRY_WRONG_SIDE",
                    "minimum_pending_distance": minimum}
        if bid - entry < minimum:
            return {"status": "INVALID", "reason": "SELL_STOP_ENTRY_TOO_CLOSE",
                    "minimum_pending_distance": minimum}
        return {"status": "VALID", "reason": "", "minimum_pending_distance": minimum}
    if direction == "BUY":
        if entry <= ask:
            return {"status": "INVALID", "reason": "BUY_STOP_ENTRY_WRONG_SIDE",
                    "minimum_pending_distance": minimum}
        if entry - ask < minimum:
            return {"status": "INVALID", "reason": "BUY_STOP_ENTRY_TOO_CLOSE",
                    "minimum_pending_distance": minimum}
        return {"status": "VALID", "reason": "", "minimum_pending_distance": minimum}
    return {"status": "INVALID", "reason": "INVALID_DIRECTION",
            "minimum_pending_distance": minimum}


def get_symbol_trade_constraints(symbol: str) -> dict[str, float]:
    """Read point/trade_stops_level/trade_tick_size for a symbol (read-only)."""
    import MetaTrader5 as mt5

    defaults = {"point": 0.01, "trade_stops_level": 0.0, "trade_tick_size": 0.01}
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return defaults
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return defaults
        return {
            "point": float(getattr(info, "point", 0.01) or 0.01),
            "trade_stops_level": float(getattr(info, "trade_stops_level", 0.0) or 0.0),
            "trade_tick_size": float(getattr(info, "trade_tick_size", 0.01) or 0.01),
        }
    finally:
        mt5.shutdown()


def calc_volume(
    entry: float, sl: float, balance: float, risk_percent: float,
    tick_size: float, tick_value: float,
) -> float:
    """Risk-based lot size mirroring the EA (0.5% default, hard cap 1%)."""
    risk_percent = max(0.0, min(risk_percent, MAX_RISK_PERCENT))
    risk_usd = balance * risk_percent / 100.0
    distance = abs(entry - sl)
    if distance <= 0 or tick_size <= 0:
        return 0.0
    risk_per_lot = (distance / tick_size) * max(tick_value, 0.0)
    if risk_per_lot <= 0:
        return 0.0
    return risk_usd / risk_per_lot


def _plan_dir(root: Path) -> Path:
    return root / "Parallel_AI_V2" / "AI_Trade_State"


def _plan_path(root: Path, signal_id: str) -> Path:
    return _plan_dir(root) / f"{signal_id}.json"


def save_plan(root: Path, plan: dict[str, Any]) -> None:
    path = _plan_path(root, str(plan["signal_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_plans(root: Path) -> list[dict[str, Any]]:
    folder = _plan_dir(root)
    if not folder.exists():
        return []
    plans = []
    for path in sorted(folder.glob("*.json")):
        try:
            plans.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return plans


LIFECYCLE_COLUMNS = [
    "event_id", "server_time", "event_kind", "stage", "position_id", "deal_ticket",
    "signal_id", "direction", "volume", "price", "profit", "commission", "swap", "fee",
    "initial_volume", "initial_sl", "initial_risk", "tp1_price", "tp2_price",
    "tp1_done", "tp2_done", "runner_active", "close_reason", "magic",
]


def append_ai_lifecycle_event(root: Path, position_id: str, event: dict[str, Any]) -> None:
    """Append one AI position lifecycle row in the EA's Trade_Lifecycle format.

    The extra `magic` column marks Trader B (parallel_ai_magic) so the daily
    review can distinguish AI positions from EA positions.
    """
    import csv

    lifecycle_dir = root / "Trade_Lifecycle"
    lifecycle_dir.mkdir(parents=True, exist_ok=True)
    path = lifecycle_dir / f"Position_{position_id}.csv"
    if not path.exists():
        path.write_text(";".join(LIFECYCLE_COLUMNS) + "\n", encoding="utf-8-sig")
    row = {column: "" for column in LIFECYCLE_COLUMNS}
    row.update(event)
    if not str(row.get("magic", "")).strip():
        row["magic"] = str(DEFAULT_AI_MAGIC)
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIFECYCLE_COLUMNS, delimiter=";")
        writer.writerow({column: row.get(column, "") for column in LIFECYCLE_COLUMNS})


def _server_time_text(timestamp: int | float) -> str:
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%Y.%m.%d %H:%M:%S")


def queue_ai_open_notification(
    root: Path, plan: dict[str, Any], position: Any, mt5: Any | None = None,
) -> None:
    """Queue an AI open-trade Lark notification in the EA's open-card format."""
    entry = float(plan["entry"])
    sl = float(plan["sl"])
    volume = float(plan.get("initial_volume", getattr(position, "volume", 0.1)))
    risk = round(abs(entry - sl) * volume * 100.0, 2)
    deal_ticket = "0"
    if mt5 is not None:
        for deal in (mt5.history_deals_get(position=position.ticket) or []):
            if int(getattr(deal, "entry", -1)) == 0:  # DEAL_ENTRY_IN
                deal_ticket = str(deal.ticket)
                break
    facts = {
        "signal_id": str(plan.get("signal_id", "")),
        "direction": str(plan.get("direction", "")),
        "open_time": _server_time_text(position.time),
        "entry_price": entry,
        "initial_volume": volume,
        "initial_sl": sl,
        "initial_risk": risk,
        "tp1_price": float(plan.get("tp1", 0.0) or 0.0),
        "tp2_price": float(plan.get("tp2", 0.0) or 0.0),
        "signal_route": str(plan.get("route", "")),
        "context_loaded": True,
        "pattern": "",
        "ai_allow_trade": True,
        "ai_is_error": False,
        "ai_confidence": int(plan.get("confidence", 0) or 0),
        "ai_reason": str(plan.get("reason", "")),
        "status": "open",
    }
    item = {
        "schema_version": 2,
        "notification_id": f"PARALLEL_AI_OPEN_{position.ticket}",
        "notification_type": "open_trade_facts",
        "position_id": str(position.ticket),
        "deal_ticket": deal_ticket,
        "attempts": 0,
        "facts": facts,
    }
    outbox = root / "Lark_Outbox" / "Pending"
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{item['notification_id']}.json"
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")


def queue_ai_close_notification(root: Path, plan: dict[str, Any]) -> None:
    """Queue an AI final-settlement Lark notification when a position fully closes."""
    position_id = int(plan.get("position_ticket", 0) or plan.get("order_ticket", 0) or 0)
    if not position_id:
        return
    entry = float(plan["entry"])
    sl = float(plan["sl"])
    volume = float(plan.get("initial_volume", 0.1))
    facts = {
        "signal_id": str(plan.get("signal_id", "")),
        "direction": str(plan.get("direction", "")),
        "entry_price": entry,
        "initial_volume": volume,
        "initial_sl": sl,
        "initial_risk": round(abs(entry - sl) * volume * 100.0, 2),
        "tp1_price": float(plan.get("tp1", 0.0) or 0.0),
        "tp2_price": float(plan.get("tp2", 0.0) or 0.0),
        "status": "closed",
    }
    item = {
        "schema_version": 2,
        "notification_id": f"PARALLEL_AI_CLOSE_{position_id}",
        "notification_type": "final_trade_facts",
        "position_id": str(position_id),
        "deal_ticket": "",
        "attempts": 0,
        "facts": facts,
    }
    outbox = root / "Lark_Outbox" / "Pending"
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{item['notification_id']}.json"
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")


def place_ai_pending(
    root: Path, config: dict[str, Any], signal_id: str,
    decision: dict[str, Any], raw: dict[str, Any],
) -> dict[str, Any]:
    """Place an AI-group pending order from a validated OPEN decision."""
    import MetaTrader5 as mt5

    symbol = str(raw.get("symbol", "XAUUSD.s"))
    direction = str(decision.get("direction", "")).upper()
    if direction not in {"BUY", "SELL"}:
        return {"ok": False, "error": "无效方向"}
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return {"ok": False, "error": f"MT5初始化失败 {mt5.last_error()}"}
    try:
        info = mt5.account_info()
        symbol_info = mt5.symbol_info(symbol)
        if info is None or symbol_info is None:
            return {"ok": False, "error": "无法读取账户或品种信息"}
        tick = mt5.symbol_info_tick(symbol)
        tick_size = float(symbol_info.trade_tick_size or 0.01)
        tick_value = float(symbol_info.trade_tick_value or 1.0)
        # STALE check: the auditor validated the plan against the decision-time
        # quote; if the current quote has already crossed the STOP entry by the
        # time we reach execution, do not place a wrong-side order.
        if tick is not None:
            trade_stops_level = float(getattr(symbol_info, "trade_stops_level", 0.0) or 0.0)
            execution_plan = validate_stop_plan(
                decision, float(tick.bid), float(tick.ask), tick_size, trade_stops_level
            )
            if execution_plan["status"] != "VALID":
                return {
                    "ok": False,
                    "state": "STALE",
                    "reason": "ENTRY_ALREADY_CROSSED",
                    "plan_validation": execution_plan,
                    "execution_bid": float(tick.bid),
                    "execution_ask": float(tick.ask),
                }
        entry = _align(float(decision["entry"]), tick_size)
        sl = _align(float(decision["sl"]), tick_size)
        tp1 = _align(float(decision["tp1"]), tick_size)
        tp2 = _align(float(decision["tp2"]), tick_size)
        volume = calc_volume(
            entry, sl, float(info.balance),
            float(config.get("parallel_ai_risk_percent", 0.5)),
            tick_size, tick_value,
        )
        volume = _normalize_volume(
            volume,
            float(symbol_info.volume_step or 0.01),
            float(symbol_info.volume_min or 0.01),
        )
        if volume <= 0:
            return {"ok": False, "error": "手数计算为0"}
        magic = int(config.get("parallel_ai_magic", DEFAULT_AI_MAGIC))
        order_type = mt5.ORDER_TYPE_BUY_STOP if direction == "BUY" else mt5.ORDER_TYPE_SELL_STOP
        now_server = tick.time if tick else int(time.time())
        # Pending orders (limit/stop) use RETURN, not FOK/IOC. FOK/IOC are for
        # market execution (TRADE_ACTION_DEAL); RETURN keeps any unfilled
        # remainder as a pending order when the order triggers.
        filling = mt5.ORDER_FILLING_RETURN
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": entry,
            "sl": sl,
            "tp": 0.0,
            "deviation": 30,
            "magic": magic,
            "comment": ("AI_" + signal_id)[:31],
            "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": int(now_server) + PENDING_EXPIRY_SECONDS,
            "type_filling": filling,
        }
        # PRECHECK via order_check: never send a request that already fails
        # server-side validation (filling mode, stops level, margin, symbol).
        check = mt5.order_check(request)
        if not precheck_passed(check):
            retcode = check.retcode if check is not None else "None"
            comment = check.comment if check is not None else ""
            last_error = mt5.last_error()
            return {
                "ok": False,
                "state": "PRECHECK_FAIL",
                "error": f"预检失败 retcode={retcode} last_error={last_error} {comment}",
                "retcode": retcode,
                "comment": comment,
                "last_error": last_error,
                "request": request,
            }

        # ORDER_SEND
        result = mt5.order_send(request)
        if result is None:
            last_error = mt5.last_error()
            return {
                "ok": False,
                "state": "ORDER_SEND_ERROR",
                "error": "order_send 返回 None（挂单未发出）",
                "last_error": last_error,
                "request": request,
            }
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {
                "ok": False,
                "state": "SERVER_REJECTED",
                "retcode": result.retcode,
                "comment": result.comment,
                "error": f"下单被服务器拒绝 retcode={result.retcode} {result.comment}",
                "request": request,
            }

        # ORDER_ACCEPTED → verify actual order state.
        ticket = int(result.order)
        execution_state = "ORDER_ACCEPTED"
        active_order = mt5.orders_get(ticket=ticket)
        if active_order:
            execution_state = "PENDING_ACTIVE"
        else:
            history_order = mt5.history_orders_get(ticket=ticket)
            if history_order:
                state_int = int(history_order[-1].state)
                execution_state = (
                    "FILLED"
                    if state_int == mt5.ORDER_STATE_FILLED
                    else f"ORDER_STATE_{state_int}"
                )
            else:
                execution_state = "ORDER_STATE_UNKNOWN"

        plan = {
            "signal_id": signal_id,
            "symbol": symbol,
            "direction": direction,
            "route": str(decision.get("route", "")),
            "reason": str(decision.get("reason", "")),
            "confidence": int(decision.get("confidence", 0) or 0),
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "initial_volume": volume,
            "order_ticket": ticket,
            "state": execution_state,
            "tp1_done": False,
            "tp2_done": False,
            "remaining": volume,
            "created_beijing": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "conditions": decision.get("conditions", []),
        }
        save_plan(root, plan)
        return {
            "ok": True,
            "state": execution_state,
            "order_ticket": ticket,
            "volume": volume,
        }
    finally:
        mt5.shutdown()


def _close_partial(ticket: int, volume: float, symbol: str, direction: str, magic: int = DEFAULT_AI_MAGIC):
    import MetaTrader5 as mt5

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    symbol_info = mt5.symbol_info(symbol)
    filling = _pick_filling(symbol_info) if symbol_info else mt5.ORDER_FILLING_FOK
    price = tick.ask if direction == "BUY" else tick.bid
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_SELL if direction == "BUY" else mt5.ORDER_TYPE_BUY,
        "position": ticket,
        "price": price,
        "deviation": 30,
        "magic": magic,
        "comment": "AI_tp_close",
        "type_filling": filling,
    }
    result = mt5.order_send(request)
    return result


def _modify_sl(ticket: int, symbol: str, sl: float) -> bool:
    import MetaTrader5 as mt5

    position = mt5.positions_get(ticket=ticket)
    if not position:
        return False
    symbol_info = mt5.symbol_info(symbol)
    filling = _pick_filling(symbol_info) if symbol_info else mt5.ORDER_FILLING_FOK
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": sl,
        "tp": float(position[0].tp or 0.0),
        "type_filling": filling,
    }
    result = mt5.order_send(request)
    return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE


def _deal_exit_facts(mt5: Any, position_id: int, deal_ticket: int) -> dict[str, Any]:
    """Return profit/price/volume/time for one closing deal."""
    for deal in (mt5.history_deals_get(position=position_id) or []):
        if int(deal.ticket) == int(deal_ticket):
            return {
                "profit": float(deal.profit),
                "price": float(deal.price),
                "volume": float(deal.volume),
                "time": int(deal.time),
            }
    return {}


def _position_close_facts(mt5: Any, position_id: int) -> dict[str, Any]:
    """Aggregate the closing deals for a fully closed position.

    ``profit`` 只表示“最后一次平仓动作本身”的利润，绝不等于整笔累计；
    整笔累计由 MT5 Deals 在复盘时重新汇总，避免生命周期 CSV 利润双计。
    """
    deals = [
        d for d in (mt5.history_deals_get(position=position_id) or [])
        if int(getattr(d, "entry", 0)) in (1, 3)  # DEAL_ENTRY_OUT / OUT_BY
    ]
    if not deals:
        return {}
    last = deals[-1]
    return {
        "deal": int(last.ticket),
        "profit": float(last.profit) + float(last.commission) + float(last.swap) + float(last.fee),
        "whole_trade_net": sum(float(d.profit) + float(d.commission) + float(d.swap) + float(d.fee) for d in deals),
        "price": float(last.price),
        "volume": float(last.volume),
        "time": int(last.time),
    }


def _resolve_plan_initial_volume(plan: dict[str, Any], mt5: Any, symbol: str, ticket: int) -> float | None:
    """安全解析 plan 的 initial_volume。

    1. plan 本身有合法 initial_volume -> 直接返回。
    2. 缺失时从 MT5 真实事实恢复（持仓量 -> 挂单量 -> 开仓 Deal 量）。
    3. 无法恢复 -> 返回 None，调用方跳过该 plan，不猜 0。
    """
    raw = plan.get("initial_volume")
    if raw not in (None, ""):
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    try:
        if ticket:
            positions = mt5.positions_get(ticket=ticket) or []
            if positions and float(positions[0].volume or 0) > 0:
                return float(positions[0].volume)
            orders = mt5.orders_get(ticket=ticket) or []
            if orders and float(orders[0].volume_initial or 0) > 0:
                return float(orders[0].volume_initial)
            history = mt5.history_orders_get(ticket=ticket) or []
            if history and float(history[-1].volume_initial or 0) > 0:
                return float(history[-1].volume_initial)
            deals = mt5.history_deals_get(position=ticket) or []
            entry_deals = [d for d in deals if int(d.entry) == 0]
            if entry_deals and float(entry_deals[0].volume or 0) > 0:
                return float(entry_deals[0].volume)
    except Exception:
        pass
    return None


def manage_ai_positions(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Advance staged exits for filled AI-group positions. Runs each service cycle."""
    import MetaTrader5 as mt5

    plans = load_plans(root)
    if not plans:
        return []
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return [{"ok": False, "error": "MT5初始化失败"}]
    results: list[dict[str, Any]] = []
    try:
        magic = int(config.get("parallel_ai_magic", DEFAULT_AI_MAGIC))
        tp1_close_percent = float(config.get("parallel_ai_tp1_close_percent", 50.0))
        tp2_close_percent = float(config.get("parallel_ai_tp2_close_percent", 25.0))
        # 只有真正下过单（或已成交）的 plan 才需要仓位管理；rule_blocked /
        # STALE / INVALID / expired / cancelled / closed 等没有 initial_volume。
        manageable_states = {"PENDING", "PENDING_ACTIVE", "ORDER_ACCEPTED", "ACCEPTED", "FILLED"}
        for plan in plans:
            state = str(plan.get("state", "pending")).upper()
            if state not in manageable_states:
                continue
            symbol = str(plan.get("symbol", "XAUUSD.s"))
            direction = str(plan.get("direction", "")).upper()
            entry = float(plan.get("entry") or 0.0)
            tp1 = float(plan.get("tp1") or 0.0)
            tp2 = float(plan.get("tp2") or 0.0)
            ticket = int(plan.get("order_ticket", 0))
            initial_volume = _resolve_plan_initial_volume(plan, mt5, symbol, ticket)
            if initial_volume is None:
                # 旧 plan 缺 initial_volume 且无法从 MT5 可靠恢复：跳过，不猜 0。
                results.append({
                    "ok": False,
                    "error": "无法恢复 initial_volume，跳过仓位管理",
                    "signal_id": str(plan.get("signal_id", "")),
                })
                continue
            if state in {"PENDING", "PENDING_ACTIVE", "ORDER_ACCEPTED", "ACCEPTED"}:
                positions = mt5.positions_get(symbol=symbol) or []
                filled = next(
                    (p for p in positions if str(p.comment).startswith("AI_") and str(p.comment) == ("AI_" + str(plan["signal_id"]))[:31]),
                    None,
                )
                if filled is not None:
                    plan["state"] = "filled"
                    plan["position_ticket"] = filled.ticket
                    plan["remaining"] = float(filled.volume)
                    save_plan(root, plan)
                    try:
                        append_ai_lifecycle_event(root, str(filled.ticket), {
                            "event_id": f"{filled.ticket}_OPEN",
                            "server_time": _server_time_text(filled.time),
                            "event_kind": "OPEN",
                            "stage": "OPEN",
                            "position_id": str(filled.ticket),
                            "deal_ticket": "0",
                            "signal_id": str(plan.get("signal_id", "")),
                            "direction": direction,
                            "volume": str(plan.get("initial_volume", filled.volume)),
                            "price": str(plan.get("entry", filled.price_open)),
                            "initial_volume": str(plan.get("initial_volume", filled.volume)),
                            "initial_sl": str(plan.get("sl", "")),
                            "initial_risk": f"{abs(entry - float(plan.get('sl', entry))) * initial_volume * 100.0:.2f}",
                            "tp1_price": str(tp1),
                            "tp2_price": str(tp2),
                        })
                    except Exception:
                        pass
                    try:
                        queue_ai_open_notification(root, plan, filled, mt5)
                    except Exception:
                        pass
                    state = "FILLED"
                    ticket = filled.ticket
                else:
                    active_orders = mt5.orders_get(ticket=ticket) or []
                    if not active_orders:
                        history = mt5.history_orders_get(ticket=ticket) or []
                        if history:
                            state_int = int(history[-1].state)
                            if state_int == mt5.ORDER_STATE_EXPIRED:
                                plan["state"] = "expired"
                            elif state_int == mt5.ORDER_STATE_CANCELED:
                                plan["state"] = "cancelled"
                            elif state_int == mt5.ORDER_STATE_FILLED:
                                plan["state"] = "filled"
                            else:
                                plan["state"] = f"order_state_{state_int}"
                        else:
                            plan["state"] = "expired"
                        save_plan(root, plan)
                    continue
            if state != "FILLED":
                continue
            positions = mt5.positions_get(ticket=ticket) or []
            if not positions:
                plan["state"] = "closed"
                save_plan(root, plan)
                try:
                    facts = _position_close_facts(mt5, ticket)
                    if facts:
                        append_ai_lifecycle_event(root, str(ticket), {
                            "event_id": f"{ticket}_CLOSE",
                            "server_time": _server_time_text(facts.get("time", time.time())),
                            "event_kind": "FINAL", "stage": "RUNNER",
                            "position_id": str(ticket), "deal_ticket": str(facts.get("deal", "")),
                            "signal_id": str(plan.get("signal_id", "")), "direction": direction,
                            "volume": str(facts.get("volume", "")),
                            "price": str(facts.get("price", "")),
                            "profit": f"{facts.get('profit', 0.0):.2f}",
                            "close_reason": str(plan.get("close_reason", "") or "AI exit"),
                        })
                except Exception:
                    pass
                try:
                    queue_ai_close_notification(root, plan)
                except Exception:
                    pass
                continue
            position = positions[0]
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                continue
            bid, ask = tick.bid, tick.ask
            symbol_info = mt5.symbol_info(symbol)
            step = float(symbol_info.volume_step or 0.01)
            minimum = float(symbol_info.volume_min or 0.01)
            remaining = float(plan.get("remaining", position.volume))
            changed = False
            if not bool(plan.get("tp1_done")):
                reached = (bid >= tp1) if direction == "BUY" else (ask <= tp1)
                if reached:
                    close_volume = _normalize_volume(initial_volume * tp1_close_percent / 100.0, step, minimum)
                    close_result = _close_partial(ticket, min(close_volume, remaining), symbol, direction, magic)
                    ok_close = close_result is not None and close_result.retcode in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL}
                    ok_sl = _modify_sl(ticket, symbol, _align(entry, float(symbol_info.trade_tick_size or 0.01)))
                    if ok_close:
                        plan["tp1_done"] = True
                        plan["remaining"] = max(0.0, remaining - close_volume)
                        plan["breakeven_set"] = ok_sl
                        changed = True
                        results.append({"signal_id": plan["signal_id"], "event": "TP1"})
                        try:
                            deal_ticket = int(getattr(close_result, "deal", 0) or 0)
                            facts = _deal_exit_facts(mt5, ticket, deal_ticket)
                            append_ai_lifecycle_event(root, str(ticket), {
                                "event_id": f"{ticket}_TP1",
                                "server_time": _server_time_text(facts.get("time", time.time())),
                                "event_kind": "EXIT", "stage": "TP1",
                                "position_id": str(ticket), "deal_ticket": str(deal_ticket),
                                "signal_id": str(plan.get("signal_id", "")), "direction": direction,
                                "volume": str(facts.get("volume", close_volume)),
                                "price": str(facts.get("price", "")),
                                "profit": f"{facts.get('profit', 0.0):.2f}",
                                "tp1_done": "1",
                            })
                        except Exception:
                            pass
            elif not bool(plan.get("tp2_done")):
                reached = (bid >= tp2) if direction == "BUY" else (ask <= tp2)
                if reached:
                    close_volume = _normalize_volume(initial_volume * tp2_close_percent / 100.0, step, minimum)
                    close_result = _close_partial(ticket, min(close_volume, float(plan.get("remaining", remaining))), symbol, direction, magic)
                    ok_close = close_result is not None and close_result.retcode in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL}
                    ok_sl = _modify_sl(ticket, symbol, _align(tp1, float(symbol_info.trade_tick_size or 0.01)))
                    if ok_close:
                        plan["tp2_done"] = True
                        plan["remaining"] = max(0.0, float(plan.get("remaining", remaining)) - close_volume)
                        plan["lock_r1_set"] = ok_sl
                        changed = True
                        results.append({"signal_id": plan["signal_id"], "event": "TP2"})
                        try:
                            deal_ticket = int(getattr(close_result, "deal", 0) or 0)
                            facts = _deal_exit_facts(mt5, ticket, deal_ticket)
                            append_ai_lifecycle_event(root, str(ticket), {
                                "event_id": f"{ticket}_TP2",
                                "server_time": _server_time_text(facts.get("time", time.time())),
                                "event_kind": "EXIT", "stage": "TP2",
                                "position_id": str(ticket), "deal_ticket": str(deal_ticket),
                                "signal_id": str(plan.get("signal_id", "")), "direction": direction,
                                "volume": str(facts.get("volume", close_volume)),
                                "price": str(facts.get("price", "")),
                                "profit": f"{facts.get('profit', 0.0):.2f}",
                                "tp2_done": "1",
                            })
                        except Exception:
                            pass
            if changed:
                save_plan(root, plan)
    finally:
        mt5.shutdown()
    return results


def _trade_group_for_magic(magic: int, ai_magic: int, e2e_magic: int) -> str | None:
    """Classify a trade by its entry magic. Returns None for E2E test orders."""
    if magic == EA_MAGIC:
        return "ea"
    if magic == ai_magic:
        return "ai"
    if magic == e2e_magic:
        return None
    # magic=0 是桌面客户端手动下单的默认值；其它非EA/非AI的 magic 也按人工归类。
    # （本账户只挂本系统自己的 EA，magic 固定，因此 0 号单就是人工单。）
    return "manual"


def ea_position_ids_from_lifecycle(actual_trade_rows: list[dict[str, Any]]) -> set[int]:
    """Identify Trader A positions from their own OPEN lifecycle facts.

    Current EA slot magic changes with the trading day, so a fixed magic alone
    cannot safely distinguish these positions from manual trades.
    """
    position_ids: set[int] = set()
    for trade in actual_trade_rows:
        if str(trade.get("data_source") or "") != "lifecycle":
            continue
        rows = trade.get("rows") or []
        if not any(
            str(row.get("event_kind") or "").upper() == "OPEN"
            and str(row.get("trader") or "").upper() == "A"
            for row in rows if isinstance(row, dict)
        ):
            continue
        try:
            position_id = int(trade.get("position_id") or 0)
        except (TypeError, ValueError):
            continue
        if position_id > 0:
            position_ids.add(position_id)
    return position_ids


def snapshot_manual_open_positions(root: Path, config: dict[str, Any]) -> int:
    """记录当前非EA/非Parallel AI持仓的开仓快照，且只写一次不覆盖。

    magic=0 是桌面客户端手动下单的默认值，按“人工”归类（本账户只挂本系统自己的
    EA，magic 固定）。后续用户移动 SL/TP 也不会覆盖这份开仓快照，供人工交易
    R 重建使用。
    """
    import MetaTrader5 as mt5

    config = config or {}
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return 0
    try:
        positions = mt5.positions_get() or []
        e2e_magic = int(config.get("parallel_ai_e2e_test_magic", 2026072903))
        ai_magic = int(config.get("parallel_ai_magic", DEFAULT_AI_MAGIC))
        snapshot_dir = root / "Manual_Trade_Snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for position in positions:
            magic = int(getattr(position, "magic", 0) or 0)
            if magic in (EA_MAGIC, ai_magic, e2e_magic):
                continue
            position_id = int(getattr(position, "ticket", 0) or 0)
            if not position_id:
                continue
            path = snapshot_dir / f"Position_{position_id}.json"
            if path.exists():
                continue
            entry = float(getattr(position, "price_open", 0) or 0)
            sl = float(getattr(position, "sl", 0) or 0)
            tp = float(getattr(position, "tp", 0) or 0)
            volume = float(getattr(position, "volume", 0) or 0)
            pos_type = int(getattr(position, "type", 0) or 0)
            direction = "BUY" if pos_type == 0 else "SELL" if pos_type == 1 else str(pos_type)
            risk_distance = abs(entry - sl) if entry > 0 and sl > 0 else None
            risk_amount = (
                round(risk_distance * volume * 100.0, 2) if risk_distance is not None else None
            )
            snapshot = {
                "position_id": position_id,
                "open_deal_ticket": "",
                "symbol": str(getattr(position, "symbol", "") or ""),
                "direction": direction,
                "entry": entry,
                "initial_sl": sl,
                "initial_tp": tp,
                "volume": volume,
                "open_time": datetime.fromtimestamp(
                    float(getattr(position, "time", 0) or 0), tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "initial_risk_distance": round(risk_distance, 2) if risk_distance is not None else None,
                "initial_risk_amount": risk_amount,
                "magic": magic,
                "comment": str(getattr(position, "comment", "") or ""),
                "snapshot_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
            path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            saved += 1
        return saved
    finally:
        mt5.shutdown()


def collect_position_ownership(
    root: Path, server_open: str, server_close: str,
    config: dict[str, Any] | None = None,
    ea_position_ids: set[int] | None = None,
) -> dict[int, str]:
    """Map position_id -> trade group using the OPEN deal magic.

    Ownership is decided once per Position (never per partial-close deal), so a
    TP1 close with magic=0 cannot split a position into a different group. E2E
    test positions map to None and are excluded. Empty on MT5 read failure.
    """
    import MetaTrader5 as mt5

    config = config or {}
    known_ea_positions = ea_position_ids or set()
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return {}
    try:
        start_epoch = datetime.strptime(server_open, "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
        end_epoch = datetime.strptime(server_close, "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
        wide_start = datetime.fromtimestamp(start_epoch - 2 * 86400)
        wide_end = datetime.fromtimestamp(end_epoch + 2 * 86400)
        deals = mt5.history_deals_get(wide_start, wide_end) or []
        e2e_magic = int(config.get("parallel_ai_e2e_test_magic", 2026072903))
        ai_magic = int(config.get("parallel_ai_magic", DEFAULT_AI_MAGIC))
        position_magic: dict[int, int] = {}
        for deal in deals:
            if int(deal.entry) == 0:  # DEAL_ENTRY_IN
                position_magic.setdefault(int(deal.position_id), int(deal.magic))
        ownership: dict[int, str] = {}
        for position_id, magic in position_magic.items():
            group = (
                "ea" if position_id in known_ea_positions
                else _trade_group_for_magic(magic, ai_magic, e2e_magic)
            )
            if group is not None:
                ownership[position_id] = group
        return ownership
    finally:
        mt5.shutdown()


def collect_trade_groups(
    root: Path, config: dict[str, Any], server_open: str, server_close: str,
    ea_position_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Read the broker session's closed trades from MT5 history and group them."""
    import MetaTrader5 as mt5

    empty = {"ea": {"count": 0}, "ai": {"count": 0}, "manual": {"count": 0}, "unknown": {"count": 0}}
    known_ea_positions = ea_position_ids or set()
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return empty
    try:
        start_epoch = datetime.strptime(server_open, "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
        end_epoch = datetime.strptime(server_close, "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
        # The MT5 API's narrow time windows are unreliable; query a wide range
        # and filter locally by the deal timestamps (server-time epoch).
        wide_start = datetime.fromtimestamp(start_epoch - 2 * 86400)
        wide_end = datetime.fromtimestamp(end_epoch + 2 * 86400)
        deals = mt5.history_deals_get(wide_start, wide_end) or []
        groups: dict[str, dict[str, Any]] = {
            "ea": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0},
            "ai": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0},
            "manual": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0},
            "unknown": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0, "details": []},
        }
        e2e_magic = int(config.get("parallel_ai_e2e_test_magic", 2026072903))
        ai_magic = int(config.get("parallel_ai_magic", DEFAULT_AI_MAGIC))
        # Determine ownership from the OPEN deal (DEAL_ENTRY_IN) magic, never
        # from a partial-close deal whose magic may be 0.
        position_magic: dict[int, int] = {}
        for deal in deals:
            if int(deal.entry) == 0:  # DEAL_ENTRY_IN
                position_magic.setdefault(int(deal.position_id), int(deal.magic))
        by_position: dict[int, list] = {}
        for deal in deals:
            if deal.entry not in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
                continue
            if not (start_epoch <= float(deal.time) <= end_epoch):
                continue
            by_position.setdefault(deal.position_id, []).append(deal)
        for position_id, position_deals in by_position.items():
            magic = position_magic.get(int(position_id), int(position_deals[0].magic))
            group = (
                "ea" if int(position_id) in known_ea_positions
                else _trade_group_for_magic(magic, ai_magic, e2e_magic)
            )
            if group is None:
                continue
            net = sum(float(d.profit) + float(d.commission) + float(d.swap) + float(d.fee) for d in position_deals)
            groups[group]["count"] += 1
            groups[group]["net"] += net
            if net > 0:
                groups[group]["win"] += 1
            elif net < 0:
                groups[group]["loss"] += 1
            if group == "unknown":
                groups[group]["details"].append({
                    "position_id": str(position_id),
                    "deal_tickets": [str(int(d.ticket)) for d in position_deals],
                    "magic": magic,
                })
            risk = _lookup_risk(root, position_id)
            if risk and risk > 0 and group == "ai":
                groups[group]["r_total"] += net / risk
                groups[group]["r_samples"] += 1
        # Open-at-day-end: decided from history so an offline rebuild matches a
        # live run. A position is "open" when its open deal is at/before the
        # session close and it has no closing deal at/before that close.
        open_times: dict[int, float] = {}
        last_close_times: dict[int, float] = {}
        for deal in deals:
            if int(deal.entry) == 0:
                open_times.setdefault(int(deal.position_id), float(deal.time))
            elif int(deal.entry) in (1, 3):
                pid = int(deal.position_id)
                last_close_times[pid] = max(last_close_times.get(pid, 0.0), float(deal.time))
        for position_id in position_magic:
            magic = position_magic[position_id]
            group_name = (
                "ea" if position_id in known_ea_positions
                else _trade_group_for_magic(magic, ai_magic, e2e_magic)
            )
            if group_name is None:
                continue
            open_time = open_times.get(position_id)
            if open_time is None or open_time > end_epoch:
                continue
            last_close = last_close_times.get(position_id)
            if last_close is None or last_close > end_epoch:
                groups[group_name]["open"] = int(groups[group_name].get("open", 0)) + 1
        return groups
    finally:
        mt5.shutdown()


def fallback_trade_groups_from_lifecycle(
    actual_trade_rows: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从生命周期 CSV 推导三组统计，用于 MT5 历史读取失败时的兜底。

    生命周期 CSV 是 EA/AI 各自写出的成交事实，MT5 连接失败（例如策略测试器
    占用终端）时仍可用。EA 生命周期 CSV 没有 magic 列，按 EA 处理；AI 生命周期
    CSV 带 parallel_ai_magic；E2E 测试 magic 排除。
    """
    settings = config or {}
    ai_magic = int(settings.get("parallel_ai_magic", DEFAULT_AI_MAGIC))
    ea_magic = str(int(settings.get("ea_magic", EA_MAGIC)))
    e2e_magic = str(int(settings.get("parallel_ai_e2e_test_magic", 2026072903)))
    groups = {
        "ea": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0},
        "ai": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0},
        "manual": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0},
        "unknown": {"count": 0, "open": 0, "win": 0, "loss": 0, "net": 0.0, "r_total": 0.0, "r_samples": 0},
    }
    for row in actual_trade_rows:
        magic = str(row.get("magic") or "").strip()
        if magic == e2e_magic:
            continue
        if magic == str(ai_magic):
            group = "ai"
        elif magic == ea_magic or magic == "":
            group = "ea"
        else:
            # 含 magic=0（桌面手动下单）→ 人工；非EA/非AI的其它 magic 也归人工。
            group = "manual"
        status = str(row.get("status") or "")
        if not status.startswith("closed"):
            groups[group]["open"] += 1
            continue
        groups[group]["count"] += 1
        net = float(row.get("whole_trade_net") or row.get("net_profit") or 0.0)
        groups[group]["net"] += net
        if net > 0:
            groups[group]["win"] += 1
        elif net < 0:
            groups[group]["loss"] += 1
        if group == "ai":
            final_r = float(row.get("final_r") or 0.0)
            groups[group]["r_total"] += final_r
            groups[group]["r_samples"] += 1
    return groups


def fallback_ownership_from_lifecycle(
    actual_trade_rows: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """从生命周期 CSV 推导 position_id -> 交易组归属，用于 MT5 读取失败时的兜底。"""
    settings = config or {}
    ai_magic = str(int(settings.get("parallel_ai_magic", DEFAULT_AI_MAGIC)))
    ea_magic = str(int(settings.get("ea_magic", EA_MAGIC)))
    e2e_magic = str(int(settings.get("parallel_ai_e2e_test_magic", 2026072903)))
    ownership: dict[str, str] = {}
    for row in actual_trade_rows:
        position_id = str(row.get("position_id") or "").strip()
        if not position_id:
            continue
        magic = str(row.get("magic") or "").strip()
        if magic == e2e_magic:
            continue
        if magic == ai_magic:
            ownership[position_id] = "ai"
        elif magic == ea_magic or magic == "":
            ownership[position_id] = "ea"
        else:
            # 含 magic=0（桌面手动下单）→ 人工
            ownership[position_id] = "manual"
    return ownership


def fallback_ai_trade_rows_from_lifecycle(
    actual_trade_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从生命周期 CSV 提取 Parallel AI（Trader B）成交行，用于 MT5 失败时的兜底。"""
    return [
        dict(row)
        for row in actual_trade_rows
        if str(row.get("magic") or "").strip() == str(DEFAULT_AI_MAGIC)
    ]


def collect_ai_trade_details(
    root: Path, server_open: str, server_close: str
) -> list[dict[str, Any]]:
    """Return AI-group trade plans for the broker day, with the opening reason."""
    day = str(server_open)[:10].replace(".", "")
    plans = load_plans(root)
    matched = [plan for plan in plans if day in str(plan.get("signal_id", ""))]
    matched.sort(key=lambda plan: str(plan.get("created_beijing", "")))
    return matched


def _lookup_risk(root: Path, position_id: int) -> float | None:
    for plan in load_plans(root):
        if int(plan.get("position_ticket", 0) or 0) == position_id or int(plan.get("order_ticket", 0) or 0) == position_id:
            distance = abs(float(plan["entry"]) - float(plan["sl"]))
            return distance * float(plan["initial_volume"]) * 100.0
    return None


def collect_ai_trade_rows(
    root: Path, server_open: str, server_close: str,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild AI (Trader B) trades from MT5 deals + AI plan files.

    This is authoritative for realized net: it sums the real closing deals
    (profit + commission + swap + fee) rather than trusting a lifecycle CSV
    that may double-count a TP1 partial close. Entry/SL/volume/route come from
    the AI plan file when available, otherwise from the opening deal.
    """
    import MetaTrader5 as mt5

    config = config or {}
    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        return []
    try:
        start_epoch = datetime.strptime(server_open, "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
        end_epoch = datetime.strptime(server_close, "%Y.%m.%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
        wide_start = datetime.fromtimestamp(start_epoch - 2 * 86400)
        wide_end = datetime.fromtimestamp(end_epoch + 2 * 86400)
        deals = mt5.history_deals_get(wide_start, wide_end) or []
        ai_magic = int(config.get("parallel_ai_magic", DEFAULT_AI_MAGIC))
        plans_by_position: dict[int, dict[str, Any]] = {}
        for plan in load_plans(root):
            pid = int(plan.get("position_ticket", 0) or plan.get("order_ticket", 0) or 0)
            if pid:
                plans_by_position.setdefault(pid, plan)
        position_magic: dict[int, int] = {}
        open_deals: dict[int, Any] = {}
        for deal in deals:
            if int(deal.entry) == 0:
                position_magic.setdefault(int(deal.position_id), int(deal.magic))
                open_deals.setdefault(int(deal.position_id), deal)
        close_deals: dict[int, list[Any]] = {}
        for deal in deals:
            if int(deal.entry) in (1, 3):
                close_deals.setdefault(int(deal.position_id), []).append(deal)
        rows: list[dict[str, Any]] = []
        for position_id, magic in position_magic.items():
            if magic != ai_magic:
                continue
            opens = open_deals.get(position_id)
            closes = sorted(close_deals.get(position_id, []), key=lambda d: int(d.time))
            if not closes and not opens:
                continue
            plan = plans_by_position.get(position_id, {})
            direction = str(plan.get("direction") or "").upper()
            if not direction and opens is not None:
                direction = "BUY" if float(opens.volume) > 0 else "SELL"
            open_time = opens.time if opens is not None else None
            if open_time is None or float(open_time) > end_epoch:
                continue  # opened after this broker session
            window_closes = [
                d for d in closes if start_epoch <= float(d.time) <= end_epoch
            ]
            last_close_time = int(closes[-1].time) if closes else None
            # 只保留“本交易时段内有平仓动作”或“仍持仓到时段末”的 AI 仓位；
            # 完全在本次交易日之前就已平仓的仓位属于前一天，不能混入本日复盘。
            if last_close_time is not None and float(last_close_time) < start_epoch:
                continue
            # "Open at day end" means there is no closing deal at/before the
            # session close, or the final close happens after it.
            open_at_day_end = (
                last_close_time is None or float(last_close_time) > end_epoch
            )
            close_time = last_close_time if not open_at_day_end else None
            entry = float(plan.get("entry") or (opens.price if opens is not None else 0.0) or 0.0)
            sl = float(plan.get("sl") or 0.0)
            volume = float(plan.get("initial_volume") or (opens.volume if opens is not None else 0.0) or 0.0)
            # Realized net only counts closing deals inside the session window;
            # floating P/L on a still-open runner is not "已实现".
            net = sum(
                float(d.profit) + float(d.commission) + float(d.swap) + float(d.fee)
                for d in (window_closes if open_at_day_end else closes)
            )
            risk = abs(entry - sl) * volume * 100.0 if entry > 0 and sl > 0 and volume > 0 else 0.0
            final_r = round(net / risk, 2) if risk > 0 else None
            opened_today = (
                open_time is not None and start_epoch <= float(open_time) <= end_epoch
            )
            if open_at_day_end:
                status = "open_at_day_end"
            elif opened_today:
                status = "closed"
            else:
                status = "closed_cross_day"
            rows.append({
                "position_id": str(position_id),
                "magic": str(ai_magic),
                "direction": direction,
                "route": str(plan.get("route", "") or ""),
                "entry_price": entry,
                "initial_volume": volume,
                "initial_sl": sl,
                "initial_risk": round(risk, 2) if risk > 0 else None,
                "tp1_price": plan.get("tp1"),
                "tp2_price": plan.get("tp2"),
                "open_time": _server_time_text(open_time) if open_time else "",
                "close_time": _server_time_text(close_time) if close_time else "",
                "net_profit": round(net, 2),
                "whole_trade_net": round(net, 2),
                "final_r": final_r,
                "status": status,
                "close_reason": _ai_close_reason(plan, window_closes) if not open_at_day_end else "",
            })
        rows.sort(key=lambda r: r["open_time"] or r["close_time"])
        return rows
    finally:
        mt5.shutdown()


def _ai_close_reason(plan: dict[str, Any], closes: list[Any]) -> str:
    """Human-friendly close reason from plan stage flags + deal reason."""
    if not closes:
        return ""
    reasons = [int(getattr(d, "reason", 0) or 0) for d in closes]
    tp1_done = bool(plan.get("tp1_done"))
    tp2_done = bool(plan.get("tp2_done"))
    if tp2_done and tp1_done:
        return "TP1部分止盈 + TP2部分止盈"
    if tp1_done and 4 in reasons:
        return "TP1部分止盈 + 剩余止损"
    if 4 in reasons:
        return "止损平仓"
    if tp1_done:
        return "TP1部分止盈"
    if all(r == 3 for r in reasons):
        return "AI主动退出"
    return "AI主动退出｜历史原因未记录"
