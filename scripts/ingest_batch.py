"""Batch ingest from a list file.

Each line: one of `arxiv:<id>` / `doi:<doi>` / `s2:<paperId>` / `url:<...>`
or a bare arxiv id like `2310.12345`.

Usage:
    python scripts/ingest_batch.py --file ids.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paper_rag import config as cfg
from paper_rag.utils.logger import get_logger
from paper_rag.utils.paths import ensure_dirs

log = get_logger("ingest_batch")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Batch ingest papers.")
    p.add_argument("--file", required=True, help="Plain text file, one id per line")
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def _route(identifier: str):
    iden = identifier.strip()
    if iden.startswith("arxiv:") or _looks_like_arxiv(iden):
        from paper_rag.ingest.arxiv_source import ArxivSource

        return ArxivSource(), iden.removeprefix("arxiv:")
    if iden.startswith("doi:"):
        from paper_rag.ingest.semantic_scholar_source import SemanticScholarSource

        return SemanticScholarSource(), iden
    if iden.startswith("s2:"):
        from paper_rag.ingest.semantic_scholar_source import SemanticScholarSource

        return SemanticScholarSource(), iden.removeprefix("s2:")
    if iden.startswith("url:"):
        from paper_rag.ingest.url_source import UrlSource

        return UrlSource(), iden.removeprefix("url:")
    if iden.startswith("openalex:") or iden.startswith("https://openalex.org/"):
        from paper_rag.ingest.openalex_source import OpenAlexSource

        return OpenAlexSource(), iden.removeprefix("openalex:")
    raise ValueError(f"unknown id format: {identifier!r}")


def _looks_like_arxiv(s: str) -> bool:
    return any(ch.isdigit() for ch in s) and "." in s and "/" not in s


def main() -> int:
    args = parse_args()
    cfg.load()
    ensure_dirs()

    from paper_rag.store.ingest_pipeline import ingest

    lines = [l.strip() for l in Path(args.file).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    log.info(f"batch size = {len(lines)}")
    ok = fail = 0
    for i, iden in enumerate(lines, 1):
        try:
            src, key = _route(iden)
            res = src.fetch(key)
            out = ingest(res, force=args.force)
            ok += 1
            log.info(f"[{i}/{len(lines)}] {iden} -> {out.get('status')} chunks={out.get('chunks')}")
        except Exception as e:
            fail += 1
            log.error(f"[{i}/{len(lines)}] {iden} FAILED: {type(e).__name__}: {e}")

    # rebuild bm25 once at the end
    try:
        from paper_rag.retrieve import sparse_bm25

        sparse_bm25.build_index(force=True)
    except Exception as e:
        log.warning(f"bm25 rebuild skipped: {e}")

    # Drain async wiki queue so the script doesn't exit while updates are pending.
    try:
        from paper_rag.wiki.queue import wait_drained

        log.info("draining async wiki queue (max 5min)...")
        if not wait_drained(timeout=300):
            log.warning("wiki queue did not drain within timeout")
    except Exception as e:
        log.warning(f"wiki queue drain skipped: {e}")

    log.info(f"batch done: {ok} ok, {fail} fail")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
