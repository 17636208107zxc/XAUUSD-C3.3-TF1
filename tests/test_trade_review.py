import pytest

from tools.trade_review import (
    build_trade_review_facts,
    validate_trade_review,
)


def _payload():
    return {
        "trade_ownership": {
            "100": "ea",
            "200": "ai",
            "300": "e2e",
            "400": "unknown",
        },
        "actual_trade_rows": [
            {
                "position_id": "100", "magic": "2026072901", "status": "closed",
                "direction": "BUY", "entry_price": 100.0, "initial_sl": 98.0,
                "initial_risk": 20.0, "tp1_price": 102.0, "tp2_price": 104.0,
                "whole_trade_net": 20.0, "final_r": 1.0, "close_reason": "DEAL_REASON_TP",
                "open_time": "2026.08.19 10:00:00", "close_time": "2026.08.19 11:00:00",
                "initial_volume": 0.1, "tp1_done": True, "tp2_done": False,
                "runner_done": False,
            },
            # E2E test trade must be excluded
            {
                "position_id": "300", "magic": "2026072903", "status": "closed",
                "direction": "SELL", "entry_price": 100.0, "whole_trade_net": 1.0,
            },
        ],
        "ai_trade_rows": [
            {
                "position_id": "200", "magic": "2026072902", "status": "closed",
                "direction": "SELL", "route": "FIB_PA", "entry_price": 100.0,
                "initial_sl": 102.0, "initial_risk": 20.0, "tp1_price": 98.0,
                "tp2_price": 96.0, "whole_trade_net": -20.0, "final_r": -1.0,
                "close_reason": "DEAL_REASON_SL",
                "open_time": "2026.08.19 12:00:00", "close_time": "2026.08.19 13:00:00",
            },
        ],
        "candidate_rows": [],
        "ai_candidate_rows": [],
    }


def test_build_trade_review_facts_excludes_e2e_and_unknown():
    facts = build_trade_review_facts(_payload(), root=None)
    ids = [f["position_id"] for f in facts]
    assert "100" in ids  # EA
    assert "200" in ids  # AI
    assert "300" not in ids  # E2E excluded
    assert "400" not in ids  # unknown excluded
    assert len(facts) == 2


def test_build_trade_review_facts_marks_ai_rule_unavailable_without_root():
    facts = build_trade_review_facts(_payload(), root=None)
    ai = next(f for f in facts if f["position_id"] == "200")
    assert ai["rule_compliance"]["status"] == "unavailable"


def _valid_review(facts):
    reviews = [{"position_id": f["position_id"], "review": "test review"} for f in facts]
    return {
        "per_trade_reviews": reviews,
        "group_ea": "ea summary",
        "group_ai": "ai summary",
        "group_manual": "无",
        "overall": "overall",
        "insights_supported": "无",
        "insights_observe": "无",
        "insights_avoid": "无",
    }


def test_validate_trade_review_accepts_valid():
    facts = build_trade_review_facts(_payload(), root=None)
    review = _valid_review(facts)
    assert validate_trade_review(review, facts) == review


def test_validate_trade_review_rejects_wrong_count():
    facts = build_trade_review_facts(_payload(), root=None)
    review = _valid_review(facts)
    review["per_trade_reviews"] = review["per_trade_reviews"][:1]
    with pytest.raises(ValueError):
        validate_trade_review(review, facts)


def test_validate_trade_review_rejects_unknown_position_id():
    facts = build_trade_review_facts(_payload(), root=None)
    review = _valid_review(facts)
    review["per_trade_reviews"][0]["position_id"] = "999999"
    with pytest.raises(ValueError):
        validate_trade_review(review, facts)
