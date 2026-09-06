"""The repair-agent spine (Build 7, D-215/.1) — deterministic, proposal-only.

Triage consumes S6 interpretations (verdict + cause_kind — never re-parsed
error strings, D-215 §1) and maps each failed/errored run to at most one
deterministic repair proposal:

  - ``regenerate_from_current_org`` — the claim predates the org's current
    truth (``vr_formula_drift`` / ``no_active_vr`` / ``vr_formula_indeterminate``
    / ``rejected_unasserted_reason``): the repair is a fresh S3 generation for
    the claim's requirement (the D-205.1 re-version path).
  - ``rerun`` — the run could not be evaluated (``not_evaluated`` / outcome
    ``errored``): infrastructure, not semantics.

**Findings never get proposals**: ``enforcement_gap`` and the *_not_* verdicts
are the product's OUTPUT. Nothing auto-applies in the spine — a human approves
on the Repairs panel; apply executes immediately and stamps the ledger.
Best-effort consoles throughout (never raise into a page or tick).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

# verdict/cause → proposal kind (the deterministic spine map, D-215.1 §1)
_REGENERATE_CAUSES = frozenset({
    "vr_formula_drift", "no_active_vr", "vr_formula_indeterminate",
})
_REGENERATE_VERDICTS = frozenset({"rejected_unasserted_reason"})
_RERUN_VERDICTS = frozenset({"not_evaluated"})
# the product's findings — NEVER repaired (D-215 §1)
_FINDING_VERDICTS = frozenset({
    "prohibition_not_enforced", "value_not_persisted",
    "state_not_transitioned", "automation_not_triggered",
    "asserted_metadata_absent", "asserted_value_differs",
})
# D-236: the RECIPE-OWNER causes — the test itself is buggy, so the LLM can
# propose a concrete recipe edit. These take PRECEDENCE over the verdict-level
# finding/regenerate mapping (a value_not_persisted is normally a finding, but a
# value_not_persisted CAUSED BY field_not_createable is a recipe bug). Mirrors
# evolution/repair.py:suggest_repairs' "recipe" owner classes.
_RECIPE_EDIT_CAUSES = frozenset({
    "field_not_createable", "automation_effect_absent", "platform_constraint",
    # D-425: the value-aware splits of automation_effect_absent keep its
    # triage mapping (recipe_edit) — every one of these WAS
    # automation_effect_absent before the split, so membership here preserves
    # pre-D-425 behaviour. A per-kind repair policy (e.g. representation
    # mismatch → regenerate the claim) is a separate decision, not taken here.
    "automation_effect_record_absent", "automation_effect_divergent",
    "automation_effect_value_absent", "representation_mismatch",
    # D-427, DECIDED — the absence-mirror causes
    # (automation_effect_record_present, other_writer_produced_record) are
    # deliberately NOT members. A record appearing where the claim says none
    # should is the shape of a GENUINE ORG REGRESSION; auto-firing billed LLM
    # recipe-edits at it would have the system explain away its own best
    # signal. Unlike the D-425 splits (which inherited
    # automation_effect_absent's class), this family has no predecessor —
    # proposal_for falls through to None: a finding, the spine never guesses.
})

# The sentinel the LLM returns to DROP a field (vs any other value = set it).
from primeqa.intelligence.llm.prompts.repair_proposal import (  # noqa: E402
    REMOVE_SENTINEL,
)


def proposal_for(verdict: Optional[str], cause_kind: Optional[str],
                 outcome: Optional[str]) -> Optional[str]:
    """The deterministic triage map. Pure. None = no proposal (a finding, a
    pass, or an unmapped shape — the spine never guesses). D-236: a recipe-owner
    CAUSE wins first — that failure class is a test bug the LLM repairs, not a
    finding/drift."""
    if cause_kind in _RECIPE_EDIT_CAUSES:
        return "recipe_edit"
    if verdict in _FINDING_VERDICTS:
        return None
    if cause_kind in _REGENERATE_CAUSES or verdict in _REGENERATE_VERDICTS:
        return "regenerate_from_current_org"
    if verdict in _RERUN_VERDICTS or outcome == "errored":
        return "rerun"
    return None


def apply_field_changes(field_values: dict, sobject: str,
                        field_changes: dict) -> dict:
    """Pure: apply the LLM's bare-keyed ``field_changes`` onto a recipe create's
    (object-qualified) ``field_values``. ``REMOVE_SENTINEL`` drops the field
    (either key form); any other value sets it, preferring the existing key form
    and otherwise the ``{sobject}.field`` qualified convention (D-115.4). Returns
    a NEW dict — the input is never mutated."""
    out = dict(field_values or {})
    for bare, val in (field_changes or {}).items():
        if not bare:
            continue
        qualified = f"{sobject}.{bare}"
        if val == REMOVE_SENTINEL:
            out.pop(qualified, None)
            out.pop(bare, None)
        elif bare in out and qualified not in out:
            out[bare] = val                      # keep the existing (bare) key
        else:
            out.pop(bare, None)
            out[qualified] = val                 # the recipe's qualified convention
    return out


def _repair_settings(tenant_id: int) -> dict:
    """Best-effort read of the per-tenant repair policy from the PUBLIC
    ``tenant_agent_settings`` (all four fields ORM-mapped since migration
    069, Step A). Returns ``{auto_apply, agent_enabled, gate_apply_enabled,
    max_attempts}`` — the DORMANT defaults on any error (no apply path
    opens because a settings read failed). No threshold is read: the
    apply paths decide on the proposal's gate_verdict, never on the LLM's
    self-reported confidence."""
    out = {"auto_apply": False, "agent_enabled": True,
           "gate_apply_enabled": False, "max_attempts": 3}
    try:
        from primeqa.core.models import TenantAgentSettings
        from primeqa.db import get_db
        db = next(get_db())
        try:
            s = db.query(TenantAgentSettings).filter_by(tenant_id=tenant_id).first()
        finally:
            db.close()
        if s is not None:
            out["agent_enabled"] = bool(getattr(s, "agent_enabled", True))
            out["auto_apply"] = bool(getattr(s, "repair_auto_apply", False))
            out["gate_apply_enabled"] = bool(
                getattr(s, "repair_gate_apply_enabled", False))
            try:
                out["max_attempts"] = int(s.max_fix_attempts_per_run)
            except (TypeError, ValueError):
                pass
    except Exception as exc:                              # pragma: no cover
        log.warning("repair settings unavailable for tenant %s: %s", tenant_id, exc)
    return out


# Step A: agent_enabled=false now gates CREATION. The skip is logged once
# per tenant per process (the ui_schedules / stale_tenants loudly-once
# posture) — visible, never a flood.
_WARNED_DISABLED: set = set()


def _recipe_id_for_run(conn, run_id):
    return conn.execute(text(
        "SELECT recipe_id FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)"),
        {"r": str(run_id)}).scalar()


def _error_evidence_for_run(conn, run_id) -> dict:
    """The failed create step's error surface from the run's evidence JSONB."""
    ev = conn.execute(text(
        "SELECT evidence FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)"),
        {"r": str(run_id)}).scalar() or {}
    for s in (ev.get("steps") or []):
        if isinstance(s, dict) and s.get("kind") == "create" and not s.get("success", True):
            return {"error_code": s.get("error_code"),
                    "error_message": s.get("message"),
                    "error_fields": list(s.get("error_fields") or [])}
    return {"error_code": None, "error_message": None, "error_fields": []}


def _read_subject_create(session, recipe_id):
    """``(recipe_read, body, idx, sobject, field_values)`` for the recipe's
    SUBJECT create (the last positive CreateStep), or None when the current
    recipe is not a data recipe with such a create. ``body`` is the live
    ``DataRecipeBody`` (observation_realization)."""
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    rr = SemanticTransactionCoordinator().get_recipe_latest(session, recipe_id)
    if rr is None:
        return None
    body = rr.observation_realization
    steps = list(getattr(body, "steps", None) or [])
    idx = None
    for i, s in enumerate(steps):
        if (getattr(s, "kind", None) == "create"
                and getattr(s, "expect_rejection", None) is None):
            idx = i
    if idx is None:
        return None
    create = steps[idx]
    sobject = getattr(create.target_object, "external_id", None) or "the object"
    return rr, body, idx, sobject, dict(create.field_values or {})


def _propose_recipe_edit(conn, tenant_id: int, row, api_key: Optional[str]):
    """Build the failure context + call the LLM → ``{confidence, field_changes,
    rationale}`` or None (no key / no recipe / LLM fail / no usable edit).
    Best-effort — never raises."""
    if not api_key:
        return None
    try:
        from sqlalchemy.orm import Session
        recipe_id = _recipe_id_for_run(conn, row["run_id"])
        if not recipe_id:
            return None
        session = Session(bind=conn)
        try:
            sub = _read_subject_create(session, recipe_id)
        finally:
            session.close()
        if sub is None:
            return None
        _, _, _, sobject, field_values = sub
        err = _error_evidence_for_run(conn, row["run_id"])
        from primeqa.intelligence.llm.gateway import llm_call
        resp = llm_call(
            task="repair_proposal", tenant_id=tenant_id, api_key=api_key,
            context={"verdict": row["verdict"], "cause_kind": row["cause_kind"],
                     "sobject": sobject, "current_field_values": field_values,
                     "error_code": err["error_code"],
                     "error_message": err["error_message"],
                     "error_fields": err["error_fields"],
                     "run_id": str(row["run_id"]),
                     "claim_test_id": str(row["claim_test_id"])})
        parsed = getattr(resp, "parsed_content", None)
        if not isinstance(parsed, dict) or parsed.get("_parse_error"):
            return None
        fc = parsed.get("field_changes")
        if not isinstance(fc, dict) or not fc:
            return None                          # no usable edit — leave for next tick
        try:
            conf = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        return {"confidence": conf, "field_changes": fc,
                "rationale": str(parsed.get("rationale") or "")}
    except Exception as exc:
        log.warning("repair_proposal LLM failed for tenant %s run %s: %s",
                    tenant_id, row["run_id"], exc)
        return None


def _recipe_edit_attempts(conn, claim_test_id) -> int:
    """How many recipe_edit proposals were already APPLIED for this claim — the
    attempt-cap basis (no fix-loops)."""
    return conn.execute(text(
        "SELECT COUNT(*) FROM repair_proposals "
        "WHERE claim_test_id = CAST(:t AS uuid) "
        "  AND proposal_kind = 'recipe_edit' AND status = 'applied'"),
        {"t": str(claim_test_id)}).scalar() or 0


def triage_new_failures(tenant_id: int, *, limit: int = 50,
                        api_key_resolver=None) -> dict:
    """Scan failed/errored interpretations that have no proposal yet and write
    proposals for the mapped shapes (proposed status; dedup by the partial
    unique index — one active proposal per (claim, kind)). Best-effort; never
    raises. Returns ``{proposed: n, scanned: n}``.

    D-236: a ``recipe_edit`` kind first calls the LLM (``api_key_resolver`` →
    a per-env key) to fill the proposed edit + confidence; if the model produces
    no usable edit (or no key) the run is SKIPPED (a later tick retries). The
    attempt cap (``max_fix_attempts_per_run``) stops fix-loops per claim."""
    try:
        from primeqa.intelligence import repair_gate
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.shared.stale_tenants import skip_unprovisioned
        proposed = scanned = 0
        settings = _repair_settings(tenant_id)
        if not settings["agent_enabled"]:
            if tenant_id not in _WARNED_DISABLED:
                _WARNED_DISABLED.add(tenant_id)
                log.warning("repair triage: agent_enabled=false for tenant %s "
                            "— no proposals are created (warned once per "
                            "process)", tenant_id)
            return {"proposed": 0, "scanned": 0, "disabled": True}
        with get_tenant_connection(tenant_id) as conn:
            if skip_unprovisioned(conn, tenant_id, "s6_interpretations", log):
                return {"proposed": 0, "scanned": 0, "unprovisioned": True}
            rows = conn.execute(text(
                "SELECT i.run_id, i.claim_test_id, i.outcome::text AS outcome, "
                "       i.verdict, i.cause_kind, r.environment_id "
                "FROM s6_interpretations i "
                "JOIN s4_execution_runs r ON r.run_id = i.run_id "
                "WHERE i.outcome IN ('failed', 'errored') "
                "  AND NOT EXISTS (SELECT 1 FROM repair_proposals p "
                "                  WHERE p.run_id = i.run_id) "
                "ORDER BY r.finished_at DESC LIMIT :lim"),
                {"lim": limit}).mappings().all()
            for row in rows:
                scanned += 1
                kind = proposal_for(row["verdict"], row["cause_kind"],
                                    row["outcome"])
                if kind is None:
                    continue
                confidence = None
                proposed_payload = {}
                if kind == "recipe_edit":
                    # attempt cap — stop fix-loops per claim.
                    if _recipe_edit_attempts(conn, row["claim_test_id"]) >= \
                            settings["max_attempts"]:
                        continue
                    # D-236 review fix: the scan is keyed on run_id but the dedup
                    # index is keyed on (claim_test_id, kind) — a duplicate failing
                    # run for the SAME claim would pass the scan and pay for an LLM
                    # call only to lose the INSERT to ON CONFLICT. Short-circuit
                    # BEFORE the (billed) call when an active proposal already exists.
                    if conn.execute(text(
                            "SELECT 1 FROM repair_proposals "
                            "WHERE claim_test_id = CAST(:t AS uuid) "
                            "  AND proposal_kind = 'recipe_edit' "
                            "  AND status IN ('proposed', 'approved') LIMIT 1"),
                            {"t": str(row["claim_test_id"])}).first() is not None:
                        continue
                    api_key = (api_key_resolver(tenant_id, row["environment_id"])
                               if api_key_resolver else None)
                    edit = _propose_recipe_edit(conn, tenant_id, row, api_key)
                    if edit is None:
                        continue                 # no usable LLM edit — skip; retry later
                    confidence = edit["confidence"]
                    proposed_payload = {"field_changes": edit["field_changes"],
                                        "rationale": edit["rationale"]}
                # Step A: the gate runs BEFORE the write — no proposal row
                # exists without a verdict and its grounding source.
                gate = repair_gate.classify_row(
                    conn, tenant_id, {**dict(row), "proposal_kind": kind},
                    proposed_payload.get("field_changes"))
                n = conn.execute(text(
                    "INSERT INTO repair_proposals "
                    "(run_id, claim_test_id, environment_id, verdict, "
                    " cause_kind, proposal_kind, payload, confidence, "
                    " proposed_payload, gate_verdict, grounding_source, "
                    " classified_at, classifier_version) "
                    "VALUES (:rid, :tid, :eid, :v, :c, :k, CAST(:p AS jsonb), "
                    "        :conf, CAST(:pp AS jsonb), :gv, CAST(:gs AS jsonb), "
                    "        :at, :cv) "
                    "ON CONFLICT DO NOTHING"),
                    {"rid": str(row["run_id"]), "tid": str(row["claim_test_id"]),
                     "eid": row["environment_id"], "v": row["verdict"],
                     "c": row["cause_kind"], "k": kind,
                     "p": json.dumps({}), "conf": confidence,
                     "pp": json.dumps(proposed_payload),
                     "gv": gate.verdict,
                     "gs": json.dumps(gate.grounding, default=str),
                     "at": datetime.now(timezone.utc),
                     "cv": repair_gate.CLASSIFIER_VERSION}).rowcount
                proposed += n
        return {"proposed": proposed, "scanned": scanned}
    except Exception as exc:
        log.warning("repair triage failed for tenant %s: %s", tenant_id, exc)
        return {"proposed": 0, "scanned": 0}


def list_proposals(tenant_id: int, *, statuses=("proposed", "approved"),
                   limit: int = 50) -> dict:
    """Best-effort: the Repairs panel read. Never raises."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            rows = conn.execute(text(
                "SELECT id, run_id, claim_test_id, environment_id, verdict, "
                "       cause_kind, proposal_kind, status, payload, "
                "       proposed_payload, created_at, gate_verdict, "
                "       grounding_source "
                "FROM repair_proposals WHERE status = ANY(:st) "
                "ORDER BY created_at DESC LIMIT :lim"),
                {"st": list(statuses), "lim": limit}).mappings().all()
            # Step A header: verdict counts over the OPEN proposals — one
            # GROUP BY, computed here, never in the template.
            counts = {"DERIVED": 0, "SPECULATIVE": 0, "SEMANTIC": 0,
                      "UNCLASSIFIED": 0}
            for v, n in conn.execute(text(
                    "SELECT COALESCE(gate_verdict, 'UNCLASSIFIED'), COUNT(*) "
                    "FROM repair_proposals WHERE status = 'proposed' "
                    "GROUP BY 1")).all():
                counts[v] = int(n)
        return {"available": True, "verdict_counts": counts, "proposals": [{
            "id": r["id"], "run_id": str(r["run_id"]),
            "claim_test_id": str(r["claim_test_id"]),
            "environment_id": r["environment_id"],
            "verdict": r["verdict"], "cause_kind": r["cause_kind"],
            "proposal_kind": r["proposal_kind"], "status": r["status"],
            "payload": r["payload"],
            # Step A: the gate verdict decides the action; the LLM's
            # confidence is NOT read here — it is audit-only.
            "gate_verdict": r["gate_verdict"],
            "grounding": r["grounding_source"] or {},
            "destination": (r["grounding_source"] or {}).get("destination"),
            "proposed_payload": r["proposed_payload"] or {},
            "created_at": r["created_at"].isoformat(),
        } for r in rows]}
    except Exception as exc:
        log.warning("list_proposals failed for tenant %s: %s", tenant_id, exc)
        return {"available": False, "proposals": []}


def open_proposal_for_run(tenant_id: int, run_id) -> Optional[dict]:
    """Best-effort: the open ('proposed') repair proposal for ONE run, or None
    (D-231). Lets the run-detail page surface the actionable fix inline instead of
    dead-ending at read-only suggestion text. Never raises."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT id, proposal_kind, proposed_payload, gate_verdict, "
                "       grounding_source, claim_test_id "
                "FROM repair_proposals "
                "WHERE run_id = CAST(:rid AS uuid) AND status = 'proposed' "
                "ORDER BY created_at DESC LIMIT 1"),
                {"rid": str(run_id)}).mappings().first()
        if row is None:
            return None
        return {"id": row["id"], "proposal_kind": row["proposal_kind"],
                "claim_test_id": str(row["claim_test_id"]),
                "gate_verdict": row["gate_verdict"],
                "grounding": row["grounding_source"] or {},
                "destination": (row["grounding_source"] or {}).get("destination"),
                "proposed_payload": row["proposed_payload"] or {}}
    except Exception as exc:
        log.warning("open_proposal_for_run failed for tenant %s run %s: %s",
                    tenant_id, run_id, exc)
        return None


def decide_proposal(tenant_id: int, proposal_id: int, *, approve: bool,
                    decided_by: Optional[int] = None) -> dict:
    """Approve (and immediately APPLY) or reject one proposal. Apply:
    ``rerun`` → enqueue S4 on the proposal's environment;
    ``regenerate_from_current_org`` → enqueue S3 for the claim's
    ``generated_from`` requirement (idempotency at an unchanged S1 seq is
    reported as already-current, not a failure). Best-effort; never raises."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        with get_tenant_connection(tenant_id) as conn:
            row = conn.execute(text(
                "SELECT id, run_id, claim_test_id, environment_id, "
                "       proposal_kind, status, gate_verdict, grounding_source "
                "FROM repair_proposals "
                "WHERE id = :pid"), {"pid": proposal_id}).mappings().first()
        if row is None or row["status"] not in ("proposed", "approved"):
            return {"ok": False, "error": "proposal not found or already decided"}

        if not approve:
            _stamp(tenant_id, proposal_id, "rejected", decided_by, {})
            return {"ok": True, "status": "rejected"}

        # Step A: the refusal is the control (hiding the button is only
        # presentation). Apply requires the dormant-first switch ON, a
        # DERIVED verdict and a recorded grounding source — never the
        # LLM's confidence.
        refusal = _apply_refusal(_repair_settings(tenant_id), row)
        if refusal:
            return {"ok": False, "status": row["status"], "refused": True,
                    "gate_verdict": row["gate_verdict"], "error": refusal}

        outcome = _apply(tenant_id, row)
        # D-236 review fix: a genuine apply failure (recipe_edit with no subject
        # create / no recipe; regenerate with no link) must NOT be mis-stamped
        # 'applied' — that would vanish the proposal from the panel as a false
        # success. Leave it 'proposed' for retry + surface the error (mirrors the
        # auto-apply path's `if outcome.get('error')` guard).
        if outcome.get("error"):
            return {"ok": False, "status": "proposed", "error": outcome["error"],
                    **outcome}
        _stamp(tenant_id, proposal_id, "applied", decided_by, outcome)
        return {"ok": True, "status": "applied", **outcome}
    except Exception as exc:
        log.warning("decide_proposal failed for tenant %s proposal %s: %s",
                    tenant_id, proposal_id, exc)
        return {"ok": False, "error": str(exc)}


def _apply_refusal(settings: dict, row) -> Optional[str]:
    """Why this proposal may NOT be applied right now — ``None`` when it may.
    Pure over the settings + the row's gate columns."""
    if not settings.get("gate_apply_enabled"):
        return ("apply actions are dormant — the repair gate switch is off "
                "(Settings › Agent)")
    gv = row.get("gate_verdict")
    if gv != "DERIVED":
        return (f"{gv or 'UNCLASSIFIED'}: not applicable — only a DERIVED "
                "proposal with a recorded grounding source can be applied")
    if not (row.get("grounding_source") or {}):
        return "DERIVED without a recorded grounding source — refused"
    return None


def _apply_recipe_edit(tenant_id: int, row) -> dict:
    """Apply a recipe_edit (D-236): write a NEW recipe version with the LLM's
    field_changes on the subject create (actor 's8' → recipe_s8_rewrite
    provenance; the prior version is preserved → reversible), then enqueue a
    re-verify run. Best-effort: returns ``{action, error}`` on any failure."""
    from sqlalchemy.orm import Session

    from primeqa.execution_engine.intake import enqueue_s4_execution
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    try:
        with get_tenant_connection(tenant_id) as conn:
            pp = conn.execute(text(
                "SELECT proposed_payload FROM repair_proposals WHERE id = :pid"),
                {"pid": row["id"]}).scalar() or {}
            field_changes = (pp or {}).get("field_changes") or {}
            if not field_changes:
                return {"action": "recipe_edit", "error": "no proposed field_changes"}
            recipe_id = _recipe_id_for_run(conn, row["run_id"])
            if not recipe_id:
                return {"action": "recipe_edit", "error": "no recipe for run"}
            session = Session(bind=conn)
            try:
                sub = _read_subject_create(session, recipe_id)
                if sub is None:
                    return {"action": "recipe_edit",
                            "error": "no subject create on current recipe"}
                rr, body, idx, sobject, field_values = sub
                new_fv = apply_field_changes(field_values, sobject, field_changes)
                new_steps = list(body.steps)
                new_steps[idx] = body.steps[idx].model_copy(
                    update={"field_values": new_fv})
                new_body = body.model_copy(update={"steps": new_steps})
                res = SemanticTransactionCoordinator().write_recipe(
                    session, actor="s8", recipe_id=recipe_id,
                    claim_test_id=rr.claim_test_id,
                    trigger_kind=rr.trigger_kind, recipe_kind=rr.recipe_kind,
                    causal_initiation=rr.causal_initiation,
                    observation_realization=new_body,
                    execution_environment=rr.execution_environment,
                    claim_version_seq=rr.claim_version_seq, priority=rr.priority)
                session.commit()
            finally:
                session.close()
        job = enqueue_s4_execution(
            tenant_id=tenant_id, test_id=row["claim_test_id"],
            environment_id=row["environment_id"])
        return {"action": "recipe_edit", "recipe_id": str(recipe_id),
                "new_version_seq": res.version_seq, "s4_job_id": job.id}
    except Exception as exc:
        log.warning("recipe_edit apply failed for tenant %s proposal %s: %s",
                    tenant_id, row.get("id"), exc)
        return {"action": "recipe_edit", "error": str(exc)}


def _apply(tenant_id: int, row) -> dict:
    """Execute one approved proposal. Returns the payload to stamp."""
    if row["proposal_kind"] == "recipe_edit":
        return _apply_recipe_edit(tenant_id, row)
    if row["proposal_kind"] == "rerun":
        from primeqa.execution_engine.intake import enqueue_s4_execution
        job = enqueue_s4_execution(
            tenant_id=tenant_id, test_id=row["claim_test_id"],
            environment_id=row["environment_id"])
        return {"action": "rerun", "s4_job_id": job.id}

    # regenerate_from_current_org: resolve the claim's requirement key, then
    # enqueue a fresh S3 generation (the D-205.1 re-version path; idempotent
    # per (key, s1_seq) — an unchanged org reports already-current).
    from sqlalchemy import text as _text

    from primeqa.generation.intake import enqueue_s3_generation
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        key = conn.execute(_text(
            "SELECT external_key FROM test_requirement_links "
            "WHERE test_id = :tid AND link_kind = 'generated_from' "
            "ORDER BY linked_at DESC LIMIT 1"),
            {"tid": str(row["claim_test_id"])}).scalar()
    if not key:
        return {"action": "regenerate", "error": "no generated_from link"}
    try:
        job = enqueue_s3_generation(
            tenant_id=tenant_id,
            requirement_ref={"key": key, "text": ""},
            environment_id=row["environment_id"])
        already = job.status not in ("queued",)
        return {"action": "regenerate", "requirement_key": key,
                "s3_job_id": job.id,
                "note": ("already generated at the current org version"
                         if already else None)}
    except Exception as exc:
        return {"action": "regenerate", "requirement_key": key,
                "error": str(exc)}


def _env_is_production(tenant_id: int, env_id) -> bool:
    """Best-effort: is this env production? Unknown → True (the SAFE default — an
    env the agent can't classify is NEVER auto-applied)."""
    try:
        from primeqa.core.repository import EnvironmentRepository
        from primeqa.db import get_db
        db = next(get_db())
        try:
            env = EnvironmentRepository(db).get_environment(env_id, tenant_id)
        finally:
            db.close()
        return True if env is None else bool(getattr(env, "is_production", True))
    except Exception:                                    # pragma: no cover
        return True


def auto_apply_proposals(tenant_id: int, *, limit: int = 20) -> dict:
    """D-236 + Step A: the FLAG-GATED autonomous-apply pass. DORMANT unless
    the tenant's ``repair_auto_apply`` AND ``agent_enabled`` AND the
    dormant-first ``repair_gate_apply_enabled`` switch are ALL on. For each
    fresh ``proposed`` recipe_edit whose gate_verdict is DERIVED with a
    recorded grounding source, on a SANDBOX env, under the attempt cap:
    apply it + stamp applied / auto_applied. ``confidence`` is NEVER read
    (the guesser's own score is not a gate). A PRODUCTION env is NEVER
    auto-applied (always human). Best-effort; never raises. Returns
    ``{applied, skipped}``."""
    settings = _repair_settings(tenant_id)
    if not (settings["auto_apply"] and settings["agent_enabled"]
            and settings["gate_apply_enabled"]):
        return {"applied": 0, "skipped": 0}              # dormant — the default
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.shared.stale_tenants import skip_unprovisioned
        applied = skipped = 0
        with get_tenant_connection(tenant_id) as conn:
            if skip_unprovisioned(conn, tenant_id, "repair_proposals", log):
                return {"applied": 0, "skipped": 0, "unprovisioned": True}
            rows = conn.execute(text(
                "SELECT id, run_id, claim_test_id, environment_id, proposal_kind, "
                "       gate_verdict, grounding_source FROM repair_proposals "
                "WHERE status = 'proposed' AND proposal_kind = 'recipe_edit' "
                "ORDER BY created_at ASC LIMIT :lim"),
                {"lim": limit}).mappings().all()
        for row in rows:
            if _apply_refusal(settings, row):
                skipped += 1                             # not DERIVED / no grounding
                continue
            if _env_is_production(tenant_id, row["environment_id"]):
                skipped += 1                             # prod is always human-gated
                continue
            with get_tenant_connection(tenant_id) as conn:
                if _recipe_edit_attempts(conn, row["claim_test_id"]) >= \
                        settings["max_attempts"]:
                    skipped += 1
                    continue
            outcome = _apply(tenant_id, row)
            if outcome.get("error"):
                skipped += 1
                continue
            with get_tenant_connection(tenant_id) as conn:
                conn.execute(text(
                    "UPDATE repair_proposals SET status = 'applied', "
                    "auto_applied = true, decided_at = :at, "
                    "payload = payload || CAST(:p AS jsonb) WHERE id = :pid"),
                    {"at": datetime.now(timezone.utc),
                     "p": json.dumps({k: v for k, v in outcome.items()
                                      if v is not None}),
                     "pid": row["id"]})
            applied += 1
        return {"applied": applied, "skipped": skipped}
    except Exception as exc:
        log.warning("auto_apply_proposals failed for tenant %s: %s", tenant_id, exc)
        return {"applied": 0, "skipped": 0}


def _stamp(tenant_id: int, proposal_id: int, status: str,
           decided_by: Optional[int], payload: dict) -> None:
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        conn.execute(text(
            "UPDATE repair_proposals SET status = :st, decided_by = :by, "
            "decided_at = :at, payload = payload || CAST(:p AS jsonb) "
            "WHERE id = :pid"),
            {"st": status, "by": decided_by,
             "at": datetime.now(timezone.utc),
             "p": json.dumps({k: v for k, v in payload.items() if v is not None}),
             "pid": proposal_id})
