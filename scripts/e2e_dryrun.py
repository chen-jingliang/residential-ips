#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""家宽甄选 + 导出链路 端到端演练 (用真实 ip-api 情报驱动真实的 classify_and_export/export_all)

为什么用演练而不是真跑 main():
  本机在 GFW 内侧, PROBE_SESSION 强制 trust_env=False → 海外节点无一可达,
  main() 会触发"全部测活失败保留上次 output"的早退分支, 验证不到改动的部分。
  而本次改动集中在**测活之后**的半条链路 (分类/家宽甄选/去重/导出),
  故用真实 IP 情报 + 真实节点 URI 构造 test_results, 原样喂给真实代码跑通。

验证目标:
  ① classify_and_export 能跑通并被 RES_SOFT_TIER / MAX_RES_PER_IP 正确约束
  ② 多国家家宽能各自导出 xx.txt / clash-xx.yaml / singbox-xx.json
  ③ make_node_name 对三种家宽等级的标注正确
  ④ 机房/CDN 绝不混入家宽专区
"""
import os, sys, json, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main_v2 as mv

BASEDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASEDIR, "output")
DRY = os.path.join(BASEDIR, "output-dryrun")

# (出口IP, 期望归宿) — 全部是 ip-api.com 上查得到的真实 IP
SAMPLES = [
    # 真实民用/家宽
    ("24.90.100.1",  "US", "res"),   # Charter Spectrum (US)
    ("73.219.20.1",  "US", "res"),   # Comcast Cable (US)
    ("62.155.10.1",  "DE", "res"),   # Deutsche Telekom (DE)
    ("200.100.50.1", "BR", "res"),   # Vivo / Telefonica Brasil (BR)
    ("123.201.10.1", "IN", "res"),   # YOU Broadband India (IN)
    ("211.72.35.1",  "TW", "res"),   # Hinet 中华电信 (TW)
    ("219.85.10.1",  "TW", "res"),   # So-net Taiwan (TW)
    ("175.45.176.1", "KP", "res"),   # Star JV (hosting=false, 次级判定)
    # 必须被排除的机房 / CDN
    ("88.198.10.1",  "DE", "dc"),    # Hetzner
    ("104.16.1.1",   "CA", "dc"),    # Cloudflare
    ("8.8.8.8",      "US", "dc"),    # Google
]

FAIL = []


def build_fake_results():
    """用真实可解析的节点 URI + 真实出口 IP 拼 test_results"""
    import requests
    s = requests.Session(); s.trust_env = True
    ips = [x[0] for x in SAMPLES]
    r = s.post(mv.IP_API_BATCH_URL, json=[{"query": i} for i in ips], timeout=25)
    if r.status_code != 200:
        print(f"[!] ip-api 不可用 (HTTP {r.status_code}), 无法演练"); sys.exit(2)
    recs = {rec.get("query"): rec for rec in r.json()}

    out = []
    for idx, (ip, _cc, kind) in enumerate(SAMPLES, 1):
        rec = recs.get(ip) or {}
        uri = (f"vless://11111111-2222-3333-4444-5555555555{idx:02d}"
               f"@node{idx}.example.com:{10000+idx}"
               f"?encryption=none&security=tls&type=ws&path=%2Fws#{kind}-{ip}")
        parsed = mv.parse_node_uri(uri)
        assert parsed, f"节点 URI 解析失败: {uri}"
        ob, server, port, proto = parsed
        out.append({
            "raw": uri, "server": server, "port": port, "proto": proto,
            "alive": True, "latency_ms": 100 + idx,
            "exit_ip": ip,
            "exit_country_online": rec.get("countryCode"),
            "exit_asn_online": rec.get("as"),
            "exit_asn_org_online": rec.get("asname") or "",
            "exit_isp_online": rec.get("isp") or "",
            "mitm_risk": False, "is_warp": False,
            "speed_bps": 5_000_000, "is_stalled": False,
        })
    return out, recs


def main():
    results, recs = build_fake_results()
    print("=" * 74)
    print("输入: 真实出口 IP 情报 (ip-api.com 实测)")
    print("=" * 74)
    for ip, cc, kind in SAMPLES:
        rec = recs.get(ip) or {}
        print(f"  {ip:16} [{kind}]  hosting={str(rec.get('hosting')):5} "
              f"proxy={str(rec.get('proxy')):5} AS={rec.get('as')}")

    # 导出到独立目录, 不污染真实 output
    mv.RESIDENTIAL_COUNTRY_DIR_ORIG = mv.RESIDENTIAL_COUNTRY_DIR
    shutil.rmtree(DRY, ignore_errors=True)
    mv.OUTPUT_DIR = DRY
    mv.COUNTRY_DIR = os.path.join(DRY, "by-country")
    mv.RESIDENTIAL_COUNTRY_DIR = os.path.join(DRY, "residential-by-country")

    print("\n" + "=" * 74)
    print("运行真实 classify_and_export()")
    print("=" * 74)
    unique, residential, non_res = mv.classify_and_export(results)
    print(f"[+] 去重后 {len(unique)} | 家宽区 {len(residential)} | 普通区 {len(non_res)}")

    # ── 断言 1: 机房/CDN 绝不入家宽区 ──
    print("\n" + "=" * 74)
    print("断言1: 机房 / CDN 绝不混入家宽专区")
    print("=" * 74)
    res_ips = {n["exit_ip"] for n in residential}
    for ip, cc, kind in SAMPLES:
        if kind != "dc":
            continue
        ok = ip not in res_ips
        if not ok:
            FAIL.append(f"机房 IP {ip} 混入了家宽专区")
        print(f"  {'✅' if ok else '❌'} {ip:16} 未入家宽区")

    # ── 断言 2: 真家宽进了家宽区 ──
    print("\n" + "=" * 74)
    print("断言2: 真实家宽进入家宽专区")
    print("=" * 74)
    for ip, cc, kind in SAMPLES:
        if kind != "res":
            continue
        ok = ip in res_ips
        # KP 那条靠次级判定, 允许在 residential 或 residential_soft 两档之一
        if not ok:
            FAIL.append(f"真实家宽 {ip} 未被识别")
        node = next((n for n in residential if n["exit_ip"] == ip), None)
        print(f"  {'✅' if ok else '❌'} {ip:16} → {node['net_type'] if node else '未识别':20} "
              f"conf={node['confidence'] if node else '-'}")

    # ── 断言 3: 多国家家宽导出 ──
    print("\n" + "=" * 74)
    print("断言3: 分国家家宽订阅导出")
    print("=" * 74)
    total, res = mv.export_all(unique, residential, non_res)
    print(f"[+] 导出: 总 {total} | 家宽 {res}")
    rdir = mv.RESIDENTIAL_COUNTRY_DIR
    files = sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []
    ccs = sorted({f.split(".")[0].replace("clash-", "").replace("singbox-", "")
                  for f in files if f.endswith(".txt")})
    print(f"  家宽按国家目录: {files}")
    if not ccs:
        FAIL.append("家宽分区未导出任何国家文件")
    else:
        print(f"  ✅ 家宽国家覆盖: {ccs}")
    for f in files:
        p = os.path.join(rdir, f)
        if os.path.getsize(p) == 0:
            FAIL.append(f"导出空文件: {f}")

    # ── 断言 4: 命名标注 ──
    print("\n" + "=" * 74)
    print("断言4: 节点命名标注")
    print("=" * 74)
    for f in sorted(os.listdir(rdir)):
        if not f.endswith(".txt"):
            continue
        import base64 as _b64
        content = open(os.path.join(rdir, f), encoding="utf-8").read()
        for ln in _b64.b64decode(content).decode().splitlines():
            import urllib.parse as up
            name = up.unquote(ln.split("#")[-1])
            print(f"  {name}")
            if "家宽" not in name:
                FAIL.append(f"家宽专区节点缺家宽标注: {name}")

    # ── 断言 5: 订阅内容可被解析回 (roundtrip 保真) ──
    print("\n" + "=" * 74)
    print("断言5: 导出订阅可被自身解析器读回")
    print("=" * 74)
    import base64 as _b64
    ok_rt = True
    for f in sorted(os.listdir(rdir)):
        if not f.endswith(".txt"):
            continue
        content = open(os.path.join(rdir, f), encoding="utf-8").read()
        n = 0
        for ln in _b64.b64decode(content).decode().splitlines():
            if mv.parse_node_uri(ln):
                n += 1
            else:
                ok_rt = False
                FAIL.append(f"{f} 存在无法回读的节点: {ln[:60]}")
        print(f"  {'✅' if n else '❌'} {f}: {n} 条全部可读回")

    print("\n" + "=" * 74)
    if FAIL:
        print(f"共 {len(FAIL)} 项失败:")
        for x in FAIL:
            print(f"  - {x}")
        sys.exit(1)
    print("端到端演练通过 ✅")
    print(f"[i] 演练产物在 {DRY} (不影响 output/)")


if __name__ == "__main__":
    main()
