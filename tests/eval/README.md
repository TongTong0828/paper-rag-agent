# paper_rag 评测说明

## 何时用什么模式

| 阶段 | 命令 | 评什么 | 需要 |
|---|---|---|---|
| 没装 LLM、刚入完库 | `--retrieval-only` | 召回@k、MRR | Qdrant + bge-m3 + 已 ingest 的论文 |
| 想看完整 RAG 但不花 LLM-judge 钱 | `--no-judge` | 上面 + 引用准确率 + must_contain | 加上 LLM API |
| 完整端到端 | （默认）| 上面 + judge_faithful/complete/concise | 加上 gold_answer |

## 评测集 schema

每行一个 `EvalItem`（见 `schema.py`）：

```jsonc
{
  "qid": "q001",
  "question": "...",
  "intent": "factual|reasoning|explore",
  "relevant_paper_ids": ["arxiv:..."],   // paper-level GT，必填
  "relevant_chunk_ids": ["..."],          // chunk-level GT，可选（有就能算 cite_precision）
  "must_contain": ["关键术语"],            // 答案必须出现的子串
  "must_not_contain": ["错误数字"],        // 答案不能出现的子串
  "gold_answer": "...",                   // 给 LLM-judge 用，可选
  "notes": "..."
}
```

## 标注流程建议（成本最低）

1. 选 5~10 篇你最熟悉的论文，先 `python scripts/ingest_one.py` 入库
2. 每篇出 3~5 个问题，覆盖 factual / reasoning，先**只填 paper_id 和 must_contain**
3. 跑 `--retrieval-only` 看 paper_recall@k；recall 上得去再做下一步
4. 加 1~2 个 explore 问题（多文献综合）
5. 重要的题再补 gold_answer，跑完整 judge

**先跑 retrieval-only，把检索调到 ≥0.7 再上完整 RAG**——不然评测的全是 LLM 抖动而不是检索能力。

## 输出

- 每条一行简报（recall/mrr/cites/must_contain/cite_p）
- 末尾 aggregate 表
- 完整 JSON 落到 `data/index/eval_runs/<timestamp>.json`，方便 diff 历次实验

## 验收线（阶段 2.5 目标）

- `paper_recall@k` ≥ 0.7（检索能拿到对的论文）
- `cite_existence` = 1.0（不应该有伪造引用，qa_agentic 已带校验）
- `cite_precision` ≥ 0.6（如有 chunk-level GT）
- `must_contain` ≥ 0.8（关键词覆盖）
- `judge_faithful` ≥ 4.0（如有 gold_answer）

## 加大评测规模时

- 评测耗时 ≈ N × (检索时间 + LLM 时间 × 迭代轮数)
- LLM-judge 单题 ≈ 1 次额外调用
- 大批量先 `--retrieval-only`，之后再分段跑完整 RAG
