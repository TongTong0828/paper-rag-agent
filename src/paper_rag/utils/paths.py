"""Path helpers built on top of loaded config."""

from __future__ import annotations

from pathlib import Path

from .. import config as cfg
from .ids import to_safe_dirname


def paper_dir(paper_id: str) -> Path:
    return Path(cfg.load().paths.papers_dir) / to_safe_dirname(paper_id)


def parsed_dir(paper_id: str) -> Path:
    return Path(cfg.load().paths.parsed_dir) / to_safe_dirname(paper_id)


def ensure_dirs() -> None:
    c = cfg.load()
    for p in (c.paths.papers_dir, c.paths.parsed_dir, c.paths.index_dir, c.paths.models_dir):
        Path(p).mkdir(parents=True, exist_ok=True)
