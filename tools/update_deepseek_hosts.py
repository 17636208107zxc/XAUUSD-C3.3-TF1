"""Keep api.deepseek.com real A records pinned in the Windows hosts file.

The EA's MQL5 WebRequest bypasses Clash and can land on polluted DNS answers,
so its DeepSeek review calls intermittently hit wrong CDN edges (403/404).
Pinning the real IPs in hosts bypasses DNS entirely. This script runs daily:
it queries two clean DoH resolvers, compares with the pinned section, and
updates the hosts file only when the IP set actually changed.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import socket
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path

HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts")
MARKER = "# DeepSeek API real IPs"
FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
DOH_SERVERS = (
    ("ali", "https://dns.alidns.com/resolve?name=api.deepseek.com&type=A", {}),
    (
        "tencent",
        "https://doh.pub/dns-query?name=api.deepseek.com&type=A",
        {"accept": "application/dns-json"},
    ),
)


def _fetch_ips() -> dict[str, set[str]]:
    collected: dict[str, set[str]] = {}
    for name, url, headers in DOH_SERVERS:
        ips: set[str] = set()
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            for answer in payload.get("Answer", []):
                if int(answer.get("type", 0)) != 1:
                    continue
                data = str(answer.get("data", "")).strip()
                try:
                    addr = ipaddress.IPv4Address(data)
                except ValueError:
                    continue
                if addr.is_global and addr not in FAKE_IP_NET:
                    ips.add(str(addr))
        except Exception:
            ips = set()
        collected[name] = ips
    return collected


def _resolve_ips(collected: dict[str, set[str]]) -> list[str]:
    valid = [ips for ips in collected.values() if ips]
    if not valid:
        return []
    common = set.intersection(*valid)
    if common:
        return sorted(common)
    return sorted(set.union(*valid))


def _verify_ip(ip: str) -> bool:
    """True when the IP really serves api.deepseek.com over TLS/SNI."""
    context = ssl.create_default_context()
    try:
        raw = socket.create_connection((ip, 443), timeout=8)
        with context.wrap_socket(raw, server_hostname="api.deepseek.com") as sock:
            sock.sendall(
                b"GET /models HTTP/1.1\r\n"
                b"Host: api.deepseek.com\r\n"
                b"Connection: close\r\n\r\n"
            )
            data = sock.recv(4096)
            status_line = data.split(b"\r\n", 1)[0]
            status = int(status_line.split(b" ", 2)[1])
            return status in (200, 401)
    except Exception:
        return False


def target_ips() -> list[str]:
    """Current verified real IPs: DoH candidates that actually serve the API."""
    candidates = _resolve_ips(_fetch_ips())
    return [ip for ip in candidates if _verify_ip(ip)]


def _strip_section(lines: list[str]) -> list[str]:
    kept: list[str] = []
    skipping = False
    for line in lines:
        if line.strip().startswith(MARKER):
            skipping = True
            continue
        if skipping:
            if not line.strip():
                skipping = False
            continue
        kept.append(line)
    return kept


def _hosts_ip_set(lines: list[str]) -> set[str]:
    result: set[str] = set()
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "api.deepseek.com":
            result.add(parts[0])
    return result


def _new_block(ips: list[str]) -> list[str]:
    header = f"{MARKER} - updated {datetime.now():%Y-%m-%d %H:%M}"
    return [header] + [f"{ip} api.deepseek.com" for ip in sorted(ips)] + [""]


def _write_hosts(kept: list[str], block: list[str]) -> None:
    backup = HOSTS_PATH.with_name("hosts.deepseek-backup")
    backup.write_text(
        HOSTS_PATH.read_text(encoding="ascii", errors="replace"), encoding="ascii"
    )
    kept_text = "\n".join(kept).rstrip("\n")
    if kept_text:
        kept_text += "\n"
    HOSTS_PATH.write_text(kept_text + "\n".join(block) + "\n", encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update DeepSeek pinned IPs in the Windows hosts file"
    )
    parser.add_argument("--log-dir", type=Path, default=None)
    args = parser.parse_args()

    def log(message: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}"
        print(line)
        if args.log_dir:
            args.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = args.log_dir / "DeepSeek_Hosts_Updater.log"
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    collected = _fetch_ips()
    ips = target_ips()
    log(
        "DoH解析: ali="
        + ",".join(sorted(collected.get("ali", [])))
        + " | tencent="
        + ",".join(sorted(collected.get("tencent", [])))
    )
    log("验证通过的真实IP: " + (", ".join(ips) if ips else "无"))
    if not ips:
        log("未获取到有效IP，保留现有hosts不变")
        return 0
    original_lines = HOSTS_PATH.read_text(encoding="ascii", errors="replace").splitlines()
    current = _hosts_ip_set(original_lines)
    target = set(ips)
    if current == target:
        log(f"无需更新，当前已固定: {', '.join(sorted(current))}")
        return 0
    _write_hosts(_strip_section(original_lines), _new_block(ips))
    log(
        "hosts已更新: "
        + (", ".join(sorted(current)) if current else "无")
        + " -> "
        + ", ".join(sorted(target))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
