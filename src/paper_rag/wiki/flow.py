"""Create / patch flow for wiki entries.

create_entry:  LLM -> definition + key_papers + open_problems from chunks
patch_entry:   LLM emits a JSON diff against existing entry; we merge fields.

Both pipelines respect lock_until rate limit and run a self-eval gate.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .. import config as cfg
from ..rag.llm import chat
from ..utils.logger import get_logger
from .concept_extractor import _format_chunks
from .schema import Variant, WikiEntry, make_entry_id


log = get_logger("wiki.flow")


_CREATE_PROMPT = """You author a concise wiki entry for a research concept.

Concept name: {name}
Category: {category}
Source paper: {paper_id} — {title}

Evidence chunks:
{chunks}

Return ONLY JSON:

  {"definition": "<2-3 sentence definition citing chunk ids like [chunk:abc]>",
   "aliases": ["<English-or-Chinese alias 1>", "<alias 2>"],
   "open_problems": ["...", "..."],
   "self_eval": <0-1 float; how confident the entry is well-grounded>}

Notes on aliases:
- Provide 1-3 high-confidence aliases (e.g. ["对比学习", "CL"] for "Contrastive Learning").
- Include translation across Chinese/English when applicable.
- Do NOT pad with low-confidence guesses; empty list is OK.
"""


_PATCH_PROMPT = """You produce a JSON patch for an existing wiki entry given new
evidence. Be conservative: only emit fields that should change. NEVER rewrite
fields you cannot justify from the new evidence.

Existing entry (JSON):
{existing}

New evidence (chunks):
{chunks}

New paper: {paper_id} — {title}

Return ONLY JSON:

  {"patch": {
      "definition": "...optional refined definition...",
      "add_key_papers": ["paper_id1", ...],
      "add_aliases": ["..."],
      "add_open_problems": ["..."],
      "add_variants": [{"name": "...", "summary": "...", "paper_id": "..."}],
      "add_related": ["entry_id1", ...]
   },
   "self_eval": <0-1 float; whether this patch improves the entry>,
   "reason": "<short>"}
"""


def _clean_aliases(raw: list, primary: str) -> list[str]:
    """Filter aliases: drop empty, drop duplicates, drop the primary name itself."""
    primary_norm = "".join(c.lower() for c in primary if c.isalnum())
    seen: set[str] = set()
    out: list[str] = []
    for a in raw:
        if not isinstance(a, str):
            continue
        a = a.strip()
        if not a or len(a) < 2:
            continue
        norm = "".join(c.lower() for c in a if c.isalnum())
        if not norm or norm == primary_norm or norm in seen:
            continue
        seen.add(norm)
        out.append(a)
    return out[:5]


def _self_eval_gate(score: float, label: str) -> bool:
    threshold = cfg.load().wiki.self_eval_threshold
    ok = score >= threshold
    log.info(f"self_eval {label}: {score:.2f} (threshold {threshold:.2f}) -> {'PASS' if ok else 'DROP'}")
    return ok


def _rate_limited(entry: WikiEntry) -> bool:
    if entry.lock_until and entry.lock_until > datetime.utcnow():
        log.info(f"{entry.entry_id} locked until {entry.lock_until.isoformat()}, skip")
        return True
    return False


def _refresh_lock(entry: WikiEntry) -> WikiEntry:
    hours = cfg.load().wiki.rate_limit_hours
    entry.lock_until = datetime.utcnow() + timedelta(hours=hours)
    return entry


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def create_entry(*, name: str, category: str, paper_id: str, paper_title: str,
                 chunks: list[dict]) -> WikiEntry | None:
    raw = chat(
        [{"role": "user", "content": _CREATE_PROMPT
            .replace("{name}", name)
            .replace("{category}", category)
            .replace("{paper_id}", paper_id)
            .replace("{title}", paper_title or "")
            .replace("{chunks}", _format_chunks(chunks))}],
        temperature=0.2,
        max_tokens=600,
    )
    try:
        data = _parse_json(raw)
    except Exception as e:
        log.warning(f"create_entry parse failed: {e}")
        return None

    score = float(data.get("self_eval", 0.0))
    if not _self_eval_gate(score, f"create:{name}"):
        return None

    entry = WikiEntry(
        entry_id=make_entry_id(name),
        name=name,
        category=category if category in {"concept", "method", "task", "dataset", "metric"} else "concept",
        definition=(data.get("definition") or "").strip(),
        aliases=_clean_aliases(data.get("aliases") or [], primary=name),
        key_papers=[paper_id],
        open_problems=list(data.get("open_problems") or []),
        evidence_chunks=[c.get("chunk_id") for c in chunks if c.get("chunk_id")],
    )
    return _refresh_lock(entry)


def patch_entry(*, existing: WikiEntry, paper_id: str, paper_title: str,
                chunks: list[dict]) -> WikiEntry | None:
    if _rate_limited(existing):
        return None

    raw = chat(
        [{"role": "user", "content": _PATCH_PROMPT
            .replace("{existing}", json.dumps(existing.model_dump(mode="json"), ensure_ascii=False))
            .replace("{paper_id}", paper_id)
            .replace("{title}", paper_title or "")
            .replace("{chunks}", _format_chunks(chunks))}],
        temperature=0.2,
        max_tokens=600,
    )
    try:
        data = _parse_json(raw)
    except Exception as e:
        log.warning(f"patch_entry parse failed: {e}")
        return None

    score = float(data.get("self_eval", 0.0))
    if not _self_eval_gate(score, f"patch:{existing.entry_id}"):
        return None

    patch = data.get("patch") or {}
    merged = existing.model_copy(deep=True)

    new_def = (patch.get("definition") or "").strip()
    if new_def:
        merged.definition = new_def

    for pid in patch.get("add_key_papers", []) or []:
        if pid and pid not in merged.key_papers:
            merged.key_papers.append(pid)
    if paper_id not in merged.key_papers:
        merged.key_papers.append(paper_id)

    for a in patch.get("add_aliases", []) or []:
        if a and a not in merged.aliases:
            merged.aliases.append(a)
    for op in patch.get("add_open_problems", []) or []:
        if op and op not in merged.open_problems:
            merged.open_problems.append(op)
    for r in patch.get("add_related", []) or []:
        if r and r not in merged.related:
            merged.related.append(r)
    for v in patch.get("add_variants", []) or []:
        if not isinstance(v, dict) or not v.get("name"):
            continue
        if any(existing_v.name == v["name"] for existing_v in merged.variants):
            continue
        merged.variants.append(Variant(**{k: v.get(k) for k in ("name", "summary", "paper_id")}))

    for cid in [c.get("chunk_id") for c in chunks if c.get("chunk_id")]:
        if cid not in merged.evidence_chunks:
            merged.evidence_chunks.append(cid)

    return _refresh_lock(merged)
