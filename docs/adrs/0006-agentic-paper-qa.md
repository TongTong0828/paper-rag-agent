# ADR-0006 · Agentic RAG 内闭环 (paper_qa)

- **日期**: 2026-05-13
- **状态**: accepted

## 决策

`paper_qa` 内部以"小 agent"形式做闭环：意图分类 → query 改写 → 混合检索 → rerank → 反思 →（不足则迭代）→ 答案 + 引用校验。**主 agent（DeerFlow Lead）只看到一次 tool 调用**。

## 边界

| 能力 | 在哪做 |
|---|---|
| Plan / Todo / 多 tool 编排 / 派 subagent | 主 agent（DeerFlow Lead） |
| 单次 RAG 内的多跳、改写、反思 | `paper_qa` 内闭环 |

## 硬上限（写死在 config）

- `rag.max_inner_iters: 3`（即使意图分类要 explore=3，也不超）
- `rag.max_inner_tokens: 8000`
- 单次反思失败/异常 → 默认 sufficient，避免死循环

## 配置

- `rag.enable_hyde: true` 控制 HyDE 是否参与改写
- `rag.enable_reflect: true` 控制是否做检索后反思
- `reranker.enabled` 默认 false（依赖较重，按需开）

## 不做

- 不在主 agent 与 paper_qa 间共享中间 chunks（避免污染主上下文）
- 不在 paper_qa 内做"答案后反思"（成本高，价值有限）
- 不让 paper_qa 自己派 subagent（边界混乱）
