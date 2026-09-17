from pathlib import Path
import re


EA_PATH = Path(r"D:\Backup\Downloads\XAUUSD_M5_AI_Pullback_V3_9_14.mq5")


def _source() -> str:
    return EA_PATH.read_text(encoding="utf-8-sig")


def _exit_branch(source: str) -> str:
    start = source.index("else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)")
    end = source.index("\n   }\n}\n#endif", start)
    return source[start:end]


def test_final_exit_notification_occurs_only_after_no_position_is_confirmed():
    branch = _exit_branch(_source())
    position_check = branch.index("if(!position_still_open)")
    final_notice = branch.index('"[最终退出]')
    partial_observation = branch.index('"position_update"')

    assert position_check < final_notice < partial_observation


def legacy_open_and_close_cards_share_stable_position_trade_number():
    source = _source()

    assert re.search(
        r'"EA复盘｜交易#"\s*\+\s*IntegerToString\(\(long\)position_id\)\+"｜"',
        source,
    )
    assert "string BuildFinalTradeCard(" in source
    assert '"CLOSE_"+IntegerToString((long)position_id)' in source


def test_open_and_final_facts_use_stable_position_notification_ids():
    source = _source()

    assert 'IntegerToString((long)position_id)+"_OPEN"' in source
    assert 'IntegerToString((long)position_id)+"_FINAL"' in source
    assert 'notification_id,"open_trade_facts"' in source
    assert 'notification_id,"final_trade_facts"' in source


def test_open_notification_is_queue_only_and_never_sent_directly_by_mt5():
    source = _source()
    start = source.index("bool SendOpenTradeCard(")
    end = source.index("void EmitScanResult(", start)
    body = source[start:end]

    assert "g_lark.SendCard" not in body
    assert "QueueLarkFactsToCommonOutbox" in body


def test_trade_lifecycle_records_every_deal_profit_component():
    source = _source()
    start = source.index("bool WriteTradeLifecycleEvent(")
    end = source.index("bool QueueLarkFactsToCommonOutbox(", start)
    body = source[start:end]

    for field in ("DEAL_PROFIT", "DEAL_COMMISSION", "DEAL_SWAP", "DEAL_FEE"):
        assert field in body
    assert "Trade_Lifecycle" in body
    assert "event_id;server_time;event_kind;stage;position_id" in body


def test_final_card_aggregates_all_realized_pl_components():
    source = _source()
    start = source.index("bool AggregateClosedTradeFacts(")
    end = source.index("string BuildFinalTradeCard(", start)
    aggregate = source[start:end]

    for field in ("DEAL_PROFIT", "DEAL_COMMISSION", "DEAL_SWAP", "DEAL_FEE"):
        assert field in aggregate
    assert "facts.net_profit=facts.gross_profit+facts.commission+facts.swap+facts.fee" in aggregate


def test_tp_stage_handlers_do_not_send_lark_cards():
    source = _source()
    stage_start = source.index("const TradeRuntimeState before_exit=g_trades.State();")
    stage_end = source.index("string expiry_error", stage_start)
    stage_body = source[stage_start:stage_end]

    assert "SendOpenTradeCard" not in stage_body
    assert "SendFinalTradeCard" not in stage_body
    assert "QueueLarkCardToCommonOutbox" not in stage_body
