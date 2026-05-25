# Demo — visual walk-through

A 4-diagram tour of the internals. Read top-to-bottom or jump to whatever
caught your eye in the [README](../README.md).

## 1. Abstain decision (ADR-0014)

The single decision that makes the difference between "answer with the
evidence we have" and "decline cleanly". Three bands, six branches, no
LLM call below 0.21:

```mermaid
flowchart TD
    A([retrieve+rerank<br/>chunks]) --> B{n_chunks > 0?}
    B -- no --> NC[no_chunks]
    B -- yes --> C[evidence_score: avg of top-k normalised scores]
    C --> D{enabled?}
    D -- no --> CONF1[confident · disabled]
    D -- yes --> E{score field?}
    E -- missing --> CONF2[confident · missing]
    E -- BM25/RRF only --> CONF3[confident · low_degraded]
    E -- rerank/dense --> F{score &lt; threshold_low?}
    F -- yes --> NE[no_evidence<br/>LLM is SKIPPED]
    F -- no --> G{score &lt; threshold_high?}
    G -- yes --> WK[weak_evidence<br/>LLM with hint]
    G -- no --> CONF4[confident<br/>LLM normal]

    style NE fill:#fee,stroke:#c33
    style WK fill:#fff3cd,stroke:#cb8800
    style CONF4 fill:#e6f4ea,stroke:#34a853
```

Calibration data lives in `tests/eval/abstain_calibration_report.json`,
re-run via `make calibrate-abstain` (offline) or
`scripts/calibrate_abstain.py --mode online` (real LLM).

---

## 2. Proactive scheduler (M9)

Daily / weekly cron plus 4 push channels. The scheduler is a separate
sidecar container so a cron crash does NOT take the QA gateway down.

```mermaid
flowchart LR
    subgraph CRON [APScheduler sidecar]
        DAY[daily 08:00<br/>digest]
        WK[Mon 09:00<br/>stale_scan]
        SUB[on-paper-indexed<br/>sub_match]
        AI[on-message-arxiv<br/>auto_ingest]
    end

    DAY --> INBOX[(inbox.sqlite<br/>kind=daily_digest)]
    WK --> INBOX2[(inbox.sqlite<br/>kind=stale_paper)]
    SUB --> INBOX3[(inbox.sqlite<br/>kind=sub_match)]
    AI --> INBOX4[(inbox.sqlite<br/>kind=auto_ingest)]

    INBOX --> WH{webhook<br/>fan-out}
    INBOX2 --> WH
    INBOX3 --> WH
    INBOX4 --> WH

    WH -->|markdown| DT[DingTalk]
    WH -->|interactive card| FS[Feishu / Lark]
    WH -->|markdown| WC[WeCom]
    WH -->|HTML| EM[SMTP email]
    WH -.->|always| FE[Frontend inbox UI]
```

Webhook failures never block inbox writes, but they DO log a warning so
misconfigured endpoints surface in production. See ADR-0018 + the
`proactive/webhook.py` `@register` decorator.

---

## 3. Feedback data loop (M11)

Thumbs-down / copy-answer events feed back into the threshold calibrator.
The loop is **semi-automatic** by design — humans review the proposed new
thresholds before they ship.

```mermaid
flowchart LR
    UI[Frontend<br/>thumbs / copy] -->|POST /feedback| GW[Gateway]
    GW --> EV[(feedback.sqlite<br/>events)]

    subgraph WEEKLY [Weekly cron]
        EX[collect_hard_cases.py]
        CAL[calibrate_abstain.py<br/>--mode online]
    end

    EV --> EX
    EX -->|hard_cases.jsonl| CAL
    CAL -->|recommendation.json| PR[GitHub PR<br/>config update]
    PR -.->|human review| MERGE[merge → reload config]
    MERGE -.-> RAG[QA path<br/>uses new thresholds]
```

This is why we keep `enabled: true` on abstain even before final
calibration — the running thresholds are *meant* to drift slowly with
real-user signal, not be locked in once.

---

## 4. Gateway middleware stack

Eight layers, in the exact request order. The two most important
properties: **timeout bypass for SSE** (so streaming answers don't get
cut at 60s) and **rate-limit fail-open** (Redis outage falls back to an
in-memory window instead of 429-ing everyone).

```mermaid
flowchart LR
    REQ([client]) --> M1[BodySize<br/>50MB cap]
    M1 --> M2[GZip]
    M2 --> M3[RequestId<br/>uuid4 hex]
    M3 --> M4[AccessLog<br/>JSON line]
    M4 --> M5[Prometheus<br/>histogram + counter]
    M5 --> M6[RateLimit<br/>Redis or in-mem]
    M6 --> M7[Timeout 60s<br/>SSE bypass]
    M7 --> M8[BetterAuth<br/>session token + LRU]
    M8 --> ROUTE([paper_rag router])

    style M7 fill:#e8f0fe,stroke:#1a73e8
    style M6 fill:#e8f0fe,stroke:#1a73e8
```

Cardinality control: `path_template` is normalised to `/papers/:id` style
before it reaches Prometheus, otherwise per-request UUIDs would explode
the metric series. ADR-0020 covers the full design.

---

## See also

- [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) — 30-minute walkthrough
- [`PERF_BASELINE.md`](PERF_BASELINE.md) — latency + throughput numbers
- [`adrs/`](adrs/) — 21 frozen design decisions
- [`diagrams/abstain_flow.md`](diagrams/abstain_flow.md) — sequence diagram
- [`../examples/`](../examples/) — runnable Python walk-throughs
