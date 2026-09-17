from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from tools.rebuild_daily_review_offline import enrich_legacy_trade_notifications


def _inventory(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_legacy_open_notification_restores_frozen_risk_without_modifying_source(tmp_path: Path):
    sent = tmp_path / "Lark_Outbox" / "Sent"
    sent.mkdir(parents=True)
    source = sent / "OPEN_718041351.json"
    source.write_text(
        json.dumps(
            {
                "card": {
                    "card": {
                        "body": {
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": (
                                        "**初始SL：** 4422.03\n"
                                        "**预计最大风险：** 46.83 USD\n"
                                        "**1R目标：** 4408.65\n"
                                        "**2R目标：** 4401.96"
                                    ),
                                }
                            ]
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()
    rows = [
        {
            "position_id": "718041351",
            "data_source": "legacy_event",
            "whole_trade_net": 179.49,
            "initial_risk": 0,
        }
    ]

    enrich_legacy_trade_notifications(tmp_path, rows)

    assert rows[0]["initial_sl"] == 4422.03
    assert rows[0]["initial_risk"] == 46.83
    assert rows[0]["tp1_price"] == 4408.65
    assert rows[0]["tp2_price"] == 4401.96
    assert rows[0]["final_r"] == 3.83
    assert rows[0]["data_source"] == "legacy_event_notification"
    assert source.read_bytes() == before


def test_offline_rebuild_writes_preview_without_touching_lark_outbox(tmp_path: Path):
    root = tmp_path / "account"
    raw = root / "Raw_Data"
    raw.mkdir(parents=True)
    (raw / "Market_Snapshots_2026-08-11.csv").write_text(
        "server_time,beijing_time,m5_open,m5_high,m5_low,m5_close,m5_atr14\n"
        "2026-08-11 01:02:00,2026-08-11 06:02:00,4389.97,4391,4388,4390,4\n"
        "2026-08-11 23:58:00,2026-08-12 04:58:00,4368,4370,4366,4367.80,4\n",
        encoding="utf-8-sig",
    )
    (raw / "Strategy_Events_2026-08-11.csv").write_text(
        "server_time,beijing_time,event_type,signal_id,stage,outcome,reason,details,order_ticket,position_id,deal_ticket,direction,volume,price,profit\n"
        "2026-08-11 06:45:00,2026-08-11 11:45:00,candidate,S1,candidate,,本地候选已形成,"
        '"{""signal_route"":""FIB_PA"",""trade_plan"":{""entry"":4415.4,""sl"":4422.03,""tp1"":4408.01},""bars"":[{""time"":""2026.08.11 06:40:00""}]}",0,0,0,SELL,0,4415.4,0\n'
        "2026-08-11 06:45:01,2026-08-11 11:45:01,ai_reject,S1,ai,,证据不足,allow=false | confidence=62 | threshold=70,0,0,0,SELL,0,4415.4,0\n",
        encoding="utf-8-sig",
    )
    saved = root / "Daily_Review" / "2026-08-11"
    saved.mkdir(parents=True)
    (saved / "Daily_Data_2026-08-11.json").write_text(
        json.dumps(
            {
                "session_window": {
                    "server_open": "2026.08.11 01:02:00",
                    "server_close": "2026.08.11 23:58:00",
                    "used_fallback": False,
                },
                "program_issues": [],
                "strategy_issues": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (saved / "Daily_Review_2026-08-11.json").write_text(
        json.dumps(
            {
                "market_summary": "先涨后跌。尾段震荡。",
                "market_regime": "先趋势后震荡",
                "ai_filter_assessment": "存在1个AI拒绝样本。",
                "trade_statistics": {},
                "block_reason_counts": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    outbox = root / "Lark_Outbox"
    for bucket in ("Pending", "Sent", "Uncertain"):
        path = outbox / bucket / f"sentinel-{bucket}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"sentinel":true}', encoding="utf-8")
    before = _inventory(outbox)

    config = tmp_path / "review_config.json"
    config.write_text(json.dumps({"data_root": str(root)}), encoding="utf-8")
    output = tmp_path / "preview"
    result = subprocess.run(
        [
            sys.executable,
            "tools/rebuild_daily_review_offline.py",
            "--config",
            str(config),
            "--day",
            "2026-08-11",
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (output / "Daily_Data_2026-08-11.json").exists()
    assert (output / "Daily_Data_2026-08-11.csv").exists()
    assert (output / "Daily_Review_2026-08-11.json").exists()
    assert (output / "Daily_Review_2026-08-11.md").exists()
    assert (output / "Daily_Review_2026-08-11.html").exists()
    assert (output / "Daily_Candidates_2026-08-11.md").exists()
    assert (output / "Daily_Candidates_2026-08-11.html").exists()
    assert "候选订单明细" in (output / "Daily_Candidates_2026-08-11.md").read_text(
        encoding="utf-8"
    )
    assert _inventory(outbox) == before
    assert "Lark发送：禁用（离线重建）" in result.stdout
