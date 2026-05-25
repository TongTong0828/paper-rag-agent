# docs/

> 文档地图。所有里程碑历史在 [`STATUS.md`](./STATUS.md)，路线图见 [GitHub Issues](https://github.com/TongTong0828/paper-rag-agent/issues)。

| 文档 | 适合谁 |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | 想理解系统结构和数据流的人 |
| [`STATUS.md`](./STATUS.md) | 想知道"什么已经做完了"的人 |
| [`OPERATIONS.md`](./OPERATIONS.md) | 要部署、运行、调试这套系统的人 |
| [`PERFORMANCE.md`](./PERFORMANCE.md) | 关心延迟 / 吞吐 / 内存 / 优化方向的人 |
| [`ACCEPTANCE_REPORT.md`](./ACCEPTANCE_REPORT.md) | 端到端验收的真实数据 |
| [`INTERVIEW_NOTES.md`](./INTERVIEW_NOTES.md) | 面试讲解硬料 |
| [`M8_PRD.md`](./M8_PRD.md) | 服务化 PRD（接入 DeerFlow gateway / BetterAuth / Qdrant 容器化） |
| [`M10_PRD.md`](./M10_PRD.md) | 交付物 PRD（Markdown 综述 / PPT / Word / LaTeX bib） |
| [`M11_PRD.md`](./M11_PRD.md) | 数据闭环 PRD（行为埋点 / hard case / abstain 自适应） |
| [`M9_PRD.md`](./M9_PRD.md) | 主动 Agent PRD（日报 / 订阅 / 提醒 / 自动 ingest） |
| [`adrs/`](./adrs/) | 想理解"为什么这么设计"的人 |

## ADR 索引

| # | 决策 | 状态 |
|---|---|---|
| [0001](./adrs/0001-four-subsystems.md) | 4 子系统解耦 | accepted |
| [0002](./adrs/0002-mineru-local.md) | MinerU 本地版 | accepted |
| [0003](./adrs/0003-embedding-bge-m3.md) | Embedding 选 bge-m3 | accepted |
| [0004](./adrs/0004-dual-store.md) | Qdrant + SQLite 双库 | accepted |
| [0005](./adrs/0005-paper-id.md) | paper_id 三级规则 | accepted |
| [0006](./adrs/0006-agentic-paper-qa.md) | paper_qa 内闭环 | accepted |
| [0007](./adrs/0007-wiki-self-evolve.md) | 自进化 Wiki 设计 | accepted |
| [0008](./adrs/0008-deerflow-integration-and-guardrails.md) | DeerFlow 集成 + 护栏 | accepted |
| [0009](./adrs/0009-m5-p0-fixes.md) | M5 P0 生产化修复 | accepted |
| [0010](./adrs/0010-m5-p1-retrieval-async-fts5.md) | M5 P1 检索增强 / Wiki 异步 / FTS5 | accepted |
| [0011](./adrs/0011-m5-p2-finishing-touches.md) | M5 P2 qa_cache / 反例评测 / wiki_review / 工具 docstring / version / sanity | accepted |
| [0012](./adrs/0012-acceptance-fixes.md) | 端到端验收发现 & 修复（embedded Qdrant / arxiv v4 / query_points） | accepted |
| [0013](./adrs/0013-m6-large-eval-prod-streaming-bibtex.md) | M6 大评测 / 性能基准 / 可观测性 / Chaos / 多轮 / 流式 / BibTeX | accepted |
| [0014](./adrs/0014-abstain-three-tier-decision.md) | M7 P0 abstain 三档决策（解决 n03 无证据仍出 cites 问题）+ signal_quality fail-open | accepted |
| [0015](./adrs/0015-m8-service-deerflow-gateway.md) | M8 服务化（接 DeerFlow gateway / BetterAuth / Qdrant 容器化 / user_id 隔离） | accepted |
| [0016](./adrs/0016-m10-deliverables.md) | M10 交付物升级（Markdown 综述 / PPT / Word / LaTeX bib） | accepted |
| [0017](./adrs/0017-m11-data-feedback-loop.md) | M11 数据闭环（行为埋点 / hard case 自动收集 / abstain 阈值自适应 / 人在 loop） | accepted |
| [0018](./adrs/0018-m9-proactive-agent.md) | M9 主动 Agent（日报 / 订阅 / Stale 提醒 / 自动 ingest） | accepted |
| [0015](./adrs/0015-m8-service-deerflow-gateway.md) | M8 服务化（接 DeerFlow gateway / BetterAuth / Qdrant 容器化 / user_id 隔离） | accepted |
