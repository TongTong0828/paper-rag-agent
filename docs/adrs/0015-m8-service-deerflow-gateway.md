# ADR-0015 · M8 服务化（接入 DeerFlow Gateway，BetterAuth，Qdrant 容器化）

- **日期**: 2026-05-19
- **状态**: accepted
- **关联 PRD**: `docs/M8_PRD.md`

## Context

paper_rag 走完 M0-M7 后，工程层面已工业级（48/48 → 60/60 测试 / 33 题端到端 / abstain 三档决策 / 完整 ADR 链）。但**产品形态仍是脚本**：用户必须 `import paper_rag`、设环境变量、跑 Python，没有 HTTP 入口、没有多用户、没有 UI。

为了从"工具"升级成"产品"，需要服务化。Day 1 调研（D 步）发现 DeerFlow 本身已经是一个 production-grade FastAPI 服务，不应自建。本 ADR 记录服务化的几条关键决策。

## Decisions

### 决策 1：复用 gateway，不另起服务

paper_rag 作为**新增 router** 接入 `backend/app/gateway/`，与已有 14 个 router 平级（threads / runs / memory / agents / uploads / ...）。

**理由**：
- DeerFlow gateway 自带 lifespan / CORS / SSE / 流式响应 / OpenAPI 自动生成
- 双服务会引入双认证、双监控、双部署，运维灾难
- 复用 RunManager / StreamBridge 让 paper_rag 与 lead_agent 共享调度器

### 决策 2：HTTP API + LangChain Tool 双形态共存

paper_rag 同时暴露：
- `/api/paper_rag/*` HTTP 端点（前端 UI / 第三方集成 / curl）
- LangChain `@tool`（lead_agent 智能编排，已存在 6 个）

两种形态共享 **同一份**底层逻辑（`paper_rag.rag.qa_agentic.answer()` / `qa_stream.stream_answer()`），只在最外层做 router 适配 vs tool 适配。

**理由**：HTTP 是"用户直接用"，Tool 是"agent 编排用"，两种产品形态都需要，不该二选一。

### 决策 3：直接接 BetterAuth（不做 X-User-Id 临时方案）

BetterAuth 在前端已就位（`frontend/src/server/better-auth/`，emailAndPassword + session SQLite）。
gateway 端新增 `app/gateway/middleware/auth.py`：
- 启动时直连前端的 BetterAuth SQLite（同 docker network）
- 每次请求查 session 表（user_id 缓存 60s 减少 IO）
- 注入 `request.state.user_id`，未登录返回 401

**理由**：
- 临时 `X-User-Id` 方案会污染所有 router，将来切回真 auth 要改一遍
- BetterAuth 已经存在且质量高，没理由不用
- 直接读 SQLite 比 HTTP roundtrip 快 10x，没有跨进程开销

**例外**：`/metrics` 走内网 CIDR 白名单（运维场景）。

### 决策 4：Qdrant 直接服务化（不留 embedded fallback）

`docker-compose.yaml` 加 `qdrant` service，paper_rag 容器通过 `QDRANT_URL=http://qdrant:6333` 连过去；本地开发仍可保留 embedded（cfg.qdrant.local_path 不变）。

**理由**：
- 多 worker / 多用户必须共享同一个向量库，embedded 单进程是死路
- 现在不切 M9 一定要切，越早越好（数据量小，迁移成本低）
- Qdrant 容器化后 volume 持久化、重启不丢数据
- 反正生产 yaml 与 dev yaml 已经分开（local.yaml + production.yaml），不破坏现有开发体验

### 决策 5：用户隔离用 user_id 列 + payload filter，不分库

- SQLite 各表加 `user_id` 列 + 索引
- Qdrant payload 加 `user_id` 字段，查询强制 `must` filter
- 历史数据 user_id = `'system'`，作为公共空间所有人可见

**理由**：
- 单 Qdrant 集合管理简单，运维成本低
- 100 用户 × 50 paper 仍只是 5000 chunk * 50 ≈ 250K vectors，单集合性能没问题
- `system` 命名空间天然支持"基础库 + 用户私有库"双层结构

### 决策 6：流式接口完全沿用 qa_stream.py（已有 7 类事件）

不新设计 SSE 协议，把 `qa_stream.stream_answer()` 的 generator 直接通过 `sse-starlette` 包装。事件类型：
- `intent` / `rewrite` / `retrieved` / `reflect` / **`abstain`** / `answer_chunk` / `done` / `error`

**理由**：协议已稳定 + qa_stream 已含 abstain 事件 + 测试已覆盖。

## Consequences

### Positive

- **2 天工程量**（不是从 0 自建的 5+ 天）
- **0 运维灾难**：单 gateway 单监控单认证
- **lead_agent 完全不动**：6 个 tool 已存在，向后兼容
- **真·产品形态**：用户能用 curl / 前端 UI 直接用，不用写代码
- **abstain / qa_history / wiki 全部 user_id 化**，为 M11 数据闭环铺平地基

### Negative / Trade-offs

- **耦合 DeerFlow gateway**：paper_rag 不再能脱离 DeerFlow 单独跑（但反正生态都在 DeerFlow 里）
- **Qdrant 多服务部署**：docker compose 多一个 service（acceptable）
- **BetterAuth 强依赖**：用户必须先注册账号（需要发布前端 + 后端，不再是单 Python 进程）
- **session SQLite 与 paper_rag SQLite 是两个文件**：不规范但符合关注点分离

## Alternatives considered

1. **paper_rag 自建 FastAPI 服务（端口 8002，nginx 反代）**：拒。两个服务 = 两套监控 + 两套认证 + 两份 Dockerfile，运维不收敛。
2. **临时用 `X-User-Id` header（开发期）**：拒。临时方案会污染所有 router 接口签名，回头改 BetterAuth 要改一遍。
3. **每个用户一个 Qdrant 集合（强隔离）**：拒。100+ 集合管理成本爆炸，且 Qdrant 不擅长 collection 频繁创建。
4. **不暴露 HTTP，只走 lead_agent**：拒。lead_agent 路径不适合纯文档管理（list papers / 删除等），且第三方集成（curl / Zapier）也需要直 HTTP。

## 验收 (DoD)

见 PRD § 10。

## 实施时间表

- **D1 上午**：router 骨架 + schema
- **D1 下午**：BetterAuth 中间件 + user_id 注入 + SQLite 表加 user_id 列
- **D2 上午**：Qdrant 服务化 + docker compose 集成
- **D2 下午**：验收测试 + README quickstart

## 后续 ADR

- **ADR-0016（M9 主动 Agent）**：cron / 订阅 / 会议日程关联
- **ADR-0017（M10 交付物）**：Markdown 综述 / PPT / Word / LaTeX
- **ADR-0018（M11 数据闭环）**：行为埋点 / hard case 自动收集 / abstain 阈值自适应
