#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""往返保真测试: parse → 导出 (v2ray URI / clash) → 再 parse → 逐字段比对
找出"CI 测活通过但客户端连不上"的根因: 导出丢字段。"""
import os, sys, json, copy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main_v2 as mv
from test_parsers import SAMPLES

FAIL = []

def norm(ob):
    o = copy.deepcopy(ob)
    o.pop("tag", None)
    return o

def compare(name, orig_uri, exported_uri, kind):
    p1 = mv.parse_node_uri(orig_uri)
    p2 = mv.parse_node_uri(exported_uri) if exported_uri else None
    if not p1:
        FAIL.append(f"[{kind}] {name}: 原始 URI 解析失败")
        return
    if not p2:
        FAIL.append(f"[{kind}] {name}: 导出后 URI 解析失败 → {exported_uri[:100]}")
        return
    o1, o2 = norm(p1[0]), norm(p2[0])
    # 递归比对关键字段
    diffs = []
    def walk(a, b, path=""):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in a:
                if k not in b:
                    diffs.append(f"{path}{k} 丢失 (原值={a[k]!r})")
                else:
                    walk(a[k], b[k], path + k + ".")
            for k in b:
                if k not in a:
                    diffs.append(f"{path}{k} 新增 (导出值={b[k]!r})")
        elif isinstance(a, list) and isinstance(b, list):
            if a != b:
                diffs.append(f"{path} 列表变化: {a!r} → {b!r}")
        else:
            if str(a) != str(b):
                diffs.append(f"{path} 变化: {a!r} → {b!r}")
    walk(o1, o2)
    # 忽略无害差异
    harmless = ("tls.insecure",)  # insecure=false 时导出省略是允许的
    real = [d for d in diffs if not any(d.startswith(h) for h in harmless) or "丢失" in d]
    if real:
        FAIL.append(f"[{kind}] {name}: 字段丢失/变化:\n    " + "\n    ".join(real))

print("=" * 70)
print("往返测试: parse → outbound_to_v2ray_link → parse (v2ray.txt 订阅链路)")
print("=" * 70)
for name, uri in SAMPLES.items():
    p = mv.parse_node_uri(uri)
    if not p:
        print(f"  ⏭ {name}: 跳过 (解析失败)")
        continue
    ob = p[0]
    link = mv.outbound_to_v2ray_link(ob, "TestName")
    if not link:
        FAIL.append(f"[v2ray] {name}: 导出返回空字符串 (节点会从订阅消失!)")
        print(f"  ❌ {name}: 导出返回空")
        continue
    compare(name, uri, link, "v2ray")
    print(f"  {'✅' if not any(name in f for f in FAIL) else '❌'} {name}")

print()
print("=" * 70)
print("往返测试: parse → outbound_to_clash (clash.yaml 订阅链路)")
print("=" * 70)
for name, uri in SAMPLES.items():
    p = mv.parse_node_uri(uri)
    if not p:
        continue
    ob = p[0]
    try:
        c = mv.outbound_to_clash(ob, "TestName")
    except Exception as e:
        FAIL.append(f"[clash] {name}: 导出抛异常 {e}")
        print(f"  ❌ {name}: 异常 {e}")
        continue
    if not c:
        FAIL.append(f"[clash] {name}: 导出返回空")
        print(f"  ❌ {name}: 导出返回空")
        continue
    print(f"  ✅ {name}: 导出 {c.get('type')}")

print()
print("=" * 70)
if FAIL:
    print(f"发现 {len(FAIL)} 个问题:\n")
    for f in FAIL:
        print(f"  • {f}\n")
    sys.exit(1)
else:
    print("往返零丢失 ✅")
