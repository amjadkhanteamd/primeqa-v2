"""Acceptance-criterion coverage — the deterministic FLOOR for the per-AC
coverage enforcer (D-247).

The MODEL owns the AC list (it parses messy Jira prose far better than a regex);
this module is only the cross-check FLOOR that catches model UNDER-declaration
("AI lists, computer double-checks"). It is conservative by design — it counts
only confidently-detected line-leading AC markers and prefers to UNDERcount (miss
an exotic/inline marker) rather than false-flag a well-declared requirement; a
false floor would only cost one bounded re-enumerate hop, never a wrong claim.

Pure: no S1, no DB, no LLM. The enforcer's denominator is the model's declared
list; ``floor_shortfall`` only raises an under-declaration signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedAC:
    """One regex-detected acceptance-criterion span (the floor's view)."""

    index: int   # 1-based, by position in the text
    label: str   # the criterion's leading text, trimmed (for re-prompt copy)


# Line-leading marker forms, highest-confidence first. The first form that yields
# >=2 distinct (deduped) items wins — so a requirement using explicit ``AC1:``
# markers is not also re-scanned as a numbered list. Group 1 is the criterion text.
_MARKERS = (
    re.compile(r"^\s*AC\s*\d+[:.)\-\s]+(.*\S)", re.IGNORECASE),  # "AC1: ...", "AC 1 - ..."
    re.compile(r"^\s*#{1,3}\s+(.*\S)"),                          # Jira ordered list "# ...", "## ..."
    re.compile(r"^\s*\d+[.)]\s+(.*\S)"),                         # "1. ...", "2) ..."
    re.compile(r"^\s*[-*•]\s+(.*\S)"),                      # "- ...", "* ...", "• ..."
)


def _normalize(span: str) -> str:
    """Canonical span text for dedup: drop ``{{...}}`` field tokens, collapse
    whitespace, lowercase. Two verbatim-duplicated AC lines (the pre-1513cc1
    Jira-import artifact) normalize equal and dedupe to one."""
    span = re.sub(r"\{\{.*?\}\}", "", span)
    return re.sub(r"\s+", " ", span).strip().lower()


def _label(span: str, limit: int = 80) -> str:
    span = span.strip()
    return span if len(span) <= limit else span[: limit - 1].rstrip() + "…"


def parse_acceptance_criteria(text: str) -> list[DetectedAC]:
    """Detect AC-like items in requirement text — the conservative floor.

    Returns the deduped, position-indexed (1-based) items of the first marker
    form that yields >=2 distinct items; ``[]`` when no confident structure is
    found (the floor then contributes nothing — it never blocks generation)."""
    if not text:
        return []
    lines = text.splitlines()
    for marker in _MARKERS:
        spans: list[str] = []
        seen: set[str] = set()
        for line in lines:
            m = marker.match(line)
            if not m:
                continue
            norm = _normalize(m.group(1))
            if not norm or norm in seen:
                continue
            seen.add(norm)
            spans.append(m.group(1).strip())
        if len(spans) >= 2:
            return [DetectedAC(index=i + 1, label=_label(s)) for i, s in enumerate(spans)]
    return []


def compute_uncovered(declared_indices, tagged_indices) -> list[int]:
    """Declared AC indices with no tagged intent, ascending."""
    return sorted(set(declared_indices) - set(tagged_indices))


def floor_shortfall(declared_count: int, detected: int) -> int:
    """How many ACs the regex floor suspects the model UNDER-declared:
    ``max(0, detected - declared_count)``. Zero when the floor found no structure
    or the model declared at least as many as the floor detected."""
    return max(0, detected - declared_count)


__all__ = [
    "DetectedAC",
    "parse_acceptance_criteria",
    "compute_uncovered",
    "floor_shortfall",
]
