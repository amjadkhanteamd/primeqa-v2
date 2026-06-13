"""Integration: the production S6 reader's positive-vertical methods against
real S1 (D-229) — `flows_for_object` + `field_meta`.

Seeds an isolated S1 graph: an Object + a Flow (`TRIGGERS_ON` + a
`flow_details` row with `is_active=False`) + a Field (`BELONGS_TO` + a
`field_details` row with `is_createable=False`), reads each back through S1's
query interface, and wires them into `attribute_run` end-to-end for the
positive-vertical failures (automation_not_triggered → automation_inactive,
value_not_persisted → field_not_createable).

Uses the governance test DB; unique names + cleanup so it doesn't pollute the
session-scoped S1 seed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from primeqa.semantic.connection import get_tenant_connection
from primeqa.semantic.query import SemanticOrgModel
from primeqa.interpretation import S1ValidationRuleReader, attribute_run, interpret_run
from primeqa.execution_engine.evidence import (
    AssertEvidence,
    CleanupRecord,
    CreateAttemptEvidence,
    DataReadEvidence,
    RunEvidence,
)

from .conftest import TEST_TENANT_ID

_T = datetime(2026, 5, 27, tzinfo=timezone.utc)


def _seed():
    """Object + Flow(TRIGGERS_ON, inactive) + Field(BELONGS_TO, not createable).
    Returns (seq, obj_id, flow_id, field_id, obj_name, field_bare)."""
    tag = uuid4().hex[:8]
    obj_name = f"S6Pos_{tag}__c"
    flow_name = f"S6Flow_{tag}"
    field_bare = "Amount__c"
    field_api = f"{obj_name}.{field_bare}"
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        seq = conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type) "
            "VALUES (:n,'manual_checkpoint') RETURNING version_seq"),
            {"n": f"s6pos_v_{tag}"}).scalar()

        def _ent(etype, api):
            return conn.execute(text(
                "INSERT INTO entities (entity_type, sf_api_name, display_name, "
                "attributes, valid_from_seq, valid_to_seq, last_synced_at) "
                "VALUES (:et,:api,:api,'{}'::jsonb,:vf,NULL,NOW()) RETURNING id"),
                {"et": etype, "api": api, "vf": seq}).scalar()

        obj_id = _ent("Object", obj_name)
        flow_id = _ent("Flow", flow_name)
        field_id = _ent("Field", field_api)

        def _edge(src, tgt, etype, cat):
            conn.execute(text(
                "INSERT INTO edges (source_entity_id, target_entity_id, edge_type, "
                "edge_category, properties, valid_from_seq, valid_to_seq) VALUES "
                "(CAST(:s AS uuid),CAST(:t AS uuid),:e,:c,'{}'::jsonb,:vf,NULL)"),
                {"s": str(src), "t": str(tgt), "e": etype, "c": cat, "vf": seq})

        _edge(flow_id, obj_id, "TRIGGERS_ON", "BEHAVIOR")
        _edge(field_id, obj_id, "BELONGS_TO", "STRUCTURAL")

        conn.execute(text(
            "INSERT INTO flow_details (entity_id, flow_type, is_active) "
            "VALUES (CAST(:e AS uuid), 'AutoLaunchedFlow', :a)"),
            {"e": str(flow_id), "a": False})          # INACTIVE
        conn.execute(text(
            "INSERT INTO field_details (entity_id, object_entity_id, field_type, "
            "is_createable) VALUES (CAST(:e AS uuid), CAST(:o AS uuid), 'currency', :c)"),
            {"e": str(field_id), "o": str(obj_id), "c": False})   # NOT createable
    return int(seq), obj_id, flow_id, field_id, obj_name, field_bare


def _cleanup(obj_id, flow_id, field_id):
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text("DELETE FROM flow_details WHERE entity_id = CAST(:e AS uuid)"),
                     {"e": str(flow_id)})
        conn.execute(text("DELETE FROM field_details WHERE entity_id = CAST(:e AS uuid)"),
                     {"e": str(field_id)})
        for eid in (flow_id, field_id):
            conn.execute(text("DELETE FROM edges WHERE source_entity_id = CAST(:e AS uuid)"),
                         {"e": str(eid)})
        conn.execute(text(
            "DELETE FROM entities WHERE id IN "
            "(CAST(:o AS uuid), CAST(:f AS uuid), CAST(:d AS uuid))"),
            {"o": str(obj_id), "f": str(flow_id), "d": str(field_id)})


def _positive_run(sobject, *, captured, claim_kind):
    create = CreateAttemptEvidence(
        step_id="create", ordinal=0, sobject=sobject, field_values={"x": 1},
        http_status=201, success=True, error_code=None, message=None,
        rejection_body=(), matched=None, cleanup=CleanupRecord(attempted=False),
        started_at=_T, finished_at=_T, duration_ms=1)
    read = DataReadEvidence(
        step_id="read", ordinal=1, soql="SELECT x", sobject=sobject,
        fields_captured=tuple(captured), row_count=1, rows=({},),
        started_at=_T, finished_at=_T, duration_ms=1)
    assertion = AssertEvidence(
        step_id="assert", ordinal=2, predicate="equals", subject_ref="read",
        evaluated_row_count=1, held=False, started_at=_T, finished_at=_T, duration_ms=0)
    ev = RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1, claim_test_id=uuid4(),
        claim_version_seq=None, environment_id=0, api_choice="rest", outcome="failed",
        started_at=_T, finished_at=_T, steps=(create, read, assertion))
    return ev, claim_kind


def test_flows_for_object_and_automation_inactive(db_setup):
    seq, obj_id, flow_id, field_id, obj_name, _ = _seed()
    try:
        with get_tenant_connection(TEST_TENANT_ID) as conn:
            reader = S1ValidationRuleReader(SemanticOrgModel(conn), at_seq=seq)
            flows = reader.flows_for_object(obj_name)
            assert len(flows) == 1
            assert flows[0].is_active is False        # ← from flow_details

            ev, kind = _positive_run(obj_name, captured=("Status__c",),
                                     claim_kind="automation-effect-claim")
            interp = attribute_run(
                interpret_run(ev, claim_kind=kind), ev, s1=reader)
            assert interp.verdict == "automation_not_triggered"
            assert interp.cause.cause_kind == "automation_inactive"
            assert interp.outcome == "failed"          # never re-judged
    finally:
        _cleanup(obj_id, flow_id, field_id)


def test_field_meta_and_field_not_createable(db_setup):
    seq, obj_id, flow_id, field_id, obj_name, field_bare = _seed()
    try:
        with get_tenant_connection(TEST_TENANT_ID) as conn:
            reader = S1ValidationRuleReader(SemanticOrgModel(conn), at_seq=seq)
            # bare-name lookup resolves the object-qualified S1 field
            meta = reader.field_meta(obj_name, field_bare)
            assert meta is not None and meta.is_createable is False

            ev, kind = _positive_run(obj_name, captured=(field_bare,),
                                     claim_kind="value-claim")
            interp = attribute_run(
                interpret_run(ev, claim_kind=kind), ev, s1=reader)
            assert interp.verdict == "value_not_persisted"
            assert interp.cause.cause_kind == "field_not_createable"
            assert field_bare in interp.cause.detail
    finally:
        _cleanup(obj_id, flow_id, field_id)
