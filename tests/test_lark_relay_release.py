from pathlib import Path


EA_PATH = Path(r"D:\Backup\Downloads\XAUUSD_M5_AI_Pullback_V3_9_14.mq5")


def _source() -> str:
    return EA_PATH.read_text(encoding="utf-8-sig")


def test_open_facts_are_durably_queued_with_python_contract():
    source = _source()
    assert "QueueLarkFactsToCommonOutbox" in source
    assert '\\"schema_version\\":2' in source
    assert '\\"notification_id\\":\\"' in source
    assert '\\"notification_type\\":\\""+JsonEscape(notification_type)+"\\"' in source
    assert 'notification_id,"open_trade_facts"' in source
    assert '\\"facts\\":' in source
    assert 'outbox_root+"\\\\Pending"' in source


def test_open_notification_uses_only_deterministic_queue_deduplication():
    source = _source()
    notify_start = source.index("bool SendOpenTradeCard(")
    notify_end = source.index("void EmitScanResult(", notify_start)
    notify_body = source[notify_start:notify_end]
    assert "g_lark.SendCard" not in notify_body
    assert "MarkLarkDealSent" not in notify_body
    assert "QueueLarkFactsToCommonOutbox" in notify_body
    assert 'IntegerToString((long)position_id)+"_OPEN"' in notify_body


def test_direct_lark_send_has_bounded_retry_and_response_diagnostics():
    source = _source()
    send_start = source.index("bool SendCard(const string card_json,string &error)")
    send_end = source.index("};", send_start)
    send_body = source[send_start:send_end]
    assert "for(int attempt=1;attempt<=3;attempt++)" in send_body
    assert "Response=" in send_body
    assert "Headers=" in send_body


def test_open_trade_card_contains_configured_security_keyword():
    source = _source()
    card_start = source.index("string BuildOpenTradeCard(")
    card_end = source.index("bool QueueLarkCardToCommonOutbox", card_start)

    assert "EA复盘" in source[card_start:card_end]
