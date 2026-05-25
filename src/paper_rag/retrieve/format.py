"""Format chunks into LLM-friendly evidence blocks."""

from __future__ import annotations


def format_evidence(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        head = f"[{i}] paper_id={c.get('paper_id')} section={c.get('section')} modality={c.get('modality')} score={c.get('score', 0):.3f}"
        body = (c.get("text") or "").strip()
        parts.append(f"{head}\nchunk_id={c.get('chunk_id')}\n{body}")
    return "\n\n---\n\n".join(parts)
