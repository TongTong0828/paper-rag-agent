"""Direct-PDF ingest helper.

Bypasses the arxiv API (which rate-limits aggressively) by hitting
`https://arxiv.org/pdf/<id>.pdf` directly with httpx. Title is fetched
once via the abstract page (HTML), parsed with a tiny regex.

Usage:
    python scripts/ingest_arxiv_direct.py 2310.11511 2005.11401 2401.01313
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from paper_rag import config as cfg
from paper_rag.utils.ids import make_paper_id, normalize_arxiv
from paper_rag.utils.logger import get_logger
from paper_rag.utils.paths import ensure_dirs, paper_dir

log = get_logger("ingest_arxiv_direct")

_TITLE_RE = re.compile(r'<meta name="citation_title" content="([^"]+)"')
_AUTHOR_RE = re.compile(r'<meta name="citation_author" content="([^"]+)"')
_YEAR_RE = re.compile(r'<meta name="citation_date" content="(\d{4})')


def _fetch_meta(arxiv_id: str) -> dict:
    url = f"https://arxiv.org/abs/{arxiv_id}"
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "paper-rag/0.1"}) as cli:
        r = cli.get(url)
        r.raise_for_status()
        html = r.text
    title_m = _TITLE_RE.search(html)
    return {
        "title": title_m.group(1) if title_m else f"arXiv {arxiv_id}",
        "authors": _AUTHOR_RE.findall(html),
        "year": int(_YEAR_RE.search(html).group(1)) if _YEAR_RE.search(html) else None,
    }


def _fetch_pdf(arxiv_id: str, target: Path) -> None:
    pdf_path = target / "raw.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        log.info(f"PDF already present: {pdf_path}")
        return
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    log.info(f"GET {url}")
    with httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "paper-rag/0.1"}) as cli:
        r = cli.get(url)
        r.raise_for_status()
        pdf_path.write_bytes(r.content)


def fetch_one(arxiv_id: str):
    from paper_rag.ingest.schema import FetchResult, PaperMeta

    arxiv_id = normalize_arxiv(arxiv_id) or arxiv_id
    paper_id = make_paper_id(arxiv_id=arxiv_id)
    target = paper_dir(paper_id)
    target.mkdir(parents=True, exist_ok=True)

    _fetch_pdf(arxiv_id, target)
    meta = _fetch_meta(arxiv_id)

    pmeta = PaperMeta(
        paper_id=paper_id,
        title=meta["title"].strip(),
        authors=meta["authors"],
        year=meta["year"],
        venue="arXiv",
        arxiv_id=arxiv_id,
        urls=[f"https://arxiv.org/abs/{arxiv_id}"],
        source="arxiv-direct",
    )
    import json as _json

    (target / "meta.json").write_text(
        _json.dumps(pmeta.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (target / "source.txt").write_text(f"source=arxiv-direct\nquery={arxiv_id}\n", encoding="utf-8")
    return FetchResult(meta=pmeta, pdf_path=str(target / "raw.pdf"))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cfg.load()
    ensure_dirs()
    from paper_rag.store.ingest_pipeline import ingest

    for arg in sys.argv[1:]:
        try:
            res = fetch_one(arg)
            log.info(f"fetched {res.meta.paper_id}: {res.meta.title!r}")
            out = ingest(res)
            log.info(f"  -> {out.get('status')} chunks={out.get('chunks')}")
        except Exception as e:
            log.error(f"FAILED {arg}: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
