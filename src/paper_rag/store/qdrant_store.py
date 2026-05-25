"""Qdrant adapter (paper_chunks collection)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from .. import config as cfg
from ..utils.logger import get_logger

log = get_logger("store.qdrant")

_CLIENT = None


def get_client():
    """Return a Qdrant client.

    Resolution:
      1. If `qdrant.url` looks like an HTTP(S) endpoint, use server mode.
      2. Else if `qdrant.local_path` is set OR url starts with "file://" or
         "local://", use embedded mode (single-process, no docker).
    """
    global _CLIENT
    if _CLIENT is None:
        from qdrant_client import QdrantClient

        c = cfg.load().qdrant
        local_path = getattr(c, "local_path", None)
        url = c.url or ""
        if local_path:
            log.info(f"qdrant client (local path) at {local_path}")
            _CLIENT = QdrantClient(path=local_path)
        elif url.startswith(("file://", "local://")):
            path = url.split("://", 1)[1]
            log.info(f"qdrant client (local path) at {path}")
            _CLIENT = QdrantClient(path=path)
        else:
            log.info(f"qdrant client (server) at {url}")
            _CLIENT = QdrantClient(url=url)
    return _CLIENT


def _stable_point_id(chunk_id: str) -> int:
    """Qdrant point id must be int or UUID; use first 16 hex of sha1 -> int."""
    h = hashlib.sha1(chunk_id.encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def upsert_chunks(items: Iterable[dict[str, Any]], vectors: list[list[float]]) -> int:
    """Upsert chunks. items[i] must contain 'chunk_id' and metadata fields.

    Each vector pairs with items at the same index.
    """
    from qdrant_client.http import models as qm

    items = list(items)
    if len(items) != len(vectors):
        raise ValueError(f"items({len(items)}) and vectors({len(vectors)}) must align")

    client = get_client()
    coll = cfg.load().qdrant.collection_chunks
    points = []
    for it, vec in zip(items, vectors):
        pid = _stable_point_id(it["chunk_id"])
        payload = {k: v for k, v in it.items() if k != "vector"}
        points.append(qm.PointStruct(id=pid, vector=vec, payload=payload))
    client.upsert(collection_name=coll, points=points, wait=True)
    log.info(f"qdrant upsert {len(points)} -> {coll}")
    return len(points)


def search(query_vec: list[float], top_k: int = 8, paper_ids: list[str] | None = None,
           modality: str | None = None) -> list[dict]:
    """Vector search with metadata filters.

    Returns empty list (graceful degrade) when Qdrant is unreachable or
    returns an error. Caller should treat this as `no chunks` rather than
    propagating the exception.
    """
    from qdrant_client.http import models as qm

    must: list = []
    if paper_ids:
        must.append(qm.FieldCondition(key="paper_id", match=qm.MatchAny(any=paper_ids)))
    if modality:
        must.append(qm.FieldCondition(key="modality", match=qm.MatchValue(value=modality)))
    flt = qm.Filter(must=must) if must else None

    try:
        client = get_client()
        coll = cfg.load().qdrant.collection_chunks
        # qdrant-client >= 1.10 prefers query_points; older has search.
        if hasattr(client, "query_points"):
            qres = client.query_points(
                collection_name=coll,
                query=query_vec,
                query_filter=flt,
                limit=top_k,
                with_payload=True,
            )
            res = qres.points if hasattr(qres, "points") else qres
        else:
            res = client.search(
                collection_name=coll,
                query_vector=query_vec,
                query_filter=flt,
                limit=top_k,
                with_payload=True,
            )
    except Exception as e:
        log.warning(f"qdrant search degraded (returning []): {type(e).__name__}: {e}")
        return []

    out = []
    for hit in res:
        d = dict(hit.payload or {})
        d["score"] = float(hit.score)
        out.append(d)
    return out
