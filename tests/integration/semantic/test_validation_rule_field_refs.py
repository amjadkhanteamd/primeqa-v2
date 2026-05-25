"""Integration: ValidationRule REFERENCES extraction (D-107, slice 2, approach B).

Seeds an Object + Fields + a ValidationRule (with a formula) on the local-PG
semantic test DB, runs the bespoke field_refs writer, and asserts the
validation_rule_field_refs rows + references_status, then runs the existing
derivation (edges_for_entity) through to the materialized REFERENCES edges.

Covers: same-object read; multi-reference_type per field; ischanged; cross-object
dotted -> no edge + status partial; ISNEW -> no edge + status complete;
NotParsed -> no edges + status unparsed.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.semantic.derivation import edges_for_entity
from primeqa.sync.validation_rule_refs import write_field_refs


def _seed_vr(seed, conn, formula):
    """Seed Opportunity + Amount/Status fields + a VR (formula in attributes) +
    its detail row. Returns (vr_id, fields, resolve)."""
    v = seed.version()
    obj = seed.entity("Object", "Opportunity", v)
    amount = seed.entity("Field", "Opportunity.Amount", v)
    status = seed.entity("Field", "Opportunity.Status", v)
    vr = seed.entity("ValidationRule", f"Opportunity.R_{uuid4().hex[:6]}", v,
                     attributes={"formula_text": formula})
    conn.execute(text(
        "INSERT INTO validation_rule_details (entity_id, object_entity_id, is_active) "
        "VALUES (CAST(:vr AS uuid), CAST(:obj AS uuid), TRUE)"
    ), {"vr": str(vr), "obj": str(obj)})

    def resolve(entity_type, external_id):
        r = conn.execute(text(
            "SELECT id FROM entities WHERE entity_type = :et "
            "AND sf_api_name = :api AND valid_to_seq IS NULL"
        ), {"et": entity_type, "api": external_id}).first()
        return str(r[0]) if r else None

    return vr, {"Amount": amount, "Status": status, "Object": obj}, resolve


def _junction(conn, vr):
    return conn.execute(text(
        "SELECT field_entity_id, reference_type, is_priorvalue, is_ischanged "
        "FROM validation_rule_field_refs WHERE validation_rule_entity_id = CAST(:vr AS uuid)"
    ), {"vr": str(vr)}).fetchall()


def _status(conn, vr):
    return conn.execute(text(
        "SELECT references_status FROM validation_rule_details WHERE entity_id = CAST(:vr AS uuid)"
    ), {"vr": str(vr)}).scalar()


def _reference_edges(vr, conn):
    return [e for e in edges_for_entity(vr, conn) if e["edge_type"] == "REFERENCES"]


def test_same_object_read_to_materialized_edge(seed, conn):
    vr, fields, resolve = _seed_vr(seed, conn, "ISBLANK(Amount)")
    assert write_field_refs(conn, vr_entity_id=vr, formula_text="ISBLANK(Amount)",
                            parent_object_api="Opportunity", resolve=resolve) == "complete"
    rows = _junction(conn, vr)
    assert len(rows) == 1 and str(rows[0][0]) == str(fields["Amount"]) and rows[0][1] == "read"
    edges = _reference_edges(vr, conn)
    assert len(edges) == 1
    assert str(edges[0]["target_entity_id"]) == str(fields["Amount"])
    assert edges[0]["properties"]["reference_type"] == "read"


def test_multi_reference_type_two_edges(seed, conn):
    f = "Amount <> PRIORVALUE(Amount)"
    vr, fields, resolve = _seed_vr(seed, conn, f)
    assert write_field_refs(conn, vr_entity_id=vr, formula_text=f,
                            parent_object_api="Opportunity", resolve=resolve) == "complete"
    edges = _reference_edges(vr, conn)
    types = sorted(e["properties"]["reference_type"] for e in edges)
    assert types == ["priorvalue", "read"]                      # same field, two edges
    assert all(str(e["target_entity_id"]) == str(fields["Amount"]) for e in edges)
    # the priorvalue row carries its flag
    pv = [r for r in _junction(conn, vr) if r[1] == "priorvalue"][0]
    assert pv[2] is True and pv[3] is False


def test_ischanged_reference_type(seed, conn):
    f = "ISCHANGED(Status)"
    vr, fields, resolve = _seed_vr(seed, conn, f)
    assert write_field_refs(conn, vr_entity_id=vr, formula_text=f,
                            parent_object_api="Opportunity", resolve=resolve) == "complete"
    edges = _reference_edges(vr, conn)
    assert len(edges) == 1 and edges[0]["properties"]["reference_type"] == "ischanged"
    assert str(edges[0]["target_entity_id"]) == str(fields["Status"])


def test_cross_object_dotted_no_edge_status_partial(seed, conn):
    f = 'Account.Industry = "Tech"'
    vr, _, resolve = _seed_vr(seed, conn, f)
    assert write_field_refs(conn, vr_entity_id=vr, formula_text=f,
                            parent_object_api="Opportunity", resolve=resolve) == "partial"
    assert _junction(conn, vr) == []
    assert _reference_edges(vr, conn) == []
    assert _status(conn, vr) == "partial"


def test_isnew_no_edge_status_complete(seed, conn):
    f = "ISNEW()"
    vr, _, resolve = _seed_vr(seed, conn, f)
    assert write_field_refs(conn, vr_entity_id=vr, formula_text=f,
                            parent_object_api="Opportunity", resolve=resolve) == "complete"
    assert _junction(conn, vr) == [] and _reference_edges(vr, conn) == []
    assert _status(conn, vr) == "complete"


def test_unparsed_no_edge_status_unparsed(seed, conn):
    f = 'REGEX(Name, "[0-9]+")'
    vr, _, resolve = _seed_vr(seed, conn, f)
    assert write_field_refs(conn, vr_entity_id=vr, formula_text=f,
                            parent_object_api="Opportunity", resolve=resolve) == "unparsed"
    assert _junction(conn, vr) == [] and _reference_edges(vr, conn) == []
    assert _status(conn, vr) == "unparsed"


def test_default_status_is_pending_before_extraction(seed, conn):
    # #1 honest default: a freshly-inserted detail row, before the writer runs,
    # is 'pending' (not-yet-evaluated) — never masquerading as 'unparsed'.
    vr, _, _ = _seed_vr(seed, conn, "ISBLANK(Amount)")
    assert _status(conn, vr) == "pending"


def test_resync_formula_change_no_stale_current_edges(seed, conn):
    # #2 version-scoped: a formula change supersedes the VR (new entity_id), so
    # the new version's id gets fresh field_refs while the old id keeps its
    # historical refs. The CURRENT (active) VR reflects the new formula — no
    # stale current REFERENCES edges.
    s1 = seed.version()
    obj = seed.entity("Object", "Opportunity", s1)
    amount = seed.entity("Field", "Opportunity.Amount", s1)
    status = seed.entity("Field", "Opportunity.Status", s1)

    def resolve(entity_type, external_id):
        r = conn.execute(text(
            "SELECT id FROM entities WHERE entity_type = :et "
            "AND sf_api_name = :api AND valid_to_seq IS NULL"
        ), {"et": entity_type, "api": external_id}).first()
        return str(r[0]) if r else None

    def _add_detail(vr_id):
        conn.execute(text(
            "INSERT INTO validation_rule_details (entity_id, object_entity_id, is_active) "
            "VALUES (CAST(:vr AS uuid), CAST(:obj AS uuid), TRUE)"
        ), {"vr": str(vr_id), "obj": str(obj)})

    # v1: ISBLANK(Amount)
    vr1 = seed.entity("ValidationRule", "Opportunity.Rule", s1)
    _add_detail(vr1)
    write_field_refs(conn, vr_entity_id=vr1, formula_text="ISBLANK(Amount)",
                     parent_object_api="Opportunity", resolve=resolve)

    # formula changes -> supersede v1 at s2, create v2 (same api name, new id, active)
    s2 = seed.version()
    conn.execute(text("UPDATE entities SET valid_to_seq = :s2 WHERE id = CAST(:vr AS uuid)"),
                 {"s2": s2, "vr": str(vr1)})
    vr2 = seed.entity("ValidationRule", "Opportunity.Rule", s2)
    _add_detail(vr2)
    write_field_refs(conn, vr_entity_id=vr2, formula_text="ISBLANK(Status)",
                     parent_object_api="Opportunity", resolve=resolve)

    # the CURRENT (active) VR is v2 -> REFERENCES Status only, no stale Amount
    cur = conn.execute(text(
        "SELECT id FROM entities WHERE entity_type='ValidationRule' "
        "AND sf_api_name='Opportunity.Rule' AND valid_to_seq IS NULL"
    )).scalar()
    assert str(cur) == str(vr2)
    cur_targets = {str(e["target_entity_id"]) for e in _reference_edges(cur, conn)}
    assert cur_targets == {str(status)}                 # B only; A is gone from current

    # the superseded v1 keeps its historical refs (Amount) — not leaked into current
    old_targets = {str(e["target_entity_id"]) for e in _reference_edges(vr1, conn)}
    assert old_targets == {str(amount)}
