# PERFORMANCE — paper_rag 性能基准

> 测试环境：macOS arm64 / Python 3.10.13 / 32GB RAM / **CPU only**（embedded Qdrant + bge-m3 CPU 模式）
> 库规模：6 paper / 270 chunks
> 日期：2026-05-19

## 检索阶段（hybrid: dense + FTS5 + RRF）

10 query 串行测试（warm-up 后稳态）：

| Stage | mean | P50 | P95 | P99 |
|---|---|---|---|---|
| 端到端 retrieval | **113ms** | 113ms | 115ms | 115ms |

包含：bge-m3 query encode → Qdrant search (top 20) → FTS5 search (top 20) → RRF fuse → 截 top_k*2

**结论**：检索延迟非常稳定（方差 <2ms），跟 query 复杂度几乎无关。CPU 跑 bge-m3 query encode 是主要开销（~80ms）。

## 端到端 qa_agentic（含 LLM）

5 题真实评测：

| 指标 | 值 |
|---|---|
| 平均/题 | ~225s（3.7 分钟） |
| factual intent (1 iter) | ~120-180s |
| reasoning intent (2 iter) | ~250-380s |
| LLM-judge 单次 | ~10-30s |

**瓶颈**：Qwen3.5-plus 单次 chat 调用 ~20-60s，单题需要 4-8 次 LLM 调用：
1. intent classification (1×)
2. query rewrite (1×)
3. reflect (0-2×)
4. final answer (1×)
5. judge (1× factual / 1× complete / 1× concise)

**优化方向**：
1. 启用 `qa_cache`（重复 query 直接复用）
2. 把 intent / reflect / judge 这三个轻量调用换成 small_model（如 qwen-turbo）
3. dense_queries 并行 retrieve（当前是串行 4 次，可并发）

## Ingest 阶段

单篇论文（pymupdf 解析 + 50 chunks，CPU 模式）：

| 阶段 | 耗时 |
|---|---|
| arxiv PDF 下载 | 2-5s |
| pymupdf 解析 | <1s |
| chunk 切分 | <1s |
| **bge-m3 encode 50 chunks** | **60-80s** |
| Qdrant upsert | <1s |
| FTS5 增量同步 | <0.1s |
| **总计** | **~70s/篇** |

bge-m3 encode 是主要瓶颈。GPU 模式下可降到 ~10s。

## 模块加载（cold start）

| 项目 | 耗时（首次）| 缓存后 |
|---|---|---|
| bge-m3 模型加载 | 5-10s（已下载）| 5-10s |
| FlagReranker 加载 | 5-10s | 5-10s |
| Qdrant embedded 启动 | <1s | <1s |
| SQLite WAL 初始化 | <0.1s | <0.1s |
| **冷启动总计** | **~12-22s** | — |

## 内存占用

| 进程 | RSS |
|---|---|
| Python + bge-m3 + Qdrant embedded | **2.5-3.5 GB** |
| 加 FlagReranker | +1.5 GB |
| 加 LLM SDK | +200 MB |

> ⚠️ macOS Metal (MPS) 后端尝试分配 23GB 内存，遇 OOM。已强制 CPU 模式（`embedding.device=auto` 在 Darwin 自动 fallback CPU）。

## 评测耗时（参考）

| 评测模式 | 题数 | 总耗时 | 单题平均 |
|---|---|---|---|
| `--retrieval-only` | 33 | **17.9s** | 0.54s/题 |
| `--no-judge` | 5 | 643s | 128s/题 |
| `--no-judge` | **33** | **4670s（77.8min）** | **141s/题** |
| 完整 + judge | 5 | 1124s | 225s/题 |

### 33 题端到端实测（M6，2026-05-19）

| 指标 | 实测 | 验收线 | 状态 |
|---|---|---|---|
| paper_recall@k | **0.909** | ≥0.70 | ✅ 远超 |
| paper_mrr | **0.803** | ≥0.60 | ✅ 远超 |
| fpr@k | **0.000** | ≤0.20 | ✅ 满分 |
| cite_existence | **1.000** | ≥0.90 | ✅ 零幻觉引用 |
| must_contain | **1.000** | ≥0.80 | ✅ 满分 |
| violations / errors | 0 / 0 | 0 / 0 | ✅ |
| 平均 cites/题 | 5–8 条 | ≥3 | ✅ |

**对比 5 题小集**：recall 1.00 → 0.909（自然回落，扩样本暴露 1–2 题边界），但 fpr 与 cite_existence **稳定满分**——证明引用纪律在更广覆盖下仍然 hold 住。

**真实暴露问题**（M7 候选 P1）：反例 n03（"上海明天天气"）retrieval recall=0.00 ✅，但 LLM 仍输出 14 条引用 ⚠️——retrieval 知道无相关，但生成层未触发 abstain。需要加入 **相似度阈值 abstain** 策略。

> 结果文件：`paper_rag/data/index/eval_runs/1779172826.json`

## 验收线

| 指标 | 当前 | 目标 |
|---|---|---|
| Retrieval P95 | 115ms | <300ms ✅ |
| qa_agentic P50 (factual) | ~150s | <60s（启 small_model + cache） |
| Ingest 单篇 | 70s | <30s（GPU） |
| 内存稳态 | ~3.5GB | <8GB ✅ |
| **abstain.decide() 延迟** | **2.3μs/call** | <1ms ✅（M7 P0 ADR-0014） |
