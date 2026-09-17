from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _ai_status_text(trade: dict[str, Any]) -> str:
    if bool(trade.get("ai_is_error")):
        return "异常·降级放行"
    return "允许" if trade.get("ai_allow_trade", True) else "未允许"


KNOWN_STAGE_KEYS = ("TP1", "TP2", "RUNNER", "OTHER")
FINAL_STAGES = {"RUNNER", "REMAINDER", "INITIAL_EXIT", "OTHER_EXIT"}


def _float(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip().replace(".", "-")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _row_net(row: dict[str, Any]) -> float:
    return sum(_float(row.get(key)) for key in ("profit", "commission", "swap", "fee"))


def _first_nonempty(rows: list[dict[str, str]], key: str, fallback: Any = "") -> Any:
    for row in rows:
        value = row.get(key, "")
        if str(value).strip() != "":
            return value
    return fallback


def load_position_lifecycle(root: Path, position_id: str) -> list[dict[str, str]]:
    path = root / "Trade_Lifecycle" / f"Position_{position_id}.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter=";")]


def _deduplicate(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        event_id = str(row.get("event_id", "")).strip()
        if not event_id:
            event_id = "|".join(
                str(row.get(key, ""))
                for key in ("position_id", "deal_ticket", "event_kind", "server_time")
            )
        if event_id in seen:
            continue
        seen.add(event_id)
        output.append(row)
    output.sort(key=lambda row: str(row.get("server_time", "")))
    return output


def aggregate_position(
    position_id: str,
    rows: list[dict[str, str]],
    fallback: dict[str, object],
) -> dict[str, object]:
    unique = _deduplicate(rows)
    stage_net = {key: 0.0 for key in KNOWN_STAGE_KEYS}
    calculated_total = 0.0
    open_time = ""
    close_time = ""
    close_reason = ""
    closed = False

    for row in unique:
        net = _row_net(row)
        calculated_total += net
        event_kind = str(row.get("event_kind", "")).strip().upper()
        stage = str(row.get("stage", "")).strip().upper()
        server_time = str(row.get("server_time", "")).strip()
        if event_kind == "OPEN" and not open_time:
            open_time = server_time
        if event_kind in {"EXIT", "FINAL", "FINAL_EXIT"}:
            key = stage if stage in {"TP1", "TP2", "RUNNER"} else "OTHER"
            stage_net[key] += net
            close_time = server_time or close_time
            if event_kind in {"FINAL", "FINAL_EXIT"} or stage in FINAL_STAGES:
                closed = True
        reason = str(row.get("close_reason", "")).strip()
        if reason:
            close_reason = reason
            closed = True

    fallback_net = fallback.get("net_profit", fallback.get("whole_trade_net"))
    if fallback_net is not None:
        authoritative = _float(fallback_net)
        difference = authoritative - calculated_total
        if abs(difference) >= 0.005:
            stage_net["OTHER"] += difference
        calculated_total = authoritative

    fallback_initial_risk = _float(fallback.get("initial_risk"))
    lifecycle_initial_risk = _float(_first_nonempty(unique, "initial_risk", 0.0))
    initial_risk = (
        fallback_initial_risk if fallback_initial_risk > 0 else lifecycle_initial_risk
    )
    net_profit = round(calculated_total, 2)
    final_r = round(net_profit / initial_risk, 2) if initial_risk > 0 else None
    direction = str(
        fallback.get("direction", _first_nonempty(unique, "direction", ""))
    ).upper()
    status = str(fallback.get("status", "")).strip()
    if not status:
        status = "closed" if closed else "open"

    return {
        **fallback,
        "position_id": str(position_id),
        "magic": str(_first_nonempty(unique, "magic", "")),
        "direction": direction,
        "open_time": str(fallback.get("open_time", open_time) or open_time),
        "close_time": str(fallback.get("close_time", close_time) or close_time),
        "entry_price": _float(
            fallback.get("entry_price", _first_nonempty(unique, "price", 0.0))
        ),
        "initial_volume": _float(
            fallback.get(
                "initial_volume", _first_nonempty(unique, "initial_volume", 0.0)
            )
        ),
        "initial_sl": _float(
            fallback.get("initial_sl", _first_nonempty(unique, "initial_sl", 0.0))
        ),
        "initial_risk": initial_risk,
        "tp1_price": _float(
            fallback.get("tp1_price", _first_nonempty(unique, "tp1_price", 0.0))
        ),
        "tp2_price": _float(
            fallback.get("tp2_price", _first_nonempty(unique, "tp2_price", 0.0))
        ),
        "stage_net": {key: round(value, 2) for key, value in stage_net.items()},
        "net_profit": net_profit,
        "whole_trade_net": net_profit,
        "final_r": final_r,
        "tp1_done": bool(stage_net["TP1"]) or any(
            _bool(row.get("tp1_done")) for row in unique
        ),
        "tp2_done": bool(stage_net["TP2"]) or any(
            _bool(row.get("tp2_done")) for row in unique
        ),
        "runner_done": bool(stage_net["RUNNER"]),
        "runner_active": any(_bool(row.get("runner_active")) for row in unique)
        and not closed,
        "close_reason": str(fallback.get("close_reason", close_reason) or close_reason),
        "status": status,
        "rows": unique,
    }


def trades_for_server_window(
    root: Path, server_open: str, server_close: str
) -> list[dict[str, object]]:
    start = _time(server_open)
    end = _time(server_close)
    if start is None or end is None or end < start:
        raise ValueError("invalid server review window")
    lifecycle = root / "Trade_Lifecycle"
    if not lifecycle.exists():
        return []

    output: list[dict[str, object]] = []
    for path in sorted(lifecycle.glob("Position_*.csv")):
        position_id = path.stem.removeprefix("Position_")
        rows = load_position_lifecycle(root, position_id)
        if not rows:
            continue
        trade = aggregate_position(position_id, rows, fallback={})
        open_dt = _time(trade.get("open_time"))
        close_dt = _time(trade.get("close_time"))
        in_window = [
            row
            for row in trade["rows"]
            if (row_time := _time(row.get("server_time"))) is not None
            and start <= row_time <= end
        ]
        active_at_close = open_dt is not None and open_dt <= end and (
            close_dt is None or close_dt > end
        )
        if not in_window and not active_at_close:
            continue

        today_net = round(sum(_row_net(row) for row in in_window), 2)
        if close_dt is not None and start <= close_dt <= end:
            status = "closed_cross_day" if open_dt is not None and open_dt < start else "closed"
        else:
            status = "open_at_day_end"
        trade.update(
            {
                "open_server": trade.get("open_time", ""),
                "close_server": trade.get("close_time", ""),
                "today_net": today_net,
                "whole_trade_net": trade["net_profit"],
                "status": status,
            }
        )
        output.append(trade)
    output.sort(key=lambda trade: str(trade.get("open_time", "")))
    return output


_MT5_DEAL_REASON_NAMES = {
    0: "DEAL_REASON_CLIENT",
    1: "DEAL_REASON_MOBILE",
    2: "DEAL_REASON_WEB",
    3: "DEAL_REASON_EXPERT",
    4: "DEAL_REASON_SL",
    5: "DEAL_REASON_TP",
    6: "DEAL_REASON_SO",
    7: "DEAL_REASON_ROLLOVER",
    8: "DEAL_REASON_VWAP",
    9: "DEAL_REASON_CLOSEBY",
}


def enrich_close_reason_from_mt5(
    trades: list[dict[str, object]],
    terminal_path: str,
    symbol: str = "XAUUSD.s",
) -> list[dict[str, object]]:
    """Override close_reason/exit_price/slippage from MT5 authoritative deals.

    The EA lifecycle CSV has been observed recording DEAL_REASON_CLIENT for a
    real stop-loss trigger, so MT5 ``history_deals_get`` is the authoritative
    source for the daily review. Never changes any live trading behavior.
    """
    if not trades:
        return trades
    try:
        import MetaTrader5 as mt5
    except Exception:
        return trades
    if not mt5.initialize(terminal_path, timeout=5000):
        return trades
    try:
        for trade in trades:
            position_id = str(trade.get("position_id") or "").strip()
            if not position_id or position_id == "0":
                continue
            try:
                deals = mt5.history_deals_get(position=int(position_id))
            except Exception:
                continue
            if not deals:
                continue
            out_deals = [d for d in deals if int(d.entry) == 1]  # DEAL_ENTRY_OUT
            if not out_deals:
                continue
            out = out_deals[-1]
            reason_int = int(out.reason)
            reason = _MT5_DEAL_REASON_NAMES.get(reason_int, f"DEAL_REASON_{reason_int}")
            exit_price = float(out.price)
            csv_reason = str(trade.get("close_reason") or "").strip()
            if csv_reason and csv_reason != reason:
                # 记录“数据一致性异常”，供日报按 MT5 正确展示的同时亮出上游错误。
                trade["data_inconsistency"] = (
                    f"平仓原因不一致：系统记录为 {csv_reason}，MT5真实为 {reason}"
                )
            trade["close_reason"] = reason
            trade["exit_price"] = exit_price
            direction = str(trade.get("direction") or "").upper()
            sl = _float(trade.get("initial_sl"))
            if sl > 0 and exit_price > 0:
                if direction == "BUY":
                    slippage = round(sl - exit_price, 2)
                elif direction == "SELL":
                    slippage = round(exit_price - sl, 2)
                else:
                    slippage = round(abs(exit_price - sl), 2)
                trade["sl_slippage"] = slippage
    finally:
        mt5.shutdown()
    return trades


def _bar_epoch_text(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _mfe_mae_from_bars(
    rates,
    direction: str,
    entry: float,
    risk: float,
) -> dict[str, Any]:
    """Return MFE/MAE R + price + bar-open time from MT5 OHLC bars."""
    highs = [float(r[2]) for r in rates]
    lows = [float(r[3]) for r in rates]
    times = [float(r[0]) for r in rates]
    if direction == "BUY":
        mfe_idx = max(range(len(highs)), key=lambda i: highs[i])
        mae_idx = min(range(len(lows)), key=lambda i: lows[i])
        mfe_price = highs[mfe_idx]
        mae_price = lows[mae_idx]
        mfe = (mfe_price - entry) / risk
        mae = (mae_price - entry) / risk
    elif direction == "SELL":
        mfe_idx = min(range(len(lows)), key=lambda i: lows[i])
        mae_idx = max(range(len(highs)), key=lambda i: highs[i])
        mfe_price = lows[mfe_idx]
        mae_price = highs[mae_idx]
        mfe = (entry - mfe_price) / risk
        mae = (entry - mae_price) / risk
    else:
        return {}
    return {
        "mfe_r": round(max(mfe, 0.0), 4),
        "mae_r": round(min(mae, 0.0), 4),
        "mfe_price": round(mfe_price, 2),
        "mae_price": round(mae_price, 2),
        "mfe_time": _bar_epoch_text(times[mfe_idx]),
        "mae_time": _bar_epoch_text(times[mae_idx]),
    }


def _mfe_mae_from_ticks(
    ticks,
    direction: str,
    entry: float,
    risk: float,
) -> dict[str, Any]:
    """Return MFE/MAE R + price + exact tick time from MT5 tick rows."""
    rows = [
        (float(t["time"]), float(t["bid"] or 0), float(t["ask"] or 0))
        for t in ticks
        if float(t["bid"] or 0) > 0 and float(t["ask"] or 0) > 0
    ]
    if not rows:
        return {}
    if direction == "BUY":
        prices = [r[1] for r in rows]
    elif direction == "SELL":
        prices = [r[2] for r in rows]
    else:
        return {}
    mfe_idx = max(range(len(prices)), key=lambda i: prices[i]) if direction == "BUY" else min(range(len(prices)), key=lambda i: prices[i])
    mae_idx = min(range(len(prices)), key=lambda i: prices[i]) if direction == "BUY" else max(range(len(prices)), key=lambda i: prices[i])
    mfe_price = prices[mfe_idx]
    mae_price = prices[mae_idx]
    if direction == "BUY":
        mfe = (mfe_price - entry) / risk
        mae = (mae_price - entry) / risk
    else:
        mfe = (entry - mfe_price) / risk
        mae = (entry - mae_price) / risk
    return {
        "mfe_r": round(max(mfe, 0.0), 4),
        "mae_r": round(min(mae, 0.0), 4),
        "mfe_price": round(mfe_price, 2),
        "mae_price": round(mae_price, 2),
        "mfe_time": _bar_epoch_text(rows[mfe_idx][0]),
        "mae_time": _bar_epoch_text(rows[mae_idx][0]),
    }


def enrich_trade_mfe_mae(
    trades: list[dict[str, object]],
    terminal_path: str,
    symbol: str = "XAUUSD.s",
) -> list[dict[str, object]]:
    """给已成交交易补“开仓后最大浮盈 / 最大浮亏”（R），只用于复盘。

    时间范围严格限定在实际开仓时间 → 实际最终平仓时间，绝不含平仓后的未来行情。
    回退链：M5 → M1 → Tick。M5/M1 的 MFE/MAE 时间取对应 bar 的开盘时间，
    只有 Tick 才是精确逐笔时间；无法可靠确定时只标记“数据不足”，不猜时间。
    """
    if not trades:
        return trades
    try:
        import MetaTrader5 as mt5
    except Exception:
        return trades
    if not mt5.initialize(terminal_path, timeout=5000):
        return trades
    try:
        for trade in trades:
            trade["mfe_r"] = None
            trade["mae_r"] = None
            trade["mfe_price"] = None
            trade["mae_price"] = None
            trade["mfe_time"] = None
            trade["mae_time"] = None
            trade["mfe_source"] = "数据不足"
            trade["mae_source"] = "数据不足"
            if not str(trade.get("status") or "").startswith("closed"):
                continue
            entry = _float(trade.get("entry_price"))
            sl = _float(trade.get("initial_sl"))
            direction = str(trade.get("direction") or "").upper()
            # 交易生命周期里的 open/close 时间与 MT5 Deal 时间一致，均为 UTC。
            open_dt = _time(trade.get("open_time"))
            close_dt = _time(trade.get("close_time"))
            if open_dt is not None:
                open_dt = open_dt.replace(tzinfo=timezone.utc)
            if close_dt is not None:
                close_dt = close_dt.replace(tzinfo=timezone.utc)
            if entry <= 0 or sl <= 0 or not open_dt or not close_dt or close_dt <= open_dt:
                continue
            risk = abs(entry - sl)
            if risk <= 0:
                continue

            same_m5_bar = (
                int(open_dt.timestamp() // 300) == int(close_dt.timestamp() // 300)
            )
            computed: dict[str, Any] = {}
            source = ""
            if not same_m5_bar:
                rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, open_dt, close_dt)
                if rates is not None and len(rates) > 0:
                    computed = _mfe_mae_from_bars(rates, direction, entry, risk)
                    source = "M5"
            if not computed:
                rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, open_dt, close_dt)
                if rates is not None and len(rates) > 0:
                    computed = _mfe_mae_from_bars(rates, direction, entry, risk)
                    source = "M1"
            if not computed:
                ticks = mt5.copy_ticks_range(symbol, open_dt, close_dt, mt5.COPY_TICKS_ALL)
                if ticks is not None and len(ticks) > 0:
                    computed = _mfe_mae_from_ticks(ticks, direction, entry, risk)
                    source = "Tick" if computed else "数据不足"
            if computed:
                trade["mfe_r"] = computed["mfe_r"]
                trade["mae_r"] = computed["mae_r"]
                trade["mfe_price"] = computed["mfe_price"]
                trade["mae_price"] = computed["mae_price"]
                trade["mfe_time"] = computed["mfe_time"]
                trade["mae_time"] = computed["mae_time"]
                trade["mfe_source"] = source
                trade["mae_source"] = source
    finally:
        mt5.shutdown()
    return trades


def validate_commentary(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"reason", "comment"}:
        raise ValueError("commentary requires exact reason/comment fields")
    reason = str(value["reason"]).strip()
    comment = str(value["comment"]).strip()
    if not reason or not comment or len(reason) > 180 or len(comment) > 100:
        raise ValueError("commentary text length invalid")
    return {"reason": reason, "comment": comment}


def fallback_commentary(
    notification_type: str, trade: dict[str, object]
) -> dict[str, str]:
    direction = str(trade.get("direction", "")).upper()
    direction_text = "上涨" if direction == "BUY" else "下跌" if direction == "SELL" else "当前"
    if notification_type == "open_trade_facts":
        conditions: list[str] = [f"价格整体处于{direction_text}方向"]
        fib = _float(trade.get("fib_retracement"))
        if fib > 0:
            conditions.append(f"回调接近Fib {fib:.1f}%区域")
        if trade.get("sr_confluence"):
            conditions.append("附近存在支撑或阻力配合")
        if trade.get("ma_confluence"):
            conditions.append("均线方向形成配合")
        if trade.get("pattern"):
            conditions.append("出现反转形态确认")
        return {
            "reason": "，".join(conditions[:4]) + "。",
            "comment": "当前条件满足后，EA按既定规则执行开仓。",
        }

    net = _float(trade.get("net_profit", trade.get("whole_trade_net")))
    close_reason = str(trade.get("close_reason", "")).strip() or "既定退出规则"
    result = "盈利" if net > 0 else "亏损" if net < 0 else "基本持平"
    return {
        "reason": f"仓位通过{close_reason}全部结束，整笔交易最终{result}。",
        "comment": f"本单已完成结算，累计净盈亏为{net:+.2f} USD。",
    }


def _display_time(value: object) -> str:
    parsed = _time(value)
    return parsed.strftime("%m-%d %H:%M") if parsed else "—"


def _money(value: Any) -> str:
    return f"{_float(value):+.2f}"


def _price(value: Any) -> str:
    number = _float(value)
    return f"{number:.2f}" if number > 0 else "—"


def _stage_line(name: str, done: bool, value: float, inactive: str) -> str:
    return f"{name}：✅ ${_money(value)}" if done else f"{name}：❌ {inactive}"


def render_trade_card(
    notification_type: str,
    trade: dict[str, object],
    commentary: dict[str, str],
) -> dict[str, object]:
    position_id = str(trade.get("position_id", ""))
    direction = str(trade.get("direction", "")).upper() or "—"
    if notification_type == "open_trade_facts":
        source_label = "AI" if str(trade.get("magic", "")).strip() == "2026072902" else ""
        title = f"🔵 {source_label}交易#{position_id}｜{direction}｜开仓"
        risk = _float(trade.get("initial_risk"))
        risk_text = f"${risk:.2f}" if risk > 0 else "无法计算"
        markdown = (
            f"**开仓通知**\n\n"
            f"**时间：** {_display_time(trade.get('open_time'))}\n"
            f"**成交：** {_price(trade.get('entry_price'))}｜{_float(trade.get('initial_volume')):.2f}手\n"
            f"**止损：** {_price(trade.get('initial_sl'))}｜风险 {risk_text}\n"
            f"**目标：** {_price(trade.get('tp1_price'))}（1R）｜{_price(trade.get('tp2_price'))}（2R）\n\n"
            f"**开仓理由：**\n{commentary['reason']}\n\n"
            f"**简评：** {commentary['comment']}\n\n"
            f"**🤖 AI：** {_ai_status_text(trade)}｜"
            f"{int(_float(trade.get('ai_confidence')))}分"
        )
        theme = "blue"
    else:
        net = _float(trade.get("net_profit", trade.get("whole_trade_net")))
        icon = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        theme = "green" if net > 0 else "red" if net < 0 else "grey"
        source_label = "AI" if str(trade.get("magic", "")).strip() == "2026072902" else ""
        title = f"{icon} {source_label}交易#{position_id}｜{direction}｜最终结算"
        stages = dict(trade.get("stage_net") or {})
        final_r = trade.get("final_r")
        r_text = f"{_float(final_r):+.2f}R" if final_r is not None else "无法计算"
        markdown = (
            f"**最终结算**\n\n"
            f"**时间：** {_display_time(trade.get('close_time'))}\n"
            f"**入场：** {_price(trade.get('entry_price'))}｜{_float(trade.get('initial_volume')):.2f}手\n"
            f"**止损：** {_price(trade.get('initial_sl'))}\n"
            f"**目标：** {_price(trade.get('tp1_price'))}（1R）｜{_price(trade.get('tp2_price'))}（2R）\n\n"
            f"{_stage_line('TP1', bool(trade.get('tp1_done')), _float(stages.get('TP1')), '未触发')}\n"
            f"{_stage_line('TP2', bool(trade.get('tp2_done')), _float(stages.get('TP2')), '未触发')}\n"
            f"{_stage_line('Runner', bool(trade.get('runner_done')), _float(stages.get('RUNNER')), '未启动')}\n"
            + (f"**其他退出：** ${_money(stages.get('OTHER'))}\n" if abs(_float(stages.get('OTHER'))) >= 0.005 else "")
            + f"\n**总净盈亏：** {icon} ${_money(net)}\n"
            f"**最终R：{r_text}**\n"
            f"**结束说明：** {commentary['reason']}\n"
            f"**简评：** {commentary['comment']}\n\n"
            f"**本单结束：✅**"
        )

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": theme,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {"elements": [{"tag": "markdown", "content": markdown}]},
        },
    }


# UTF-8 notification templates. These definitions intentionally replace the
# earlier migration templates, some of which were imported from a mojibake file.
def _open_route_label(route: str, direction: str) -> str:
    if route == "FIB_PA":
        return "Fib + PA 路径"
    if route == "EMA_H23":
        return "EMA H2/H3 路径"
    if route == "EMA_L23":
        return "EMA L2/L3 路径"
    if route == "BOTH":
        ema_label = "EMA H2/H3" if direction == "BUY" else "EMA L2/L3"
        return f"Fib + PA 与 {ema_label} 双路径"
    return "已通过EA候选条件（路径记录缺失）"


def _open_pattern_text(value: object) -> str:
    raw = str(value or "").strip().lower()
    patterns: list[str] = []
    if "pin bar" in raw or "pinbar" in raw:
        patterns.append("针杆")
    if "hammer" in raw:
        patterns.append("锤子")
    if "engulf" in raw:
        patterns.append("吞没")
    if "strong reversal" in raw:
        patterns.append("强反转")
    if not patterns and raw:
        patterns.append(str(value).strip())
    return "与".join(patterns)


def build_open_trade_explanation(
    trade: dict[str, object],
) -> dict[str, str]:
    """Build open-card wording from structured trade facts only."""
    direction = str(trade.get("direction", "")).strip().upper()
    direction_text = direction if direction in {"BUY", "SELL"} else "当前"
    route = str(trade.get("signal_route", "")).strip().upper()
    route_label = _open_route_label(route, direction)
    if str(trade.get("magic", "")).strip() == "2026072902":
        ai_reason = str(trade.get("ai_reason", "")).strip()
        if ai_reason:
            return {"route": route_label, "reason": ai_reason, "fallback_comment": ai_reason}
    reason_parts: list[str] = []

    if bool(trade.get("context_loaded")):
        reason_parts.append(f"{direction_text}方向的趋势与结构前置条件已通过")
    else:
        reason_parts.append(f"EA已生成{direction_text}方向候选")

    fib = _float(trade.get("fib_retracement"))
    has_fib_route = route in {"FIB_PA", "BOTH"}
    if has_fib_route and fib > 0:
        reason_parts.append(f"Fib回调{fib:.1f}%处于有效区域")

    has_ema_route = route in {"EMA_H23", "EMA_L23", "BOTH"}
    ema_distance = _float(trade.get("ema_distance_usd"))
    if has_ema_route and (ema_distance >= 0 or bool(trade.get("ma_confluence"))):
        ema_text = "价格位于EMA20附近"
        attempt = int(_float(trade.get("h_attempt")))
        if attempt in {2, 3}:
            prefix = "H" if direction == "BUY" else "L"
            ema_text += f"并形成{prefix}{attempt}"
        pattern = _open_pattern_text(trade.get("pattern"))
        if pattern:
            ema_text += f"、{pattern}确认"
        if has_fib_route and fib > 0:
            ema_text = "同时" + ema_text
        reason_parts.append(ema_text)
    elif has_fib_route:
        pattern = _open_pattern_text(trade.get("pattern"))
        if pattern:
            reason_parts.append(f"回调后出现{pattern}确认")

    if all(
        _float(trade.get(field)) > 0
        for field in ("initial_sl", "tp1_price", "tp2_price")
    ):
        reason_parts.append("止损及1R/2R目标完整")
    if bool(trade.get("ai_is_error")):
        reason_parts.append("DeepSeek审核异常，按规则降级放行，因此成交")
    elif bool(trade.get("ai_allow_trade")):
        reason_parts.append("DeepSeek审核允许，因此成交")

    risk = _float(trade.get("initial_risk"))
    action = "做多" if direction == "BUY" else "做空" if direction == "SELL" else "开仓"
    route_comment = "双路径共同确认" if route == "BOTH" else route_label.replace(" 路径", "确认")
    if risk > 0:
        fallback_comment = (
            f"{route_comment}后{action}，条件较完整；初始风险{risk:.2f} USD，"
            "第一目标1R，第二目标2R。"
        )
    else:
        fallback_comment = f"{route_comment}后{action}，止损和目标已按候选计划设置。"

    return {
        "route": route_label,
        "reason": "；".join(reason_parts) + "。",
        "fallback_comment": fallback_comment,
    }


def fallback_commentary(
    notification_type: str, trade: dict[str, object]
) -> dict[str, str]:
    if notification_type == "open_trade_facts":
        explanation = build_open_trade_explanation(trade)
        return {
            "reason": explanation["reason"],
            "comment": explanation["fallback_comment"],
        }

    direction = str(trade.get("direction", "")).upper()
    net = _float(trade.get("net_profit", trade.get("whole_trade_net")))
    close_reason = str(trade.get("close_reason", "")).strip() or "既定退出规则"
    result = "盈利" if net > 0 else "亏损" if net < 0 else "基本持平"
    return {
        "reason": f"仓位通过{close_reason}全部结束，整笔交易最终{result}。",
        "comment": f"本单已经完成结算，累计净盈亏为 {net:+.2f} USD。",
    }


def _display_time(value: object) -> str:
    parsed = _time(value)
    return parsed.strftime("%m-%d %H:%M") if parsed else "—"


def _money(value: Any) -> str:
    return f"{_float(value):+.2f}"


def _price(value: Any) -> str:
    number = _float(value)
    return f"{number:.2f}" if number > 0 else "—"


def _stage_line(name: str, done: bool, value: float, inactive: str) -> str:
    return f"{name}：✅ ${_money(value)}" if done else f"{name}：❌ {inactive}"


def render_trade_card(
    notification_type: str,
    trade: dict[str, object],
    commentary: dict[str, str],
) -> dict[str, object]:
    position_id = str(trade.get("position_id", ""))
    direction = str(trade.get("direction", "")).upper() or "—"
    if notification_type == "open_trade_facts":
        source_label = "AI" if str(trade.get("magic", "")).strip() == "2026072902" else ""
        title = f"🔵 {source_label}交易#{position_id}｜{direction}｜开仓"
        risk = _float(trade.get("initial_risk"))
        explanation = build_open_trade_explanation(trade)
        if risk > 0:
            risk_text = f"初始风险 {risk:.2f} USD（1R）"
            tp1_r_text = f"1R ≈ {risk:.2f} USD"
            tp2_r_text = f"2R ≈ {risk * 2:.2f} USD"
        else:
            risk_text = "初始风险无法计算"
            tp1_r_text = "1R"
            tp2_r_text = "2R"
        markdown = (
            "**开仓通知**\n\n"
            f"**时间：** {_display_time(trade.get('open_time'))}\n"
            f"**成交：** {_price(trade.get('entry_price'))}｜{_float(trade.get('initial_volume')):.2f}手\n"
            f"**止损：** {_price(trade.get('initial_sl'))}｜{risk_text}\n"
            "**目标：**\n"
            f"TP1：{_price(trade.get('tp1_price'))}｜{tp1_r_text}\n"
            f"TP2：{_price(trade.get('tp2_price'))}｜{tp2_r_text}\n\n"
            f"**入场路径：** {explanation['route']}\n\n"
            f"**开仓理由：**\n{explanation['reason']}\n\n"
            f"**简评：** {commentary['comment']}\n\n"
            f"**🤖 AI：** {_ai_status_text(trade)}｜{int(_float(trade.get('ai_confidence')))}分"
        )
        theme = "blue"
    else:
        net = _float(trade.get("net_profit", trade.get("whole_trade_net")))
        icon = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
        theme = "green" if net > 0 else "red" if net < 0 else "grey"
        source_label = "AI" if str(trade.get("magic", "")).strip() == "2026072902" else ""
        title = f"{icon} {source_label}交易#{position_id}｜{direction}｜最终结算"
        stages = dict(trade.get("stage_net") or {})
        final_r = trade.get("final_r")
        r_text = f"{_float(final_r):+.2f}R" if final_r is not None else "无法计算"
        markdown = (
            "**最终结算**\n\n"
            f"**时间：** {_display_time(trade.get('close_time'))}\n"
            f"**入场：** {_price(trade.get('entry_price'))}｜{_float(trade.get('initial_volume')):.2f}手\n\n"
            f"{_stage_line('TP1', bool(trade.get('tp1_done')), _float(stages.get('TP1')), '未触发')}\n"
            f"{_stage_line('TP2', bool(trade.get('tp2_done')), _float(stages.get('TP2')), '未触发')}\n"
            f"{_stage_line('Runner', bool(trade.get('runner_done')), _float(stages.get('RUNNER')), '未启动')}\n"
            + (f"**其他退出：** ${_money(stages.get('OTHER'))}\n" if abs(_float(stages.get('OTHER'))) >= 0.005 else "")
            + f"\n**总净盈亏：** {icon} ${_money(net)}\n"
            f"**最终R：{r_text}**\n"
            f"**平仓理由：** {commentary['reason']}\n"
            f"**简评：** {commentary['comment']}\n\n"
            "**本单结束：✅**"
        )

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": theme,
                "title": {"tag": "plain_text", "content": title},
            },
            "body": {"elements": [{"tag": "markdown", "content": markdown}]},
        },
    }
