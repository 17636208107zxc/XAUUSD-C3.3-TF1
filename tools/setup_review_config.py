from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def default_common_root() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise RuntimeError("APPDATA环境变量不存在，请使用--common-files手动指定路径")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


def discover_data_roots(observation_root: Path) -> list[Path]:
    observation_root = Path(observation_root)
    if not observation_root.exists():
        return []
    roots = {path.parent.resolve() for path in observation_root.rglob("Raw_Data") if path.is_dir()}
    return sorted(roots, key=lambda path: str(path).lower())


def build_config(data_root: Path) -> dict[str, Any]:
    return {
        "data_root": str(Path(data_root).resolve()),
        "model": "deepseek-v4-flash",
        "daily_review_model": "deepseek-v4-pro",
        "api_key_environment": "DEEPSEEK_API_KEY",
        "api_key_file": "Config/deepseek_api_key.txt",
        "monitor_prompt_file": "market_monitor_prompt.txt",
        "daily_prompt_file": "daily_review_prompt.txt",
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
        "monitor_daily_limit": 29,
        "daily_review_limit": 1,
        "max_daily_reviews_per_run": 1,
        "lark_daily_notify_enabled": True,
        "lark_webhook_environment": "LARK_WEBHOOK_URL",
        "lark_webhook_file": "Config/lark_webhook.txt",
        "lark_timeout_seconds": 5,
        "lark_outbox_enabled": True,
        "lark_retries": 2,
        "lark_retry_delay_seconds": 1,
        "lark_security_keyword": "EA复盘",
        "timeout_seconds": 20,
        "retries": 1,
        "event_merge_minutes": 10,
        "failure_cooldown_minutes": 30,
        "poll_seconds": 60,
        "raw_data_retention_days": 90,
        "archive_directory": "",
    }


def choose_root(roots: list[Path], requested: Path | None) -> Path:
    if requested is not None:
        return requested.resolve()
    if not roots:
        raise RuntimeError("未发现EA数据目录。请先把V3.8 EA挂到XAUUSD.s M5并等待生成Raw_Data。")
    if len(roots) == 1:
        return roots[0]
    print("发现多个EA数据目录：")
    for index, root in enumerate(roots, 1):
        print(f"  {index}. {root}")
    while True:
        selected = input("请输入序号：").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(roots):
            return roots[int(selected) - 1]
        print("序号无效，请重新输入。")


def install_config(data_root: Path, output_config: Path) -> Path:
    data_root = data_root.resolve()
    output_config.parent.mkdir(parents=True, exist_ok=True)
    config = build_config(data_root)
    output_config.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )

    target_config_dir = data_root / "Config"
    target_config_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "market_monitor_prompt.txt",
        "daily_review_prompt.txt",
        "trade_commentary_prompt.txt",
        "independent_ai_prompt.txt",
        "independent_ai_divergence_prompt.txt",
        "parallel_ai_entry_prompt.txt",
        "parallel_ai_difference_prompt.txt",
    ):
        source = PROJECT_ROOT / "config" / name
        shutil.copy2(source, target_config_dir / name)
    key_file = target_config_dir / "deepseek_api_key.txt"
    if not key_file.exists():
        key_file.write_text("请把DeepSeek API Key粘贴到这里并删除本行\n", encoding="utf-8")
    lark_file = target_config_dir / "lark_webhook.txt"
    if not lark_file.exists():
        lark_file.write_text("请把Lark Webhook粘贴到这里并删除本行\n", encoding="utf-8")
    return output_config


def main() -> int:
    parser = argparse.ArgumentParser(description="查找MT5公共目录并生成AI盯盘复盘配置")
    parser.add_argument("--common-files", type=Path, default=None)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "config" / "review_config.json",
    )
    args = parser.parse_args()

    if args.data_root is not None:
        selected = choose_root([], args.data_root)
    else:
        common_files = (args.common_files or default_common_root()).resolve()
        roots = discover_data_roots(common_files / "XAUUSD_M5_AI_Pullback")
        selected = choose_root(roots, None)
    output = install_config(selected, args.output.resolve())
    print(f"已选择数据目录：{selected}")
    print(f"已生成配置：{output}")
    print(f"请填写API Key：{selected / 'Config' / 'deepseek_api_key.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
