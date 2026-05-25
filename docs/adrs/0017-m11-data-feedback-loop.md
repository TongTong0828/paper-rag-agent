# ADR-0017 · M11 数据闭环（行为埋点 / Hard Case 自动收集 / Abstain 自适应）

- **日期**: 2026-05-21
- **状态**: accepted
- **关联 PRD**: `docs/M11_PRD.md`

## Context

paper_rag 走完 M0-M10 后，**算法和功能都到位了**——但有一个本质缺陷：**评测集是静态的**。33 题 qa_set.real.jsonl 反映的是 prompt 设计者的偏见，不是真实用户分布。这导致：

1. abstain 阈值靠手动 ROC（5/19 拍脑袋定的 0.20/0.40），扩库后失准
2. hallucination / 不准确答案没有反馈通道
3. 系统不会因为"用得多"而变好——这是产品壁垒的对立面

M11 要补的是**真实工业级 RAG 产品最稀缺的一层**：用户行为反哺。

## Decisions

### 决策 1：埋点 schema = `(user_id, trace_id, event_type, payload_json, created_at)`
五元组足够覆盖正向（thumbs_up / copy_answer）+ 反向（thumbs_down / abandon）信号。trace_id 复用 ADR-0013 已有的，**不另开调用链**。

### 决策 2：隐式信号优先于显式反馈
用户不会主动点反馈（行业经验：< 5% 显式率）。`copy_answer` / `follow_up_question` / `abandon` 这三个隐式信号是主力，thumbs_up/down 是辅助。

### 决策 3：人在 loop 审核 abstain 阈值更新
不让系统自己改自己。calibrate 输出推荐阈值 + confusion matrix，工程师 CR 通过 GitHub PR。
**理由**：阈值漂移、恶意 feedback 灌量、不可解释性都靠这道闸门挡掉。

### 决策 4：feedback_events 走单独 SQLite 表，不混进 paper_rag 主库
- 主库 `papers.sqlite` 是用户内容（papers / chunks / wiki），feedback 是元数据
- 主库 schema 稳定优先，feedback 表会随 event_type 演化
- 单独 `data/index/feedback.sqlite` 文件，主库 schema migration 不受影响

### 决策 5：隐私优先 — comment 不存原文
free-text comment 只存 `length + 关键词命中标志`（hallucination / irrelevant / incomplete 这种枚举）。**用户输入是负担，不是资产**。

### 决策 6：3 级 retention（7d / 30d / 90d）
原始 events 90 天后自动聚合到月度 stats，原表清理。**避免无限膨胀** + 满足合规。

### 决策 7：Hard case 触发规则编码到 `collect_hard_cases.py`，不写在 SQL 里
触发规则会随系统演化（新 event_type / 新阈值），写在 Python 里好测、好改、可版本化。SQL 只做最朴素的"按时间 + user_id 过滤"。

### 决策 8：abstain 自适应不直接改 default.yaml，输出到独立文件
- 输出 `data/index/abstain_calibration_$(timestamp).json`
- 默认 PR template 引用这个 JSON
- **default.yaml 永远是经过 review 的"信任版本"**

## Consequences

### Positive
- 真正的"用得越多越好用" — 产品壁垒
- 评测集自扩展，不靠 prompt 设计者偏见
- abstain 阈值数据驱动，不是常量
- 隐私 / retention / 人审核三道闸 → 工业级合规

### Negative / Trade-offs
- 前期数据稀疏（W1 < 10 条 events），阈值不能立刻更新
- 增加 SQLite 文件 + cron job 运维负担
- 依赖前端配合埋点（M11.UI 要前端工程师介入）

## Alternatives considered

1. **Prometheus events 即埋点** ❌：counter 是聚合的，丢失单次细节，无法做 hard case
2. **Postgres 替 SQLite** ❌：单机部署 + 数据量小（10K events/月），没必要
3. **完全无人值守自动改阈值** ❌：见决策 3
4. **存原始 comment 文本** ❌：见决策 5
5. **不分库（混进 papers.sqlite）** ❌：见决策 4

## 验收 (DoD)
见 PRD § 9。

## 后续
- ADR-0018（M9 主动 Agent）：cron / 订阅 / 会议关联
- ADR-0019（M12 多模态扩展，按需）：图表 GPT-4V / 引用图谱
- ADR-0020（M13 A/B 实验框架）：当 events 数据稳定后引入
