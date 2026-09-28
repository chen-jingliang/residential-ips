#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅源扩容收益对比: 旧 BASE 源 vs 扩容后全量源

对比口径:
  raw         —— 抓回来的原始 URI 去重数
  fingerprint —— (host, port, proto, 凭据) 指纹去重后的候选数, 这才是真正值得测活的数量
  regression  —— 旧源里有没有因为本次改造而丢失的候选 (必须为 0)

用法: python scripts/compare_sources.py
"""
import os, sys, json, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main_v2 as mv


def cred_fp(ob, proto):
    """复刻 main_v2 的凭据指纹口径"""
    try:
        if proto == "shadowsocks":
            return f"{ob.get('method','')}|{ob.get('password','')}"
        if proto == "hysteria2":
            return f"{ob.get('password','') or ''}|{ob.get('server_ports','')}"
        if proto == "tuic":
            return f"{ob.get('uuid','')}|{ob.get('password','')}"
        for k in ("uuid", "password", "user_id"):
            if ob.get(k):
                return str(ob[k])
    except Exception:
        pass
    return ""


def collect(label, urls):
    """抓取一组源并按凭据指纹去重。注意参数顺序: (label, urls)"""
    fps, raws, detail = set(), set(), []

    def _f(url):
        for attempt in range(2):
            try:
                r = mv.http_get(url, timeout=30)
                if r.status_code == 200:
                    return url, mv.extract_nodes_from_text(r.text) or set(), None
                if attempt == 1:
                    return url, set(), f"HTTP {r.status_code}"
            except Exception as e:
                if attempt == 1:
                    return url, set(), str(e)[:60]
            time.sleep(0.6)
        return url, set(), "retry exhausted"

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=10) as ex:
        for url, nodes, err in ex.map(_f, urls):
            raws |= nodes
            detail.append((url, len(nodes), err))
            for n in nodes:
                p = mv.parse_node_uri(n)
                if p:
                    ob, server, port, proto = p
                    fps.add(((server or "").lower(), port, proto, cred_fp(ob, proto)))

    print(f"\n{'=' * 66}\n{label}   ({len(urls)} 个源, {time.time() - t0:.0f}s)\n{'=' * 66}")
    ok = sorted([c for u, c, e in detail if not e], reverse=True)
    bad = [(u, e) for u, c, e in detail if e]
    print(f"  raw 去重 URI       : {len(raws)}")
    print(f"  凭据指纹去重(可测) : {len(fps)}")
    print(f"  抓取成功 / 失败    : {len(detail) - len(bad)} / {len(bad)}")
    if ok:
        print(f"  单源节点数 TOP10   : {ok[:10]}")
    for u, e in bad:
        print(f"    [FAIL] {e:<40} {u}")
    return fps, raws


if __name__ == "__main__":
    old_fps, old_raws = collect("【扩容前】BASE_SOURCE_URLS", list(mv.BASE_SOURCE_URLS))
    new_fps, new_raws = collect("【扩容后】SOURCE_URLS (BASE + EXTRA)", list(mv.SOURCE_URLS))

    print(f"\n{'=' * 66}\n净增收益\n{'=' * 66}")
    print(f"  raw 去重 URI       : {len(old_raws)} -> {len(new_raws)}   (+{len(new_raws) - len(old_raws)})")
    print(f"  凭据指纹去重(可测) : {len(old_fps)} -> {len(new_fps)}   (+{len(new_fps) - len(old_fps)})")
    print(f"  纯新增候选         : {len(new_fps - old_fps)}")
    lost = len(old_fps - new_fps)
    print(f"  旧源丢失           : {lost}   {'✅ 无回归' if lost == 0 else '❌ 有回归! 检查 BASE 源是否被误删'}")

    json.dump({
        "old_raw": len(old_raws), "new_raw": len(new_raws),
        "old_candidates": len(old_fps), "new_candidates": len(new_fps),
        "delta": len(new_fps) - len(old_fps), "regression_lost": lost,
    }, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "compare_result.json"), "w"), indent=1)
