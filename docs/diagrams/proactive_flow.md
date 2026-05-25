# proactive_flow.md — M9 主动 Agent 时序图

> 对应 ADR-0018 / proactive 包 7 模块 + cron_runner

## 1. daily_digest 推送（cron sidecar）

```mermaid
sequenceDiagram
    autonumber
    participant CR as cron_runner<br/>(APScheduler @ 08:00)
    participant D as digest.daily_digest_for_all_users
    participant S as subscriptions.list_users
    participant A as arxiv API
    participant T as small_model (TL;DR cache)
    participant I as inbox.write
    participant W as webhook.fan_out
    participant DB as feedback.sqlite

    CR->>D: trigger
    D->>S: list_users()
    S-->>D: [alice, bob, ...]
    loop per user
        D->>S: list_keywords(user_id)
        loop per keyword
            D->>A: search latest arxiv (last 24h, max=5)
            A-->>D: papers[]
        end
        D->>D: dedup by arxiv_id
        loop per paper (max 10)
            D->>T: tldr(paper_id)<br/>(cross-user cache, 100x cost saving)
            T-->>D: 50-char TL;DR
        end
        D->>D: render_digest_card(bullets)
        D->>I: write(kind="daily_digest", title, body_md, ...)
        I->>DB: INSERT inbox_items
        I->>W: fan_out(item) [P3-13]
        W->>W: list_webhooks(user_id)
        par dingtalk
            W->>+W: HMAC sign + POST
        and feishu
            W->>+W: card payload + POST
        and email
            W->>+W: SMTP send
        end
        W-->>I: { sent: N, results: [...] }
    end
    CR-->>CR: log result, sleep until next trigger
```

## 2. 订阅匹配 + 自动 ingest（QA 流处理 hook）

```mermaid
sequenceDiagram
    autonumber
    participant U as User (alice)
    participant R as Router /qa/sync
    participant Q as qa_agentic
    participant H as auto_ingest_hook
    participant BG as asyncio.create_task<br/>background_ingest
    participant ING as paper_rag.ingest
    participant M as matcher
    participant SUB as subscriptions
    participant I as inbox.write

    U->>R: "I just read https://arxiv.org/abs/2310.11511,<br/>what is its key contribution?"
    R->>Q: answer(question)
    Q->>H: detect_arxiv_ids(question)
    H-->>Q: ["2310.11511"]
    Q->>BG: schedule_for_text(question, user_id)
    Note over BG: returns immediately,<br/>QA path NOT blocked
    Q-->>R: answer (based on retrieve)
    R-->>U: response

    par background ingest
        BG->>ING: ingest_one("2310.11511", user_id="alice")
        ING-->>BG: { paper_id, n_chunks, title }
        BG->>I: write(kind="auto_ingest", "✅ ingested arxiv:2310.11511")
    end

    par sub matching (after ingest)
        BG->>M: match_paper_to_subs(paper_id="arxiv:2310.11511")
        M->>SUB: list_subs_with_dense_topics()
        SUB-->>M: [(bob, "RAG", high), (carol, "agents", normal), ...]
        M->>M: bge-m3 cosine vs subscription embeddings
        M->>M: filter sim ≥ threshold(strength)<br/>skip ingester(=alice)
        loop matched subs (excluding alice)
            M->>I: write(kind="sub_match", to=bob, "📚 New paper for 'RAG': ...")
            I->>I: SUB → mark_matched()
        end
    end
```

## 3. stale_scan（每周一 09:00）

```mermaid
sequenceDiagram
    autonumber
    participant CR as cron_runner
    participant S as stale.stale_scan_for_all_users
    participant PA as paper_access.list_users_with_access
    participant SF as paper_access.stale_for_user<br/>(older_than_days=30)
    participant ST as sqlite_store.get_paper
    participant I as inbox.write

    CR->>S: trigger (Monday 09:00)
    S->>PA: list_users_with_access()
    PA-->>S: ["alice", "bob"]
    loop per user
        S->>SF: stale_for_user(user_id, 30)
        SF-->>S: [{paper_id, last_accessed_at, access_count}, ...]
        loop top 3 stale
            S->>ST: get_paper(paper_id)
            ST-->>S: { title, abstract, ... }
            S->>I: write(kind="stale_paper", "🕰 复习一下：{title}")
        end
    end
```

## 关键点

- **3 类推送共享 `inbox.write`**：唯一写入点 → 唯一触发 webhook fan-out
- **跨用户 TL;DR 缓存**：digest 阶段 100x 成本节省
- **ingester 不收自己的 sub_match**：避免噪声
- **背景 ingest 永不阻塞 QA**：`asyncio.create_task` + best-effort
