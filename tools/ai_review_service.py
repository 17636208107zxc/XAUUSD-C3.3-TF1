from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from tools.trade_lifecycle import (
    aggregate_position,
    enrich_close_reason_from_mt5,
    enrich_trade_mfe_mae,
    fallback_commentary,
    load_position_lifecycle,
    render_trade_card,
    trades_for_server_window,
    validate_commentary,
)
from tools.ai_trade_manager import TERMINAL_PATH

# 默认 MT5 终端（V3.9.20 用的那个）；配置里给了 trade_terminal_path 时才改绑。
_DEFAULT_TRADE_TERMINAL = TERMINAL_PATH
from tools.independent_ai_observer import process_independent_ai_cycle
from tools.parallel_ai_auditor import process_parallel_ai_cycle
from tools.four_hour_review import (
    build_four_hour_review,
    build_four_hour_pro_payload,
    due_full_window,
    render_four_hour_card,
    session_four_hour_windows,
    validate_four_hour_pro_analysis,
)
from tools.daily_review_renderer import (
    build_comparison_card_markdown,
    build_daily_review_sections,
    build_parallel_ai_card_markdown,
    render_daily_review_markdown,
    render_candidate_card_markdown,
)
from tools.review_core import (
    ApiBudget,
    BEIJING_TZ,
    atomic_write_json,
    atomic_write_text,
    beijing_hour_key,
    build_daily_payload,
    build_mt5_m1_loader,
    build_mt5_tick_loader,
    compact_monitor_payload,
    deduplicate_rows,
    is_scheduled_monitor_due,
    parse_beijing_datetime,
    read_csv_rows,
    rows_for_server_window,
    validate_daily_response,
    apply_local_daily_facts,
    build_daily_fallback_response,
    validate_monitor_response,
    append_missed_candidate_history,
    load_missed_candidate_history,
)

LOGGER = logging.getLogger("xauusd_ai_review")


class DefiniteLarkFailure(RuntimeError):
    """The remote endpoint explicitly rejected the request."""


class UncertainLarkDelivery(RuntimeError):
    """The request may have been accepted but no trustworthy response arrived."""


def load_pending_close_triggers(root: Path) -> list[dict[str, Any]]:
    pending_dir = root / "Session_Close_Triggers" / "Pending"
    triggers: list[dict[str, Any]] = []
    if not pending_dir.exists():
        return triggers
    required = {
        "trigger_id",
        "review_key",
        "session_open_server",
        "session_close_server",
    }
    for path in sorted(pending_dir.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict) or not required.issubset(value):
                raise ValueError("missing required close-trigger fields")
            server_open = str(value["session_open_server"]).strip()
            server_close = str(value["session_close_server"]).strip()
            datetime.strptime(server_open, "%Y.%m.%d %H:%M:%S")
            datetime.strptime(server_close, "%Y.%m.%d %H:%M:%S")
            if server_open >= server_close:
                raise ValueError("session_open_server must precede session_close_server")
            value["_pending_path"] = str(path)
            triggers.append(value)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.error("invalid broker close trigger | File=%s | %s", path, exc)
    triggers.sort(key=lambda value: str(value["session_close_server"]))
    return triggers


def mark_close_trigger_processed(root: Path, trigger: dict[str, Any]) -> None:
    source = Path(str(trigger["_pending_path"]))
    processed_dir = root / "Session_Close_Triggers" / "Processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    os.replace(source, processed_dir / source.name)

HIGH_PRIORITY_EVENT_TYPES = {
    "candidate",
    "ai_reject",
    "pending_created",
    "pending_filled",
    "pending_closed",
    "position_update",
    "position_closed",
    "risk_block",
    "execution_error",
}

EVENT_PRIORITY = {
    "execution_error": 100,
    "position_closed": 90,
    "pending_filled": 80,
    "ai_reject": 70,
    "candidate": 60,
    "pending_created": 50,
    "pending_closed": 40,
    "position_update": 30,
    "risk_block": 20,
    "local_reject": 10,
}


def load_lark_webhook(config: dict[str, Any], base_dir: Path) -> str:
    env_name = str(config.get("lark_webhook_environment", "LARK_WEBHOOK_URL"))
    from_env = os.environ.get(env_name, "").strip()
    if from_env:
        return from_env
    webhook_file = Path(str(config.get("lark_webhook_file", "Config/lark_webhook.txt")))
    if not webhook_file.is_absolute():
        webhook_file = base_dir / webhook_file
    if not webhook_file.exists():
        return ""
    value = webhook_file.read_text(encoding="utf-8-sig").strip()
    if value.startswith("请把Lark Webhook") or value.startswith("请把飞书 Webhook"):
        return ""
    return value


class LarkWebhookClient:
    def __init__(
        self,
        webhook: str,
        timeout_seconds: float = 5.0,
        retries: int = 2,
        retry_delay_seconds: float = 1.0,
        opener: Any | None = None,
        sleeper: Any | None = None,
    ) -> None:
        if not webhook.strip():
            raise ValueError("Lark Webhook is empty")
        self.webhook = webhook.strip()
        self.timeout_seconds = max(0.5, min(float(timeout_seconds), 10.0))
        self.retries = max(0, min(int(retries), 5))
        self.retry_delay_seconds = max(0.0, min(float(retry_delay_seconds), 10.0))
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper or time.sleep

    def send_card(self, card: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.webhook,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        for attempt in range(self.retries + 1):
            receipt: dict[str, Any] = {
                "success": False,
                "http_status": None,
                "lark_code": None,
                "lark_message": "",
                "response_body": "",
                "attempt": attempt + 1,
                "sent_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            }
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    status = int(getattr(response, "status", response.getcode()))
                    response_text = response.read().decode("utf-8", errors="replace")
            except Exception as exc:
                err = UncertainLarkDelivery(
                    f"Lark delivery outcome uncertain: {type(exc).__name__}: {exc}"
                )
                err.receipt = receipt
                raise err

            definite_error = ""
            lark_code: Any = None
            lark_message = ""
            if status != 200:
                definite_error = f"Lark Webhook returned HTTP {status}: {response_text[:500]}"
            else:
                try:
                    value = json.loads(response_text) if response_text else {}
                except json.JSONDecodeError:
                    definite_error = f"Lark Webhook returned non-JSON: {response_text[:500]}"
                else:
                    if isinstance(value, dict):
                        code = value.get("code", value.get("StatusCode", 0))
                        lark_code = code
                        lark_message = str(
                            value.get("msg", value.get("StatusMessage", ""))
                        )
                        if str(code) not in {"0", "0.0"}:
                            message = value.get(
                                "msg", value.get("StatusMessage", "unknown error")
                            )
                            definite_error = (
                                f"Lark Webhook business error {code}: {message}"
                            )
            if not definite_error:
                return {
                    **receipt,
                    "success": True,
                    "http_status": status,
                    "lark_code": lark_code if lark_code is not None else 0,
                    "lark_message": lark_message or "ok",
                    "response_body": response_text[:500],
                }
            if attempt < self.retries:
                if self.retry_delay_seconds > 0:
                    self.sleeper(self.retry_delay_seconds)
                continue
            err = DefiniteLarkFailure(definite_error)
            err.receipt = {
                **receipt,
                "http_status": status,
                "lark_code": lark_code,
                "lark_message": lark_message,
                "response_body": response_text[:500],
            }
            raise err
        err = DefiniteLarkFailure("Lark Webhook retry loop ended unexpectedly")
        err.receipt = receipt
        raise err


def generate_trade_commentary(
    notification_type: str,
    trade: dict[str, Any],
    client: Any,
    system_prompt: str,
) -> dict[str, Any]:
    try:
        raw, usage = client.call(
            system_prompt,
            {"event": notification_type, "trade": trade},
            max_tokens=220,
        )
        text = validate_commentary(raw)
        return {**text, "degraded": False, "usage": usage}
    except Exception as exc:
        text = fallback_commentary(notification_type, trade)
        return {
            **text,
            "degraded": True,
            "error": type(exc).__name__,
        }


def _strip_keyword_prefix(text: str, keyword: str) -> str:
    def clean(value: str) -> str:
        if value == keyword:
            return ""
        return re.sub(
            rf"^{re.escape(keyword)}(?:\s*[｜|：:]\s*|\s+)",
            "",
            value,
            count=1,
        )

    if text.startswith("**"):
        heading_end = text.find("**", 2)
        if heading_end >= 2:
            heading = text[2:heading_end]
            cleaned_heading = clean(heading)
            if cleaned_heading != heading:
                return f"**{cleaned_heading}**{text[heading_end + 2:]}"
    return clean(text)


def ensure_lark_security_keyword(
    card: dict[str, Any], keyword: str = "EA复盘"
) -> dict[str, Any]:
    keyword = keyword.strip()
    if not keyword:
        return card

    interactive = card.get("card")
    body = interactive.get("body") if isinstance(interactive, dict) else None
    elements = body.get("elements") if isinstance(body, dict) else None
    if not isinstance(elements, list):
        raise ValueError("unable to inject Lark security keyword into card body")

    header = interactive.get("header")
    title = header.get("title") if isinstance(header, dict) else None
    if isinstance(title, dict):
        title["content"] = _strip_keyword_prefix(
            str(title.get("content", "")).strip(), keyword
        )

    footer = f"安全校验：{keyword}"
    normalized_elements: list[Any] = []
    for element in elements:
        if not isinstance(element, dict):
            normalized_elements.append(element)
            continue
        content = str(element.get("content", ""))
        if element.get("tag") == "markdown" and content.strip() == footer:
            continue
        if element.get("tag") == "markdown":
            element["content"] = _strip_keyword_prefix(content, keyword)
        normalized_elements.append(element)
    normalized_elements.append({"tag": "markdown", "content": footer})
    body["elements"] = normalized_elements
    return card


_OUTBOX_CLOCK_FORMATS = (
    "%Y.%m.%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%d %H:%M",
)
# Doo 服务器时间为 UTC+3，北京时间 UTC+8，换算差 5 小时。
_OUTBOX_BEIJING_MINUS_SERVER_HOURS = 5


def _parse_outbox_clock(value: Any) -> datetime | None:
    """解析卡片里记录的时间；EA 用服务器时间，Python 侧可能用北京时间。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in _OUTBOX_CLOCK_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _outbox_order_key(path: Path) -> tuple[bool, datetime, int, str]:
    """补发排序：按事件发生时间，同一笔交易先开单卡、后结算卡。

    历史实现按文件名排序，导致 `..._FINAL`（F）排在 `..._OPEN`（O）之前，
    结算卡总是先发出来。这里改为读取卡片自带时间字段来排序。
    归一到服务器时间基准，避免北京时间的日报卡与服务器时间的交易卡错位。
    """
    item: dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, dict):
            item = loaded
    except (OSError, json.JSONDecodeError, ValueError):
        item = {}
    facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
    notification_type = str(item.get("notification_type", "")).strip()

    when: datetime | None = None
    if notification_type == "final_trade_facts":
        when = _parse_outbox_clock(facts.get("close_time"))
    if when is None:
        when = _parse_outbox_clock(facts.get("open_time"))
    if when is None:
        when = _parse_outbox_clock(item.get("created_server"))
    if when is None:
        beijing = _parse_outbox_clock(item.get("created_at_beijing"))
        if beijing is not None:
            when = beijing - timedelta(hours=_OUTBOX_BEIJING_MINUS_SERVER_HOURS)
    if when is None:
        try:
            when = datetime.fromtimestamp(path.stat().st_mtime) - timedelta(
                hours=_OUTBOX_BEIJING_MINUS_SERVER_HOURS
            )
        except OSError:
            when = None

    if notification_type == "open_trade_facts":
        stage = 0
    elif notification_type == "final_trade_facts":
        stage = 1
    else:
        stage = 0
    return (when is None, when or datetime.min, stage, path.stem)


def process_lark_outbox(
    config: dict[str, Any],
    root: Path,
    opener: Any | None = None,
    sleeper: Any | None = None,
    commentary_client: Any | None = None,
    commentary_prompt: str = "",
) -> list[dict[str, str]]:
    if not bool(config.get("lark_outbox_enabled", True)):
        return []
    pending_dir = root / "Lark_Outbox" / "Pending"
    if not pending_dir.exists():
        return []
    webhook = load_lark_webhook(config, root)
    if not webhook:
        LOGGER.warning("Lark outbox not processed: webhook is not configured")
        return []
    def _make_client(target_webhook: str) -> LarkWebhookClient:
        return LarkWebhookClient(
            target_webhook,
            timeout_seconds=float(config.get("lark_timeout_seconds", 5)),
            retries=int(config.get("lark_retries", 2)),
            retry_delay_seconds=float(config.get("lark_retry_delay_seconds", 1)),
            opener=opener,
            sleeper=sleeper,
        )

    client = _make_client(webhook)
    parallel_webhook = str(config.get("parallel_ai_lark_webhook", "")).strip() or webhook
    parallel_client = _make_client(parallel_webhook) if parallel_webhook != webhook else client
    results: list[dict[str, str]] = []
    sent_dir = root / "Lark_Outbox" / "Sent"
    uncertain_dir = root / "Lark_Outbox" / "Uncertain"
    sent_dir.mkdir(parents=True, exist_ok=True)
    uncertain_dir.mkdir(parents=True, exist_ok=True)
    # 按事件发生时间顺序补发，避免结算卡先于开单卡发出。
    for pending_path in sorted(pending_dir.glob("*.json"), key=_outbox_order_key):
        notification_id = pending_path.stem
        item: dict[str, Any] | None = None
        try:
            loaded = json.loads(pending_path.read_text(encoding="utf-8-sig"))
            if not isinstance(loaded, dict):
                raise ValueError("outbox item must be a JSON object")
            item = loaded
            notification_id = str(item.get("notification_id", "")).strip()
            card = item.get("card")
            if not notification_id:
                raise ValueError("outbox item requires notification_id")
            if int(item.get("schema_version", 1) or 1) >= 2 and not isinstance(card, dict):
                notification_type = str(item.get("notification_type", "")).strip()
                facts = item.get("facts")
                position_id = str(item.get("position_id", "")).strip()
                if (
                    notification_type not in {"open_trade_facts", "final_trade_facts"}
                    or not isinstance(facts, dict)
                    or not position_id
                ):
                    raise ValueError("schema 2 outbox item requires trade facts and position_id")
                trade = aggregate_position(
                    position_id,
                    load_position_lifecycle(root, position_id),
                    facts,
                )
                # 用MT5真实平仓原因覆盖EA生命周期CSV里可能写错的DEAL_REASON_CLIENT
                # （真实止损单常被EA误记成客户端平仓，导致“手动平仓”误报）。
                if notification_type == "final_trade_facts":
                    from tools.ai_trade_manager import TERMINAL_PATH

                    enriched = enrich_close_reason_from_mt5(
                        [trade], TERMINAL_PATH, "XAUUSD.s"
                    )
                    trade = enriched[0] if enriched else trade
                saved_commentary = item.get("commentary")
                if isinstance(saved_commentary, dict):
                    commentary = validate_commentary(
                        {
                            "reason": saved_commentary.get("reason", ""),
                            "comment": saved_commentary.get("comment", ""),
                        }
                    )
                    commentary_meta = {**saved_commentary, **commentary}
                elif (
                    bool(config.get("trade_commentary_enabled", True))
                    and commentary_client is not None
                    and commentary_prompt.strip()
                ):
                    commentary_meta = generate_trade_commentary(
                        notification_type,
                        trade,
                        commentary_client,
                        commentary_prompt,
                    )
                else:
                    commentary_meta = {
                        **fallback_commentary(notification_type, trade),
                        "degraded": True,
                        "error": "commentary_not_configured",
                    }
                card = render_trade_card(notification_type, trade, commentary_meta)
                item["aggregated_trade"] = trade
                item["commentary"] = commentary_meta
                item["card"] = card
                atomic_write_json(pending_path, item)
            if not isinstance(card, dict):
                raise ValueError("outbox item requires card")
            card = ensure_lark_security_keyword(
                card, str(config.get("lark_security_keyword", "EA复盘"))
            )
            item["card"] = card
            sent_path = sent_dir / f"{notification_id}.json"
            if sent_path.exists():
                pending_path.unlink()
                continue
            target_client = (
                parallel_client
                if str(item.get("notification_type", "")).startswith("parallel_ai_")
                else client
            )
            target_group = (
                "parallel_ai_backup"
                if target_client is parallel_client and parallel_client is not client
                else "main"
            )
            receipt = target_client.send_card(card)
            if not isinstance(receipt, dict):
                receipt = {"success": True, "attempt": 1, "sent_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")}
            sent_at = str(receipt.get("sent_at") or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"))
            item["sent_at_beijing"] = sent_at
            item["send_time"] = sent_at
            item["target_group"] = target_group
            item["http_status"] = receipt.get("http_status")
            item["lark_code"] = receipt.get("lark_code")
            item["lark_message"] = str(receipt.get("lark_message") or "")
            item["response_body"] = str(receipt.get("response_body") or "")[:500]
            item["retry_count"] = max(0, int(receipt.get("attempt", 1) or 1) - 1)
            item["delivery_state"] = "sent"
            item.pop("last_error", None)
            atomic_write_json(pending_path, item)
            os.replace(pending_path, sent_path)
            if str(item.get("notification_type", "")) == "daily_review":
                review_day = str(item.get("review_day", "")).strip()
                if review_day:
                    atomic_write_text(
                        root / "Daily_Review" / review_day / "Lark_Sent.flag",
                        str(item["sent_at_beijing"]) + "\n",
                    )
            results.append({"notification_id": notification_id, "status": "sent"})
            LOGGER.info("Lark outbox sent | ID=%s", notification_id)
        except UncertainLarkDelivery as exc:
            try:
                if item is None:
                    item = {}
                receipt = getattr(exc, "receipt", None)
                if isinstance(receipt, dict):
                    item["http_status"] = receipt.get("http_status")
                    item["lark_code"] = receipt.get("lark_code")
                    item["lark_message"] = str(receipt.get("lark_message") or "")
                    item["response_body"] = str(receipt.get("response_body") or "")[:500]
                    item["retry_count"] = max(0, int(receipt.get("attempt", 1) or 1) - 1)
                item["attempts"] = int(item.get("attempts", 0) or 0) + 1
                item["last_attempt_beijing"] = datetime.now(BEIJING_TZ).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                item["last_error"] = str(exc)[:1000]
                item["delivery_state"] = "uncertain"
                atomic_write_json(pending_path, item)
                os.replace(pending_path, uncertain_dir / pending_path.name)
            except Exception:
                LOGGER.exception(
                    "Lark outbox uncertain metadata write failed | File=%s", pending_path
                )
                raise
            results.append({"notification_id": notification_id, "status": "uncertain"})
            LOGGER.error("Lark outbox delivery uncertain | ID=%s | %s", notification_id, exc)
        except Exception as exc:
            try:
                if item is not None:
                    receipt = getattr(exc, "receipt", None)
                    if isinstance(receipt, dict):
                        item["http_status"] = receipt.get("http_status")
                        item["lark_code"] = receipt.get("lark_code")
                        item["lark_message"] = str(receipt.get("lark_message") or "")
                        item["response_body"] = str(receipt.get("response_body") or "")[:500]
                        item["retry_count"] = max(0, int(receipt.get("attempt", 1) or 1) - 1)
                    item["attempts"] = int(item.get("attempts", 0) or 0) + 1
                    item["last_attempt_beijing"] = datetime.now(BEIJING_TZ).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    item["last_error"] = str(exc)[:1000]
                    item["delivery_state"] = "failed"
                    atomic_write_json(pending_path, item)
            except Exception:
                LOGGER.exception("Lark outbox failure metadata write failed | File=%s", pending_path)
            results.append({"notification_id": notification_id, "status": "failed"})
            LOGGER.error("Lark outbox send failed | ID=%s | %s", notification_id, exc)
    return results


def _lark_number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "0.00"


def collect_notification_issues(root: Path) -> list[str]:
    issues: list[str] = []
    scan_dirs = [
        root / "Lark_Outbox" / "Uncertain",
        root / "Lark_Outbox" / "History",
    ]
    seen: set[str] = set()
    for directory in scan_dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.stem in seen:
                continue
            seen.add(path.stem)
            note = ""
            try:
                item = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(item, dict):
                    if directory.name == "History" and (
                        item.get("acknowledged") is True
                        or str(item.get("delivery_state") or "").upper()
                        in {"CLOSED", "ACKNOWLEDGED"}
                    ):
                        continue
                    note = str(item.get("note") or item.get("last_error") or "").strip()
            except (OSError, json.JSONDecodeError):
                note = ""
            suffix = f"（{note}）" if note else "（发送结果未知）"
            issues.append(f"Lark通知状态未知：{path.stem}{suffix}")
    pending_dir = root / "Lark_Outbox" / "Pending"
    if pending_dir.exists():
        for path in sorted(pending_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                issues.append(f"Lark通知队列文件无法读取：{path.stem}")
                continue
            if isinstance(item, dict) and item.get("last_error"):
                issues.append(f"Lark通知待重试：{path.stem}")
    return issues


def build_daily_lark_card_legacy(
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    api_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    stats = dict(payload.get("statistics") or response.get("trade_statistics") or {})
    metrics = dict(payload.get("market_metrics") or {})
    reject = dict(payload.get("ai_reject_analysis") or {})
    actual_trades = list(payload.get("actual_trade_rows") or [])
    closed_trade_count = sum(row.get("status") == "closed" for row in actual_trades)
    open_at_close_count = sum(
        row.get("status") == "open_at_day_end" for row in actual_trades
    )
    net_profit = float(stats.get("net_profit", 0) or 0)
    header_template = "green" if net_profit > 0 else "red" if net_profit < 0 else "blue"
    monitor_used = int((api_counts or {}).get("monitor", 0))
    daily_used = int((api_counts or {}).get("daily", 0))

    market_text = (
        f"**每日复盘**\n"
        f"**📈 一、本交易日盘面**\n"
        f"**行情类型：** {response.get('market_regime', '数据不足')}\n"
        f"**开 / 高 / 低 / 收：** {_lark_number(metrics.get('open'))} / {_lark_number(metrics.get('high'))} / "
        f"{_lark_number(metrics.get('low'))} / {_lark_number(metrics.get('close'))}\n"
        f"**全天振幅：** {_lark_number(metrics.get('range_usd'))} USD\n"
        f"**净变化：** {_lark_number(metrics.get('net_change_usd'))} USD\n\n"
        f"{response.get('market_summary', '')}"
    )
    execution_text = (
        f"**📊 二、EA执行情况**\n"
        f"候选信号：{int(stats.get('candidate_count', 0) or 0)}\n"
        f"AI允许：{int(stats.get('ai_allow_count', 0) or 0)} ｜ AI拒绝：{int(stats.get('ai_reject_count', 0) or 0)} ｜ AI异常：{int(stats.get('ai_error_count', 0) or 0)}\n"
        f"实际开仓：{int(stats.get('trade_count', 0) or 0)}\n"
        f"已结束交易：{closed_trade_count} ｜ 收盘时持仓：{open_at_close_count}\n"
        f"盈利：{int(stats.get('win_count', 0) or 0)} ｜ 亏损：{int(stats.get('loss_count', 0) or 0)}\n"
        f"已实现净盈亏：{_lark_number(stats.get('net_profit'))} USD"
    )
    reject_text = (
        f"**🚫 三、AI拒绝信号复盘**\n"
        f"总拒绝：{int(reject.get('total_rejected', 0) or 0)}\n"
        f"有效过滤：{int(reject.get('effective_filter_count', 0) or 0)}\n"
        f"错过机会：{int(reject.get('missed_opportunity_count', 0) or 0)}\n"
        f"未决：{int(reject.get('unresolved_count', 0) or 0)}\n"
        f"已决样本有效过滤率：{_lark_number(reject.get('effective_filter_rate'))}%\n\n"
        f"{response.get('ai_filter_assessment', '')}"
    )
    fit_text = (
        f"**🧠 四、策略与盘面匹配**\n"
        f"{response.get('strategy_market_fit', '')}"
    )
    summary_text = (
        f"**📝 五、本交易日总结**\n{response.get('markdown_report', '')}\n\n"
        f"**🤖 API预算**\n盯盘调用：{monitor_used} / 29 ｜ 日报调用：{daily_used} / 1"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": header_template,
                "title": {"tag": "plain_text", "content": f"📊 每日复盘｜{review_day}"},
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": market_text},
                    {"tag": "markdown", "content": execution_text},
                    {"tag": "markdown", "content": reject_text},
                    {"tag": "markdown", "content": fit_text},
                    {"tag": "markdown", "content": summary_text},
                ]
            },
        },
    }


def build_daily_lark_card(
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    api_counts: dict[str, int] | None = None,
    backfill: bool = False,
    daily_limit: int = 1,
    monitor_limit: int = 29,
) -> dict[str, Any]:
    stats = dict(payload.get("statistics") or response.get("trade_statistics") or {})
    groups = dict(payload.get("trade_groups") or {})
    # EA主卡颜色只看正式EA净盈亏，不能用混入AI/测试的整体统计。
    ea_net = float(groups.get("ea", {}).get("net", stats.get("net_profit", 0)) or 0)
    net_profit = ea_net
    header_template = "green" if net_profit > 0 else "red" if net_profit < 0 else "blue"
    sections = build_daily_review_sections(
        review_day,
        payload,
        response,
        api_counts,
        daily_limit=daily_limit,
        monitor_limit=monitor_limit,
    )
    title_text = f"每日复盘（补发）｜{review_day}" if backfill else f"每日复盘｜{review_day}"
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": header_template,
                "title": {"tag": "plain_text", "content": title_text},
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": section} for section in sections
                ]
            },
        },
    }


def build_candidate_lark_card(
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any] | None = None,
    api_counts: dict[str, int] | None = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """Build the second Lark card: formal candidate order details."""
    title_text = (
        f"候选订单明细（补发）｜{review_day}"
        if backfill
        else f"候选订单明细｜{review_day}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title_text},
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": render_candidate_card_markdown(
                            review_day,
                            payload,
                            api_counts,
                            include_title=False,
                            backfill=backfill,
                        ),
                    }
                ]
            },
        },
    }


def build_parallel_ai_lark_card(
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any] | None = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """卡2：Parallel AI每日复盘卡，只统计 Trader B。"""
    response = response or {}
    title_text = (
        f"Parallel AI每日复盘（补发）｜{review_day}"
        if backfill
        else f"Parallel AI每日复盘｜{review_day}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title_text},
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": build_parallel_ai_card_markdown(
                            review_day, payload, response
                        ),
                    }
                ]
            },
        },
    }


def build_comparison_lark_card(
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any] | None = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """卡3：EA / Parallel AI / 人工三组对照卡。"""
    response = response or {}
    title_text = (
        f"EA / AI / 人工交易对照（补发）｜{review_day}"
        if backfill
        else f"EA / AI / 人工交易对照｜{review_day}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title_text},
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": build_comparison_card_markdown(
                            review_day, payload, response
                        ),
                    }
                ]
            },
        },
    }


def _queue_lark_card(
    root: Path,
    notification_id: str,
    notification_type: str,
    review_day: str,
    card: dict[str, Any],
) -> str:
    outbox_root = root / "Lark_Outbox"
    paths = {
        "queued": outbox_root / "Pending" / f"{notification_id}.json",
        "already_sent": outbox_root / "Sent" / f"{notification_id}.json",
        "uncertain": outbox_root / "Uncertain" / f"{notification_id}.json",
    }
    for status in ("already_sent", "uncertain", "queued"):
        if paths[status].exists():
            return status
    atomic_write_json(
        paths["queued"],
        {
            "schema_version": 1,
            "notification_id": notification_id,
            "notification_type": notification_type,
            "review_day": review_day,
            "attempts": 0,
            "created_at_beijing": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "card": card,
        },
    )
    return "queued"


def queue_daily_lark_card(root: Path, review_day: str, card: dict[str, Any]) -> str:
    return _queue_lark_card(
        root,
        f"DAILY_{review_day}",
        "daily_review",
        review_day,
        card,
    )


def _legacy_review_root(config: dict[str, Any]) -> Path | None:
    value = str(config.get("four_hour_legacy_data_root") or "").strip()
    return Path(value) if value else None


def _review_source_root(config: dict[str, Any], service_root: Path) -> Path:
    value = str(config.get("four_hour_data_root") or "").strip()
    return Path(value) if value else service_root


def _review_rows_for_server_window(
    source_root: Path, legacy_root: Path | None, prefix: str,
    server_open: str, server_close: str, keys: tuple[str, ...],
    timestamp_field: str = "server_time",
) -> list[dict[str, str]]:
    rows = rows_for_server_window(
        source_root, prefix, server_open, server_close,
        timestamp_field=timestamp_field,
    )
    if legacy_root is not None and legacy_root != source_root:
        rows += rows_for_server_window(
            legacy_root, prefix, server_open, server_close,
            timestamp_field=timestamp_field,
        )
    return sorted(deduplicate_rows(rows, keys), key=lambda row: str(row.get(timestamp_field) or ""))


def build_session_four_hour_facts(
    root: Path, server_open: str, server_close: str,
    legacy_root: Path | None = None,
    output_root: Path | None = None,
) -> list[dict[str, Any]]:
    facts = []
    for window in session_four_hour_windows(server_open, server_close):
        report = build_four_hour_review(
            root, window["start"], window["end"], legacy_root=legacy_root
        )
        report["daily_only"] = window["daily_only"]
        if output_root is not None and not window["daily_only"]:
            report_day = window["start"][:10]
            start_hour, end_hour = window["start"][11:13], window["end"][11:13]
            saved_path = (output_root / "Four_Hour_Review" / report_day
                          / f"FOUR_HOUR_{report_day}_{start_hour}-{end_hour}.json")
            if saved_path.exists():
                try:
                    saved = load_json(saved_path)
                    if (saved.get("start") == window["start"]
                            and saved.get("end") == window["end"]
                            and saved.get("pro_status") == "ok"):
                        report["pro_analysis"] = validate_four_hour_pro_analysis(
                            saved.get("pro_analysis") or {}
                        )
                        report["pro_status"] = "ok"
                except (OSError, ValueError, json.JSONDecodeError):
                    LOGGER.warning("saved four-hour Pro analysis unreadable: %s", saved_path)
        facts.append(report)
    return facts


def process_due_four_hour_review(
    config: dict[str, Any], root: Path, now_beijing: datetime,
    dry_run: bool = False, pro_client: Any = None, pro_prompt: str = "",
) -> dict[str, Any] | None:
    if not bool(config.get("four_hour_review_enabled", False)):
        return None
    window = due_full_window(
        now_beijing, int(config.get("server_beijing_offset_hours", 5))
    )
    if window is None:
        return None
    day = window["start"][:10]
    start_hour, end_hour = window["start"][11:13], window["end"][11:13]
    notification_id = f"FOUR_HOUR_{day}_{start_hour}-{end_hour}"
    output_dir = root / "Four_Hour_Review" / day
    report_path = output_dir / f"{notification_id}.json"
    markdown_path = output_dir / f"{notification_id}.md"
    report = build_four_hour_review(
        _review_source_root(config, root), window["start"], window["end"],
        legacy_root=_legacy_review_root(config),
    )
    if dry_run:
        return {"status": "preview", "id": notification_id, "report": report,
                "markdown": render_four_hour_card(
                    report, int(config.get("server_beijing_offset_hours", 5)))}
    if report_path.exists():
        return {"status": "already_queued", "id": notification_id}
    if not report["metrics"].get("available"):
        return {"status": "insufficient_data", "id": notification_id}
    if bool(config.get("four_hour_pro_enabled", False)):
        report["pro_status"] = "unavailable"
        if pro_client is not None and pro_prompt:
            try:
                raw_analysis, usage = pro_client.call(
                    pro_prompt,
                    build_four_hour_pro_payload(
                        _review_source_root(config, root), report,
                        legacy_root=_legacy_review_root(config),
                    ),
                    max_tokens=max(200, int(config.get("four_hour_pro_max_tokens", 1000))),
                )
                report["pro_analysis"] = validate_four_hour_pro_analysis(raw_analysis)
                report["pro_status"] = "ok"
                append_api_usage(root, "four_hour_review", now_beijing, usage, True)
            except Exception as exc:
                LOGGER.warning("four-hour Pro unavailable for %s: %s", notification_id, type(exc).__name__)
                append_api_usage(root, "four_hour_review", now_beijing, None, False,
                                 type(exc).__name__)
    markdown = render_four_hour_card(
        report, int(config.get("server_beijing_offset_hours", 5)))
    card = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content":
                          f"四小时小复盘｜{day} {start_hour}:00–{end_hour}:00（服务器时间）"},
            },
            "body": {"elements": [{"tag": "markdown", "content": markdown}]},
        },
    }
    status = "disabled"
    if bool(config.get("four_hour_lark_notify_enabled", True)):
        status = _queue_lark_card(root, notification_id, "four_hour_review", day, card)
    atomic_write_json(report_path, report)
    atomic_write_text(markdown_path, markdown)
    return {"status": status, "id": notification_id, "report": report}


def queue_candidate_lark_card(
    root: Path, review_day: str, card: dict[str, Any]
) -> str:
    return _queue_lark_card(
        root,
        f"DAILY_{review_day}_CANDIDATES",
        "daily_review",
        review_day,
        card,
    )


def queue_parallel_ai_lark_card(
    root: Path, review_day: str, card: dict[str, Any]
) -> str:
    return _queue_lark_card(
        root,
        f"DAILY_{review_day}_PARALLEL_AI",
        "daily_review",
        review_day,
        card,
    )


def queue_comparison_lark_card(
    root: Path, review_day: str, card: dict[str, Any]
) -> str:
    return _queue_lark_card(
        root,
        f"DAILY_{review_day}_COMPARISON",
        "daily_review",
        review_day,
        card,
    )


def queue_lark_keyword_test_card(root: Path) -> str:
    notification_id = "TEST_LARK_KEYWORD_FOOTER_20260811"
    outbox_root = root / "Lark_Outbox"
    paths = {
        "queued": outbox_root / "Pending" / f"{notification_id}.json",
        "already_sent": outbox_root / "Sent" / f"{notification_id}.json",
        "uncertain": outbox_root / "Uncertain" / f"{notification_id}.json",
    }
    for status in ("already_sent", "uncertain", "queued"):
        if paths[status].exists():
            return status

    card = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": "【测试】Lark通知格式与安全词验证",
                },
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            "本卡片仅用于验证通知格式和Lark安全词，"
                            "不代表开仓、平仓或交易信号。"
                        ),
                    }
                ]
            },
        },
    }
    atomic_write_json(
        paths["queued"],
        {
            "schema_version": 1,
            "notification_id": notification_id,
            "notification_type": "lark_keyword_footer_test",
            "attempts": 0,
            "created_at_beijing": datetime.now(BEIJING_TZ).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "card": card,
        },
    )
    return "queued"


def send_daily_lark_if_enabled(
    config: dict[str, Any],
    root: Path,
    review_day: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    api_counts: dict[str, int],
    backfill: bool = False,
) -> str:
    if not bool(config.get("lark_daily_notify_enabled", True)):
        return "disabled"
    daily_limit = max(0, int(config.get("daily_review_limit", 1)))
    monitor_limit = max(0, int(config.get("monitor_daily_limit", 29)))
    statuses = [
        queue_daily_lark_card(
            root,
            review_day,
            build_daily_lark_card(
                review_day,
                payload,
                response,
                api_counts,
                backfill=backfill,
                daily_limit=daily_limit,
                monitor_limit=monitor_limit,
            ),
        ),
        queue_parallel_ai_lark_card(
            root,
            review_day,
            build_parallel_ai_lark_card(review_day, payload, response, backfill=backfill),
        ),
        queue_comparison_lark_card(
            root,
            review_day,
            build_comparison_lark_card(review_day, payload, response, backfill=backfill),
        ),
    ]
    candidates = payload.get("candidate_rows") or []
    if candidates:
        statuses.append(
            queue_candidate_lark_card(
                root,
                review_day,
                build_candidate_lark_card(
                    review_day, payload, response, api_counts, backfill=backfill
                ),
            )
        )
    if "queued" in statuses:
        return "queued"
    if "uncertain" in statuses:
        return "uncertain"
    if "already_sent" in statuses:
        return "already_sent"
    return "queued"

    # Legacy direct-send implementation intentionally remains unreachable during
    # the migration window. All Lark delivery now goes through process_lark_outbox.
    webhook = load_lark_webhook(config, root)
    if not webhook:
        LOGGER.warning("Lark日报未发送：Webhook未配置")
        return "not_configured"
    try:
        client = LarkWebhookClient(
            webhook,
            timeout_seconds=float(config.get("lark_timeout_seconds", 5)),
        )
        client.send_card(build_daily_lark_card(review_day, payload, response, api_counts))
        atomic_write_text(
            root / "Daily_Review" / review_day / "Lark_Sent.flag",
            datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S") + "\n",
        )
        return "sent"
    except Exception as exc:
        LOGGER.error("Lark日报发送失败：%s", exc)
        return "failed"


def retry_saved_daily_lark(
    config: dict[str, Any],
    root: Path,
    review_day: str,
    api_counts: dict[str, int],
    backfill: bool = False,
) -> str:
    output_dir = root / "Daily_Review" / review_day
    outbox = root / "Lark_Outbox"
    main_file = f"DAILY_{review_day}.json"
    candidate_file = f"DAILY_{review_day}_CANDIDATES.json"
    pending_main = (outbox / "Pending" / main_file).exists()
    pending_candidate = (outbox / "Pending" / candidate_file).exists()
    uncertain_main = (outbox / "Uncertain" / main_file).exists()
    uncertain_candidate = (outbox / "Uncertain" / candidate_file).exists()
    sent_main = (outbox / "Sent" / main_file).exists()
    sent_candidate = (outbox / "Sent" / candidate_file).exists()
    if pending_main or pending_candidate:
        return "queued"
    if uncertain_main or uncertain_candidate:
        return "uncertain"
    if sent_main and sent_candidate:
        return "already_sent"
    if sent_main:
        # Main card already delivered, but the candidate card may be missing
        # (e.g. queued before the two-card split). Requeue candidates if the
        # saved payload actually contains any.
        payload_path = output_dir / f"Daily_Data_{review_day}.json"
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("candidate_rows"):
            return send_daily_lark_if_enabled(
                config, root, review_day, payload, {}, api_counts, backfill=backfill
            )
        return "already_sent"
    payload_path = output_dir / f"Daily_Data_{review_day}.json"
    response_path = output_dir / f"Daily_Review_{review_day}.json"
    if not payload_path.exists() or not response_path.exists():
        return "missing_saved_review"
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Lark日报补发读取失败：%s", type(exc).__name__)
        return "failed"
    if not isinstance(payload, dict) or not isinstance(response, dict):
        return "failed"
    return send_daily_lark_if_enabled(
        config, root, review_day, payload, response, api_counts, backfill=backfill
    )


class DeepSeekJsonClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: float, retries: int) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API key is empty")
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key.strip(), base_url="https://api.deepseek.com")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def call(
        self,
        system_prompt: str,
        payload: dict[str, Any],
        max_tokens: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            started = perf_counter()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(
                                payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                    response_format={"type": "json_object"},
                    stream=False,
                    max_tokens=max_tokens,
                    timeout=self.timeout_seconds,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                content = response.choices[0].message.content or ""
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise ValueError("AI response is not a JSON object")
                usage_obj = getattr(response, "usage", None)
                usage = {
                    "model": getattr(response, "model", self.model) or self.model,
                    "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                    "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
                    "response_time_ms": int((perf_counter() - started) * 1000),
                    "attempts": attempt + 1,
                }
                return value, usage
            except Exception as exc:  # bounded transport/parse retry
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 8))
        raise RuntimeError("DeepSeek request failed") from last_error


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"config must be JSON object: {path}")
    return value


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_api_key(config: dict[str, Any], base_dir: Path) -> str:
    env_name = str(config.get("api_key_environment", "DEEPSEEK_API_KEY"))
    from_env = os.environ.get(env_name, "").strip()
    if from_env:
        return from_env
    key_file = Path(str(config.get("api_key_file", "Config/deepseek_api_key.txt")))
    if not key_file.is_absolute():
        key_file = base_dir / key_file
    if key_file.exists():
        value = key_file.read_text(encoding="utf-8-sig").strip()
        if value.startswith("请把DeepSeek API Key"):
            return ""
        return value
    return ""


def resolve_data_root(config: dict[str, Any], config_path: Path) -> Path:
    explicit = str(config.get("data_root", "")).strip()
    if explicit:
        return Path(os.path.expandvars(explicit)).expanduser().resolve()
    if config_path.parent.name.lower() == "config":
        return config_path.parent.parent.resolve()
    raise ValueError("data_root is required when config is not inside the account Config directory")


def prompt_text(path_value: str, config_path: Path) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        path = config_path.parent / path
    return path.read_text(encoding="utf-8")


def apply_trade_terminal_override(config: dict[str, Any]) -> str:
    """允许每套配置绑定各自的 MT5 终端。

    多套 EA/服务并存时（例如 V3.9.20 与 V3.10.9 跑在不同终端、不同账户），
    交易与行情查询必须连到对应终端，否则会把单子下到错误的账户。
    配置项缺省时保持原行为（沿用 ai_trade_manager 的默认终端）。
    """
    path = str(config.get("trade_terminal_path") or "").strip()
    import tools.ai_trade_manager as trade_manager

    if not path:
        # 未配置就回到该模块的默认终端，避免同进程里残留上一套配置的绑定。
        path = _DEFAULT_TRADE_TERMINAL
    changed = _DEFAULT_TRADE_TERMINAL != path or TERMINAL_PATH != path
    trade_manager.TERMINAL_PATH = path
    globals()["TERMINAL_PATH"] = path
    if changed:
        LOGGER.info("MT5 trade terminal bound to %s", path)
    return path


def parallel_audit_session_summary(
    root: Path, server_open: str, server_close: str
) -> dict[str, int]:
    """Read-only audit counts; never changes trade/P&L facts in the daily review."""
    def parse_server(value: str) -> datetime:
        return datetime.strptime(value.replace(".", "-"), "%Y-%m-%d %H:%M:%S")

    start, end = parse_server(server_open), parse_server(server_close)
    counts = {
        "total": 0, "agree": 0, "uncomparable": 0,
        "calculation_anomaly": 0, "local_entry_disagreement": 0,
        "ai_review_disagreement": 0, "execution_suspect": 0,
        "recovered_episodes": 0,
    }
    records_dir = Path(root) / "Parallel_AI_V2" / "Records"
    if not records_dir.exists():
        return counts
    seen: set[str] = set()
    for path in sorted(records_dir.glob("Comparisons_*.jsonl")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            try:
                row = json.loads(line)
                stamp = parse_server(str(row.get("m5_time", "")))
            except (ValueError, json.JSONDecodeError):
                continue
            snapshot_id = str(row.get("snapshot_id", ""))
            if not start <= stamp <= end or snapshot_id in seen:
                continue
            seen.add(snapshot_id)
            classification = str(row.get("classification", "")).upper()
            counts["total"] += 1
            if classification in {"AGREE_WAIT", "AGREE_OPEN"}:
                counts["agree"] += 1
            elif classification in {"UNCOMPARABLE", "DATA_SYNC_ERROR"}:
                counts["uncomparable"] += 1
            elif classification == "CALCULATION_ANOMALY":
                counts["calculation_anomaly"] += 1
            elif classification in {"LOCAL_ENTRY_DISAGREEMENT", "DIRECTION_DISAGREEMENT", "PLAN_DISAGREEMENT"}:
                counts["local_entry_disagreement"] += 1
            elif classification == "AI_REVIEW_DISAGREEMENT":
                counts["ai_review_disagreement"] += 1
            elif classification in {"EXECUTION_SUSPECT", "PENDING_FILL_ANOMALY"}:
                counts["execution_suspect"] += 1
    return counts


def rows_for_date(root: Path, prefix: str, day: str) -> list[dict[str, str]]:
    return read_csv_rows(root / "Raw_Data" / f"{prefix}_{day}.csv")


def append_api_usage(
    root: Path,
    module: str,
    beijing_time: datetime,
    usage: dict[str, Any] | None,
    success: bool,
    error: str = "",
) -> None:
    path = root / "Logs" / f"API_Usage_{beijing_time:%Y-%m}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "beijing_time",
        "module",
        "success",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "response_time_ms",
        "attempts",
        "error",
    ]
    write_header = not path.exists() or path.stat().st_size == 0
    row = {
        "beijing_time": beijing_time.strftime("%Y-%m-%d %H:%M:%S"),
        "module": module,
        "success": str(success).lower(),
        "model": (usage or {}).get("model", ""),
        "prompt_tokens": (usage or {}).get("prompt_tokens", 0),
        "completion_tokens": (usage or {}).get("completion_tokens", 0),
        "total_tokens": (usage or {}).get("total_tokens", 0),
        "response_time_ms": (usage or {}).get("response_time_ms", 0),
        "attempts": (usage or {}).get("attempts", 0),
        "error": error,
    }
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def write_monitor_output(root: Path, response: dict[str, Any], trigger: str) -> None:
    day = response["beijing_time"][:10]
    output_dir = root / "AI_Monitor"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"Monitor_Data_{day}.csv"
    fieldnames = [
        "beijing_time",
        "trigger_type",
        "market_state",
        "trend_alignment",
        "ea_state",
        "main_reason",
        "economic_event_note",
        "execution_status",
        "alert_level",
        "summary",
        "condition_summary_json",
    ]
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    with csv_path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                **{
                    name: response.get(name, "")
                    for name in fieldnames
                    if name != "condition_summary_json"
                },
                "trigger_type": trigger,
                "condition_summary_json": json.dumps(
                    response["condition_summary"], ensure_ascii=False
                ),
            }
        )
        handle.flush()
        os.fsync(handle.fileno())
    md_path = output_dir / f"Monitor_Text_{day}.md"
    with md_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(
            f"\n### {response['beijing_time']} · {trigger}\n\n"
            f"{response['summary'].strip()}\n"
        )


def write_daily_output(
    root: Path,
    day: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    api_counts: dict[str, int] | None = None,
    daily_limit: int = 1,
    monitor_limit: int = 29,
) -> None:
    output_dir = root / "Daily_Review" / day
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / f"Daily_Data_{day}.json", payload)
    flattened = {
        "review_date": day,
        **response["trade_statistics"],
        **{
            f"block_{key}": value
            for key, value in response["block_reason_counts"].items()
        },
        "data_status": response["data_status"],
        "ea_runtime_status": response["ea_runtime_status"],
        "no_trade_main_reason": response["no_trade_main_reason"],
    }
    csv_path = output_dir / f"Daily_Data_{day}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flattened))
        writer.writeheader()
        writer.writerow(flattened)
    atomic_write_text(
        output_dir / f"Daily_Review_{day}.md",
        render_daily_review_markdown(
            day,
            payload,
            response,
            api_counts,
            daily_limit=daily_limit,
            monitor_limit=monitor_limit,
        ),
    )
    atomic_write_text(
        output_dir / f"Daily_Candidates_{day}.md",
        render_candidate_card_markdown(day, payload, api_counts),
    )
    atomic_write_json(output_dir / f"Daily_Review_{day}.json", response)


def collect_recent_data(
    root: Path,
    now_beijing: datetime,
    lookback_minutes: int = 90,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    days = sorted(
        {
            now_beijing.date().isoformat(),
            (now_beijing.date() - timedelta(days=1)).isoformat(),
        }
    )
    snapshots: list[dict[str, str]] = []
    events: list[dict[str, str]] = []
    calendar: list[dict[str, str]] = []
    for day in days:
        snapshots.extend(rows_for_date(root, "Market_Snapshots", day))
        events.extend(rows_for_date(root, "Strategy_Events", day))
        calendar.extend(rows_for_date(root, "Economic_Calendar", day))
    cutoff = now_beijing - timedelta(minutes=lookback_minutes)

    def recent(row: dict[str, str]) -> bool:
        value = row.get("beijing_time", "")
        if not value:
            return False
        try:
            return parse_beijing_datetime(value) >= cutoff
        except ValueError:
            return False

    now_text = now_beijing.strftime("%Y-%m-%d %H:%M:%S")
    return (
        [row for row in snapshots if recent(row)],
        [row for row in events if recent(row)],
        [
            row
            for row in calendar
            if recent(row) or row.get("event_beijing_time", "") >= now_text
        ],
    )


def event_signature(row: dict[str, str]) -> str:
    return "|".join(
        row.get(key, "")
        for key in (
            "beijing_time",
            "event_type",
            "signal_id",
            "deal_ticket",
            "order_ticket",
            "reason",
        )
    )


def update_pending_events(
    state: dict[str, Any],
    all_recent_events: list[dict[str, str]],
) -> list[dict[str, str]]:
    last_seen = str(state.get("last_seen_event_time", ""))
    new_events = [
        row for row in all_recent_events if row.get("beijing_time", "") > last_seen
    ]
    pending = [
        row
        for row in state.get("pending_events", [])
        if isinstance(row, dict) and row.get("beijing_time")
    ]
    seen = {event_signature(row) for row in pending}
    for row in new_events:
        signature = event_signature(row)
        if signature not in seen:
            pending.append(row)
            seen.add(signature)
    pending.sort(key=lambda row: row.get("beijing_time", ""))
    state["pending_events"] = pending[-200:]
    if all_recent_events:
        state["last_seen_event_time"] = max(
            row.get("beijing_time", "") for row in all_recent_events
        )
    return state["pending_events"]


def latest_local_reject_reason(events: list[dict[str, str]]) -> str:
    for row in reversed(events):
        if row.get("event_type") == "local_reject":
            return row.get("reason", "")
    return ""


def event_batch_due(
    pending: list[dict[str, str]],
    state: dict[str, Any],
    current_beijing: datetime,
    merge_minutes: int,
    scheduled_due: bool,
) -> bool:
    if not pending:
        return False
    event_types = {row.get("event_type", "") for row in pending}
    high_priority = bool(event_types & HIGH_PRIORITY_EVENT_TYPES)
    reject_reason = latest_local_reject_reason(pending)
    changed_reject = bool(
        reject_reason and reject_reason != state.get("last_local_reject_reason", "")
    )
    if not high_priority and not changed_reject:
        return False
    if scheduled_due:
        return True
    try:
        latest_time = parse_beijing_datetime(pending[-1]["beijing_time"])
    except (KeyError, ValueError):
        return True
    return current_beijing - latest_time >= timedelta(minutes=merge_minutes)


def choose_event_trigger(pending: list[dict[str, str]]) -> str:
    types = {row.get("event_type", "") for row in pending}
    if not types:
        return "scheduled"
    return max(types, key=lambda value: EVENT_PRIORITY.get(value, 0))


def semantic_snapshot_signature(snapshot: dict[str, str]) -> str:
    def number(name: str) -> float:
        try:
            return float(snapshot.get(name, "") or 0)
        except ValueError:
            return 0.0

    features: list[str] = [
        snapshot.get("ea_state", ""),
        snapshot.get("last_scan_stage", ""),
        snapshot.get("last_scan_reason", ""),
        snapshot.get("weekend_guard", ""),
        snapshot.get("rollover_guard", ""),
    ]
    for prefix in ("m5", "m15", "h1", "h4"):
        close = number(f"{prefix}_close")
        ema = number(f"{prefix}_ema20")
        rsi = number(f"{prefix}_rsi14")
        macd = number(f"{prefix}_macd_hist")
        features.extend(
            [
                "above" if close > ema else "below" if close < ema else "flat",
                "high" if rsi >= 60 else "low" if rsi <= 40 else "mid",
                "positive" if macd > 0 else "negative" if macd < 0 else "zero",
            ]
        )
    spread = number("spread")
    atr = number("m5_atr14")
    ratio = spread / atr if atr > 0 else 0
    features.append("wide" if ratio > 0.08 else "normal")
    return "|".join(features)


def load_prior_issue_history(root: Path, review_day: str, days: int = 10) -> list[dict[str, Any]]:
    target = datetime.strptime(review_day, "%Y-%m-%d").date()
    history: list[dict[str, Any]] = []
    for offset in range(1, days + 1):
        day = (target - timedelta(days=offset)).isoformat()
        path = root / "Daily_Review" / day / f"Daily_Review_{day}.json"
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            history.append(
                {
                    "review_date": day,
                    "observation_items": value.get("observation_items", []),
                    "validation_issues": value.get("validation_issues", []),
                    "execution_issues": value.get("execution_issues", []),
                }
            )
    return history


def archive_old_raw_data(
    root: Path,
    current_beijing_date: str,
    retention_days: int,
    archive_directory: str,
) -> int:
    if retention_days <= 0 or not archive_directory.strip():
        return 0
    raw = root / "Raw_Data"
    if not raw.exists():
        return 0
    cutoff = datetime.strptime(current_beijing_date, "%Y-%m-%d").date() - timedelta(
        days=retention_days
    )
    archive_root = Path(os.path.expandvars(archive_directory)).expanduser().resolve()
    archive_root.mkdir(parents=True, exist_ok=True)
    moved = 0
    for path in sorted(raw.glob("*.csv")):
        stamp = path.stem[-10:]
        try:
            file_day = datetime.strptime(stamp, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_day >= cutoff:
            continue
        zip_path = archive_root / f"XAUUSD_AI_Raw_{stamp[:7]}.zip"
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            arcname = f"{root.name}/Raw_Data/{path.name}"
            if arcname not in archive.namelist():
                archive.write(path, arcname=arcname)
        path.unlink()
        moved += 1
    return moved


def monitor_cooldown_active(state: dict[str, Any], current_beijing: datetime) -> bool:
    value = str(state.get("monitor_retry_after", "")).strip()
    if not value:
        return False
    try:
        return current_beijing < parse_beijing_datetime(value)
    except ValueError:
        state.pop("monitor_retry_after", None)
        return False


def process_weekly_review(
    config: dict[str, Any],
    root: Path,
    config_path: Path,
    now_beijing: datetime,
    daily_client: Any,
) -> dict[str, Any]:
    if not bool(config.get("weekly_review_enabled", False)):
        return {"status": "disabled"}
    from tools.weekly_review import (
        aggregate_weekly_facts,
        build_weekly_fallback_response,
        build_weekly_lark_card,
        build_weekly_payload,
        last_completed_week,
        validate_weekly_response,
        write_weekly_output,
    )

    monday, friday = last_completed_week(now_beijing)
    week_key = monday
    friday_report = root / "Daily_Review" / friday / f"Daily_Review_{friday}.md"
    if not friday_report.exists():
        return {"status": "week_incomplete", "week": week_key}
    weekly_dir = root / "Weekly_Review" / week_key
    if (weekly_dir / f"Weekly_Review_{week_key}.md").exists():
        return {"status": "already_done", "week": week_key}
    notification_id = f"WEEKLY_{week_key}"
    for status in ("Sent", "Pending", "Uncertain"):
        if (root / "Lark_Outbox" / status / f"{notification_id}.json").exists():
            return {"status": status.lower(), "week": week_key}

    facts, days = aggregate_weekly_facts(root, monday, friday)
    if not days:
        return {"status": "no_data", "week": week_key}
    weekly_prompt = prompt_text(
        str(config.get("weekly_review_prompt_file", "weekly_review_prompt.txt")),
        config_path,
    )
    payload = build_weekly_payload(facts)
    ai_fallback = False
    usage = None
    try:
        raw, usage = daily_client.call(weekly_prompt, payload, max_tokens=2400)
        response = validate_weekly_response(raw)
    except Exception as exc:
        ai_fallback = True
        usage = None
        append_api_usage(root, "weekly_review", now_beijing, None, False, str(exc))
        LOGGER.exception("weekly review AI unavailable; using local fallback")
        response = build_weekly_fallback_response(facts)
    try:
        write_weekly_output(root, week_key, facts, response)
        card = build_weekly_lark_card(week_key, facts, response)
        markdown = "\n\n".join(
            str(element.get("content", ""))
            for element in card.get("card", {}).get("body", {}).get("elements", [])
        )
        (weekly_dir / f"Weekly_Review_{week_key}.md").write_text(
            markdown, encoding="utf-8"
        )
        if not ai_fallback:
            append_api_usage(root, "weekly_review", now_beijing, usage, True)
        lark_status = _queue_lark_card(
            root,
            notification_id,
            "weekly_review",
            week_key,
            card,
        )
        return {
            "status": "generated",
            "week": week_key,
            "lark": lark_status,
            "ai_fallback": ai_fallback,
        }
    except Exception as exc:
        LOGGER.exception("weekly review write failed")
        return {"status": "failed", "week": week_key, "error": str(exc)}


def run_once(
    config_path: Path,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    config = load_json(config_path)
    root = resolve_data_root(config, config_path)
    root.mkdir(parents=True, exist_ok=True)
    apply_trade_terminal_override(config)
    state_path = root / "Logs" / "review_service_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = load_state(state_path)
    if not dry_run:
        try:
            from tools.ai_trade_manager import snapshot_manual_open_positions

            snapshot_manual_open_positions(root, config)
        except Exception:
            LOGGER.exception("manual/open-position snapshot failed")
    current_utc = now or datetime.now(timezone.utc)
    current_beijing = current_utc.astimezone(BEIJING_TZ)
    day = current_beijing.date().isoformat()
    budget = ApiBudget(
        root / "Logs" / "api_usage.json",
        monitor_limit=int(config.get("monitor_daily_limit", 29)),
        daily_limit=int(config.get("daily_review_limit", 1)),
    )

    monitor_prompt = prompt_text(
        str(config.get("monitor_prompt_file", "market_monitor_prompt.txt")),
        config_path,
    )
    daily_prompt = prompt_text(
        str(config.get("daily_prompt_file", "daily_review_prompt.txt")),
        config_path,
    )
    trade_review_prompt = ""
    try:
        trade_review_prompt = prompt_text(
            str(config.get("trade_review_prompt_file", "trade_review_prompt.txt")),
            config_path,
        )
    except FileNotFoundError:
        LOGGER.warning("trade review prompt missing; skipping deep review")
    commentary_prompt = ""
    if bool(config.get("trade_commentary_enabled", True)):
        try:
            commentary_prompt = prompt_text(
                str(config.get("trade_commentary_prompt_file", "trade_commentary_prompt.txt")),
                config_path,
            )
        except FileNotFoundError:
            LOGGER.warning("trade commentary prompt missing; local fallback text will be used")
    independent_prompt = ""
    divergence_prompt = ""
    independent_prompt_error = ""
    if bool(config.get("independent_ai_enabled", True)):
        try:
            independent_prompt = prompt_text(
                str(config.get("independent_ai_prompt_file", "independent_ai_prompt.txt")),
                config_path,
            )
            divergence_prompt = prompt_text(
                str(
                    config.get(
                        "independent_ai_divergence_prompt_file",
                        "independent_ai_divergence_prompt.txt",
                    )
                ),
                config_path,
            )
        except FileNotFoundError as exc:
            independent_prompt_error = str(exc)
            LOGGER.error("independent AI prompt missing | %s", exc)
    parallel_primary_prompt = ""
    parallel_difference_prompt = ""
    parallel_prompt_error = ""
    if bool(config.get("parallel_ai_enabled", False)):
        try:
            parallel_primary_prompt = prompt_text(
                str(config.get("parallel_ai_entry_prompt_file", "parallel_ai_entry_prompt.txt")),
                config_path,
            )
            parallel_difference_prompt = prompt_text(
                str(config.get("parallel_ai_difference_prompt_file", "parallel_ai_difference_prompt.txt")),
                config_path,
            )
        except FileNotFoundError as exc:
            parallel_prompt_error = str(exc)
            LOGGER.error("parallel AI prompt missing | %s", exc)
    client = None
    daily_client = None
    if not dry_run:
        api_key = load_api_key(config, root)
        timeout_seconds = float(config.get("timeout_seconds", 20))
        retries = int(config.get("retries", 1))
        client = DeepSeekJsonClient(
            api_key,
            str(config.get("model", "deepseek-v4-flash")),
            timeout_seconds,
            retries,
        )
        # 日报和四小时复盘共用 Pro 客户端，各时段只生成一份复盘；
        # 盯盘、开平仓点评和平行 AI 仍使用默认 flash 模型。
        daily_client = DeepSeekJsonClient(
            api_key,
            str(config.get("daily_review_model", "deepseek-v4-pro")),
            timeout_seconds,
            retries,
        )

    results: dict[str, Any] = {
        "monitor": [],
        "daily": [],
        "four_hour": [],
        "daily_lark_retry": [],
        "lark_outbox": [],
        "parallel_ai": [],
        "archive": 0,
    }
    if not dry_run:
        results["lark_outbox"] = process_lark_outbox(
            config,
            root,
            commentary_client=client,
            commentary_prompt=commentary_prompt,
        )

    if bool(config.get("parallel_ai_enabled", False)):
        if parallel_prompt_error:
            results["parallel_ai"] = [
                {"status": "CONFIG_ERROR", "error": "parallel_ai_prompt_missing"}
            ]
        else:
            try:
                results["parallel_ai"] = process_parallel_ai_cycle(
                    config=config,
                    root=root,
                    now_beijing=current_beijing,
                    client=client,
                    primary_prompt=parallel_primary_prompt,
                    difference_prompt=parallel_difference_prompt,
                    dry_run=dry_run,
                )
            except Exception as exc:
                LOGGER.exception("parallel AI auditor cycle failed")
                results["parallel_ai"] = [
                    {"status": "AUDITOR_CYCLE_FAILED", "error": type(exc).__name__}
                ]
        if not dry_run and bool(config.get("parallel_ai_trade_enabled", False)):
            try:
                from tools.ai_trade_manager import manage_ai_positions

                results["parallel_ai_manage"] = manage_ai_positions(root, config)
            except Exception as exc:
                LOGGER.exception("AI trade manager cycle failed")
                results["parallel_ai_manage"] = [
                    {"status": "MANAGE_FAILED", "error": type(exc).__name__}
                ]
        # A newly queued disagreement is sent in this same poll whenever possible.
        if not dry_run:
            results["lark_outbox"] += process_lark_outbox(
                config, root, commentary_client=client,
                commentary_prompt=commentary_prompt,
            )

    review_source_root = _review_source_root(config, root)
    review_legacy_root = _legacy_review_root(config)
    close_triggers = load_pending_close_triggers(review_source_root)
    max_daily_reviews_per_run = max(
        0, int(config.get("max_daily_reviews_per_run", 1))
    )
    completed_daily_reviews = 0
    for trigger in close_triggers:
        if completed_daily_reviews >= max_daily_reviews_per_run:
            break
        review_day = str(trigger["review_key"])
        server_open = str(trigger["session_open_server"])
        server_close = str(trigger["session_close_server"])
        report_path = (
            root
            / "Daily_Review"
            / review_day
            / f"Daily_Review_{review_day}.md"
        )
        if report_path.exists():
            retry_status = retry_saved_daily_lark(
                config, root, review_day, budget.counts(day)
            )
            if retry_status in {"queued", "uncertain", "failed"}:
                results["daily_lark_retry"].append(
                    {"date": review_day, "status": retry_status}
                )
            if not dry_run and retry_status in {
                "queued",
                "uncertain",
                "already_sent",
                "disabled",
            }:
                mark_close_trigger_processed(review_source_root, trigger)
            continue
        snapshots = _review_rows_for_server_window(
            review_source_root, review_legacy_root,
            "Market_Snapshots", server_open, server_close, ("server_time",),
        )
        events = _review_rows_for_server_window(
            review_source_root, review_legacy_root,
            "Strategy_Events", server_open, server_close,
            (
                "server_time",
                "event_type",
                "signal_id",
                "deal_ticket",
                "reason",
            ),
        )
        calendar = _review_rows_for_server_window(
            review_source_root, review_legacy_root,
            "Economic_Calendar", server_open, server_close,
            ("value_id", "event_id", "event_beijing_time"),
            timestamp_field="event_server_time",
        )
        if not snapshots and not events:
            LOGGER.warning("skip daily review %s: no EA data", review_day)
            continue
        lifecycle_dir = review_source_root / "Trade_Lifecycle"
        lifecycle_trades = None
        if lifecycle_dir.exists() and any(lifecycle_dir.glob("Position_*.csv")):
            lifecycle_trades = trades_for_server_window(review_source_root, server_open, server_close)
        if review_legacy_root is not None and review_legacy_root != review_source_root:
            legacy_trades = trades_for_server_window(review_legacy_root, server_open, server_close)
            lifecycle_trades = (lifecycle_trades or []) + legacy_trades
        tick_diagnostics: dict[str, Any] = {}
        m1_loader = build_mt5_m1_loader(
            "XAUUSD.s", TERMINAL_PATH, diagnostics=tick_diagnostics
        )
        tick_loader = build_mt5_tick_loader(
            "XAUUSD.s", TERMINAL_PATH, diagnostics=tick_diagnostics
        )
        payload = build_daily_payload(
            review_day,
            snapshots,
            events,
            calendar,
            prior_issue_history=load_prior_issue_history(root, review_day),
            lifecycle_trades=lifecycle_trades,
            missed_history_records=load_missed_candidate_history(root),
            m1_loader=m1_loader,
            tick_loader=tick_loader,
            tick_diagnostics=tick_diagnostics,
        )
        execution_issues = [
            str(row.get("reason", row.get("details", "执行异常")))
            for row in payload.get("execution_events", [])
        ]
        payload["program_issues"] = execution_issues + collect_notification_issues(root)
        payload["strategy_issues"] = []
        # 当日行情回顾 + “该开单的地方有没有开出来”（确定性事实，供 Pro 解释）
        try:
            from tools.daily_market_review import build_market_review

            payload["market_review"] = build_market_review(
                review_source_root, review_day, payload.get("candidate_rows") or [],
                supplemental_root=review_legacy_root, snapshot_rows=snapshots,
            )
        except Exception:
            LOGGER.exception("market review facts failed")
            payload["market_review"] = {}
        payload["session_window"] = {
            "basis": "broker_session_close",
            "server_open": server_open,
            "server_close": server_close,
            "used_fallback": bool(trigger.get("used_fallback", False)),
        }
        if bool(config.get("four_hour_review_enabled", False)):
            payload["four_hour_reviews"] = build_session_four_hour_facts(
                review_source_root, server_open, server_close, review_legacy_root,
                output_root=root,
            )
        payload["source_counts"] = {
            "snapshots": len(snapshots),
            "events": len(events),
            "calendar": len(calendar),
        }
        payload["parallel_ai_audit"] = parallel_audit_session_summary(
            root, server_open, server_close
        )
        try:
            from tools.review_core import collect_ai_candidate_rows

            payload["ai_candidate_rows"] = collect_ai_candidate_rows(
                root, server_open, server_close
            )
        except Exception:
            payload["ai_candidate_rows"] = []
        if bool(config.get("parallel_ai_trade_enabled", False)):
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
                # MT5 历史读取失败时（例如策略测试器占用终端），用已收集的生命周期
                # CSV 兜底，避免三组对照卡误报“今日无正式交易”。
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
            except Exception as exc:
                LOGGER.exception("trade group collection failed")
                payload["trade_groups"] = {}
                payload["trade_ownership"] = {}
                payload["ai_trade_details"] = []
                payload["ai_trade_rows"] = []
                payload["trade_review_facts"] = []
        if dry_run:
            results["daily"].append(
                {"date": review_day, "payload": payload, "dry_run": True}
            )
            completed_daily_reviews += 1
            continue
        if not budget.consume_daily(day):
            LOGGER.info("daily review quota already used for Beijing day %s", day)
            break
        ai_fallback_used = False
        try:
            LOGGER.info(
                "daily review AI call | day=%s | model=%s",
                review_day,
                daily_client.model,  # type: ignore[union-attr]
            )
            raw_response, usage = daily_client.call(  # type: ignore[union-attr]
                daily_prompt,
                payload,
                max_tokens=1600,
            )
            response = validate_daily_response(raw_response)
            response = apply_local_daily_facts(response, payload)
        except Exception as exc:
            ai_fallback_used = True
            usage = None
            append_api_usage(
                root,
                "daily_review",
                current_beijing,
                None,
                False,
                str(exc),
            )
            LOGGER.exception(
                "daily review AI unavailable for %s; using local fallback", review_day
            )
            response = apply_local_daily_facts(
                build_daily_fallback_response(payload, review_day), payload
            )
        # 三组对照逐笔深度点评：DeepSeek Pro 只读事实，失败不阻塞日报。
        response["trade_review"] = None
        try:
            from tools.trade_review import generate_trade_review

            facts = payload.get("trade_review_facts") or []
            if facts and trade_review_prompt:
                response["trade_review"], _review_usage = generate_trade_review(
                    daily_client, trade_review_prompt, facts
                )
        except Exception as exc:
            LOGGER.warning("trade review generation failed: %s", exc)
            response["trade_review"] = None
        try:
            write_daily_output(
                root,
                review_day,
                payload,
                response,
                budget.counts(day),
                daily_limit=int(config.get("daily_review_limit", 1)),
                monitor_limit=int(config.get("monitor_daily_limit", 29)),
            )
            append_missed_candidate_history(
                root,
                review_day,
                payload.get("unfilled_candidate_review") or {"items": []},
            )
            if not ai_fallback_used:
                append_api_usage(root, "daily_review", current_beijing, usage, True)
            lark_status = send_daily_lark_if_enabled(
                config, root, review_day, payload, response, budget.counts(day)
            )
            results["daily"].append(
                {
                    "date": review_day,
                    "status": "generated",
                    "lark": lark_status,
                    "ai_fallback": ai_fallback_used,
                }
            )
            if lark_status in {"queued", "uncertain", "already_sent", "disabled"}:
                mark_close_trigger_processed(review_source_root, trigger)
            completed_daily_reviews += 1
        except Exception as exc:
            if not ai_fallback_used:
                append_api_usage(
                    root,
                    "daily_review",
                    current_beijing,
                    None,
                    False,
                    str(exc),
                )
            budget.refund_daily(day)
            LOGGER.exception("daily review failed for %s", review_day)
            # Keep the broker-close trigger pending so the next cycle retries it.
            break

    four_hour_prompt = ""
    if bool(config.get("four_hour_pro_enabled", False)) and not dry_run:
        try:
            four_hour_prompt = prompt_text(
                str(config.get("four_hour_prompt_file", "four_hour_review_prompt.txt")),
                config_path,
            )
        except FileNotFoundError:
            LOGGER.exception("four-hour Pro prompt missing; local facts will be sent")
    try:
        four_hour_result = process_due_four_hour_review(
            config, root, current_beijing, dry_run=dry_run,
            pro_client=daily_client, pro_prompt=four_hour_prompt,
        )
        if four_hour_result:
            results["four_hour"].append(four_hour_result)
    except Exception:
        LOGGER.exception("four-hour review failed; next poll can retry")

    if not dry_run:
        results["weekly"] = process_weekly_review(
            config, root, config_path, current_beijing, daily_client
        )

    snapshots, recent_events, calendar = collect_recent_data(root, current_beijing)
    if bool(config.get("independent_ai_enabled", False)) and not bool(config.get("parallel_ai_enabled", False)):
        results["independent_ai"] = []
        if independent_prompt_error:
            results["independent_ai"] = [
                {"status": "CONFIG_ERROR", "error": "independent_ai_prompt_missing"}
            ]
        else:
            try:
                observer_results = process_independent_ai_cycle(
                    config=config,
                    root=root,
                    now_beijing=current_beijing,
                    snapshots=snapshots,
                    events=recent_events,
                    client=client,
                    primary_prompt=independent_prompt,
                    explanation_prompt=divergence_prompt,
                    dry_run=dry_run,
                )
                results["independent_ai"] = observer_results
                if not dry_run:
                    for item in observer_results:
                        usage_type = str(item.get("usage_type", "")).strip()
                        if not usage_type:
                            continue
                        append_api_usage(
                            root,
                            usage_type,
                            current_beijing,
                            item.get("usage") if isinstance(item.get("usage"), dict) else None,
                            bool(item.get("success", False)),
                            str(item.get("error", "")),
                        )
            except Exception as exc:
                LOGGER.exception("independent AI observer cycle failed")
                results["independent_ai"] = [
                    {"status": "OBSERVER_CYCLE_FAILED", "error": type(exc).__name__}
                ]
    pending = update_pending_events(state, recent_events)
    scheduled_due = is_scheduled_monitor_due(
        current_utc, state.get("last_monitor_hour")
    )
    merge_minutes = int(config.get("event_merge_minutes", 10))
    event_due = event_batch_due(
        pending,
        state,
        current_beijing,
        merge_minutes,
        scheduled_due,
    )

    produced_daily = bool(results["daily"])
    yesterday = (current_beijing.date() - timedelta(days=1)).isoformat()
    yesterday_report_exists = (
        root / "Daily_Review" / yesterday / f"Daily_Review_{yesterday}.md"
    ).exists()
    if (
        current_beijing.hour == 10
        and current_beijing.minute == 5
        and (produced_daily or yesterday_report_exists)
        and not event_due
    ):
        scheduled_due = False

    trigger = "scheduled"
    if event_due and scheduled_due:
        trigger = "scheduled+" + choose_event_trigger(pending)
    elif event_due:
        trigger = choose_event_trigger(pending)

    if (scheduled_due or event_due) and snapshots:
        payload_events = pending if pending else recent_events
        payload = compact_monitor_payload(payload_events, snapshots, calendar)
        payload["beijing_time"] = current_beijing.strftime("%Y-%m-%d %H:%M:%S")
        payload["trigger_type"] = trigger
        semantic_signature = semantic_snapshot_signature(snapshots[-1])
        if pending:
            semantic_signature += "|" + "|".join(event_signature(row) for row in pending)
        unchanged = (
            semantic_signature == state.get("last_monitor_signature")
            and not event_due
        )
        if not unchanged:
            if dry_run:
                results["monitor"].append(
                    {"trigger": trigger, "payload": payload, "dry_run": True}
                )
            elif monitor_cooldown_active(state, current_beijing):
                results["monitor"].append(
                    {"trigger": trigger, "status": "cooldown"}
                )
            elif budget.consume_monitor(day):
                try:
                    raw_response, usage = client.call(  # type: ignore[union-attr]
                        monitor_prompt,
                        payload,
                        max_tokens=700,
                    )
                    response = validate_monitor_response(raw_response)
                    response["beijing_time"] = payload["beijing_time"]
                    response["trigger_type"] = (
                        "scheduled"
                        if trigger.startswith("scheduled")
                        else trigger
                    )
                    write_monitor_output(root, response, trigger)
                    append_api_usage(root, "market_monitor", current_beijing, usage, True)
                    results["monitor"].append(
                        {"trigger": trigger, "status": "generated"}
                    )
                    state["last_monitor_signature"] = semantic_signature
                    state.pop("monitor_retry_after", None)
                    if pending:
                        state["last_local_reject_reason"] = latest_local_reject_reason(
                            pending
                        )
                        state["pending_events"] = []
                except Exception as exc:
                    cooldown_minutes = max(1, int(config.get("failure_cooldown_minutes", 30)))
                    state["monitor_retry_after"] = (
                        current_beijing + timedelta(minutes=cooldown_minutes)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    append_api_usage(
                        root,
                        "market_monitor",
                        current_beijing,
                        None,
                        False,
                        str(exc),
                    )
                    LOGGER.exception("market monitor call failed")
            else:
                results["monitor"].append(
                    {"trigger": trigger, "status": "quota_exhausted"}
                )
        if scheduled_due:
            state["last_monitor_hour"] = beijing_hour_key(current_utc)

    state["last_run_beijing"] = current_beijing.strftime("%Y-%m-%d %H:%M:%S")
    if not dry_run and state.get("last_archive_date") != day:
        moved = archive_old_raw_data(
            root,
            day,
            int(config.get("raw_data_retention_days", 90)),
            str(config.get("archive_directory", "")),
        )
        results["archive"] = moved
        state["last_archive_date"] = day
    if not dry_run:
        atomic_write_json(state_path, state)
    return results


def configure_logging(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / f"Review_Service_{datetime.now(BEIJING_TZ):%Y-%m}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="XAUUSD AI monitor and Beijing-time daily review service"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one scheduling cycle and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build payloads without calling DeepSeek or writing state",
    )
    parser.add_argument("--poll-seconds", type=int, default=None)
    args = parser.parse_args()
    config = load_json(args.config)
    root = resolve_data_root(config, args.config)
    configure_logging(root / "Logs")
    if args.once or args.dry_run:
        print(
            json.dumps(
                run_once(args.config, dry_run=args.dry_run),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    poll_seconds = args.poll_seconds or int(config.get("poll_seconds", 60))
    while True:
        try:
            run_once(args.config)
        except Exception:
            LOGGER.exception("review service cycle failed; EA trading is unaffected")
        time.sleep(max(30, poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
