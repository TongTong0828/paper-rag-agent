# paper_rag

[![ci](https://github.com/TongTong0828/paper-rag-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/TongTong0828/paper-rag-agent/actions)
[![codecov](https://codecov.io/gh/TongTong0828/paper-rag-agent/branch/main/graph/badge.svg)](https://codecov.io/gh/TongTong0828/paper-rag-agent)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)]()
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Agentic RAG over academic papers. Originally built as a sub-system for
[DeerFlow](https://github.com/bytedance/deer-flow); the code in this repo runs
standalone as a Python package + FastAPI router.

What's inside:

- Hybrid retrieval (BM25 + dense Qdrant, RRF fusion, BGE reranker)
- Three-tier abstain decision (`confident / weak_evidence / no_evidence`) with
  data-driven threshold calibration
- Self-evolving wiki with cross-paper concept linking
- Five deliverable formats: markdown survey / pptx / docx / latex+bib / pdf
- Proactive scheduler: daily digest, subscription matching, stale-paper
  reminders, auto-ingest webhook
- Feedback loop: thumbs / copy events → hard-case dataset → semi-auto
  threshold recalibration

Numbers (HEAD of `main`):

| | |
|---|---|
| Tests | 162 passing (incl. 19 middleware) |
| ADRs | 21 |
| HTTP endpoints | 19 |
| Grafana panels / alert rules | 13 / 13 |

---

## Quickstart

```bash
# 1. Install (dev mode)
make install-dev
make qdrant-up
make init-store

# 2. Ingest a paper and ask a question (CLI)
make ingest ID=2310.11511                 # Self-RAG
make ask Q="What is Self-RAG?"

# 3. Run the gateway + frontend (only when integrated with deer-flow)
make up                                   # gateway + frontend + qdrant
make obs-up                               # Prometheus + Grafana

# 4. Tests
make test
```

CI installs only the minimal dependency set required for pure-logic tests
and the import-walk smoke check; see `.github/workflows/ci.yml` if you want
to reproduce that environment locally.

---

## Architecture

```mermaid
flowchart TB
    FE[Frontend / Next.js<br/><sub>workspace/paper-rag</sub>] -->|HTTPS| GW

    subgraph GW [DeerFlow Gateway · FastAPI]
        direction TB
        MW[8 middleware layers<br/><sub>BodySize → GZip → RequestId → AccessLog<br/>→ Prometheus → RateLimit → Timeout → Auth</sub>]
        ROUTER[paper_rag router<br/>19 endpoints]
        MW --> ROUTER
    end

    GW --> PKG

    subgraph PKG [paper_rag package]
        direction LR
        RAG[rag/<br/>retrieve/]
        DEL[deliver/<br/>wiki/]
        STORE[store/<br/>feedback/]
        PROACT[proactive/]
    end

    PKG --> QDR[(Qdrant<br/>vectors)]
    PKG --> SQL[(SQLite × 2<br/>papers + feedback)]

    CRON[APScheduler sidecar<br/><sub>daily 08:00 · Mon 09:00</sub>] -->|fan-out| HOOKS[DingTalk · Feishu<br/>WeCom · Email]
    CRON -.reads.- SQL

    PROM[Prometheus<br/>15s scrape] -->|metrics| GRA[Grafana<br/>13 panels]
    PROM --> AM[alertmanager<br/>13 rules]
    GW -.scrapes.- PROM
    PKG -.scrapes.- PROM
```

The gateway and middleware live in the deer-flow monorepo. A snapshot of
those files (router + middleware + frontend page + observability stack)
is reproduced under [`docs/integration/`](docs/integration/) for reference.

---

## Request flow (typical QA)

```mermaid
sequenceDiagram
    autonumber
    participant U as Client
    participant GW as Gateway (8 mw)
    participant QA as qa_stream
    participant RET as retrieve
    participant ABS as abstain
    participant LLM as LLM

    U->>GW: POST /api/paper_rag/qa (SSE)
    GW->>GW: RequestId · AccessLog · Prometheus<br/>RateLimit · Timeout (SSE bypass) · Auth
    GW->>QA: dispatch with user_id

    loop _retrieve_round
        QA->>RET: rewrite + hybrid_search<br/>(BM25 FTS5 + Qdrant, RRF)
        RET->>QA: candidates
        QA->>RET: rerank (BGE-reranker-v2-m3)
        RET->>QA: top_k chunks
    end

    QA->>ABS: decide(chunks, low=0.21, high=0.48)
    alt score < 0.21
        ABS-->>QA: no_evidence — canned reply
        QA-->>U: SSE done (LLM skipped)
    else 0.21 ≤ score < 0.48
        ABS-->>QA: weak — inject hint
        QA->>LLM: chat(hint + evidence)
        LLM-->>QA: answer
        QA-->>U: SSE stream
    else score ≥ 0.48
        ABS-->>QA: confident
        QA->>LLM: chat(evidence)
        LLM-->>QA: answer
        QA-->>U: SSE stream
    end

    QA->>QA: validate_citations + detect_suspicious
    QA-->>QA: paper_access.touch_many() (async)
```

See [`docs/diagrams/abstain_flow.md`](docs/diagrams/abstain_flow.md) for the
full mermaid sequence diagram and [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md)
for a longer walkthrough.

---

## Performance baseline

Numbers from `docs/PERF_BASELINE.md`; production traffic will refresh them.

| Metric | Value | Notes |
|---|---|---|
| Test suite | 3.0 s | 162 tests, used as CI gate |
| Recall@10 | 0.90 | 33-question eval set |
| Abstain neg-blocked / pos-kept | 100% / 97% | offline calibration |
| QA P50 / P95 | ~2.0s / ~5.0s | confident path / reflect+long answer |
| `no_evidence` latency | ~250 ms | LLM is skipped |
| Cold start (qa_agentic) | 316 ms | OpenAI client + tiktoken |
| Lean docker image | ~600 MB | multi-stage + venv copy |

---

## Failure modes

| Component down | Effect | Degradation |
|---|---|---|
| Qdrant | Dense recall is empty | BM25-only path; abstain switches to BM25 score |
| LLM | Generation fails | Evidence-only response, `qa_degraded_total` increments |
| Reranker | Rerank fails | Falls back to RRF order, marks `quality=low` |
| feedback.sqlite locked | Inbox writes drop | Logs warning; QA path unaffected |
| All webhooks | No outbound push | Inbox still readable from frontend |
| Cron container | Digest / stale skip | Gateway unaffected; manual `POST /proactive/digest/run` |
| Redis (rate limit) | Multi-replica counters drift | Falls back to in-memory window |

13 Prometheus alert rules cover 5xx > 1%, p95 > 5s, abstain rate > 30%,
auth/timeout/rate spikes, and component-down conditions.

---

## Key design decisions

| ADR | Topic |
|---|---|
| 0014 | Three-tier abstain — calibration vs recall trade-off |
| 0015 | M8 service split: sibling package + gateway router |
| 0016 | N+1+S call shape for survey generation |
| 0017 | M11 feedback loop — semi-auto threshold recalibration |
| 0018 | M9 proactive agent + APScheduler |
| 0019 | Two SQLite databases (papers vs feedback) |
| 0020 | 8-layer middleware stack + Prometheus cardinality control |
| 0021 | Four langgraph middlewares (cost / latency / recursion / PII) |

Full set: [`docs/adrs/`](docs/adrs/).

---

## Repository layout

```
paper_rag/
├── src/paper_rag/
│   ├── rag/                  Core agentic loop
│   │   ├── abstain.py        Three-tier decision (ADR-0014)
│   │   ├── qa_agentic.py     Rewrite → retrieve → rerank → reflect
│   │   └── qa_stream.py      SSE variant
│   ├── retrieve/             Hybrid + rerank
│   ├── deliver/              5 deliverable formats
│   ├── proactive/            Daily digest, subscriptions, stale, webhook
│   ├── feedback/             Event store + hard-case extraction
│   └── observability/        60-line stdlib Prometheus exposition
├── tests/                    pytest 162/162
├── scripts/
│   ├── calibrate_abstain.py  Online / offline threshold calibration
│   └── collect_hard_cases.py Weekly cron entry
└── docs/
    ├── SYSTEM_DESIGN.md
    ├── PERF_BASELINE.md
    ├── EVAL_REPORT.md
    ├── adrs/                 21 ADRs
    ├── diagrams/             3 mermaid sequence diagrams
    └── integration/          Snapshot of deer-flow integration files
```

---

## Development

```bash
# Install dev tooling
pip install -e .[dev]
pre-commit install                  # local lint matches CI

# Lint (same rules CI runs)
ruff check --select E,F,W,I --ignore E501 src tests

# Tests (real pytest, with coverage)
pytest -q \
    --ignore=tests/eval \
    --ignore=tests/test_gateway_paper_rag.py \
    --ignore=tests/test_middleware.py \
    --ignore=tests/test_langgraph_middleware.py \
    --cov=src/paper_rag

# Lightweight fallback for environments without pytest
PYTHONPATH=src:tests python scripts/_run_tests.py
PYTHONPATH=src python scripts/_run_smoke.py

# Threshold calibration
python scripts/calibrate_abstain.py --mode offline
python scripts/calibrate_abstain.py --mode online --no-rewrite --top-k 8

# Collect hard cases (weekly)
make hard-cases
```

Contributions and bug reports are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
