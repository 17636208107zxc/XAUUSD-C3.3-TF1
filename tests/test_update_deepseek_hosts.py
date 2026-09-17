from tools.update_deepseek_hosts import (
    _hosts_ip_set,
    _new_block,
    _resolve_ips,
    _strip_section,
)


def test_resolve_ips_prefers_common_and_falls_back_to_union():
    collected = {
        "ali": {"1.1.1.1", "2.2.2.2"},
        "tencent": {"2.2.2.2", "3.3.3.3"},
    }
    assert _resolve_ips(collected) == ["2.2.2.2"]

    disjoint = {"ali": {"1.1.1.1"}, "tencent": {"3.3.3.3"}}
    assert _resolve_ips(disjoint) == ["1.1.1.1", "3.3.3.3"]

    assert _resolve_ips({"ali": set(), "tencent": set()}) == []


def test_strip_section_only_removes_marked_deepseek_block():
    lines = [
        "127.0.0.1 localhost",
        "",
        "# DeepSeek API real IPs - updated 2026-08-14 13:33",
        "223.221.177.184 api.deepseek.com",
        "49.119.123.181 api.deepseek.com",
        "",
        "0.0.0.0 www.example.com",
    ]
    kept = _strip_section(lines)
    assert "127.0.0.1 localhost" in kept
    assert "0.0.0.0 www.example.com" in kept
    assert not any("api.deepseek.com" in line for line in kept)


def test_hosts_ip_set_and_new_block_roundtrip():
    block = _new_block(["60.164.4.101", "49.119.123.181", "223.221.177.184"])
    assert block[0].startswith("# DeepSeek API real IPs")
    assert _hosts_ip_set(block) == {
        "60.164.4.101",
        "49.119.123.181",
        "223.221.177.184",
    }
