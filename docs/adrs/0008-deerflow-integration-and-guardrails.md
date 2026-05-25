# ADR-0008 · DeerFlow 集成与自进化护栏

- **日期**: 2026-05-13
- **状态**: accepted

## DeerFlow 集成边界

paper_rag 是**独立 Python 包**，不入侵 backend/harness。集成只发生在两处：

1. `backend/packages/harness/deerflow/community/paper_rag/`
   - 用 `@tool` 把 paper_rag.tools.* 包装成 LangChain Tool
   - **lazy import**：只在首次工具调用时把 `paper_rag/src` 注入 sys.path（找不到就抛清晰错误）
   - 文件不依赖 `app.*`，符合 harness/app boundary（CI 测试 `test_harness_boundary.py`）

2. `skills/custom/paper-research/SKILL.md`
   - 注册 5 个 tool 名（paper_search/qa/section/compare + wiki_lookup）
   - 写决策流：何时用 search vs qa vs section vs compare vs wiki
   - 强制"引用或闭嘴"：所有 claim 必须带 [chunk:<id>]
   - 比较类硬上限：≤4 paper × ≤4 dim

## 不做

- 不改 lead_agent.agent.py
- 不改 langgraph.json
- 不在 harness 里做 paper_rag 的状态管理（独立包负责）

## 自进化护栏（落到代码）

| 护栏 | 实现位置 |
|---|---|
| 频率限制（同 entry 24h 内最多 1 次更新） | `flow.py:_rate_limited` + `_refresh_lock` |
| Patch 不 rewrite | `flow.py:patch_entry` 只接受 add_* 字段，definition 仅在显式给出新值时覆盖 |
| LLM 自评打分 < 0.7 直接丢弃 | `flow.py:_self_eval_gate` |
| 版本历史保留 | `wiki_versions` 表，每次 upsert 写一条 |
| 一致性校验（heuristic） | `wiki/consistency.py:check_entry` 标记 short_def / no_key_papers / self_related |
| Wiki 默认关闭 | `config/default.yaml: wiki.enabled: false` |
| Trigger 失败不阻塞 ingest | `ingest_pipeline.py` 用 try/except 包住 `on_paper_indexed` |

## 部署建议

- 阶段 3 默认 `wiki.enabled: false`：先把 RAG 主路径用稳，再分批开 wiki
- 开启前先跑 ≥ 30 篇 ingest，让评测线（recall@k ≥0.7）过线
- 再开 wiki 跑 5 篇看 self_eval gate 命中率（>50% 通过说明 LLM 抽取靠谱）
- 然后扩到 100 篇，跑 `scripts/wiki_review.py` 看一致性 issues 数量
