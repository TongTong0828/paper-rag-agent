"""Initialize SQLite tables and Qdrant collections.

Run once before any ingest:
    python scripts/init_store.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper_rag import config as cfg
from paper_rag.utils.logger import get_logger
from paper_rag.utils.paths import ensure_dirs

log = get_logger("init_store")


def init_qdrant() -> None:
    from qdrant_client.http import models as qm

    from paper_rag.store.qdrant_store import get_client

    c = cfg.load()
    client = get_client()  # respects qdrant.local_path / url
    distance = qm.Distance.COSINE if c.qdrant.distance.lower() == "cosine" else qm.Distance.DOT

    for name in (c.qdrant.collection_chunks, c.qdrant.collection_wiki):
        existing = {col.name for col in client.get_collections().collections}
        if name in existing:
            log.info(f"Qdrant collection already exists: {name}")
            continue
        client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=c.embedding.dim, distance=distance),
        )
        log.info(f"Created Qdrant collection: {name} (dim={c.embedding.dim})")


def init_sqlite() -> None:
    """SQLite schema is created lazily by sqlmodel; keep this as a no-op for now.

    When src/store/sqlite_store.py is implemented, import its `create_all()` here.
    """
    c = cfg.load()
    Path(c.paths.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    log.info(f"SQLite path ready: {c.paths.sqlite_path}")


def main() -> None:
    cfg.load()
    ensure_dirs()
    init_sqlite()
    init_qdrant()
    log.info("init_store done.")


if __name__ == "__main__":
    main()
