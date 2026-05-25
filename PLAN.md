# PLAN.md — paper_rag 项目规划

> 唯一的"项目计划"权威文件。其它文档（README/ADR/OPERATIONS）讨论"是什么"和"怎么做"，**这里讨论"做到哪了 / 接下来做什么 / 为什么这么做"**。

- **当前版本**: `0.1.0-dev`
- **最后更新**: 2026-05-14
- **状态**: 阶段 0-4 主体完成，待端到端验收 + 生产化打磨

---

## 1. 项目愿景（North Star）

把"读论文 + 做研究"这件事做成一个**Agent 友好**的服务：

- 任何论文进来，都能被结构化地解析、索引、可问答
- 上层 Agent（DeerFlow Lead）通过 5 个简洁工具完成检索 / 阅读 / 对比 / 概念查询
- 系统能**自己**沉淀概念知识到 Wiki，越用越聪明，但**永远不会**幻觉污染原始数据

非目标：不是通用文档 RAG、不是论文翻译工具、不是引文管理器。

---

## 2. 路线图（Roadmap）

| 里程碑 | 状态 | 内容 |
|---|---|---|
| **M0** 骨架 | ✅ Done | 目录、配置、CI 准备、ADR 0001-0005 |
| **M1** 单篇闭环 | ✅ Done | A/B/C 极简版，能 ingest + 问答 |
| **M2** Agentic RAG | ✅ Done | BM25 + RRF + reranker + 意图/改写/反思/迭代 |
| **M2.5** 评测 | ✅ Done | qa_set + run_eval + LLM-judge |
| **M3** Wiki 0.1 | ✅ Done | 抽取 + create/patch + 限频 + 一致性 |
| **M4** DeerFlow 集成 | ✅ Done | community/paper_rag + skill |
| **M5** 生产化 ← **当前** | 🚧 Doing | 必修 5 项 + 异步队列 + 观测性 |
| M6 评测加固 | ⏳ | 反例集 + Ablation 实验 + dashboards |
| M7 长记忆与协作 | ⏳ | 多用户 wiki / 私有命名空间 / 引用导出 |

详细已完成项与未完成项见 [`docs/STATUS.md`](./docs/STATUS.md)。

---

## 3. 当前迭代（M5 · 生产化）

按"必修 → 值得 → 锦上添花"分级。一条对应一个 issue 编号，可勾选追踪。

### 3.1 必修（P0，本迭代必须完成）— ✅ 已完成（ADR-0009）

- [x] **#1 SQLite 并发**：`create_engine` 加 `connect_args={"check_same_thread": False}`，启动 `PRAGMA journal_mode=WAL` + `busy_timeout=5000`
- [x] **#2 跨 source 去重**：`upsert_paper` 前按 `DOI → arxiv_id → title_norm` 跨记录查；命中则返回 `merged_into=<existing_id>`，禁止重复入库
- [x] **#3 citation 兜底告警**：`citation_check.detect_suspicious_citations` 检测 `[1]` / `(Smith 2020)` 等非 `[chunk:]` 引用形态，写入 `suspicious_citations` 字段；prompt 同步加强
- [x] **#4 检索失败降级**：`qdrant_store.search` 捕获异常返回 `[]`；`qa_agentic` 在 `final_chunks` 空 / chat 失败时 short-circuit 不抛
- [x] **#5 MinerU 输出适配**：按 `magic-pdf` 实际产物结构（`<basename>/auto/`）解析；把 `images/` 复制到 `parsed_dir/figures/`；重写 markdown 图片路径
- [x] **#12（顺手）ingest_runs 表**：每步插一条 step/started/finished/error，`papers.error` 留 last error

### 3.2 值得做（P1，本迭代尽量完成）— ✅ 已完成（ADR-0010）

- [x] **#6 上下文增强 ablation**：`tests/eval/run_ablation_context.py` 临时双 collection 对比 prefix vs raw
- [x] **#7 BM25 长期方案**：`retrieve/fts5.py` SQLite FTS5 + sync 触发器；配置 `sparse_backend: fts5`；hybrid 自动选
- [x] **#8 Reranker 默认开**：`reranker.enabled=true`；暴露 `cache_dir` / `use_fp16`；三重 graceful degrade
- [x] **#9 BM25 paper_id 过滤**：sparse_bm25 / fts5 都支持 `paper_ids` 入参，先打分后过滤
- [x] **#10 wiki 别名补全**：create prompt 加 `aliases` 字段；`_clean_aliases` dedup + 去 primary + 上限 5
- [x] **#11 trigger 异步化**：`wiki/queue.py` daemon 线程 + Queue；ingest_pipeline 改 enqueue；ingest_batch 末尾 wait_drained
- [x] **#12 ingest_runs 表**：~~每步插一条~~ → ✅ 已在 ADR-0009 顺手做掉

### 3.3 锦上添花（P2）— ✅ 已完成（ADR-0011）

- [x] **#13** qa_cache：`rag/qa_cache.py` SQLite + 24h TTL；默认关；`qa_agentic` 入口/出口接通
- [x] **#14** 评测反例：`EvalItem.irrelevant_paper_ids` + `false_positive_rate` + `run_eval` aggregate `fpr@k`
- [x] **#15** wiki_review：`--limit` / `--stale-days` / `--dry-run`，按 updated_at 排序选最旧
- [x] **#16** 工具 docstring few-shot：5 个 tool 都加 2-3 条调用示例（含中英）
- [x] **#17** arxiv version：`Paper.arxiv_version` 列；`split_arxiv_version`；`ArxivSource` 透传
- [x] **#18** 章节 sanity：`chunk/sanity.py grade_sections`；`parsed_with={parser}+{quality}`

### 3.4 离开 M5 的"definition of done" — ✅ 全部达成（含 LLM-judge）

- [x] 全部 P0 完成且回归测试通过
- [x] **端到端验收（2026-05-19）** — 详见 [`docs/ACCEPTANCE_REPORT.md`](./docs/ACCEPTANCE_REPORT.md) + [ADR-0012](./docs/adrs/0012-acceptance-fixes.md)
  - [x] paper_recall@k = **1.00** (≥0.70 ✅)
  - [x] paper_mrr = **1.00**
  - [x] cite_existence = **1.00** (= 1.00 ✅)
  - [x] suspicious_citations = **0** (= 0 ✅)
  - [x] must_contain = **1.00**
  - [x] fpr@k = **0.00** (<0.30 ✅，从 retrieval-only 0.75 → +LLM 0.25 → +judge 0.00)
  - [x] **judge_faithful = 5.0** (≥4.0 ✅)
  - [x] judge_complete = 4.6 (≥4.0 ✅)
  - [x] 全流程无 `database is locked` / `qdrant unreachable` 异常
  - [x] DeerFlow 5 个 LangChain @tool 注册并真实调用成功
- [x] 文档同步：ADR-0012 + ACCEPTANCE_REPORT.md + INTERVIEW_NOTES.md + 更新 PLAN/README/CHANGELOG

---

## 4. 长期方向（M6+，提前预约的"大设计"问题）

| 方向 | 关键问题 |
|---|---|
| 多用户 Wiki | 共享 vs 私有命名空间；冲突合并策略 |
| 评测稳态 | 评测集自动扩展；从用户真实问题挑战集 |
| 引用导出 | BibTeX / Markdown 引用注释；自动跑出 References 段 |
| 离线 LLM | 把 reflect/intent/judge 这种轻量 LLM 调用换成本地 ≤7B 模型 |
| 跨 Agent 协作 | paper_rag 是否参与 DeerFlow subagent 调度？以何种 contract？ |

---

## 5. 工作流约定

- **代码风格**：ruff（`make lint` / `make format`）
- **分支**：`feat/<issue#>-<slug>` / `fix/<issue#>-<slug>` / `docs/<slug>`
- **PR 要求**：
  - 关联 issue 编号
  - 改动 ≥ 100 行需要补/改测试
  - 修改 schema / 配置项 → 同步 ADR 或更新现有 ADR
  - 影响 `paper_id` / chunk schema / embedding 模型 → **必须**写迁移说明
- **测试**：纯逻辑测试目标保持 100% 通过；新增依赖外部服务的测试要能 skip
- **ADR**：架构决策都写入 `docs/adrs/NNNN-<slug>.md`，写明 status: proposed/accepted/superseded

---

## 6. 文档地图

| 文件 | 用途 |
|---|---|
| `README.md` | 项目门面，5 分钟了解 + 快速开始 |
| **`PLAN.md`**（本文件）| 路线 / 当前迭代 / 长期方向 |
| `docs/ARCHITECTURE.md` | 一图 + 一节文字说清整套架构 |
| `docs/STATUS.md` | 已完成项 checklist（细到子任务） |
| `docs/OPERATIONS.md` | 部署、运维、故障排查 |
| `docs/adrs/*.md` | 单点决策记录（不改写历史） |
| `CHANGELOG.md` | 版本历史（Keep a Changelog 格式） |
| `CONTRIBUTING.md` | 贡献者指南 |
| `tests/eval/README.md` | 评测使用与验收线 |
