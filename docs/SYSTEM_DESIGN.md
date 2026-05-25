# SYSTEM_DESIGN.md — paper_rag 1-pager

> **目标读者**：面试官 / 同行 review / 新人 onboarding
> **适合时长**：30 分钟从架构讲到关键决策
> **维护**：随重大架构变更（每次 ADR 引入）同步本文

## 1. TL;DR（30 秒版）

paper_rag 是一个 **Agentic RAG 学术论文研读系统**，集成在 DeerFlow 框架上，
支持自然语言问答、跨文献综述生成、知识演化、订阅推送、自动入库 5 大产品能
力，覆盖 0 → 1 完整数据闭环。

技术栈：bge-m3 embedding + Qdrant 向量库 + SQLite + FTS5 混合检索 + BGE-
reranker-v2-m3 + Qwen-plus 通过 OpenAI 兼容协议。后端 Python 3.10+，前端
Next.js，部署 docker-compose。**149 个测试全绿、21 份 ADR、19 个 HTTP 端
点、4 类交付物**。

## 2. 架构图（高层）

```
                 ┌─────────────────┐
                 │  Frontend (NX)  │
                 │  inbox / qa UI  │
                 └────────┬────────┘
                          │ HTTPS
                  ┌───────▼────────┐
                  │  DeerFlow Gateway (FastAPI) │
                  │  + BetterAuth middleware    │  ← user_id 强隔离
                  │  + paper_rag router (20 ep) │
                  │  + /metrics (Prometheus)    │
                  └───────┬─────────────────────┘
                          │  PYTHONPATH=/opt/paper_rag/src
                  ┌───────▼────────────────────────┐
                  │  paper_rag package             │
                  │   ┌─────────┐  ┌───────────┐   │
                  │   │ rag/    │  │ deliver/  │   │
                  │   │ retrieve│  │ wiki/     │   │
                  │   │ store/  │  │ proactive/│   │
                  │   │ feedback│  │ deliver/  │   │
                  │   └─────────┘  └───────────┘   │
                  └───────┬────────────┬───────────┘
              ┌───────────┘            │
       ┌──────▼──────┐         ┌───────▼─────────┐
       │ Qdrant      │         │  SQLite (×2)    │
       │ (vectors)   │         │ papers.sqlite   │
       └─────────────┘         │ feedback.sqlite │ ← ADR-0019 双库
                               └─────────────────┘
                                       ▲
              ┌────────────────────────┘
              │  cron sidecar (Docker)
       ┌──────▼─────────┐
       │ APScheduler    │ daily_digest @ 08:00
       │ (cron_runner)  │ stale_scan   @ Mon 09:00
       └────────────────┘
                                       ▲
              ┌────────────────────────┘
              │
       ┌──────▼─────────┐
       │ Webhooks       │ DingTalk / Feishu / WeCom / Email
       │ (P3-13)        │ (best-effort, never block inbox.write)
       └────────────────┘
```

## 3. 关键决策（10 个 ADR 速览）

| ADR | 决策 | 为什么 |
|---|---|---|
| 0014 | abstain 三档（confident / weak / no_evidence） | 拒答可控，避免 LLM 幻觉式硬答 |
| 0015 | M8 服务化：sibling 包 + gateway router | 不污染主仓 deps，Python 3.10/3.12 双 venv 兼容 |
| 0016 | M10 综述生成 N+1+S 调用 | 一次 8K token 灌不下，N 篇深读 + 1 次综合 + 引用清洗 |
| 0017 | M11 反馈数据闭环 | 用户反馈 → hard cases → 半自动阈值校准 |
| 0018 | M9 主动 Agent + APScheduler | 用户没理由每天打开 → 给 4 类推送理由 |
| 0019 | 双 SQLite 数据库 | papers vs feedback 写入冲突 + 备份语义不同 |
| 0020 | gateway 8 层中间件栈 + Prom/Grafana | 可观测 + 防恶意 + Redis backend 多副本 ready |
| 0021 | LangGraph 中间件强化（cost / latency / recursion / PII） | agent 失控两道闸 + token 成本可观测 + 默认 PII redact |

完整 ADR 列表：`docs/adrs/`（21 份）。

## 4. 数据流（典型 QA 请求）

```
1. 用户问 → /api/paper_rag/qa (SSE)
2. BetterAuth 中间件验 cookie → request.state.user_id = "alice"
3. router 依赖 get_current_user_id 注入
4. lazy import qa_stream → 跑 _retrieve_round
   ├─ query_rewrite (LLM, 1 call)
   ├─ hybrid_search (BM25 + dense via Qdrant)
   └─ rerank (BGE-reranker-v2-m3, top_k*3 → top_k)
5. abstain.decide(chunks, low=0.21, high=0.48):
   ├─ < 0.21 → 直接返回 canned msg, 跳 LLM
   ├─ 0.21–0.48 → LLM 带 insufficiency hint
   └─ >= 0.48 → 正常 LLM 答
6. validate_citations + detect_suspicious_citations
7. SSE done event → router 拿 paper_ids
8. asyncio.run_in_executor → paper_access.touch_many("alice", pids)
   └─ 喂数据给 stale_scan
9. inbox.write 不会触发（QA 路径），但若是订阅匹配 → fan_out webhook
```

## 5. 性能基线

> 数据见 `docs/PERF_BASELINE.md`（待跑），以下是估算量级。

| 指标 | 量级 | 备注 |
|---|---|---|
| QA 端到端延迟 P50 | ~2s | 含 1 次 rewrite + retrieve + rerank + 1 次 chat (cached intent) |
| QA 端到端延迟 P95 | ~5s | reflect 二轮 + 长答案 |
| recall@k=10 | 0.90 | M5 baseline，固定 33 题 eval |
| abstain pos_kept | 96.7% | offline 标定 |
| abstain neg_blocked | 100% | offline 标定 |
| 测试套件运行时间 | ~2.2s | 113 项纯逻辑测试 |
| 容器构建 lean tag | ~600MB | 多阶段 + venv copy |

## 6. 故障域 / 降级路径

| 组件挂了 | 影响 | 降级 |
|---|---|---|
| Qdrant | dense 召回 0 | 走 BM25 only，abstain 用 score_bm25_norm |
| LLM | chat 失败 | 返回 evidence-only，metric `qa_degraded_total` |
| reranker | rerank 失败 | 走 RRF score 排序，标记 `quality=low` |
| feedback.sqlite locked | feedback/inbox 写失败 | log warning，不影响 QA 主路径 |
| webhook 全挂 | 无外部推送 | inbox 仍可前端轮询 |
| cron 容器死 | digest / stale 不跑 | gateway 不受影响，POST /proactive/digest/run 手动触发 |

## 7. 边界 / 已知局限

- ❌ 无多机部署（SQLite 不能横向扩）— 触发条件：feedback.sqlite > 1GB
- ❌ 无 PDF 优先级（M10 不做，P3 加了 fallback）
- ❌ 无知识图谱 / NL 订阅
- ❌ 无视频/图表理解
- ⚠️ 33 题 eval 集小，统计意义弱

## 8. 30 分钟讲什么

1. **5 min 概览**：本文 §1 + §2 + §3
2. **10 min 一个完整 QA 流程**：本文 §4，结合 abstain 三档手画时序
3. **10 min 一个深度技术点**（任选）：
   - abstain 三档阈值标定（数据驱动）
   - M11 数据闭环 + hard case 自动收集
   - M9 proactive：cron + matcher + webhook fan-out
   - 双 SQLite 边界（ADR-0019）
4. **5 min 后续 roadmap**：本文 §7

