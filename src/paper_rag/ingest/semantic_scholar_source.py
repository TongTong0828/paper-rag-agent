"""Semantic Scholar source.

Uses the Graph API:
    GET /graph/v1/paper/{id}?fields=title,authors,year,venue,abstract,externalIds,openAccessPdf

`identifier` accepted forms: arxiv:xxx, DOI:xxx, S2 paperId. The PDF is only
downloaded when openAccessPdf is provided.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..utils.ids import make_paper_id, normalize_arxiv, normalize_doi
from ..utils.logger import get_logger
from ..utils.paths import paper_dir
from .schema import FetchResult, PaperMeta
from .sources import PaperSource


log = get_logger("ingest.s2")

_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = "title,authors,year,venue,abstract,externalIds,openAccessPdf"


class SemanticScholarSource(PaperSource):
    name = "semantic_scholar"

    def __init__(self, api_key: str | None = None, timeout: float = 30) -> None:
        self.headers = {"User-Agent": "paper-rag/0.1"}
        if api_key:
            self.headers["x-api-key"] = api_key
        self.timeout = timeout

    def fetch(self, identifier: str) -> FetchResult:
        s2_id = self._normalize_id(identifier)
        log.info(f"s2 fetch id = {s2_id}")
        with httpx.Client(timeout=self.timeout, headers=self.headers) as cli:
            r = cli.get(f"{_BASE}/paper/{s2_id}", params={"fields": _FIELDS})
            r.raise_for_status()
            data = r.json()

        ext = data.get("externalIds") or {}
        arxiv_id = ext.get("ArXiv")
        doi = ext.get("DOI")
        paper_id = make_paper_id(
            arxiv_id=arxiv_id,
            doi=doi,
            pdf_path=None,
        ) if (arxiv_id or doi) else f"s2:{data['paperId']}"

        target = paper_dir(paper_id)
        target.mkdir(parents=True, exist_ok=True)
        pdf_path = target / "raw.pdf"
        pdf_url = (data.get("openAccessPdf") or {}).get("url")
        if pdf_url and not pdf_path.exists():
            log.info(f"downloading PDF {pdf_url}")
            with httpx.Client(timeout=120, follow_redirects=True) as cli:
                resp = cli.get(pdf_url)
                resp.raise_for_status()
                pdf_path.write_bytes(resp.content)
        elif not pdf_url:
            log.warning(f"no openAccessPdf for {s2_id}; pdf_path will be empty")

        meta = PaperMeta(
            paper_id=paper_id,
            title=(data.get("title") or "").strip(),
            authors=[a["name"] for a in (data.get("authors") or []) if a.get("name")],
            year=data.get("year"),
            venue=data.get("venue"),
            doi=doi,
            arxiv_id=arxiv_id,
            abstract=data.get("abstract"),
            urls=[u for u in (pdf_url, f"https://www.semanticscholar.org/paper/{data.get('paperId')}") if u],
            source=self.name,
            extra={"externalIds": ext, "paperId": data.get("paperId")},
        )
        (target / "meta.json").write_text(
            json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target / "source.txt").write_text(
            f"source={self.name}\nquery={identifier}\n", encoding="utf-8"
        )
        return FetchResult(meta=meta, pdf_path=str(pdf_path) if pdf_path.exists() else "")

    def _normalize_id(self, identifier: str) -> str:
        if identifier.lower().startswith("arxiv:"):
            return identifier
        if identifier.lower().startswith("doi:"):
            doi = normalize_doi(identifier)
            return f"DOI:{doi}" if doi else identifier
        if "/" in identifier and not identifier.startswith("http"):
            return f"DOI:{identifier}"
        ax = normalize_arxiv(identifier)
        if ax:
            return f"arxiv:{ax}"
        return identifier
