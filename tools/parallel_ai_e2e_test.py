"""One-shot Parallel AI execution-chain E2E test (demo account only).

This module never changes the formal Parallel AI trading logic. It reuses the
production plan/check helpers (validate_stop_plan, compute_minimum_pending_distance,
precheck_passed) but places orders under a dedicated E2E test magic and comment so
they can never leak into EA / AI / manual production statistics.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


TEST_COMMENT_SELL = "AI_E2E_TEST_SELLSTOP"
TEST_COMMENT_BUY = "AI_E2E_TEST_BUYSTOP"
TEST_COMMENT_MARKET = "AI_E2E_TEST_MARKET"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(step: str, message: str) -> None:
    print(f"[{_now()}] {step}: {message}")


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _confirm_demo(mt5: Any) -> tuple[bool, dict[str, Any]]:
    info = mt5.account_info()
    if info is None:
        return False, {"error": "account_info returned None"}
    trade_mode = int(getattr(info, "trade_mode", -1))
    evidence = {
        "login": int(getattr(info, "login", 0)),
        "server": str(getattr(info, "server", "")),
        "trade_mode": trade_mode,
        "is_demo": trade_mode == 0,
        "balance": float(getattr(info, "balance", 0.0)),
    }
    return trade_mode == 0, evidence


def _symbol_snapshot(mt5: Any, symbol: str) -> dict[str, Any]:
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    return {
        "symbol": symbol,
        "bid": float(tick.bid),
        "ask": float(tick.ask),
        "tick_size": float(getattr(info, "trade_tick_size", 0.01) or 0.01),
        "point": float(getattr(info, "point", 0.01) or 0.01),
        "trade_stops_level": float(getattr(info, "trade_stops_level", 0.0) or 0.0),
        "volume_min": float(getattr(info, "volume_min", 0.01) or 0.01),
        "volume_step": float(getattr(info, "volume_step", 0.01) or 0.01),
        "filling_mode": int(getattr(info, "filling_mode", 0) or 0),
    }


def _align(price: float, tick: float) -> float:
    return round(price / tick) * tick


def _test_stop(
    mt5: Any, config: dict[str, Any], snap: dict[str, Any],
    direction: str, test_magic: int, evidence: dict[str, Any],
) -> bool:
    from tools.ai_trade_manager import compute_minimum_pending_distance, precheck_passed, validate_stop_plan

    tick = snap["tick_size"]
    min_dist = compute_minimum_pending_distance(snap["trade_stops_level"], snap["point"])
    safety_buffer = 1.0
    if direction == "SELL":
        entry = _align(snap["bid"] - min_dist - safety_buffer, tick)
        sl = _align(entry + 2.0, tick)
        order_type = mt5.ORDER_TYPE_SELL_STOP
        comment = TEST_COMMENT_SELL
        key = "sell_stop"
    else:
        entry = _align(snap["ask"] + min_dist + safety_buffer, tick)
        sl = _align(entry - 2.0, tick)
        order_type = mt5.ORDER_TYPE_BUY_STOP
        comment = TEST_COMMENT_BUY
        key = "buy_stop"

    plan = validate_stop_plan(
        {"direction": direction, "entry": entry}, snap["bid"], snap["ask"], tick, snap["trade_stops_level"]
    )
    evidence[key] = {
        "direction": direction, "bid": snap["bid"], "ask": snap["ask"],
        "entry": entry, "sl": sl, "minimum_pending_distance": min_dist,
        "plan_validation": plan,
    }
    if plan["status"] != "VALID":
        _log(f"{key}_plan", f"INVALID: {plan['reason']}")
        return False
    _log(f"{key}_plan", f"VALID entry={entry} sl={sl} min_dist={min_dist}")

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": snap["symbol"],
        "volume": snap["volume_min"],
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": 0.0,
        "deviation": 30,
        "magic": test_magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_SPECIFIED,
        "expiration": int(mt5.symbol_info_tick(snap["symbol"]).time) + 900,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    check = mt5.order_check(request)
    if not precheck_passed(check):
        retcode = check.retcode if check is not None else "None"
        evidence[key]["precheck"] = {"ok": False, "retcode": retcode,
                                     "comment": check.comment if check is not None else "",
                                     "last_error": mt5.last_error()}
        _log(f"{key}_precheck", f"FAIL retcode={retcode} {mt5.last_error()}")
        return False
    evidence[key]["precheck"] = {"ok": True, "retcode": check.retcode, "comment": check.comment}
    _log(f"{key}_precheck", f"OK retcode={check.retcode} comment={check.comment}")

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        evidence[key]["order_send"] = {"ok": False, "retcode": result.retcode if result else "None",
                                       "comment": result.comment if result else "",
                                       "last_error": mt5.last_error()}
        _log(f"{key}_order_send", f"FAIL {mt5.last_error()}")
        return False
    ticket = int(result.order)
    evidence[key]["order_send"] = {"ok": True, "retcode": result.retcode, "ticket": ticket}
    _log(f"{key}_order_send", f"OK ticket={ticket}")

    active = mt5.orders_get(ticket=ticket)
    if not active:
        evidence[key]["orders_get"] = {"ok": False, "found": False}
        _log(f"{key}_orders_get", "FAIL order not found")
        return False
    evidence[key]["orders_get"] = {
        "ok": True, "ticket": active[0].ticket, "type": active[0].type,
        "magic": active[0].magic, "comment": active[0].comment,
    }
    _log(f"{key}_orders_get", f"PENDING_ACTIVE magic={active[0].magic} comment={active[0].comment}")

    cancel = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
    if cancel is None or cancel.retcode != mt5.TRADE_RETCODE_DONE:
        evidence[key]["cancel"] = {"ok": False, "retcode": cancel.retcode if cancel else "None"}
        _log(f"{key}_cancel", "FAIL")
        return False
    evidence[key]["cancel"] = {"ok": True, "retcode": cancel.retcode}
    _log(f"{key}_cancel", "OK")

    history = mt5.history_orders_get(ticket=ticket)
    if not history:
        evidence[key]["history"] = {"ok": False, "found": False}
        _log(f"{key}_history", "FAIL order not in history")
        return False
    state_int = int(history[-1].state)
    evidence[key]["history"] = {"ok": True, "state": state_int,
                                "state_name": "CANCELLED" if state_int == mt5.ORDER_STATE_CANCELED else state_int}
    _log(f"{key}_history", f"state={state_int}")
    return True


def _test_market(
    mt5: Any, config: dict[str, Any], snap: dict[str, Any],
    test_magic: int, evidence: dict[str, Any],
) -> bool:
    from tools.ai_trade_manager import _pick_filling, precheck_passed

    symbol = snap["symbol"]
    tick = mt5.symbol_info_tick(symbol)
    ask = float(tick.ask)
    filling = _pick_filling(mt5.symbol_info(symbol))
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": snap["volume_min"],
        "type": mt5.ORDER_TYPE_BUY,
        "price": ask,
        "sl": 0.0,
        "tp": 0.0,
        "deviation": 30,
        "magic": test_magic,
        "comment": TEST_COMMENT_MARKET,
        "type_filling": filling,
    }
    evidence["market"] = {"direction": "BUY", "volume": snap["volume_min"], "ask": ask, "filling": filling}

    check = mt5.order_check(request)
    if not precheck_passed(check):
        evidence["market"]["precheck"] = {"ok": False, "retcode": check.retcode if check else "None",
                                          "comment": check.comment if check else "", "last_error": mt5.last_error()}
        _log("market_precheck", f"FAIL {mt5.last_error()}")
        return False
    evidence["market"]["precheck"] = {"ok": True, "retcode": check.retcode}
    _log("market_precheck", f"OK retcode={check.retcode}")

    result = mt5.order_send(request)
    if result is None or result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
        evidence["market"]["order_send"] = {"ok": False, "retcode": result.retcode if result else "None",
                                            "comment": result.comment if result else "", "last_error": mt5.last_error()}
        _log("market_order_send", f"FAIL {mt5.last_error()}")
        return False
    deal_ticket = int(result.deal) if getattr(result, "deal", 0) else int(result.order)
    evidence["market"]["order_send"] = {"ok": True, "retcode": result.retcode, "deal": deal_ticket}
    _log("market_order_send", f"OK retcode={result.retcode} deal={deal_ticket}")

    positions = [
        p for p in (mt5.positions_get(symbol=symbol) or [])
        if int(p.magic) == test_magic and str(p.comment).startswith("AI_E2E_TEST")
    ]
    if not positions:
        evidence["market"]["position"] = {"ok": False, "found": False}
        _log("market_position", "FAIL position not found")
        return False
    position = positions[0]
    if int(position.magic) != test_magic:
        evidence["market"]["position"] = {"ok": False, "magic_mismatch": position.magic}
        _log("market_position", "ABORT magic mismatch")
        return False
    position_ticket = int(position.ticket)
    evidence["market"]["position"] = {"ok": True, "ticket": position_ticket,
                                      "magic": position.magic, "volume": position.volume}
    _log("market_position", f"FOUND ticket={position_ticket} magic={position.magic}")

    bid = float(mt5.symbol_info_tick(symbol).bid)
    close_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(position.volume),
        "type": mt5.ORDER_TYPE_SELL,
        "position": position_ticket,
        "price": bid,
        "deviation": 30,
        "magic": test_magic,
        "comment": "AI_E2E_TEST_MARKET_CLOSE",
        "type_filling": filling,
    }
    close = mt5.order_send(close_request)
    if close is None or close.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
        evidence["market"]["close"] = {"ok": False, "retcode": close.retcode if close else "None",
                                       "last_error": mt5.last_error()}
        _log("market_close", "FAIL")
        return False
    close_deal = int(close.deal) if getattr(close, "deal", 0) else int(close.order)
    evidence["market"]["close"] = {"ok": True, "retcode": close.retcode, "deal": close_deal}
    _log("market_close", f"OK deal={close_deal}")
    return True


def _check_residue(mt5: Any, test_magic: int) -> dict[str, int]:
    positions = [p for p in (mt5.positions_get() or []) if int(p.magic) == test_magic]
    orders = [o for o in (mt5.orders_get() or []) if int(o.magic) == test_magic]
    return {"positions": len(positions), "orders": len(orders)}


def _send_lark_card(webhook: str, title: str, content: str) -> tuple[int, str]:
    import urllib.request

    card = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
            "body": {"elements": [{"tag": "markdown", "content": content}]},
        },
    }
    body = json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.getcode()
        text = resp.read().decode("utf-8", errors="replace")
    return status, text


def main() -> int:
    parser = argparse.ArgumentParser(description="Parallel AI E2E execution-chain test")
    parser.add_argument("--config", required=True)
    parser.add_argument("--force", action="store_true", help="ignore e2e_test_enabled gate")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = _load_config(config_path)
    if not args.force and not bool(config.get("parallel_ai_e2e_test_enabled", False)):
        _log("GATE", "parallel_ai_e2e_test_enabled is false; aborting")
        return 3

    import MetaTrader5 as mt5
    from tools.ai_review_service import apply_trade_terminal_override
    from tools.ai_trade_manager import TERMINAL_PATH

    # 多套系统并存时，E2E 必须打到本配置绑定的终端/账户（例如 V3.10.9 的 871610），
    # 否则会在 V3.9.20 的账户上下测试单。
    apply_trade_terminal_override(config)
    from tools import ai_trade_manager as _trade_manager
    TERMINAL_PATH = _trade_manager.TERMINAL_PATH

    if not mt5.initialize(TERMINAL_PATH, timeout=5000):
        _log("MT5_INIT", f"FAIL {mt5.last_error()}")
        return 2
    try:
        demo_ok, demo_evidence = _confirm_demo(mt5)
        require_demo = bool(config.get("parallel_ai_e2e_require_demo_account", True))
        if require_demo and not demo_ok:
            evidence = {"account_check": demo_evidence, "status": "E2E_TEST_ABORTED",
                        "reason": "ACCOUNT_NOT_CONFIRMED_DEMO"}
            _log("ACCOUNT_CHECK", f"ABORT not demo: {demo_evidence}")
            _save_evidence(config, evidence)
            return 4

        symbol = "XAUUSD.s"
        snap = _symbol_snapshot(mt5, symbol)
        test_magic = int(config.get("parallel_ai_e2e_test_magic", 2026072903))
        evidence: dict[str, Any] = {
            "account_check": demo_evidence, "symbol_check": snap, "test_magic": test_magic,
        }
        _log("ACCOUNT_CHECK", f"DEMO confirmed login={demo_evidence['login']} trade_mode={demo_evidence['trade_mode']}")

        sell_ok = _test_stop(mt5, config, snap, "SELL", test_magic, evidence)
        buy_ok = _test_stop(mt5, config, snap, "BUY", test_magic, evidence)

        market_ok = True
        if bool(config.get("parallel_ai_e2e_market_fill_test_enabled", False)):
            market_ok = _test_market(mt5, config, snap, test_magic, evidence)
        else:
            evidence["market"] = {"skipped": True}

        residue = _check_residue(mt5, test_magic)
        evidence["residue"] = residue

        passed = sell_ok and buy_ok and market_ok and residue["orders"] == 0 and residue["positions"] == 0
        evidence["status"] = "E2E_TEST_PASS" if passed else "E2E_TEST_FAIL"
        _log("RESIDUE", f"orders={residue['orders']} positions={residue['positions']}")
        _log("RESULT", evidence["status"])
        _save_evidence(config, evidence)

        title = "🧪 Parallel AI全链路测试完成" if passed else "🧪 Parallel AI全链路测试失败"
        content = _summary_content(evidence, passed)
        backup_webhook = str(config.get("parallel_ai_e2e_lark_webhook", ""))
        if backup_webhook:
            try:
                status, text = _send_lark_card(backup_webhook, title, content)
                evidence["lark"] = {"http": status, "response": text}
                _log("LARK", f"http={status} response={text}")
            except Exception as exc:
                evidence["lark"] = {"error": f"{type(exc).__name__}: {exc}"}
                _log("LARK", f"FAIL {exc}")
        else:
            evidence["lark"] = {"skipped": "no backup webhook configured"}
            _log("LARK", "no backup webhook configured; skipping")

        # Auto-disable test mode so the next service start cannot fire test orders.
        config["parallel_ai_e2e_test_enabled"] = False
        config["parallel_ai_e2e_market_fill_test_enabled"] = False
        _save_config(config_path, config)
        _log("CONFIG", "e2e test mode disabled")
        _save_evidence(config, evidence)
        return 0 if passed else 1
    finally:
        mt5.shutdown()


def _save_evidence(config: dict[str, Any], evidence: dict[str, Any]) -> None:
    data_root = Path(str(config.get("data_root", "")))
    if not str(data_root).strip():
        return
    out_dir = data_root / "E2E_Test"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"e2e_test_{stamp}.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("EVIDENCE", str(path))


def _summary_content(evidence: dict[str, Any], passed: bool) -> str:
    demo = evidence.get("account_check", {})
    sell = evidence.get("sell_stop", {})
    buy = evidence.get("buy_stop", {})
    market = evidence.get("market", {})
    residue = evidence.get("residue", {})
    lines = [
        f"模拟账户：{'确认 DEMO' if demo.get('is_demo') else '未确认'}（login={demo.get('login')}）",
        "",
        "SELL_STOP：",
        f"创建 {'✅' if sell.get('order_send', {}).get('ok') else '❌'}",
        f"服务器接受 {'✅' if sell.get('precheck', {}).get('ok') else '❌'}",
        f"Ticket确认 {'✅' if sell.get('orders_get', {}).get('ok') else '❌'}",
        f"撤销 {'✅' if sell.get('cancel', {}).get('ok') else '❌'}",
        "",
        "BUY_STOP：",
        f"创建 {'✅' if buy.get('order_send', {}).get('ok') else '❌'}",
        f"服务器接受 {'✅' if buy.get('precheck', {}).get('ok') else '❌'}",
        f"Ticket确认 {'✅' if buy.get('orders_get', {}).get('ok') else '❌'}",
        f"撤销 {'✅' if buy.get('cancel', {}).get('ok') else '❌'}",
        "",
        "Market生命周期：",
        f"开仓 {'✅' if market.get('order_send', {}).get('ok') else ('跳过' if market.get('skipped') else '❌')}",
        f"Position确认 {'✅' if market.get('position', {}).get('ok') else ('—' if market.get('skipped') else '❌')}",
        f"关闭 {'✅' if market.get('close', {}).get('ok') else ('—' if market.get('skipped') else '❌')}",
        "",
        f"测试残留：挂单 {residue.get('orders', '?')} ／ 持仓 {residue.get('positions', '?')}",
        "",
        "Parallel AI执行链：" + ("PASS" if passed else "FAIL"),
        "",
        "安全校验：EA复盘",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
