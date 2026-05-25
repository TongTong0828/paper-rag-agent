"""Section-completeness sanity checker.

Heuristic: a "complete" academic paper parse should contain at least one
section name matching each major area. We just look at the section names
(case-insensitive substring match) — chunk count would be too noisy.

Returns a string label suitable for `Paper.parsed_with` augmentation:
  - "complete": all 4 areas found
  - "partial": at least intro+method but missing experiments or conclusion
  - "minimal": only intro/abstract found
  - "broken": none of the canonical areas found

Used by ingest_pipeline to set `parsed_with={parser_name}+{quality}` so we
can later filter out broken parses without re-running everything.
"""

from __future__ import annotations


_AREAS = {
    "intro": ["abstract", "introduction", "intro"],
    "method": ["method", "approach", "methodology", "model", "framework", "architecture"],
    "experiment": ["experiment", "evaluation", "result", "ablation", "analysis"],
    "conclusion": ["conclusion", "discussion", "summary", "limitation", "future work"],
}


def grade_sections(section_names: list[str]) -> str:
    lows = [n.lower() for n in section_names]

    def _has(area: str) -> bool:
        return any(any(k in name for k in _AREAS[area]) for name in lows)

    intro = _has("intro")
    method = _has("method")
    exp = _has("experiment")
    concl = _has("conclusion")

    if intro and method and exp and concl:
        return "complete"
    if intro and method and (exp or concl):
        return "partial"
    if intro or method:
        return "minimal"
    return "broken"
