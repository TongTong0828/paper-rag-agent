"""OpenAlex source.

Free, no API key required. Mainly used for metadata enrichment;
PDF is downloaded only when an open-access PDF URL is provided.
"""

from __future__ import annotations

import json

import httpx

from ..utils.ids import make_paper_id, normalize_doi
from ..utils.logger import get_logger
from ..utils.paths import paper_dir
from .schema import FetchResult, PaperMeta
from .sources import PaperSource

log = get_logger("ingest.openalex")

_BASE = "https://api.openalex.org"


class OpenAlexSource(PaperSource):
    name = "openalex"

    def fetch(self, identifier: str) -> FetchResult:
        if identifier.lower().startswith("doi:"):
            doi = normalize_doi(identifier)
            url = f"{_BASE}/works/doi:{doi}"
        elif identifier.startswith("https://openalex.org/"):
            url = identifier.replace("https://openalex.org/", f"{_BASE}/works/")
        else:
            url = f"{_BASE}/works/{identifier}"

        log.info(f"openalex GET {url}")
        with httpx.Client(timeout=30) as cli:
            r = cli.get(url, params={"mailto": "paper-rag@example.com"})
            r.raise_for_status()
            data = r.json()

        ids = data.get("ids") or {}
        doi = (ids.get("doi") or "").replace("https://doi.org/", "") or None
        paper_id = make_paper_id(doi=doi) if doi else f"openalex:{data['id'].rsplit('/', 1)[-1]}"

        target = paper_dir(paper_id)
        target.mkdir(parents=True, exist_ok=True)
        pdf_path = target / "raw.pdf"
        oa = data.get("open_access") or {}
        pdf_url = oa.get("oa_url")
        if pdf_url and not pdf_path.exists():
            with httpx.Client(timeout=120, follow_redirects=True) as cli:
                try:
                    resp = cli.get(pdf_url)
                    resp.raise_for_status()
                    pdf_path.write_bytes(resp.content)
                except Exception as e:
                    log.warning(f"openalex PDF download failed: {e}")

        authors = [
            a.get("author", {}).get("display_name", "")
            for a in (data.get("authorships") or [])
            if a.get("author")
        ]
        meta = PaperMeta(
            paper_id=paper_id,
            title=(data.get("title") or "").strip(),
            authors=[a for a in authors if a],
            year=data.get("publication_year"),
            venue=(data.get("primary_location") or {}).get("source", {}).get("display_name"),
            doi=doi,
            abstract=_decode_abstract(data.get("abstract_inverted_index")),
            urls=[u for u in (pdf_url, data.get("id")) if u],
            source=self.name,
        )
        (target / "meta.json").write_text(
            json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target / "source.txt").write_text(
            f"source={self.name}\nquery={identifier}\n", encoding="utf-8"
        )
        return FetchResult(meta=meta, pdf_path=str(pdf_path) if pdf_path.exists() else "")


def _decode_abstract(inv: dict | None) -> str | None:
    if not inv:
        return None
    pos2word: dict[int, str] = {}
    for word, positions in inv.items():
        for p in positions:
            pos2word[p] = word
    if not pos2word:
        return None
    return " ".join(pos2word[i] for i in sorted(pos2word))
