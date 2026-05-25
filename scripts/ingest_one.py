"""Ingest a single paper end-to-end.

Usage:
    python scripts/ingest_one.py --arxiv 2310.12345
    python scripts/ingest_one.py --pdf /path/to/paper.pdf --title "..."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper_rag import config as cfg
from paper_rag.utils.logger import get_logger
from paper_rag.utils.paths import ensure_dirs

log = get_logger("ingest_one")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest a single paper.")
    p.add_argument("--arxiv", help="arXiv id, e.g. 2310.12345")
    p.add_argument("--pdf", help="Local PDF path")
    p.add_argument("--title", help="Title (used when --pdf without metadata)")
    p.add_argument("--force", action="store_true", help="Re-ingest even if status=done")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not (args.arxiv or args.pdf):
        log.error("Need one of --arxiv / --pdf")
        return 2

    cfg.load()
    ensure_dirs()

    if args.arxiv:
        from paper_rag.ingest.arxiv_source import ArxivSource

        result = ArxivSource().fetch(args.arxiv)
    else:
        from paper_rag.ingest.local_source import LocalSource

        result = LocalSource(title=args.title).fetch(args.pdf)

    log.info(f"fetched: {result.meta.paper_id} title={result.meta.title!r}")

    from paper_rag.store.ingest_pipeline import ingest

    out = ingest(result, force=args.force)
    log.info(f"ingest result: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
