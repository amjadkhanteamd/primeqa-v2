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


def _resolve_scope_and_readiness(tenant_id, release_id, keys) -> tuple:
    """The Evaluate act's two pre-conditions, read over ONE tenant session:
    the canonical scope (AUD-014 — empty?) and Step 2's readiness (D-484 —
    current?). A failed scope read is returned AS a failure (``scope=None``),
    never swallowed: the caller refuses on it."""
    from sqlalchemy.orm import Session

    from primeqa.intelligence.quality_evidence import release_scope
    from primeqa.intelligence.substrate_decision import release_scope_readiness
    from primeqa.semantic.connection import get_tenant_connection
    scope, readiness, error = None, None, None
    try:
        with get_tenant_connection(tenant_id) as conn:
            s = Session(bind=conn)
            try:
                scope = release_scope(s, tenant_id=tenant_id, release_id=release_id, keys=keys)
                readiness = release_scope_readiness(tenant_id, keys, session=s)
            finally:
                s.close()
    except Exception as exc:  # noqa: BLE001 — a pre-condition that cannot be read refuses
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    return scope, readiness, error


def _refusal(reason, sentence, *, scope=None, items=None, substrate=None, mode="policy", extra=None) -> dict:
    """The refused envelope — no row written, the reason and the sentence
    naming why. ``items`` carries Step 2's non-current items."""
    out = {
        "refused": True, "reason": reason, "sentence": sentence,
        "recommendation": None, "confidence": None,
        "reasoning": [{"check": ("readiness" if reason in ("scope_not_current", "scope_unavailable")
                                 else "scope" if reason == "scope_empty" else "policy"),
                       "status": "fail", "detail": sentence}],
        "criteria_met": {("readiness" if reason in ("scope_not_current", "scope_unavailable")
                          else "scope" if reason == "scope_empty" else "policy"): False},
        "metrics": None, "mode": mode, "recommendation_source": mode,
        "v1": None, "substrate": substrate, "scope": scope, "items": items or [],
    }
    out.update(extra or {})
    return out


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
    from primeqa.intelligence.substrate_decision import get_release_substrate_decision
    keys = external_keys_for_requirements(
        release_repo.list_requirements(release.id, tenant_id=tenant_id))
    # The act's pre-conditions, in order — emptiness, then currency, then the
    # policy — each a refusal that names its TRUE reason (a non-current scope
    # presupposes a scope; D-494's lesson was refusals for the wrong reason).
    #
    # AUD-014: a decision requires a NON-EMPTY graded scope. Zero checks — no
    # requirement, no live test case, no active target, everything excluded —
    # refuses here, before Step 2 and before the engine (which refuses too, by
    # construction: quality_decision_console.grade_release). A scope that
    # cannot be READ refuses as well: unknown is not satisfied.
    resolved, scope, error = _resolve_scope_and_readiness(tenant_id, release.id, keys)
    if resolved is None:
        return _refusal("scope_unavailable",
                        f"the release's scope could not be read — evaluate refused ({error})",
                        mode="substrate")
    if resolved.get("empty"):
        empty = resolved["empty"]
        return _refusal("scope_empty", empty["sentence"], mode="substrate",
                        scope={k: resolved[k] for k in ("keys", "requirement_count", "functional_ids",
                                                         "conformance_ids", "targets", "targets_source",
                                                         "active_targets", "checks")},
                        extra={"empty": empty})
    # Step 2 (§c): the Evaluate ACT refuses a non-current scope — a readiness
    # FACT, named item by item, with no decision row written.
    if not scope.get("available"):
        return _refusal("scope_unavailable",
                        "the scope's readiness could not be read — evaluate refused",
                        mode="substrate", scope=scope)
    if scope.get("non_current"):
        items = [i for i in scope["items"] if i["state"] != "CURRENT"]
        return _refusal("scope_not_current",
                        f"{len(items)} item(s) in scope are not current — evaluate refused",
                        mode="substrate", scope=scope, items=items)
    # Step 5 (LLD_STEP_5 §b): the RECOMMENDATION is the policy engine's — the
    # active tenant policy over the six evidence axes; gate logic lives only
    # there. The substrate block keeps riding along for CI / the ledger
    # (D-198 slice 4) — a fact set, no longer the release's verdict.
    substrate = get_release_substrate_decision(tenant_id, keys, criteria)
    graded = _grade_under_policy(tenant_id, release.id, keys)
    if not graded.get("ok"):
        # Ruling 3: no active policy → a refusal, never a silent default. (The
        # engine's own emptiness refusal lands here too, should the scope
        # change between the pre-condition read and the grade.)
        return _refusal(graded.get("reason") or "policy_unavailable", graded.get("sentence"),
                        mode="policy", substrate=substrate, scope=scope,
                        extra=({"empty": graded["empty"]} if graded.get("empty") else None))
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
