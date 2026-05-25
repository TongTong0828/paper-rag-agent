# ADR-0010 · M5 P1 检索增强、Wiki 异步化、FTS5

- **日期**: 2026-05-14
- **状态**: accepted

## 变更

### #8 Reranker 默认开

- `config.reranker.enabled` 默认 `true`
- 暴露 `cache_dir` / `use_fp16` 到配置
- `rerank.py` 加 **三重 graceful degrade**：FlagEmbedding 装不上 / 模型加载失败 / compute_score 异常 — 都退回 RRF 顺序，不抛
- 配套加 `_LOAD_FAILED` 标志位避免反复重试

### #9 BM25 paper_id 提前过滤

- `sparse_bm25.search(query, top_k, paper_ids=...)` 在打分后**先过滤再截断**，避免 top-N 全是无关 paper 导致 0 命中
- `hybrid.py` 不再做 in-memory 二次过滤

### #10 Wiki 别名补全

- `flow.py:_CREATE_PROMPT` 让 LLM 顺手输出 `aliases: []`（含中英）
- 新增 `_clean_aliases(raw, primary)`：去空 / 去长度 <2 / 去归一化重复 / 去与 primary 同名 / 上限 5
- `create_entry` 把 cleaned aliases 写到 `WikiEntry.aliases`，立即可被 `normalize.find_match` 利用

### #11 Trigger 异步化

- 新增 `wiki/queue.py`：单 daemon 线程 + `queue.Queue` + `submit_paper_indexed` / `wait_drained`
- `ingest_pipeline` 把同步 `on_paper_indexed` 换成 `submit_paper_indexed`，主路径不再被 LLM 抽取阻塞
- worker 每个任务 `from . import triggers as _t; _t.on_paper_indexed(...)`：fresh lookup，便于测试 monkey-patch
- `scripts/ingest_batch.py` 末尾加 `wait_drained(timeout=300)` 保证退出前清空
- 未来切 Redis + RQ 只需替换这个 queue 模块，调用方不变

### #7 SQLite FTS5

- 新增 `retrieve/fts5.py`：lazy CREATE VIRTUAL TABLE + INSERT/UPDATE/DELETE 三个 sync 触发器
- 增量更新（chunk 写入即同步 FTS）
- `_build_match_query` 把自然语言 query 拆 token、清非字母数字+CJK、用双引号包成 phrase atom、OR 连接
- 配置 `retrieve.sparse_backend: fts5 | rank_bm25`（默认 fts5）
- `hybrid._sparse_search` 自动选 backend，FTS5 异常时 fallback rank_bm25

### #6 Context-prefix ablation

- 新增 `tests/eval/run_ablation_context.py`：
  - 临时建两个 Qdrant collection (`*_ctx` / `*_raw`)
  - 分别用 `context_text` 与 `text` 重新 embed
  - 跑评测集对比 paper_recall@k / mrr
  - 阈值 ≥ 0.02 才判定 prefix 有效

不做：评测器本身集成进 run_eval（耗时太长）。手动调用即可。

## 工程兜底（顺手做的健壮性修复）

- `wiki/triggers.py`：把 `from sqlmodel import Session, select` 改成局部 import，让模块在零依赖时仍可被 walk 到（不再阻挡 wiki.queue 测试）
- `retrieve/fts5.py`：同样把 `sqlmodel.Session` 局部 import
- 这把 smoke 从 54/59 提升到 57/61

## 测试

新增 `tests/test_m5_p1.py` 5 项：

- FTS5 match query builder (中英 + 标点)
- alias 清洗（dedup / 去 primary / 长度过滤）
- alias 非字符串容错
- 异步 queue drain 行为
- 异步 queue 单任务失败不影响其他任务

累计 **29/29 纯逻辑测试通过**。

## 验收

- 全部 P1 完成
- 测试无回归
- 端到端验收仍待用户手工：起 Qdrant + ingest 5+ 论文 + 跑评测，预期 `paper_recall@k` 比 M5 P0 时再上升一档（reranker 启用）
