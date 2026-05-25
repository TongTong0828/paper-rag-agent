# PERF_BASELINE.md — paper_rag 性能基线

> **生成时间**：2026-05-21
> **环境**：macOS darwin / Python 3.10.13
> **再生成**：见文末"再生路径"
> **对应文档**：`SYSTEM_DESIGN.md` §5

## 1. 测试套件运行时间（CI gate）

| 维度 | 数值 |
|---|---|
| pytest 全套（113 项，--ignore=tests/eval） | mean **3.01s**（3 次 run: 2.62 / 2.98 / 3.43s） |
| `_run_tests.py` zero-deps fallback | ~3s（107 项） |
| 模块导入冷启动总耗时 | ~370ms |

> 这是 CI gate 的硬性指标。pytest > 10s 触发 alert（暗示有人引入了网络
> 调用或 fixture 漏斗）。

## 2. 模块导入开销（影响 gateway 冷启动）

| 模块 | 冷启动耗时 |
|---|---|
| `paper_rag.rag.qa_agentic` | **316.0 ms** ⚠️ |
| `paper_rag.proactive` | 28.7 ms |
| `paper_rag.rag.qa_stream` | 16.3 ms |
| `paper_rag.feedback.store` | 4.4 ms |
| `paper_rag.observability.metrics` | 3.9 ms |
| `paper_rag.deliver.dispatch` | 2.1 ms |
| `paper_rag` (top-level) | 0.8 ms |

**关键发现**：`qa_agentic` 冷启动 316ms — 主要是 OpenAI client init + tiktoken
encoding 加载。Gateway router 已经用 lazy import 隔离，但首次 QA 请求会吃这
310ms。**优化建议**：gateway lifespan 提前 warm `qa_agentic` import。

## 3. QA 端到端延迟（估算）

> 需要 Qdrant + LLM 在线，离线环境跑不了。以下是从代码路径 + 经验值推算的
> 量级，等 production 部署后用 `tests/perf_bench.py --with-llm --queries 30`
> 实测后回填本表。

| 阶段 | P50 | P95 | 说明 |
|---|---|---|---|
| query_rewrite | 200ms | 600ms | LLM 1 次（small_model） |
| hybrid_search (BM25+dense) | 80ms | 200ms | Qdrant + SQLite FTS5 |
| rerank (BGE v2 m3) | 150ms | 400ms | top_k*3 → top_k 重排 |
| abstain.decide | 1ms | 3ms | 纯 Python 算分 |
| LLM chat (chat_model) | 1500ms | 4000ms | Qwen-plus 通过 DashScope |
| validate_citations + suspicious | 5ms | 20ms | regex 扫一遍 |
| **总 P50** | **~2.0s** | — | confident 档单轮 |
| **总 P95** | — | **~5.0s** | reflect 二轮 + 长答案 |

## 4. abstain 三档延迟分布（理论）

| 决策 | 是否调 LLM 答 | 端到端延迟 |
|---|---|---|
| `confident` (70%) | ✅ | ~2.0s |
| `weak_evidence` (18%) | ✅ + insufficiency hint | ~2.2s |
| `no_evidence` (12%) | ❌ skip | **~250ms** |

> 12% 的 `no_evidence` 直接跳过 LLM，**节约 100% LLM 调用成本**且把 P50
> 拉低到 250ms（仅 retrieve 开销）。这是 abstain 在 latency 上的隐藏收益。

## 5. 容器构建（M9.5 Dockerfile）

| 镜像 tag | 大小 | 构建时间（首次） |
|---|---|---|
| `paper-rag:lean` (默认) | ~600MB | ~3min（含 FlagEmbedding wheel） |
| `paper-rag:bake` (--build-arg MODE=bake) | ~3.5GB | ~6min（额外下 bge-m3 weights） |
| `.dockerignore` 减小后构建上下文 | ~5MB | — |

## 6. 已知瓶颈 / 优化机会

| 瓶颈 | 量级 | 优化方向 | 优先级 |
|---|---|---|---|
| qa_agentic 冷启动 316ms | gateway 首次请求慢 | lifespan warmup | P1 |
| LLM chat P95 4s | 主要延迟来源 | 流式输出 + small_model 跨 stage 复用 | P0 |
| rerank 同步阻塞 | 全 thread 占用 | rerank server / batch | P2 |
| Qdrant embedded 模式单线程 | 高并发受限 | 切 server 模式 + 6333 端口 | P1 |

## 7. 再生路径

```bash
# 测试套件耗时
cd paper_rag && time python -m pytest -q --ignore=tests/eval

# 模块导入开销
python -c "
import time, importlib, sys
mods = ['paper_rag', 'paper_rag.rag.qa_agentic', 'paper_rag.rag.qa_stream',
        'paper_rag.proactive', 'paper_rag.deliver.dispatch']
for m in mods:
    for k in list(sys.modules):
        if k.startswith('paper_rag'):
            del sys.modules[k]
    t0 = time.time()
    importlib.import_module(m)
    print(f'{m}: {round((time.time()-t0)*1000,1)}ms')
"

# 实测 QA 延迟（需要 Qdrant + LLM）
PAPER_RAG_CONFIG=config/local.yaml \
    python tests/perf_bench.py --queries 30 --with-llm
```

## 8. 历史趋势（占位）

| 日期 | 测试数 | pytest 平均 | qa_agentic 冷启 | 备注 |
|---|---|---|---|---|
| 2026-05-21 | 113 | 3.01s | 316ms | 当前基线（M9.5 + P3） |
| _下次 prod 部署后回填_ | — | — | — | — |

