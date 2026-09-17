import json

import pytest

from tools.parallel_ai_cards import (
    build_parallel_ai_card,
    fallback_short_explanation,
    notification_id_for_comparison,
)


def comparison(classification="LOCAL_ENTRY_DISAGREEMENT", difference_id="G05"):
    return {
        "classification": classification, "difference_id": difference_id,
        "snapshot_id": "XAUUSD.s_M5_20260812_1035",
        "reason": "独立AI认为回调有效，EA认为回调失效",
        "details": {"ai_gate": "PASS", "ea_gate": "FAIL"},
        "ai": {"action": "OPEN", "direction": "SELL"},
        "ea": {"action": "WAIT", "direction": "NONE"},
    }


def markdown(card):
    return "\n".join(element["content"] for element in card["card"]["body"]["elements"] if element["tag"] == "markdown")


def test_local_entry_disagreement_card_is_short_and_readable():
    value = comparison()
    card = build_parallel_ai_card(value, fallback_short_explanation(value))
    text = json.dumps(card, ensure_ascii=False)
    assert all(label in text for label in ("结论：", "原因：", "分歧点："))
    assert "G05" in text
    assert "只读对比，不会自动下单" in text
    assert len(markdown(card)) <= 420
    assert not card["card"]["header"]["title"]["content"].startswith("EA复盘")


@pytest.mark.parametrize("classification,title", [
    ("CALCULATION_ANOMALY", "计算结果异常"),
    ("LOCAL_ENTRY_DISAGREEMENT", "开仓条件分歧"),
    ("AI_REVIEW_DISAGREEMENT", "EA审核分歧"),
    ("DIRECTION_DISAGREEMENT", "交易方向分歧"),
    ("PLAN_DISAGREEMENT", "价格计划分歧"),
    ("EXECUTION_SUSPECT", "疑似执行漏单"),
    ("PENDING_FILL_ANOMALY", "挂单成交异常"),
])
def test_titles_name_the_actual_alert(classification, title):
    value = comparison(classification, "G06")
    card = build_parallel_ai_card(value, fallback_short_explanation(value))
    assert title in card["card"]["header"]["title"]["content"]


def test_notification_identity_is_stable_and_class_specific():
    first = notification_id_for_comparison(comparison())
    assert first == notification_id_for_comparison(comparison())
    assert first != notification_id_for_comparison(comparison("PLAN_DISAGREEMENT", "ENTRY"))
