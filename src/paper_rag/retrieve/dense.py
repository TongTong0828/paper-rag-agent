"""Dense retrieval over the paper_chunks collection."""

from __future__ import annotations

from ..embed import bge_m3
from ..store import qdrant_store


def retrieve(query: str, top_k: int = 8, paper_ids: list[str] | None = None,
             modality: str | None = None) -> list[dict]:
    qvec = bge_m3.encode_one(query)
    return qdrant_store.search(qvec, top_k=top_k, paper_ids=paper_ids, modality=modality)
