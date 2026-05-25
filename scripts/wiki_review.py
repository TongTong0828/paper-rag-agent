"""Run a wiki self-review pass.

Usage:
    python scripts/wiki_review.py                       # all entries
    python scripts/wiki_review.py --limit 20            # at most 20 entries
    python scripts/wiki_review.py --stale-days 7        # only entries not updated in N days
    python scripts/wiki_review.py --limit 5 --dry-run   # show what would be reviewed
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper_rag import config as cfg
from paper_rag.utils.logger import get_logger

log = get_logger("wiki_review")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Periodic wiki review.")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N entries (default: all)")
    p.add_argument("--stale-days", type=int, default=None,
                   help="Only process entries not updated in N days")
    p.add_argument("--dry-run", action="store_true",
                   help="List entries that would be reviewed; make no LLM calls")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cfg.load()
    if not cfg.load().wiki.enabled:
        log.warning("wiki.enabled=false in config; nothing to do")
        return 0

    from sqlmodel import Session, select

    from paper_rag.embed import bge_m3
    from paper_rag.store.sqlite_store import Chunk, get_engine
    from paper_rag.wiki import store as wstore
    from paper_rag.wiki.flow import patch_entry

    entries = wstore.list_all()
    log.info(f"loaded {len(entries)} entries")

    if args.stale_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=args.stale_days)
        entries = [e for e in entries if e.updated_at < cutoff]
        log.info(f"after stale-days filter (>{args.stale_days}d): {len(entries)}")

    # Process oldest first so review effort is spread evenly over time.
    entries.sort(key=lambda e: e.updated_at)
    if args.limit is not None:
        entries = entries[: args.limit]

    if args.dry_run:
        for e in entries:
            log.info(f"[dry-run] {e.entry_id} v{e.version} updated={e.updated_at.isoformat()}")
        return 0

    engine = get_engine()
    reviewed = updated = 0
    for e in entries:
        with Session(engine) as s:
            rows = list(
                s.exec(select(Chunk).where(Chunk.chunk_id.in_(e.evidence_chunks)))
            )
        chunks = [{"chunk_id": r.chunk_id, "text": r.text, "section": None} for r in rows][:10]
        if not chunks:
            continue
        reviewed += 1
        host_paper = e.key_papers[0] if e.key_papers else "review"
        patched = patch_entry(
            existing=e,
            paper_id=host_paper,
            paper_title="(periodic review)",
            chunks=chunks,
        )
        if patched:
            wstore.upsert_entry(patched, reason="periodic review")
            try:
                vec = bge_m3.encode_one(f"{patched.name}\n{patched.definition}")
                wstore.upsert_qdrant(patched, vec)
            except Exception as ex:
                log.warning(f"qdrant mirror skipped: {ex}")
            updated += 1
    log.info(f"review done: reviewed={reviewed} updated={updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
