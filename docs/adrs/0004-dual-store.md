# ADR-0004 · Qdrant + SQLite 双库分工

- **日期**: 2026-05-13
- **状态**: accepted

## 决策

向量库用 Qdrant，关系数据用 SQLite，**绝不混用**。

## 分工

| 库 | 干什么 | 不干什么 |
|---|---|---|
| **Qdrant** (`paper_chunks` / `wiki_entries`) | 向量召回 + 按 metadata 过滤（modality/year/section/paper_id） | 不做主键查询、不做关系 join、不做强一致写 |
| **SQLite** (`papers.sqlite`) | 论文/章节/chunk 元数据、引用图、Wiki 条目（结构化字段）、用户笔记、ingest 状态机 | 不做向量检索、不存大文本（chunk text 只存指针） |

## 表结构（SQLite，待 sqlmodel 实现）

```
papers (paper_id PK, title, authors_json, year, venue, doi, arxiv_id,
        abstract, status, parsed_with, created_at, updated_at)
sections (section_id PK, paper_id FK, idx, name, page_start, page_end)
chunks (chunk_id PK, paper_id FK, section_id FK, modality, page,
        text, context_text, neighbors_json, char_start, char_end)
references (src_paper_id FK, dst_paper_id FK, context, citation_marker)
ingest_runs (run_id PK, paper_id FK, status, error, started_at, finished_at)
wiki_entries (entry_id PK, name, aliases_json, category, definition,
              key_papers_json, variants_json, related_json,
              open_problems_json, version, updated_at, lock_until)
wiki_versions (entry_id FK, version, content_json, reason, created_at)
wiki_evidence (entry_id FK, chunk_id FK)
```

## Qdrant collections

- `paper_chunks`：dim 1024，distance Cosine。Payload 含全部 chunk metadata（含 text 副本，避免每次查询都回 SQLite）
- `wiki_entries`：dim 1024。Payload 含 entry_id / name / category / version

## 理由

- 向量库的关系查询慢且不灵活；SQL 的向量扩展（pgvector）单机性能/过滤不如 Qdrant
- 开发期 SQLite 零运维；生产期可平滑切 Postgres（sqlmodel 抽象）
- 双库各自独立备份/重建：Qdrant 重建从 SQLite 拉 chunk 重新 embedding；SQLite 不需要重建

## 风险

- 双写一致性：ingest 必须严格按 SQLite → Qdrant 顺序，且 SQLite 状态机驱动 Qdrant 写入
- 如果 Qdrant 写失败，SQLite 状态停留在 `embedded`，下次 retry
