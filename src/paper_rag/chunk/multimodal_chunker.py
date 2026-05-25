"""Multimodal chunk extractors: figures, tables, formulas.

Each returns chunks distinct from text chunks. The current implementation is
markdown-pattern-based (works on both MinerU and pymupdf output, with reduced
recall for the latter).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FIGURE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)]+)\)")
_TABLE_BLOCK_RE = re.compile(r"((?:^\|.*\|\s*$\n?)+)", re.MULTILINE)
_FORMULA_BLOCK_RE = re.compile(r"\$\$(?P<body>.+?)\$\$", re.DOTALL)


@dataclass
class MMChunk:
    text: str           # what gets embedded
    modality: str       # figure | table | formula
    raw: str            # original markdown snippet


def extract_figures(body: str) -> list[MMChunk]:
    out: list[MMChunk] = []
    for m in _FIGURE_RE.finditer(body):
        alt = m.group("alt").strip()
        path = m.group("path").strip()
        context = _surrounding_text(body, m.start(), m.end())
        text = f"Figure: {alt}\nContext: {context}\nPath: {path}"
        out.append(MMChunk(text=text, modality="figure", raw=m.group(0)))
    return out


def extract_tables(body: str) -> list[MMChunk]:
    out: list[MMChunk] = []
    for m in _TABLE_BLOCK_RE.finditer(body):
        block = m.group(1).strip()
        context = _surrounding_text(body, m.start(), m.end())
        out.append(MMChunk(text=f"Table:\n{block}\nContext: {context}", modality="table", raw=block))
    return out


def extract_formulas(body: str) -> list[MMChunk]:
    out: list[MMChunk] = []
    for m in _FORMULA_BLOCK_RE.finditer(body):
        latex = m.group("body").strip()
        context = _surrounding_text(body, m.start(), m.end())
        out.append(MMChunk(text=f"Formula: {latex}\nContext: {context}", modality="formula", raw=m.group(0)))
    return out


def _surrounding_text(body: str, start: int, end: int, span: int = 240) -> str:
    left = max(0, start - span)
    right = min(len(body), end + span)
    snippet = body[left:start] + " " + body[end:right]
    return re.sub(r"\s+", " ", snippet).strip()
