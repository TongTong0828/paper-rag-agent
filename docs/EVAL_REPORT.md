# EVAL_REPORT.md — paper_rag 端到端评测

> **生成时间**：2026-05-21
> **数据集**：`tests/eval/qa_set.real.jsonl` — 33 questions (30 positives + 3 negatives)
> **范围**：M5 baseline 实测数据 + M11 离线标定 + abstain 三档分布
> **再生成**：`python scripts/calibrate_abstain.py --mode {online|offline}`

## 1. 总览

| 指标 | M5 baseline (2026-05-18) | M11 当前 (2026-05-21) | M10.1 online 实测 (2026-05-22) | Δ |
|---|---|---|---|---|
| recall@k (k=10) | 0.90 | 0.90 | — | — |
| fpr@k             | 0.75 | 0.75 | — | — |
| abstain enabled   | ❌ | ✅ | ✅ | new |
| neg_blocked_rate (no_evidence) | n/a | 100% (offline) | **100% (online)** | ✅ confirmed |
| pos_kept_rate (confident+weak) | n/a | 96.7% (offline) | **90% (online, BM25 fallback)** | new |
| 综合测试通过 / 总数 | 34 / 34 | **133 / 133** | **133/133** | +99 |
| HTTP 端点数 | 0 | **20** | **20** | +20 |

> **online 模式下 pos_kept 从 97% → 90%**：因为 Qdrant 不可达，hybrid_search 走 BM25-only fallback，dense 召回为 0。这反而暴露了一个真实场景下 abstain 的鲁棒性 — 即便 dense 挂了，三档拦截依然把所有真负例挡住，pos_kept 仅退化 7%。生产环境正常态（dense + BM25 + rerank）应回到 95%+ 区间。

## 2. abstain 三档分布（offline + online 对比）

阈值通过 `scripts/calibrate_abstain.py` 在 33 题数据集上跑出：

| 决策档 | offline 数量 | online (BM25 only) 数量 | 占比对比 |
|---|---|---|---|
| `confident` | 23 | 23 | 70% / 70% |
| `weak_evidence` | 6 | 4 | 18% / 12% |
| `no_evidence` | 4 | 6 | 12% / 18% |

> online 模式下因 Qdrant 不可达走 BM25 only，更多 borderline 正例被打到 no_evidence —— **abstain 倾向"宁可不答也不乱答"，符合工程预期**。

**关键阈值**：

| 字段 | 旧值 (M5) | offline 标定 (M11) | **online 实测 (M10.1)** | 来源 |
|---|---|---|---|---|
| `threshold_low` | 0.20 | 0.21 | **0.0238** | 真实负例 max(0.0038) + margin |
| `threshold_high` | 0.40 | 0.48 | **0.1507** | 真实正例 25th percentile |
| `min_chunks` | 3 | 3 | 3 | 不变 |

> **score 量纲差异**：online BM25-norm score 范围 `0.0003 ~ 0.99`，offline 合成 score `0.05 ~ 0.76`。这就是为什么 online 模式阈值小一个数量级 —— **不能直接迁移到 production config，必须等 Qdrant + rerank 全在线后再标一次**。当前 default.yaml 仍用 offline 0.21/0.48 作为合理上线值。

## 3. 召回质量分布（online 真实）

| 档位 | 平均 evidence_score | 样本数 | 备注 |
|---|---|---|---|
| 真负例 (3 题) | 0.0023 (range 0.0003–0.0038) | 3 | 全数被 no_evidence 拦截 |
| 真正例 - 强 | 0.30+ | 23 | 直接 confident |
| 真正例 - 中 | 0.05–0.15 | 4 | 落 weak_evidence 档 |
| 真正例 - 弱 | <0.024 | 3 | 落 no_evidence（BM25 fallback 召不到 dense 信息）|

> 真负例与真正例 score 区间相差 **2-3 个数量级**（0.0023 vs 0.30+），这是 abstain 能拦得住的关键前提。继续扩大数据集后需重新标定避免 overfit。

## 4. 已知局限

1. **数据集小**：33 题（30 正 + 3 负）属于 sanity check 规模，统计意义弱。
   建议扩到 100+ 题（含 20+ 真负例）后重跑 online 模式。
2. **offline 模式**用合成 score 分布，**仅供工程对齐** — 真实生产环境
   建议 `--mode online`（需要 Qdrant + LLM 在线）。
3. **未覆盖多论文 mix**：当前题集都是单 paper QA，未测跨文献综合（M10 deliver
   场景）。

## 5. 再生路径

```bash
# 离线（CI / dev）
python scripts/calibrate_abstain.py --mode offline \
    --qa-set tests/eval/qa_set.real.jsonl \
    --out data/index/abstain_calibration.json \
    --target-fpr 0.0

# 在线（生产校准，需要 Qdrant + LLM）
python scripts/calibrate_abstain.py --mode online \
    --qa-set tests/eval/qa_set.real.jsonl \
    --top-k 8

# Online 模式但 LLM 不可达 — 仍能跑（M10.1 实测路径）
python scripts/calibrate_abstain.py --mode online --no-rewrite \
    --qa-set tests/eval/qa_set.real.jsonl \
    --top-k 8

# 再跑 hard case 收集
python scripts/collect_hard_cases.py --out docs/HARD_CASES_REPORT.md

# 全量回归
PYTHONPATH=src:tests python scripts/_run_tests.py
```

## 6. 下一步

- [ ] 扩 QA set 到 100+ 题（含更多负例 + 多论文综合）→ 重跑 online 标定
- [ ] feedback loop：M11 hard_case → 自动加进 QA set
- [ ] 加 retrieval-only ablation：rerank on/off / RRF k=60 vs 30 / dense-only
- [ ] 加 perf 维度：每档延迟 P50/P95/P99 → 对接 PERF_BASELINE.md
- [ ] 启动 Qdrant 后用全栈跑一次 online 标定，刷新 default.yaml 阈值

## 7. 更新机制

- offline 标定可在 CI 跑（`make calibrate-abstain`），保 sanity（neg_blocked>=95%）
- online 标定走 PR review（M11.C abstain_autocalibrate.py）
- 阈值改动必须更新本文 §8 历史趋势

## 8. 历史趋势

| 日期 | 数据点 | 模式 | τ_low / τ_high | neg_blocked | pos_kept | 备注 |
|---|---|---|---|---|---|---|
| 2026-05-18 | M5 baseline | n/a | 0.20 / 0.40 | n/a | n/a | abstain 未启用，仅 recall@k=0.90 |
| 2026-05-21 | M11 offline | 合成 score | 0.21 / 0.48 | 100% | 96.7% | default.yaml 上线值 |
| **2026-05-22** | **M10.1 online** | BM25-only fallback (Qdrant down) | **0.0238 / 0.1507** | **100%** | **90%** | 真实数据，证明 abstain 在 dense 挂掉时仍鲁棒 |
| 待续 | — | online 全栈（Qdrant + LLM） | — | — | — | 等 prod 部署 |
