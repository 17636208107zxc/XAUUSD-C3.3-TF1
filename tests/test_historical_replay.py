from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.historical_replay import (
    CachedDeepSeekClient,
    build_snapshot,
    simulate_stop_plan,
)


def _bars(count=210, base=4300.0):
    start = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)
    out = []
    for i in range(count):
        ts = int((start + timedelta(minutes=5 * i)).timestamp())
        out.append({"time": ts, "open": base, "high": base + 1.0,
                    "low": base - 1.0, "close": base, "tick_volume": 100, "spread": 10})
    return out


def test_snapshot_has_no_future_lookahead():
    bars = _bars()
    index = 205
    snap = build_snapshot(bars, index)
    assert len(snap["bars"]) == 200
    # Last bar must equal the decision bar, never a later bar.
    assert snap["bars"][-1]["time"] == datetime.fromtimestamp(bars[index]["time"], tz=timezone.utc).strftime("%Y.%m.%d %H:%M:%S")
    # No future bar is present.
    future_ts = bars[index + 1]["time"]
    future_str = datetime.fromtimestamp(future_ts, tz=timezone.utc).strftime("%Y.%m.%d %H:%M:%S")
    assert all(bar["time"] != future_str for bar in snap["bars"])


def test_simulate_stop_plan_not_triggered_expires():
    bars = _bars()
    decision = {"direction": "SELL", "entry": 4295.0, "sl": 4305.0, "tp1": 4290.0, "tp2": 4285.0}
    # SELL_STOP entry 4295 is below the flat market 4300; no bar drops to it.
    outcome = simulate_stop_plan(decision, bars, None, 205)
    assert outcome["status"] == "NOT_TRIGGERED"
    assert outcome["exit"] == "EXPIRED"


def test_simulate_stop_plan_sell_fills_and_hits_sl():
    bars = _bars()
    # Force future bars to drop into entry and then bounce above SL.
    for offset, high, low in ((1, 4295.0, 4294.0), (2, 4306.0, 4295.0)):
        b = bars[205 + offset]
        b["high"] = high
        b["low"] = low
    decision = {"direction": "SELL", "entry": 4295.0, "sl": 4305.0, "tp1": 4290.0, "tp2": 4285.0}
    outcome = simulate_stop_plan(decision, bars, None, 205)
    assert outcome["status"] == "FILLED"
    assert outcome["exit"] == "SL"
    assert outcome["net_r"] == -1.0


def test_cached_client_reuses_response(tmp_path):
    class Raw:
        def call(self, prompt, payload, max_tokens):
            return {"action": "WAIT"}, {"model": "fake"}

    client = CachedDeepSeekClient(Raw(), tmp_path, "deepseek-v4-flash", "promptsha")
    value, _ = client.call("p", {"market": {}}, 100)
    assert value == {"action": "WAIT"}
    assert client.misses == 1
    client.call("p", {"market": {}}, 100)
    assert client.hits == 1
