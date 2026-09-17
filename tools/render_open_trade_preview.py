from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_review_service import ensure_lark_security_keyword
from tools.render_daily_lark_preview import render_card_html
from tools.trade_lifecycle import fallback_commentary, render_trade_card


def build_preview_card(item: dict[str, Any]) -> dict[str, Any]:
    trade = item.get("aggregated_trade") or item.get("facts")
    if not isinstance(trade, dict):
        raise ValueError("open outbox item requires aggregated_trade or facts")
    if str(item.get("notification_type") or "open_trade_facts") != "open_trade_facts":
        raise ValueError("preview input must be an open_trade_facts item")

    saved = item.get("commentary")
    if isinstance(saved, dict) and str(saved.get("comment") or "").strip():
        commentary = {
            "reason": str(saved.get("reason") or "本地事实理由将在卡片中生成。"),
            "comment": str(saved["comment"]).strip(),
        }
    else:
        commentary = fallback_commentary("open_trade_facts", trade)
    card = render_trade_card("open_trade_facts", trade, commentary)
    return ensure_lark_security_keyword(card, "EA复盘")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a local open-trade Lark preview")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    item = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if not isinstance(item, dict):
        raise ValueError("preview input must be a JSON object")
    card = build_preview_card(item)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_card_html(card), encoding="utf-8")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
