# ADR-0018 · M9 主动 Agent（日报 / 订阅 / 提醒 / 自动 ingest）

- **日期**: 2026-05-21
- **状态**: accepted
- **关联 PRD**: `docs/M9_PRD.md`

## Context

M0-M11 的 paper_rag 是被动的：用户问、系统答。这意味着用户没理由每天打开它——是工具，不是产品。M9 要给"每天打开"一个理由：每日简报、订阅匹配、复习提醒、自动 ingest。

## Decisions

### 决策 1：v1 推送通道走前端 inbox 轮询，**不接 IM**
- 前端每 60s 调 `/api/paper_rag/inbox?unread_only=true`
- IM（钉钉 / 飞书 / 邮件）留 M11.5 — 这些都需要外部凭证 + 限流策略
- 优点：闭环最短，本地能跑通

### 决策 2：调度走 in-process APScheduler，**不接 WorkBuddy automation**
- 虽然 WorkBuddy 已有 cron 表，但它是 **LLM-prompted automation**，每次唤醒都跑 LLM
- paper_rag 自己的主动任务是**确定性逻辑**（取 arxiv → match → 写 inbox），不需要 LLM 决策每一步
- in-process APScheduler 更省钱、更可控
- 单 worker 限制（多 worker 会重复跑）→ 文件锁 + 部署期 `--workers 1`

### 决策 3：订阅模型用 keyword + dense 主题向量，**不做 NL 理解**
- 用户输入关键词 → 提供"严格 / 普通 / 宽松" 三档 strength（对应 sim 阈值）
- 不解析 "我对 RAG 综述感兴趣" 这种 NL — v1 用关键词完全够用，前端只暴露 keyword input
- 高阶 NL 订阅留 M12

### 决策 4：自动 ingest 通过 chat 流处理 hook，**不阻塞答复**
- `qa_agentic.answer()` 入口检测 arxiv URL → `asyncio.create_task(background_ingest(...))`
- 答复正常返回，ingest 完成后写 inbox
- 用户体验：先得答案，10 秒后收到"已为你入库"提醒

### 决策 5：inbox 是新表（`inbox_items`），不复用 feedback_events
- inbox 是**系统给用户**，feedback 是**用户给系统**——方向相反，schema 完全不同
- 复用名字会让两套关注点混淆
- 都在 `feedback.sqlite` 文件下（与 papers 主库分开）以保持 schema 演化自由

### 决策 6：`paper_access` 表用于 stale 检测，**不复用 conversation history**
- 多轮对话历史已存在 (M6 #29)，但它是按 conversation_id 组织的
- stale 需要按 (user_id, paper_id) 组织 + 累计访问次数
- schema 太不同，独立表更清晰

### 决策 7：订阅匹配 hook 进 ingest pipeline 末端
- 新 paper ingest 完成 → 触发 `on_paper_ingested(paper_id, user_id_who_ingested)`
- 不通知 ingester 自己（避免冗余）
- 跨用户隔离：A 的私有 paper 在 user_id != A 的订阅匹配中**跳过**（除非是 'system' 共享空间的 paper）

### 决策 8：每日 digest 用 small_model（小模型）做 TL;DR
- 关键词命中的论文可能多达 10-20 篇 → 全用 chat_model 单次调用太贵
- 小模型（qwen-turbo 或同等）每篇 50 字摘要，满足"扫一眼"场景
- 配置项：`config/default.yaml` 已有 `llm.small_model`，复用

## Consequences

### Positive
- 给用户每天打开 paper_rag 的理由（日报 + 提醒）
- 真正的"主动 Agent"产品形态
- 数据飞轮：用户访问 paper → paper_access 更新 → stale 检测准确 → 复习提醒有效 → 用户回访
- ingest hook 让 chat 体验更自然

### Negative / Trade-offs
- 调度依赖单 worker 部署（M9.5 才能上 Redis-backed 队列）
- arxiv API 速率限制风险（已被 M6 踩过）
- inbox 表无限膨胀风险（30 天 retention 治理）
- TL;DR 成本：100 用户 × 5 关键词 × 24h 平均 2 篇 = 1000 次 small_model 调用/日
  - 缓解：跨用户共享 TL;DR 缓存（同一篇 paper TL;DR 只算 1 次）

## Alternatives considered

1. **接 WorkBuddy automation 引擎跑 LLM agent** ❌：每次唤醒都 LLM 跑，成本 100x，过度设计
2. **用 Redis Streams + Celery** ❌：单机部署用不上，运维负担
3. **用户用 NL 描述订阅（"凡是 ColBERT 后续工作"）** ❌：v1 keyword 已够，NL 理解风险大
4. **每个用户独立 cron entry** ❌：100 用户 = 100 cron job 管理灾难，统一 batch 跑更简洁
5. **复用 feedback_events 表存 inbox** ❌：见决策 5
6. **直接 push 到 Slack/钉钉** ❌：v1 闭环最短优先，IM 留 M11.5

## 验收 (DoD)
见 PRD § 10。

## 后续 ADR
- ADR-0019（M9.5 推送多通道）：WebSocket / 邮件 / IM webhook
- ADR-0020（M12 ML 推荐）：跨用户协同 / 个性化排序
- ADR-0021（M13 A/B 实验框架）：当数据稳定后引入
