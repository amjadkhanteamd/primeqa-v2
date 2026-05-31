"""Integration: the production S6 `S1ValidationRuleReader` against real S1
(D-111.1 slice 2b).

Seeds an isolated S1 graph mirroring a real validation rule (an Object + a VR
carrying `formula_text` + `error_message` in `attributes` + an `APPLIES_TO`
edge + a `validation_rule_details` row with `is_active`), reads it back through
S1's query interface (`SemanticOrgModel` → the reader), and confirms all three
`VrMeta` fields come back — including `is_active`, the field the new
`get_entity_details` extension surfaces. Then wires it into `attribute_run`
end-to-end (the same path slice 2a stub-tested).

Uses the governance test DB (the conftest's `db_setup`); seeds with unique
names + cleans up so it doesn't pollute the session-scoped S1 seed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection
from primeqa.semantic.query import SemanticOrgModel
from primeqa.interpretation import S1ValidationRuleReader, attribute_run, interpret_run
from primeqa.execution_engine.evidence import (
    CleanupRecord,
    CreateAttemptEvidence,
    RunEvidence,
)

from .conftest import TEST_TENANT_ID

_T = datetime(2026, 5, 27, tzinfo=timezone.utc)
_FORMULA = "ISBLANK(Reason__c)"
_MSG = "A reason is required."


def _seed():
    """Seed Object + VR(formula+message) + APPLIES_TO + validation_rule_details
    (the VR INACTIVE), with a fresh unique tag per call. Returns
    ``(version_seq, object_id, vr_id, object_name, vr_name)``."""
    import json
    tag = uuid4().hex[:8]
    obj_name, vr_name = f"S6Obj_{tag}__c", f"S6Obj_{tag}.RequireReason"
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        seq = conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type) "
            "VALUES (:n,'manual_checkpoint') RETURNING version_seq"),
            {"n": f"s6_v_{tag}"}).scalar()
        obj_id = conn.execute(text(
            "INSERT INTO entities (entity_type, sf_api_name, display_name, "
            "attributes, valid_from_seq, valid_to_seq, last_synced_at) "
            "VALUES ('Object',:api,:api,'{}'::jsonb,:vf,NULL,NOW()) RETURNING id"),
            {"api": obj_name, "vf": seq}).scalar()
        vr_id = conn.execute(text(
            "INSERT INTO entities (entity_type, sf_api_name, display_name, "
            "attributes, valid_from_seq, valid_to_seq, last_synced_at) "
            "VALUES ('ValidationRule',:api,:api,CAST(:attrs AS jsonb),:vf,NULL,NOW()) "
            "RETURNING id"),
            {"api": vr_name, "vf": seq,
             "attrs": json.dumps({"formula_text": _FORMULA, "error_message": _MSG})}).scalar()
        conn.execute(text(
            "INSERT INTO edges (source_entity_id, target_entity_id, edge_type, "
            "edge_category, properties, valid_from_seq, valid_to_seq) "
            "VALUES (CAST(:s AS uuid),CAST(:t AS uuid),'APPLIES_TO','BEHAVIOR','{}'::jsonb,:vf,NULL)"),
            {"s": str(vr_id), "t": str(obj_id), "vf": seq})
        conn.execute(text(
            "INSERT INTO validation_rule_details (entity_id, object_entity_id, is_active) "
            "VALUES (CAST(:vr AS uuid), CAST(:obj AS uuid), :active)"),
            {"vr": str(vr_id), "obj": str(obj_id), "active": False})  # INACTIVE
    return int(seq), obj_id, vr_id, obj_name, vr_name


def _cleanup(obj_id, vr_id):
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text("DELETE FROM validation_rule_details WHERE entity_id = CAST(:v AS uuid)"),
                     {"v": str(vr_id)})
        conn.execute(text("DELETE FROM edges WHERE source_entity_id = CAST(:v AS uuid)"),
                     {"v": str(vr_id)})
        conn.execute(text("DELETE FROM entities WHERE id IN (CAST(:o AS uuid), CAST(:v AS uuid))"),
                     {"o": str(obj_id), "v": str(vr_id)})


def _not_enforced_run(sobject):
    """A behavioral-negative run where the create SUCCEEDED (prohibition not
    enforced) — the verdict attribute_run deepens."""
    create = CreateAttemptEvidence(
        step_id="create-violating", ordinal=0, sobject=sobject,
        field_values={"Reason__c": None}, http_status=201, success=True,
        error_code=None, message=None, rejection_body=(), matched=False,
        cleanup=CleanupRecord(attempted=True, succeeded=True, record_id="001Z"),
        started_at=_T, finished_at=_T, duration_ms=1)
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, environment_id=0, api_choice="rest", outcome="failed",
        started_at=_T, finished_at=_T, steps=(create,))


def test_production_reader_and_attribute_run_end_to_end(db_setup):
    seq, obj_id, vr_id, obj_name, vr_name = _seed()
    try:
        with get_tenant_connection(TEST_TENANT_ID) as conn:
            model = SemanticOrgModel(conn)
            reader = S1ValidationRuleReader(model, at_seq=seq)

            # 1) the reader returns the full VrMeta — incl. is_active via the
            #    new get_entity_details extension.
            vrs = reader.vrs_for_object(obj_name)
            assert len(vrs) == 1
            vr = vrs[0]
            assert vr.name == vr_name
            assert vr.is_active is False                 # ← from validation_rule_details
            assert vr.formula_text == _FORMULA           # ← from attributes
            assert vr.error_message == _MSG              # ← from attributes

            # 2) wire it into attribute_run end-to-end: a non-enforcing run whose
            #    grounding VR is INACTIVE → cause vr_inactive (cites the VR).
            evidence = _not_enforced_run(obj_name)
            interp = attribute_run(interpret_run(evidence), evidence, s1=reader)
            assert interp.verdict == "prohibition_not_enforced"
            assert interp.cause.cause_kind == "vr_inactive"
            assert interp.cause.vr_name == vr_name
            assert interp.outcome == "failed"            # never re-judged
    finally:
        _cleanup(obj_id, vr_id)


def test_get_entity_details_surfaces_is_active(db_setup):
    # The S1 extension directly: get_entity_details returns the detail row.
    seq, obj_id, vr_id, _obj_name, _vr_name = _seed()
    try:
        with get_tenant_connection(TEST_TENANT_ID) as conn:
            model = SemanticOrgModel(conn)
            details = model.get_entity_details(vr_id, at_seq=seq)
            assert details is not None
            assert details["is_active"] is False
            # an Object has a detail table but no row here → None (graceful).
            assert model.get_entity_details(obj_id, at_seq=seq) is None
    finally:
        _cleanup(obj_id, vr_id)
