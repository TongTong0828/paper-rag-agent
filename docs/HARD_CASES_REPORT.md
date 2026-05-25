# HARD_CASES_REPORT.md — paper_rag M11 反馈数据闭环

> **生成时间**：2026-05-21
> **数据源**：`feedback.sqlite` via `scripts/collect_hard_cases.py --since 365d`
> **再生成**：见文末

## 1. 当前状态

| 维度 | 数值 |
|---|---|
| feedback_events 总数 (since 365d) | **0** |
| 检测到的 hard cases | **0** |
| 累计 hard_cases.jsonl 条数 | 0 |
| 评估集 qa_set.real.jsonl 条数 | 33 |

**结论**：脚本流程跑通（无报错、能写文件、dedup 检查工作正常），但**还没
有真实生产反馈进入数据闭环**——这是预期的：M11 端点 2026-05-21 才上线，
还没有用户在 inbox / qa 上提交 thumbs_down 等事件。

## 2. Hard case 触发规则（M11 / ADR-0017 §7）

脚本目前实现 4 类规则：

| # | 触发条件 | 含义 | 优先级 |
|---|---|---|---|
| 1 | `thumbs_down` + reason ∈ {hallucination, irrelevant} | 最强负反馈 | P0 |
| 2 | ≥ 2 个 `follow_up_question` (5 min 内同 conversation) | 用户反复追问 = 答案不到位 | P1 |
| 3 | `judge_score` faithful<4 OR complete<3 | LLM judge 标差 | P1 |
| 4 | `abstain_followup_ingest` | 系统漏召论文，用户手动补 | P0 |

## 3. 期望使用方式

```bash
# 1. 让一些用户用 paper_rag QA + 点反馈（thumbs_up/down）
# 2. 每周 cron 跑：
python scripts/collect_hard_cases.py --since 7d \
    --out tests/eval/hard_cases.jsonl

# 3. hard cases 进 eval set：
cat tests/eval/hard_cases.jsonl >> tests/eval/qa_set.real.jsonl

# 4. 重跑标定 + 评测：
python scripts/calibrate_abstain.py --mode online --qa-set tests/eval/qa_set.real.jsonl
python scripts/eval_run.py
```

## 4. 关键工程发现（脚本运行）

- ✅ 脚本启动 0 报错（schema 自动 init OK）
- ✅ `--since` 参数解析（`365d` → epoch 计算正确）
- ✅ 空数据集时输出 0/0/0 而不是崩溃
- ✅ dedup 用现有 hard_cases.jsonl 的 question 做集合，避免重复入库
- ⚠️ 数据闭环本身工作但缺**输入** — 需要人工用 paper_rag 几天产生有效反馈

## 5. 下一步

- [ ] dogfood：自己用 paper_rag 答 20 个真实问题，每条点反馈
- [ ] 加 `--since-events` flag：按事件数限流（避免单次 cron 处理太多）
- [ ] 集成进 cron_runner（M9.5）：周一 09:30 自动跑 collect_hard_cases
- [ ] 接 M11.C abstain 自适应：hard cases 反向影响下一轮阈值标定
