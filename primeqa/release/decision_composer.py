"""Release decision composer — theme #3 slice 3 (D-198); Step 5: the policy
engine owns the recommendation (LLD_STEP_5_QUALITY_POLICY §b).

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
_EFFECT_STATUS = {"BLOCK": "fail", "REVIEW": "fail", "CONDITIONAL": "warn", "ALLOW": "pass", None: "pass"}


def _grade_under_policy(tenant_id, release_id, keys) -> dict:
    """Step 5: the active policy over the assembled evidence, in one tenant
    transaction; the policy is marked USED (frozen) in that same transaction,
    BEFORE the public decision row is written — the conservative order across
    the two connections (a used-but-unrecorded policy is merely frozen early;
    a recorded decision under an unfrozen policy would be the real defect)."""
    from primeqa.intelligence import quality_policy as qp
    from primeqa.intelligence.quality_decision_console import _with_session, grade_release

    def _do(s):
        out = grade_release(s, tenant_id=tenant_id, release_id=release_id, keys=keys)
        if out.get("ok"):
            qp.mark_used(s, policy_id=out["decision"]["policy"]["id"])
        return out
    try:
        return _with_session(tenant_id, None, _do)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "policy_unavailable",
                "sentence": f"The quality policy could not grade this release: {str(exc)[:200]}"}


def external_keys_for_requirements(requirements) -> list:
    """The requirement→IDENTITY key convention — one builder for the views
    panel + the composer so the two call sites can't drift. Step 1: the
    row's ``external_key`` (the identity it decorates), falling back to the
    byte-identical pre-072 derivation. Accepts ORM rows or dicts."""
    from primeqa.test_representation.identity import key_for_requirement_row
    keys = []
    for r in requirements or []:
        rid = r.get("id") if isinstance(r, dict) else getattr(r, "id", None)
        if rid:
            keys.append(key_for_requirement_row(r))
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
        release_scope_readiness,
    )
    keys = external_keys_for_requirements(
        release_repo.list_requirements(release.id, tenant_id=tenant_id))
    # Step 2 (§c): the Evaluate ACT refuses a non-current scope — a readiness
    # FACT, named item by item, with no decision row written. (The policy
    # object that will make this configurable is Step 5.)
    scope = release_scope_readiness(tenant_id, keys)
    if scope.get("available") and scope.get("non_current"):
        items = [i for i in scope["items"] if i["state"] != "CURRENT"]
        return {
            "refused": True, "reason": "scope_not_current",
            "recommendation": None, "confidence": None,
            "reasoning": [{"check": "readiness", "status": "fail",
                           "detail": f"{len(items)} item(s) in scope are not "
                                     "current — evaluate refused"}],
            "criteria_met": {"readiness": False}, "metrics": None,
            "mode": "substrate", "recommendation_source": "substrate",
            "v1": None, "substrate": None, "scope": scope, "items": items,
        }
    # Step 5 (LLD_STEP_5 §b): the RECOMMENDATION is the policy engine's — the
    # active tenant policy over the six evidence axes; gate logic lives only
    # there. The substrate block keeps riding along for CI / the ledger
    # (D-198 slice 4) — a fact set, no longer the release's verdict.
    substrate = get_release_substrate_decision(tenant_id, keys, criteria)
    graded = _grade_under_policy(tenant_id, release.id, keys)
    if not graded.get("ok"):
        # Ruling 3: no active policy → a refusal, never a silent default.
        return {
            "refused": True, "reason": graded.get("reason") or "policy_unavailable",
            "sentence": graded.get("sentence"),
            "recommendation": None, "confidence": None,
            "reasoning": [{"check": "policy", "status": "fail", "detail": graded.get("sentence")}],
            "criteria_met": {"policy": False}, "metrics": None,
            "mode": "policy", "recommendation_source": "policy",
            "v1": None, "substrate": substrate, "scope": scope, "items": [],
        }
    decision = graded["decision"]
    combined = {
        "recommendation": decision["recommendation"],
        "confidence": decision["confidence"],
        # the ledger's reasoning rows: one per evidence line (status ← effect)
        "reasoning": [{"check": ln["axis"], "status": _EFFECT_STATUS.get(ln["effect"], "pass"),
                       "detail": f"{ln['observed']} — {ln['rule']}"
                                 + (f" → {ln['effect']}" if ln["effect"] else "")}
                      for ln in decision["evidence_lines"]],
        "criteria_met": {ln["axis"]: ln["effect"] not in ("BLOCK", "REVIEW") for ln in decision["evidence_lines"]},
        "metrics": (substrate.get("metrics") if substrate.get("applicable") else None),
    }

    envelope = {
        **combined,
        "mode": "policy",
        "recommendation_source": "policy",
        "v1": None,
        "substrate": substrate,
        "policy": decision["policy"],
        "plan_id": decision["plan_id"],
        "decision": decision,
    }
    release_repo.create_decision(
        release_id=release.id,
        recommendation=combined["recommendation"],
        confidence=combined["confidence"],
        reasoning=envelope,
        criteria_met=combined.get("criteria_met"),
        recommended_by="ai",
        policy_id=decision["policy"]["id"],
        policy_version=decision["policy"]["version"],
        plan_id=decision["plan_id"],
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
