from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "XAUUSD_M5_AI_Pullback_V3_8_AI_MONITOR.mq5"
SINGLE = ROOT / "XAUUSD_M5_AI_Pullback_V3_8_AI_MONITOR_SINGLE.mq5"
INCLUDE_DIR = ROOT / "Include" / "XAUAI"
LOCAL_INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"\s*$', re.MULTILINE)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_comments_and_strings(text: str) -> str:
    out: list[str] = []
    i = 0
    state = "code"
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line_comment"
                out.extend("  ")
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                out.extend("  ")
                i += 2
                continue
            if ch == '"':
                state = "string"
                out.append(" ")
                i += 1
                continue
            if ch == "'":
                state = "char"
                out.append(" ")
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if state == "line_comment":
            if ch == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                out.extend("  ")
                i += 2
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
            continue
        if state in {"string", "char"}:
            if ch == "\\" and nxt:
                out.extend("  ")
                i += 2
                continue
            terminator = '"' if state == "string" else "'"
            if ch == terminator:
                state = "code"
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
    if state in {"string", "char", "block_comment"}:
        raise AssertionError(f"unterminated lexical state: {state}")
    return "".join(out)


def assert_balanced(path: Path) -> None:
    text = strip_comments_and_strings(path.read_text(encoding="utf-8"))
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in pairs.items()}
    stack: list[tuple[str, int]] = []
    for index, ch in enumerate(text):
        if ch in pairs:
            stack.append((ch, index))
        elif ch in closing:
            assert stack, f"{path.name}: unmatched {ch} at {index}"
            opener, opener_index = stack.pop()
            assert opener == closing[ch], (
                f"{path.name}: {opener} at {opener_index} closed by {ch} at {index}"
            )
    assert not stack, f"{path.name}: unclosed delimiters: {stack[-5:]}"


def assert_preprocessor_balanced(path: Path) -> None:
    depth = 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if token in {"#if", "#ifdef", "#ifndef"}:
            depth += 1
        elif token == "#endif":
            depth -= 1
            assert depth >= 0, f"{path.name}: extra #endif at line {lineno}"
    assert depth == 0, f"{path.name}: preprocessor depth={depth}"


def assert_local_includes_exist(path: Path) -> None:
    for match in LOCAL_INCLUDE_RE.finditer(path.read_text(encoding="utf-8")):
        child = (path.parent / match.group(1)).resolve()
        assert child.exists(), f"{path.name}: missing include {match.group(1)}"


def main() -> None:
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_single_file.py")], check=True)

    assert MAIN.exists()
    assert SINGLE.exists()
    modules = sorted(INCLUDE_DIR.glob("*.mqh"))
    assert len(modules) == 18, f"expected 18 modules, got {len(modules)}"

    for path in [MAIN, SINGLE, *modules]:
        assert_balanced(path)
        assert_preprocessor_balanced(path)
    assert_local_includes_exist(MAIN)
    for module in modules:
        assert_local_includes_exist(module)

    main_text = MAIN.read_text(encoding="utf-8")
    single_text = SINGLE.read_text(encoding="utf-8")
    trade_text = (INCLUDE_DIR / "TradeManager.mqh").read_text(encoding="utf-8")

    assert '#property version "3.80"' in main_text
    assert "V3.6" not in main_text
    assert "InpSignalTimeframe" not in main_text
    assert "input ENUM_TIMEFRAMES" not in main_text
    assert "PERIOD_M1" not in main_text
    assert "g_signal_timeframe=PERIOD_M5" in main_text
    assert "本EA只能在M5" in main_text
    assert '#include "' not in single_text
    assert single_text.count("#include <Trade/Trade.mqh>") == 1
    assert single_text.count("void OnTick()") == 1
    assert single_text.count("void OnTradeTransaction(") == 1
    assert "TRADE_TRANSACTION_ORDER_DELETE" in main_text
    assert "g_trades.HandleOrderRemoved(trans.order,trans.order_state)" in main_text
    assert "if(!OrderSelect(ticket))" in trade_text
    assert "HistoryOrderSelect(ticket)" in trade_text
    assert "ORDER_STATE_EXPIRED" in trade_text
    assert "ORDER_STATE_CANCELED" in trade_text
    assert "ResetTradeRuntimeState(m_state);\n      SaveState();\n      return false;" in trade_text
    weekend_text = (INCLUDE_DIR / "WeekendRiskGuard.mqh").read_text(encoding="utf-8")
    assert "SymbolInfoSessionTrade" in weekend_text
    assert "EvaluateWeekendGuard" in main_text
    assert "CancelAllManagedPending" in trade_text
    assert "CloseAllManagedPositions" in trade_text
    assert "InpWeekendNoNewTradeMinutes=180" in main_text
    assert "InpWeekendForceCloseMinutes=60" in main_text
    rollover_text = (INCLUDE_DIR / "RolloverRiskGuard.mqh").read_text(encoding="utf-8")
    assert "struct RolloverGuardStatus" in rollover_text
    assert "IsMinuteInsideRolloverWindow" in rollover_text
    assert "minute_of_day>=start_minutes || minute_of_day<end_minutes" in rollover_text
    assert "InpRolloverGuardEnabled=true" in main_text
    assert "InpRolloverStartHour=23" in main_text
    assert "InpRolloverStartMinute=30" in main_text
    assert "InpRolloverEndHour=1" in main_text
    assert "InpRolloverEndMinute=30" in main_text
    assert main_text.count("EvaluateRolloverGuard") >= 3
    assert "pre_order_rollover_guard" in main_text
    assert "[换日风控] 已进入禁止新开仓窗口，挂单已删除" in main_text
    rollover_block = main_text.split(
        "if(InpRolloverGuardEnabled && rollover_guard.block_new_entries", 1
    )[1].split("const double bid", 1)[0]
    assert "CancelAllManagedPending" in rollover_block
    assert "CloseAllManagedPositions" not in rollover_block
    assert "struct RolloverGuardStatus" in single_text

    observation_text = (INCLUDE_DIR / "ObservationLogger.mqh").read_text(encoding="utf-8")
    calendar_text = (INCLUDE_DIR / "EconomicCalendarLogger.mqh").read_text(encoding="utf-8")
    assert 'input group "【AI盯盘数据采集】"' in main_text
    assert "InpObservationLoggingEnabled=true" in main_text
    assert "FILE_COMMON" in observation_text
    assert all(tf in observation_text for tf in ("PERIOD_M5", "PERIOD_M15", "PERIOD_H1", "PERIOD_H4"))
    assert "CalendarValueHistory" in calendar_text
    assert 'NULL,"USD"' in calendar_text.replace(" ", "")
    assert "CALENDAR_IMPORTANCE_HIGH" in calendar_text
    assert "g_observation.WriteMarketSnapshot" in main_text
    assert '"candidate"' in main_text
    assert '"ai_reject"' in main_text
    assert '"pending_filled"' in main_text
    assert '"position_closed"' in main_text
    assert "class CObservationLogger" in single_text
    assert "class CEconomicCalendarLogger" in single_text

    before = sha256(SINGLE)
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_single_file.py")], check=True)
    after = sha256(SINGLE)
    assert before == after, "single-file generation is not deterministic"

    print("release validation: PASS")
    print(f"main sha256:   {sha256(MAIN)}")
    print(f"single sha256: {sha256(SINGLE)}")
    print(f"modules: {len(modules)}")


if __name__ == "__main__":
    main()
