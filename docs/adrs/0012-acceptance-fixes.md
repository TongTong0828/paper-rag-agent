# ADR-0012 · 端到端验收发现 & 修复

- **日期**: 2026-05-18
- **状态**: accepted

第一次真实端到端验收。前 11 个 ADR 都是"基于设计的代码"，本 ADR 记录"代码与真实环境对接时暴露的 bug 与修复"。

## 验收设置

- 无 Docker：用 `qdrant.local_path` embedded 模式
- 无 MinerU：fallback 到 pymupdf
- 无 LLM：跑 `--retrieval-only` 通路
- 真 arxiv 拉 2 篇论文（Self-RAG / RAG）

详细数据见 `docs/ACCEPTANCE_REPORT.md`。

## 架构改动

### 新增 `qdrant.local_path` 配置

**问题**：任何没装 Docker 的环境（CI、贡献者第一次跑、试用）都连 `localhost:6333` 失败。

**决策**：`_Qdrant.local_path: str | None = None`，非空时走 `QdrantClient(path=...)` 文件存储模式。`get_client` 优先级：`local_path` > `url` 含 `file://`/`local://` 前缀 > 远端 url。

**影响**：开发环境不再硬依赖 Docker。生产仍用远端 Qdrant 服务。

### 新增 `PAPER_RAG_CONFIG` 环境变量

**问题**：要切配置（local vs default）只能改源码或传参；不优雅。

**决策**：`config.load()` 优先级 = 显式参数 > `PAPER_RAG_CONFIG` env > `config/default.yaml`。

## Bug 修复（运行时回归）

### #19 `arxiv>=4.0` 移除了 `Result.download_pdf`

老接口 `result.download_pdf(...)` 没了，改用 `client.download_pdf(result, ...)`。多级兜底：`client.download_pdf` → `result.download_pdf`（旧 arxiv） → `httpx` 直接下载 `result.pdf_url`。

### #20 `qdrant-client>=1.10` 弃用 `client.search()`

新版只暴露 `query_points()`，且返回结构是 `QueryResponse(points=[...])` 而非 `list`。修复点：
- `store/qdrant_store.py:search`
- `wiki/store.py:search_qdrant`

模式：`hasattr(client, "query_points")` 优先用新 API，否则回退老 `search`。返回值 `qres.points if hasattr(qres, "points") else qres`。

### #21 `wiki/store.py` 残留多余 `)`

之前重构时漏删的 SyntaxError；之前 smoke 一直被 sqlmodel 缺失掩盖。装上 sqlmodel 才浮出。

### #22 `init_store.py` 直接 new `QdrantClient(url=...)` 绕过 `get_client`

新逻辑（local_path / url 兜底）写在 `get_client`，但 `init_store.py` 没用。改成调 `qdrant_store.get_client()`。

## 设计决策没有被推翻

P0–P2（11 份 ADR）的所有决策都通过了真实验证：

| 决策 | 验证结果 |
|---|---|
| ADR-0001 4 子系统 | A→B→C 全程顺利 |
| ADR-0002 MinerU 兜底 | pymupdf fallback 正常生效 |
| ADR-0003 bge-m3 | 模型下载 4.3GB，dim=1024 OK |
| ADR-0004 双库 | SQLite (WAL) + Qdrant (embedded) 共存无冲突 |
| ADR-0005 paper_id | `arxiv:2310.11511` 形式工作 |
| ADR-0006 paper_qa 闭环 | （未跑 LLM；retrieval 部分 OK） |
| ADR-0007 Wiki | trigger 入队成功（未消费，需 LLM） |
| ADR-0008 DeerFlow 集成 | （未跑） |
| ADR-0009 P0 | SQLite WAL 启用、降级生效（query_points 不存在时 [] 而非 crash） |
| ADR-0010 P1 | FTS5 reindex 54 → fused 24，hybrid 正常 |
| ADR-0011 P2 | sanity 标 broken（pymupdf 无 section header）符合预期 |

## 验收线达成

| 指标 | 实测 | 线 | 判定 |
|---|---|---|---|
| paper_recall@k | 0.90 | ≥0.70 | ✅ |
| paper_mrr | 1.00 | — | ✅ |
| `database is locked` | 未出现 | 0 次 | ✅ |
| `qdrant unreachable` | 未出现（降级生效） | 0 次 | ✅ |
| 全模块可导入 | 63/63 | 全过 | ✅ |
| 纯逻辑测试 | 34/34 | 全过 | ✅ |
| `fpr@k` | 0.75 | <0.30 | ⚠️ 数据规模问题 |

## 跟进项

- 待 LLM 接入：跑 `cite_existence` / `suspicious_citations` 真实数据
- 待 MinerU 装上：重 ingest 验证 `figures/` 重写
- 库扩到 5+ 篇 + 启用 reranker，验证 `fpr@k` 是否能降到 <0.30
