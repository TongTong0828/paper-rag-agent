"""Citation validation.

Two responsibilities:
  1. Drop any [chunk:<id>] whose id is not in the retrieved set (anti-hallucination).
  2. Detect "suspicious" citation forms the model might fall back to:
       - bracketed numbers like "[1]", "[12]"
       - parenthetical author/year like "(Vaswani et al., 2017)" or "(Smith 2020)"
     These are flagged so the upstream caller can surface them in trace
     metadata or evaluation.
"""

from __future__ import annotations

import re

_CITE_RE = re.compile(r"\[chunk:([0-9a-f]{6,40})\]")

# Bracketed numeric like [1], [12]. Avoid matching [chunk:..] (already excluded by
# the negative lookahead) and avoid markdown checkboxes "[x]" (require digits only).
_NUM_CITE_RE = re.compile(r"(?<!chunk:)\[(\d{1,3})\]")

# (Author Year) and (Author et al., Year) patterns.
_AUTHOR_YEAR_RE = re.compile(
    r"\(\s*[A-Z][A-Za-z\u00c0-\u017f\.\-]+(?:\s+et\s+al\.?)?(?:\s*,)?\s*(?:18|19|20)\d{2}[a-z]?\s*\)"
)


def validate_citations(answer: str, retrieved: list[dict]) -> tuple[str, list[str]]:
    """Drop chunk-citations whose id is not in retrieved set.

    Returns (cleaned_answer, valid_chunk_ids).
    """
    allowed = {c.get("chunk_id") for c in retrieved if c.get("chunk_id")}
    found = _CITE_RE.findall(answer)
    valid = [cid for cid in found if cid in allowed]

    def _sub(m):
        return m.group(0) if m.group(1) in allowed else ""

    cleaned = _CITE_RE.sub(_sub, answer)
    return cleaned, valid


def detect_suspicious_citations(answer: str) -> dict:
    """Return a structured report of non-`[chunk:]` citation forms in `answer`.

    The presence of any of these means the model is "citing" without referring
    to the retrieved chunks — i.e. potentially hallucinating sources.

    Returns:
        {
            "numeric": ["[1]", "[12]", ...],
            "author_year": ["(Vaswani et al., 2017)", ...],
            "count": int,
        }
    """
    numeric = _NUM_CITE_RE.findall(answer)
    author_year = _AUTHOR_YEAR_RE.findall(answer)
    return {
        "numeric": [f"[{n}]" for n in numeric],
        "author_year": author_year,
        "count": len(numeric) + len(author_year),
    }
