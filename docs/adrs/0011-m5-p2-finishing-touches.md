# ADR-0011 · M5 P2 锦上添花（6 项）

- **日期**: 2026-05-14
- **状态**: accepted

P2 是"看进度安排"的功能项。此次一并完成。

## 决策

### #13 qa_cache（24h key=question_norm）

新增 `rag/qa_cache.py`：
- SQLite 表 `qa_cache`（lazy CREATE）
- key = `sha1(norm(question) + "|" + sorted(paper_ids))`：标准化空白/大小写、paper_ids 排序确保顺序无关
- value = 答案精简版（answer / citations / chunk_ids / trace / suspicious_citations），不存重型 chunks
- TTL 通过 `rag.qa_cache_ttl_hours` 配置，默认 24h；过期自动 evict
- 默认 `qa_cache_enabled: false`（开启需有正确性预期，重 ingest 后建议手动清表）
- `qa_agentic.answer` 入口处 `get`，返回前 `put`

### #14 评测加反例集

- `EvalItem.irrelevant_paper_ids: list[str]`（默认空）
- `metrics.false_positive_rate(predicted, irrelevant, k)`：top-k 内出现的反例占比；空 GT 返回 None 让 aggregator 跳过
- `run_eval.py` 自动算 `fpr@k`，写到每条结果 + aggregate

### #15 wiki_review 加 --limit / --stale-days / --dry-run

- `--stale-days N` 只处理 `updated_at < now - N days` 的条目
- `--limit N` 取最旧的 N 条（spread review effort over time）
- `--dry-run` 列出待 review 不调 LLM

### #16 DeerFlow 工具 docstring few-shot

5 个工具的 docstring 都加了 2-3 条具体调用示例：
- `paper_qa`：单篇 / 全局 / 多篇对比
- `paper_search`：宽口径 / 限定数量
- `paper_section`：按论文按节
- `paper_compare`：成本警告 + 调用例
- `wiki_lookup`：英文 / 中英混合

LangChain `parse_docstring=True` 会把 Examples 段也喂给 LLM，提升 tool selection 准确率。

### #17 arxiv version 单独存

- `Paper.arxiv_version: str | None`（如 "v2"）；paper_id 始终用 stripped form 保证去重
- `utils/ids.split_arxiv_version` 返回 `(id, version)`
- `ArxivSource.fetch` 解析输入里的版本号，写到 `meta.extra.arxiv_version`
- `upsert_paper` 写到列；要查 v1 vs v3 直接 `SELECT WHERE arxiv_id=? AND arxiv_version=?`

### #18 章节完整性 sanity check

- `chunk/sanity.py:grade_sections(names) -> "complete"|"partial"|"minimal"|"broken"`
- 4 个领域：intro / method / experiment / conclusion，每个用关键词 substring 命中
- `ingest_pipeline` 在 chunked 之后 set `parsed_with={parser_name}+{quality}`（如 `mineru+broken`）
- 后续可以 `SELECT WHERE parsed_with LIKE '%broken'` 找出问题论文

## 测试

`tests/test_m5_p2.py` 5 项全过：
- split_arxiv_version (含 url / 无版本)
- grade_sections complete + partial/minimal/broken
- false_positive_rate (含 None / k 截断)
- qa_cache_key_normalization (paper_ids 顺序无关 / question 大小写 / 空白)

累计 **34/34 纯逻辑测试通过**。smoke 59/63（剩 4 个真依赖 sqlmodel 的 model 类）。

## 不在范围

- qa_cache 的 LRU 容量限制（目前无；表会无限增长，定期 `DELETE WHERE created_at < ?` 即可）
- arxiv version 字段还未在 ingest_batch / ingest_one 透传（后续）
- sanity 没在 batch ingest 末尾汇总告警（"X 篇被标 broken"）
