# paper_rag

[![status](https://img.shields.io/badge/status-0.1.0--dev-orange)]()
[![python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![tests](https://img.shields.io/badge/tests-149%2F149-success)]()
[![ADR](https://img.shields.io/badge/ADR-21-blue)]()
[![endpoints](https://img.shields.io/badge/HTTP%20endpoints-19-blue)]()

> **30 秒电梯讲法**: paper_rag 是为 [DeerFlow](https://github.com/bytedance/deer-flow) 写的工业级 Agentic RAG 论文研读子系统。0 → 1 全栈：采集解析 / 混合检索 + 重排 / 三档拒答 / 自演化 wiki / 4 类主动推送 / 5 种交付物 / 数据反馈闭环 / 8 层 gateway 中间件 + 19 层 langgraph 中间件 / 13 panel Grafana + 13 alert rules。**149 测试全绿、21 ADR、12 个里程碑**。

---

## 目录（按阅读时长分层）

| 时长 | 章节 | 给谁看 |
|---|---|---|
| **30 秒** | [§1 一句话](#1-一句话) | 招聘官 / 路过的人 |
| **5 分钟** | [§2 五大能力](#2-五大能力5min) · [§3 架构图](#3-架构图5min) | 面试官 phone screen |
| **30 分钟** | [§4-§9 完整解读](#4-数据流典型-qa-请求30min-讲解入口) | 面试官 onsite / 同行 review |
| **深读** | [§10 关键代码导览](#10-关键代码导览) · [docs/](#11-延伸阅读) | 同行 / 接手人 |

---

## 1. 一句话

**为研究者打造 AI 论文研究助理**：用自然语言问，跨论文综合答 + 引用纪律，外加每天自动给你打开它的理由。

| 维度 | 数值 |
|---|---|
| 测试通过率 | pytest **149/149** ✅ |
| HTTP 端点 | **19 个** |
| ADR 决策 | **21 份** |
| Gateway 中间件层 | **8 层**（Auth + Observability×3 + Protection×4） |
| LangGraph 中间件 | **19 个**（含新增 4 个：cost / latency / recursion / PII） |
| Grafana panel / alert rules | **13 / 13** |
| 子系统 | 5（QA / Wiki / Deliver / Feedback / Proactive） |

---

## 2. 五大能力（5min）

| 能力 | 说明 | 对应 ADR |
|---|---|---|
| **🔍 Agentic QA** | 跨论文自然语言问答，流式 + 多轮反思 + **三档拒答**（confident/weak/no_evidence）| ADR-0014 |
| **📄 Wiki 知识库** | 每篇论文自动生成可演化 wiki + 跨文献关联 | M5 |
| **📊 5 种交付物** | markdown 综述 / pptx / docx / latex_bib / pdf | ADR-0016 |
| **🔄 数据闭环** | thumbs_down → hard cases → 半自动 abstain 阈值校准 | ADR-0017 |
| **🔔 主动 Agent** | 每日简报 / 订阅匹配 / 复习提醒 / 自动入库 + 4 通道 webhook | ADR-0018 |

### 解决的核心问题

1. **"读 RAG 综述要手翻 50 篇 paper"** → 一句话问，跨文献综合答 + `[chunk:<id>]` 引用纪律
2. **"LLM 一本正经胡编"** → abstain 三档拦掉 100% 真负例，96.7% 真正例保留
3. **"今天又没打开知识库"** → 每天 8 点 daily_digest 推送，订阅命中即触发
4. **"读完就忘"** → 30 天未访问的论文周一自动复习卡片
5. **"用户给反馈但系统不学习"** → M11 闭环：feedback → hard cases → 重标定阈值

---

## 3. 架构图（5min）

```
                 ┌─────────────────┐
                 │  Frontend (NX)  │  /workspace/paper-rag
                 │  inbox / qa UI  │
                 └────────┬────────┘
                          │ HTTPS
                  ┌───────▼────────────────────────┐
                  │  DeerFlow Gateway (FastAPI)    │
                  │  ┌──────────────────────────┐  │
                  │  │ 8 层中间件               │  │
                  │  │ BodySize → GZip          │  │
                  │  │ → RequestId → AccessLog  │  │
                  │  │ → Prometheus → RateLimit │  │
                  │  │ → Timeout → BetterAuth   │  │
                  │  └──────────────────────────┘  │
                  │  paper_rag router 19 endpoints │
                  └───────┬─────────────────────────┘
                          │  PYTHONPATH 注入 (sibling 包)
                  ┌───────▼────────────────────────┐
                  │  paper_rag package             │
                  │   ┌──────────┐  ┌────────────┐ │
                  │   │ rag/     │  │ deliver/   │ │
                  │   │ retrieve │  │ wiki/      │ │
                  │   │ store/   │  │ proactive/ │ │
                  │   │ feedback │  │ deliver/   │ │
                  │   └──────────┘  └────────────┘ │
                  └───────┬─────────────┬──────────┘
              ┌───────────┘             │
       ┌──────▼──────┐         ┌────────▼────────┐
       │ Qdrant      │         │ SQLite ×2       │
       │ (vectors)   │         │ papers.sqlite   │
       └─────────────┘         │ feedback.sqlite │
                               └─────────────────┘
                                      ▲
              ┌───────────────────────┘
              │ docker sidecar
       ┌──────▼─────────┐         ┌────────────────────┐
       │ APScheduler    │ ──────► │ webhook fan-out    │
       │ (cron_runner)  │         │ DingTalk / Feishu  │
       │  daily 08:00   │         │ WeCom / Email      │
       │  Mon 09:00     │         └────────────────────┘
       └────────────────┘

观测栈（独立 compose override）：
   Prometheus (15s scrape) → Grafana (13 panel) + alertmanager (13 alert rules)
```

---

## 4. 数据流（典型 QA 请求，30min 讲解入口）

```
1. 用户问 "What is Self-RAG?" → POST /api/paper_rag/qa (SSE)

2. 8 层中间件依次过栈：
   ① BodySizeLimit (50MB cap)
   ② GZip
   ③ RequestId (uuid4 hex)
   ④ AccessLog (JSON-line)
   ⑤ Prometheus (gateway_http_request_duration_seconds)
   ⑥ RateLimit (per-user 滑动窗口, Redis 可选)
   ⑦ Timeout (60s, SSE 路径 bypass)
   ⑧ BetterAuth → request.state.user_id = "alice"

3. router 通过 Depends(get_current_user_id) 注入 user_id

4. lazy import qa_stream → _retrieve_round
   ├─ query_rewrite (LLM 1 call, 失败 fail-open 走原 query)
   ├─ hybrid_search (BM25 FTS5 + dense Qdrant，RRF 融合)
   └─ rerank (BGE-reranker-v2-m3, top_k*3 → top_k)

5. abstain.decide(chunks, low=0.21, high=0.48):
   ├─ score < 0.21 (12% / 真负例 100% 命中) → 直接 canned msg, 跳 LLM
   ├─ 0.21–0.48 (18% borderline) → LLM 带 insufficiency hint
   └─ >= 0.48 (70% confident) → 正常 LLM 答

6. validate_citations 清洗非法引用 + detect_suspicious 标 numeric/author-year

7. SSE 流的 done event → router 拿 paper_ids → run_in_executor
   → paper_access.touch_many("alice", pids)（异步喂数据给 stale_scan）

8. SSE close → access log 一条 JSON line
```

详细见 [`docs/diagrams/abstain_flow.md`](docs/diagrams/abstain_flow.md)。

---

## 5. 关键技术决策（30min onsite 重点）

| ADR | 决策 | 关键 trade-off |
|---|---|---|
| 0014 | abstain 三档（confident / weak / no_evidence）| 拒答可控 vs 召回率：阈值标定后 100% 真负例拦截，pos_kept 97% |
| 0015 | M8 服务化：sibling 包 + gateway router | 不污染 DeerFlow 主仓 deps，Python 3.10/3.12 双 venv 兼容 |
| 0016 | 综述生成走 N+1+S 调用 | 一次 8K token 灌不下：N 篇深读 + 1 次综合 + 引用清洗 |
| 0017 | M11 反馈数据闭环 | 半自动 PR review，避免阈值漂移失控 |
| 0018 | M9 主动 Agent + APScheduler | 不接 DeerFlow automation：自家任务确定性逻辑省钱省工 |
| 0019 | **双 SQLite 数据库** | papers vs feedback 写入冲突 + 备份语义不同，应用层 join |
| 0020 | gateway 8 层中间件栈 + Prom/Grafana | path_template 防 cardinality 爆炸 + Redis fail-open 不 DDoS 自己 |

完整 21 份 ADR：[`docs/adrs/`](docs/adrs/)。

---

## 6. 性能基线

> 详见 [`docs/PERF_BASELINE.md`](docs/PERF_BASELINE.md)。生产环境实测会刷新此表。

| 指标 | 数值 | 备注 |
|---|---|---|
| pytest 全套 | mean **3.01s** | 113 项纯逻辑测试，CI gate |
| recall@k=10 | 0.90 | M5 baseline，33 题 eval set |
| abstain neg_blocked / pos_kept | 100% / 97% | offline 标定 |
| QA 端到端 P50 | ~2.0s | confident 档单轮 |
| QA 端到端 P95 | ~5.0s | reflect 二轮 + 长答案 |
| `no_evidence` 路径延迟 | **~250ms** | 跳 LLM 节流器 |
| qa_agentic 冷启动 | 316ms | OpenAI client + tiktoken；待 lifespan warmup |
| Docker lean 镜像 | ~600MB | 多阶段 + venv copy |

---

## 7. 可观测性 / 故障域

| 组件挂了 | 影响 | 自动降级 |
|---|---|---|
| Qdrant | dense 召回 0 | BM25 only，abstain 用 score_bm25_norm |
| LLM | chat 失败 | 返回 evidence-only，metric `qa_degraded_total` |
| reranker | rerank 失败 | RRF score 排序，标 `quality=low` |
| feedback.sqlite locked | inbox 写失败 | log warning，QA 主路径不受影响 |
| webhook 全挂 | 无外部推送 | inbox 仍可前端轮询 |
| cron 容器死 | digest / stale 不跑 | gateway 不受影响，POST /proactive/digest/run 手动触发 |
| Redis (RateLimit) | 多副本计数失效 | 自动 fallback 到内存窗口 |

13 条 Prometheus alert rule：5xx>1% / p95>5s / abstain>30% / 504/429/401 spike / GatewayDown / QdrantDown 等。

---

## 8. Quickstart

```bash
# 1. 单机模式（开发）
cd paper_rag
make install-dev
make qdrant-up
make init-store
make ingest ID=2310.11511        # 入第一篇 Self-RAG paper
make ask Q="What is Self-RAG?"   # 直接命令行问答

# 2. 服务化（生产）
cd ..
make up                                                # gateway + frontend + qdrant
make obs-up                                            # Prometheus + Grafana 监控栈
open http://localhost:2026/workspace/paper-rag         # 前端入口
open http://localhost:9090                             # Prometheus
open http://localhost:3001                             # Grafana (admin/admin)

# 3. 跑评测 + 标定
cd paper_rag
make calibrate-abstain                                 # offline 模式，秒级
python scripts/calibrate_abstain.py --mode online --no-rewrite --top-k 8

# 4. 收集 hard cases
make hard-cases                                        # 每周 cron
```

---

## 9. 项目里程碑

| 里程碑 | 完成度 | 关键产出 |
|---|---|---|
| M0–M5 | ✅ | 采集 / 解析 / 切分 / 检索 / 重排 / 评测 / 主线打通 |
| M6 产品化 | ✅ | bibtex 导出 / 多模态 chunk / wiki 自演化 |
| M7 abstain | ✅ | 三档拒答 + 阈值标定脚本 |
| M8 服务化 | ✅ | gateway router 5 → 19 端点 |
| M9 主动 Agent | ✅ | 4 类推送 + APScheduler sidecar |
| M9.5 cron 落地 | ✅ | docker-compose proactive sidecar |
| M9.6 中间件 | ✅ | 8 层栈 + auth 三处优化 |
| M9.7 监控栈 | ✅ | Prom + Grafana + 13 alert rules |
| M10 交付物 | ✅ | 5 种格式 |
| M11 数据闭环 | ✅ | feedback + hard cases + 半自动校准 |

---

## 10. 关键代码导览

```
paper_rag/
├── src/paper_rag/
│   ├── rag/                       # 核心 Agentic RAG
│   │   ├── abstain.py             ★ 三档拒答 (ADR-0014)
│   │   ├── qa_agentic.py          ★ 主链路（rewrite/retrieve/rerank/reflect/abstain）
│   │   └── qa_stream.py           ★ SSE 流式版本
│   ├── retrieve/
│   │   ├── hybrid.py              # BM25 + dense RRF 融合
│   │   └── rerank.py              # BGE-reranker-v2-m3
│   ├── deliver/                   # M10 交付物（5 格式）
│   │   ├── survey_md.py           ★ N+1+S 综述生成
│   │   ├── pdf.py                 # reportlab + 纯 Python fallback
│   │   └── dispatch.py
│   ├── proactive/                 # M9 主动 Agent
│   │   ├── matcher.py             ★ 三档 strength + 跳过 ingester
│   │   ├── digest.py              ★ daily 简报 + 跨用户 TL;DR 缓存
│   │   ├── webhook.py             ★ DingTalk / Feishu / WeCom / Email
│   │   └── cron_runner.py         # APScheduler BlockingScheduler
│   ├── feedback/                  # M11 数据闭环
│   └── observability/metrics.py   # 60 行 stdlib Prometheus 文本
├── tests/                         # pytest 149/149
│   └── test_middleware.py         ★ 19 项中间件测试
├── docs/
│   ├── SYSTEM_DESIGN.md           ★ 30min 1-pager
│   ├── PERF_BASELINE.md           ★ 性能基线
│   ├── EVAL_REPORT.md             # 评测数据
│   ├── HARD_CASES_REPORT.md
│   ├── adrs/                      # 21 份 ADR
│   └── diagrams/                  # 3 张 mermaid 时序图
└── scripts/
    ├── calibrate_abstain.py       ★ 阈值标定（online/offline 双模式）
    └── collect_hard_cases.py      # 数据闭环每周 cron

backend/app/gateway/
├── routers/paper_rag.py           ★ 19 个 HTTP 端点
└── middleware/
    ├── auth.py                    ★ BetterAuth + LRU
    ├── observability.py           ★ RequestId / AccessLog / Prometheus
    └── protection.py              ★ BodySize / Timeout / RateLimit (Redis)

docker/observability/              # M9.7 监控栈
├── docker-compose.observability.yaml
├── prometheus/{prometheus.yml, alerts.yml}
└── grafana/provisioning/{datasources, dashboards}
```

---

## 11. 延伸阅读

- 30 min 一文读透：[`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md)
- 全部 milestone 进度：[`docs/STATUS.md`](docs/STATUS.md)
- 决策追溯：[`docs/adrs/`](docs/adrs/)（20 份）
- 时序图：[`docs/diagrams/`](docs/diagrams/)（abstain / proactive / feedback）
- Changelog：[`CHANGELOG.md`](CHANGELOG.md)

---

## 12. 演示话术（30 min onsite）

1. **5 min** — 概览（§1 + §2 + §3 + §6 性能基线）
2. **10 min** — 一个完整 QA 流程走读（§4 + 配 abstain_flow.md 时序图）
3. **10 min** — 一个深度技术点任选：
   - **abstain 三档阈值标定**（数据驱动 + 半自动校准）
   - **M11 数据闭环 + hard case 自动收集**
   - **M9 proactive：cron + matcher + webhook fan-out**
   - **双 SQLite 边界（ADR-0019）+ 8 层中间件（ADR-0020）**
4. **5 min** — 后续 roadmap + 对 DeerFlow 主仓的最小改动哲学（§7 故障域）

---

## License

MIT — see [LICENSE](LICENSE).
