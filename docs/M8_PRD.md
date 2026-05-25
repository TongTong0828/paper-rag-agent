# M8 PRD · paper_rag 服务化（接入 DeerFlow Gateway）

- **状态**: draft
- **作者**: paper_rag 团队
- **日期**: 2026-05-19
- **关联 ADR**: 0015
- **预计工程量**: 2 天
- **目标里程碑**: M8 服务化

---

## 1 · 目标 (Goals)

**把 paper_rag 从"脚本/库"升级成"DeerFlow 用户能直接用的产品能力"**。

具体落到：

1. **任意用户可通过 HTTP** 使用 paper_qa / ingest / wiki，不需要写 Python。
2. **多用户隔离**：不同 BetterAuth 用户的 paper / wiki / 对话 / abstain 配置互不可见。
3. **DeerFlow lead_agent 自动调度** 已有的 6 个 LangChain tool（无需重做）。
4. **Qdrant 服务化**：从 embedded（`local_path`）切到 docker compose 起的独立服务，支持多进程共享。
5. **可观测**：paper_rag observability 的 Prometheus counters 通过 gateway `/metrics` 暴露。
6. **一键启动**：`make up` 之后用户 30 秒能问出第一个问题。

---

## 2 · 非目标 (Non-Goals)

- ❌ 自建 FastAPI 服务（DeerFlow gateway 已存在，不重复造轮子）
- ❌ 全新认证体系（直接接 BetterAuth）
- ❌ 公网部署 / k8s 编排（先做本地 docker compose 单机版）
- ❌ 计费 / 配额 / 限流的完整方案（M8 只做最小限流，配额 M11 闭环阶段做）
- ❌ 移动端原生客户端（用现有 Next.js 前端）

---

## 3 · 现状 (Context)

### 3.1 DeerFlow 已具备（D 调研）

- `backend/app/gateway/`：完整 FastAPI 网关，14 个 router（threads / runs / memory / agents / uploads / artifacts / channels / mcp / skills 等）
- `docker/docker-compose.yaml`：5 服务编排（nginx / frontend / gateway / langgraph / provisioner）
- `frontend/src/server/better-auth/`：BetterAuth 已集成（emailAndPassword）
- `harness/community/paper_rag/`：6 个 LangChain tool 已暴露（paper_qa / search / section / compare / wiki_lookup / export_bibtex）
- `paper_rag.observability`：Prometheus text-format counters + histograms（已埋点 qa_agentic）

### 3.2 Gap

- ❌ paper_rag 没有专属 HTTP 端点（只能通过 lead_agent 间接用）
- ❌ gateway 没有 `/metrics` 端点
- ❌ paper_rag 内部数据（papers/wiki/qa_history/abstain config）没有 user_id 维度
- ❌ Qdrant 跑在 embedded 模式，docker compose 没起 Qdrant service
- ❌ gateway 端没有 BetterAuth session 校验中间件（信任前端）

---

## 4 · 用户故事 (User Stories)

### US-1：研究者首次使用
> 张同学打开 deer-flow 前端，注册账号；上传 3 篇 PDF；30 秒后问"这三篇的对比"，看到流式答案 + 引用 + 一键导出 BibTeX。

### US-2：跨设备继续
> 李同学在公司电脑入了 10 篇 NLP 论文，回家用同账号登录，paper 库与对话历史完整可见。

### US-3：lead agent 智能调度
> 王同学在 lead_agent 聊天里问"帮我总结 RAG 综述并跟最近的 web 资讯对比"，lead_agent 同时调 `paper_qa_tool`（paper_rag）+ `web_search_tool`（其他 community 模块），两路证据合并答。

### US-4：运维监控
> 运维通过 `curl http://localhost:8001/metrics` 看到 paper_rag 的 QPS / abstain 分布 / 平均 cites / fpr 等指标。

---

## 5 · 接口设计 (API)

### 5.1 New Router: `app/gateway/routers/paper_rag.py`

| Method | Path | 用途 | 认证 | 流式 |
|---|---|---|---|---|
| POST | `/api/paper_rag/qa` | 单次问答 | ✅ | SSE |
| POST | `/api/paper_rag/qa/sync` | 同步问答（无流） | ✅ | ❌ |
| GET | `/api/paper_rag/papers` | 列出当前用户的 paper | ✅ | ❌ |
| POST | `/api/paper_rag/papers/ingest` | 入库一篇（arxiv id / pdf url） | ✅ | ❌ |
| DELETE | `/api/paper_rag/papers/{paper_id}` | 软删 | ✅ | ❌ |
| GET | `/api/paper_rag/wiki/{paper_id}` | 取 wiki 词条 | ✅ | ❌ |
| POST | `/api/paper_rag/wiki/{paper_id}/regenerate` | 触发重生 | ✅ | ❌ |
| GET | `/api/paper_rag/abstain/config` | 当前用户的 abstain 阈值 | ✅ | ❌ |
| PATCH | `/api/paper_rag/abstain/config` | 调整阈值 | ✅ | ❌ |

### 5.2 New Router: `app/gateway/routers/metrics.py`

| Method | Path | 用途 | 认证 |
|---|---|---|---|
| GET | `/metrics` | Prometheus text format | ❌（内网信任） |

### 5.3 请求/响应示例

```http
POST /api/paper_rag/qa
Authorization: Bearer <better-auth-jwt>
Content-Type: application/json

{
  "question": "What is Self-RAG?",
  "paper_ids": null,           // null = 全库
  "conversation_id": "thr_xx", // 可选，多轮
  "stream": true
}
```

SSE 响应（沿用 `qa_stream` 现有 7 类事件）：
```
event: intent
data: {"intent": "factual", "top_k": 5, ...}

event: rewrite
data: {"queries": [...]}

event: retrieved
data: {"iter": 0, "n_chunks": 5}

event: abstain
data: {"decision": "confident", "evidence_score": 0.71, ...}

event: answer_chunk
data: {"text": "Self-RAG is..."}

event: done
data: {"citations": ["chunk:..."], "trace_id": "abc"}
```

---

## 6 · 数据模型变更 (Data Model)

### 6.1 `papers` 表
```sql
ALTER TABLE papers ADD COLUMN user_id TEXT;
CREATE INDEX idx_papers_user ON papers(user_id);
-- 历史数据 user_id = 'system'（共享空间）
```

### 6.2 `qa_history` 表
```sql
ALTER TABLE qa_history ADD COLUMN user_id TEXT;
CREATE INDEX idx_qa_history_user_conv ON qa_history(user_id, conversation_id);
```

### 6.3 `wiki_entries` 表（已有）
```sql
ALTER TABLE wiki_entries ADD COLUMN user_id TEXT;
-- 默认 'system'（公共 wiki），支持 user_id != system 时覆盖
```

### 6.4 `abstain_user_config` 表（新建）
```sql
CREATE TABLE abstain_user_config (
    user_id TEXT PRIMARY KEY,
    threshold_low REAL NOT NULL DEFAULT 0.20,
    threshold_high REAL NOT NULL DEFAULT 0.40,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 6.5 Qdrant payload
- 每条 chunk payload 加 `user_id` 字段
- 查询时强制 filter（`get_qdrant_filter(user_id)`），降级到 `system` 公共空间作为 fallback

---

## 7 · 认证 (Auth) — 接 BetterAuth

### 7.1 流程

```
前端 (Next.js) ──> /api/auth/[...all] (BetterAuth) ──> 颁发 session cookie
                                                          ↓
前端调 gateway ──> Authorization: Bearer <session-token>
                                                          ↓
gateway 中间件 ──> 校验 session ──> 注入 user_id 到 request.state.user
                                                          ↓
paper_rag router 用 Depends(get_current_user) 拿 user_id
```

### 7.2 实现

- 新建 `backend/app/gateway/middleware/auth.py`
- 调用 BetterAuth Node 进程（`http://frontend:3000/api/auth/get-session`）校验
- 或直接读 BetterAuth SQLite session 表（更快，避免 HTTP roundtrip）
- 失败返回 401；成功 `request.state.user_id = session.userId`

### 7.3 例外
- `/metrics` 端点：内网 IP 白名单（环境变量 `METRICS_ALLOW_CIDR`）
- `/api/paper_rag/qa` 流式：第一帧前完成认证

---

## 8 · Qdrant 服务化

### 8.1 docker-compose.yaml 新增

```yaml
qdrant:
  image: qdrant/qdrant:v1.13.0
  container_name: deer-flow-qdrant
  volumes:
    - qdrant_data:/qdrant/storage
  ports:
    - "6333:6333"
  networks:
    - deer-flow

paper_rag:
  build:
    context: ../paper_rag
    dockerfile: Dockerfile
  environment:
    - PAPER_RAG_CONFIG=/app/config/production.yaml
    - QDRANT_URL=http://qdrant:6333
    - SQLITE_PATH=/data/papers.sqlite
  volumes:
    - paper_rag_data:/data
  depends_on:
    - qdrant
  networks:
    - deer-flow
```

### 8.2 paper_rag/config/production.yaml

```yaml
qdrant:
  url: $QDRANT_URL          # remote 模式
  local_path: null          # 关闭 embedded
  collection_chunks: paper_chunks
embedding:
  device: auto              # GPU 优先（生产）
```

---

## 9 · 监控 (Observability)

### 9.1 Prometheus 端点

- gateway 加 router `/metrics`
- 内部调 `paper_rag.observability.metrics.render()`
- 暴露指标：
  - `paper_rag_qa_total{intent, stop, user_id}` （加 user_id 标签可量化用户活跃度）
  - `paper_rag_qa_abstain_total{decision}`
  - `paper_rag_qa_degraded_total{reason}`
  - `paper_rag_qa_latency_seconds_*` （histogram）
  - `paper_rag_qa_citations_total`

### 9.2 Grafana 面板（可选，M8 不强制）

- 推荐 4 张图：QPS / Latency P95 / Abstain 分布饼图 / Errors

---

## 10 · 验收标准 (DoD)

| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | `make up` 后 30 秒内 `/healthz` 返回 200 | 自动测试 |
| 2 | 未登录用户访问 `/api/paper_rag/qa` 返回 401 | curl |
| 3 | 用户 A 入库的 paper 在用户 B 的 `/papers` 里不可见 | 集成测试 |
| 4 | SSE 流式 `/api/paper_rag/qa` 至少 yield 4 类事件 | curl + 校验 |
| 5 | `/metrics` 暴露 ≥ 5 个 paper_rag_qa_* 指标 | curl + grep |
| 6 | Qdrant 容器重启后数据不丢 | 重启 + 查询 |
| 7 | lead_agent 仍能正常调 paper_qa_tool（向后兼容） | 手测 |
| 8 | 60 个纯逻辑测试 + 新增至少 5 个 router 测试全绿 | pytest |

---

## 11 · 风险 (Risks)

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| BetterAuth session 校验慢（多 RTT） | 中 | qa P50 +50ms | 直接读 session SQLite（同进程内） |
| Qdrant 容器内存超限（3.5GB） | 低 | OOM | docker compose `mem_limit: 6G` |
| 历史 paper（user_id=null）数据迁移 | 中 | 现有用户体验断 | 默认填 `system`，所有人可见 |
| paper_rag CPU 模式 + remote Qdrant 跨容器网络 | 低 | retrieve P95 +20ms | 同 docker network，loopback 损耗 < 5ms |

---

## 12 · 时间表

| Day | 工作 |
|---|---|
| **D1 上午** | router 骨架（paper_rag + metrics）+ schema 定义 |
| **D1 下午** | BetterAuth 中间件 + user_id 注入 + sqlite 表加 user_id |
| **D2 上午** | Qdrant remote + docker-compose 集成 + production.yaml |
| **D2 下午** | 验收测试（5 个 router 测试 + 流式集成测试）+ README quickstart |

---

## 13 · 后续 (Out of scope, M9+)

- 多 worker 部署（uvicorn workers > 1，需要 Qdrant + Redis 协同）
- 用户配额（按 user_id 限速 / paper 数上限）
- 跨用户共享 wiki（org / team 级）
- 移动端友好（短答复模板）
