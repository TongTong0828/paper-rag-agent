# ADR-0007 · 自进化 Wiki 设计

- **日期**: 2026-05-13
- **状态**: accepted

## 范围

Wiki = 跨论文的"概念条目"集合（不是论文笔记，不是知识图谱）。entry 维度 ≈ 一个 named technique / task / dataset / metric。

## 触发点

| 触发 | 实现 |
|---|---|
| 单篇 ingest 完成 | `wiki/triggers.py:on_paper_indexed`，串在 `ingest_pipeline` 末尾，wiki.enabled=false 时短路 |
| 用户修订 | （留 TODO）UI 改 entry → 写 wiki_versions + 检查 related |
| 周期回顾 | `scripts/wiki_review.py`，按 entry 拉 evidence_chunks 复跑 patch_entry |

## 流程

1. `extract_concepts`：LLM 从 chunks 抽 ≤5 个核心概念（保 recall）
2. `find_match`：归一化名 → 别名 → wiki Qdrant 语义近邻（阈值 0.85）
3. 命中 → `patch_entry`（emit JSON patch；不重写整条），未命中 → `create_entry`（多源生成 definition + open_problems）
4. **self-eval gate**：LLM 同时输出置信度 score，<0.7 直接丢弃
5. **rate limit**：单 entry `lock_until = now + 24h`，期间忽略再次更新
6. 写 SQLite (`wiki_entries` + `wiki_versions` 版本日志) + 同步 Qdrant `wiki_entries` collection（definition embedding，便于反哺 RAG）

## 反哺

- `wiki_lookup` tool：concept → 直接命中 / 别名 / 语义近邻
- `paper_qa` 在 query_rewrite 阶段可命中 wiki 时把 definition 当 HyDE 种子（留作进一步增强；当前未启用）

## 不做

- 不做答案后整条重写（成本 + 幻觉风险）
- 不做关联条目自动生成（防爆炸）
- 不做语义去重（成本 + 误杀）

## 默认配置（保守）

```yaml
wiki:
  enabled: false                # 阶段 3 默认关，主路径不受影响
  similarity_threshold: 0.85
  rate_limit_hours: 24
  self_eval_threshold: 0.7
```
