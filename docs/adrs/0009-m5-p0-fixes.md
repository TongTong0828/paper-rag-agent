# ADR-0009 · M5 P0 生产化修复

- **日期**: 2026-05-14
- **状态**: accepted

## 背景

M0–M4 主体完成后做了一次"上生产前必修项"诊断，得到 5 个会在真实流量下出问题的点。本 ADR 记录修复方案与权衡。

## 决策

### #1 SQLite 并发写

`get_engine` 改用：

```python
create_engine(
    url,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 5},
)
event.listen(engine, "connect", _apply_pragmas)  # PRAGMA journal_mode=WAL ...
```

每连接 PRAGMA：

- `journal_mode=WAL`：读不阻塞写，写不阻塞读
- `busy_timeout=5000`：5s 内忙等再失败，避免立即抛 `database is locked`
- `synchronous=NORMAL`：WAL 安全，吞吐 ~2x
- `foreign_keys=ON`：默认是关的，强制开

代价：WAL 模式 SQLite 多生成 `-wal`/`-shm` 两个文件；备份要带上。

### #2 跨 source 去重

新增 `find_existing_paper(doi=, arxiv_id=, title_norm=)`，按优先级 DOI > arxiv_id > title_norm 探。命中 → ingest_pipeline 直接返回 `{status: skipped, merged_into: <existing_id>}`。

不做语义去重（成本 + 误杀），不自动合并已有 entry 的 metadata（避免覆盖好数据，等显式 `--force` 用户决策）。

### #3 citation 兜底告警

`detect_suspicious_citations`：正则识别 `[1]` / `(Vaswani et al., 2017)` 等非 `[chunk:]` 引用形态。`qa_simple` / `qa_agentic` 把它写到返回的 `suspicious_citations` 字段，并 `log.warning`。Prompt 同步加强："NEVER use [1] or (Author 2020) — they will be considered hallucinated"。

下游评测 / DeerFlow 工具适配层可以读这个字段决定是否信任答案。

### #4 Qdrant 检索失败降级

`qdrant_store.search` 用 try/except 包住调用，异常返回 `[]` 并 `log.warning`。`qa_agentic` 在 `final_chunks` 为空时 short-circuit 不调 LLM；额外把 chat 调用也包进 try/except，挂掉时返回 `(LLM unavailable; see chunks for evidence)` 而不是炸到 user。

trace 里增加 `degraded` 字段（值 `no_chunks` / `chat_error:<class>`）便于排查。

### #5 MinerU 输出适配

按 `magic-pdf` 实际产物结构重写 `parse_pdf`：

```
out_dir/<basename>/auto/<basename>.md   →  out_dir/paper.md  (image paths rewritten)
out_dir/<basename>/auto/images/         →  out_dir/figures/  (copied)
out_dir/<basename>/auto/*content_list*.json → out_dir/layout.json
```

实现要点：
- `_locate_outputs` 优先查标准布局，否则 fallback 找最大 `.md`
- `_normalize_into` 复制图片到 `figures/`，`paper.md` 里所有 `![](images/foo.png)` 重写为 `![](figures/foo.png)`
- 任意环节失败 → `MineruError`，由 `dispatcher` 降级到 `pymupdf`

### 顺手做的 #12 ingest_runs 表

新增 `IngestRun` 模型 + `record_ingest_step` / `finish_ingest_step`。`ingest_pipeline` 用 `_step()` 包住每个阶段，失败时同时写入 `Paper.error`（last error）和 `IngestRun`（流水）。这样 debug 流程：先看 `papers.status`，再 `SELECT * FROM ingest_runs WHERE paper_id=? ORDER BY id`。

## 测试

新增 `tests/test_m5_fixes.py` 5 项：

- numeric / author_year / clean 三种 suspicious 形态
- validate_citations drops unknown
- mineru image path rewrite

累计 **24/24 纯逻辑测试通过**。`smoke` 维持 54/59（sqlmodel 相关 5 个待装依赖）。

## 不在本 ADR 范围

- #6 ablation / #7 FTS5 / #8 reranker 默认开 / #9 BM25 paper_id 提前过滤 / #10 wiki 别名补全 / #11 trigger 异步化 — 进 PLAN.md 3.2 P1
- #13–#18 P2

## 验收（部分通过）

- ✅ 全部 P0 代码完成
- ✅ 回归测试通过（24/24 纯逻辑）
- ⏳ 端到端验收（需要用户启 Qdrant + 装依赖 + 喂论文，paper_recall@k ≥ 0.7、suspicious_citations = 0）
