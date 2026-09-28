#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实证: 公开 OpenVPN 配置的服务器是不是家宽 IP?

⚠️ 本机 DNS 已被 fake-ip 全面劫持 (连不存在域名都返回 198.20.0.x), 故本脚本
   只处理 remote 里直接写**字面 IPv4** 的配置, 完全绕开 DNS, 保证结论可信。
"""
import os, re, sys, json, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main_v2 as mv
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 公开可访问的 .ovpn 文件 (GitHub raw), 混合商业 VPN 与个人/社区搭建
SOURCES = [
    "https://raw.githubusercontent.com/bubnovd/RouterOS-scripts/main/OVPN/client1.ovpn",
    "https://raw.githubusercontent.com/777nq/ValoVcEgypt/main/Valorant.ovpn",
    "https://raw.githubusercontent.com/BarrRedKola/torrentns/main/nordVPN/sg220.ovpn",
    "https://raw.githubusercontent.com/Vinicius-Tavares-Silva/yt-transcribe/main/proton.ovpn",
    "https://raw.githubusercontent.com/matteosox/nba/main/vpn/config.ovpn",
    "https://raw.githubusercontent.com/chemputer/PrivadoVPN-Config/main/Tokyo.ovpn",
]
# 经典免费 OpenVPN 站点, 看是否还活着 / 提供什么
FREE_SITES = [
    "https://www.vpnbook.com/freevpn",
    "https://www.freeopenvpn.com/",
    "https://openvpn.net/community-resources/",
]

REMOTE_RE = re.compile(r"^\s*remote\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})\s+(\d+)", re.M)


def sess():
    s = requests.Session()
    s.trust_env = True
    return s


def classify(ips):
    URL = ("http://ip-api.com/batch?fields=status,countryCode,isp,org,as,"
           "asname,reverse,mobile,proxy,hosting,query")
    s = sess()
    out, recs = {}, {}
    for i in range(0, len(ips), 100):
        try:
            r = s.post(URL, json=[{"query": x} for x in ips[i:i + 100]], timeout=40)
            for rec in r.json():
                recs[rec.get("query")] = rec
        except Exception as e:
            print(f"    [!] ip-api 失败: {str(e)[:50]}")
        time.sleep(1.2)
    for ip in ips:
        rec = recs.get(ip)
        if not rec or rec.get("status") != "success":
            out[ip] = ("unresolved", 0, {})
            continue
        net, conf = mv.classify_network_type(ip, rec.get("countryCode", ""), None, "", rec)
        out[ip] = (net, conf, rec)
    return out


def tally(label, res):
    c = Counter(v[0] for v in res.values())
    n = len(res)
    print(f"\n----- {label} (n={n}) -----")
    if not n:
        print("    无样本")
        return
    for k, v in c.most_common():
        print(f"    {k:<18} {v:>4}  ({v / n * 100:.1f}%)")
    strict = c.get("residential", 0) + c.get("mobile", 0)
    print(f"    严格家宽率 {strict / n * 100:.1f}%   |  含疑似 {(strict + c.get('residential_soft',0))/n*100:.1f}%")


if __name__ == "__main__":
    print("=" * 72)
    print("OpenVPN 配置服务器归属实测 (仅用字面 IP, 绕开被劫持的 DNS)")
    print("=" * 72)

    s = sess()
    texts = []
    for u in SOURCES:
        try:
            r = s.get(u, headers=UA, timeout=25)
            if r.status_code == 200:
                texts.append((u.split("/")[-1], r.text))
        except Exception as e:
            print(f"  [!] {u.split('/')[-1]}: {str(e)[:40]}")
    print(f"\n[*] 抓到 {len(texts)} 份 .ovpn")

    pairs = set()
    for name, txt in texts:
        for ip, port in REMOTE_RE.findall(txt):
            pairs.add((ip, int(port)))
    print(f"[*] 字面 IP 端点 {len(pairs)} 个")

    if pairs:
        res = classify(sorted({ip for ip, _ in pairs}))
        for ip, (net, conf, rec) in sorted(res.items()):
            print(f"    {ip:>16} {net:<16} hosting={rec.get('hosting')} proxy={rec.get('proxy')} "
                  f"| {rec.get('org') or rec.get('isp') or '-'}")
        tally("OpenVPN 公开配置", res)

    # 免费 OpenVPN 站点存活情况
    print("\n" + "=" * 72)
    print("免费 OpenVPN 站点现状")
    print("=" * 72)
    for u in FREE_SITES:
        try:
            r = s.get(u, headers=UA, timeout=25)
            zips = re.findall(r'href="([^"]*\.zip)"', r.text)
            ovpnlinks = re.findall(r'href="([^"]*\.ovpn)"', r.text)
            print(f"  {u}")
            print(f"    HTTP {r.status_code} | 页面 {len(r.text)} 字节 | zip 链接 {len(zips)} | ovpn 链接 {len(ovpnlinks)}")
        except Exception as e:
            print(f"  {u}\n    失败: {str(e)[:60]}")
