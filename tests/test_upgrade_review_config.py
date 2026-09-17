from __future__ import annotations

import json
from pathlib import Path

from tools.upgrade_review_config import upgrade_config


def test_upgrade_config_raises_limits_and_preserves_existing_paths(tmp_path: Path):
    config_path = tmp_path / "review_config.json"
    config_path.write_text(
        json.dumps(
            {
                "data_root": "D:/MT5/Data",
                "api_key_file": "Config/deepseek_api_key.txt",
                "monitor_daily_limit": 29,
                "daily_review_limit": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    backup_path = upgrade_config(config_path)

    upgraded = json.loads(config_path.read_text(encoding="utf-8"))
    assert upgraded["data_root"] == "D:/MT5/Data"
    assert upgraded["api_key_file"] == "Config/deepseek_api_key.txt"
    assert upgraded["monitor_daily_limit"] == 29
    assert upgraded["daily_review_limit"] == 1
    assert upgraded["max_daily_reviews_per_run"] == 1
    assert upgraded["daily_review_model"] == "deepseek-v4-pro"
    assert upgraded["trade_commentary_enabled"] is True
    assert upgraded["trade_commentary_prompt_file"] == "trade_commentary_prompt.txt"
    assert upgraded["trade_commentary_max_tokens"] == 220
    assert "trade_commentary_daily_limit" not in upgraded
    assert upgraded["independent_ai_enabled"] is False
    assert upgraded["independent_ai_daily_limit"] == 300
    assert upgraded["independent_ai_min_confidence"] == 70
    assert upgraded["independent_ai_ea_wait_seconds"] == 120
    assert upgraded["independent_ai_m5_bars"] == 13
    assert upgraded["independent_ai_max_tokens"] == 350
    assert upgraded["independent_ai_explanation_max_tokens"] == 220
    assert upgraded["independent_ai_prompt_file"] == "independent_ai_prompt.txt"
    assert (
        upgraded["independent_ai_divergence_prompt_file"]
        == "independent_ai_divergence_prompt.txt"
    )
    assert upgraded["parallel_ai_enabled"] is True
    assert upgraded["parallel_ai_mode"] == "shadow"
    assert upgraded["parallel_ai_rule_version"] == "EA_OPEN_V3_9_20_R1"
    assert upgraded["parallel_ai_daily_limit"] == 300
    assert upgraded["parallel_ai_ea_wait_seconds"] == 120
    assert upgraded["parallel_ai_primary_max_tokens"] == 900
    assert upgraded["parallel_ai_difference_max_tokens"] == 180
    assert backup_path.exists()
    original = json.loads(backup_path.read_text(encoding="utf-8"))
    assert original["monitor_daily_limit"] == 29
    assert original["daily_review_limit"] == 1
