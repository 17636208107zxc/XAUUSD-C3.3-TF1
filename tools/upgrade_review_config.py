from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "review_config.json"
UPDATES: dict[str, Any] = {
    "monitor_daily_limit": 29,
    "daily_review_limit": 1,
    "max_daily_reviews_per_run": 1,
    "daily_review_model": "deepseek-v4-pro",
    "trade_commentary_enabled": True,
    "trade_commentary_prompt_file": "trade_commentary_prompt.txt",
    "trade_commentary_max_tokens": 220,
    "independent_ai_enabled": False,
    "independent_ai_daily_limit": 300,
    "independent_ai_min_confidence": 70,
    "independent_ai_ea_wait_seconds": 120,
    "independent_ai_m5_bars": 13,
    "independent_ai_max_tokens": 350,
    "independent_ai_explanation_max_tokens": 220,
    "independent_ai_prompt_file": "independent_ai_prompt.txt",
    "independent_ai_divergence_prompt_file": "independent_ai_divergence_prompt.txt",
    "parallel_ai_enabled": True,
    "parallel_ai_mode": "shadow",
    "parallel_ai_rule_version": "EA_OPEN_V3_9_20_R1",
    "parallel_ai_daily_limit": 300,
    "parallel_ai_ea_wait_seconds": 120,
    "parallel_ai_primary_max_tokens": 900,
    "parallel_ai_difference_max_tokens": 180,
    "parallel_ai_entry_prompt_file": "parallel_ai_entry_prompt.txt",
    "parallel_ai_difference_prompt_file": "parallel_ai_difference_prompt.txt",
    "lark_daily_notify_enabled": True,
    "lark_webhook_environment": "LARK_WEBHOOK_URL",
    "lark_webhook_file": "Config/lark_webhook.txt",
    "lark_timeout_seconds": 5,
    "lark_outbox_enabled": True,
    "lark_retries": 2,
    "lark_retry_delay_seconds": 1,
    "lark_security_keyword": "EA复盘",
}


def upgrade_config(config_path: Path) -> Path:
    config_path = Path(config_path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")
    value = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("review_config.json必须是JSON对象")

    backup_path = config_path.with_name(config_path.name + ".before_daily_scheduler_upgrade.bak")
    if not backup_path.exists():
        shutil.copy2(config_path, backup_path)

    # Keep explicit user choices.  Only the legacy observer is forcibly disabled
    # because V2 and the old observer must never run side by side.
    for key, default in UPDATES.items():
        value.setdefault(key, default)
    value["independent_ai_enabled"] = False
    config_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    data_root_text = str(value.get("data_root", "")).strip()
    if data_root_text:
        data_root = Path(data_root_text).expanduser()
        if data_root.exists():
            for prompt_name in (
                "trade_commentary_prompt.txt",
                "independent_ai_prompt.txt",
                "independent_ai_divergence_prompt.txt",
                "parallel_ai_entry_prompt.txt",
                "parallel_ai_difference_prompt.txt",
            ):
                prompt_source = DEFAULT_CONFIG_PATH.parent / prompt_name
                prompt_target = data_root / "Config" / prompt_name
                if prompt_source.exists():
                    prompt_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(prompt_source, prompt_target)
            lark_file = data_root / "Config" / "lark_webhook.txt"
            lark_file.parent.mkdir(parents=True, exist_ok=True)
            if not lark_file.exists():
                lark_file.write_text(
                    "请把Lark Webhook粘贴到这里并删除本行\n",
                    encoding="utf-8",
                )
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(description="升级每日复盘调度与API额度配置")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()
    backup = upgrade_config(args.config)
    print(f"已升级配置：{args.config.resolve()}")
    print("AI盯盘额度：29次/北京时间自然日")
    print("日报额度：1次/北京时间自然日")
    print("单次轮询最多生成：1份日报（优先昨天；历史缺失按后续自然日逐日补）")
    print(f"原配置备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
