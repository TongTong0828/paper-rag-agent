"""Concept extraction.

Given a paper's chunks, ask the LLM to surface 3-8 high-value concepts that
deserve a wiki entry. We deliberately prefer recall < precision: better to
miss a concept than spam the wiki with noise.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

from ..rag.llm import chat
from ..utils.logger import get_logger


log = get_logger("wiki.extract")

_PROMPT = """You extract high-value concepts that deserve a wiki entry from a
research paper's content. Be conservative: surface ONLY concepts that
(a) are core to the paper's contribution OR widely-used named techniques,
(b) have a clear, citable definition.

Skip generic terms like "neural network", "training", "experiment".

Return ONLY JSON:

  {"concepts": [
     {"name": "Contrastive Learning",
      "category": "method" | "concept" | "task" | "dataset" | "metric",
      "evidence_chunk_ids": ["...", "..."]},
     ...
  ]}

Limit to 5 concepts max.

Paper title: {title}

Chunks (truncated):
{chunks}
"""


def _format_chunks(chunks: Iterable[dict], budget: int = 6000) -> str:
    rows: list[str] = []
    used = 0
    for c in chunks:
        body = (c.get("text") or "").strip()
        line = f"[chunk:{c.get('chunk_id')}] section={c.get('section')}\n{body}\n"
        if used + len(line) > budget:
            break
        rows.append(line)
        used += len(line)
    return "\n".join(rows)


def extract_concepts(*, title: str, chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []
    try:
        raw = chat(
            [{"role": "user", "content": _PROMPT.replace("{title}", title or "")
                .replace("{chunks}", _format_chunks(chunks))}],
            temperature=0,
            max_tokens=600,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        concepts = data.get("concepts") or []
    except Exception as e:
        log.warning(f"extract failed: {e}")
        return []

    cleaned: list[dict] = []
    for c in concepts:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        cleaned.append({
            "name": c["name"].strip(),
            "category": c.get("category", "concept"),
            "evidence_chunk_ids": c.get("evidence_chunk_ids", []) or [],
        })
    log.info(f"extracted {len(cleaned)} concepts from {title!r}")
    return cleaned
