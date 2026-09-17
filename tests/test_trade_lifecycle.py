from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.trade_lifecycle import (
    aggregate_position,
    build_open_trade_explanation,
    fallback_commentary,
    render_trade_card,
    trades_for_server_window,
    validate_commentary,
)


def _open_trade() -> dict[str, object]:
    return {
        "position_id": "720560839",
        "direction": "SELL",
        "open_time": "2026.08.11 19:50:34",
        "entry_price": 4382.05,
        "initial_volume": 0.12,
        "initial_sl": 4386.14,
        "initial_risk": 49.08,
        "tp1_price": 4377.96,
        "tp2_price": 4373.87,
        "signal_route": "BOTH",
        "context_loaded": True,
        "fib_retracement": 56.0351,
        "h_attempt": 2,
        "ema_distance_usd": 2.6093,
        "ma_confluence": True,
        "pattern": "Engulfing + Strong Reversal",
        "ai_allow_trade": True,
        "ai_confidence": 76,
    }


@pytest.mark.parametrize(
    ("route", "direction", "expected"),
    [
        ("FIB_PA", "BUY", "Fib + PA 路径"),
        ("EMA_H23", "BUY", "EMA H2/H3 路径"),
        ("EMA_L23", "SELL", "EMA L2/L3 路径"),
        ("BOTH", "BUY", "Fib + PA 与 EMA H2/H3 双路径"),
        ("BOTH", "SELL", "Fib + PA 与 EMA L2/L3 双路径"),
        ("UNKNOWN", "SELL", "已通过EA候选条件（路径记录缺失）"),
    ],
)
def test_open_explanation_maps_actual_route(
    route: str, direction: str, expected: str
) -> None:
    result = build_open_trade_explanation(
        {"signal_route": route, "direction": direction}
    )
    assert result["route"] == expected


def test_open_explanation_uses_candidate_facts_without_inventing_gates() -> None:
    result = build_open_trade_explanation(_open_trade())

    assert "SELL方向的趋势与结构前置条件已通过" in result["reason"]
    assert "Fib回调56.0%" in result["reason"]
    assert "EMA20附近" in result["reason"]
    assert "L2" in result["reason"]
    assert "吞没与强反转确认" in result["reason"]
    assert "止损及1R/2R目标完整" in result["reason"]
    assert "DeepSeek审核允许" in result["reason"]
    assert "点差通过" not in result["reason"]
    assert "0.8R通过" not in result["reason"]


def _row(
    event_id: str,
    event_kind: str,
    stage: str,
    profit: float,
    *,
    commission: float = 0.0,
    swap: float = 0.0,
    fee: float = 0.0,
    server_time: str = "2026-08-10 12:00:00",
) -> dict[str, str]:
    return {
        "event_id": event_id,
        "server_time": server_time,
        "event_kind": event_kind,
        "stage": stage,
        "position_id": "718041351",
        "deal_ticket": event_id,
        "signal_id": "S1",
        "direction": "SELL",
        "volume": "0.01",
        "price": "4415.34",
        "profit": str(profit),
        "commission": str(commission),
        "swap": str(swap),
        "fee": str(fee),
        "initial_volume": "0.07",
        "initial_sl": "4422.03",
        "initial_risk": "46.83",
        "tp1_price": "4408.65",
        "tp2_price": "4401.96",
        "tp1_done": "1" if stage in {"TP1", "TP2", "RUNNER"} else "0",
        "tp2_done": "1" if stage in {"TP2", "RUNNER"} else "0",
        "runner_active": "1" if stage == "RUNNER" else "0",
        "close_reason": "",
    }


def test_aggregate_position_sums_all_deals_costs_and_stages():
    rows = [
        _row("100", "OPEN", "OPEN", 0.0),
        _row("101", "EXIT", "TP1", 18.90, commission=-0.50),
        _row("102", "EXIT", "TP2", 17.30, commission=-0.50),
        _row("103", "EXIT", "RUNNER", 18.10, commission=-0.50, swap=-0.50),
    ]

    trade = aggregate_position("718041351", rows, fallback={"status": "closed"})

    assert trade["stage_net"] == {
        "TP1": 18.40,
        "TP2": 16.80,
        "RUNNER": 17.10,
        "OTHER": 0.0,
    }
    assert trade["net_profit"] == 52.30
    assert trade["final_r"] == 1.12


def test_aggregate_position_deduplicates_replayed_deal_event():
    exit_row = _row("101", "EXIT", "TP1", 20.0)
    trade = aggregate_position(
        "718041351", [exit_row, dict(exit_row)], fallback={"status": "open"}
    )
    assert trade["net_profit"] == 20.0
    assert trade["stage_net"]["TP1"] == 20.0


def test_final_card_survives_missing_initial_risk():
    trade = aggregate_position(
        "718041351",
        [_row("101", "EXIT", "INITIAL_EXIT", -46.83)],
        {
            "status": "closed",
            "direction": "SELL",
            "initial_risk": 0.0,
            "open_time": "2026-08-11 06:46:00",
            "close_time": "2026-08-11 07:03:00",
            "entry_price": 4415.34,
            "initial_volume": 0.07,
        },
    )
    trade["initial_risk"] = 0.0
    trade["final_r"] = None

    card = render_trade_card(
        "final_trade_facts",
        trade,
        {"reason": "价格触发初始止损，仓位已经全部结束。", "comment": "本单按原计划止损结束。"},
    )
    rendered = json.dumps(card, ensure_ascii=False)

    assert "最终R：无法计算" in rendered
    assert "总净盈亏" in rendered
    assert "本单结束：✅" in rendered


def test_trade_card_templates_use_business_titles_without_security_keyword():
    trade = {
        "position_id": "718041351",
        "direction": "BUY",
        "open_time": "2026-08-11 06:46:00",
        "close_time": "2026-08-11 07:03:00",
        "entry_price": 4415.34,
        "initial_volume": 0.07,
        "initial_sl": 4408.65,
        "initial_risk": 46.83,
        "tp1_price": 4422.03,
        "tp2_price": 4428.72,
        "net_profit": -46.83,
        "final_r": -1.0,
    }
    commentary = {"reason": "交易条件满足。", "comment": "按计划执行。"}

    open_card = render_trade_card("open_trade_facts", trade, commentary)
    final_card = render_trade_card("final_trade_facts", trade, commentary)

    assert open_card["card"]["header"]["title"]["content"] == (
        "🔵 交易#718041351｜BUY｜开仓"
    )
    assert open_card["card"]["body"]["elements"][0]["content"].startswith(
        "**开仓通知**"
    )
    assert final_card["card"]["header"]["title"]["content"] == (
        "🔴 交易#718041351｜BUY｜最终结算"
    )
    assert final_card["card"]["body"]["elements"][0]["content"].startswith(
        "**最终结算**"
    )


def test_open_card_shows_r_money_and_ignores_ai_generated_reason() -> None:
    card = render_trade_card(
        "open_trade_facts",
        _open_trade(),
        {
            "reason": "AI泛泛地说下跌趋势和反转信号。",
            "comment": "双路径确认后顺势做空，风险安排清楚。",
        },
    )
    text = card["card"]["body"]["elements"][0]["content"]

    assert "初始风险 49.08 USD（1R）" in text
    assert "TP1：4377.96｜1R ≈ 49.08 USD" in text
    assert "TP2：4373.87｜2R ≈ 98.16 USD" in text
    assert "入场路径：** Fib + PA 与 EMA L2/L3 双路径" in text
    assert "Fib回调56.0%" in text
    assert "EMA20附近并形成L2" in text
    assert "AI泛泛地说" not in text
    assert "双路径确认后顺势做空" in text


def test_open_card_survives_unavailable_risk_without_inventing_money() -> None:
    trade = _open_trade()
    trade["initial_risk"] = 0
    card = render_trade_card(
        "open_trade_facts",
        trade,
        {"reason": "输入中的AI理由。", "comment": "按既定条件执行。"},
    )
    text = card["card"]["body"]["elements"][0]["content"]

    assert "初始风险无法计算" in text
    assert "TP1：4377.96｜1R" in text
    assert "TP2：4373.87｜2R" in text
    assert "1R ≈ 0.00 USD" not in text
    assert "2R ≈ 0.00 USD" not in text


def test_commentary_validation_and_fallback_are_plain_and_bounded():
    value = validate_commentary(
        {"reason": "价格处于下跌趋势，回调后出现空头反转确认。", "comment": "条件满足后按计划开仓。"}
    )
    assert value["reason"].startswith("价格处于")
    with pytest.raises(ValueError, match="exact reason/comment"):
        validate_commentary({"reason": "x", "comment": "y", "extra": "z"})

    fallback = fallback_commentary(
        "open_trade_facts",
        {"direction": "SELL", "trend": "down", "fib_retracement": 50.0},
    )
    assert fallback["reason"]
    assert fallback["comment"]


def test_open_preview_script_renders_saved_facts_without_sending(
    tmp_path: Path,
) -> None:
    input_json = tmp_path / "720560839_OPEN.json"
    output_html = tmp_path / "preview.html"
    input_json.write_text(
        json.dumps(
            {
                "notification_type": "open_trade_facts",
                "aggregated_trade": _open_trade(),
                "commentary": {
                    "reason": "旧AI泛化理由。",
                    "comment": "双路径确认后顺势做空，风险安排清楚。",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "tools/render_open_trade_preview.py",
            "--input",
            str(input_json),
            "--output",
            str(output_html),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    text = output_html.read_text(encoding="utf-8")
    assert "1R ≈ 49.08 USD" in text
    assert "2R ≈ 98.16 USD" in text
    assert "Fib + PA 与 EMA L2/L3 双路径" in text
    assert text.count("安全校验：EA复盘") == 1


def test_trades_for_server_window_includes_cross_day_close(tmp_path: Path):
    lifecycle = tmp_path / "Trade_Lifecycle"
    lifecycle.mkdir()
    header = ";".join(_row("0", "OPEN", "OPEN", 0.0).keys())
    rows = [
        _row("100", "OPEN", "OPEN", 0.0, server_time="2026-08-10 20:50:16"),
        _row("101", "EXIT", "TP1", 45.33, server_time="2026-08-10 22:05:55"),
        _row("102", "EXIT", "RUNNER", 6.97, server_time="2026-08-11 02:31:21"),
    ]
    content = [header]
    for row in rows:
        content.append(";".join(row[key] for key in row))
    (lifecycle / "Position_718041351.csv").write_text(
        "\n".join(content) + "\n", encoding="utf-8-sig"
    )

    trades = trades_for_server_window(
        tmp_path, "2026-08-11 00:00:00", "2026-08-11 23:59:59"
    )

    assert len(trades) == 1
    assert trades[0]["position_id"] == "718041351"
    assert trades[0]["today_net"] == 6.97
    assert trades[0]["whole_trade_net"] == 52.30
    assert trades[0]["status"] == "closed_cross_day"
