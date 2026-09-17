from tools.ai_trade_manager import (
    _align,
    _normalize_volume,
    _position_close_facts,
    _trade_group_for_magic,
    append_ai_lifecycle_event,
    calc_volume,
    collect_trade_groups,
    collect_position_ownership,
    compute_minimum_pending_distance,
    load_plans,
    place_ai_pending,
    precheck_passed,
    queue_ai_open_notification,
    save_plan,
    validate_stop_plan,
)


def test_align_rounds_to_tick():
    assert _align(4317.827, 0.01) == 4317.83
    assert _align(4317.823, 0.01) == 4317.82


def test_normalize_volume_respects_min_and_step():
    assert _normalize_volume(0.2715, 0.01, 0.01) == 0.27
    assert _normalize_volume(0.004, 0.01, 0.01) == 0.01
    assert _normalize_volume(0.509, 0.01, 0.01) == 0.50


def test_calc_volume_matches_ea_risk_style():
    # 0.5% of ~10316 balance, SL distance 1.90, tick 0.01, tick value 1.0
    volume = calc_volume(4349.79, 4351.69, 10316.55, 0.5, 0.01, 1.0)
    assert 0.25 < volume < 0.30
    # hard cap at 1%
    capped = calc_volume(4349.79, 4351.69, 1000.0, 10.0, 0.01, 1.0)
    assert capped <= calc_volume(4349.79, 4351.69, 1000.0, 1.0, 0.01, 1.0) + 1e-9


def test_plan_roundtrip(tmp_path):
    plan = {
        "signal_id": "XAUUSD.s_M5_20260814_0700",
        "direction": "SELL",
        "entry": 4325.04,
        "sl": 4322.46,
        "tp1": 4327.62,
        "tp2": 4330.20,
        "initial_volume": 0.20,
        "state": "pending",
        "tp1_done": False,
        "tp2_done": False,
    }
    save_plan(tmp_path, plan)
    loaded = load_plans(tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["signal_id"] == plan["signal_id"]
    assert loaded[0]["entry"] == 4325.04


def test_precheck_passed_accepts_order_check_success_codes():
    from types import SimpleNamespace

    assert precheck_passed(SimpleNamespace(retcode=0)) is True
    assert precheck_passed(SimpleNamespace(retcode=10009)) is True
    assert precheck_passed(SimpleNamespace(retcode=10015)) is False
    assert precheck_passed(None) is False
    assert precheck_passed(SimpleNamespace(retcode="not-a-number")) is False


def test_compute_minimum_pending_distance():
    # 50 points * 0.01 = 0.50; never below one point.
    assert compute_minimum_pending_distance(50, 0.01) == 0.50
    assert compute_minimum_pending_distance(0, 0.01) == 0.01


def test_validate_stop_plan_sell_matrix():
    # SELL_STOP entry must be strictly below Bid by at least 0.50.
    assert validate_stop_plan({"direction": "SELL", "entry": 100.0}, 101.0, 101.2, 0.01, 50)["status"] == "VALID"
    assert validate_stop_plan({"direction": "SELL", "entry": 101.0}, 101.0, 101.2, 0.01, 50)["reason"] == "SELL_STOP_ENTRY_WRONG_SIDE"
    assert validate_stop_plan({"direction": "SELL", "entry": 101.5}, 101.0, 101.2, 0.01, 50)["reason"] == "SELL_STOP_ENTRY_WRONG_SIDE"
    assert validate_stop_plan({"direction": "SELL", "entry": 100.7}, 101.0, 101.2, 0.01, 50)["reason"] == "SELL_STOP_ENTRY_TOO_CLOSE"


def test_validate_stop_plan_buy_matrix():
    # BUY_STOP entry must be strictly above Ask by at least 0.50.
    assert validate_stop_plan({"direction": "BUY", "entry": 102.0}, 100.0, 101.0, 0.01, 50)["status"] == "VALID"
    assert validate_stop_plan({"direction": "BUY", "entry": 101.0}, 100.0, 101.0, 0.01, 50)["reason"] == "BUY_STOP_ENTRY_WRONG_SIDE"
    assert validate_stop_plan({"direction": "BUY", "entry": 100.5}, 100.0, 101.0, 0.01, 50)["reason"] == "BUY_STOP_ENTRY_WRONG_SIDE"
    assert validate_stop_plan({"direction": "BUY", "entry": 101.3}, 100.0, 101.0, 0.01, 50)["reason"] == "BUY_STOP_ENTRY_TOO_CLOSE"


def test_append_ai_lifecycle_event_writes_magic(tmp_path):
    append_ai_lifecycle_event(tmp_path, "740111524", {
        "event_id": "740111524_OPEN", "server_time": "2026.08.18 20:41:23",
        "event_kind": "OPEN", "stage": "OPEN", "position_id": "740111524",
        "direction": "SELL", "volume": "0.1", "price": "4362.86",
        "initial_volume": "0.1", "initial_sl": "4367.61", "initial_risk": "47.50",
        "tp1_price": "4358.75", "tp2_price": "4354.85",
    })
    import csv
    path = tmp_path / "Trade_Lifecycle" / "Position_740111524.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
    assert len(rows) == 1
    assert rows[0]["magic"] == "2026072902"
    assert rows[0]["direction"] == "SELL"


def test_queue_ai_open_notification_writes_outbox_item(tmp_path):
    from types import SimpleNamespace

    position = SimpleNamespace(ticket=740111524, time=1787114483, volume=0.1)
    plan = {
        "signal_id": "XAUUSD.s_M5_20260818_2035", "direction": "SELL", "route": "FIB_PA",
        "entry": 4362.86, "sl": 4367.61, "tp1": 4358.75, "tp2": 4354.85,
        "initial_volume": 0.1, "confidence": 72, "reason": "结构看跌",
    }
    queue_ai_open_notification(tmp_path, plan, position, mt5=None)
    import json
    path = tmp_path / "Lark_Outbox" / "Pending" / "PARALLEL_AI_OPEN_740111524.json"
    item = json.loads(path.read_text(encoding="utf-8"))
    assert item["notification_type"] == "open_trade_facts"
    assert item["position_id"] == "740111524"
    assert item["facts"]["direction"] == "SELL"
    assert item["facts"]["entry_price"] == 4362.86


def _fake_mt5(check_retcode=None, check_none=False, send_retcode=10009,
              send_order=12345, send_comment=""):
    import types

    mt5 = types.ModuleType("MetaTrader5")
    mt5.TRADE_RETCODE_DONE = 10009
    mt5.TRADE_RETCODE_INVALID_PRICE = 10015
    mt5.ORDER_FILLING_RETURN = 0
    mt5.ORDER_TYPE_BUY_STOP = 0
    mt5.ORDER_TYPE_SELL_STOP = 1
    mt5.TRADE_ACTION_PENDING = 5
    mt5.ORDER_TIME_SPECIFIED = 1
    mt5.ORDER_STATE_FILLED = 2
    mt5.initialize = lambda *args, **kwargs: True
    mt5.shutdown = lambda: None
    mt5.last_error = lambda: (0, "Success")

    class _Info:
        balance = 10000.0
    mt5.account_info = lambda: _Info()

    class _SymbolInfo:
        trade_tick_size = 0.01
        trade_tick_value = 1.0
        volume_step = 0.01
        volume_min = 0.01
    mt5.symbol_info = lambda symbol: _SymbolInfo()

    class _Tick:
        bid = 4360.0
        ask = 4360.2
        time = 1700000000
    mt5.symbol_info_tick = lambda symbol: _Tick()

    class _Check:
        def __init__(self, retcode):
            self.retcode = retcode
            self.comment = "Done" if retcode in (0, 10009) else "Invalid price"
            self.margin = 1.0

    if check_none:
        mt5.order_check = lambda request: None
    else:
        mt5.order_check = lambda request: _Check(check_retcode)

    mt5.order_send_calls = []

    class _Send:
        def __init__(self, retcode, order, comment):
            self.retcode = retcode
            self.order = order
            self.comment = comment

    def _order_send(request):
        mt5.order_send_calls.append(request)
        return _Send(send_retcode, send_order, send_comment)
    mt5.order_send = _order_send

    mt5.orders_get = lambda ticket=None: []
    mt5.history_orders_get = lambda ticket=None: None
    return mt5


def _pending_args(tmp_path):
    root = tmp_path
    config = {"parallel_ai_magic": 2026072902, "parallel_ai_risk_percent": 0.5}
    signal_id = "XAUUSD.s_M5_20260818_1910"
    decision = {
        "direction": "SELL",
        "entry": 4355.0,
        "sl": 4360.0,
        "tp1": 4345.0,
        "tp2": 4335.0,
        "route": "FIB_PA",
        "reason": "test",
        "conditions": [],
    }
    raw = {"symbol": "XAUUSD.s"}
    return root, config, signal_id, decision, raw


def test_precheck_fail_blocks_order_send(tmp_path, monkeypatch):
    import sys

    for kwargs in ({"check_retcode": 10015}, {"check_none": True}):
        fake = _fake_mt5(**kwargs)
        monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
        root, config, signal_id, decision, raw = _pending_args(tmp_path)
        result = place_ai_pending(root, config, signal_id, decision, raw)
        assert result["ok"] is False
        assert result["state"] == "PRECHECK_FAIL"
        assert "last_error" in result
        assert fake.order_send_calls == []


def test_precheck_pass_proceeds_to_order_send(tmp_path, monkeypatch):
    import sys

    fake = _fake_mt5(check_retcode=0, send_retcode=10009, send_order=999)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    root, config, signal_id, decision, raw = _pending_args(tmp_path)
    result = place_ai_pending(root, config, signal_id, decision, raw)
    assert len(fake.order_send_calls) == 1
    assert result["ok"] is True
    assert result["order_ticket"] == 999


def test_order_send_still_uses_trade_retcode_done(tmp_path, monkeypatch):
    import sys

    # order_check passes, but order_send is rejected: must not be treated as success.
    fake = _fake_mt5(check_retcode=0, send_retcode=10015, send_comment="rejected")
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    root, config, signal_id, decision, raw = _pending_args(tmp_path)
    result = place_ai_pending(root, config, signal_id, decision, raw)
    assert result["ok"] is False
    assert result["state"] == "SERVER_REJECTED"
    assert result["retcode"] == 10015


def test_place_ai_pending_detects_stale_entry(tmp_path, monkeypatch):
    import sys

    fake = _fake_mt5(check_retcode=0, send_retcode=10009)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    root, config, signal_id, decision, raw = _pending_args(tmp_path)
    # Execution tick bid = 4360.0; SELL_STOP entry above it is stale.
    decision["entry"] = 4361.0
    decision["sl"] = 4365.0
    decision["tp1"] = 4355.0
    decision["tp2"] = 4350.0
    result = place_ai_pending(root, config, signal_id, decision, raw)
    assert result["ok"] is False
    assert result["state"] == "STALE"
    assert result["reason"] == "ENTRY_ALREADY_CROSSED"
    assert fake.order_send_calls == []


def test_trade_group_for_magic_classification():
    assert _trade_group_for_magic(2026072901, 2026072902, 2026072903) == "ea"
    assert _trade_group_for_magic(2026072902, 2026072902, 2026072903) == "ai"
    assert _trade_group_for_magic(2026072903, 2026072902, 2026072903) is None
    # magic=0 是桌面客户端手动下单的默认值，按“人工”归类
    assert _trade_group_for_magic(0, 2026072902, 2026072903) == "manual"
    assert _trade_group_for_magic(999999, 2026072902, 2026072903) == "manual"


def test_position_close_facts_profit_is_only_last_deal():
    from types import SimpleNamespace

    deals = [
        SimpleNamespace(ticket=1, entry=1, profit=36.0, commission=0.0, swap=0.0,
                        fee=0.0, price=4497.39, volume=0.02, time=1000),
        SimpleNamespace(ticket=2, entry=1, profit=-1.5, commission=0.0, swap=0.0,
                        fee=0.0, price=4487.11, volume=0.03, time=2000),
    ]
    mt5 = SimpleNamespace(history_deals_get=lambda position=None: deals)
    facts = _position_close_facts(mt5, 740111524)
    assert facts["profit"] == -1.5  # 只表示最后一次平仓动作本身
    assert facts["whole_trade_net"] == 34.5  # 整笔累计另存字段


def _deal(ticket, position_id, entry, magic, time, profit=0.0,
          commission=0.0, swap=0.0, fee=0.0):
    from types import SimpleNamespace

    return SimpleNamespace(
        ticket=ticket,
        position_id=position_id,
        entry=entry,
        magic=magic,
        time=time,
        profit=profit,
        commission=commission,
        swap=swap,
        fee=fee,
    )


def _fake_mt5_history(deals, positions=()):
    import types

    mt5 = types.ModuleType("MetaTrader5")
    mt5.DEAL_ENTRY_IN = 0
    mt5.DEAL_ENTRY_OUT = 1
    mt5.DEAL_ENTRY_OUT_BY = 3
    mt5.initialize = lambda *args, **kwargs: True
    mt5.shutdown = lambda: None
    mt5.history_deals_get = lambda *args, **kwargs: list(deals)
    mt5.positions_get = lambda *args, **kwargs: list(positions)
    return mt5


def test_collect_trade_groups_partial_close_magic_zero_stays_ai(tmp_path, monkeypatch):
    import sys
    from datetime import datetime, timezone
    from types import SimpleNamespace

    mid = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    deals = [
        # AI position 100: opened with AI magic, TP1 partial close magic=0,
        # final close magic=AI. Must remain a single AI trade.
        _deal(1, 100, 0, 2026072902, mid - 1000),
        _deal(2, 100, 1, 0, mid, profit=-30.0),
        _deal(3, 100, 3, 2026072902, mid + 60, profit=20.0),
        # E2E position 200: open + partial + final all E2E magic, fully excluded.
        _deal(4, 200, 0, 2026072903, mid - 1000),
        _deal(5, 200, 1, 2026072903, mid, profit=-0.16),
        _deal(6, 200, 3, 2026072903, mid + 60, profit=0.34),
        # Position 500: open magic 0（桌面手动下单）→ 归入人工组。
        _deal(7, 500, 0, 0, mid - 1000),
        _deal(8, 500, 1, 0, mid, profit=-1.0),
        # AI position 400: open inside the window, no close -> open at day end.
        _deal(9, 400, 0, 2026072902, mid),
    ]
    positions = []
    monkeypatch.setitem(sys.modules, "MetaTrader5", _fake_mt5_history(deals, positions))

    groups = collect_trade_groups(
        tmp_path,
        {"parallel_ai_magic": 2026072902, "parallel_ai_e2e_test_magic": 2026072903},
        "2026.08.19 01:00:00",
        "2026.08.19 23:00:00",
    )

    assert groups["ai"]["count"] == 1
    assert groups["ai"]["win"] == 0
    assert groups["ai"]["loss"] == 1
    assert groups["ai"]["net"] == -10.0
    assert groups["ai"]["open"] == 1

    assert groups["ea"]["count"] == 0
    assert groups["manual"]["count"] == 1
    assert groups["manual"]["net"] == -1.0
    assert groups["unknown"]["count"] == 0
    assert groups["unknown"]["net"] == 0.0

    # E2E position 200 must not leak into any official group.
    for key in ("ea", "ai", "manual", "unknown"):
        group = groups[key]
        assert group["count"] == (1 if key in ("ai", "manual") else 0)


def test_lifecycle_ea_positions_override_rotating_magic_without_relabeling_manual(tmp_path, monkeypatch):
    import sys
    from datetime import datetime, timezone

    from tools.ai_trade_manager import ea_position_ids_from_lifecycle

    mid = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc).timestamp()
    deals = [
        _deal(1, 100, 0, 2026091601, mid - 1000),
        _deal(2, 100, 1, 0, mid, profit=68.31),
        _deal(3, 101, 0, 2026091601, mid + 100),
        _deal(4, 300, 0, 0, mid - 1000),
        _deal(5, 300, 1, 0, mid + 20, profit=-5.0),
    ]
    monkeypatch.setitem(sys.modules, "MetaTrader5", _fake_mt5_history(deals))
    lifecycle_rows = [
        {"position_id": "100", "data_source": "lifecycle",
         "rows": [{"event_kind": "OPEN", "trader": "A", "slot_magic": "2026091601"}]},
        {"position_id": "101", "data_source": "lifecycle",
         "rows": [{"event_kind": "OPEN", "trader": "A", "slot_magic": "2026091601"}]},
        {"position_id": "300", "data_source": "legacy_event", "rows": []},
    ]
    ea_ids = ea_position_ids_from_lifecycle(lifecycle_rows)
    config = {"parallel_ai_magic": 2026072902, "parallel_ai_e2e_test_magic": 2026072903}

    groups = collect_trade_groups(
        tmp_path, config, "2026.09.16 01:00:00", "2026.09.16 23:00:00",
        ea_position_ids=ea_ids,
    )
    ownership = collect_position_ownership(
        tmp_path, "2026.09.16 01:00:00", "2026.09.16 23:00:00",
        config, ea_position_ids=ea_ids,
    )

    assert ea_ids == {100, 101}
    assert groups["ea"]["count"] == 1
    assert groups["ea"]["open"] == 1
    assert groups["ea"]["net"] == 68.31
    assert groups["manual"]["count"] == 1
    assert groups["manual"]["net"] == -5.0
    assert ownership[100] == ownership[101] == "ea"
    assert ownership[300] == "manual"
