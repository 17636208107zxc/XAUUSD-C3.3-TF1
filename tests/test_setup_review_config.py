from pathlib import Path

from tools.setup_review_config import discover_data_roots, build_config


def test_discover_data_roots_finds_account_symbol_folders(tmp_path: Path):
    root = tmp_path / "XAUUSD_M5_AI_Pullback"
    account = root / "Broker_Server" / "Account_123" / "XAUUSD.s_M5"
    (account / "Raw_Data").mkdir(parents=True)
    assert discover_data_roots(root) == [account]


def test_build_config_points_to_selected_data_root(tmp_path: Path):
    config = build_config(tmp_path / "Account_1" / "XAUUSD.s_M5")
    assert config["data_root"] == str((tmp_path / "Account_1" / "XAUUSD.s_M5").resolve())
    assert config["model"] == "deepseek-v4-flash"
    assert config["daily_review_model"] == "deepseek-v4-pro"
    assert config["monitor_daily_limit"] == 29
    assert config["daily_review_limit"] == 1
    assert config["max_daily_reviews_per_run"] == 1
    assert config["trade_commentary_enabled"] is True
    assert config["trade_commentary_prompt_file"] == "trade_commentary_prompt.txt"
    assert config["trade_commentary_max_tokens"] == 220
    assert "trade_commentary_daily_limit" not in config
    assert config["independent_ai_enabled"] is False
    assert config["independent_ai_daily_limit"] == 300
    assert config["independent_ai_min_confidence"] == 70
    assert config["independent_ai_ea_wait_seconds"] == 120
    assert config["independent_ai_m5_bars"] == 13
    assert config["independent_ai_max_tokens"] == 350
    assert config["independent_ai_explanation_max_tokens"] == 220
    assert config["independent_ai_prompt_file"] == "independent_ai_prompt.txt"
    assert (
        config["independent_ai_divergence_prompt_file"]
        == "independent_ai_divergence_prompt.txt"
    )
    assert config["parallel_ai_enabled"] is True
    assert config["parallel_ai_mode"] == "shadow"
    assert config["parallel_ai_rule_version"] == "EA_OPEN_V3_9_20_R1"
    assert config["parallel_ai_daily_limit"] == 300
    assert config["parallel_ai_ea_wait_seconds"] == 120
    assert config["parallel_ai_primary_max_tokens"] == 900
    assert config["parallel_ai_difference_max_tokens"] == 180
