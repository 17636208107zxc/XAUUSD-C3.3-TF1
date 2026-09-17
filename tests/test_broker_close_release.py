from pathlib import Path


EA_PATH = Path(r"D:\Backup\Downloads\XAUUSD_M5_AI_Pullback_V3_9_14.mq5")


def _source() -> str:
    return EA_PATH.read_text(encoding="utf-8-sig")


def test_broker_close_uses_last_symbol_session_and_five_minute_delay():
    source = _source()
    assert "ResolveDailyReviewSession" in source
    assert "SymbolInfoSessionTrade(symbol,day_of_week,session_index" in source
    assert "earliest_start_seconds" in source
    assert "latest_end_seconds" in source
    assert "session.ready_server=session.session_close+delay_minutes*60" in source


def test_close_scheduler_checks_previous_day_and_skips_weekend_without_sessions():
    source = _source()
    assert "for(int day_offset=-1;day_offset<=0;day_offset++)" in source
    assert "return (datetime)((((long)value)/seconds_per_day)*seconds_per_day)" in source
    assert "ENUM_DAY_OF_WEEK StableServerDayOfWeek" in source
    assert "const long day_index=((long)day_midnight)/seconds_per_day" in source
    assert "int anchored_day=(int)((day_index+4)%7)" in source
    assert "StableServerDayOfWeek(day_midnight)" in source
    assert "Reason=" in source
    assert "Session_Close_Triggers\\\\Pending" in source
    assert "Session_Close_Triggers\\\\Processed" in source
    assert "FileIsExist(pending_path,FILE_COMMON)" in source
    assert "FileIsExist(processed_path,FILE_COMMON)" in source


def test_close_scheduler_uses_timer_so_no_post_close_tick_is_required():
    source = _source()
    assert "EventSetTimer(30)" in source
    assert "void OnTimer()" in source
    assert "MaybeProcessDailyReviewCloseTriggers(TimeTradeServer(),false,\"OnTimer\")" in source
    assert "EventKillTimer()" in source


def test_close_scheduler_has_init_timer_and_throttled_tick_recovery_paths():
    source = _source()
    assert "MaybeProcessDailyReviewCloseTriggers(TimeTradeServer(),true,\"OnInit\")" in source
    assert "MaybeProcessDailyReviewCloseTriggers(TimeTradeServer(),false,\"OnTimer\")" in source
    assert "MaybeProcessDailyReviewCloseTriggers(server_now,false,\"OnTick\")" in source
    assert "g_last_daily_review_check_server" in source
    assert "server_now-g_last_daily_review_check_server<30" in source


def test_close_scheduler_logs_timer_start_and_rate_limited_schedule_diagnostics():
    source = _source()
    assert "[收盘复盘调度] 30秒定时器已启动" in source
    assert "[收盘复盘调度诊断] Source=" in source
    assert "SessionOpen=" in source
    assert "SessionClose=" in source
    assert "Ready=" in source
    assert "g_last_daily_review_diagnostic_server" in source
