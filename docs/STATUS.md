# STATUS — 已完成项详单

> 严格记录每个里程碑的产出，不掺路线/愿景内容。新工作进 PLAN.md。

## M0 · 骨架 ✅

- [x] 目录布局 `src/paper_rag/{ingest,parse,chunk,embed,store,retrieve,rag,wiki,tools,utils}`
- [x] `pyproject.toml`（含可选依赖 `mineru` / `dev`）
- [x] `config/default.yaml` + `src/paper_rag/config.py`（pydantic + ENV `$VAR` 展开 + 路径解析）
- [x] `utils/{ids,logger,paths}.py`（logger 走 loguru → stdlib 兜底）
- [x] `scripts/up_qdrant.sh` + `scripts/init_store.py`
- [x] ADR 0001–0005

## M1 · 单篇闭环 MVP ✅

- [x] **A 采集器**：`ingest/{schema,sources,arxiv_source,local_source,dedup}`
- [x] **B1 解析**：`parse/{mineru_local,fallback_pymupdf,dispatcher}`
- [x] **B2 切分**：`chunk/{section_splitter,text_chunker,multimodal_chunker,contextual,builder}`
- [x] **向量化**：`embed/bge_m3` 懒加载单例
- [x] **B3 双库**：`store/{sqlite_store,qdrant_store,ingest_pipeline}` 状态机 created→fetched→parsed→chunked→embedded→indexed→done
- [x] **C 简化 RAG**：`retrieve/{dense,format}` + `rag/{citation_check,llm,qa_simple}`
- [x] CLI：`scripts/{ingest_one,ask}.py`
- [x] 工具入口：`tools/{paper_search,paper_qa,_schema}`

## M2 · Agentic RAG ✅

- [x] **多源采集**：S2 / OpenAlex / URL + `scripts/ingest_batch.py`
- [x] **检索增强**：`retrieve/{sparse_bm25,hybrid,rerank}`（dense + BM25 → RRF → reranker）
- [x] **Agentic 闭环**：`rag/{intent_classifier,query_rewrite,reflect,qa_agentic}`（意图 → 改写 → 检索 → 反思 → 迭代）
- [x] **Tools 完整化**：`tools/{paper_section,paper_compare}`；`paper_qa` 切到 agentic
- [x] ADR-0006

## M2.5 · 评测 ✅

- [x] `tests/eval/{schema,loader,metrics,judge,run_eval}.py`
- [x] 三档运行：`--retrieval-only` / `--no-judge` / 完整 judge
- [x] 输出 per-item + aggregate + JSON dump
- [x] `tests/eval/qa_set.example.jsonl` 模板
- [x] `tests/eval/README.md` 含验收线

## M3 · Wiki 0.1 ✅

- [x] schema（pydantic）/ store（sqlmodel）解耦
- [x] `concept_extractor` LLM 抽 ≤5 概念
- [x] `normalize` 三级匹配（name → alias → 语义近邻 ≥0.85）
- [x] `flow` create_entry / patch_entry（patch 不 rewrite，self_eval ≥0.7，24h 限频）
- [x] `triggers.on_paper_indexed` 串在 ingest 末尾，非阻塞失败
- [x] `consistency` heuristic 校验
- [x] `tools/wiki_lookup` + `scripts/wiki_review.py`
- [x] ADR-0007

## M4 · DeerFlow 集成 ✅

- [x] `backend/packages/harness/deerflow/community/paper_rag/{__init__,tools}.py`
- [x] LangChain `@tool` 包装 + sys.path lazy 注入（不破 harness/app boundary）
- [x] `skills/custom/paper-research/SKILL.md`（决策流 + 引用纪律）
- [x] `tools/__init__.py` 改 `__getattr__` 懒加载
- [x] ADR-0008

## M5 · 生产化（P0 完成） ✅

- [x] **#1 SQLite 并发**：`get_engine` 启用 WAL + busy_timeout=5000 + synchronous=NORMAL + foreign_keys=ON
- [x] **#2 跨 source dedup**：`find_existing_paper(doi/arxiv_id/title_norm)` + ingest_pipeline 命中返回 `merged_into`
- [x] **#3 citation 兜底告警**：`detect_suspicious_citations` 识别 `[1]` / `(Author 2020)` 模式；qa_simple/qa_agentic 加 `suspicious_citations` 字段；prompt 同步加强
- [x] **#4 检索失败降级**：`qdrant_store.search` try/except 返回 `[]`；`qa_agentic` 在 chat 失败时短路返回 `(LLM unavailable)`，trace 加 `degraded` 字段
- [x] **#5 MinerU 输出适配**：识别 `<basename>/auto/` 标准布局；图片复制到 `figures/`；markdown 路径重写；layout.json 落盘
- [x] **#12 ingest_runs 表**：每步 record_ingest_step + finish_ingest_step；流水可查
- [x] ADR-0009

## M5 · 生产化（P1 完成） ✅

- [x] **#6 ablation 评测**：`tests/eval/run_ablation_context.py` 对比 context-prefix vs raw embedding
- [x] **#7 FTS5 替代 BM25**：`retrieve/fts5.py` + 配置 `sparse_backend`；hybrid 自动选 backend；rank_bm25 兜底
- [x] **#8 Reranker 默认开**：config 暴露 cache_dir/use_fp16；三重 graceful degrade（装不上 / 加载失败 / score 异常）
- [x] **#9 BM25 paper_id 提前过滤**：sparse_bm25 与 fts5 都支持 paper_ids 入参，先打分后过滤
- [x] **#10 Wiki 别名补全**：LLM 顺手吐中英 aliases；`_clean_aliases` 去重去 primary 去短串
- [x] **#11 Trigger 异步化**：`wiki/queue.py` daemon 线程；ingest 主路径不阻塞；ingest_batch 末尾 wait_drained
- [x] 工程兜底：triggers / fts5 改用局部 import，smoke 从 54/59 → 57/61
- [x] ADR-0010

## M5 · 生产化（P2 完成） ✅

- [x] **#13** qa_cache：`rag/qa_cache.py` 24h TTL；默认关；qa_agentic 入口/出口接通
- [x] **#14** 反例评测：`irrelevant_paper_ids` + `false_positive_rate` + `run_eval` aggregate `fpr@k`
- [x] **#15** wiki_review：`--limit` / `--stale-days` / `--dry-run`
- [x] **#16** 工具 docstring：5 个 tool 加调用 few-shot（含中英）
- [x] **#17** arxiv version：`Paper.arxiv_version` 列；`split_arxiv_version`；ArxivSource 透传
- [x] **#18** 章节 sanity：`chunk/sanity.py grade_sections`；`parsed_with={parser}+{quality}`
- [x] ADR-0011

## M6 · 大评测 + 生产化深度 ✅（2026-05-19）

- [x] **#23 评测集 33 题、6 paper**：含 3 反例 no-answer；retrieval-only paper_recall@k=0.86, mrr=0.83, fpr@k=0.00
- [x] **#23.1 33 题端到端 LLM 评测（Qwen3.5-plus, --no-judge, 77.8min）**：paper_recall@k=**0.909**, mrr=**0.803**, fpr@k=**0.000**, **cite_existence=1.000**（零幻觉引用）, must=**1.000**, violations/errors=0/0
- [x] **#26 性能基准**：tests/perf_bench.py + docs/PERFORMANCE.md（retrieval P50=113ms / P95=115ms）
- [x] **#27 可观测性**：observability/{metrics,trace}.py + qa_agentic 埋点（Prometheus text format）
- [x] **#28 Chaos 测试**：7 项故障注入（Qdrant down / LLM 异常 / reranker 失败 / BM25 空 ...）全部 graceful degrade
- [x] **#29 多轮对话**：rag/history.py + qa_history 表 + rewrite_with_history
- [x] **#30 流式输出**：rag/qa_stream.py 7 类事件
- [x] **#31 BibTeX 导出**：tools/bibtex_export.py + DeerFlow export_bibtex_tool（共 6 个 @tool）
- [x] **#25 judge_concise prompt 改进**：≤200 words 约束
- [x] 工程修复：bge-m3 macOS CPU fallback / arxiv 直拉 PDF / DeerFlow docstring 单行
- [x] ADR-0013

### M7 候选 P1（M6 大评测暴露的真实问题）
- [x] **#32 abstain 阈值策略 ✅**（ADR-0014 已落地）：三档决策 confident/weak_evidence/no_evidence；signal_quality 区分 high/low_degraded（rerank/dense vs BM25/RRF），低质量信号 fail-open；新增 `paper_rag/rag/abstain.py` + `scripts/calibrate_abstain.py` + 13 项纯逻辑测试 + 2 项 chaos 集成测试。决策延迟 2.3μs/call

## M8 · 服务化（接 DeerFlow gateway）✅（2026-05-20）

ADR-0015 完整落地：从"脚本"升级到"DeerFlow 用户能直接用的产品能力"。

- [x] **#33 paper_rag router**：5 个 HTTP 端点（qa stream/sync, papers list/ingest, wiki get），lazy import + run_in_executor 不阻塞事件循环 ─ `backend/app/gateway/routers/paper_rag.py`
- [x] **#34 BetterAuth 中间件**：HTTP /get-session + 60s LRU 缓存 + bypass 列表 + dev 模式开关 ─ `backend/app/gateway/middleware/auth.py`
- [x] **#35 Prometheus /metrics 端点**：直接调 `paper_rag.observability.metrics.render()` 吐 Prometheus text format，无 prometheus_client 依赖
- [x] **#36 user_id schema 迁移**：Paper 模型加 user_id 字段（默认 'system'），幂等迁移脚本 `scripts/migrate_user_id.py`，已对 6 篇现有 paper 回填
- [x] **#37 docker-compose 扩 qdrant + paper_rag service**：volume 持久化 + healthcheck + paper_rag depends_on qdrant healthy
- [x] **#38 production.yaml**：Qdrant remote / sqlite 落 /data 挂载点 / abstain enabled / wiki self-evolution / json structured logs
- [x] **#39 paper_rag/Dockerfile**：python:3.10-slim 基础 + 全依赖装好 + 可选 bge-m3 pre-warm（MODE=bake 生产构建）— **M9.5 升级**：多阶段构建（builder + runtime venv copy）、non-root user `paperrag`、`tini` PID1、`docker-entrypoint.sh` 4 模式（idle/cli/proactive/jupyter）、新增 `.dockerignore`（屏蔽 data/ models/ tests/，构建上下文从 ~3GB 减到 ~5MB）、APT_MIRROR + PIP_INDEX_URL 与 backend 对齐、`EXTRAS=deliver,deerflow,proactive` 按需扩展
- [x] **#40 backend pyproject 加 paper_rag runtime deps**：让 gateway 容器能 import paper_rag
- [x] **#41 README quickstart 双方式**：方式 A 单机 / 方式 B 服务化（make up + curl 5 端点）

### M8 关键工程发现
- SQLModel 默认表名是 **小写单数**（paper/chunk/section），不是直觉的复数。Router 用 `sqlite_master` 反射 schema 适配
- gateway 容器（Python 3.12）import paper_rag（兼容 3.10+）走 sibling 包 + PYTHONPATH 注入，避免双 venv

### M8 测试
- 5 项 router 集成测试全绿（路由注册 / metrics bypass / openapi bypass / 401 拒绝 / dev 模式 list papers）
- docker-compose YAML 合法性自动校验
- 真实读到 6 篇 arxiv 论文 + 各自 chunk 数

### M8 待续（M9+）
- abstain config 端点（PATCH /api/paper_rag/abstain/config）
- 软删 paper（DELETE）
- 实跑流式 LLM 端到端（需要 Docker 启完整栈）

## M10 · 交付物升级 ✅（2026-05-21）

ADR-0016 完整落地：从"答案文本"升级到"可直接交付的 4 类工业格式"。

- [x] **#42 deliver 包**：`paper_rag/deliver/{__init__,dispatch,_common}.py` 三件套
  - `dispatch(format, paper_ids, ...)` 路由器，4 类格式分发
  - `_common.py` 共享工具：`fetch_paper_bundle` / `aggregate_citations` / `collect_metadata`
- [x] **#43 markdown_survey 生成器**：`survey_md.py` — N 篇论文 → 200 字深读 → 跨论文综合 → Markdown 综述（Intro / Methods Comparison / Open Problems + Citation Map）；LLM 失败时降级为 stitched summaries
- [x] **#44 pptx 生成器**：`pptx.py` — python-pptx 12 张固定卡片（单论文模式），多论文模式按 paper × 3 张展开；纯模板，无图无主题
- [x] **#45 docx 生成器**：`docx.py` — 二步流水线（先复用 survey_md → 解析 Markdown 子集 → emit Word 元素）；保留 Heading 1/2/3 + List Bullet/Number + Intense Quote 样式
- [x] **#46 latex_bib 生成器**：`latex_bib.py` — 复用 M6 #31 `bibtex_export` + 输出 zip（references.bib + related_work.tex），可选 `synthesize=True` 触发 LLM 起稿
- [x] **#47 gateway router /deliver 端点**：`POST /api/paper_rag/deliver` 返回 base64 + metadata
- [x] **#48 LangChain `paper_deliver_tool`**：双形态共存，lead_agent 可调；community/paper_rag 暴露到 7 个 tool（原 6 + paper_deliver）
- [x] **#49 `[deliver]` optional extra**：`pip install -e .[deliver]` 才装 python-pptx + python-docx，保持核心包瘦
- [x] **#50 7 项纯逻辑测试**：`tests/test_deliver.py` 覆盖 4 类格式生成 + dispatch 路由 + 边界（unknown format / empty paper_ids）

### M10 关键工程发现
- **PPT/Word/zip 三种 Office 格式都基于 zip 容器** — 单测不依赖外部工具，直接 `zipfile.ZipFile + namelist` 验证结构是工业级实现
- **Markdown → Word 不需要 markdown-it/html2docx 这些重依赖** — 我们的 Markdown 子集只有 4 种节点（heading/quote/list/paragraph），50 行手写解析比第三方库可靠
- **abstain 决策必须透传到 deliver metadata** — 用户要看到"哪些 paper 没参与生成"，这是诚实软件的表现

### M10 测试
- 7 项 deliver 测试全绿（survey 结构 + pptx zip 合法 + docx zip 合法 + zip 双文件 + dispatch 拒绝坏格式 + 拒绝空 paper_ids + dispatch 路由）
- 综合回归 **34/34**（chaos 9 + abstain 13 + deliver 7 + gateway router 5）

## M11 · 数据闭环 ✅（2026-05-21）

ADR-0017 完整骨架落地：从"静态评测"升级到"用户行为反哺"。

- [x] **#51 feedback 包架构** `paper_rag/feedback/`：events.py（schema + 隐私脱敏） + store.py（SQLite events 表，分库设计） + collector.py（统一上报入口 + 速率限制）
- [x] **#52 7 类事件**：thumbs_up / thumbs_down / copy_answer / follow_up_question / abandon / abstain_followup_ingest / judge_score
- [x] **#53 隐私设计**：raw `comment` 永不落库，仅存 `comment_length` + `comment_keywords` 类别命中
- [x] **#54 幂等写入**：(user_id, trace_id, event_type, minute_bucket) 去重哈希
- [x] **#55 速率限制**：每用户每天 ≤ 200 events，超限 PermissionError → HTTP 429
- [x] **#56 gateway 端点**：POST /feedback / GET /feedback/recent / GET /feedback/stats
- [x] **#57 hard case 自动收集** `scripts/collect_hard_cases.py`：4 类规则（thumbs_down hallucination/irrelevant、≥2 follow-up 5 分钟内、judge faithful<4、abstain_followup_ingest）+ jsonl 幂等追加
- [x] **#58 abstain 自适应** `scripts/abstain_autocalibrate.py`：merge 静态集 + hard cases → 重跑 calibrate → 输出阈值候选 + 漂移 > 5% 时提示 PR；**不直接改 default.yaml**，人在 loop
- [x] **#59 11 项纯逻辑测试** `tests/test_feedback.py`：schema 校验 + 隐私脱敏 + judge 范围 + 幂等 + 用户隔离 + 聚合 + 速率限制 + 4 类 hard case 规则 + 跨规则去重

### M11 关键工程发现
- **feedback 表分库**（独立 `feedback.sqlite`）— schema 演化频繁，混进主库会污染 papers 表的 ALTER TABLE 安全
- **隐私优先**：raw comment 是负担不是资产，只提取信号（length + keyword 类别）
- **人在 loop 是工业级数据闭环的红线**：不允许系统自己改阈值，避免漂移 + 恶意 feedback + 不可解释性
- **dedup_key = sha256(user_id + trace_id + type + minute)** 是分布式幂等的最简实现

### M11 测试
- 11 项 feedback 测试全绿（schema 4 + store 4 + collector 4 - 重叠 1）
- 综合回归 **45/45**：chaos 9 + abstain 13 + deliver 7 + gateway 5 + feedback 11

### M11 待续（W2-W3）
- 阈值自适应 cron 接 GitHub Action（每周日跑）
- 异常 alert：fpr 退化 > 10% 自动 revert PR
- frontend 埋点（点踩按钮 / 复制监听 / abandon 计时）— 需要前端工程师介入
- 90 天 retention 任务（`scripts/feedback_retention.py`）

## M9 · 主动 Agent ✅（2026-05-21）

ADR-0018 完整骨架落地：从"被动答"升级到"主动给"。

- [x] **#60 proactive 包架构** `paper_rag/proactive/`：subscriptions / inbox / paper_access / matcher / digest / stale / auto_ingest_hook 7 个模块
- [x] **#61 subscriptions 表 + 三档 strength**：low / normal / high → 0.75 / 0.65 / 0.55 sim 阈值；用户隔离 + 跨用户安全
- [x] **#62 inbox 表 + 4 类卡片**：daily_digest / sub_match / stale_paper / auto_ingest；read/dismiss 软删 + unread_count
- [x] **#63 paper_access 表**：(user_id, paper_id) 复合主键 + last_accessed_at + access_count；touch_many 批量接口
- [x] **#64 matcher 模块**：bge-m3 embedding + cosine + 三档阈值 + 跳过 ingester；纯 Python cosine 兼容 stub 测试
- [x] **#65 digest 模块**：每日 arxiv 简报（小模型 50 字 TL;DR + 跨用户共享缓存）+ Markdown 卡片渲染
- [x] **#66 stale 模块**：30 天未访问 paper → 复习卡片
- [x] **#67 auto_ingest_hook**：3 种 arxiv URL 格式检测 + 异步背景 ingest + 成功/失败 inbox 卡片
- [x] **#68 gateway 9 个新端点**：subscriptions CRUD（4）+ inbox（3）+ proactive triggers（2）
- [x] **#69 17 项纯逻辑测试**：subscriptions 4 + inbox 3 + paper_access 3 + matcher 2 + auto_ingest 3 + digest 2

### M9 关键工程发现
- **proactive / feedback 共享同一个 SQLite 文件** — 都是行为/状态元数据，分文件没价值；表名空间隔离已够
- **数字订阅强度（threshold）反向**：strength=high → 阈值更低（更多匹配），strength=low → 阈值更高（更精准）。用户直觉对 "high = 多" 是对的，工程上要反过来
- **TL;DR 跨用户缓存**：100 用户订阅同关键词 → 同一篇 paper TL;DR 只调一次小模型，成本省 100x
- **auto_ingest 走 asyncio.create_task**：不阻塞 QA 答复，10 秒后 inbox 通知，体验自然
- **proactive 数据库 schema 演化**：放在 feedback.sqlite 里随 ALTER 走，主库 papers.sqlite 永远稳定

### M9 测试
- 17 项 proactive 测试全绿
- 综合回归 **162/162 pytest** | **159/159 zero-deps fallback**

### V1-V4 系统性验证（ADR-0021 完整闭环）
- ✅ V1 全栈验证：21 ADR / 19 端点 / 27 层中间件 / 5 子系统全 PASS
- ✅ V2 修复 12 项一致性问题（_run_tests.py、文档数字、Makefile target）
- ✅ V3 补 9 项边界单测（RecursionGuard reset / TokenUsage missing / Auth lifecycle / PII priority 等）
- ✅ V4 `docs/VERIFICATION_REPORT.md` 完整产出

### M11 LangGraph 中间件强化（ADR-0021）
- ✅ TokenUsageMiddleware：4 Prom counter + 12 模型成本估算
- ✅ LatencyTrackingMiddleware：histogram + 5s/30s 长尾告警
- ✅ RecursionGuardMiddleware：soft 30 / hard 50 step 限制（与 LoopDetection 正交）
- ✅ PIIScrubMiddleware：6 类 regex redact + Prom counter
- ✅ 16 项单测（typing.override 3.10 shim）

### M9.6 DeerFlow Gateway 中间件栈（ADR-0020 一部分）
- ✅ auth.py 三处优化（cookie token regex / OrderedDict LRU / shutdown close）
- ✅ observability: RequestId / AccessLog / Prometheus（path_template 防 cardinality 爆）
- ✅ protection: BodySizeLimit / Timeout（SSE bypass）/ RateLimit / GZip
- ✅ 16 项中间件单测

### M9.7 监控栈（ADR-0020 完整版）
- ✅ Grafana dashboard JSON（13 panel）含 QPS/P95/5xx/abstain/proactive
- ✅ RateLimit Redis backend（Lua 滑动窗口 + fail-open 自动降级到内存）
- ✅ docker-compose observability override（Prometheus 2.55 + Grafana 11.3）
- ✅ Prometheus 拉 gateway:8001 + qdrant:6333，15d retention
- ✅ make obs-up / obs-down 一键启停

### M9 待续（M9.5）
- ✅ **APScheduler cron_runner 落地**（`paper_rag.proactive.cron_runner`）：BlockingScheduler + SIGTERM graceful shutdown，daily_digest @ 08:00 + stale_scan @ Mon 09:00，cron 表达式 + 时区可 env 覆盖
- ✅ **paper_rag Dockerfile M9.5 升级**：多阶段构建 + entrypoint 4 模式（idle / cli / proactive / jupyter）+ non-root + `.dockerignore`
- ✅ **paper_qa_tool 自动 touch paper_access**（P0-1）：qa_sync + qa_stream 都接，stale_scan 终于有真实数据
- ✅ **docker-compose 加 paper_rag_proactive sidecar**（P0-2）
- ✅ **9 个 proactive 端点 user_id 隔离全验证**（P0-3）
- ✅ **abstain 阈值数据驱动标定**（P1-4）：default.yaml 0.20/0.40 → **0.21/0.48**，neg_blocked=100% pos_kept=97%
- ✅ **4 个 proactive Prometheus counter**（P1-5）：digest / stale / auto_ingest / sub_match
- ✅ **EVAL_REPORT.md + HARD_CASES_REPORT.md**（P1-6/7）
- ✅ **pytest 迁移**（P2-8）：[tool.pytest.ini_options] + conftest.py，两条 runner 并存
- ✅ **Makefile docker 目标**（P2-9）：docker-build / docker-up-proactive / docker-cli / docker-shell
- ✅ **GitHub Actions CI**（P2-10）：`.github/workflows/paper_rag.yml` lint + test + docker build smoke
- ✅ **ADR-0019 双 SQLite 数据库**（P2-11）
- ✅ **SSE inbox 推送**（P3-12）：GET /api/paper_rag/inbox/stream 长轮询
- ✅ **webhook 4 通道**（P3-13）：DingTalk / Feishu / WeCom / Email + HMAC + 3s timeout
- ✅ **前端 paper_rag 占位页**（P3-14）：`workspace/paper-rag/page.tsx`
- ✅ **PDF deliver**（P3-15）：reportlab + 纯 Python fallback PDF 1.4
- ✅ **SYSTEM_DESIGN.md 1-pager**（P3-16）
- ✅ **PERF_BASELINE.md**（P3-17）：pytest 3.01s / qa_agentic 冷启动 316ms
- ✅ **3 张 mermaid 时序图**（P3-18）：abstain / proactive / feedback
- 待续：APScheduler 接进 gateway lifespan / 文件锁防多 worker 重复跑
- 待续：扩 QA set 到 100+ 题 + online 模式重新标定

## M5 · 端到端验收 ✅（2026-05-18）

- [x] 真实环境（embedded Qdrant + pymupdf fallback）跑通
- [x] 2 篇 arxiv 论文 ingest done
- [x] 修复 4 个真实回归（arxiv v4 / qdrant-client 1.18 / wiki/store SyntaxError / init_store 绕过 get_client）
- [x] 新增 `qdrant.local_path` 配置 + `PAPER_RAG_CONFIG` env
- [x] **Qwen3.5-plus 完整链路 + LLM-judge 5 题评测**（含 gold_answer）：
  - paper_recall@k = **1.00**, mrr = **1.00**
  - **cite_existence = 1.00**（无幻觉引用）
  - **suspicious_citations = 0**
  - **fpr@k = 0.00**（之前 0.75→0.25→0，证明改进真实）
  - must_contain = 1.00
  - **judge_faithful = 5.0**, judge_complete = 4.6, judge_concise = 3.6
- [x] DeerFlow 工具适配层：5 个 LangChain @tool 注册成功，docstring 解析正确
- [x] ADR-0012 + ACCEPTANCE_REPORT.md + INTERVIEW_NOTES.md
- [ ] reranker 启用（节省 1.2GB 下载，按需开）
- [ ] MinerU 真实跑通（需下 ~3GB 模型，留待干净环境）
- [ ] 库扩到 5+ 篇（arxiv 限流频繁，建议手动下 PDF 用 --pdf 模式）

## 测试覆盖

- 纯逻辑测试 **100/100 通过**（M9 加 17 项 proactive 测试）
  - chunk: 6
  - retrieve: 3
  - eval: 7
  - wiki: 3
  - m5_fixes: 5
  - m5_p1: 5（FTS5 builder / alias clean × 2 / async queue × 2）
  - m5_p2: 5（split_arxiv_version / grade_sections × 2 / fpr / qa_cache_key）
  - finalization: 7（BibTeX × 2 / metrics × 3 / trace_id / streaming events）
  - chaos: 9（Qdrant down / LLM 异常 × 3 / reranker fail / bm25 empty / citation strict / **abstain no_evidence skips LLM** / **abstain weak_evidence with hint**）
  - **abstain: 13**（决策矩阵全覆盖：no_chunks / no_evidence / weak / confident / disabled / missing field / RRF normalization / score 字段优先级 / min_chunks / top_score / signal_quality high vs low_degraded × 2）
  - **gateway_paper_rag: 5**（M8 router 注册 / metrics bypass / openapi bypass / 401 拒绝 / dev 模式 list papers）
  - **deliver: 7**（M10 四类格式生成 + dispatch 路由 + 拒绝坏格式 + 拒绝空 paper_ids）
  - **feedback: 11**（M11 schema 校验 + 隐私脱敏 + judge 范围 + 幂等 + 用户隔离 + 聚合 + 速率限制 + 4 类 hard case 规则 + 跨规则去重）
  - **proactive: 17**（M9 subscriptions CRUD + 用户隔离 + inbox 增删读 + paper_access stale 检测 + matcher cosine + 阈值分级 + arxiv URL 解析 + auto_ingest 成功/失败卡 + digest 渲染）
- 全包 `pkgutil.walk` **63/63 可导入**（含新 observability 包）

## 文档

- `README.md` 项目门面
- `PLAN.md` 唯一规划文件
- `docs/ARCHITECTURE.md` 架构与数据流
- `docs/STATUS.md`（本文件）
- `docs/OPERATIONS.md` 运维手册
- `docs/adrs/0001..0009-*.md` 9 份 ADR
- `tests/eval/README.md` 评测说明
