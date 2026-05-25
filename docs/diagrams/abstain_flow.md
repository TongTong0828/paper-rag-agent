# abstain_flow.md — abstain 三档拒答时序图

> 对应 ADR-0014 / qa_agentic.py / qa_stream.py
> 阈值标定参见 `EVAL_REPORT.md`

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant R as Router
    participant Q as qa_agentic
    participant H as hybrid_search<br/>(BM25 + Qdrant)
    participant K as rerank<br/>(BGE v2 m3)
    participant A as abstain.decide
    participant L as LLM (Qwen-plus)
    participant C as citation_check

    U->>R: POST /api/paper_rag/qa/sync
    R->>R: BetterAuth → user_id
    R->>Q: answer(question, paper_ids)
    Q->>Q: query_rewrite (1 LLM call)
    Q->>H: hybrid_search (top_k=10)
    H-->>Q: candidates (BM25 + dense fused via RRF)
    Q->>K: rerank(candidates, top_k=10)
    K-->>Q: chunks ranked by score_rerank
    Q->>A: decide(chunks, low=0.21, high=0.48)

    alt evidence_score < 0.21 (12%)
        A-->>Q: decision=no_evidence
        Q-->>R: canned message,<br/>n_chunks=N, abstain.decision="no_evidence"
        Note over Q,L: ❌ LLM call SKIPPED<br/>~250ms total
    else 0.21 ≤ score < 0.48 (18%)
        A-->>Q: decision=weak_evidence
        Q->>L: chat(prompt + insufficiency hint)
        L-->>Q: answer (cautious tone)
        Q->>C: validate_citations + detect_suspicious
        C-->>Q: cleaned, valid_cite_ids
        Q-->>R: answer with abstain.decision="weak_evidence"
    else score ≥ 0.48 (70%)
        A-->>Q: decision=confident
        Q->>L: chat(prompt with strict cite rules)
        L-->>Q: answer
        Q->>C: validate_citations + detect_suspicious
        C-->>Q: cleaned, valid_cite_ids
        Q-->>R: answer with abstain.decision="confident"
    end
    R->>R: paper_access.touch_many(user_id, pids)<br/>(non-blocking, M9 stale tracking)
    R-->>U: { answer, citations, abstain, trace_id }
```

## 关键点

- **abstain 是延迟节流器**：12% no_evidence 直接 skip LLM，把 P50 从 ~2s 拉到 ~250ms
- **fail-open 不在这一层**：reranker 挂了走 BM25 score，但 abstain 仍然按 score_field 分档
- **paper_access.touch 在 router 层**：QA 完答完后异步写入，喂数据给 stale_scan
