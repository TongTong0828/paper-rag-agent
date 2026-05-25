"""Generic URL source: download the PDF directly if URL ends in .pdf,
otherwise fall back to local html-to-text extraction is NOT done here
(use jina_ai/firecrawl as a separate path for non-PDF web reading).
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx

from ..utils.ids import make_paper_id
from ..utils.logger import get_logger
from ..utils.paths import paper_dir
from .schema import FetchResult, PaperMeta
from .sources import PaperSource

log = get_logger("ingest.url")


class UrlSource(PaperSource):
    name = "url"

    def __init__(self, title: str | None = None) -> None:
        self.title = title

    def fetch(self, identifier: str) -> FetchResult:
        url = identifier
        path = urlparse(url).path
        if not path.lower().endswith(".pdf"):
            raise ValueError(
                "UrlSource currently supports direct PDF URLs only. "
                "For HTML pages use jina_ai reader as a separate ingest path."
            )

        log.info(f"url fetch GET {url}")
        with httpx.Client(timeout=120, follow_redirects=True) as cli:
            resp = cli.get(url)
            resp.raise_for_status()
            content = resp.content

        tmp = paper_dir(make_paper_id(pdf_path="/dev/null") if False else "sha1:tmp")
        tmp.mkdir(parents=True, exist_ok=True)
        tmp_pdf = tmp / "raw.pdf"
        tmp_pdf.write_bytes(content)

        paper_id = make_paper_id(pdf_path=tmp_pdf)
        target = paper_dir(paper_id)
        target.mkdir(parents=True, exist_ok=True)
        final_pdf = target / "raw.pdf"
        if not final_pdf.exists():
            final_pdf.write_bytes(content)
        # cleanup tmp dir if different
        if tmp != target:
            try:
                tmp_pdf.unlink()
                tmp.rmdir()
            except OSError:
                pass

        meta = PaperMeta(
            paper_id=paper_id,
            title=self.title or url.rsplit("/", 1)[-1],
            urls=[url],
            source=self.name,
        )
        (target / "meta.json").write_text(
            json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target / "source.txt").write_text(
            f"source={self.name}\nquery={url}\n", encoding="utf-8"
        )
        return FetchResult(meta=meta, pdf_path=str(final_pdf))
