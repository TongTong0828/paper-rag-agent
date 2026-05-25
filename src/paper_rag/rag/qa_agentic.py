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


def answer(question: str, *, paper_ids: list[str] | None = None,
           conversation_id: str | None = None) -> dict:
    from ..observability import histogram, new_trace_id

    trace_id = new_trace_id()
    timer = histogram("paper_rag_qa_latency_seconds")
    with timer.time():
        out = _answer_impl(question, paper_ids=paper_ids, trace_id=trace_id,
                           conversation_id=conversation_id)
    # Persist for multi-turn (no-op if conversation_id None)
    if conversation_id:
        try:
            from . import history

            history.append(conversation_id, question, out.get("answer", ""),
                           out.get("citations", []))
        except Exception as e:
            log.warning(f"history.append failed (non-fatal): {e}")
    return out


def _answer_impl(question: str, *, paper_ids: list[str] | None, trace_id: str,
                 conversation_id: str | None = None) -> dict:
    from ..observability import counter

    # Multi-turn: rewrite follow-up into self-contained question
    original_question = question
    if conversation_id:
        try:
            from . import history

            question = history.rewrite_with_history(question, conversation_id)
            if question != original_question:
                log.info(f"history rewrite: {original_question!r} -> {question!r}")
        except Exception as e:
            log.warning(f"history rewrite failed (non-fatal): {e}")

    # qa_cache short-circuit (no-op if disabled in config)
    try:
        from . import qa_cache

        cached = qa_cache.get(question, paper_ids)
        if cached is not None:
            counter("paper_rag_qa_total", {"stop": "cache_hit"}).inc()
            return {
                "answer": cached.get("answer", ""),
                "citations": cached.get("citations", []),
                "chunks": [],  # not re-fetched; chunk_ids preserved in trace
                "suspicious_citations": cached.get("suspicious_citations", {"numeric": [], "author_year": [], "count": 0}),
                "trace": {**(cached.get("trace") or {}), "from_cache": True, "trace_id": trace_id, "cached_chunk_ids": cached.get("chunk_ids", [])},
            }
    except Exception as e:
        log.warning(f"qa_cache get failed (non-fatal): {e}")
    c = cfg.load().rag
    intent_cfg = classify(question)
    max_iter = min(intent_cfg["max_iter"], c.max_inner_iters)
    top_k = intent_cfg["top_k"]

    trace: list[dict] = []
    all_chunks: dict[str, dict] = {}
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

        if c.enable_reflect and it < max_iter - 1:
            evidence_str = format_evidence(chunks)
            r = reflect(question, evidence_str)
            trace.append({"query": current_query, "n_retrieved": len(chunks), "reflect": r})
            if r["sufficiency"] == "sufficient":
                stopped = "answered"
                break
            if r["follow_up"]:
                current_query = r["follow_up"]
                continue
            stopped = "answered"
            break
        else:
            trace.append({"query": current_query, "n_retrieved": len(chunks), "reflect": None})
            stopped = "answered"
            break

    final_chunks = list(all_chunks.values())[: top_k * 2]
    if not final_chunks:
        counter("paper_rag_qa_total", {"intent": intent_cfg["intent"], "stop": "no_chunks"}).inc()
        counter("paper_rag_qa_degraded_total", {"reason": "no_chunks"}).inc()
        counter("paper_rag_qa_abstain_total", {"decision": abstain_mod.DECISION_NO_CHUNKS}).inc()
        return {
            "answer": "(no evidence found in the indexed papers)",
            "citations": [],
            "chunks": [],
            "suspicious_citations": {"numeric": [], "author_year": [], "count": 0},
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

    # === ADR-0014 abstain decision (after retrieve, before LLM) ===
    abstain_cfg = c.abstain
    abstain_result = abstain_mod.decide(
        final_chunks,
        enabled=abstain_cfg.enabled,
        threshold_low=abstain_cfg.threshold_low,
        threshold_high=abstain_cfg.threshold_high,
        min_chunks=abstain_cfg.min_chunks,
    )
    counter("paper_rag_qa_abstain_total", {"decision": abstain_result["decision"]}).inc()
    if abstain_result.get("signal_quality") == "low_degraded":
        # Surface degraded retrieval (dense down, only BM25/RRF available) so
        # ops can alert on it. Decision still falls through to confident.
        counter("paper_rag_qa_degraded_total", {"reason": "abstain_low_quality_signal"}).inc()
    log.info(
        f"abstain decision: {abstain_result['decision']} "
        f"score={abstain_result['evidence_score']:.3f} "
        f"top={abstain_result['top_chunk_score']:.3f} "
        f"field={abstain_result['score_field']} "
        f"quality={abstain_result.get('signal_quality')} "
        f"n={abstain_result['n_chunks']}"
    )

    # no_evidence: skip the LLM entirely — saves a call AND prevents the
    # n03-style "14 fabricated cites on weather question" failure mode.
    if abstain_result["decision"] == abstain_mod.DECISION_NO_EVIDENCE:
        counter("paper_rag_qa_total", {"intent": intent_cfg["intent"], "stop": "no_evidence_abstain"}).inc()
        return {
            "answer": abstain_cfg.no_evidence_message,
            "citations": [],
            "chunks": final_chunks,  # still return chunks for inspection / debug
            "suspicious_citations": {"numeric": [], "author_year": [], "count": 0},
            "trace": {
                "intent": intent_cfg,
                "iters": trace,
                "stopped_by": "no_evidence_abstain",
                "abstain": abstain_result,
                "trace_id": trace_id,
            },
        }

    evidence = format_evidence(final_chunks)
    user = f"Question: {question}\n\nEvidence:\n{evidence}\n\nAnswer (use ONLY [chunk:<id>] citations, never [1] or (Author 2020)):"
    if abstain_result["decision"] == abstain_mod.DECISION_WEAK:
        # Inject explicit insufficiency hint — the LLM may still answer, but
        # is told to flag uncertainty rather than hallucinate citations.
        user += abstain_mod.WEAK_EVIDENCE_HINT
    try:
        raw = chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=1024,
        )
    except Exception as e:
        log.warning(f"chat failed, returning evidence-only: {e}")
        counter("paper_rag_qa_total", {"intent": intent_cfg["intent"], "stop": "chat_error"}).inc()
        counter("paper_rag_qa_degraded_total", {"reason": "chat_error"}).inc()
        return {
            "answer": "(LLM unavailable; see chunks for evidence)",
            "citations": [],
            "chunks": final_chunks,
            "suspicious_citations": {"numeric": [], "author_year": [], "count": 0},
            "trace": {
                "intent": intent_cfg,
                "iters": trace,
                "stopped_by": stopped,
                "degraded": f"chat_error:{type(e).__name__}",
                "trace_id": trace_id,
            },
        }
    cleaned, valid = validate_citations(raw, final_chunks)
    suspicious = detect_suspicious_citations(cleaned)
    if suspicious["count"]:
        log.warning(f"suspicious citations detected: {suspicious}")
    log.info(f"qa_agentic done: trace_id={trace_id} iters={len(trace)} stop={stopped} cites={len(valid)}")
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
    try:
        from . import qa_cache

        qa_cache.put(question, paper_ids, out)
    except Exception as e:
        log.warning(f"qa_cache put failed (non-fatal): {e}")
    return out
