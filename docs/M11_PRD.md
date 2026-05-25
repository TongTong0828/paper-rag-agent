# M11 PRD · 数据闭环（行为埋点 / Hard Case 自动收集 / Abstain 阈值自适应）

- **状态**: draft → approved
- **作者**: paper_rag 团队
- **日期**: 2026-05-21
- **关联 ADR**: 0017
- **预计工程量**: 2-3 周
- **目标里程碑**: M11 数据闭环

---

## 1 · 目标 (Goals)

**让 paper_rag 用得越多越好用** —— 系统通过用户真实行为持续进化：

1. **行为埋点**：用户正反馈（点赞、复制、追问）+ 反馈（点踩、修正、放弃）一律入库
2. **Hard case 自动收集**：低评分会话进 `hard_cases.jsonl`，**评测集自扩展**
3. **Abstain 阈值自适应**：每周从 hard cases 重新 calibrate，**人在 loop 审核 PR**
4. **Wiki 自进化触发**：高频低评分主题自动起 wiki 词条任务

---

## 2 · 非目标 (Non-Goals)

- ❌ 训练 / 微调模型（M12 才考虑 LoRA）
- ❌ 完全无人值守的"系统自己改自己"（避免漂移；阈值更新走 PR review）
- ❌ 用户级行为分析仪表盘（M9 + 第三方 BI 工具做）
- ❌ A/B 实验框架（先做埋点，AB 框架是 M13）

---

## 3 · 现状 (Context)

### 3.1 已具备
- M8 router 已注入 `user_id`、`trace_id`
- M7 P0 abstain 决策已在 trace 中
- `paper_rag.observability.metrics` Prometheus counters
- `tests/eval/qa_set.real.jsonl` 静态评测集（33 题）

### 3.2 Gap
- ❌ 用户对答案的反馈（点赞/点踩/复制等）无地方落
- ❌ trace_id 是有了，但事后无法关联用户行为
- ❌ 评测集是静态的——只能反映 prompt 设计者的偏见，不反映真实用户分布
- ❌ abstain 阈值是手动调（5/19 用 33 题 ROC 拍脑袋），没有持续调优机制

---

## 4 · 用户故事 (User Stories)

### US-1：用户给反馈（被动）
> 张同学：在前端答案下点了"👎"，弹一个简短表单"哪里不对？" → 选"幻觉/编造"。
> 系统：写一条 `feedback` 事件到 SQLite，关联 trace_id + 答案 + 召回 chunks，**下周自动进 hard cases**。

### US-2：用户给反馈（主动）
> 李同学：复制了某段答案到自己笔记 → 系统埋点"高质量答案"。
> 系统：把这段答案 + cite 模式作为正例，**强化对应主题的 prompt**。

### US-3：拒答后用户自己补 PDF
> 王同学：问"FlashAttention 为啥快"→ 系统 abstain 拒答（库里没这篇）。
> 王同学：手动上传 FlashAttention.pdf 入库，再问同问题 → 系统答了。
> **这条行为路径 = 一次最强信号**："我应该 ingest 这篇"，沉淀到推荐入库白名单。

### US-4：研究者维护"对系统的信任"
> 赵同学：每月看一次 hard cases 统计（20 条 + 各自 trace），判断哪些是系统问题、哪些是用户 question 模糊。
> 系统：提供 `scripts/hard_case_review.py` CLI 工具。

### US-5：阈值自适应
> 系统每周自动跑 `calibrate_abstain.py`（用现集合 + 新 hard cases），输出 `abstain_calibration.json`，包含**推荐阈值 + 当前 vs 推荐的 confusion matrix**。
> 工程师：CR 通过，merge 一个改 `default.yaml` 的 PR。

---

## 5 · 数据 schema

### 5.1 `feedback_events` 表

```sql
CREATE TABLE feedback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    trace_id TEXT,                  -- 关联 qa_agentic 的 trace
    conversation_id TEXT,           -- 关联多轮会话
    event_type TEXT NOT NULL,       -- thumbs_up/down, copy, follow_up, abandon, abstain_followup_ingest, judge_score
    payload_json TEXT NOT NULL,     -- 事件详情（点踩原因、复制片段、判分等）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_feedback_user_time ON feedback_events(user_id, created_at);
CREATE INDEX idx_feedback_trace ON feedback_events(trace_id);
CREATE INDEX idx_feedback_type ON feedback_events(event_type);
```

### 5.2 事件类型清单（v1）

| `event_type` | 触发方 | payload 关键字段 |
|---|---|---|
| `thumbs_up` | 用户主动 | trace_id |
| `thumbs_down` | 用户主动 | trace_id, reason (`hallucination` / `irrelevant` / `incomplete` / `other`), comment |
| `copy_answer` | 前端自动埋 | trace_id, snippet_chars |
| `follow_up_question` | 自动追问检测 | trace_id, prev_trace_id |
| `abandon` | 5 分钟无后续 | trace_id |
| `abstain_followup_ingest` | abstain 拒答后用户自己 ingest | trace_id, ingested_paper_id |
| `judge_score` | 离线 judge | trace_id, faithful, complete, concise |

### 5.3 隐私设计

- 用户 free-text comment **不存原文**，存 length + 关键词命中标志
- 个人信息（emails / 真实姓名 / etc）一律 hash 后存
- 7 天 retention：原始 events 保留 90 天后聚合到月度 stats，原表清理

---

## 6 · 接口设计

### 6.1 上报端点

```http
POST /api/paper_rag/feedback
Authorization: <BetterAuth cookie>

{
  "trace_id": "abc123",
  "event_type": "thumbs_down",
  "payload": {
    "reason": "hallucination",
    "comment_length": 42
  }
}
```

返回 `{"id": 1234, "status": "recorded"}`，幂等（同 trace_id + event_type + 同分钟去重）。

### 6.2 查询端点（运维 / 用户自查）

```http
GET /api/paper_rag/feedback/recent?limit=20
GET /api/paper_rag/feedback/stats   # 当前用户的反馈聚合
```

### 6.3 LangChain Tool（可选，M11.5）

`paper_feedback_tool` 让 agent 自己埋点（"用户在追问 X，我标一下"），仅给 lead_agent 调用。

---

## 7 · Hard Case 自动收集

### 7.1 触发规则（v1）

任一命中即归档：

| Rule | 含义 |
|---|---|
| `thumbs_down + reason in {hallucination, irrelevant}` | 用户明确指出问题 |
| `≥ 2 follow_up_question` 在 5 分钟内 | 用户多次追问 = 答案不够 |
| `judge_score.faithful < 4 或 complete < 3` | 离线 judge 判低 |
| `abandon` 后 ≥ 30 天再问类似问题 | 长尾用户重新提问 |
| `abstain_followup_ingest` | 系统漏召回但库里其实有 |

### 7.2 输出格式

```jsonl
{"qid": "hc_2026_05_21_001", "question": "...", "trace_id": "...", 
 "rule": "thumbs_down_hallucination", "expected_behavior": "...",
 "captured_at": "2026-05-21T..."}
```

### 7.3 脚本

```bash
# Cron 每周一 09:00 自动跑
docker compose exec paper_rag python scripts/collect_hard_cases.py \
    --since 7d --out tests/eval/hard_cases.jsonl
```

---

## 8 · Abstain 阈值自适应

### 8.1 流程（半自动）

```
Cron 每周日 22:00:
  1. 跑 calibrate_abstain.py 用 (qa_set.real.jsonl + hard_cases.jsonl)
  2. 输出 abstain_calibration_$(date).json
  3. 如果新阈值 vs 旧阈值差异 > 5%（任一方向）:
     a. 在 GitHub 起 PR：改 default.yaml + 附 calibration JSON
     b. 工程师 CR
     c. Merge → CI 自动重启服务
  4. 否则只 archive 数据
```

### 8.2 关键决策：人在 loop

**不让系统自己改自己**——避免：
- 阈值漂移（连续 N 周小调整最终偏离合理范围）
- hard cases 偏分布污染（恶意用户灌反馈）
- 不可解释性（出问题难定位）

工程师 CR 是闸门，但 PR 已经把 "推荐阈值 + 数据 + confusion matrix" 都准备好，CR 工作量低（< 5 min）。

### 8.3 失败时回滚

如果新阈值 merged 后某指标退化 > 10%（例如 fpr 从 0 → 0.15）：
- alert 触发
- 自动开 revert PR

---

## 9 · 验收 (DoD)

| # | 验收点 | 验证 |
|---|---|---|
| 1 | `feedback_events` 表创建 + 5 个 event_type 全部能写入 | 单测 |
| 2 | POST /api/paper_rag/feedback 接收 + 幂等 + user_id 注入 | 集成测试 |
| 3 | hard_case_collector 能从 stub events 抽出 5 类 hard case | 单测 |
| 4 | calibrate 脚本能读 hard_cases.jsonl 并输出阈值候选 | 单测 |
| 5 | 隐私脱敏：comment 不存原文 | 单测 |
| 6 | 5 项 feedback 纯逻辑单测全绿 | pytest |
| 7 | 端到端：mock 一次 thumbs_down → hard case 自动收 → calibrate 输出新阈值 | 集成测试 |

---

## 10 · 风险与缓解

| 风险 | 缓解 |
|---|---|
| 用户不点反馈（按钮使用率 <5%）| 隐式信号优先（复制 / 追问 / abandon），显式反馈是补充 |
| 反馈被恶意刷量 | user_id 维度上限（每天每用户 ≤50 events）+ 异常检测 |
| 阈值漂移 | 强制人在 loop（PR review）+ revert 阈值 |
| hard cases 数据稀疏（前 4 周 <10 条）| 阈值更新触发线设为 ≥30 条新 hard cases |
| events 表无限膨胀 | 7/30/90 天分级 retention + 月度聚合 |

---

## 11 · 时间表

| Week | 任务 |
|---|---|
| **W1** | feedback 包 + SQLite schema + POST 端点 + 5 项单测（M11.A） |
| **W2** | hard_case_collector 脚本 + abstain 自适应骨架（M11.B + M11.C 半成品） |
| **W3** | abstain CI 接入 + alert 接入 + 端到端集成测试 + 文档 |

---

## 12 · 后续 (Out of scope, M12+)

- ML / LoRA 微调用户偏好
- 跨用户协同（"和你研究方向相近的 5 人也对这答案点踩"）
- 评测集自扩展引入 LLM 改写
- A/B 实验框架（M13）
