from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_review_service import (
    build_candidate_lark_card,
    build_daily_lark_card,
)


HEADER_COLORS = {
    "green": "#14532d",
    "red": "#7f1d1d",
    "blue": "#1e3a8a",
    "orange": "#78350f",
}


def _inline_markdown(value: str) -> str:
    escaped = html.escape(value)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def _markdown_block(value: str) -> str:
    rendered: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            rendered.append('<div class="gap"></div>')
        elif len(line) >= 4 and set(line) <= {"━", "─"}:
            rendered.append('<div class="divider"></div>')
        elif line.startswith("### "):
            rendered.append(f"<h3>{_inline_markdown(line[4:])}</h3>")
        elif line.startswith("## "):
            rendered.append(f"<h2>{_inline_markdown(line[3:])}</h2>")
        elif line.startswith("# "):
            rendered.append(f"<h1>{_inline_markdown(line[2:])}</h1>")
        elif line.startswith("- "):
            rendered.append(f'<div class="bullet">• {_inline_markdown(line[2:])}</div>')
        else:
            rendered.append(f"<p>{_inline_markdown(line)}</p>")
    return "\n".join(rendered)


def render_card_html(card: dict[str, Any]) -> str:
    card_data = dict(card.get("card") or {})
    header = dict(card_data.get("header") or {})
    title = html.escape(str(dict(header.get("title") or {}).get("content") or "Lark卡片预览"))
    color = HEADER_COLORS.get(str(header.get("template") or "blue"), "#1e3a8a")
    body = dict(card_data.get("body") or {})
    blocks = [
        _markdown_block(str(element.get("content") or ""))
        for element in body.get("elements") or []
        if element.get("tag") == "markdown"
    ]
    body_html = '\n<div class="divider"></div>\n'.join(blocks)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0b0b0c; color: #f2f3f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }}
  .page {{ min-height: 100vh; padding: 36px 16px 64px; }}
  .label {{ width: min(760px, 100%); margin: 0 auto 12px; color: #8f959e; font-size: 13px; }}
  .card {{ width: min(760px, 100%); margin: 0 auto; background: #252629; border: 1px solid #34363a; border-radius: 12px; overflow: hidden; box-shadow: 0 18px 55px rgba(0,0,0,.38); }}
  .header {{ padding: 17px 22px; background: {color}; font-size: 21px; line-height: 1.35; font-weight: 750; }}
  .content {{ padding: 20px 22px 24px; }}
  h1 {{ margin: 0 0 14px; font-size: 21px; }}
  h2 {{ margin: 0 0 12px; color: #ffffff; font-size: 18px; line-height: 1.4; }}
  h3 {{ margin: 18px 0 10px; padding-left: 10px; border-left: 3px solid #4b8cff; color: #ffffff; font-size: 17px; line-height: 1.4; font-weight: 750; }}
  p, .bullet {{ margin: 6px 0; font-size: 15px; line-height: 1.65; overflow-wrap: anywhere; }}
  strong {{ color: #ffffff; font-weight: 700; }}
  .gap {{ height: 5px; }}
  .divider {{ height: 1px; margin: 20px 0; background: #3b3d42; }}
  .notice {{ width: min(760px, 100%); margin: 14px auto 0; color: #8f959e; font-size: 12px; line-height: 1.5; }}
</style>
</head>
<body>
<main class="page">
  <div class="label">Lark 卡片本地预览（不会发送消息）</div>
  <article class="card">
    <header class="header">{title}</header>
    <section class="content">{body_html}</section>
  </article>
  <div class="notice">此页面由已保存的日报数据生成，仅用于检查内容和排版。</div>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a local HTML preview of a daily Lark card")
    parser.add_argument("--day", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="同时生成第二张「正式候选订单明细」卡片预览",
    )
    args = parser.parse_args()

    payload = json.loads(args.data.read_text(encoding="utf-8-sig"))
    response = json.loads(args.response.read_text(encoding="utf-8-sig"))
    card = build_daily_lark_card(
        args.day,
        payload,
        response,
        {"monitor": 5, "daily": 1},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_card_html(card), encoding="utf-8")
    printed = [str(args.output.resolve())]
    if args.candidates:
        candidate_card = build_candidate_lark_card(
            args.day,
            payload,
            response,
            {"monitor": 5, "daily": 1},
        )
        candidate_html = args.output.parent / f"Daily_Candidates_{args.day}.html"
        candidate_html.write_text(render_card_html(candidate_card), encoding="utf-8")
        printed.append(str(candidate_html.resolve()))
    print("\n".join(printed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
