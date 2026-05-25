"""Agentic paper_qa: intent -> rewrite -> hybrid retrieve -> rerank -> reflect -> iterate.

Closed-loop: the lead agent only sees ONE tool call; all internal hops happen here.
Hard caps: max_inner_iters and max_inner_tokens from config.

Output:
    {
      "answer": str,
      "citations": [chunk_id, ...],
      "chunks": [...],          # final chunks used for the answer
      "trace": {                # for debugging/inspection
        "intent": ...,
        "iters": [{"query":..., "n_retrieved":..., "reflect":...}, ...],
        "stopped_by": "answered" | "max_iters" | "no_evidence",
      }
    }
"""

from __future__ import annotations

from .. import config as cfg
from ..retrieve.format import format_evidence
from ..retrieve.hybrid import hybrid_search
from ..retrieve.rerank import rerank
from ..utils.logger import get_logger
from . import abstain as abstain_mod
from .citation_check import detect_suspicious_citations, validate_citations
from .intent_classifier import classify
from .llm import chat
from .query_rewrite import rewrite
from .reflect import reflect

log = get_logger("rag.qa_agentic")

_SYSTEM = (
    "You are a careful academic research assistant. Answer ONLY using the "
    "evidence chunks provided. After each factual statement, cite the chunk "
    "with [chunk:<chunk_id>]. NEVER use [1], [2], or (Author 2020) style "
    "citations — they will be considered hallucinated. Keep the answer "
    "concise: at most 200 words, dense and informative, no padding. If the "
    "evidence is insufficient, say so explicitly. Do NOT fabricate paper "
    "titles, numbers, authors, or years."
)

_EMPTY_SUSPICIOUS: dict = {"numeric": [], "author_year": [], "count": 0}


# ---------------------------------------------------------------------------
# Stage helpers — each stage is independently unit-testable.
# ---------------------------------------------------------------------------


def _retrieve_round(query: str, paper_ids: list[str] | None, top_k: int) -> list[dict]:
    """One round: hybrid search across rewritten queries -> rerank."""
    rw = rewrite(query)
    pooled: dict[str, dict] = {}
    for q in rw["dense_queries"]:
        for hit in hybrid_search(q, top_k=top_k, paper_ids=paper_ids):
            cid = hit.get("chunk_id")
            if not cid:
                continue
            if cid not in pooled or hit.get("score_rrf", 0) > pooled[cid].get("score_rrf", 0):
                pooled[cid] = hit
    candidates = list(pooled.values())
    candidates.sort(key=lambda x: x.get("score_rrf", 0), reverse=True)
    candidates = candidates[: top_k * 3]
    return rerank(query, candidates, top_k=top_k)


def _maybe_rewrite_with_history(question: str, conversation_id: str | None) -> str:
    """If the request belongs to a multi-turn conversation, fold the history
    into a self-contained question. Failures are non-fatal."""
    if not conversation_id:
        return question
    try:
        from . import history

        rewritten = history.rewrite_with_history(question, conversation_id)
        if rewritten != question:
            log.info(f"history rewrite: {question!r} -> {rewritten!r}")
        return rewritten
    except Exception as e:  # noqa: BLE001 — history layer must never break QA
        log.warning(f"history rewrite failed (non-fatal): {e}")
        return question


def _check_cache(question: str, paper_ids: list[str] | None, trace_id: str) -> dict | None:
    """qa_cache short-circuit. Returns the cached response (already shaped
    for the public ``answer`` contract) or None if no hit."""
    try:
        from ..observability import counter
        from . import qa_cache

        cached = qa_cache.get(question, paper_ids)
    except Exception as e:  # noqa: BLE001
        log.warning(f"qa_cache get failed (non-fatal): {e}")
        return None
    if cached is None:
        return None
    counter("paper_rag_qa_total", {"stop": "cache_hit"}).inc()
    return {
        "answer": cached.get("answer", ""),
        "citations": cached.get("citations", []),
        "chunks": [],  # not re-fetched; chunk_ids preserved in trace
        "suspicious_citations": cached.get("suspicious_citations", _EMPTY_SUSPICIOUS),
        "trace": {
            **(cached.get("trace") or {}),
            "from_cache": True,
            "trace_id": trace_id,
            "cached_chunk_ids": cached.get("chunk_ids", []),
        },
    }


def _retrieve_loop(
    question: str,
    paper_ids: list[str] | None,
    top_k: int,
    max_iter: int,
    enable_reflect: bool,
) -> tuple[dict[str, dict], list[dict], str]:
    """Run up to ``max_iter`` rounds of retrieve+reflect.

    Returns (all_chunks, trace, stopped_by).
    """
    all_chunks: dict[str, dict] = {}
    trace: list[dict] = []
    current_query = question
    stopped = "max_iters"

    for it in range(max_iter):
        chunks = _retrieve_round(current_query, paper_ids, top_k)
        for ch in chunks:
            cid = ch.get("chunk_id")
            if cid and cid not in all_chunks:
                all_chunks[cid] = ch

        if not chunks:
            trace.append({"query": current_query, "n_retrieved": 0, "reflect": None})
            stopped = "no_evidence"
            break

        if enable_reflect and it < max_iter - 1:
            r = reflect(question, format_evidence(chunks))
            trace.append({"query": current_query, "n_retrieved": len(chunks), "reflect": r})
            if r["sufficiency"] == "sufficient":
                stopped = "answered"
                break
            if r["follow_up"]:
                current_query = r["follow_up"]
                continue
            stopped = "answered"
            break

        trace.append({"query": current_query, "n_retrieved": len(chunks), "reflect": None})
        stopped = "answered"
        break

    return all_chunks, trace, stopped


def _no_chunks_response(intent_cfg: dict, trace: list[dict], stopped: str, trace_id: str) -> dict:
    """Final response when retrieve produced zero usable chunks."""
    from ..observability import counter

    counter("paper_rag_qa_total", {"intent": intent_cfg["intent"], "stop": "no_chunks"}).inc()
    counter("paper_rag_qa_degraded_total", {"reason": "no_chunks"}).inc()
    counter("paper_rag_qa_abstain_total", {"decision": abstain_mod.DECISION_NO_CHUNKS}).inc()
    return {
        "answer": "(no evidence found in the indexed papers)",
        "citations": [],
        "chunks": [],
        "suspicious_citations": _EMPTY_SUSPICIOUS,
        "trace": {
            "intent": intent_cfg,
            "iters": trace,
            "stopped_by": stopped,
            "degraded": "no_chunks",
            "abstain": {
                "decision": abstain_mod.DECISION_NO_CHUNKS,
                "evidence_score": 0.0,
                "n_chunks": 0,
            },
            "trace_id": trace_id,
        },
    }


def _decide_abstain(final_chunks: list[dict], abstain_cfg) -> dict:
    """Run abstain.decide and emit the matching counters/log line."""
    from ..observability import counter

    result = abstain_mod.decide(
        final_chunks,
        enabled=abstain_cfg.enabled,
        threshold_low=abstain_cfg.threshold_low,
        threshold_high=abstain_cfg.threshold_high,
        min_chunks=abstain_cfg.min_chunks,
    )
    counter("paper_rag_qa_abstain_total", {"decision": result["decision"]}).inc()
    if result.get("signal_quality") == "low_degraded":
        counter("paper_rag_qa_degraded_total", {"reason": "abstain_low_quality_signal"}).inc()
    log.info(
        f"abstain decision: {result['decision']} "
        f"score={result['evidence_score']:.3f} "
        f"top={result['top_chunk_score']:.3f} "
        f"field={result['score_field']} "
        f"quality={result.get('signal_quality')} "
        f"n={result['n_chunks']}"
    )
    return result


def _no_evidence_response(
    intent_cfg: dict,
    trace: list[dict],
    abstain_result: dict,
    abstain_cfg,
    final_chunks: list[dict],
    trace_id: str,
) -> dict:
    """Skip the LLM entirely when abstain says no_evidence."""
    from ..observability import counter

    counter(
        "paper_rag_qa_total",
        {"intent": intent_cfg["intent"], "stop": "no_evidence_abstain"},
    ).inc()
    return {
        "answer": abstain_cfg.no_evidence_message,
        "citations": [],
        "chunks": final_chunks,  # still return chunks for inspection / debug
        "suspicious_citations": _EMPTY_SUSPICIOUS,
        "trace": {
            "intent": intent_cfg,
            "iters": trace,
            "stopped_by": "no_evidence_abstain",
            "abstain": abstain_result,
            "trace_id": trace_id,
        },
    }


def _build_user_prompt(question: str, final_chunks: list[dict], abstain_result: dict) -> str:
    evidence = format_evidence(final_chunks)
    user = (
        f"Question: {question}\n\nEvidence:\n{evidence}\n\n"
        "Answer (use ONLY [chunk:<id>] citations, never [1] or (Author 2020)):"
    )
    if abstain_result["decision"] == abstain_mod.DECISION_WEAK:
        # Inject explicit insufficiency hint — the LLM may still answer, but
        # is told to flag uncertainty rather than hallucinate citations.
        user += abstain_mod.WEAK_EVIDENCE_HINT
    return user


def _chat_failed_response(
    intent_cfg: dict,
    trace: list[dict],
    stopped: str,
    final_chunks: list[dict],
    trace_id: str,
    err: Exception,
) -> dict:
    from ..observability import counter

    counter(
        "paper_rag_qa_total",
        {"intent": intent_cfg["intent"], "stop": "chat_error"},
    ).inc()
    counter("paper_rag_qa_degraded_total", {"reason": "chat_error"}).inc()
    return {
        "answer": "(LLM unavailable; see chunks for evidence)",
        "citations": [],
        "chunks": final_chunks,
        "suspicious_citations": _EMPTY_SUSPICIOUS,
        "trace": {
            "intent": intent_cfg,
            "iters": trace,
            "stopped_by": stopped,
            "degraded": f"chat_error:{type(err).__name__}",
            "trace_id": trace_id,
        },
    }


def _store_in_cache(question: str, paper_ids: list[str] | None, out: dict) -> None:
    try:
        from . import qa_cache

        qa_cache.put(question, paper_ids, out)
    except Exception as e:  # noqa: BLE001
        log.warning(f"qa_cache put failed (non-fatal): {e}")


def _persist_history(
    conversation_id: str | None, question: str, out: dict
) -> None:
    if not conversation_id:
        return
    try:
        from . import history

        history.append(
            conversation_id,
            question,
            out.get("answer", ""),
            out.get("citations", []),
        )
    except Exception as e:  # noqa: BLE001
        log.warning(f"history.append failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def answer(
    question: str,
    *,
    paper_ids: list[str] | None = None,
    conversation_id: str | None = None,
) -> dict:
    from ..observability import histogram, new_trace_id

    trace_id = new_trace_id()
    timer = histogram("paper_rag_qa_latency_seconds")
    with timer.time():
        out = _answer_impl(
            question,
            paper_ids=paper_ids,
            trace_id=trace_id,
            conversation_id=conversation_id,
        )
    _persist_history(conversation_id, question, out)
    return out


def _answer_impl(
    question: str,
    *,
    paper_ids: list[str] | None,
    trace_id: str,
    conversation_id: str | None = None,
) -> dict:
    from ..observability import counter

    # Stage 1 — fold conversation history into a self-contained question.
    question = _maybe_rewrite_with_history(question, conversation_id)

    # Stage 2 — qa_cache short-circuit.
    cached = _check_cache(question, paper_ids, trace_id)
    if cached is not None:
        return cached

    # Stage 3 — pick intent + retrieve loop.
    c = cfg.load().rag
    intent_cfg = classify(question)
    max_iter = min(intent_cfg["max_iter"], c.max_inner_iters)
    top_k = intent_cfg["top_k"]
    all_chunks, trace, stopped = _retrieve_loop(
        question,
        paper_ids,
        top_k,
        max_iter,
        enable_reflect=c.enable_reflect,
    )

    # Stage 4 — short-circuit if retrieve produced nothing.
    final_chunks = list(all_chunks.values())[: top_k * 2]
    if not final_chunks:
        return _no_chunks_response(intent_cfg, trace, stopped, trace_id)

    # Stage 5 — abstain decision (after retrieve, before LLM, see ADR-0014).
    abstain_cfg = c.abstain
    abstain_result = _decide_abstain(final_chunks, abstain_cfg)
    if abstain_result["decision"] == abstain_mod.DECISION_NO_EVIDENCE:
        return _no_evidence_response(
            intent_cfg, trace, abstain_result, abstain_cfg, final_chunks, trace_id
        )

    # Stage 6 — LLM call + citation cleanup.
    user = _build_user_prompt(question, final_chunks, abstain_result)
    try:
        raw = chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=1024,
        )
    except Exception as e:  # noqa: BLE001 — fall back to evidence-only response
        log.warning(f"chat failed, returning evidence-only: {e}")
        return _chat_failed_response(intent_cfg, trace, stopped, final_chunks, trace_id, e)

    cleaned, valid = validate_citations(raw, final_chunks)
    suspicious = detect_suspicious_citations(cleaned)
    if suspicious["count"]:
        log.warning(f"suspicious citations detected: {suspicious}")
    log.info(
        f"qa_agentic done: trace_id={trace_id} iters={len(trace)} "
        f"stop={stopped} cites={len(valid)}"
    )
    counter("paper_rag_qa_total", {"intent": intent_cfg["intent"], "stop": stopped}).inc()
    counter("paper_rag_qa_citations_total").inc(len(valid))
    if suspicious["count"]:
        counter("paper_rag_qa_suspicious_total").inc(suspicious["count"])

    out = {
        "answer": cleaned,
        "citations": valid,
        "chunks": final_chunks,
        "suspicious_citations": suspicious,
        "trace": {
            "intent": intent_cfg,
            "iters": trace,
            "stopped_by": stopped,
            "abstain": abstain_result,
            "trace_id": trace_id,
        },
    }
    _store_in_cache(question, paper_ids, out)
    return out
