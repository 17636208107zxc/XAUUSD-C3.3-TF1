from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path
from typing import Any

SYSTEM_PROMPT = """你是一个严格但不过度过滤的 MT5 XAUUSD M5已收盘周期 EA V3.8 候选交易信号过滤器。
你只能审核EA已经产生的候选信号，不能寻找机会、改变方向、决定仓位、修改止损止盈或绕过风控。
候选发送前，EA已执行共同的本地账户安全、主要结构、点差、止损和风险收益准备，并已计算候选的数值风险字段。
Serialized route evidence fields: signal_route, fib_path_valid, ema_h23_path_valid, three_bar_impulse_valid, three_bar_failure_reason.
FIB_PA表示fib_path_valid=true并且同方向Price Action有效。
EMA_H23表示BUY的EMA20附近H2/H3路径；EMA_L23表示SELL的EMA20附近L2/L3路径。
fib_path_valid=false is allowed for EMA_H23 and EMA_L23。
BOTH表示两条入场路径都通过，但仍然只有一个候选。
three_bar_impulse_valid is soft evidence。三根HH/HL或LL/LH推进及其总幅度仅作趋势上下文；three_bar_failure_reason解释该软证据为何失败。
three_bar_impulse_valid=false alone must not reject a valid candidate。
Not every candidate has passed all Fib/three-bar/H2H3/second-PA hard rules. 不要重新实施已废弃的通用本地入场硬门禁。
AI是第二层数据一致性和风险过滤器，应重点审核方向、signal_route、序列化证据和风险字段是否一致，以及是否逆主要趋势、结构明确失效、明显过度延伸、核心数据缺失或存在真实风险。
没有S/R、背离或精确50% Fib不能单独否决；不要因为软证据失败或信号不完美就拒绝。
confidence表示候选值得继续执行的综合信心，不是涨跌概率。正常合格信号应为70分以上。
只能返回合法JSON。禁止Markdown、代码块、JSON外文字和附加字段。JSON字段必须且只能是allow_trade、confidence、reason。"""


def validate_decision(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"allow_trade", "confidence", "reason"}:
        raise ValueError("decision must contain exact fields")
    allow = value["allow_trade"]
    confidence = value["confidence"]
    reason = value["reason"]
    if type(allow) is not bool:
        raise ValueError("allow_trade must be bool")
    if type(confidence) is not int or not 0 <= confidence <= 100:
        raise ValueError("confidence must be integer from 0 to 100")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be non-empty string")
    return {"allow_trade": allow, "confidence": confidence, "reason": reason}


def load_completed_ids(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["SignalID"] for row in csv.DictReader(handle) if row.get("SignalID")}


def run_batch(input_path: Path, output_path: Path, client: Any, model: str,
              timeout: float = 10.0, retries: int = 2) -> dict[str, int]:
    completed = load_completed_ids(output_path)
    stats = {"processed": 0, "skipped": 0, "errors": 0}
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    with input_path.open("r", encoding="utf-8-sig", newline="") as source, \
         output_path.open("a", encoding="utf-8", newline="") as target:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=["SignalID", "AllowTrade", "Confidence", "Reason"])
        if write_header:
            writer.writeheader()
            target.flush()
        seen: set[str] = set()
        for row in reader:
            signal_id = (row.get("SignalID") or "").strip()
            if not signal_id or signal_id in seen:
                raise ValueError(f"missing or duplicate SignalID: {signal_id!r}")
            seen.add(signal_id)
            if signal_id in completed:
                stats["skipped"] += 1
                continue
            payload = row.get("PayloadJSON") or ""
            json.loads(payload)
            last_error: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                                  {"role": "user", "content": payload}],
                        response_format={"type": "json_object"}, stream=False,
                        max_tokens=300, timeout=timeout,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                    decision = validate_decision(json.loads(response.choices[0].message.content))
                    writer.writerow({"SignalID": signal_id,
                                     "AllowTrade": str(decision["allow_trade"]).lower(),
                                     "Confidence": decision["confidence"],
                                     "Reason": decision["reason"]})
                    target.flush()
                    os.fsync(target.fileno())
                    stats["processed"] += 1
                    break
                except Exception as exc:  # transport/SDK errors are bounded here
                    last_error = exc
                    if attempt < retries:
                        time.sleep(min(2 ** attempt, 8))
            else:
                stats["errors"] += 1
                raise RuntimeError(f"SignalID {signal_id} failed after {retries + 1} attempts") from last_error
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch DeepSeek filter for MT5 candidate CSV")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        parser.error("DEEPSEEK_API_KEY environment variable is required")
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    print(run_batch(args.input, args.output, client, args.model, args.timeout, args.retries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
