# Changelog

遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased] — M9 主动 Agent（ADR-0018）

### Added
- **#60 proactive 包架构** `paper_rag/proactive/`：subscriptions / inbox / paper_access / matcher / digest / stale / auto_ingest_hook 7 模块
- **#61 subscriptions 表 + 三档 strength**：low/normal/high → 0.75/0.65/0.55 sim 阈值；用户隔离 + 跨用户 delete/toggle 安全
- **#62 inbox 表 + 4 类卡片**：daily_digest / sub_match / stale_paper / auto_ingest；read/dismiss 软删 + unread_count
- **#63 paper_access 表**：(user_id, paper_id) 复合主键 + ON CONFLICT 累计 access_count
- **#64 `matcher.py`**：bge-m3 + 纯 Python cosine + 三档阈值 + 跳过 ingester（不通知发起入库的人）
- **#65 `digest.py`**：每日 arxiv 简报，小模型 50 字 TL;DR + 跨用户缓存（100x 节省）+ Markdown 卡片
- **#66 `stale.py`**：30 天未访问 paper → 复习卡片
- **#67 `auto_ingest_hook.py`**：3 种 arxiv URL 格式检测 + asyncio 后台 ingest（不阻塞答复）+ 成功/失败 inbox
- **#68 gateway 9 个端点**：GET/POST/DELETE/PATCH /subscriptions + GET /inbox + 2 个 inbox 状态 + POST /proactive/digest/run + POST /proactive/stale/run
- **#69 17 项纯逻辑测试** `tests/test_proactive.py`
- **#70 (M9.5) APScheduler cron_runner** `paper_rag/proactive/cron_runner.py`：BlockingScheduler + SIGTERM 优雅退出，daily_digest @ 08:00 + stale_scan @ Mon 09:00，env 可覆盖 cron 表达式与时区
- **#71 (M9.5) paper_rag Dockerfile 多阶段重构**：builder/runtime venv 分离 + non-root user `paperrag` + `tini` PID1，节约 ~150MB 终镜像；新增 `docker-entrypoint.sh` 4 模式（idle/cli/proactive/jupyter）；新增 `.dockerignore`（构建上下文 ~3GB → ~5MB）
- **#72 (M9.5) pyproject extras**：新增 `proactive`（apscheduler）；`_run_tests.py` 扩展到 14 个模块覆盖 abstain + deliver + gateway + feedback + proactive，**103/103 全绿**
- **#73 (P0-1) qa_sync + qa_stream 自动 touch paper_access**：QA 主路径异步 fan-out paper_ids → stale_scan 终于有真实数据；2 项纯逻辑测试
- **#74 (P0-2) docker-compose 加 paper_rag_proactive sidecar**：MODE=proactive，复用 paper_rag_data volume，PAPER_RAG_CRON_* env 可覆盖
- **#75 (P0-3) 9 个 proactive 端点 user_id 隔离全验证**：单测加 401 全覆盖 + 跨用户订阅泄漏防御
- **#76 (P1-4) abstain 阈值数据驱动标定**：calibrate_abstain.py 新增 `--mode offline`；default.yaml 0.20/0.40 → 0.21/0.48；data/index/abstain_calibration.json 审计跟踪
- **#77 (P1-5) 4 个 proactive Prometheus counter**：digest_total / stale_card_total / auto_ingest_total{status} / sub_match_total
- **#78 (P1-6/7) EVAL_REPORT.md + HARD_CASES_REPORT.md**：评测数据可追溯，反馈闭环 dogfood 入口
- **#79 (P2-8) pytest 迁移**：`[tool.pytest.ini_options]` + tests/conftest.py，pytest 114/114 与 _run_tests.py 111/111 双路径并存
- **#80 (P2-9) Makefile docker 目标 + 工具命令**：docker-build / docker-up-proactive / docker-cli / docker-shell / calibrate-abstain / hard-cases / test-pytest
- **#81 (P2-10) GitHub Actions CI** `.github/workflows/paper_rag.yml`：lint (ruff) + pytest + smoke import + offline calibration sanity + docker build smoke
- **#82 (P2-11) ADR-0019 双 SQLite 数据库决策**：papers.sqlite vs feedback.sqlite 边界正式记录
- **#83 (P3-12) SSE inbox 推送端点** `GET /api/paper_rag/inbox/stream`：长轮询 + 5s heartbeat，新 inbox item 即时推到前端
- **#84 (P3-13) webhook 4 通道**：DingTalk（HMAC SHA256）/ Feishu / WeCom / SMTP Email；inbox.write 后 best-effort fan-out；3 项单测
- **#85 (P3-14) 前端 paper_rag 占位页** `frontend/src/app/workspace/paper-rag/page.tsx`：fetch inbox + subscriptions 列表，端到端通畅
- **#86 (P3-15) PDF deliver 后端**：reportlab + 纯 Python fallback（PDF 1.4 手写）；`[deliver-pdf]` optional extra；1 项单测
- **#87 (P3-16) SYSTEM_DESIGN.md 1-pager**：架构图 + ADR 速查 + 数据流 + 故障域，30 分钟讲完整个系统
- **#88 (P3-17) PERF_BASELINE.md**：pytest 全套 3.01s / qa_agentic 冷启动 316ms / 模块导入开销表
- **#89 (P3-18) 3 张 mermaid 时序图**：abstain_flow / proactive_flow / feedback_loop
- **#90 (M9.6) DeerFlow gateway 工业级中间件栈**：`backend/app/gateway/middleware/{auth,observability,protection}.py` 三模块 7 个中间件
  - **auth.py 三处优化**：cookie 提取 session token（regex 3 模式 + fallback）/ OrderedDict LRU O(1) 驱逐 / shutdown 关闭共享 httpx client
  - **observability**：RequestId（UUID4 + 64 字符截断 + echo header）、AccessLog（JSON-line + skip /health|/metrics）、Prometheus（route path_template 防 cardinality 爆炸）
  - **protection**：BodySizeLimit（默认 50MB env 可调）、Timeout（默认 60s + SSE bypass）、RateLimit（user_id 优先 IP 兜底滑动窗口）、GZip（minimum_size=1024 starlette stdlib）
  - **8 层有序栈**：BodySize → GZip → RequestId → AccessLog → Prometheus → RateLimit → Timeout → Auth → handler
  - **16 项纯逻辑单测** `tests/test_middleware.py`，importlib 加载避开 deerflow runtime 依赖
- **#91 (M9.7-A) Grafana dashboard JSON**：13 panel `docker/observability/grafana/provisioning/dashboards/gateway.json`，含 QPS / 延迟 P50/P95/P99 / 5xx 率 / abstain 三档分布 / 4 类 proactive 推送 / 4 个 stat panel（429/504/413/401）
- **#92 (M9.7-B) RateLimit Redis backend**：`_RedisBackend` 类 + Lua 滑动窗口脚本，`(allow|reject|fallthrough, count)` 三态返回；Redis 不可达自动 fallback 到内存模式（30s 重试间隔），3 项纯逻辑单测覆盖
- **#93 (M9.7-C) 监控栈 docker-compose**：`docker/observability/docker-compose.observability.yaml` 加 Prometheus v2.55 + Grafana 11.3，attach 到 deer-flow 网络；prometheus.yml 拉 gateway:8001 + qdrant:6333；grafana provisioning 自动注入 datasource + dashboard
- **#94 (M9.7-D) ADR-0020 中间件栈与监控架构**：5 条决策（8 层栈、path_template、双 backend、override 编排、never raise）
- **#95 (M9.7) Makefile obs-up / obs-down**：一键启停监控栈
- **#96 全量回归 133/133 全绿**（pytest）：M9.6 之后 +3 Redis backend 测试
- **#97 (M10.1) Online abstain 标定实测**：calibrate_abstain.py 加 `--no-rewrite` flag，跳过 LLM rewrite 让 Qdrant/LLM 部分不可用时仍能跑；33 题 537s 完成，**neg_blocked=100% pos_kept=90%**（BM25 fallback 模式）；EVAL_REPORT.md §8 历史趋势加 online 实测列
- **#98 (M10.2) lead_agent paper_qa 唤醒**：新增 `paper-research` subagent (`backend/packages/harness/deerflow/subagents/builtins/paper_research.py`)；paper_qa_tool docstring 加 PRIORITY TRIGGER 关键词（paper / 论文 / arxiv / doi / 学术 / 综述 / literature）+ 引用纪律强约束
- **#99 (M10.3) frontend 主侧边栏挂 paper-rag**：`workspace-nav-chat-list.tsx` 加 BookOpenIcon 入口；占位页升级为 3-tab（Inbox / Subscriptions / Ask）+ qa_sync 可交互问答 + 可订阅
- **#100 (M10.4) Prometheus 13 条 alert rules**：`alerts.yml` 含 5xx>1%/p95>5s/AbstainNoEvidenceHigh/SuspiciousCitationSpike/AutoIngestFailure/ProactiveCronStalled 等；`alertmanager/alertmanager.yml` 三 receiver（default/critical/warning）+ inhibit rules；docker-compose 挂载
- **#101 (M10.5) README 面试视角重写**：12 章 30s/5min/30min 三层；架构图 + 7 个关键决策表 + 性能基线 + 故障域 + 演示话术
- **#102 (M11.1) TokenUsageMiddleware 升级**：日志 + 4 个 Prom counter（tokens_input/output/calls/cost_usd）+ 12 模型成本估算表 + register_model_price() 注入接口
- **#103 (M11.2) LatencyTrackingMiddleware**：before/after 计时 + Prom histogram `deerflow_llm_latency_seconds{model}` + 5s warn / 30s critical 长尾告警
- **#104 (M11.3) RecursionGuardMiddleware**：与 LoopDetection 正交（按总 step 数而非重复模式），soft 30 注 wrap-up / hard 50 strip tool_calls；env 双限可调
- **#105 (M11.4) PIIScrubMiddleware**：6 类正则（APIKEY/EMAIL/CC/PHONE_CN/PHONE/PHONE_US/IP），before_model 阶段 mutate `state["messages"]`；Prom counter `deerflow_pii_redacted_total{label}`
- **#106 (M11.5) lead_agent 注册新中间件 + 16 项单测**：`tests/test_langgraph_middleware.py`，importlib + typing.override 3.10 shim 避开 deerflow runtime 依赖
- **#107 (M11) ADR-0021 LangGraph 中间件强化**：4 决策 + 4 alternative + 后续触发条件
- **#108 全量回归 149/149 全绿**（pytest）：M10.5 之后 +16 langgraph middleware 测试
- **#109 (V1) 系统性全量功能验证 + 修复**：发现并修复 12 项一致性/边界/漏测问题；`_run_tests.py` 加 test_middleware/test_langgraph_middleware（111→**159**）；文档统一 19 端点 / 21 ADR / 149/162 测试数；Makefile +`test-middleware` target 一键跑 35 项中间件测试
- **#110 (V3) 补漏 9 项边界单测**：RecursionGuard reset-after-hard / TokenUsage missing-metadata / RateLimit user_id-keying / RequestId concurrent uniqueness / Auth shared_client lifecycle / BodySize SKIP_PREFIXES / PII priority order
- **#111 (V4) `docs/VERIFICATION_REPORT.md`**：21 ADR 实现对照 / 19 端点鉴权清单 / 27 层中间件加载验证 / 5 子系统 PASS/FAIL / V1 修复清单
- **#112 全量回归 162/162 pytest + 159/159 fallback 双路全绿**

### Architecture
- proactive / feedback 共享 `feedback.sqlite` 文件（都是行为/状态元数据）
- "数字订阅强度反向"：strength=high → 阈值低（更多匹配），符合用户直觉但工程要反过来

### Tests
- 综合回归 **45/45 → 62/62**（proactive 17 项加入）
- paper_rag 总测试 **83/83 → 100/100**

### Endpoints (paper_rag router)
- M8（5）+ M10（1）+ M11（3）+ M9（9） = **18 个**

## [Unreleased] — M11 数据闭环（ADR-0017）

### Added
- **#51 feedback 包架构** `paper_rag/feedback/`：events.py（schema + 隐私脱敏） + store.py（独立 SQLite 文件 feedback.sqlite） + collector.py（统一上报 + 速率限制）
- **#52 7 类事件**：thumbs_up / thumbs_down / copy_answer / follow_up_question / abandon / abstain_followup_ingest / judge_score
- **#53 隐私脱敏**：raw `comment` 永不落库，仅存 `comment_length` + `comment_keywords`（hallucination / wrong_paper / outdated / missing_context 4 类正则命中）
- **#54 幂等 dedup_key**：sha256(user_id + trace_id + type + minute) — 双击 / 重试 / 网络抖动统一去重
- **#55 速率限制**：每用户每天 ≤ 200 events，超限 PermissionError → HTTP 429
- **#56 gateway 端点**：POST /api/paper_rag/feedback + GET /feedback/recent + GET /feedback/stats
- **#57 `scripts/collect_hard_cases.py`**：4 类规则自动收集 + jsonl 幂等追加
- **#58 `scripts/abstain_autocalibrate.py`**：merge 静态集 + hard cases → 重跑 calibrate → 漂移 > 5% 时提示 PR；**不直接改 default.yaml**，人在 loop
- **#59 11 项纯逻辑测试** `tests/test_feedback.py`

### Architecture
- feedback_events 表走独立 SQLite 文件（避免与 papers.sqlite 主库 ALTER TABLE 冲突）
- 阈值更新强制人审核（GitHub PR review），避免漂移 + 恶意 feedback 灌量

### Tests
- 综合回归 **34/34 → 45/45**（feedback 11 项加入）
- paper_rag 总测试 **72/72 → 83/83**

## [Unreleased] — M10 交付物升级（ADR-0016）

### Added
- **#42 deliver 包架构** `paper_rag/deliver/`：dispatch.py 路由 + _common.py 共享工具（PaperBundle / fetch_paper_bundle / aggregate_citations / collect_metadata）
- **#43 markdown_survey 生成器** `survey_md.py`：N 篇论文 → 200 字深读 → 跨论文综合 LLM call → Markdown 综述（Intro / Methods Comparison / Open Problems + 双层 References）；LLM 失败降级到 stitched summaries
- **#44 pptx 生成器** `pptx.py`（python-pptx）：单论文 12 张固定卡片 + 多论文 paper×3 张
- **#45 docx 生成器** `docx.py`（python-docx）：先复用 survey_md → 50 行手写 Markdown 子集解析 → emit Word 元素（Heading 1/2/3 / List Bullet/Number / Intense Quote）
- **#46 latex_bib 生成器** `latex_bib.py`：复用 M6 #31 bibtex_export + zip（references.bib + related_work.tex），synthesize 选项触发 LLM
- **#47 gateway POST /api/paper_rag/deliver** 端点：base64 + metadata（透传 abstain decisions / papers_skipped）
- **#48 LangChain `paper_deliver_tool`**：community/paper_rag 暴露 6 → **7 个 tool**
- **#49 `[deliver]` optional extra**（`pyproject.toml`）：python-pptx + python-docx 仅按需装

### Changed
- `harness/community/paper_rag/__init__.py` 导出加 paper_deliver_tool
- gateway router 6 个端点（M8 5 + M10 1）

### Tests
- `tests/test_deliver.py` 7 项纯逻辑测试全绿（4 类生成器结构验证 + dispatch 路由 + 2 边界）
- 综合回归 **34/34**（chaos 9 + abstain 13 + deliver 7 + gateway 5）；总 65 → **72** 项

## [Unreleased] — M8 服务化（接 DeerFlow gateway，ADR-0015）

### Added
- **#33 paper_rag HTTP router** `backend/app/gateway/routers/paper_rag.py`：5 端点（POST /qa 流式 SSE / POST /qa/sync / GET /papers / POST /papers/ingest / GET /wiki/{paper_id}），lazy import + run_in_executor 不阻塞事件循环
- **#34 BetterAuth 中间件** `backend/app/gateway/middleware/auth.py`：HTTP /api/auth/get-session + 60s LRU cache + bypass 列表（/health /metrics /api/auth /docs /openapi.json /redoc）+ DEERFLOW_AUTH_DISABLED dev 模式
- **#35 Prometheus /metrics 端点** `backend/app/gateway/routers/metrics.py`：直接调 paper_rag.observability.metrics.render() 吐 text format，无 prometheus_client 依赖
- **#36 user_id schema 迁移** `paper_rag/scripts/migrate_user_id.py`：Paper 模型加 user_id 字段（默认 'system'），幂等迁移
- **#37 docker-compose 扩 qdrant + paper_rag service**（`docker/docker-compose.yaml`）：volume 持久化 + healthcheck + paper_rag depends_on qdrant healthy；gateway 注入 PAPER_RAG_HOME / QDRANT_URL / PYTHONPATH 让 router 能 import paper_rag
- **#38 production.yaml** `paper_rag/config/production.yaml`：Qdrant remote / sqlite 落 /data 挂载点 / wiki self-evolution / json structured logs
- **#39 paper_rag/Dockerfile**：python:3.10-slim + 全依赖 + 可选 bge-m3 pre-warm（MODE=bake）
- **#40 backend pyproject 加 paper_rag runtime deps**：让 gateway 容器能 import paper_rag
- **#41 README quickstart 双方式**：方式 A 单机 / 方式 B 服务化（make up + curl 5 端点）

### Schema (breaking-ish)
- `Paper.user_id` 默认 'system'。已存在数据自动回填 'system'，所有用户可见。M9+ 入库走真实 user_id。

### Tests
- `tests/test_gateway_paper_rag.py` 5 项端到端测试全绿
- 总 60 → **65** 项纯逻辑测试通过

## [Unreleased] — M7 P0 abstain 三档决策（ADR-0014）

### Added
- **#32 abstain 三档决策** `paper_rag/rag/abstain.py`：纯函数模块，retrieve 后 LLM 前做 confident/weak_evidence/no_evidence 三档决策；no_evidence **直接跳过 LLM 调用**返回 canned message（解决 M6 暴露的 n03 反例 LLM 仍出 14 条 cites 问题）
- **signal_quality 分级**：rerank/dense 为 high-quality 严格判，BM25/RRF 为 low_degraded **fail-open**（避免 reranker 故障时全量误 abstain）；按 ADR-0009 graceful degrade 总原则
- `scripts/calibrate_abstain.py`：数据驱动阈值标定（ROC + target_fpr 约束 + 25-pctile 正例上界，输出推荐配置 + confusion matrix）
- `config/default.yaml` `rag.abstain` 段：enabled / threshold_low (0.20) / threshold_high (0.40) / min_chunks (3) / no_evidence_message
- `qa_agentic.answer()` trace 加 `abstain` 字段（含 decision/evidence_score/score_field/signal_quality/n_chunks）
- `qa_stream` 同步加 `abstain` 事件类型
- Metrics：`paper_rag_qa_abstain_total{decision}` + `paper_rag_qa_degraded_total{reason="abstain_low_quality_signal"}`
- `tests/test_abstain.py` 13 项纯逻辑测试 + `tests/test_chaos.py` 加 2 项端到端集成测试（no_evidence skips LLM / weak_evidence calls LLM with hint）

### Changed
- `retrieve/hybrid.py rrf_fuse()` 保留 dense cosine 为新字段 `score_dense`（之前只在 RRF 后丢失），让 abstain 拿到真实相似度
- 性能：abstain.decide() = **2.3μs/call**（10000 次 micro-bench），相对 P95=115ms 检索可忽略

### Tests
- 纯逻辑测试 **48/48 → 60/60**（+13 abstain + 2 chaos abstain 集成）

## [Unreleased] — M6 大评测 + 生产化深度（ADR-0013）

### Added
- **#23 评测集扩到 33 题（6 paper）**：含 3 反例（no-answer 题），retrieval-only paper_recall@k=0.86, mrr=0.83, fpr@k=0.00
- **#23.1 33 题端到端 LLM 评测**（Qwen3.5-plus, --no-judge, 77.8min）：paper_recall@k=**0.909**, mrr=**0.803**, fpr@k=**0.000**, cite_existence=**1.000**（零幻觉引用）, must=**1.000**, violations/errors=0/0；结果文件 `data/index/eval_runs/1779172826.json`
- **#26 性能基准** `tests/perf_bench.py` + `docs/PERFORMANCE.md`：retrieval P50=113ms, P95=115ms, qa_agentic ~225s/题
- **#27 可观测性** `paper_rag/observability/{metrics,trace}.py`：Prometheus text format counters + histograms + trace_id；qa_agentic 已埋点
- **#28 Chaos 测试** `tests/test_chaos.py` 7 项：Qdrant down / LLM 异常 / reranker 加载失败 / BM25 空索引等故障下的 graceful degrade 验证
- **#29 多轮对话** `paper_rag/rag/history.py`：SQLite qa_history 表 + LLM rewrite_with_history；`qa_agentic.answer(conversation_id=...)` 可选启用
- **#30 流式输出** `paper_rag/rag/qa_stream.py`：generator 输出 7 类事件（intent/rewrite/retrieved/reflect/answer_chunk/done/error），走 OpenAI stream=True
- **#31 BibTeX 导出** `paper_rag/tools/bibtex_export.py` + DeerFlow `export_bibtex_tool`（共 6 个 LangChain @tool）
- `scripts/ingest_arxiv_direct.py` 绕过 arxiv API 限流（直接拉 PDF + 解析 abs 页 HTML 元数据）

### Fixed
- **#25 judge_concise**: qa_agentic prompt 加 "≤200 words, dense, no padding" 约束
- bge-m3 强制 macOS Darwin → CPU 模式（避开 MPS 23GB OOM；`embedding.device='auto'`）
- DeerFlow tool docstring 多行 Args 改单行（LangChain `parse_docstring=True` 误判）

### Known issues（M7 候选 P1）
- ~~**abstain gap**：反例 n03（"上海明天天气"）retrieval recall=0 ✅，但 LLM 仍输出 14 条 cites ⚠️。需要在 qa_agentic 入口加"top-k 平均相似度 < τ → 走 no-evidence 分支"~~ **已在 M7 P0 ADR-0014 落地解决**

### 测试
- 纯逻辑 **48/48**（chunk 6 + retrieve 3 + eval 7 + wiki 3 + m5_fixes 5 + m5_p1 5 + m5_p2 5 + finalization 7 + chaos 7）

## [Unreleased] — M5 生产化 + 端到端验收（含 LLM-judge）✅

### Verified end-to-end + LLM-judge (Qwen3.5-plus, 2 papers × 5 questions × 5 LLM-judge)
- paper_recall@k = **1.00** / paper_mrr = **1.00** / fpr@k = **0.00**
- **cite_existence = 1.00**（零幻觉引用）
- **suspicious_citations = 0**
- must_contain = 1.00 / violations = 0
- **judge_faithful = 5.0** / judge_complete = 4.6 / judge_concise = 3.6
- Round 1 retrieval-only: recall=0.90, mrr=1.00, fpr=0.75
- Round 2 +LLM: recall=0.90, mrr=0.90, fpr=0.25
- Round 3 +gold_answer +judge: recall=1.00, mrr=1.00, fpr=0.00
- 渐进改进证明 P0/P1 设计真实有效

### Added
- `tests/eval/qa_set.real.jsonl` 含 gold_answer 5 题
- `docs/INTERVIEW_NOTES.md` 项目讲解硬料
- DeerFlow 适配层 docstring 修复（多行参数描述会被 LangChain 误判，改单行；Examples → Usage）
- 装上 magic-pdf（CLI 名 `magic-pdf` 而非 `mineru`，已更新 config）

### Added (验收, ADR-0012)
- **`qdrant.local_path`**：embedded 模式（无 Docker 也能跑）；`get_client` 优先级 `local_path` > `file://`/`local://` 前缀 > 远端 url
- **`PAPER_RAG_CONFIG` env**：切配置不用改源码
- **`docs/ACCEPTANCE_REPORT.md`**：真实端到端验收数据（paper_recall@k=0.90, mrr=1.00）
- **`tests/eval/qa_set.real.jsonl`**：5 题真实评测集（Self-RAG + 原始 RAG，含反例）
- **`config/local.yaml`**：embedded 模式配置模板

### Fixed (验收暴露的回归, ADR-0012)
- **#19 arxiv v4 兼容**：`Result.download_pdf` 移除 → 用 `client.download_pdf(result, ...)`，多级 fallback（含 httpx 直下）
- **#20 qdrant-client 1.18 兼容**：`client.search()` 弃用 → 用 `query_points()`；`store/qdrant_store.py` 与 `wiki/store.py` 都修
- **#21 wiki/store.py 残余 SyntaxError**：之前重构漏删的多余 `)`
- **#22 init_store.py 绕过 get_client**：改成走 `qdrant_store.get_client()`，遵循 local_path 兜底逻辑

### Added (P2, ADR-0011)
- **#13 qa_cache**：`rag/qa_cache.py` SQLite + 24h TTL；config `rag.qa_cache_enabled` 默认 false；qa_agentic 入口短路命中、出口写回
- **#14 反例评测**：`EvalItem.irrelevant_paper_ids` + `metrics.false_positive_rate` + `run_eval` 聚合 `fpr@k`
- **#15 wiki_review --limit / --stale-days / --dry-run**：按 updated_at 排序处理最旧
- **#16 工具 docstring few-shot**：5 个 LangChain tool 都加调用例（中英）
- **#17 arxiv_version**：`Paper.arxiv_version` 列；`utils/ids.split_arxiv_version`；ArxivSource 把请求版本写到 meta.extra
- **#18 章节 sanity**：`chunk/sanity.py:grade_sections` (complete/partial/minimal/broken)；ingest_pipeline 写 `parsed_with={parser}+{quality}`
- `tests/test_m5_p2.py` 5 项

### Changed (P1, ADR-0010)
- **#8 Reranker 默认开**：`reranker.enabled=true`；config 暴露 `cache_dir` / `use_fp16`；三重 graceful degrade
- **#9 BM25 paper_id 提前过滤**：`sparse_bm25.search` 增加 `paper_ids=` 入参，先打分后过滤；hybrid 不再二次过滤
- **#10 Wiki 别名补全**：create_entry prompt 加 aliases 字段；`_clean_aliases` 去重 / 去 primary / 上限 5
- **#11 Trigger 异步化**：新增 `wiki/queue.py`（daemon 线程 + Queue）；`ingest_pipeline` 改为 enqueue；`scripts/ingest_batch.py` 末尾 wait_drained
- **配置**：`retrieve.sparse_backend` 默认 `fts5`（fallback `rank_bm25`）

### Added (P1, ADR-0010)
- **#7 SQLite FTS5 后端**：`retrieve/fts5.py` lazy CREATE VIRTUAL TABLE + 3 个 sync 触发器；`_build_match_query` 处理中英符号
- **#6 Ablation 评测脚本**：`tests/eval/run_ablation_context.py` 临时双 collection 比较 context-prefix vs raw embedding
- 工程兜底：`wiki/triggers.py` / `retrieve/fts5.py` 改用局部 sqlmodel import → smoke 从 54/59 提升到 57/61
- `tests/test_m5_p1.py` 5 项新测试（FTS5 builder / alias clean × 2 / async queue × 2）

### Changed (P0, ADR-0009)
- **#1 SQLite 并发**：启用 WAL + busy_timeout=5000 + synchronous=NORMAL + foreign_keys=ON；`connect_args={"check_same_thread": False, "timeout": 5}`
- **#2 跨 source 去重**：新增 `find_existing_paper(doi, arxiv_id, title_norm)`；ingest_pipeline 命中后返回 `merged_into=<existing_id>` 而非重复入库
- **#3 citation 兜底告警**：新增 `detect_suspicious_citations` 识别 `[1]` / `(Author 2020)` 模式；`qa_simple` / `qa_agentic` 输出 `suspicious_citations` 字段；prompt 同步加强不允许这些形式
- **#4 检索失败降级**：`qdrant_store.search` 异常时返回 `[]` + `log.warning`；`qa_agentic` 在 chat 失败时不抛，返回降级答案 + `trace.degraded` 字段
- **#5 MinerU 输出适配**：识别 `<basename>/auto/` 标准布局；`images/` 复制到 `parsed_dir/figures/`；markdown 中所有 `![](images/x)` 重写为 `![](figures/x)`；`*content_list*.json` 落 `layout.json`

### Added
- 顺手做的 **#12 ingest_runs 表**：`IngestRun` 模型 + `record_ingest_step` / `finish_ingest_step`，每个 pipeline 步骤一行流水
- ADR-0009 记录上述决策与权衡
- `tests/test_m5_fixes.py` 5 项新测试（suspicious × 3 / drops_unknown / mineru_image_rewrite）

### 待办（M6 候选）
- 多用户 wiki / 私有命名空间
- 评测集自动扩展（从用户真实问题挑）
- 引用 BibTeX 导出
- 离线 LLM 替换 reflect/intent/judge

## [0.1.0-dev] — 2026-05-14

### Added
- **M0** 骨架：目录、pydantic 配置、ENV 解析、utils（ids/logger/paths）
- **M1** 单篇闭环：arxiv/local 采集；MinerU + pymupdf 兜底；section/text/multimodal 切分；bge-m3 向量化；Qdrant + SQLite 双库与状态机；qa_simple；CLI
- **M2** Agentic RAG：S2 / OpenAlex / URL 采集；BM25 + RRF + FlagReranker；意图分类 + query 改写 + HyDE + 检索后反思 + 迭代检索；`paper_section` / `paper_compare`
- **M2.5** 评测：`tests/eval/{schema,loader,metrics,judge,run_eval}` 三档运行；LLM-judge；JSON dump
- **M3** Wiki 0.1：concept_extractor / normalize / create_entry / patch_entry / triggers / consistency；`wiki_lookup` tool；`scripts/wiki_review.py`
- **M4** DeerFlow 集成：`community/paper_rag/` LangChain `@tool` 包装；`skills/custom/paper-research/SKILL.md`
- 19/19 纯逻辑测试通过；54/59 模块可导入
- 8 份 ADR + ARCHITECTURE / OPERATIONS / STATUS 文档

### Notes
- Embedding 选定为 `BAAI/bge-m3`（ADR-0003），后续不可换；换则 Qdrant `paper_chunks` 必须重建
- Wiki 默认 `enabled: false`；先把 RAG 主路径打稳再开
