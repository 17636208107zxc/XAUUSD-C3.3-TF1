from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_review_service import (
    build_candidate_lark_card,
    build_comparison_lark_card,
    build_daily_lark_card,
    build_parallel_ai_lark_card,
    collect_notification_issues,
)
from tools.daily_review_renderer import (
    build_comparison_card_markdown,
    build_parallel_ai_card_markdown,
    render_candidate_card_markdown,
    render_daily_review_markdown,
)
from tools.render_daily_lark_preview import render_card_html
from tools.review_core import (
    apply_local_daily_facts,
    atomic_write_json,
    atomic_write_text,
    build_daily_payload,
    build_mt5_m1_loader,
    build_mt5_tick_loader,
    deduplicate_rows,
    load_missed_candidate_history,
    rows_for_server_window,
)
from tools.ai_trade_manager import TERMINAL_PATH
from tools.trade_lifecycle import (
    enrich_close_reason_from_mt5,
    enrich_trade_mfe_mae,
    trades_for_server_window,
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _data_root(config: dict[str, Any], config_path: Path) -> Path:
    raw = str(config.get("data_root") or "").strip()
    if not raw:
        raise ValueError("config.data_root is required")
    path = Path(raw)
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def _saved_paths(root: Path, day: str) -> tuple[Path, Path]:
    folder = root / "Daily_Review" / day
    return folder / f"Daily_Data_{day}.json", folder / f"Daily_Review_{day}.json"


def _markdown_text(value: Any) -> str:
    pieces: list[str] = []
    if isinstance(value, dict):
        if value.get("tag") == "markdown" and value.get("content"):
            pieces.append(str(value["content"]))
        for child in value.values():
            pieces.append(_markdown_text(child))
    elif isinstance(value, list):
        for child in value:
            pieces.append(_markdown_text(child))
    return "\n".join(piece for piece in pieces if piece)


def _markdown_number(text: str, label: str) -> float | None:
    match = re.search(rf"\*\*{re.escape(label)}：\*\*\s*([0-9]+(?:\.[0-9]+)?)", text)
    return float(match.group(1)) if match else None


def enrich_legacy_trade_notifications(
    root: Path, trades: list[dict[str, Any]]
) -> None:
    """Read legacy sent open cards to restore facts absent from old event rows."""
    sent = root / "Lark_Outbox" / "Sent"
    for trade in trades:
        if str(trade.get("data_source") or "") != "legacy_event":
            continue
        position_id = str(trade.get("position_id") or "").strip()
        candidates = [sent / f"OPEN_{position_id}.json", sent / f"{position_id}_OPEN.json"]
        source = next((path for path in candidates if path.exists()), None)
        if source is None:
            continue
        try:
            text = _markdown_text(_load(source))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        initial_sl = _markdown_number(text, "初始SL")
        initial_risk = _markdown_number(text, "预计最大风险")
        if initial_risk is None:
            initial_risk = _markdown_number(text, "初始风险")
        tp1 = _markdown_number(text, "1R目标")
        tp2 = _markdown_number(text, "2R目标")
        if initial_sl is not None:
            trade["initial_sl"] = initial_sl
        if initial_risk is not None and initial_risk > 0:
            trade["initial_risk"] = initial_risk
            net = float(trade.get("whole_trade_net", trade.get("net_profit", 0)) or 0)
            trade["final_r"] = round(net / initial_risk, 2)
        if tp1 is not None:
            trade["tp1_price"] = tp1
        if tp2 is not None:
            trade["tp2_price"] = tp2
        trade["data_source"] = "legacy_event_notification"


def rebuild(config_path: Path, day: str, output: Path) -> dict[str, Any]:
    config = _load(config_path)
    root = _data_root(config, config_path)
    saved_data_path, saved_response_path = _saved_paths(root, day)
    saved_data = _load(saved_data_path)
    response = _load(saved_response_path)
    session = dict(saved_data.get("session_window") or {})
    server_open = str(session.get("server_open") or "")
    server_close = str(session.get("server_close") or "")
    if not server_open or not server_close:
        raise ValueError("saved daily data has no broker session window")

    snapshots = deduplicate_rows(
        rows_for_server_window(root, "Market_Snapshots", server_open, server_close),
        ("server_time",),
    )
    events = deduplicate_rows(
        rows_for_server_window(root, "Strategy_Events", server_open, server_close),
        ("server_time", "event_type", "signal_id", "deal_ticket", "reason"),
    )
    calendar = deduplicate_rows(
        rows_for_server_window(
            root,
            "Economic_Calendar",
            server_open,
            server_close,
            timestamp_field="event_server_time",
        ),
        ("value_id", "event_id", "event_beijing_time"),
    )
    lifecycle_dir = root / "Trade_Lifecycle"
    lifecycle_trades = (
        trades_for_server_window(root, server_open, server_close)
        if lifecycle_dir.exists() and any(lifecycle_dir.glob("Position_*.csv"))
        else None
    )
    if lifecycle_trades:
        lifecycle_trades = enrich_close_reason_from_mt5(
            lifecycle_trades, TERMINAL_PATH, "XAUUSD.s"
        )
    tick_diagnostics: dict[str, Any] = {}
    m1_loader = build_mt5_m1_loader(
        "XAUUSD.s", TERMINAL_PATH, diagnostics=tick_diagnostics
    )
    tick_loader = build_mt5_tick_loader(
        "XAUUSD.s", TERMINAL_PATH, diagnostics=tick_diagnostics
    )
    payload = build_daily_payload(
        day,
        snapshots,
        events,
        calendar,
        prior_issue_history=list(saved_data.get("prior_issue_history") or []),
        lifecycle_trades=lifecycle_trades,
        missed_history_records=load_missed_candidate_history(root),
        m1_loader=m1_loader,
        tick_loader=tick_loader,
        tick_diagnostics=tick_diagnostics,
    )
    enrich_legacy_trade_notifications(root, payload["actual_trade_rows"])
    payload["program_issues"] = collect_notification_issues(root)
    payload["strategy_issues"] = list(saved_data.get("strategy_issues") or [])
    payload["session_window"] = session
    try:
        from tools.daily_market_review import build_market_review

        payload["market_review"] = build_market_review(
            root, day, payload.get("candidate_rows") or []
        )
    except Exception:
        payload["market_review"] = {}
    payload["source_counts"] = {
        "snapshots": len(snapshots),
        "events": len(events),
        "calendar": len(calendar),
    }
    try:
        from tools.ai_trade_manager import (
            collect_ai_trade_details,
            collect_ai_trade_rows,
            collect_position_ownership,
            collect_trade_groups,
            ea_position_ids_from_lifecycle,
            fallback_ai_trade_rows_from_lifecycle,
            fallback_ownership_from_lifecycle,
            fallback_trade_groups_from_lifecycle,
        )
        from tools.review_core import collect_ai_candidate_rows

        payload["ai_candidate_rows"] = collect_ai_candidate_rows(
            root, server_open, server_close
        )
        ea_position_ids = ea_position_ids_from_lifecycle(
            payload.get("actual_trade_rows") or []
        )
        payload["trade_groups"] = collect_trade_groups(
            root, config, server_open, server_close,
            ea_position_ids=ea_position_ids,
        )
        payload["trade_ownership"] = {
            str(position_id): group
            for position_id, group in collect_position_ownership(
                root, server_open, server_close, config,
                ea_position_ids=ea_position_ids,
            ).items()
        }
        payload["ai_trade_details"] = collect_ai_trade_details(
            root, server_open, server_close
        )
        payload["ai_trade_rows"] = collect_ai_trade_rows(
            root, server_open, server_close, config
        )
        # MT5 历史读取失败时，用生命周期 CSV 兜底，避免三组对照卡误报无交易。
        actual_rows = payload.get("actual_trade_rows") or []
        groups_empty = not any(
            int((payload.get("trade_groups") or {}).get(key, {}).get("count", 0))
            for key in ("ea", "ai", "manual")
        )
        if groups_empty and actual_rows:
            payload["trade_groups"] = fallback_trade_groups_from_lifecycle(
                actual_rows, config
            )
            payload["trade_ownership"] = fallback_ownership_from_lifecycle(
                actual_rows, config
            )
            payload["ai_trade_rows"] = fallback_ai_trade_rows_from_lifecycle(actual_rows)
        from tools.trade_review import build_trade_review_facts

        payload["actual_trade_rows"] = enrich_trade_mfe_mae(
            payload.get("actual_trade_rows") or [], TERMINAL_PATH, "XAUUSD.s"
        )
        payload["ai_trade_rows"] = enrich_trade_mfe_mae(
            payload.get("ai_trade_rows") or [], TERMINAL_PATH, "XAUUSD.s"
        )
        payload["trade_review_facts"] = build_trade_review_facts(payload, root)
    except Exception:
        payload["ai_candidate_rows"] = []
        payload["trade_groups"] = {}
        payload["trade_ownership"] = {}
        payload["ai_trade_details"] = []
        payload["ai_trade_rows"] = []
        payload["trade_review_facts"] = []

    # 三组点评必须基于修正后的 trade_groups 重新生成，不能沿用旧分类污染。
    response["trade_group_analysis"] = ""
    response = apply_local_daily_facts(response, payload)
    # 三组对照逐笔深度点评（DeepSeek Pro，只读事实；失败则回退确定性摘要）。
    response["trade_review"] = None
    try:
        from tools.ai_review_service import DeepSeekJsonClient, load_api_key, prompt_text
        from tools.trade_review import generate_trade_review

        api_key = load_api_key(config, root)
        facts = payload.get("trade_review_facts") or []
        if api_key and facts:
            client = DeepSeekJsonClient(
                api_key,
                str(config.get("daily_review_model", "deepseek-v4-pro")),
                float(config.get("timeout_seconds", 20)),
                int(config.get("retries", 1)),
            )
            prompt = prompt_text(
                str(config.get("trade_review_prompt_file", "trade_review_prompt.txt")),
                config_path,
            )
            response["trade_review"], _ = generate_trade_review(client, prompt, facts)
    except Exception as exc:
        response["trade_review"] = None
    response["issue_judgment"] = str(response.get("issue_judgment") or "")
    response["conclusion_summary"] = str(response.get("conclusion_summary") or "")
    response["trade_statistics"] = dict(payload["statistics"])
    response["block_reason_counts"] = dict(payload["statistics"]["block_reason_counts"])
    candidates = list(payload.get("candidate_rows") or [])
    allowed = sum(row.get("ai_status") == "allow" for row in candidates)
    rejected = sum(row.get("ai_status") == "reject" for row in candidates)
    error = sum(row.get("ai_status") == "error" for row in candidates)
    assessment_parts = [f"AI同意开单{allowed}个、拒绝{rejected}个"]
    if error:
        assessment_parts.append(f"异常降级放行{error}个")
    response["ai_filter_assessment"] = (
        f"今天形成的{len(candidates)}个正式候选，"
        f"{'、'.join(assessment_parts)}；"
        "每个候选最终是成交还是未成交，请看第2部分“正式候选明细”里的说明。"
    )

    output.mkdir(parents=True, exist_ok=True)
    data_out = output / f"Daily_Data_{day}.json"
    csv_out = output / f"Daily_Data_{day}.csv"
    response_out = output / f"Daily_Review_{day}.json"
    markdown_out = output / f"Daily_Review_{day}.md"
    candidates_markdown_out = output / f"Daily_Candidates_{day}.md"
    parallel_ai_markdown_out = output / f"Parallel_AI_{day}.md"
    comparison_markdown_out = output / f"Comparison_{day}.md"
    html_out = output / f"Daily_Review_{day}.html"
    candidates_html_out = output / f"Daily_Candidates_{day}.html"
    parallel_ai_html_out = output / f"Parallel_AI_{day}.html"
    comparison_html_out = output / f"Comparison_{day}.html"
    atomic_write_json(data_out, payload)
    flattened = {
        "review_date": day,
        **dict(payload["statistics"]),
        **{
            f"block_{key}": value
            for key, value in dict(payload["statistics"]["block_reason_counts"]).items()
        },
        "data_status": response.get("data_status", ""),
        "ea_runtime_status": response.get("ea_runtime_status", ""),
    }
    flattened.pop("block_reason_counts", None)
    with csv_out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flattened))
        writer.writeheader()
        writer.writerow(flattened)
    atomic_write_json(response_out, response)
    atomic_write_text(
        markdown_out,
        render_daily_review_markdown(day, payload, response, {"monitor": 5, "daily": 1}),
    )
    atomic_write_text(
        candidates_markdown_out,
        render_candidate_card_markdown(
            day, payload, {"monitor": 5, "daily": 1}
        ),
    )
    atomic_write_text(
        parallel_ai_markdown_out,
        build_parallel_ai_card_markdown(day, payload, response),
    )
    atomic_write_text(
        comparison_markdown_out,
        build_comparison_card_markdown(day, payload, response),
    )
    card = build_daily_lark_card(day, payload, response, {"monitor": 5, "daily": 1})
    atomic_write_text(html_out, render_card_html(card))
    candidate_card = build_candidate_lark_card(
        day, payload, response, {"monitor": 5, "daily": 1}
    )
    atomic_write_text(candidates_html_out, render_card_html(candidate_card))
    atomic_write_text(
        parallel_ai_html_out,
        render_card_html(build_parallel_ai_lark_card(day, payload, response)),
    )
    atomic_write_text(
        comparison_html_out,
        render_card_html(build_comparison_lark_card(day, payload, response)),
    )
    return {
        "day": day,
        "output": str(output.resolve()),
        "candidate_count": len(candidates),
        "trade_count": payload["statistics"]["trade_count"],
        "net_profit": payload["statistics"]["net_profit"],
        "lark_send": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild a saved daily review locally without queuing or sending Lark"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = rebuild(args.config.resolve(), args.day, args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Lark发送：禁用（离线重建）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
