# ADR-0021 · DeerFlow LangGraph 中间件强化（cost / latency / recursion / PII）

- **日期**: 2026-05-22
- **状态**: accepted
- **关联**: ADR-0020（gateway 中间件栈）/ deerflow agents/middlewares/

## Context

DeerFlow 主仓 langgraph agent 已经有 15 个 middleware，覆盖了 loop detection / clarification / memory / summarization / sandbox audit 等。但还有 4 个**生产级硬伤**：

1. `TokenUsageMiddleware` 只有 logging — 没接 Prometheus，没估成本
2. **没有 LLM 单步延迟追踪** — 长尾问题靠看日志猜
3. **LoopDetection 只盯重复调用**，无法处理"非重复但停不下来的合理流程"
4. **没有 PII 防护** — 用户的 email / 手机号 / API key 会原样进 LLM context 和 access log

所有这些都属于"工业级该有但没有"的范畴 — 单机 dev 跑得通，多用户上线即翻车。

## 决策

### 决策 1：升级 `TokenUsageMiddleware` 加三件事

| 维度 | 之前 | 现在 |
|---|---|---|
| logging | INFO 一行 | INFO 一行（+ model + cost）|
| Prometheus | ❌ | ✅ counter ×4：`deerflow_llm_tokens_input_total{model}` / `_output_total` / `_calls_total` / `_cost_usd_total` |
| 成本估算 | ❌ | ✅ 内置 12 个常用模型价格表，`register_model_price()` 可注入 |
| 失败行为 | logger.warning | 完全 best-effort，绝不打断 agent |

### 决策 2：新增 `LatencyTrackingMiddleware`（与 TokenUsage 配对）

- before_model 记 `time.perf_counter()`，after_model 算差值
- 写 Prometheus histogram `deerflow_llm_latency_seconds{model}`
- 长尾告警：`>5s` warning，`>30s` error
- 单线程 dict 存 `(thread_id, t0)` — 简单且对单 LLM 调用准确

### 决策 3：新增 `RecursionGuardMiddleware`（与 LoopDetection 正交）

| 中间件 | 检测维度 | 触发条件 |
|---|---|---|
| `LoopDetectionMiddleware` (existing) | 同一 tool + 同一 args | 3 次 warn / 5 次 hard |
| `RecursionGuardMiddleware` (new) | 总 step 数（不论是否重复） | soft 30 注 wrap-up / hard 50 strip tool_calls |

env 可调：`DEERFLOW_RECURSION_SOFT_LIMIT` / `DEERFLOW_RECURSION_HARD_LIMIT`

### 决策 4：新增 `PIIScrubMiddleware`（防御性 redact）

- 6 类正则：APIKEY / EMAIL / CC / PHONE_CN / PHONE / PHONE_US / IP
- before_model 阶段 mutate `state["messages"]`，对 `human` / `tool` 两类 message 的 content 做 redact
- 失败 fail-open（不阻塞 agent），结构化 INFO log + Prometheus `deerflow_pii_redacted_total{label}`
- env 全局禁用：`DEERFLOW_PII_SCRUB_DISABLED=1`

> **不是 GDPR 合规层** — 是"casual leakage"防护。真正合规要走数据分类 + DLP。

### 决策 5：注册顺序（写进 lead_agent/agent.py）

```
TokenUsage      ← 与 LatencyTracking 配对启用
LatencyTracking
PIIScrub        ← 输入端 scrub
…
LoopDetection
RecursionGuard  ← 紧跟 LoopDetection，正交防护
```

PII 在最早期 scrub，后续所有 middleware 看到的 message 都是干净的。

## Consequences

### Positive
- ✅ Token / 成本 / 延迟全可观测，Grafana panel 直接复用
- ✅ Agent 失控两道闸：LoopDetection（重复）+ RecursionGuard（总量）
- ✅ PII redact 默认开启，opt-out 而非 opt-in（更安全）
- ✅ 所有改动严格 best-effort：失败永不打断 agent flow
- ✅ 16 项纯逻辑单测，importlib 加载避开 deerflow runtime（3.10 也跑得动）

### Negative
- ⚠️ Prometheus / cost 估算价格表需要人工维护（写在源码，不走 config）
- ⚠️ PII regex 是高精度低召回，会漏边角 case（多语言地址、多变种身份证号）
- ⚠️ RecursionGuard 默认 30/50 对长任务可能误伤 — 可调

### 后续触发条件

| 信号 | 触发的下一步动作 |
|---|---|
| 价格表频繁过期 | 改成 YAML 配置 + 自动更新 |
| PII regex 漏报多 | 接 ML-based PII detection（如 presidio）|
| RecursionGuard 误伤多 | 加白名单 agent / 按 conversation_type 分级 |
| 多副本部署 | LatencyTracking 的 thread-local 计数失效 → 改 trace_id 关联 |

## Alternatives Considered

### Alt 1：把 cost / latency 写进 prometheus_client
**否决**：增加重依赖。现有 `paper_rag.observability.metrics` 60 行 stdlib 够用。

### Alt 2：用 third-party 库（如 microsoft/presidio）做 PII
**否决**：依赖 spaCy + 100MB 模型，dev 启动慢。本 ADR 走 regex MVP，留升级路径。

### Alt 3：把 RecursionGuard 合进 LoopDetection
**否决**：单一职责。LoopDetection 关注"重复"语义，RecursionGuard 关注"总量"语义，分两个 middleware 测试和调优都更容易。

### Alt 4：PII scrub 改 after_model（出口 redact）
**否决**：太晚 — 数据已经在 LLM context 里了，模型 response 里可能 echo PII。必须 before_model 做。

## Audit Trail

- 中间件源码：
  - `backend/packages/harness/deerflow/agents/middlewares/token_usage_middleware.py`（重写）
  - `backend/packages/harness/deerflow/agents/middlewares/latency_tracking_middleware.py`（新）
  - `backend/packages/harness/deerflow/agents/middlewares/recursion_guard_middleware.py`（新）
  - `backend/packages/harness/deerflow/agents/middlewares/pii_scrub_middleware.py`（新）
- 注册：`backend/packages/harness/deerflow/agents/lead_agent/agent.py` lines ~268, ~298
- 单测：`paper_rag/tests/test_langgraph_middleware.py` 16 项
- Prometheus 输出（`/metrics`）：
  - `deerflow_llm_tokens_input_total{model}` / `deerflow_llm_tokens_output_total{model}`
  - `deerflow_llm_calls_total{model}` / `deerflow_llm_cost_usd_total{model}`
  - `deerflow_llm_latency_seconds{model}`（histogram）
  - `deerflow_pii_redacted_total{label}`
- 环境变量：
  - `DEERFLOW_RECURSION_SOFT_LIMIT` / `DEERFLOW_RECURSION_HARD_LIMIT`（默认 30 / 50）
  - `DEERFLOW_PII_SCRUB_DISABLED`（默认未禁）
