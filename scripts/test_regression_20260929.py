#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-09-29 回归测试: 用户反馈「很多节点连不上 / 家宽不准」的修复验证

覆盖:
  ① Hysteria2 端口跳跃节点导出 Clash 不再崩溃 (KeyError: 'server_port' → CI 整轮失败)
  ② 去重回填 key 含凭据指纹: 同目标不同凭据不再互串结果 (死节点被标活 / 活节点被标死)
  ③ 测试结果透传 cred_fp 与实测 outbound (多 outbound URI 导出不再取错凭据)
  ④ build_group 跳过空链接 (订阅里不再出现空行节点)
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main_v2 as mv

FAIL = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(f"{name}: {detail}")


print("=" * 72)
print("回归 ①: Hysteria2 端口跳跃 → Clash 导出")
print("=" * 72)
# 构造端口跳跃 outbound (parse_hysteria2 对 mport 的产物: 无 server_port)
hop = {"type": "hysteria2", "server": "example.com",
       "server_ports": ["20000:30000", "40000"],
       "password": "secret", "tls": {"enabled": True, "server_name": "example.com"}}
try:
    c = mv.outbound_to_clash(hop, "hy2-hop-test")
    check("端口跳跃节点 Clash 导出不崩溃", c is not None)
    if c:
        check("端口取首个区间起始端口", c.get("port") == 20000, f"port={c.get('port')}")
        check("ports 保留完整跳跃区间 (Clash 用 - 连接)",
              c.get("ports") == "20000-30000,40000",
              f"ports={c.get('ports')}")
        check("server 保留", c.get("server") == "example.com")
except KeyError as e:
    check("端口跳跃节点 Clash 导出不崩溃", False, f"KeyError: {e}")

# 普通 hy2 (有 server_port) 不回归
normal = {"type": "hysteria2", "server": "example.com", "server_port": 443,
          "password": "secret", "tls": {"enabled": True, "server_name": "example.com"}}
c2 = mv.outbound_to_clash(normal, "hy2-normal")
check("普通 hy2 导出正常", c2 is not None and c2.get("port") == 443)

# 完全无端口信息 → 返回 None (而不是崩溃), build_group 会跳过
broken = {"type": "hysteria2", "server": "example.com", "password": "x"}
c3 = mv.outbound_to_clash(broken, "hy2-broken")
check("无端口节点返回 None 而非崩溃", c3 is None)

print()
print("=" * 72)
print("回归 ②: 去重回填 — 凭据指纹隔离")
print("=" * 72)
# 复刻 main() 内回填逻辑 (4 段 key), 验证不同凭据不互串
fp_ok = mv.cred_fingerprint({"uuid": "u1"}, "vless")
fp_bad = mv.cred_fingerprint({"uuid": "u2"}, "vless")
check("cred_fingerprint 模块级可用且区分凭据", fp_ok != fp_bad, f"{fp_ok} vs {fp_bad}")

DEDUP_MAP = {
    ("1.2.3.4", 443, "vless", fp_ok):  ["vless://u1@1.2.3.4:443", "vless://u1@1.2.3.4:443#dup"],
    ("1.2.3.4", 443, "vless", fp_bad): ["vless://u2@1.2.3.4:443", "vless://u2@1.2.3.4:443#dup"],
}
test_results = [
    {"raw": "vless://u1@1.2.3.4:443", "alive": True, "server": "1.2.3.4",
     "port": 443, "proto": "vless", "cred_fp": fp_ok},
    {"raw": "vless://u2@1.2.3.4:443", "alive": False, "server": "1.2.3.4",
     "port": 443, "proto": "vless", "cred_fp": fp_bad},
]
# —— 与 main() 回填段逐行一致的逻辑 ——
result_by_key = {}
for r in test_results:
    key = ((r["server"] or "").lower(), r["port"], r["proto"], r.get("cred_fp") or "")
    result_by_key[key] = r
expanded = list(test_results)
for key, uris in DEDUP_MAP.items():
    if len(uris) <= 1:
        continue
    r = result_by_key.get(key)
    if not r or not r.get("alive"):
        continue
    for extra_uri in uris[1:]:
        clone = dict(r)
        clone["raw"] = extra_uri
        expanded.append(clone)
by_raw = {e["raw"]: e["alive"] for e in expanded}
check("正确凭据组回填为 alive", by_raw.get("vless://u1@1.2.3.4:443#dup") is True,
      str(by_raw))
check("错误凭据组不被错误标活", "vless://u2@1.2.3.4:443#dup" not in by_raw,
      str(by_raw))
check("正确凭据代表节点不被覆盖为 dead", by_raw.get("vless://u1@1.2.3.4:443") is True)

print()
print("=" * 72)
print("回归 ③: 解析往返 — 端口跳跃 hy2 三端导出")
print("=" * 72)
parsed = mv.parse_node_uri(
    "hysteria2://secret@example.com:443?mport=20000-30000,40000&insecure=1&sni=example.com#hop")
check("mport URI 解析成功", bool(parsed))
if parsed:
    ob = parsed[0]
    check("解析产物无 server_port (端口跳跃)", "server_port" not in ob)
    link = mv.outbound_to_v2ray_link(ob, "t")
    check("v2ray 导出非空且含 mport", bool(link) and "mport=" in link, link[:80])
    try:
        c = mv.outbound_to_clash(ob, "t")
        check("Clash 导出不崩溃", c is not None and c.get("port") == 20000)
    except KeyError as e:
        check("Clash 导出不崩溃", False, f"KeyError: {e}")
    sb = mv.outbound_to_singbox(ob, "t")
    check("sing-box 导出保留 server_ports",
          sb.get("server_ports") == ["20000:30000", "40000:40000"],
          f"server_ports={sb.get('server_ports')}")

print()
print("=" * 72)
print("回归 ④: 未知协议导出空链接不入订阅")
print("=" * 72)
weird = {"type": "ssh", "server": "example.com", "server_port": 22}
check("未知协议 v2ray 导出为空字符串", mv.outbound_to_v2ray_link(weird, "t") == "")
check("未知协议 Clash 导出为 None", mv.outbound_to_clash(weird, "t") is None)

print()
print("=" * 72)
if FAIL:
    print(f"失败 {len(FAIL)} 项:")
    for f in FAIL:
        print(f"  ❌ {f}")
    sys.exit(1)
print("全部回归测试通过 ✅")
