"""Compact, plain-language Lark cards for meaningful parallel-audit differences."""

from __future__ import annotations

import re
from typing import Any

from .parallel_ai_calculator import gate_cn


TITLE_MAP = {
    "CALCULATION_ANOMALY": "计算结果异常",
    "LOCAL_ENTRY_DISAGREEMENT": "开仓条件分歧",
    "AI_REVIEW_DISAGREEMENT": "EA审核分歧",
    "DIRECTION_DISAGREEMENT": "交易方向分歧",
    "PLAN_DISAGREEMENT": "价格计划分歧",
    "EXECUTION_SUSPECT": "疑似执行漏单",
    "PENDING_FILL_ANOMALY": "挂单成交异常",
    "DATA_SYNC_ERROR": "数据未对齐",
    "UNCOMPARABLE": "本轮无法比较",
}


def _difference_cn(difference_id: str) -> str:
    """把内部分歧编号翻成中文大白话，编号只作为辅助。"""
    value = str(difference_id or "").strip().upper()
    plain = {
        "ACTION": "是否开仓",
        "DIRECTION": "交易方向",
        "ROUTE": "入场路径",
        "ENTRY": "计划入场价",
        "SL": "计划止损",
        "TP": "计划止盈",
    }
    if value.startswith("G") and value in gate_cn(value):
        return gate_cn(value)
    return plain.get(value, value or "结果")


def fallback_short_explanation(comparison: dict[str, Any]) -> dict[str, str]:
    classification = str(comparison.get("classification", ""))
    difference = str(comparison.get("difference_id", "")) or "结果"
    difference_cn = _difference_cn(difference)
    conclusions = {
        "CALCULATION_ANOMALY": "同样行情算出了不同数值",
        "LOCAL_ENTRY_DISAGREEMENT": "一边想开仓，另一边选择等待",
        "AI_REVIEW_DISAGREEMENT": "条件已满足，但EA审核没有放行",
        "DIRECTION_DISAGREEMENT": "双方给出的交易方向相反",
        "PLAN_DISAGREEMENT": "方向相同，但入场或止损价格不同",
        "EXECUTION_SUSPECT": "EA本应继续挂单，但没有挂出",
        "PENDING_FILL_ANOMALY": "价格碰到挂单，却没有成交记录",
    }
    reason = str(comparison.get("reason", "")).strip() or "双方在同一项规则上得到不同结果"
    return {
        "conclusion": conclusions.get(classification, "本轮结果需要人工查看"),
        "reason": reason[:80],
        "difference": f"{difference_cn}：两边判断结果不同"[:80],
    }


def _explanation_fields(explanation: dict[str, str] | str) -> dict[str, str]:
    if isinstance(explanation, dict):
        return {key: str(explanation.get(key, "")).strip() for key in ("conclusion", "reason", "difference")}
    fields = {"conclusion": "", "reason": "", "difference": ""}
    for line in str(explanation).splitlines():
        for label, key in (("结论：", "conclusion"), ("原因：", "reason"), ("差异：", "difference")):
            if line.startswith(label):
                fields[key] = line[len(label):].strip()
    return fields


def build_parallel_ai_card(
    comparison: dict[str, Any], explanation: dict[str, str] | str
) -> dict[str, Any]:
    classification = str(comparison.get("classification", ""))
    title = TITLE_MAP.get(classification, "平行判断提醒")
    fields = _explanation_fields(explanation)
    if not all(fields.values()):
        fields = fallback_short_explanation(comparison)
    snapshot = str(comparison.get("snapshot_id", ""))
    difference_id = str(comparison.get("difference_id", ""))
    difference_cn = _difference_cn(difference_id)
    content = (
        f"**结论：** {fields['conclusion']}\n"
        f"**原因：** {fields['reason']}\n"
        f"**分歧点：** {fields['difference']}\n\n"
        f"**M5：** {snapshot}\n"
        f"**分歧项：** {difference_cn}\n\n"
        "只读对比，不会自动下单。"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": "orange" if "DISAGREEMENT" in classification else "red",
                "title": {"tag": "plain_text", "content": f"{title}｜XAUUSD.s M5"},
            },
            "body": {"elements": [{"tag": "markdown", "content": content}]},
        },
    }


def notification_id_for_comparison(comparison: dict[str, Any]) -> str:
    material = "_".join((
        "PARALLEL_AI_V2",
        str(comparison.get("snapshot_id", "")),
        str(comparison.get("classification", "")),
        str(comparison.get("difference_id", "")) or "NONE",
    ))
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", material)[:220]
