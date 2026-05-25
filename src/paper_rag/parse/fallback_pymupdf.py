"""pymupdf-based fallback parser.

Produces a minimal `paper.md` (plain text per page, no figures/tables/formulas).
Used when MinerU is unavailable or fails.
"""

from __future__ import annotations

from pathlib import Path

from ..utils.logger import get_logger
from ..utils.paths import parsed_dir

log = get_logger("parse.pymupdf")


def parse_pdf(paper_id: str, pdf_path: str | Path) -> Path:
    try:
        import fitz  # pymupdf
    except ImportError as e:
        raise RuntimeError("pymupdf not installed. Run: pip install pymupdf") from e

    out_dir = parsed_dir(paper_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "paper.md"

    doc = fitz.open(str(pdf_path))
    parts: list[str] = []
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        parts.append(f"\n\n<!-- page {i + 1} -->\n\n{text.strip()}")
    md_path.write_text("\n".join(parts).strip(), encoding="utf-8")
    log.info(f"pymupdf fallback wrote {md_path} ({md_path.stat().st_size} bytes)")
    return out_dir
