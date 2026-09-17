"""每日「行情回顾 + 开单准确性」事实层测试。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.daily_market_review import (
    build_market_review,
    classify_trend,
    humanize_reason,
    render_market_review_lines,
)


def _write_snapshots(root: Path, day: str, closes: list[float]) -> None:
    folder = root / "Raw_Data"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Market_Snapshots_{day}.csv"
    fields = [
        "server_time",
        "beijing_time",
        "m5_time",
        "m5_open",
        "m5_high",
        "m5_low",
        "m5_close",
        "last_scan_stage",
        "last_scan_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, close in enumerate(closes):
            hour = 9 + index // 12
            when = f"{day} {hour:02d}:{(index * 5) % 60:02d}:00"
            writer.writerow(
                {
                    "server_time": when,
                    "beijing_time": when,
                    "m5_time": when,
                    "m5_open": close - 1,
                    "m5_high": close + 1,
                    "m5_low": close - 1,
                    "m5_close": close,
                    "last_scan_stage": "trend",
                    "last_scan_reason": "M5未形成候选 | H=H2 | H2_SIGNAL_INVALID",
                }
            )


def _write_records(root: Path, day: str, rows: list[dict]) -> None:
    folder = root / "Parallel_AI_V2" / "Records"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Comparisons_{day}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_humanize_reason_never_leaks_internal_codes():
    text = humanize_reason("M15定方向未放行 | C21_BLOCK_BUY_PRIMARY_BEAR")
    assert "未放行做多" in text
    for code in ("C21", "PULLBACK", "PRIMARY_BEAR", "SIGNAL_INVALID", "_"):
        assert code not in text


def test_classify_trend_returns_documented_labels():
    strong = classify_trend(
        {
            "available": True,
            "direction": "上涨",
            "net_move_usd": 60.0,
            "net_move_atr": 6.0,
            "net_over_range": 0.8,
            "direction_efficiency": 0.7,
            "same_direction_ratio": 0.7,
            "max_pullback_usd": 10.0,
            "max_pullback_ratio": 0.17,
            "m5_atr14": 10.0,
        }
    )
    assert strong["label"] == "强趋势"
    ranging = classify_trend(
        {
            "available": True,
            "direction": "下跌",
            "net_move_usd": 3.0,
            "net_move_atr": 0.3,
            "net_over_range": 0.1,
            "direction_efficiency": 0.05,
            "same_direction_ratio": 0.5,
            "max_pullback_usd": 40.0,
            "max_pullback_ratio": 2.0,
            "m5_atr14": 10.0,
        }
    )
    assert ranging["label"] == "区间震荡"
    assert classify_trend({"available": False})["label"] == "数据不足"


def test_build_market_review_marks_opened_and_missed(tmp_path: Path):
    day = "2026-09-14"
    _write_snapshots(tmp_path, day, [100 + index * 0.5 for index in range(40)])
    _write_records(
        tmp_path,
        day,
        [
            {
                "snapshot_id": "XAUUSD.s_M5_20260914_0910",
                "m5_time": "2026.09.14 09:10:00",
                "reference_decision": {
                    "action": "OPEN",
                    "direction": "BUY",
                    "route": "FIB_PA",
                    "entry": 120.0,
                    "sl": 118.0,
                    "tp1": 126.0,
                    "rr_to_tp1": 3.0,
                },
                "ea": {"action": "WAIT"},
                "ai": {"action": "WAIT"},
                "rule_audit": {"difference_id": "G06", "reason": "Reference与EA在G06不一致"},
            },
            {
                "snapshot_id": "XAUUSD.s_M5_20260914_0935",
                "m5_time": "2026.09.14 09:35:00",
                "reference_decision": {
                    "action": "OPEN",
                    "direction": "SELL",
                    "route": "EMA_L23",
                    "entry": 118.0,
                    "sl": 121.0,
                    "tp1": 112.0,
                    "rr_to_tp1": 2.0,
                },
                "ea": {"action": "OPEN"},
                "ai": {"action": "OPEN"},
            },
            {
                "snapshot_id": "XAUUSD.s_M5_20260914_1000",
                "m5_time": "2026.09.14 10:00:00",
                "reference_decision": {"action": "WAIT"},
                "ea": {"action": "WAIT"},
                "ai": {"action": "WAIT"},
            },
        ],
    )
    review = build_market_review(tmp_path, day)
    assert review["opportunity_total"] == 2
    assert review["opportunity_opened"] == 1
    assert review["opportunity_missed"] == 1
    missed = [item for item in review["opportunities"] if not item["ea_opened"]][0]
    assert missed["status"] == "未开出"
    assert "入场路径" in missed["reason"]
    assert "G06" not in missed["reason"]
    opened = [item for item in review["opportunities"] if item["ea_opened"]][0]
    assert opened["status"] == "已开出"
    assert opened["reason"] == ""

    body = "\n".join(render_market_review_lines(review))
    assert "## 2. 今日行情回顾与开单准确性" in body
    assert "该开单的位置" in body
    assert "未开出" in body
    assert "已按规则开出" in body
    # 全天口径与“最近这波”口径必须同时给出
    assert "最近这波" in body
    assert review["trend_recent"]["label"] in {"强趋势", "弱势趋势", "区间震荡", "数据不足"}


def test_market_review_distinguishes_scanned_opportunities_from_actual_trades():
    review = {
        "metrics": {"available": True},
        "trend": {"label": "区间震荡", "reason": "路径效率较低", "rule": "仅统计可验证的机会"},
        "opportunity_total": 0,
        "opportunity_opened": 0,
        "opportunity_missed": 0,
        "opportunities": [],
    }

    body = "\n".join(render_market_review_lines(review, actual_trade_count=2))

    assert "EA当日实际开单：** 2笔" in body
    assert "当前扫描未识别可验证的规则应开位置" in body
    assert "其中EA已开出 0 处" not in body
    assert "实际开出 0 处" not in body
    assert "当日没有出现“规则上应该开单”的位置" not in body
