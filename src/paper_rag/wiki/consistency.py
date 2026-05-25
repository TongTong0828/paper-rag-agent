"""Lightweight consistency checks for wiki entries.

Heuristic-only (no LLM calls). Use to flag entries that need human review
during periodic reviews; never auto-deletes.
"""

from __future__ import annotations

from .schema import WikiEntry


def check_entry(entry: WikiEntry) -> list[str]:
    issues: list[str] = []
    if not entry.definition or len(entry.definition) < 20:
        issues.append("definition_too_short")
    if not entry.key_papers:
        issues.append("no_key_papers")
    if entry.version > 10 and not entry.evidence_chunks:
        issues.append("high_version_no_evidence")
    if any(len(a) < 2 for a in entry.aliases):
        issues.append("trivial_alias")
    # cross-related sanity: avoid self-related
    if entry.entry_id in entry.related:
        issues.append("self_related")
    return issues


def find_problematic_entries(entries: list[WikiEntry]) -> list[dict]:
    return [
        {"entry_id": e.entry_id, "name": e.name, "issues": issues}
        for e in entries
        if (issues := check_entry(e))
    ]
