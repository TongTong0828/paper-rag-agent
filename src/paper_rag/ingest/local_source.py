"""Local PDF ingest source.

Computes paper_id by sha1, copies the file into the canonical paper dir,
and records minimal metadata (title can be supplied by caller or inferred
from the PDF later).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..utils.ids import make_paper_id
from ..utils.logger import get_logger
from ..utils.paths import paper_dir
from .schema import FetchResult, PaperMeta
from .sources import PaperSource

log = get_logger("ingest.local")


class LocalSource(PaperSource):
    name = "local"

    def __init__(self, title: str | None = None) -> None:
        self.title = title

    def fetch(self, identifier: str) -> FetchResult:
        src = Path(identifier).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"PDF not found: {src}")

        paper_id = make_paper_id(pdf_path=src)
        target = paper_dir(paper_id)
        target.mkdir(parents=True, exist_ok=True)
        pdf_path = target / "raw.pdf"
        if not pdf_path.exists():
            shutil.copy2(src, pdf_path)
            log.info(f"copied PDF -> {pdf_path}")
        else:
            log.info("PDF already present, skip copy")

        meta = PaperMeta(
            paper_id=paper_id,
            title=self.title or src.stem,
            source=self.name,
            urls=[f"file://{src}"],
        )
        (target / "meta.json").write_text(
            json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target / "source.txt").write_text(
            f"source={self.name}\nquery={identifier}\n", encoding="utf-8"
        )
        return FetchResult(meta=meta, pdf_path=str(pdf_path))
