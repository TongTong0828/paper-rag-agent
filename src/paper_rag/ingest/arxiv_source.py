"""arXiv paper source.

Uses the `arxiv` Python package to query metadata and download the PDF.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..utils.ids import make_paper_id, normalize_arxiv, split_arxiv_version
from ..utils.logger import get_logger
from ..utils.paths import paper_dir
from .schema import FetchResult, PaperMeta
from .sources import PaperSource


log = get_logger("ingest.arxiv")


class ArxivSource(PaperSource):
    name = "arxiv"

    def fetch(self, identifier: str) -> FetchResult:
        try:
            import arxiv  # lazy import; optional dep
        except ImportError as e:
            raise RuntimeError(
                "arxiv package not installed. Run: pip install arxiv"
            ) from e

        normalized = normalize_arxiv(identifier) or identifier
        _, requested_version = split_arxiv_version(identifier)
        log.info(f"arxiv fetch id = {normalized} version={requested_version}")

        # arxiv API rate-limits aggressively. Larger delay + more retries.
        client = arxiv.Client(page_size=1, delay_seconds=10, num_retries=5)
        search = arxiv.Search(id_list=[normalized])
        try:
            result = next(client.results(search))
        except StopIteration as e:
            raise ValueError(f"arxiv id not found: {normalized}") from e

        paper_id = make_paper_id(arxiv_id=normalized)
        target = paper_dir(paper_id)
        target.mkdir(parents=True, exist_ok=True)
        pdf_path = target / "raw.pdf"
        if not pdf_path.exists():
            log.info(f"downloading PDF -> {pdf_path}")
            # arxiv>=4.0 changed API: use Client.download_pdf, fallback to result method
            try:
                client.download_pdf(result, dirpath=str(target), filename="raw.pdf")
            except (AttributeError, TypeError):
                if hasattr(result, "download_pdf"):
                    result.download_pdf(dirpath=str(target), filename="raw.pdf")
                else:
                    # last-resort: httpx download via pdf_url
                    import httpx

                    with httpx.Client(timeout=120, follow_redirects=True) as cli:
                        resp = cli.get(result.pdf_url)
                        resp.raise_for_status()
                        pdf_path.write_bytes(resp.content)
        else:
            log.info("PDF already present, skip download")

        meta = PaperMeta(
            paper_id=paper_id,
            title=result.title.strip(),
            authors=[a.name for a in result.authors],
            year=result.published.year if result.published else None,
            venue="arXiv",
            doi=result.doi,
            arxiv_id=normalized,
            abstract=(result.summary or "").strip(),
            urls=[result.entry_id, result.pdf_url],
            source=self.name,
            extra={"arxiv_version": requested_version} if requested_version else {},
        )
        _persist_meta(target, meta, source_query=identifier)
        return FetchResult(meta=meta, pdf_path=str(pdf_path))


def _persist_meta(target: Path, meta: PaperMeta, source_query: str) -> None:
    (target / "meta.json").write_text(
        json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (target / "source.txt").write_text(
        f"source={meta.source}\nquery={source_query}\n", encoding="utf-8"
    )
