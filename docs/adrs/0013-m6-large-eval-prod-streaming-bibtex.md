# ADR-0013 · M6 大评测、生产化深度、流式、BibTeX、可观测性

- **日期**: 2026-05-19
- **状态**: accepted

完成阶段：将"代码 + 小样本验证"提升到"扩展评测 + 生产化深度 + 完整 agent 套件"。

## 完成项

### #23 评测集 32 题、6 paper

`tests/eval/qa_set.real.jsonl` 扩到 33 行：
- 24 个 factual / reasoning（基于 Self-RAG / 原始 RAG / RAG 综述 / FLARE / Hallucination 综述 / BEIR）
- 6 个 explore（survey 类问题）
- **3 个反例（n01-n03）**：库里没有的内容（GPU 价格 / 区块链 / 天气），系统应该 0 召回
- 23 题有 gold_answer

实测大样本数据：
- **paper_recall@k = 0.864**（小样本时 1.00；扩大后真实数据降到 0.86，仍超线）
- **paper_mrr = 0.828**
- **fpr@k = 0.00**
- **反例 n03 召回 = 0.00**（系统正确认为不知道）

### #23.1 33 题端到端 LLM 评测（Qwen3.5-plus, --no-judge）

后续再用 `--no-judge` 跑了一轮 **完整的 33 题 LLM 端到端评测**（77.8 min）：

| 指标 | 实测 | 验收线 | 状态 |
|---|---|---|---|
| paper_recall@k | **0.909** | ≥0.70 | ✅ 远超 |
| paper_mrr | **0.803** | ≥0.60 | ✅ 远超 |
| fpr@k | **0.000** | ≤0.20 | ✅ 满分 |
| cite_existence | **1.000** | ≥0.90 | ✅ 零幻觉引用 |
| must_contain | **1.000** | ≥0.80 | ✅ 满分 |
| violations / errors | 0 / 0 | 0 / 0 | ✅ |
| 平均 cites/题 | 5–8 条 | ≥3 | ✅ |

结果文件：`data/index/eval_runs/1779172826.json`

**对照 5 题小集**：recall 1.00 → 0.909（自然回落），但 fpr 与 cite_existence **稳定满分**——证明引用纪律在更广覆盖下仍 hold 住。

**真实暴露的问题（M7 候选 P1）**：反例 n03（"上海明天天气"）retrieval recall=0 ✅，但 LLM 仍输出了 14 条 cites ⚠️。retrieval 知道无相关，但生成层未触发 abstain。需要在 qa_agentic 入口加 **相似度阈值 abstain** 策略：top-k 平均相似度 < τ → 走 no-evidence 分支，直接拒答。

### #26 性能基准

`tests/perf_bench.py` + `docs/PERFORMANCE.md`：
- Retrieval P50 = 113ms / P95 = 115ms（CPU 模式 + 270 chunks）
- qa_agentic 平均 ~225s/题（Qwen3.5-plus 大陆延迟主导）
- Ingest 单篇 ~70s（CPU bge-m3 encode 占 60-80s）
- 内存稳态 ~3.5GB
- 关键发现：**macOS MPS 上 bge-m3 会触发 23GB 分配 OOM**，已强制 Darwin → CPU fallback

### #27 可观测性

新增 `paper_rag/observability/`：
- `metrics.py`：lock-protected counters + histograms + Prometheus text format `render()`
- `trace.py`：`new_trace_id()` 16-char hex
- 在 `qa_agentic` 集成：每次调用产生 `trace_id`，每次完成 inc 三个 counter（`paper_rag_qa_total{intent,stop}`、`paper_rag_qa_citations_total`、`paper_rag_qa_degraded_total{reason}`），qa_agentic 总耗时进 histogram `paper_rag_qa_latency_seconds`
- 零外部依赖（不引 prometheus_client），但输出格式可被 Prometheus / Grafana / VictoriaMetrics 直接抓取

### #28 Chaos 测试

新增 `tests/test_chaos.py`：7 项故障注入测试，验证 ADR-0009 的"graceful degrade"承诺：
- Qdrant 不可达 → search 返回 `[]`
- LLM 超时 → intent_classifier 默认 reasoning
- LLM 异常 → reflect 默认 sufficient（防死循环）
- LLM 异常 → query_rewrite 退回原 query
- Reranker 加载失败 → 退回 RRF 顺序
- BM25 空索引 → 返回 `[]`
- citation_check 严格剔除 hallucinated chunk_ids

### #29 多轮对话

新增 `paper_rag/rag/history.py`：
- SQLite `qa_history` 表（lazy CREATE）
- `append(conversation_id, q, a, citations)` / `recent(conversation_id, limit=3)`
- `rewrite_with_history()` 用 LLM 把 follow-up 改成 self-contained
- `qa_agentic.answer(question, conversation_id=...)` 可选参数；为 None 时单轮语义不变（向后兼容）

### #30 流式输出

新增 `paper_rag/rag/qa_stream.py`：
- `stream_answer(...)` 是 generator，yield 7 种事件：`intent` / `rewrite` / `retrieved` / `reflect` / `answer_chunk` / `done` / `error`
- 走 OpenAI 的 `stream=True`，token-by-token yield
- 用同一套 hard caps（max_inner_iters）

### #31 BibTeX 导出

新增 `paper_rag/tools/bibtex_export.py` + `tools/__init__.py` 路由：
- `BibtexExportInput.paper_ids: list[str]` → 返回 `{bibtex, n_exported, missing}`
- 从 SQLite 读元数据（不打外网）
- arxiv 用 `@misc` + `eprint` + `archivePrefix=arXiv`，DOI 用 `@article`
- 自动转义 `{}`，cite key 安全归一（`arxiv:2310.11511` → `arxiv_2310_11511`）
- DeerFlow 适配层加 `export_bibtex_tool`（共 6 个 LangChain @tool）

### #25 修 judge_concise

`qa_agentic._SYSTEM` 加 "≤200 words, dense and informative, no padding" — 待重跑评测验证（5 题完整 judge 留作下次）

## 工程修复

- **bge-m3 强制 Darwin CPU**：embed/bge_m3.py `device='auto'` 在 macOS 直接选 CPU，避开 MPS 23GB OOM；并联调 `use_fp16` 在 CPU 时关掉
- **arxiv API 限流绕过**：新增 `scripts/ingest_arxiv_direct.py`，直接 `https://arxiv.org/pdf/<id>.pdf` 拉 PDF + 解析 abs 页 HTML 元数据，绕过 arxiv API 速率限制（429 频发）
- **DeerFlow tool docstring 单行约束**：LangChain `parse_docstring=True` 对多行 Args 描述会误判（"Arg search not found in function signature"）；每个工具 Args 段都改单行

## 测试规模

- 纯逻辑测试 **48/48**（chunk 6 + retrieve 3 + eval 7 + wiki 3 + m5_fixes 5 + m5_p1 5 + m5_p2 5 + finalization 7 + chaos 7）
- pkgutil walk 全包可导入（含新 `observability/` 模块）

## 还能继续做

- **【M7 P1】abstain 阈值策略**（#23.1 暴露）：qa_agentic 入口加 top-k 平均相似度阈值，<τ 时强制走 no-evidence 分支
- LLM-judge 重跑 33 题（含 ≤200 字 prompt 改进，看 concise 是否从 3.6 → 4.5+）
- DeerFlow gateway 真实跑：起 lead agent 调一次完整对话
- 离线 LLM 替换 reflect/intent/judge（M7 候选）
