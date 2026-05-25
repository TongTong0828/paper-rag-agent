# ADR-0016 · M10 交付物升级（综述 / PPT / Word / LaTeX bib）

- **日期**: 2026-05-20
- **状态**: accepted
- **关联 PRD**: `docs/M10_PRD.md`

## Context

paper_rag 现状（M0–M8）的产品形态停在"段落答案"——研究者拿到 cites 但还要手动整理。真实学术工作流的下游是：综述初稿、reading group PPT、导师交差 Word、LaTeX bib。M10 把这条"答案 → 可交付物"的最后一公里补上。

## Decisions

### 决策 1：交付物分 4 类（暂时），不做大而全
- `markdown_survey`、`pptx`、`docx`、`latex_bib`
- 不做 PDF（用户用 pandoc 自己转）；不做交互式 HTML 报告（M12 再说）

### 决策 2：每个生成器是独立模块（单一职责）
`paper_rag/deliver/{survey_md, pptx, docx, latex_bib}.py`，互不依赖。`deliver_dispatch(format, ...)` 是路由层。

### 决策 3：综述生成器 = qa_agentic per-paper + 一次跨 paper synthesis
- N 篇 paper → N 次 `qa_agentic.answer("Summarize paper {pid}")`（可缓存）
- 1 次 synthesis LLM call 把 N 个 summary 揉成 outline
- 每章再 1 次 LLM call 渲染段落
- **总成本 = N + 1 + sections 次 LLM 调用**（远低于"一次性给 LLM 所有 chunks"）

### 决策 4：cite 一致性—— deliver 输出复用 qa_agentic 的 `[chunk:xxx]` 格式
不发明新的引用格式。Markdown / Word / LaTeX 都保留 `[chunk:xxx]`，最后在 References 段把 chunk_id → paper title/arxiv 做一次映射。**保证答案在所有产物里语义一致**。

### 决策 5：python-pptx / python-docx 走 optional `deliver` extra
不进 paper_rag 默认 deps（很多用户只要 RAG 不要交付物）。`pip install -e .[deliver]`。

### 决策 6：HTTP + LangChain Tool 双形态共存（与 M8 一致）
新增 `POST /api/paper_rag/deliver` + `paper_deliver_tool`，underlying 共享 `paper_rag.deliver.dispatch`。

### 决策 7：abstain 信号透传到交付物 metadata
某 paper retrieve 失败 → 不打 LLM、不出现在最终产物，但 metadata 里列出"以下 paper 未参与生成"。**用户要看到系统的诚实**。

## Consequences

### Positive
- 立即拉高产品感：用户可以演示 "ingest → 30 秒拿到综述初稿" 这种 wow moment
- 复用既有能力：BibTeX / qa_agentic / abstain 全部复用
- 与 M8 的 HTTP + Tool 双形态架构 100% 一致

### Negative / Trade-offs
- 多 N+1+S 次 LLM 调用，单次综述生成 ~30-60s（用户能接受）
- python-pptx / python-docx 依赖增加包体积（optional 化对冲）
- 模板风格暂时单一（学术风），M11 加用户偏好可改

## Alternatives considered

1. **一次性把所有 chunks 灌给 LLM 让它直接输出综述**：拒。8K token 限制 + 引用纪律失控 + 一次失败全废。
2. **用户自己拼 N 次 paper_qa 调用**：拒。用户体验糟，竞争力差。
3. **接 pandoc 做格式转换**：拒。增加二进制依赖，python-pptx/docx 已经够用。
4. **生成 PDF**：拒。字体 / 渲染 / 页码全是坑，用户用 pandoc 一行命令搞定。

## 验收 (DoD)
见 PRD § 9。

## 后续 ADR
- **ADR-0017（M9 主动 Agent）**：cron / 订阅 / 会议关联
- **ADR-0018（M11 数据闭环）**：行为埋点 / abstain 自适应 / 模板个性化
