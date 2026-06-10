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
    from primeqa.release.decision_engine import DecisionEngine

    v1 = DecisionEngine(db).evaluate(release)

    criteria = release.decision_criteria or {}
    mode = criteria.get("substrate_mode", "advisory")

    substrate = None
    if mode != "off":
        from primeqa.intelligence.substrate_decision import (
            get_release_substrate_decision,
        )
        keys = external_keys_for_requirements(
            release_repo.list_requirements(release.id))
        substrate = get_release_substrate_decision(tenant_id, keys, criteria)

    combined = dict(v1)                              # never mutate v1's dict
    recommendation_source = "v1"
    if (mode == "gating" and substrate is not None
            and substrate.get("available") and substrate.get("applicable")
            and _SEVERITY[substrate["recommendation"]]
            < _SEVERITY[v1["recommendation"]]):
        combined["recommendation"] = substrate["recommendation"]
        combined["confidence"] = substrate["confidence"]
        recommendation_source = "substrate_gate"
        combined["reasoning"] = list(v1.get("reasoning") or []) + [{
            "check": "substrate_gate", "status": "fail",
            "detail": (f"Substrate evidence degraded the recommendation to "
                       f"{substrate['recommendation']} "
                       f"(v1: {v1['recommendation']})"),
        }]

    envelope = {
        **combined,
        "mode": mode,
        "recommendation_source": recommendation_source,
        "v1": v1,
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
    return envelope
