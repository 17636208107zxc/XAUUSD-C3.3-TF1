from pathlib import Path


EA_PATH = Path(r"D:\Backup\Downloads\XAUUSD_M5_AI_Pullback_V3_9_14.mq5")


def source() -> str:
    return EA_PATH.read_text(encoding="utf-8-sig")


def extract_between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    finish = text.index(end, begin) + len(end)
    return text[begin:finish]


def test_parallel_audit_exports_physically_separate_input_and_trace():
    text = source()
    assert '"Parallel_AI_V2\\\\Independent_Input\\\\Pending"' in text
    assert '"Parallel_AI_V2\\\\EA_Trace\\\\Pending"' in text
    assert "bool WriteParallelIndependentInput(" in text
    assert "bool WriteParallelEATrace(" in text
    assert "PARALLEL_AUDIT_RULE_VERSION" in text


def test_parallel_audit_export_is_observation_only():
    block = extract_between(source(), "class CParallelAuditExporter", "};")
    for forbidden in ("OrderSend", "PositionClose", "OrderDelete", "trade.Buy", "trade.Sell"):
        assert forbidden not in block


def test_parallel_audit_export_uses_atomic_replace():
    block = extract_between(source(), "bool AtomicWriteParallelAuditJson(", "\n}")
    assert 'final_path+".tmp"' in block
    assert "FileFlush(handle)" in block
    assert "FileClose(handle)" in block
    assert "FileMove(" in block
    assert "FILE_REWRITE|FILE_COMMON" in block


def test_independent_input_schema_excludes_ea_outcome_names():
    block = extract_between(source(), "string BuildParallelIndependentInputJson(", "\n}")
    for forbidden in (
        "ScanResult", "local_candidate", "ea_ai_review", "execution",
        "reject_reason", "candidate_created", "pending_created",
    ):
        assert forbidden not in block
    assert "MarketSnapshot" in block
    assert "ArraySize(bars)!=200" in block


def test_trace_reuses_locked_snapshot_identity_and_hash():
    block = extract_between(source(), "bool WriteParallelEATrace(", "\n}")
    assert "snapshot_id" in block
    assert "input_hash" in block
    assert "BuildParallelEATraceJson" in block
