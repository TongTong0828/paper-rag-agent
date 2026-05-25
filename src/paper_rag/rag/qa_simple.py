"""Simple RAG: dense retrieve -> compose prompt -> LLM answer -> citation check.

For phase 1; agentic variant (query rewrite + reflect + iterate) is in
qa_agentic.py (phase 2, TODO).
"""

from __future__ import annotations

from ..retrieve.dense import retrieve
from ..retrieve.format import format_evidence
from ..utils.logger import get_logger
from .citation_check import detect_suspicious_citations, validate_citations
from .llm import chat

log = get_logger("rag.qa_simple")

_SYSTEM = (
    "You are a careful academic research assistant. Answer ONLY using the "
    "evidence chunks provided. After each factual statement, cite the chunk "
    "with the format [chunk:<chunk_id>]. NEVER use [1], [2], or "
    "(Author 2020) style citations — they will be considered hallucinated. "
    "If the evidence is insufficient, say so explicitly. Do NOT fabricate "
    "paper titles or numbers."
)


def answer(question: str, *, top_k: int = 8, paper_ids: list[str] | None = None) -> dict:
    chunks = retrieve(question, top_k=top_k, paper_ids=paper_ids)
    if not chunks:
        return {
            "answer": "(no evidence found)",
            "citations": [],
            "chunks": [],
            "suspicious_citations": {"numeric": [], "author_year": [], "count": 0},
        }

    evidence = format_evidence(chunks)
    user = f"Question: {question}\n\nEvidence:\n{evidence}\n\nAnswer (with [chunk:<id>] citations only):"
    raw = chat([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}])
    cleaned, valid = validate_citations(raw, chunks)
    suspicious = detect_suspicious_citations(cleaned)
    if suspicious["count"]:
        log.warning(f"suspicious citations detected: {suspicious}")
    log.info(f"answer ok, citations valid={len(valid)} retrieved={len(chunks)}")
    return {
        "answer": cleaned,
        "citations": valid,
        "chunks": chunks,
        "suspicious_citations": suspicious,
    }
