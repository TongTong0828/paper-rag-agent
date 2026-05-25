# ADR-0019 · 双 SQLite 数据库边界（papers.sqlite vs feedback.sqlite）

- **日期**: 2026-05-21
- **状态**: accepted
- **关联**: ADR-0017（M11 数据闭环） / ADR-0018（M9 主动 Agent）

## Context

paper_rag 的 SQLite 存储经过 M0-M11 多个里程碑的迭代，**事实上已经分裂为两个数据库文件**，但此前从未在 ADR 中正式记录边界，导致：

1. 新模块作者不确定该写哪个库
2. 备份脚本可能漏掉其中一个
3. 跨表 JOIN 的诱惑（"反正都是 SQLite"）会拖累后续拆分

本 ADR 把现状固化为**正式架构决策**。

## 决策

paper_rag 显式维护**两个独立的 SQLite 数据库**：

| 库文件 | 存什么 | 由谁写 | 由谁读 | 路径解析函数 |
|---|---|---|---|---|
| `papers.sqlite` | papers, sections, chunks, FTS5 索引 | ingest pipeline (`store/sqlite_store`) | retrieve, qa, deliver, wiki | `paper_rag.store.sqlite_store._resolve_db_path()` |
| `feedback.sqlite` | feedback_events, subscriptions, inbox_items, paper_access | M11 router, M9 proactive | M11 collect_hard_cases, M9 cron_runner, router | `paper_rag.feedback.store._resolve_path()` |

> **不允许跨库 JOIN**。要关联两边的数据，必须在应用层做（先查 A 库的 paper_id，再查 B 库的 events）。

## Rationale

### 决策 1：分库（不合一个 papers.sqlite）

| 维度 | 合一个库 | 分两个库（采纳） |
|---|---|---|
| 写入冲突 | feedback 高频写会 lock 主库 | 隔离，QA 不受 inbox 写入影响 |
| 备份语义 | "备份知识库 = 备份用户行为" 耦合错误 | papers 可拷贝分发，feedback 是用户隐私不能跨账号迁移 |
| schema 演化 | 每次 inbox 加字段都 ALTER 主库 | 各自独立 migration |
| 测试隔离 | 单测必须用 in-memory tmpfile 才不污染主库 | `_resolve_path` 一行 monkey-patch 就行 |
| 物理迁移 | 后续要拆 Postgres 时一次性大动作 | feedback 可以先单独迁，不影响 retrieve 链路 |

### 决策 2：feedback.sqlite 同时承载 M11 反馈 + M9 proactive 的所有元数据

不再为 subscriptions / inbox / paper_access 各起一个新库：

- **同一类型的数据**（用户行为元数据），生命周期一致（随用户）、备份策略一致
- 4 张表共享一个连接池，比 4 个 SQLite 文件省 3 个 fd
- 跨表 JOIN（如 inbox WHERE paper_id IN paper_access）在同库内可行 — proactive 的 stale_scan 用过

### 决策 3：路径解析统一走两个 `_resolve_path` 函数

- `paper_access._resolve_path = lambda: feedback_store._resolve_path()` — 委托链
- 测试时 monkey-patch `feedback_store._resolve_path`，paper_access / subscriptions / inbox 全部跟随到隔离 db
- 避免每个模块各自定义路径常量带来的同步复杂度

## Consequences

### Positive
- ✅ 单测成本极低（一行 patch 就能切到 tmp db）
- ✅ 备份脚本明确：`tar papers.sqlite + feedback.sqlite`
- ✅ feedback.sqlite 可以单独 RBAC（更敏感）
- ✅ ALTER feedback 不会影响 retrieve 热路径

### Negative
- ⚠️ 应用层必须自己 join — 已通过 `paper_rag.proactive.matcher` 等模块封装
- ⚠️ 两个库需要分别初始化 schema — 已通过 `_connect()` context manager 幂等 `executescript`

### 后续触发条件

| 信号 | 触发的下一步动作 |
|---|---|
| feedback.sqlite > 1GB | 切 Postgres（先 feedback 后 papers） |
| 跨库 join 需求超过 5 处 | 重新评估是否合库 |
| 多机部署 | 强制走中心化 DB（SQLite 不行） |

## Alternatives Considered

### Alt 1：单 SQLite 库
**否决**：写入冲突 + 备份语义错误。MVP 阶段也许可以，但 M11 反馈进来后已经能感知到 inbox 高频写阻塞 QA。

### Alt 2：feedback / proactive 各起一个库
**否决**：4 张表共享 100% 一致的"用户行为元数据"语义，分库只是形式。

### Alt 3：直接上 Postgres
**否决**：M0-M11 都在工作，引入 Postgres 是 N 倍复杂度。Postgres 化是后续触发条件命中后的事。

## Audit Trail

- `papers.sqlite` 路径：`paper_rag.store.sqlite_store._resolve_db_path()` — 默认 `data/index/papers.sqlite`
- `feedback.sqlite` 路径：`paper_rag.feedback.store._resolve_path()` — 默认 `data/feedback.sqlite`
- M9 proactive 三张表与 M11 feedback_events 共享 `feedback.sqlite`，schema 在各模块的 `_connect()` 里幂等创建
- 对应测试：`tests/test_proactive.py::_fresh_db()`、`tests/test_feedback.py::_fresh_db()`、`tests/test_gateway_paper_rag.py::test_touch_paper_access_extracts_unique_ids`

