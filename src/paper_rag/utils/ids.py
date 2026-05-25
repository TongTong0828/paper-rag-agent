"""Stable paper_id generation.

Order of preference:
    arxiv:<arxiv_id>  -> arxiv: prefix
    doi:<doi>         -> doi:   prefix
    sha1:<file sha1>  -> sha1:  prefix (fallback for ad-hoc PDFs)
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def normalize_arxiv(raw: str) -> str | None:
    m = _ARXIV_RE.search(raw)
    return m.group(1) if m else None


def split_arxiv_version(raw: str) -> tuple[str | None, str | None]:
    """Return (id_without_version, version_or_None). e.g. '2310.12345v2' -> ('2310.12345', 'v2')."""
    m = _ARXIV_RE.search(raw)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def normalize_doi(raw: str) -> str | None:
    raw = raw.strip().lower()
    if raw.startswith("doi:"):
        raw = raw[4:]
    if raw.startswith("https://doi.org/"):
        raw = raw[len("https://doi.org/") :]
    return raw if raw.startswith("10.") else None


def sha1_of_file(path: str | Path, buf: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def make_paper_id(*, arxiv_id: str | None = None, doi: str | None = None, pdf_path: str | Path | None = None) -> str:
    if arxiv_id:
        return f"arxiv:{normalize_arxiv(arxiv_id) or arxiv_id}"
    if doi:
        return f"doi:{normalize_doi(doi) or doi}"
    if pdf_path:
        return f"sha1:{sha1_of_file(pdf_path)}"
    raise ValueError("Need at least one of arxiv_id / doi / pdf_path")


def to_safe_dirname(paper_id: str) -> str:
    """Filesystem-safe form: replace ':' with '_'."""
    return paper_id.replace(":", "_").replace("/", "_")
