"""Markdown header-based section splitter.

Returns a list of sections in document order. Each section keeps its
original markdown body (including images / tables / formulas).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_HEADER_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class RawSection:
    idx: int
    name: str
    level: int
    start: int
    end: int
    body: str


def split_sections(md: str) -> list[RawSection]:
    headers = list(_HEADER_RE.finditer(md))
    if not headers:
        return [RawSection(idx=0, name="Body", level=1, start=0, end=len(md), body=md.strip())]

    sections: list[RawSection] = []
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(md)
        body = md[start:end].strip()
        sections.append(
            RawSection(
                idx=i,
                name=m.group(2).strip(),
                level=len(m.group(1)),
                start=start,
                end=end,
                body=body,
            )
        )
    return sections
