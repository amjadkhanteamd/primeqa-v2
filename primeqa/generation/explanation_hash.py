"""Substrate-3 explanation_hash — mechanical canonicalization (D-075 / D-096.4).

Computes the explanation-equivalence fingerprint over the *typed*
``attempted_interpretation`` only — no free-form prose participates (D-075
Guardrail 2). Adapted to the Slice-0 / D-087 shape: ``candidate_paths`` is
canonically ordered by ``(archetype, claim_kind, subject_refs)`` and the
``path_id`` is normalized to its canonical position (the int/string history is
moot — it never enters the hash); ``dismissed_alternatives_by_reason`` is
alphabetical by reason with canonical positions; ``scoped_neighborhood``
(carried via ``extra``) is lexicographic; ``selected_path_id`` maps to the
canonical position (``None`` on refusal). ``requirement_anchor`` excerpts are
prose and are EXCLUDED by construction.

Mechanical and reproducible: same typed substance → same hash. The full
replay / drift controller (drift events, lineage comparison,
``transparency_policy_version`` migration) is deferred (D-096.4).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canon_ref(ref: Any) -> str:
    """Stable canonical string for one reference (dict or scalar)."""
    if isinstance(ref, dict):
        return json.dumps(ref, sort_keys=True, separators=(",", ":"))
    return json.dumps(ref, separators=(",", ":"))


def _canon_refs(refs: Any) -> list[str]:
    return sorted(_canon_ref(r) for r in (refs or []))


def _candidate_sort_key(path: dict) -> tuple:
    return (
        str(path.get("archetype", "")),
        str(path.get("claim_kind", "")),
        tuple(_canon_refs(path.get("subject_refs"))),
    )


def canonicalize(attempted_interpretation: dict) -> dict:
    """Return the canonical, prose-free structure the hash is computed over.

    Exposed (not just hashed) so tests can assert the canonical form and so the
    structure is inspectable. ``path_id`` is replaced by canonical position.
    """
    ai = attempted_interpretation or {}
    paths = list(ai.get("candidate_paths") or [])

    # Canonical order + position assignment (path_id normalized away).
    ordered = sorted(paths, key=_candidate_sort_key)
    pos_by_pathid: dict[Any, int] = {}
    canon_candidates: list[dict] = []
    for pos, path in enumerate(ordered):
        if path.get("path_id") is not None:
            pos_by_pathid[path["path_id"]] = pos
        canon_candidates.append({
            "position": pos,
            "archetype": path.get("archetype"),
            "claim_kind": path.get("claim_kind"),
            "subject_refs": _canon_refs(path.get("subject_refs")),
            "admissibility_status": path.get("admissibility_status"),
            "admissibility_layer": path.get("admissibility_layer"),
        })

    # Dismissals: reason -> sorted canonical positions; reasons alphabetical.
    dismissed = ai.get("dismissed_alternatives_by_reason") or {}
    canon_dismissed = {
        reason: sorted(pos_by_pathid[pid] for pid in pids if pid in pos_by_pathid)
        for reason, pids in sorted(dismissed.items())
    }

    sel = ai.get("selected_path_id")
    canon_selected = pos_by_pathid.get(sel) if sel is not None else None

    return {
        "scoped_neighborhood": _canon_refs(ai.get("scoped_neighborhood")),
        "candidate_paths": canon_candidates,
        "dismissed_alternatives_by_reason": canon_dismissed,
        "selected_path_id": canon_selected,
    }


def compute_explanation_hash(attempted_interpretation: dict) -> str:
    """SHA-256 hex digest over the canonical typed ``attempted_interpretation``
    (D-075). Deterministic; prose-free by construction."""
    canon = canonicalize(attempted_interpretation)
    payload = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
