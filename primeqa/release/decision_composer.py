"""Release decision composer — theme #3 slice 3 (D-198).

Runs BOTH decision engines — the v1 ``DecisionEngine`` (zero-diff, its tables
retire in 5b) and the substrate's ``get_release_substrate_decision`` (best-effort,
never raises) — combines them per the release's ``decision_criteria.substrate_mode``,
and records ONE ``ReleaseDecision`` row whose ``reasoning`` JSON carries the full
``{v1, substrate, mode, recommendation_source, ...}`` envelope (no migration —
``reasoning`` is a JSON column).

Modes (default **advisory**):
  - ``off``       — v1 only; the substrate is not queried.
  - ``advisory``  — v1's recommendation stands; the substrate block rides along
                    for the human + CI.
  - ``gating``    — degrade-only: the substrate can VETO (min-severity over
                    ``no_go < conditional_go < go``), never upgrade; scores are
                    never blended. A ``substrate_gate`` reasoning entry records
                    when it degraded.

The composer isolation is the 5b seam: retiring v1 later means dropping one
input here, not surgery inside an entangled engine.
"""
from __future__ import annotations

_SEVERITY = {"no_go": 0, "conditional_go": 1, "go": 2}


def external_keys_for_requirements(requirements) -> list:
    """The shared requirement→external-key convention (``jira_key`` or
    ``req-<id>``) — one builder for the views panel + the composer so the two
    call sites can't drift. Accepts ORM rows or dicts."""
    keys = []
    for r in requirements or []:
        if isinstance(r, dict):
            rid, jira = r.get("id"), r.get("jira_key")
        else:
            rid, jira = getattr(r, "id", None), getattr(r, "jira_key", None)
        if rid:
            keys.append(jira or f"req-{rid}")
    return keys


def evaluate_and_record(db, release, tenant_id, *, release_repo) -> dict:
    """Evaluate v1 + substrate, combine per ``substrate_mode``, persist one
    decision row, and return the combined envelope (the route's response body).

    The top-level ``{recommendation, confidence, reasoning, criteria_met,
    metrics}`` stay v1-shaped; when no substrate evidence applies they are
    byte-identical to v1's — the envelope only gains ``{mode,
    recommendation_source, v1, substrate}``.
    """
    # D-221 R4: the v1 DecisionEngine retired with its engine (D-220 verified
    # its corpus was empty — every v1 verdict was vacuous). The substrate
    # decision IS the recommendation now; the envelope keeps the {mode,
    # recommendation_source, v1, substrate} keys so the ledger / CI / template
    # render uniformly across old and new rows (v1 is None on new rows).
    criteria = release.decision_criteria or {}
    from primeqa.intelligence.substrate_decision import (
        get_release_substrate_decision,
    )
    keys = external_keys_for_requirements(
        release_repo.list_requirements(release.id))
    substrate = get_release_substrate_decision(tenant_id, keys, criteria)

    if substrate.get("available") and substrate.get("applicable"):
        combined = {
            "recommendation": substrate["recommendation"],
            "confidence": substrate["confidence"],
            "reasoning": substrate["reasoning"],
            "criteria_met": substrate.get("criteria_met"),
            "metrics": substrate.get("metrics"),
        }
    else:
        combined = {
            "recommendation": "no_go", "confidence": 0.5,
            "reasoning": [{"check": "has_evidence", "status": "fail",
                           "detail": "No substrate test evidence for this "
                                     "release's requirements"}],
            "criteria_met": {"has_evidence": False}, "metrics": None,
        }

    envelope = {
        **combined,
        "mode": "substrate",
        "recommendation_source": "substrate",
        "v1": None,
        "substrate": substrate,
    }
    release_repo.create_decision(
        release_id=release.id,
        recommendation=combined["recommendation"],
        confidence=combined["confidence"],
        reasoning=envelope,
        criteria_met=combined.get("criteria_met"),
        recommended_by="ai",
    )

    # D-200: heads-up email on a non-clean verdict (best-effort, never blocks
    # the decision; the log provider is the safe default).
    try:
        from primeqa.shared.notifications import notify_release_decision
        notify_release_decision(
            db, tenant_id, getattr(release, "name", f"#{release.id}"), envelope)
    except Exception:                                    # pragma: no cover
        pass
    return envelope
