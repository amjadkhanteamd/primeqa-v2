"""The deterministic field-name ladder — THE single rule implementation (F1).

Byte-parity port of ``governance_core._resolve_subject_field_name`` (the B1
production ladder, live-verified through the D-375 replay numbers). Both the
production canonicalization site and the resolution package's solver call
THIS function, so the rule can never fork again. Parity is pinned by
``tests/unit/resolution/test_field_ladder.py``'s matrix against the
governance function.

Production semantics preserved exactly (they differ from a naive mirror):
  - rule 1 (exact qualified) is CASE-SENSITIVE and returns the proposal
    verbatim;
  - the bare form is the LAST dot segment (``rsplit``);
  - rule 4 strips only a trailing ``__c`` from the proposal and compares
    against ``display_name.strip().lower()`` — no camel-splitting, no other
    suffix stripping.

Unique-match-only: 0 or >1 candidates at any firing rule → ``None`` — the
caller's refusal/drop stands, never a guess.
"""
from __future__ import annotations

from typing import Iterable, Optional


def resolve_field_name(inventory: Iterable[tuple[str, Optional[str]]],
                       name: object) -> Optional[str]:
    """``inventory`` yields ``(qualified_api_name, display_name)`` pairs of
    the owner object's own fields. Returns the resolved qualified api-name,
    or ``None`` (miss or ambiguity)."""
    if not isinstance(name, str) or not name:
        return None
    fields = [(q, label) for q, label in inventory
              if isinstance(q, str) and q]
    if any(q == name for q, _ in fields):
        return name
    bare = name.rsplit(".", 1)[-1].lower()
    label_norm = (bare[:-3].replace("_", " ").strip() if bare.endswith("__c")
                  else bare.replace("_", " ").strip())

    def _bare(api: str) -> str:
        return api.rsplit(".", 1)[-1].lower()

    for rule in ("bare", "suffix", "label"):
        if rule == "bare":
            cands = [q for q, _ in fields if _bare(q) == bare]
        elif rule == "suffix":
            cands = [q for q, _ in fields if _bare(q).endswith("_" + bare)]
        else:
            cands = [q for q, label in fields
                     if isinstance(label, str)
                     and label.strip().lower() == label_norm]
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            return None
    return None
