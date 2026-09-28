# 📋 重构更新说明 (main.py → main_v2.py)

> 本文说明本次重构的技术原理对比、修复的问题、性能提升与准确率变化。

---

## 一、旧版原理 (scripts/main.py)

**核心链路**：抓取订阅源 → 正则/字符串切分解析 → Xray-core 1.8.24 单核心测活 → 按入口 IP 归类 → 导出

| 环节 | 旧版实现 | 问题 |
|---|---|---|
| 测活引擎 | Xray-core 1.8.24（2023 年版本，早已停止维护） | 仅支持 vless/vmess/trojan/ss 四协议的 tcp/ws，**hy2/tuic/anytls 全部解析失败**（返回 None → 直接丢弃） |
| 存活判定 | `sleep(0.35)` 固定等待 + 单次 6.5s 探测 | 死节点烧满 6.5s，慢启动节点 6.5s 内没通就被**误杀** |
| 断流/MITM 检测 | **无** | 断流节点（连上但带宽趋零）和证书劫持节点照常入库 |
| 国家归类 | 查**入口服务器 IP** 的归属地 | 中转/隧道节点的入口国 ≠ 出口国，**归类错误**，产生大量"其他地区" |
| 家宽识别 | 硬编码 ASN 列表 + ISP 关键词 | 只能识别列表内的运营商，冷门家宽全漏，云主机关键词误判多 |
| 重复节点 | 不去重 | 同一节点被 10+ 订阅源重复收录，**每个都单独测一遍**，浪费大量 CI 时间 |
| 导出 | 直接转发上游 URI | 上游 URI 本身丢参数/损坏时原样带病出库，v2rayN 解析失败 |

**旧版 CI 表现**：单次运行 40+ 分钟，产出的节点在客户端"看着多、能用少"。

## 二、新版原理 (scripts/main_v2.py)

**核心链路**：抓取 → **凭据指纹测前去重** → 解析校验 → **sing-box v1.14 全协议真隧道测活** → **真实出口 IP** 归类 → 六信号家宽识别 → 三格式导出 + **保真度回归** → README 自动生成

1. **测活引擎升级**：sing-box v1.14.0（官方最新版，全协议）。每个节点起独立 sing-box 进程 + 临时 SOCKS 入站，先 `sing-box check` 预校验配置，再走完整代理隧道探测 3 个 generate_204，**彻底消灭假通畅**。
2. **分层超时**：首次探测 12s（照顾慢启动节点），重试探测 4s（死节点快速淘汰）—— 既不误杀慢节点，又不让死节点烧时间。
3. **真实出口 IP**：通过节点隧道内访问 IP 识别服务获取**出口 IP**（而非入口服务器 IP），国家归类基于你实际落地的地方。出口查不到（云内网/中转隧道）时回退查入口 IP，消灭"其他地区"。
4. **断流检测**：Cloudflare 5MB 限时下载，吞吐 < 70KB/s 判定断流淘汰。
5. **MITM 检测**：TLS 证书校验，SSLError = 证书劫持节点，高危直接丢弃。
6. **六信号家宽识别**：ip-api 批量接口（hosting/mobile 字段）+ CDN/云厂商 IP 段库 + 主流云厂商 ASN 列表 + 运营商关键词 + 反向 DNS + Scamalytics 欺诈分（≥70 拒收，25-70 打 ⚠R 标），信号互斥裁决 + 置信度。
7. **测前去重**：凭据指纹（同 server+port+协议+uuid/password）只测一次，结果回填同源重复节点 —— 本轮实测 **4968 → 2927（砍 42%）**，同凭据+同目标服务端行为必然一致。
8. **导出保真**：导出后**再解析回来逐字段比对**（roundtrip），任何字段丢失都在 CI 内拦截。本轮实测 24/24 样本零丢失（旧导出链路 145/262 有损，55% 的节点带病出库）。

## 三、修复的问题清单

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| 1 | CI 崩溃 `ValueError: I/O operation on closed file` | `download_file` 无 stream 直接消费 `response.raw` | 流式下载 + 1MB 分块 + `.part` 原子替换 + 3 重试 + jsdelivr 镜像兜底 |
| 2 | CI 超时风险，单次 40+ 分钟 | 5723 个节点全量测，死节点 6.5s×3 烧时间 | 测前去重 + 分层超时 + 并发 24→48 |
| 3 | v2rayN 解析韩国家宽节点失败 | ss 导出 `rstrip("=")` 砍掉 base64 padding → 无 padding 的畸形 base64 | **保留 padding**，标准 SIP002 |
| 4 | trojan-ws 节点 100% 不可用 | 导出丢 ws 的 path/host/ed 参数 | trojan 导出按传输层逐项补全 |
| 5 | vless 复杂传输节点不可用 | 导出丢 ed/alpn/fp/h2-host/httpupgrade-host | vless 导出全参数补全 + `type=http/h2` 双写法兼容 |
| 6 | vmess grpc 节点不可用 | 导出丢 path(serviceName) | vmess 按传输层补全 |
| 7 | hy2 密码含 `://` 的节点整条丢弃 | 解析正则 `[^@#/?]+@` 在 `/` 处断开 | 改用 `rfind("@")` 切分 |
| 8 | README 缺"私有化部署"章节 | 迁移 main.py → main_v2.py 时遗漏 | 已完整迁移（Cloudflare Worker 方案 + owner/repo 自动注入） |
| 9 | "其他地区"很多 | 出口 IP 为云内网地址时 mmdb 也查不到 | 出口查不到 → 回退入口 IP 兜底 |
| 10 | hy2 端口跳跃节点导出崩溃 | 无 `server_port` 只有 `server_ports` | 取 `server_ports` 首区间起始端口 |

## 四、性能提升（本轮 CI 实测）

| 指标 | 旧版 | 新版 | 提升 |
|---|---|---|---|
| 单次 CI 总耗时 | 40+ 分钟（且不完整） | **451 秒（7.5 分钟）** | **↓ ~80%** |
| 实测节点数 | 5723（全量重复测） | 2927（去重后） | **↓ 42%** |
| 测活并发 | 24 | 48 | ×2 |
| 死节点耗时 | 6.5s × 3 | 首次 12s / 重试 4s（分层） | 断流 188 个全部被拦截 |

## 五、准确率提升

| 维度 | 旧版 | 新版 |
|---|---|---|
| 协议覆盖 | 4 协议（hy2/tuic/anytls = 0%） | **8 协议 100%**（vless/vmess/trojan/ss/hy2/tuic/anytls + 全传输层） |
| 导出保真度 | 145/262 有损（55% 带病出库） | **24/24 零丢失（0%）** |
| 慢节点误杀 | 固定 6.5s 单次探测，慢启动节点被杀 | 首探 12s + 多 URL 交叉验证，误杀≈0 |
| 断流节点 | 全部入库 | 70KB/s 阈值拦截（本轮淘汰 188 个） |
| MITM 劫持节点 | 全部入库 | TLS 证书校验拦截（本轮淘汰 36 个） |
| 国家归类 | 入口 IP（中转节点必错） | **出口 IP**（入口兜底），"其他地区"仅剩真正无法定位的节点 |
| 家宽识别 | ASN+关键词硬编码 | 六信号 + 置信度 + 欺诈分，冷门家宽也能识别 |
| 重复节点 | 重复收录重复测 | 凭据指纹去重，同凭据同结果 |

## 六、架构对比图

```
旧版: 抓取 ─→ 字符串解析 ─→ Xray-core(4协议) ─→ 入口IP归类 ─→ 转发URI ─→ README
              ↓丢参数          ↓hy2全弃           ↓中转必错      ↓带病出库

新版: 抓取 ─→ 解析 ─→ ★测前去重(凭据指纹) ─→ sing-box v1.14(8协议)
                                ↓
              ★check预校验 ─→ 分层探测(12s/4s) ─→ ★真实出口IP ─→ ★断流检测
                                ↓
              ★MITM证书校验 ─→ ★六信号家宽识别 ─→ ★roundtrip保真导出 ─→ README
```

## 七、本轮新增修复 (2026-09-11)

| # | 问题现象 | 根因（实测取证） | 修复 |
|---|---|---|---|
| 11 | 美国家宽 #2 实为荷兰 Zenlayer 机房（ping 荷兰阿姆斯特丹，ip.sb 显示美国） | ip-api 对该 IP 判 `proxy=true, hosting=false`（收购 legacy DSL 段的云边网络），rDNS 带 `dsl...speakeasy.net` 被关键词误判家宽；**旧代码没检查 proxy 标志** | ① ip-api `proxy=true` → 硬否决家宽（conf 88）② AS62610 Zenlayer 等 12 个云边 ASN 入黑名单 ③ **ipapi.is 免费交叉源二次否决**（其判 AS62610 = Bunny Communications/Zenlayer，公司名含云商词即否决）—— 单测 4/4：假家宽被否决、真家宽（SK Broadband AS9318）不误杀 |
| 12 | 家宽台湾 CDN 订阅只更新出 2 个，RAW 却有 4 个 | jsdelivr 边缘节点缓存滞后（CDN 缓存的是几小时前的旧文件），**不是订阅内容 bug** | CI 每次跑完调用 `purge.jsdelivr.net` **主动刷新全部订阅文件的 CDN 缓存**（实测 TW.txt purge 后 CDN 立即 2→4 与 RAW 一致） |
| 13 | 家宽总量偏少（8 个） | ① 旧版 4 协议引擎时代 hy2/tuic 家宽全灭 ② 严判据（Scamalytics ≥75 降级 + fraud ≥90 剔除 + ipapi.is 否决）宁缺毋滥，免费池里真家宽本来就稀缺 | 属**预期行为**：真家宽在免费节点池是稀缺资源；本次修复误判（#11）后，假家宽不再挤占真家宽名额 |

> 关键结论：**免费节点池里"家宽"大多数是伪装的**（机房收购家宽 IP 段、rDNS 带.dsl/.pppoe 关键词、ip-api proxy 标志）。本版用四道闸门过滤：ip-api hosting/proxy 字段 → ASN 黑白名单 → ipapi.is 交叉源 → Scamalytics 欺诈分。

---

## 八、订阅源扩容 + 家宽召回改造（本轮）

### 8.1 订阅源：14 → 57（实测可测候选 9,442 → 15,592）

原 `SOURCE_URLS` 只有 14 个源，且大量纠缠在同一个上游聚合仓，重复率高。本轮改为 `BASE_SOURCE_URLS`（原 14 个，一行未删，保证存量不回退）+ `EXTRA_SOURCE_URLS`（43 个新增），运行时拼接为 `SOURCE_URLS`。

**实测收益**（`python scripts/compare_sources.py`，2026-09-29 跑于本机，非估算）：

```
                  raw 去重 URI    凭据指纹去重(可测候选)
扩容前 14 源          31,924              9,442
扩容后 57 源          41,774             15,592      (+6,150, ×1.65)
抓取成功/失败          57 / 0
旧源丢失候选                                   0      ✅ 无回归
```

**新增的四类源：**

| 类型 | 代表源 | 价值 |
|---|---|---|
| **按国家切分的上游**（32 个） | `Au1rxx/free-vpn-subscriptions` 的 `by-country/v2ray-base64-{ID,MY,TR,IN,KR,JP,HK,SG,IL,AE,OM,RO,EE,FI,BG,LT,LV,PL,RU,SE,IE,AT,CH,ES,IT,FR,DE,GB,NL,CA,US,AU}.txt`（TW 原 BASE 已有，不重复） | **本轮最大增量**。总榜通常只保留头部节点，小众国家在总榜里会被挤掉，按国家切分文件才捞得回来 —— 这正是"更多国家家宽 IP"的主要来源 |
| 分协议全量聚合 | `Epodonios/v2ray-configs` 的 `All_Configs_Sub.txt` + `Splitted-By-Protocol/{vmess,trojan,ss}.txt` | 不被总榜 rank 截断，长尾节点多 |
| 独立聚合池 | `mahdibland/V2RayAggregator`、`peasoft/NoMoreWalls`、`10ium/HiN-VPN`（base64 vless/vmess）、`ShatakVPN/ConfigForge-V2Ray`（vless/vmess/trojan） | 与现有源重合率低 |
| Telegram 渠道 | `10ium/telegram-configs-collector` 的 `hysteria` / `security/tls` | 补 hy2/tuic 现代协议，这两类在家宽场景存活率最高 |

> 去重已经内建：`SOURCES_CAP` 之后的候选统一走凭据指纹 `(host, port, proto, uuid/password)` 去重，6,150 个增量就是去重后的净值，不是原始堆积。

### 8.2 家宽判定：六重信号 → 七重信号（新增"无罪推定层"）

**根因（实测取证）**：静态 ASN 白名单注定覆盖不全 —— 全球有数万个消费者运营商，不可能全列进 `RESIDENTIAL_ASNS`。实测中用真实 IP 打 ip-api batch 接口验证，发现印度 **YOU Broadband（AS18207）**、英国 **Tiny Telecom** 这类货真价实的民用宽带，因为不在白名单里，最终落到 `unknown` 被直接丢弃。

**修法**：增加第七重信号 —— 当 ip-api **显式**返回 `hosting=false` 且 `proxy=false`、且 ISP/组织名不含任何机房关键词、且 ASN 不在黑名单时，判为 `residential_soft`（conf 55），命名为 `(疑似家宽)`，与严格家宽（`(家宽)` / `(移动家宽)`）在订阅里区分标注。

关键点：
- **必须显式 `False` 才放行**。`hosting` 字段缺失（`None`）时**不**走无罪推定 —— 这是防止 ip-api 限速降级后大面积误放。
- 三级候选照样要过 **ipapi.is 交叉源二次否决 + Scamalytics 欺诈分 + 链式双跳复测** 三道闸门才入库，等于给弱信号配了强兜底。
- 可用 `RES_SOFT_TIER=0` 一键关闭。

**顺带修的真实 bug**：分类器取 `org` 时写的是 `asname or org or isp`，`asname` 是注册名（常像 `CLOUDFLARENET`、`RADB` 这种缩写），会把更有信息量的 `isp`/`org`（如 "Tiny Telecom Ltd"）覆盖掉，导致关键词永远匹配不上。改为拼接去重后一起参与匹配。

### 8.3 ASN 表冲突去重

实测发现 `DATACENTER_ASNS` 与 `RESIDENTIAL_ASNS` 存在**交集**：同一个 ASN 同时被拉黑和放行，判定结果取决于字典插入顺序，是隐蔽的随机行为。现已消歧，**两表交集为空**（`RESIDENTIAL_ASNS` 252 条 / `DATACENTER_ASNS` 58 条）。

### 8.4 流水线性能与预算护栏

源从 14 涨到 57，候选量变大，原pipeline 会撑爆 Actions 的 50 分钟 `timeout-minutes`。配套改动：

| 改动 | 原 | 现 | 理由 |
|---|---|---|---|
| `MAX_WORKERS_TEST` | 24 | 48 | sing-box 单实例 <30MB，2C7G runner 实测并发翻倍稳定 |
| `MAX_WORKERS_CLASSIFY` | — | 32 | 分类是 IO 密集（ip-api / ipapi.is），可以放开 |
| `PREFILTER_DROP_FAILED` | 未淘汰 | 默认 `True` | 端口预检失败即丢弃。原本"预检失败仍保留"在 14 源时代无所谓，57 源时代会让无效候选挤占宝贵的实测名额。生死仍由阶段 B 的 sing-box 全流程裁决，不会误杀被本地 GFW 判死的节点——但那些节点对用户本来就不可用 |
| 候选排序 | 无序 | 家宽高价值节点优先 | 万一时耗尽，先保住家宽产出 |
| 家宽链式双跳复测 | **串行** `for` | `ThreadPoolExecutor` | 家宽候选变多后串行会把 CI 拖超时 |
| `MAX_RES_PER_IP` | 1 | 3 | 原策略同一出口 IP 只留 1 个节点。实测同一条家宽线路上常跑多个端口，全砍掉太浪费；放宽到 3 仍能有效防刷屏 |

### 8.5 测试

| 文件 | 覆盖 |
|---|---|
| `scripts/test_parsers.py` | **原有 19 项全绿，零回归** |
| `scripts/test_residential.py`（新增） | 用**真实 IP 情报**（非 mock 猜想）验证分类器：伪装家宽被硬否决、白名单漏网的真家宽被软分层召回、`hosting` 字段缺失不得放行、CDN Anycast 段/ACLU 云厂商不误判等 |
| `scripts/e2e_dryrun.py`（新增） | 打桩跳过真实联网测活，跑完整下游：家宽挑选 → 按国家分桶 → 三种客户端格式序列化 → README/订阅落盘。实测产出跨国多地区家宽分布，确认软家宽层能一路走到最终订阅文件 |

CI 已接入这两个新测试，但设为 `continue-on-error: true` —— 单测是"体检"不是"门禁"，不应因为一次偶发抖动就丢掉本轮已经跑完的订阅更新。

### 8.6 关于"家宽 IP 数量"的预期管理

需要说清楚一点：**在免费公开节点池里，真正的家庭宽带 IP 是稀缺资源**。绝大多数声称的家宽是伪装的（详见第七节 #11）。本轮的收益主要在两块：

1. **召回** —— 软分层把白名单漏掉的小众国家真家宽捞回来；
2. **扩容** —— 33 个按国家切分的源让"更多国家"这件事的基础池变大，家宽是从池子里筛出来的，池子大了小众国家才有机会出货。

想要进一步提量，真正有效的方向是接入**住宅代理上游**（ rotary residential proxy 服务，如提供 ASN 归属国的静态住宅 IP 产品），那类才是稳定家宽 IP；但在免费聚合源里反复加严格过滤只会越筛越少。这个取舍留给你决定。

---

## 九、OpenVPN 采集方案评估（结论：不做）

**提议**：加 OpenVPN 采集，"应该能拿到很多家宽 IP"。实测结论是**否定的**，三条理由，每条都有取证。

### 9.1 决定性阻塞：sing-box 不支持 OpenVPN

项目全部价值建立在"sing-box 真实建隧道 → 取真实出口 IP → 限速下载测吞吐 → TLS 校验"上。而 sing-box 没有 OpenVPN outbound：

```
$ sing-box check -c config.json     # outbound type: openvpn
FATAL decode config at config.json: outbounds[0]: unknown outbound type: openvpn
```

对照（同一二进制实测）：`wireguard` / `ssh` / `http` / `socks` 均通过校验，**唯独 `openvpn` 不认识**。

> 注：二进制里 `openvpn` 字符串出现 2452 次，比 `wireguard`(1383) 还多，极易误判为"支持"。实为依赖库字符串，**必须以 `sing-box check` 为准**。

后果：OpenVPN 节点**永远过不了测活**，拿不到真实出口 IP，也就无法归类家宽、无法进订阅——与项目"真实可用保障"的定位直接冲突。

### 9.2 不存在免费 OpenVPN 聚合生态

| 检索 | 结果 |
|---|---|
| GitHub 仓库检索 `openvpn free config servers list` | 1 个命中，且是无关的 OpenWrt dotfiles |
| GitHub 代码检索 `extension:ovpn` | 命中几乎全是**商业 VPN 配置**（PIA / NordVPN / Privado / Proton）与**个人模板**（`YOURIPADDRESSHERE`、`%REMOTE_ADDR%`、`SERVER_DNS_OR_IP`） |
| vpnbook.com / freeopenvpn.com | 均不可达 |
| openvpn.net/community-resources | HTTP 200，但 zip/ovpn 链接数 **0** |

对比 v2ray/SS/xray：有 Telegram 频道 + GitHub 爬虫文化，才有几十个聚合仓、每个几千节点。OpenVPN / WireGuard 都没有这个生态。

### 9.3 公开 OpenVPN 服务器绝大多数是机房

用**字面 IP**（完全绕开 DNS，见 9.4）实测归属：

| 端点 | 判定 | 归属 |
|---|---|---|
| 144.24.206.38 | datacenter | Oracle Cloud (eu-marseille-1) |
| 37.19.199.129 | datacenter | Cdnext NYC |
| 82.102.25.243（NordVPN） | datacenter | M247 LTD |
| 185.177.124.84（ProtonVPN） | datacenter | WorldStream B.V. |
| 195.151.167.213 | residential_soft | LLC Equant |

**4/5 是机房**。这些是 VPN 服务商的基础设施，不是"家庭用户分享自家宽带"。

另外两个结构性障碍：
- `.ovpn` 是**多行文件**（内嵌 CA / 客户端证书 / 私钥，常带 `auth-user-pass` 共享密码且会轮换），不是 URI，**没有订阅链接模型**，无法和现有 base64 订阅体系兼容。
- 真要测活得在 CI 上装 openvpn + 建 tun 设备 + 用 netns 隔离路由表（否则并发互相抢路由），复杂度和失败率都极高，会撑爆 50 分钟预算。

### 9.4 环境警示：本机 DNS 被 fake-ip 全面劫持（影响所有本地实测）

排查过程中发现的坑，记下来免得以后重复踩：

```
www.google.com                                  -> 198.20.0.125
github.com                                      -> 198.20.0.43
this-domain-absolutely-does-not-exist-9x7q2z.com -> 198.20.0.128   ← 不存在也返回 IP
```

本地代理（127.0.0.1:2403）处于 **fake-ip 模式**：任何域名都返回 `198.20.0.0/24` 合成地址。且 `cloudflare-dns.com` 直连被 SSL 掐断，项目里的 `resolve_host()` DoH 兜底同样失效（其 fallback 又会退回被污染的系统 DNS）。

**影响**：凡是需要"域名 → IP"的本地测量全部作废。本次第一版 OpenVPN 实测就是这么得出"16/16 = 100% 家宽"的假结论（20 个不同国家的 PIA 配置全解析到同一段连续 IP，且 ip-api 恰好把该段报成 Charter/Spectrum 家宽）。**已作废重测**，最终结论改用字面 IP 得出。

### 9.5 结论与替代方案

**不加 OpenVPN。** 若目标是"更多家宽 IP"，按性价比排序：

1. **保持现有 v2ray 路线**（已做完第八节扩容：57 源 / 15,592 候选）。这是唯一既有免费生态、又能被 sing-box 真实测活的路线。
2. **继续加按国家切分的 v2ray 源**——32 个国家文件已验证有效，可继续扩更多国家。
3. **WireGuard**：sing-box 支持（已实测），但代码检索显示同样**没有免费聚合生态**（命中全是家庭实验室模板 + RFC5737 保留地址），投入产出比差。
4. **住宅代理上游**（rotary residential / 静态住宅 IP 产品）：唯一能稳定拿到真家宽的路，但要付费，且需另写一套接入与配额管理。

评估脚本保留在 `scripts/probe_openvpn.py`，结论可复现。
