"""BM25 sparse retrieval over chunk text.

Index built lazily from SQLite chunks; persisted to `bm25.pkl` and rebuilt
on demand (e.g. after batch ingest). Cheap for <100k chunks.
"""

from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from pathlib import Path

from .. import config as cfg
from ..utils.logger import get_logger


log = get_logger("retrieve.bm25")

_INDEX = None


@dataclass
class _Index:
    bm25: object  # rank_bm25.BM25Okapi
    chunk_ids: list[str]
    payloads: list[dict]


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def build_index(force: bool = False) -> _Index:
    global _INDEX
    if _INDEX is not None and not force:
        return _INDEX

    from rank_bm25 import BM25Okapi  # lazy import

    from ..store.sqlite_store import Chunk, get_engine
    from sqlmodel import Session, select

    engine = get_engine()
    with Session(engine) as s:
        rows: list[Chunk] = list(s.exec(select(Chunk)))

    chunk_ids: list[str] = []
    corpus: list[list[str]] = []
    payloads: list[dict] = []
    for r in rows:
        chunk_ids.append(r.chunk_id)
        corpus.append(_tokenize(r.text))
        payloads.append(
            {
                "chunk_id": r.chunk_id,
                "paper_id": r.paper_id,
                "section": None,
                "modality": r.modality,
                "page": r.page,
                "text": r.text,
                "title": None,
            }
        )

    if not corpus:
        log.warning("bm25: empty corpus")
        bm25 = None
    else:
        bm25 = BM25Okapi(corpus)
    _INDEX = _Index(bm25=bm25, chunk_ids=chunk_ids, payloads=payloads)

    bm25_path = Path(cfg.load().paths.bm25_path)
    bm25_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(bm25_path, "wb") as f:
            pickle.dump(_INDEX, f)
        log.info(f"bm25 index built (n={len(chunk_ids)}) -> {bm25_path}")
    except Exception as e:
        log.warning(f"bm25 persist failed: {e}")
    return _INDEX


def search(query: str, top_k: int = 20, paper_ids: list[str] | None = None) -> list[dict]:
    """BM25 search with optional paper_id pre-filter.

    When `paper_ids` is given, we score the FULL corpus first, then filter,
    then truncate to `top_k`. This is correct (BM25 score is independent
    of filtering) and avoids the bug where post-filtering N=20 yields 0
    hits because all top-20 belong to other papers.

    For very large corpora (>100k chunks) consider migrating to FTS5 (#7).
    """
    idx = build_index(force=False)
    if idx.bm25 is None or not idx.chunk_ids:
        return []
    scores = idx.bm25.get_scores(_tokenize(query))

    if paper_ids:
        allowed = set(paper_ids)
        order = [
            i for i in sorted(range(len(scores)), key=lambda j: scores[j], reverse=True)
            if idx.payloads[i].get("paper_id") in allowed
        ][:top_k]
    else:
        # Take a generous candidate pool then truncate; argsort is O(n log n)
        # but with n=10k it's still <50ms.
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

    out = []
    for rank, i in enumerate(order):
        d = dict(idx.payloads[i])
        d["score_bm25"] = float(scores[i])
        d["rank_bm25"] = rank
        out.append(d)
    return out


def invalidate() -> None:
    """Force next call to rebuild from SQLite (after ingest)."""
    global _INDEX
    _INDEX = None
