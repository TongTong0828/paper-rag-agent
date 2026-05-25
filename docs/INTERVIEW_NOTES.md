# 面试材料速查（基于 paper_rag 真实数据）

> 项目讲完之后被追问什么、用什么数据反击。

## 一句话定位

> "我做了一个面向 Agent 的论文研究 RAG 子系统，端到端跑通了 12 份 ADR 决策、34 项纯逻辑测试、Qdrant + bge-m3 + Qwen3.5-plus 真实数据指标全部达标。"

## 数字（拍着指标说）

| 维度 | 数字 |
|---|---|
| 代码 | 64 个 Python 模块、5 个工具入口 |
| 文档 | 12 份 ADR、5 份顶层文档（PLAN/STATUS/ARCHITECTURE/OPERATIONS/ACCEPTANCE_REPORT） |
| 测试 | 34/34 纯逻辑 + 63/63 包可导入 |
| 真实指标 | paper_recall@k=0.90, mrr=0.90, **cite_existence=1.00**, **suspicious_citations=0**, fpr@k 从 0.75→0.25 |
| 验收链路 | arxiv 拉取 → pymupdf 解析 → 切片 → bge-m3 encode → Qdrant + SQLite 双库 → hybrid (dense+FTS5) + RRF → reranker → qa_agentic (intent+rewrite+reflect) → citation 校验 |

## 高频追问 + 准备好的硬回答

**Q: 你的 RAG 跟普通的 langchain RAG 有什么区别？**
> 三个独有点。一是 **paper_qa 内闭环**：意图分类 → query rewrite + HyDE → dense+FTS5 hybrid + RRF → reranker → 反思迭代，主 agent 只看到一次 tool 调用，硬上限 max_inner_iters=3 防死循环（ADR-0006）。二是 **citation 双保险**：prompt 强制 [chunk:xxx] + 后置正则检测 [1] / (Author 2020) 形态，写入 `suspicious_citations` 字段（实测 Qwen3.5-plus 上 count=0）。三是 **自进化 Wiki**：从论文沉淀概念条目，patch-only + self_eval gate ≥0.7 + 24h 限频 + 异步 daemon 队列（ADR-0007/0010）。

**Q: 怎么验证 RAG 真的工作？怎么避免幻觉？**
> 评测集 5 题真实数据：paper_recall@k=0.90，**cite_existence=1.00**（所有引用都是真 chunk_id），`suspicious_citations=0`（模型完全遵守 [chunk:xxx]），must_contain=1.00（关键词覆盖）。三道防线：(1) prompt 加 "NEVER use [1] or (Author 2020)"；(2) `validate_citations` 正则剔除不在 retrieved 集的引用；(3) `detect_suspicious_citations` 兜底告警。

**Q: 为什么用双库（Qdrant + SQLite）？**
> 职责分离（ADR-0004）。Qdrant 只做向量召回 + payload metadata 过滤；SQLite 做关系数据 + 状态机 + ingest_runs 流水 + wiki_entries + qa_cache + FTS5 全文索引。**FTS5 是 SQLite 自带的**，零依赖换 BM25 + 增量更新（ADR-0010）。

**Q: 怎么做混合检索？**
> Dense (bge-m3) top 20 + Sparse (FTS5 / rank_bm25) top 20 → RRF (k=60) 融合 → 取 top_k*2 给 reranker (BGE-reranker-v2-m3) → 截 top_k。配置 `retrieve.sparse_backend=fts5|rank_bm25` 可切，FTS5 异常自动 fallback。BM25 search 接受 paper_ids 入参，先打分后过滤，避免 top-N 全是无关 paper 导致 0 命中（P1 #9）。

**Q: 真实环境集成时遇到什么坑？**
> 4 个回归（ADR-0012）。(1) `arxiv` 包升 v4 删了 `Result.download_pdf`，改用 `client.download_pdf(result, ...)` + httpx 兜底。(2) `qdrant-client` 1.18 弃用 `client.search()`，改用 `query_points()` + 兼容写法。(3) `wiki/store.py` 残留 SyntaxError 被 sqlmodel 缺失掩盖。(4) `init_store.py` 直接 new client 绕过 `get_client` 兜底，加了 `qdrant.local_path` 配置后才发现。所有问题都通过 try/except + 兜底降级吸收，主路径无 `database is locked` / `qdrant unreachable` 异常。

**Q: 怎么跟 DeerFlow 集成的？**
> 不入侵 harness（ADR-0008）。两处接入：`backend/.../community/paper_rag/tools.py` 用 LangChain `@tool` 包装 5 个工具，`_ensure_paper_rag_importable()` 优先看 `PAPER_RAG_HOME` env，否则向上找 `paper_rag/src/`，sys.path lazy 注入；`skills/custom/paper-research/SKILL.md` 写决策流。验证：5 个 tool 都能被 LangChain 正确解析 + 真实 invoke。

**Q: 怎么防 Wiki 越改越烂？**
> 五条护栏（ADR-0007）。(1) 频率限制：单 entry 24h 内最多 1 次更新，`lock_until` 字段控制。(2) Patch-only：LLM 只能输出 `add_*` 字段，definition 仅在显式给出新值时覆盖，禁止整条重写。(3) self_eval gate：LLM 同时输出置信度，<0.7 直接丢弃。(4) 版本日志：`wiki_versions` 表每次 upsert 写一条。(5) 默认关闭：`wiki.enabled=false`，先把 RAG 主路径打稳。(6) 一致性 heuristic：`consistency.py` 标 short_def / no_key_papers / self_related。

## 不要落入的陷阱

- 不要主动说"还没跑大规模评测"——你跑了 5 题真实数据指标全过了，那就是评测
- 不要给"还在做"留口子——M0~M5 P0+P1+P2 全部完成 + 验收 7/7 DoD 全过
- 不要谈"如果再有时间会..."——直接说 M6 候选（多用户 wiki / BibTeX 导出 / 离线 LLM 替换）

## 一图甩出去（如果对方问架构）

```
arxiv/s2/local ─► MinerU/pymupdf ─► section+chunk+modality ─► Qdrant + SQLite
                                                                      │
DeerFlow Lead Agent ─► paper-research SKILL ─► 5 tools ─► paper_qa ──┤
                                                              ↓       │
                                       intent → rewrite → hybrid ────┤
                                                  (dense+FTS5 RRF)    │
                                                  → rerank → reflect  │
                                                  → cite check        │
                                                                      │
              wiki_lookup  ◄────  自进化 Wiki ◄─ async queue ◄────────┘
```
