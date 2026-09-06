"""Step A — the repair-proposal three-verdict gate (LLD_STEP_A_REPAIR_GATE).

A repair proposal is an LLM guess until something RECORDED says otherwise.
This module classifies every proposal — at creation (the triage tick) and
retroactively (the ``retro`` command) — into exactly one verdict derived
from recorded facts, and records WHICH fact:

  SEMANTIC     the remedy touches a field the claim asserts (the D-454
               staged+asserted intersection, reused), OR the proposal
               cannot be classified (bare staged key, unreadable recipe,
               unsupported claim kind, empty remedy). Refused. Fail closed.
  DERIVED      the diagnosis is grounded in a recorded fact AND the remedy
               value is derived, not chosen:
                 R1  attested removal — exactly one field, named by the
                     platform error, that S1 records as non-createable or
                     absent on the subject object;
                 R2  recorded picklist value — the restricted-picklist
                     error names the field and the remedy is the set's
                     recorded DEFAULT or its SOLE active value (D4);
                 K   the deterministic kinds (rerun / regenerate) — no
                     recipe mutation, grounded in the S6 outcome/cause (D1).
  SPECULATIVE  everything else: inference only, or a derived diagnosis
               with a CHOSEN value. The operator edits; nothing applies.

Order is RATIFIED: SEMANTIC evaluates first, then DERIVED, else
SPECULATIVE. ``classify`` is PURE (no I/O) over :class:`GateInputs`;
``gather_inputs`` is the impure reader (recipe at the run's pinned
version, the claim's asserted fields, the failed create's error, S1 field
facts at the org's CURRENT sequence — the run stamps no org sequence, so
the sequence used is recorded). ``repair_proposals.confidence`` is never
read here or anywhere on an apply path: it is the guesser's own score.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text

log = logging.getLogger("primeqa.repair_gate")

CLASSIFIER_VERSION = "gate@v1"

DERIVED = "DERIVED"
SPECULATIVE = "SPECULATIVE"
SEMANTIC = "SEMANTIC"
VERDICTS = (DERIVED, SPECULATIVE, SEMANTIC)

# The claim kinds whose asserted-field extractor is defined (the five
# data-behaviour bodies registered under models/claims/data_behavior). Any
# other kind → SEMANTIC (fail closed) with the reason recorded.
SUPPORTED_CLAIM_KINDS = frozenset({
    "acceptance-claim", "automation-effect-claim", "prohibition-claim",
    "state-transition-claim", "value-claim",
})

# Platform error codes that name a FIELD the org rejected on create.
_REMOVAL_ERROR_CODES = frozenset({
    "INVALID_FIELD", "INVALID_FIELD_FOR_INSERT_UPDATE",
    "CANNOT_INSERT_UPDATE_ACTIVATE_ENTITY",
})
_PICKLIST_ERROR_CODE = "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST"


def _remove_sentinel() -> str:
    from primeqa.intelligence.llm.prompts.repair_proposal import REMOVE_SENTINEL
    return REMOVE_SENTINEL


# ---------------------------------------------------------------------------
# Inputs + result (plain data — the pure classifier's whole world)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldFact:
    """What S1 records about ONE field of the subject object at ``s1_seq``."""
    exists: bool
    is_createable: Optional[bool] = None
    picklist_active_values: Optional[tuple] = None   # api names, active only
    picklist_default: Optional[str] = None
    entity_id: Optional[str] = None


@dataclass
class GateInputs:
    proposal_kind: str
    field_changes: dict = field(default_factory=dict)
    staged_keys: tuple = ()               # the subject create's raw keys
    sobject: Optional[str] = None
    recipe_readable: bool = False
    asserted_fields: frozenset = frozenset()   # bare, lowercased
    claim_kind: Optional[str] = None
    claim_readable: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_fields: tuple = ()
    s1_facts: dict = field(default_factory=dict)   # bare lower -> FieldFact
    s1_seq: Optional[int] = None
    s1_org: Optional[str] = None
    cause_kind: Optional[str] = None
    s6_verdict: Optional[str] = None
    outcome: Optional[str] = None
    failure_category: Optional[str] = None
    claim_version_seq: Optional[int] = None
    destination: Optional[dict] = None    # the requirement, resolved
    recipe_version_seq: Optional[int] = None


@dataclass(frozen=True)
class GateResult:
    verdict: str
    grounding: dict


# ---------------------------------------------------------------------------
# Normalisation — the D-454 discipline (bare, lowercased field names)
# ---------------------------------------------------------------------------

def bare(name: str) -> str:
    return str(name).rsplit(".", 1)[-1].lower()


def _touched(field_changes: dict) -> dict:
    """``{bare_lower: original_key}`` for every key the remedy touches."""
    out: dict = {}
    for k in (field_changes or {}):
        if k:
            out.setdefault(bare(k), str(k))
    return out


def _staged_forms(staged_keys, bare_name: str) -> list:
    return [k for k in (staged_keys or ()) if bare(k) == bare_name]


def _names_field(error_fields, error_message, original: str, bare_name: str):
    """How the platform error attests the field: via the structured
    ``error_fields`` (preferred), else a case-insensitive mention in the
    error message. ``None`` when neither names it."""
    for f in (error_fields or ()):
        if bare(f) == bare_name:
            return "error_fields"
    msg = (error_message or "").lower()
    if msg and (bare_name in msg or original.lower() in msg):
        return "error_message"
    return None


# ---------------------------------------------------------------------------
# The pure classifier
# ---------------------------------------------------------------------------

def classify(inp: GateInputs) -> GateResult:
    """Total over inputs: every shape lands on exactly one verdict. SEMANTIC
    first (ratified), then DERIVED, else SPECULATIVE."""
    dest = inp.destination or None
    base = {"classifier_version": CLASSIFIER_VERSION,
            "s1_seq": inp.s1_seq, "s1_as_of": "current" if inp.s1_seq else None,
            "destination": dest}

    # ---- K: the deterministic kinds — no recipe mutation (ruling D1) ----
    if inp.proposal_kind == "rerun":
        return GateResult(DERIVED, {**base, "rule": "K-rerun",
                                    "no_recipe_mutation": True,
                                    "outcome": inp.outcome,
                                    "s6_verdict": inp.s6_verdict,
                                    "failure_category": inp.failure_category})
    if inp.proposal_kind == "regenerate_from_current_org":
        return GateResult(DERIVED, {**base, "rule": "K-regen",
                                    "no_recipe_mutation": True,
                                    "cause_kind": inp.cause_kind,
                                    "s6_verdict": inp.s6_verdict,
                                    "claim_version_seq": inp.claim_version_seq})
    if inp.proposal_kind != "recipe_edit":
        return GateResult(SEMANTIC, {**base, "reason": "unknown_proposal_kind",
                                     "proposal_kind": inp.proposal_kind})

    # ---- SEMANTIC — evaluated FIRST (ratified) --------------------------
    touched = _touched(inp.field_changes)
    if not touched:
        return GateResult(SEMANTIC, {**base, "reason": "empty_remedy"})
    if not inp.recipe_readable:
        return GateResult(SEMANTIC, {**base, "reason": "recipe_unreadable"})
    if not inp.claim_readable:
        return GateResult(SEMANTIC, {**base, "reason": "claim_unreadable"})
    if inp.claim_kind not in SUPPORTED_CLAIM_KINDS:
        return GateResult(SEMANTIC, {**base, "reason": "claim_kind_unsupported",
                                     "claim_kind": inp.claim_kind})
    bare_staged = sorted(
        b for b in touched
        if any("." not in k for k in _staged_forms(inp.staged_keys, b)))
    if bare_staged:
        return GateResult(SEMANTIC, {**base, "reason": "bare_staged_key",
                                     "fields": bare_staged})
    hit = sorted(set(touched) & set(inp.asserted_fields))
    if hit:
        return GateResult(SEMANTIC, {**base, "reason": "touches_asserted_field",
                                     "fields": hit,
                                     "asserted_fields": sorted(inp.asserted_fields)})

    # ---- DERIVED — R1 / R2 (one field, attested, recorded value) -------
    if len(touched) == 1:
        b, original = next(iter(touched.items()))
        value = inp.field_changes.get(original)
        fact = inp.s1_facts.get(b)
        attested = _names_field(inp.error_fields, inp.error_message, original, b)
        # R1: attested removal
        if value == _remove_sentinel() and attested and fact is not None:
            if not fact.exists:
                return GateResult(DERIVED, {
                    **base, "rule": "R1", "field": original,
                    "error_code": inp.error_code, "attested_by": attested,
                    "s1_fact": "absent", "sobject": inp.sobject})
            if fact.is_createable is False:
                return GateResult(DERIVED, {
                    **base, "rule": "R1", "field": original,
                    "error_code": inp.error_code, "attested_by": attested,
                    "s1_fact": "is_createable=false",
                    "s1_entity_id": fact.entity_id, "sobject": inp.sobject})
        # R2: recorded picklist value (default or sole active — ruling D4)
        if (value != _remove_sentinel() and inp.error_code == _PICKLIST_ERROR_CODE
                and attested and fact is not None and fact.exists
                and fact.picklist_active_values is not None):
            active = tuple(fact.picklist_active_values)
            sval = str(value)
            if sval in active:
                if fact.picklist_default is not None and sval == fact.picklist_default:
                    return GateResult(DERIVED, {
                        **base, "rule": "R2", "field": original,
                        "error_code": inp.error_code, "attested_by": attested,
                        "matched": "default", "s1_entity_id": fact.entity_id,
                        "active_count": len(active)})
                if len(active) == 1:
                    return GateResult(DERIVED, {
                        **base, "rule": "R2", "field": original,
                        "error_code": inp.error_code, "attested_by": attested,
                        "matched": "sole_active", "s1_entity_id": fact.entity_id,
                        "active_count": 1})
                return GateResult(SPECULATIVE, {
                    **base, "reason": "chosen_picklist_value", "field": original,
                    "error_code": inp.error_code, "active_count": len(active)})
            return GateResult(SPECULATIVE, {
                **base, "reason": "value_not_recorded_in_picklist",
                "field": original, "error_code": inp.error_code,
                "active_count": len(active)})

    # ---- SPECULATIVE — inference, or a chosen value --------------------
    return GateResult(SPECULATIVE, {
        **base, "reason": ("no_platform_error" if not inp.error_code
                           else "inference_or_chosen_value"),
        "error_code": inp.error_code, "cause_kind": inp.cause_kind,
        "fields": sorted(touched.values())})


# ---------------------------------------------------------------------------
# The impure reader — recorded facts only
# ---------------------------------------------------------------------------

def _subject_create(rr):
    """``(sobject, field_values)`` of a RecipeRead's SUBJECT create (the
    last positive CreateStep), or ``(None, None)``."""
    body = getattr(rr, "observation_realization", None)
    steps = list(getattr(body, "steps", None) or [])
    create = None
    for s in steps:
        if (getattr(s, "kind", None) == "create"
                and getattr(s, "expect_rejection", None) is None):
            create = s
    if create is None:
        return None, None
    sobject = getattr(create.target_object, "external_id", None)
    return sobject, dict(create.field_values or {})


def _assert_step_fields(rr) -> set:
    out: set = set()
    body = getattr(rr, "observation_realization", None)
    for s in (getattr(body, "steps", None) or []):
        if getattr(s, "kind", None) != "assert":
            continue
        ref = getattr(getattr(s, "predicate", None), "subject_ref", None) or ""
        if "." in ref:
            out.add(bare(ref))
    return out


def _walk_field_refs(node, out: set) -> None:
    """Every Field reference in a claim body: pinned refs
    (``entity_type == 'Field'`` with an ``external_id``) plus dict keys
    shaped ``Object.Field`` (the state-transition state dicts)."""
    if isinstance(node, dict):
        if node.get("entity_type") == "Field" and node.get("external_id"):
            out.add(bare(node["external_id"]))
        for k, v in node.items():
            if isinstance(k, str) and "." in k and k.count(".") == 1 \
                    and " " not in k and k[0].isalpha():
                out.add(bare(k))
            _walk_field_refs(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _walk_field_refs(v, out)


def asserted_fields_of(claim) -> frozenset:
    """The claim's asserted bare field names: semantic-condition subjects
    (the D-454 pins builder's asserted half, same normalisation) + every
    Field reference in the asserted-truth body."""
    out: set = set()
    sc = getattr(claim, "semantic_conditions", None)
    for c in (getattr(sc, "conditions", None) or ()):
        ext = getattr(getattr(c, "subject", None), "external_id", None)
        if ext:
            out.add(bare(ext))
    body = getattr(claim, "asserted_truth", None)
    data = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    _walk_field_refs(data, out)
    return frozenset(out)


def resolve_destination(conn, claim_test_id) -> Optional[dict]:
    """The requirement behind the claim (the regenerate path's read): a
    ``req-N`` key → ``/requirements/N``; a Jira key → the list filtered by
    it; a dangling key renders as itself with no record (the absence
    rule)."""
    key = conn.execute(text(
        "SELECT external_key FROM test_requirement_links "
        "WHERE test_id = CAST(:t AS uuid) AND link_kind = 'generated_from' "
        "ORDER BY linked_at DESC LIMIT 1"), {"t": str(claim_test_id)}).scalar()
    if not key:
        return {"key": None, "url": None, "note": "no requirement link"}
    if key.startswith("req-") and key[4:].isdigit():
        return {"key": key, "url": f"/requirements/{key[4:]}"}
    return {"key": key, "url": f"/requirements?q={key}"}


def _s1_facts(conn, environment_id, sobject: str, touched: dict) -> tuple:
    """``(facts, seq, org)`` for the touched fields on ``sobject`` at the
    org's CURRENT sequence. Empty facts when the environment has no org or
    the org has never synced (no attestation is then possible)."""
    from primeqa.semantic.query import SemanticOrgModel, VersionNotFoundError
    from primeqa.sync.credentials import get_connected_org_for_environment
    org = get_connected_org_for_environment(conn, environment_id)
    if not org:
        return {}, None, None
    model = SemanticOrgModel(conn, connected_org_id=org)
    try:
        seq = model.current_version_seq()
    except VersionNotFoundError:
        return {}, None, org
    facts: dict = {}
    for b, original in touched.items():
        fname = original.rsplit(".", 1)[-1]
        ents = model.get_entities("Field", seq,
                                  filters={"sf_api_name": f"{sobject}.{fname}"})
        if not ents:
            facts[b] = FieldFact(exists=False)
            continue
        ent = ents[0]
        det = model.get_entity_details(ent.id, seq) or {}
        active, default = None, None
        pvs = det.get("picklist_value_set_entity_id")
        if pvs:
            vals = model.get_picklist_values(pvs, seq)
            active = tuple(str(v["value_api_name"]) for v in vals
                           if v.get("is_active"))
            defaults = [str(v["value_api_name"]) for v in vals
                        if v.get("is_active") and v.get("is_default")]
            default = defaults[0] if len(defaults) == 1 else None
        facts[b] = FieldFact(
            exists=True,
            is_createable=(None if det.get("is_createable") is None
                           else bool(det.get("is_createable"))),
            picklist_active_values=active, picklist_default=default,
            entity_id=str(ent.id))
    return facts, seq, org


def gather_inputs(conn, tenant_id: int, row, field_changes: Optional[dict],
                  *, s1_reader=None) -> GateInputs:
    """Read every recorded fact the classifier needs for ONE proposal-shaped
    ``row`` (``run_id``, ``claim_test_id``, ``environment_id``,
    ``proposal_kind``, ``cause_kind``, ``verdict`` = the S6 verdict, and
    optionally ``outcome``). ``s1_reader`` is injectable for tests."""
    from sqlalchemy.orm import Session

    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    kind = row["proposal_kind"]
    inp = GateInputs(proposal_kind=kind, field_changes=dict(field_changes or {}),
                     cause_kind=row.get("cause_kind"),
                     s6_verdict=row.get("verdict"), outcome=row.get("outcome"))
    run = conn.execute(text(
        "SELECT recipe_id, recipe_version_seq, claim_version_seq, outcome, "
        "       failure_category, evidence "
        "FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)"),
        {"r": str(row["run_id"])}).mappings().first()
    if run is not None:
        inp.outcome = inp.outcome or run["outcome"]
        inp.failure_category = run["failure_category"]
        inp.claim_version_seq = run["claim_version_seq"]
        inp.recipe_version_seq = run["recipe_version_seq"]
        for s in ((run["evidence"] or {}).get("steps") or []):
            if isinstance(s, dict) and s.get("kind") == "create" \
                    and not s.get("success", True):
                inp.error_code = s.get("error_code")
                inp.error_message = s.get("message")
                inp.error_fields = tuple(s.get("error_fields") or [])
                break
    inp.destination = resolve_destination(conn, row["claim_test_id"])
    if kind != "recipe_edit":
        return inp

    coord = SemanticTransactionCoordinator()
    session = Session(bind=conn)
    try:
        rr = None
        if run is not None and run["recipe_id"] is not None:
            if run["recipe_version_seq"] is not None:
                rr = coord.get_recipe_version(session, run["recipe_id"],
                                              int(run["recipe_version_seq"]))
            if rr is None:
                rr = coord.get_recipe_latest(session, run["recipe_id"])
        if rr is not None:
            sobject, fv = _subject_create(rr)
            if sobject is not None:
                inp.recipe_readable = True
                inp.sobject = sobject
                inp.staged_keys = tuple(fv.keys())
        claim = coord.get_latest_claim(session, UUID(str(row["claim_test_id"])))
        if claim is not None:
            inp.claim_readable = True
            inp.claim_kind = claim.claim_kind
            fields = set(asserted_fields_of(claim))
            if rr is not None:
                fields |= _assert_step_fields(rr)
            inp.asserted_fields = frozenset(fields)
    finally:
        session.close()

    touched = _touched(inp.field_changes)
    if inp.recipe_readable and touched:
        reader = s1_reader or _s1_facts
        try:
            facts, seq, org = reader(conn, row["environment_id"], inp.sobject, touched)
        except Exception as exc:  # noqa: BLE001 — no attestation, never a crash
            log.warning("repair gate: S1 read failed for tenant %s: %s",
                        tenant_id, exc)
            facts, seq, org = {}, None, None
        inp.s1_facts, inp.s1_seq, inp.s1_org = facts, seq, org
    return inp


def classify_row(conn, tenant_id: int, row, field_changes: Optional[dict],
                 *, s1_reader=None) -> GateResult:
    return classify(gather_inputs(conn, tenant_id, row, field_changes,
                                  s1_reader=s1_reader))


# ---------------------------------------------------------------------------
# Retro-classification (idempotent) + the D3 revert
# ---------------------------------------------------------------------------

def retro_classify(tenant_id: int, *, force: bool = False,
                   s1_reader=None) -> dict:
    """Classify every proposal whose ``gate_verdict`` is NULL or whose
    ``classifier_version`` differs (``force`` re-classifies all). A second
    run writes nothing and reports the same counts. Returns
    ``{scanned, written, counts: {(status, kind, verdict): n}}``."""
    from primeqa.semantic.connection import get_tenant_connection
    scanned = written = 0
    with get_tenant_connection(tenant_id) as conn:
        rows = conn.execute(text(
            "SELECT id, run_id, claim_test_id, environment_id, verdict, "
            "       cause_kind, proposal_kind, status, proposed_payload, "
            "       gate_verdict, classifier_version "
            "FROM repair_proposals ORDER BY id")).mappings().all()
        for r in rows:
            scanned += 1
            if (not force and r["gate_verdict"] is not None
                    and r["classifier_version"] == CLASSIFIER_VERSION):
                continue
            fc = ((r["proposed_payload"] or {}).get("field_changes")
                  if r["proposal_kind"] == "recipe_edit" else {})
            res = classify_row(conn, tenant_id, dict(r), fc, s1_reader=s1_reader)
            conn.execute(text(
                "UPDATE repair_proposals SET gate_verdict = :v, "
                "grounding_source = CAST(:g AS jsonb), classified_at = :at, "
                "classifier_version = :cv WHERE id = :pid"),
                {"v": res.verdict, "g": json.dumps(res.grounding, default=str),
                 "at": datetime.now(timezone.utc), "cv": CLASSIFIER_VERSION,
                 "pid": r["id"]})
            written += 1
    return {"scanned": scanned, "written": written,
            "counts": report_counts(tenant_id)}


def report_counts(tenant_id: int) -> dict:
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        rows = conn.execute(text(
            "SELECT status, proposal_kind, COALESCE(gate_verdict, 'NULL') AS v, "
            "       COUNT(*) AS n FROM repair_proposals "
            "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3")).all()
    return {f"{r[0]}|{r[1]}|{r[2]}": int(r[3]) for r in rows}


def revert_refused_auto_applies(tenant_id: int, *, actor_user_id: Optional[int] = None) -> list:
    """Ruling D3: every AUTO-applied recipe edit whose retro verdict is not
    DERIVED is reverted — a NEW recipe version whose content is the
    pre-edit version's, provenance ``gate_retro_revert`` naming the
    proposal and the predicted verdict — and a re-verify run is enqueued.
    Idempotent: a reverted row (``reverted_at``) is never reverted twice.
    A DERIVED row stays. Returns one record per candidate."""
    from sqlalchemy.orm import Session

    from primeqa.execution_engine.intake import enqueue_s4_execution
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation.coordinator import (
        SemanticTransactionCoordinator,
    )
    out: list = []
    with get_tenant_connection(tenant_id) as conn:
        rows = conn.execute(text(
            "SELECT id, claim_test_id, environment_id, gate_verdict, payload "
            "FROM repair_proposals WHERE status = 'applied' AND auto_applied "
            "  AND proposal_kind = 'recipe_edit' AND reverted_at IS NULL "
            "ORDER BY id")).mappings().all()
    for r in rows:
        rec = {"proposal_id": r["id"], "gate_verdict": r["gate_verdict"]}
        if r["gate_verdict"] is None:
            rec["action"] = "skipped_unclassified"
            out.append(rec)
            continue
        if r["gate_verdict"] == DERIVED:
            rec["action"] = "kept_derived"
            out.append(rec)
            continue
        payload = r["payload"] or {}
        recipe_id, new_seq = payload.get("recipe_id"), payload.get("new_version_seq")
        if not recipe_id or not new_seq:
            rec.update(action="error", error="apply payload lacks recipe_id/new_version_seq")
            out.append(rec)
            continue
        try:
            with get_tenant_connection(tenant_id) as conn:
                session = Session(bind=conn)
                try:
                    coord = SemanticTransactionCoordinator()
                    pre = coord.get_recipe_version(session, UUID(str(recipe_id)),
                                                   int(new_seq) - 1)
                    cur = coord.get_recipe_latest(session, UUID(str(recipe_id)))
                    if pre is None or cur is None:
                        raise RuntimeError(
                            f"pre-edit version {int(new_seq) - 1} of recipe "
                            f"{recipe_id} not found")
                    res = coord.write_recipe(
                        session, actor="s8", recipe_id=UUID(str(recipe_id)),
                        claim_test_id=cur.claim_test_id,
                        trigger_kind=cur.trigger_kind, recipe_kind=cur.recipe_kind,
                        causal_initiation=cur.causal_initiation,
                        observation_realization=pre.observation_realization,
                        execution_environment=cur.execution_environment,
                        claim_version_seq=cur.claim_version_seq,
                        priority=cur.priority,
                        event_context={
                            "provenance": "gate_retro_revert",
                            "proposal_id": r["id"],
                            "predicted_verdict": r["gate_verdict"],
                            "reverts_version_seq": int(new_seq),
                            "restores_version_seq": int(new_seq) - 1,
                        })
                    conn.execute(text(
                        "UPDATE repair_proposals SET revert_recipe_version_seq = :v, "
                        "reverted_at = :at WHERE id = :pid"),
                        {"v": res.version_seq, "at": datetime.now(timezone.utc),
                         "pid": r["id"]})
                    conn.execute(text(
                        "INSERT INTO public.activity_log "
                        "(tenant_id, user_id, action, entity_type, entity_id, details) "
                        "VALUES (:t, :u, 'repair.gate_retro_revert', "
                        "'repair_proposal', :pid, CAST(:d AS JSONB))"),
                        {"t": tenant_id, "u": actor_user_id, "pid": r["id"],
                         "d": json.dumps({
                             "recipe_id": str(recipe_id),
                             "reverts_version_seq": int(new_seq),
                             "restores_version_seq": int(new_seq) - 1,
                             "new_version_seq": res.version_seq,
                             "predicted_verdict": r["gate_verdict"]})})
                    session.commit()
                finally:
                    session.close()
            job = enqueue_s4_execution(
                tenant_id=tenant_id, test_id=r["claim_test_id"],
                environment_id=r["environment_id"])
            rec.update(action="reverted", recipe_id=str(recipe_id),
                       new_version_seq=res.version_seq,
                       restores_version_seq=int(new_seq) - 1,
                       s4_job_id=job.id)
        except Exception as exc:  # noqa: BLE001 — recorded per row, never silent
            log.warning("gate retro revert failed for tenant %s proposal %s: %s",
                        tenant_id, r["id"], exc)
            rec.update(action="error", error=str(exc)[:300])
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# CLI — non-secret argv only
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m primeqa.intelligence.repair_gate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("retro", help="classify every unclassified proposal")
    r.add_argument("--tenant-id", type=int, required=True)
    r.add_argument("--force", action="store_true")
    v = sub.add_parser("revert", help="D3: revert refused auto-applied edits")
    v.add_argument("--tenant-id", type=int, required=True)
    v.add_argument("--user-id", type=int, default=None)
    c = sub.add_parser("report", help="counts by status/kind/verdict")
    c.add_argument("--tenant-id", type=int, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "retro":
        print(json.dumps(retro_classify(args.tenant_id, force=args.force),
                         indent=2, default=str))
    elif args.cmd == "revert":
        print(json.dumps(revert_refused_auto_applies(
            args.tenant_id, actor_user_id=args.user_id), indent=2, default=str))
    else:
        print(json.dumps(report_counts(args.tenant_id), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
