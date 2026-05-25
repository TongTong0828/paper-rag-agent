# ADR-0020 · DeerFlow gateway 中间件栈与监控架构

- **日期**: 2026-05-21
- **状态**: accepted
- **关联**: ADR-0015（M8 服务化）/ ADR-0019（双 SQLite）

## Context

M8 的最初实现里 gateway 只挂了 1 个 BetterAuth 中间件，没有：
- 请求 ID / 结构化访问日志（多服务定位问题靠猜）
- Prometheus 指标（除 paper_rag 自己 counter 外，gateway 路由完全黑盒）
- 限流（任何用户能无限次调 LLM 烧钱）
- 全局超时（Qdrant / LLM 卡住会拖死一个 worker）
- Body size 限制（恶意上传 GB 级 PDF 会 OOM）
- gzip（综述返回 200KB JSON 不压缩）

P0-P3 + M9.6 + M9.7 把这一整套补完。本 ADR 把决策固化。

## 决策

### 决策 1：8 层中间件栈，明确分层职责

```
incoming →
  1. BodySizeLimit          (cheap header check, reject early)
  2. GZip                   (compress responses, starlette stdlib)
  3. RequestId              (assign id used by everything below)
  4. AccessLog              (one JSON line per request)
  5. Prometheus             (per-route histogram + counter)
  6. RateLimit              (per-user throttle, optional Redis backend)
  7. Timeout                (60s wall-clock cap; SSE bypass)
  8. BetterAuth             (innermost — sets user_id used by routers)
→ handler
```

**为什么是这个顺序**：

- BodySize / GZip 在最外层 — 拒绝无效请求成本最低
- RequestId 必须在 AccessLog 之前 — 日志要带 ID
- AccessLog 在 Prometheus 之前 — 日志最先 flush，Prometheus 后做（写指标不应阻塞日志）
- RateLimit 在 Auth 之后取 user_id — 但中间件加载顺序是 LIFO，所以代码里 RateLimit 在 Auth 之前 add
- Timeout 在 Auth 之后 — 短路无 cookie 的恶意流量不消耗 timeout 协程
- Auth 最内层 — 离 handler 最近，user_id 注入到 request.state

### 决策 2：Prometheus 中间件用 path_template 而不是 literal URL

`/api/paper_rag/wiki/{paper_id}` 而不是 `/api/paper_rag/wiki/arxiv:2310.11511`。否则每个 paper_id 一个 label 组合，cardinality 爆炸（10000 篇论文 × 4 method × 5 status = 20 万 series）。

### 决策 3：RateLimit 双 backend（内存 + Redis），自动降级

| backend | 单副本 | 多副本 | 故障行为 |
|---|---|---|---|
| 内存 deque（默认） | ✅ | ❌（每副本独立计数） | 进程死了重置 |
| Redis Lua（可选） | ✅ | ✅ | 失败 30s 内 fallback 到内存 |

**关键不变量**：Redis 不可达时**不能**让所有请求都 401/500，必须 fail-open 走内存兜底，否则 Redis 故障 = paper_rag DDoS。

### 决策 4：observability stack 走 docker-compose override

不污染主 docker-compose.yaml。使用 `-f docker-compose.yaml -f observability/docker-compose.observability.yaml up` 组合启动。

- `prometheus`：拉 `gateway:8001/metrics` + `qdrant:6333/metrics`，15d retention
- `grafana`：自动 provision 数据源 + dashboard（13 个 panel）
- 端口：9090 / 3001（避免和 deerflow 主前端 3000 冲突）

### 决策 5：失败永远不抛错出中间件

每个中间件的 `dispatch` 都不允许把异常抛到 Starlette。失败 → 结构化 4xx/5xx JSON。这是因为：
- 中间件链外的异常处理器（如 sentry）已经覆盖
- 中间件本身的 bug 不应影响 routing

例外：BodySize 不需要 try/except（纯计算）。

## Consequences

### Positive
- ✅ 整条链路可观测（trace_id + access log + prom 指标）
- ✅ 防恶意攻击（限流 / 超时 / body size）
- ✅ 多副本 ready（Redis backend）
- ✅ 13 panel Grafana dashboard 一键启动
- ✅ 19 项中间件单测 + 130 项整体回归

### Negative
- ⚠️ 中间件多一层 → 平均请求 +0.5ms（对 LLM 主导的 P50=2s 来说可忽略）
- ⚠️ 内存 RateLimit 在多副本下不准确（已通过 Redis backend 缓解）
- ⚠️ Prometheus + Grafana 内存 ~150MB（dev 环境可承受）

### 后续触发条件

| 信号 | 触发的下一步动作 |
|---|---|
| QPS > 200 单副本 | 多副本部署 + 启用 Redis RateLimit |
| Grafana panel 不够用 | 加 alerting rules（5xx>1%、p95>5s） |
| Prom storage > 50GB | 接 Mimir / VictoriaMetrics |
| 多 region 部署 | 中间件搬到 envoy / nginx，gateway 减负 |

## Alternatives Considered

### Alt 1：用 prometheus_client 库
**否决**：增加重依赖。已有的 `paper_rag.observability.metrics` 60 行纯 stdlib 就够了（counter + histogram + render）。

### Alt 2：把所有功能塞进单个中间件
**否决**：单一职责原则。8 个中间件 8 个独立单测，diff 友好。

### Alt 3：用 fastapi-limiter / slowapi 第三方限流
**否决**：这些库依赖 Redis 强连接 + 不支持 fail-open。我们的实现 200 行覆盖 99% 场景。

### Alt 4：让 nginx 做限流 / gzip
**否决**：nginx 拿不到 user_id（在 BetterAuth 后才有）。gzip 可以分担但 starlette 已自带，不增加复杂度。

## Audit Trail

- 中间件源码：`backend/app/gateway/middleware/{auth,observability,protection}.py`
- 单测：`paper_rag/tests/test_middleware.py` 19 项
- 监控配置：`docker/observability/{prometheus/prometheus.yml,grafana/provisioning/}`
- Grafana dashboard：13 panel，uid `deerflow-gateway-paper-rag`
- 启动命令：`make obs-up`（位于 paper_rag/Makefile）
- 配置矩阵：见 `paper_rag/docs/SYSTEM_DESIGN.md` §middleware
