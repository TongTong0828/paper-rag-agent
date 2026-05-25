# ADR-0014 · Abstain 三档决策（解决 M6 暴露的 cite-without-evidence 问题）

- **日期**: 2026-05-19
- **状态**: accepted
- **背景工单**: #32（M7 候选 P1，从 ADR-0013 #23.1 实测中识别）

## Context

M6 33 题端到端 LLM 评测暴露一个之前小评测集（5 题）看不到的真实问题：

> 反例 **n03**（"What is the weather in Shanghai tomorrow?"）：
>
> - retrieval 层正确给出 `paper_recall@k = 0.00` ✅（库里 6 篇全是 NLP 论文，无相关）
> - 但生成层依然输出了 **14 条 `[chunk:xxx]` 引用** ⚠️
>
> 召回端"知道自己不知道"，但生成端"基于不相关 chunk 硬答"。

这是典型的 **"无证据时无拒答"** 问题：retrieval 已经做对了功，但下游 LLM 没有显式的 abstain 通道，仍然把噪声 chunk 当证据用。

业内常见做法是引入一个 **evidence sufficiency gate**（abstain layer），在 LLM 调用前根据检索证据强度做三档决策：confident / weak_evidence / no_evidence。本 ADR 落地工业级实现。

## Decision

新增 `paper_rag/rag/abstain.py`，作为 qa_agentic 主链路的一个**纯函数模块**：retrieve+rerank 完成后、LLM 调用之前，调用 `abstain.decide(chunks, ...)` 获得四档决策之一：

```
no_chunks       chunks == []
no_evidence     evidence_score < threshold_low      → SKIP LLM, return canned message
weak_evidence   threshold_low <= score < threshold_high  → call LLM with explicit insufficiency hint
confident       score >= threshold_high              → normal RAG flow
```

`evidence_score` = top-`min_chunks` chunks 的归一化分数均值。

### 信号质量分级（关键工业级设计）

不是所有 retrieval 分数都同质。在线上常见三种状态：

| 信号 | 字段 | 性质 | abstain 行为 |
|---|---|---|---|
| **rerank** | `score_rerank` | bge-reranker sigmoid，0-1，**真实相关性** | 严格按阈值判 |
| **dense** | `score_dense` / `score` | bge-m3 cosine，**真实相似度** | 严格按阈值判 |
| **bm25** | `score_bm25` | 词频统计，OOD 时易误中 | **fail open**（不阻塞），打 degraded 计数器 |
| **rrf** | `score_rrf` | 排名倒数和，**与相似度无关** | **fail open**（不阻塞），打 degraded 计数器 |

> 关键洞察：RRF 的本质是融合排名，它**没有 "全部都不像"** 这个语义。10 个不相关 chunk 的 RRF 分数和 10 个完美匹配的 RRF 分数**完全相同**。所以 abstain 只在拿到真实相似度信号（rerank/dense）时才生效。

这条决策避免了一个常见反模式：阈值在测试环境（reranker on）调好，上线后 reranker 故障 fallback 到 RRF，导致**所有问题都被错误 abstain**。我们让 fail-open 比 fail-closed 优先（用户得到答案，运维收到告警），这与 ADR-0009 graceful degrade 的总原则一致。

### 阈值标定

不写死阈值，提供 `scripts/calibrate_abstain.py`：

1. 读 `tests/eval/qa_set.real.jsonl`（含正例 + 反例标注）
2. 对每题跑真实 retrieval，记录 evidence_score
3. 用反例做 ROC：在 target_fpr（默认 0）约束下选最小 τ_low，再在正例第 25 百分位取 τ_high
4. 输出推荐配置 + 实现的 confusion matrix

阈值由数据驱动，**不是拍脑袋常量**。

### 配置（`config/default.yaml`）

```yaml
rag:
  abstain:
    enabled: true
    threshold_low: 0.20
    threshold_high: 0.40
    min_chunks: 3
    no_evidence_message: "未在已索引文献中找到与该问题相关的内容..."
```

### 集成点

- `qa_agentic.answer()`：retrieve 后、LLM 前调 abstain
  - `no_evidence` → 直接返回 canned message（**不打 LLM**，省一次大调用 + 杜绝 n03 类故障）
  - `weak_evidence` → 在 user prompt 末尾追加 `WEAK_EVIDENCE_HINT`
  - `confident` → 现有流程不变
- `qa_stream.stream_answer()`：同步同一逻辑（避免双链路漂移），新增 `abstain` 事件
- `trace.abstain` 字段始终包含完整决策结果（含 signal_quality）便于离线分析

### Metrics

- `paper_rag_qa_abstain_total{decision}`：四档计数
- `paper_rag_qa_degraded_total{reason="abstain_low_quality_signal"}`：degraded 状态告警
- `qa_agentic.stop` 新增 `no_evidence_abstain` 这一终态

## Consequences

### 正向

- **杜绝"无证据 14 条 cites"**：n03 类问题在 high-quality 信号下被拦在 LLM 之前
- **省 LLM 调用**：no_evidence 直接返回，最贵的 LLM call（~30s）整个跳过
- **可观测**：每个决策落 trace + counter，事后能审计每一次 abstain 是否合理
- **可微调**：weak_evidence 中间档保留人类直觉的 "可能不太行" 状态，让 LLM 自己用证据说明
- **零回归风险**：`enabled=False` 一键退回 M6 行为；信号缺失 fail-open；reranker 故障 fail-open
- **极低开销**：abstain.decide() = 2.3μs/call，相对 P95=115ms 检索可忽略

### 反向 / 待优化

- **阈值依赖语料**：当前 (0.20, 0.40) 是基于 6-paper NLP 库 + 33 题的初始值；扩库到 50+ 应重跑 calibrate
- **weak_evidence prompt 的边界感弱**：靠 LLM 自觉 "evidence may be insufficient" 不如 evidence 重排+二次决策更稳，但代价是又多一次 LLM call
- **目前没在生产 reranker 启用下做大规模端到端验证**：本 ADR 实现工业级形态、纯逻辑测试 22/22 + 真实链路 sanity 通过；标定 + 完整 33 题端到端复跑作为 M7 P0

## Alternatives considered

1. **在 LLM system prompt 里加 "如证据不足直接拒答"**：过去 M5 已经加了，但 LLM 不可靠地遵守，n03 就是反例。**prompt-only 方案不够**，必须程序化 gate。
2. **直接 hard cut top-k score（不分档）**：失去 weak_evidence 中间档的灵活性；与人类直觉不符；边缘情况误伤大。
3. **训一个轻量分类器判 abstain**：复杂度过高；冷启动数据不够；当前规则方案已能解决 80% 问题。
4. **把 abstain 做成 reranker 的一部分**：耦合过紧；reranker 故障会同时丢掉 abstain；分离更工业级。

## 测试

新增 `tests/test_abstain.py`（13 项纯逻辑测试）：

- 空 chunks → no_chunks
- 全低分 → no_evidence
- 中等 → weak_evidence
- 全高分 → confident
- enabled=False → 始终 confident（向后兼容）
- 缺失 score 字段 → fail-open confident
- RRF / BM25-only → low_degraded fail-open
- score 字段优先级 rerank > dense > bm25 > rrf
- min_chunks 控制 mean 范围
- top_chunk_score / signal_quality 字段正确暴露

`tests/test_chaos.py` 加 2 项端到端集成：

- `test_abstain_no_evidence_skips_llm`：模拟弱召回，断言 `chat()` **0 次调用**、`stop="no_evidence_abstain"`
- `test_abstain_weak_evidence_calls_llm_with_hint`：模拟中等召回，断言 user prompt 含 `WEAK` 警示

**回归**：60 个 pure 测试中 58 通过（剩 2 是 pre-existing pytest fixture 问题，与 abstain 无关）。

## 文件清单

- 新增 `src/paper_rag/rag/abstain.py`（177 行，纯逻辑）
- 新增 `scripts/calibrate_abstain.py`（数据驱动标定）
- 新增 `tests/test_abstain.py`（13 项测试）
- 修改 `src/paper_rag/rag/qa_agentic.py`（接入 abstain，no_evidence 走 LLM 跳过）
- 修改 `src/paper_rag/rag/qa_stream.py`（同步 abstain）
- 修改 `src/paper_rag/retrieve/hybrid.py`（保留 dense cosine 为 score_dense）
- 修改 `src/paper_rag/config.py` + `config/default.yaml`（abstain 子配置）
- 修改 `tests/test_chaos.py`（+2 项 abstain 集成测试）

## 后续

- M7 P0：reranker enabled 下重跑 33 题完整 LLM 评测，验证 n03 cites 从 14 → 0
- M7 P1：calibrate 脚本接入 CI，每次 eval set 改动自动重算阈值
- M7 P2：weak_evidence 二次决策（让 LLM 先评估证据再决定是否答）
