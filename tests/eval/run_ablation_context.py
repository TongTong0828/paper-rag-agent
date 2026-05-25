"""Ablation runner: how much does context-prefix embedding help retrieval?

Compares:
  A) embed `context_text` (current default; includes "[Title: X] [Section: Y]" prefix)
  B) embed `text` (raw chunk only)

Both rebuild a temporary Qdrant collection so the run is reproducible. To
run a fair comparison the same set of papers must already exist in SQLite.

Usage:
    python tests/eval/run_ablation_context.py --file qa_set.jsonl --top-k 8

Output: a side-by-side table on paper_recall@k / mrr / chunk_recall@k.

This is intentionally heavyweight (rebuilds an index). Only run when
deciding whether to keep the prefix; not part of regular CI.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("--top-k", type=int, default=8)
    p.add_argument("--collection-suffix", default="ablation")
    return p.parse_args()


def _build_temp_collection(suffix: str, *, with_prefix: bool) -> str:
    """Re-embed all chunks from SQLite and upsert into a temp Qdrant collection."""
    from paper_rag import config as cfg
    from paper_rag.embed import bge_m3
    from paper_rag.store.qdrant_store import get_client
    from paper_rag.store.sqlite_store import Chunk, get_engine
    from sqlmodel import Session, select
    from qdrant_client.http import models as qm

    c = cfg.load()
    coll = f"{c.qdrant.collection_chunks}_{suffix}_{'ctx' if with_prefix else 'raw'}"
    client = get_client()
    existing = {col.name for col in client.get_collections().collections}
    if coll in existing:
        client.delete_collection(coll)
    client.create_collection(
        collection_name=coll,
        vectors_config=qm.VectorParams(
            size=c.embedding.dim,
            distance=qm.Distance.COSINE,
        ),
    )

    engine = get_engine()
    with Session(engine) as s:
        rows = list(s.exec(select(Chunk)))
    if not rows:
        raise RuntimeError("No chunks in SQLite; ingest some papers first.")

    texts = [(r.context_text if with_prefix else r.text) for r in rows]
    vectors = bge_m3.encode(texts)

    import hashlib

    points = []
    for r, vec in zip(rows, vectors):
        pid = int(hashlib.sha1(r.chunk_id.encode("utf-8")).hexdigest()[:16], 16)
        points.append(
            qm.PointStruct(
                id=pid,
                vector=vec,
                payload={
                    "chunk_id": r.chunk_id,
                    "paper_id": r.paper_id,
                    "modality": r.modality,
                    "text": r.text,
                },
            )
        )
    # Upsert in batches of 256 to play nice with grpc.
    for i in range(0, len(points), 256):
        client.upsert(collection_name=coll, points=points[i : i + 256], wait=True)
    return coll


def _search(coll: str, query: str, top_k: int) -> list[dict]:
    from paper_rag.embed import bge_m3
    from paper_rag.store.qdrant_store import get_client

    client = get_client()
    qvec = bge_m3.encode_one(query)
    res = client.search(collection_name=coll, query_vector=qvec, limit=top_k, with_payload=True)
    return [dict(hit.payload or {}, score=float(hit.score)) for hit in res]


def main() -> int:
    from eval.loader import load_jsonl
    from eval.metrics import mrr, recall_at_k

    args = parse_args()
    items = load_jsonl(args.file)
    print(f"loaded {len(items)} items")

    print("\nbuilding temp collection: with prefix...")
    t0 = time.time()
    coll_ctx = _build_temp_collection(args.collection_suffix, with_prefix=True)
    print(f"  done in {time.time()-t0:.1f}s -> {coll_ctx}")

    print("building temp collection: raw text...")
    t0 = time.time()
    coll_raw = _build_temp_collection(args.collection_suffix, with_prefix=False)
    print(f"  done in {time.time()-t0:.1f}s -> {coll_raw}")

    rows: list[dict] = []
    for it in items:
        ctx_chunks = _search(coll_ctx, it.question, args.top_k)
        raw_chunks = _search(coll_raw, it.question, args.top_k)

        ctx_papers = [c["paper_id"] for c in ctx_chunks]
        raw_papers = [c["paper_id"] for c in raw_chunks]

        rows.append({
            "qid": it.qid,
            "ctx_recall": recall_at_k(ctx_papers, it.relevant_paper_ids, args.top_k),
            "raw_recall": recall_at_k(raw_papers, it.relevant_paper_ids, args.top_k),
            "ctx_mrr": mrr(ctx_papers, it.relevant_paper_ids),
            "raw_mrr": mrr(raw_papers, it.relevant_paper_ids),
        })
        print(
            f"  {it.qid}: recall ctx={rows[-1]['ctx_recall']:.2f} raw={rows[-1]['raw_recall']:.2f} | "
            f"mrr ctx={rows[-1]['ctx_mrr']:.2f} raw={rows[-1]['raw_mrr']:.2f}"
        )

    print("\n=== AGGREGATE ===")
    print(f"  paper_recall@k  ctx={mean(r['ctx_recall'] for r in rows):.3f}  "
          f"raw={mean(r['raw_recall'] for r in rows):.3f}")
    print(f"  paper_mrr       ctx={mean(r['ctx_mrr'] for r in rows):.3f}  "
          f"raw={mean(r['raw_mrr'] for r in rows):.3f}")

    print(
        "\nKeep context prefix iff 'ctx' wins on BOTH recall and mrr by ≥ 0.02. "
        "Otherwise drop it (saves embedding cost on rebuild)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
