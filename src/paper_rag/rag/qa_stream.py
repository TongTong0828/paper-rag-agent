"""Streaming variant of qa_agentic.

Yields events as the pipeline progresses, so callers (e.g. DeerFlow lead
agent or a chat UI) can render incrementally instead of waiting for the
full ~2-min answer.

Event types:
    {"event": "intent",     "data": {"intent": "factual", "top_k": 5, ...}}
    {"event": "rewrite",    "data": {"queries": [...], "keywords": "..."}}
    {"event": "retrieved",  "data": {"iter": 0, "n_chunks": 7}}
    {"event": "reflect",    "data": {"sufficiency": "sufficient", ...}}
    {"event": "abstain",    "data": {"decision": "confident|weak|no_evidence|...", ...}}
    {"event": "answer_chunk","data": {"text": "..."}}
    {"event": "done",       "data": {"citations": [...], "suspicious": {...}, "abstain": {...}}}
    {"event": "error",      "data": {"message": "..."}}

Same hard caps as qa_agentic (max_inner_iters / max_inner_tokens).
"""

from __future__ import annotations

from typing import Generator

from .. import config as cfg
from ..retrieve.format import format_evidence
from ..retrieve.hybrid import hybrid_search
from ..retrieve.rerank import rerank
from ..utils.logger import get_logger
from . import abstain as abstain_mod
from .citation_check import detect_suspicious_citations, validate_citations
from .intent_classifier import classify
from .query_rewrite import rewrite
from .reflect import reflect


log = get_logger("rag.qa_stream")

_SYSTEM = (
    "You are a careful academic research assistant. Answer ONLY using the "
    "evidence chunks provided. After each factual statement, cite the chunk "
    "with [chunk:<chunk_id>]. NEVER use [1], [2], or (Author 2020) style "
    "citations. Keep the answer concise (≤200 words). If insufficient "
    "evidence, say so explicitly."
)


def _retrieve_round(query: str, paper_ids, top_k: int) -> list[dict]:
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
    return rerank(query, candidates, top_k=top_k), rw


def stream_answer(question: str, *, paper_ids: list[str] | None = None) -> Generator[dict, None, None]:
    """Yield events as the agentic pipeline progresses."""
    c = cfg.load().rag
    intent_cfg = classify(question)
    yield {"event": "intent", "data": intent_cfg}

    max_iter = min(intent_cfg["max_iter"], c.max_inner_iters)
    top_k = intent_cfg["top_k"]
    all_chunks: dict[str, dict] = {}
    current_query = question

    for it in range(max_iter):
        try:
            chunks, rw = _retrieve_round(current_query, paper_ids, top_k)
        except Exception as e:
            yield {"event": "error", "data": {"message": f"retrieve failed: {e}"}}
            return
        if it == 0:
            yield {"event": "rewrite", "data": {
                "queries": rw.get("dense_queries", []),
                "keywords": rw.get("bm25_query", ""),
            }}
        for ch in chunks:
            cid = ch.get("chunk_id")
            if cid and cid not in all_chunks:
                all_chunks[cid] = ch
        yield {"event": "retrieved", "data": {"iter": it, "n_chunks": len(chunks)}}

        if not chunks:
            break
        if c.enable_reflect and it < max_iter - 1:
            r = reflect(question, format_evidence(chunks))
            yield {"event": "reflect", "data": r}
            if r["sufficiency"] == "sufficient":
                break
            if r["follow_up"]:
                current_query = r["follow_up"]
                continue
            break
        else:
            break

    final_chunks = list(all_chunks.values())[: top_k * 2]
    if not final_chunks:
        yield {"event": "done", "data": {
            "answer": "(no evidence found)",
            "citations": [],
            "suspicious": {"count": 0},
            "degraded": "no_chunks",
            "abstain": {"decision": abstain_mod.DECISION_NO_CHUNKS},
        }}
        return

    # === ADR-0014 abstain decision ===
    abstain_cfg = c.abstain
    abstain_result = abstain_mod.decide(
        final_chunks,
        enabled=abstain_cfg.enabled,
        threshold_low=abstain_cfg.threshold_low,
        threshold_high=abstain_cfg.threshold_high,
        min_chunks=abstain_cfg.min_chunks,
    )
    yield {"event": "abstain", "data": abstain_result}

    if abstain_result["decision"] == abstain_mod.DECISION_NO_EVIDENCE:
        # Skip the LLM stream entirely.
        yield {"event": "answer_chunk", "data": {"text": abstain_cfg.no_evidence_message}}
        yield {"event": "done", "data": {
            "answer": abstain_cfg.no_evidence_message,
            "citations": [],
            "suspicious": {"count": 0},
            "abstain": abstain_result,
            "n_chunks": len(final_chunks),
        }}
        return

    # Stream the answer token by token.
    user = (
        f"Question: {question}\n\nEvidence:\n{format_evidence(final_chunks)}\n\n"
        f"Answer (use ONLY [chunk:<id>] citations, ≤200 words):"
    )
    if abstain_result["decision"] == abstain_mod.DECISION_WEAK:
        user += abstain_mod.WEAK_EVIDENCE_HINT
    full = ""
    try:
        for tok in _stream_chat(_SYSTEM, user):
            full += tok
            yield {"event": "answer_chunk", "data": {"text": tok}}
    except Exception as e:
        yield {"event": "error", "data": {"message": f"chat stream failed: {e}"}}
        return

    cleaned, valid = validate_citations(full, final_chunks)
    paper_ids_used = sorted({c.get("paper_id") for c in final_chunks if c.get("paper_id")})
    yield {"event": "done", "data": {
        "answer": cleaned,
        "citations": valid,
        "suspicious": detect_suspicious_citations(cleaned),
        "abstain": abstain_result,
        "n_chunks": len(final_chunks),
        "paper_ids": paper_ids_used,
    }}


def _stream_chat(system: str, user: str):
    """Yield string tokens from the OpenAI-compatible streaming endpoint."""
    c = cfg.load().llm
    if not (c.base_url and c.api_key):
        raise RuntimeError("LLM config missing")
    from openai import OpenAI

    client = OpenAI(base_url=c.base_url, api_key=c.api_key)
    chosen = c.chat_model
    if not chosen:
        raise RuntimeError("CHAT_MODEL not set")
    resp = client.chat.completions.create(
        model=chosen,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=600,
        stream=True,
    )
    for chunk in resp:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and getattr(delta, "content", None):
            yield delta.content
