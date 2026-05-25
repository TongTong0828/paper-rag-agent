"""MinerU local CLI wrapper.

Adapted to magic-pdf's actual output layout:

    out_dir/
        <pdf_basename>/
            auto/
                <pdf_basename>.md         <-- main markdown
                <pdf_basename>_layout.pdf
                <pdf_basename>_origin.pdf
                <pdf_basename>_content_list.json
                <pdf_basename>_middle.json
                images/                    <-- figures
                ...

We normalize this into:

    parsed_dir/
        paper.md          (rewritten image paths)
        layout.json       (alias for content_list / middle)
        figures/          (copied from images/)
        tables/           (best-effort)

If anything goes wrong (no .md, empty output, timeout) raises MineruError so
the caller can fall back to pymupdf.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .. import config as cfg
from ..utils.logger import get_logger
from ..utils.paths import parsed_dir

log = get_logger("parse.mineru")


class MineruError(RuntimeError):
    pass


def parse_pdf(paper_id: str, pdf_path: str | Path) -> Path:
    """Run MinerU on `pdf_path`, return the normalized parsed_dir.

    Raises MineruError on timeout / non-zero exit / empty output.
    """
    c = cfg.load()
    out_dir = parsed_dir(paper_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    if shutil.which(c.mineru.cli) is None:
        raise MineruError(
            f"MinerU CLI '{c.mineru.cli}' not found on PATH. "
            "Install via `pip install magic-pdf` or set mineru.cli in config."
        )

    cmd = [c.mineru.cli, "-p", str(pdf_path), "-o", str(out_dir), "-m", "auto"]
    log.info(f"mineru exec: {' '.join(cmd)} (timeout={c.mineru.timeout_sec}s)")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=c.mineru.timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise MineruError(f"mineru timeout after {c.mineru.timeout_sec}s") from e
    if proc.returncode != 0:
        raise MineruError(
            f"mineru failed (rc={proc.returncode}): {proc.stderr[-2000:]}"
        )

    md_path, mineru_assets_dir = _locate_outputs(out_dir)
    if md_path is None or not md_path.exists() or md_path.stat().st_size == 0:
        raise MineruError(f"mineru produced no markdown under {out_dir}")

    _normalize_into(out_dir, md_path, mineru_assets_dir)
    log.info(f"mineru ok -> {out_dir}")
    return out_dir


def _locate_outputs(out_dir: Path) -> tuple[Path | None, Path | None]:
    """Find (main_md, assets_dir).

    magic-pdf typical layout: <out>/<basename>/auto/<basename>.md and ../images/.
    Some versions put files at <out>/<basename>.md directly.
    """
    candidates = list(out_dir.rglob("*.md"))
    if not candidates:
        return None, None
    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    md = candidates[0]

    assets = md.parent / "images"
    if not assets.is_dir():
        for sibling in md.parent.iterdir():
            if sibling.is_dir() and sibling.name.lower() in {"images", "figures", "assets"}:
                assets = sibling
                break
    return md, assets if assets.is_dir() else None


_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _normalize_into(out_dir: Path, src_md: Path, mineru_assets: Path | None) -> None:
    """Copy figures into out_dir/figures/ and rewrite image paths in paper.md."""
    figures_dir = out_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    asset_map: dict[str, str] = {}
    if mineru_assets and mineru_assets.is_dir():
        for f in mineru_assets.iterdir():
            if not f.is_file():
                continue
            target = figures_dir / f.name
            if not target.exists():
                shutil.copy2(f, target)
            asset_map[f.name] = f"figures/{f.name}"

    md = src_md.read_text(encoding="utf-8")

    def _rewrite(match: re.Match) -> str:
        alt, path = match.group(1), match.group(2)
        basename = Path(path).name
        new_path = asset_map.get(basename, path)
        return f"![{alt}]({new_path})"

    md = _IMAGE_REF_RE.sub(_rewrite, md)
    (out_dir / "paper.md").write_text(md, encoding="utf-8")

    # Mirror layout/content_list as layout.json if present
    for cand in src_md.parent.glob("*content_list*.json"):
        try:
            (out_dir / "layout.json").write_text(
                json.dumps(json.loads(cand.read_text(encoding="utf-8")),
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            break
        except Exception:
            continue
