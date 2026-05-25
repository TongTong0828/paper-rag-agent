# feedback_loop.md — M11 数据闭环时序图

> 对应 ADR-0017 / feedback 包 + collect_hard_cases.py + abstain_autocalibrate.py

## 1. 用户反馈写入

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant FE as Frontend (inbox UI)
    participant R as Router<br/>POST /feedback
    participant F as feedback.store
    participant DB as feedback.sqlite
    participant M as metrics

    U->>FE: 看到 QA 答案<br/>点击 👎 + 选 "hallucination"
    FE->>R: POST { kind: "thumbs_down",<br/>conversation_id, message_id, reason }
    R->>R: BetterAuth → user_id
    R->>F: write_event(user_id, kind, payload, ...)
    F->>DB: INSERT feedback_events
    F-->>R: event_id
    R->>M: counter("paper_rag_feedback_total",<br/>{kind:"thumbs_down"}).inc()
    R-->>FE: { id, status: "ok", user_id }
    FE-->>U: 反馈已收到
```

## 2. 周一定时收集 hard cases

```mermaid
sequenceDiagram
    autonumber
    participant CRON as cron (M9.5 future)
    participant CHC as collect_hard_cases.py
    participant F as feedback.store.list_events
    participant DB as feedback.sqlite
    participant HC as tests/eval/hard_cases.jsonl

    CRON->>CHC: run --since 7d
    CHC->>F: list_events(since=7d)
    F->>DB: SELECT * FROM feedback_events WHERE ts >= ?
    DB-->>F: events[]
    F-->>CHC: events[]
    CHC->>CHC: Apply 4 rules:<br/>1. thumbs_down + bad reason<br/>2. ≥2 follow_up in 5min<br/>3. judge faithful<4 OR complete<3<br/>4. abstain_followup_ingest
    CHC->>HC: dedup against existing<br/>append jsonl
    CHC-->>CRON: { detected: N, new: M }
```

## 3. 半自动 abstain 阈值再校准

```mermaid
sequenceDiagram
    autonumber
    participant DEV as developer / PR review
    participant AC as abstain_autocalibrate.py
    participant HC as hard_cases.jsonl
    participant QA as qa_set.real.jsonl
    participant CAL as scripts/calibrate_abstain.py
    participant CFG as config/default.yaml
    participant CI as pytest CI

    DEV->>AC: run autocalibrate
    AC->>HC: read hard cases (last 30d)
    AC->>QA: read existing QA set
    AC->>AC: synthesize new QA entries<br/>(question + relevant_paper_ids)
    AC->>QA: append (with provenance tag)
    AC->>CAL: subprocess.run(--mode online --target-fpr 0.0)
    CAL->>CAL: gather scores (no LLM, retrieve only)
    CAL->>CAL: pick (τ_low, τ_high) via ROC sweep
    CAL-->>AC: recommendation { τ_low, τ_high, neg_blocked, pos_kept }
    AC->>AC: render PR description<br/>(diff vs current YAML)
    AC->>CFG: PROPOSED edit (NOT auto-merged)
    AC-->>DEV: PR draft<br/>"τ_low 0.21 → 0.23 (neg_blocked unchanged, pos_kept +1.2%)"
    DEV->>DEV: human review<br/>(approve / reject / tweak)
    DEV->>CI: merge if approved
    CI->>CI: run pytest 113 tests<br/>(abstain regression caught)
```

## 关键点

- **闭环不是全自动**：阈值变更走 PR review，人工把关
- **abstain_autocalibrate 解耦数据收集与阈值更新**：collect 是数据流向 jsonl，calibrate 是 jsonl 到阈值
- **regression net**：pytest 里 `test_abstain_thresholds_sane` 检查 0 < τ_low < τ_high < 1
