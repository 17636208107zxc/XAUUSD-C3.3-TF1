from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "XAUUSD_M5_AI_Pullback_V3_8_AI_MONITOR.mq5"
OUTPUT = ROOT / "XAUUSD_M5_AI_Pullback_V3_8_AI_MONITOR_SINGLE.mq5"
LOCAL_INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"\s*$')
SYSTEM_INCLUDE_RE = re.compile(r'^\s*#include\s+<([^>]+)>\s*$')


def expand(path: Path, seen: set[Path], system_includes: set[str]) -> str:
    path = path.resolve()
    if path in seen:
        return f"// [单文件合并] 已内联，跳过重复模块: {path.name}\n"
    seen.add(path)

    output: list[str] = [f"\n// ===== BEGIN INLINE: {path.relative_to(ROOT)} =====\n"]
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        local_match = LOCAL_INCLUDE_RE.match(line.rstrip("\r\n"))
        if local_match:
            child = (path.parent / local_match.group(1)).resolve()
            if not child.exists():
                raise FileNotFoundError(f"local include not found: {child}")
            output.append(expand(child, seen, system_includes))
            continue

        system_match = SYSTEM_INCLUDE_RE.match(line.rstrip("\r\n"))
        if system_match:
            include_name = system_match.group(1)
            if include_name not in system_includes:
                system_includes.add(include_name)
                output.append(f"#include <{include_name}>\n")
            else:
                output.append(
                    f"// [单文件合并] 已保留一次标准库: <{include_name}>\n"
                )
            continue

        output.append(line)

    output.append(f"// ===== END INLINE: {path.relative_to(ROOT)} =====\n")
    return "".join(output)


def main() -> None:
    if not ENTRY.exists():
        raise FileNotFoundError(ENTRY)
    banner = (
        "// XAUUSD M5 AI Pullback V3.8 AI盯盘数据采集单文件版\n"
        "// 保留：V3.7全部交易逻辑、挂单状态修复、M5专用、周末与换日风控。\n"
        "// 新增：只读M5/M15/H1/H4快照、策略事件、USD高影响经济日历CSV。\n"
        "// AI盯盘和每日复盘由外部Python程序完成，不参与任何交易决策。\n"
        "// 部署：复制本文件到 MQL5/Experts 后使用 MetaEditor 编译。\n"
        "// 仅依赖 MT5 自带标准库 <Trade/Trade.mqh>。\n"
    )
    content = banner + expand(ENTRY, set(), set())
    OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"generated: {OUTPUT}")


if __name__ == "__main__":
    main()
