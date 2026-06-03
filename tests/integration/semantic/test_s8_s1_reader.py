"""Integration: the production S8 tri-port reader against real S1 (D-143).

Seeds an isolated S1 graph (an Object + an active VR with `formula_text` +
`APPLIES_TO` + `validation_rule_details`; a picklist Field → `field_details` →
a PicklistValueSet with one active + one inactive value; a non-picklist Field),
reads it back through `SemanticOrgModel` → `S8S1Reader`, and confirms all three
ports — `resolves` / `vrs_for_object` / `active_values`. Reuses the semantic
package's transactional `conn` + `seed` fixtures (every write rolls back).
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.evolution.s1_reader import S8S1Reader
from primeqa.semantic.query import SemanticOrgModel

_FORMULA = "ISBLANK(Reason__c)"


def _pv(seed, conn, pvs_id, api, vfrom, *, is_active, order=0):
    eid = seed.entity("PicklistValue", f"PV.{api}", vfrom)
    conn.execute(text(
        "INSERT INTO picklist_value_details "
        "(entity_id, picklist_value_set_entity_id, value_label, value_api_name, "
        " is_active, is_default, sort_order) "
        "VALUES (CAST(:e AS uuid), CAST(:pvs AS uuid), :l, :a, :act, FALSE, :o)"),
        {"e": str(eid), "pvs": str(pvs_id), "l": api, "a": api,
         "act": is_active, "o": order})


def _field_details(conn, field_id, object_id, *, field_type, pvs_id=None):
    conn.execute(text(
        "INSERT INTO field_details "
        "(entity_id, object_entity_id, field_type, picklist_value_set_entity_id) "
        "VALUES (CAST(:f AS uuid), CAST(:o AS uuid), :ft, "
        "        CAST(:pvs AS uuid))"),
        {"f": str(field_id), "o": str(object_id), "ft": field_type,
         "pvs": (str(pvs_id) if pvs_id else None)})


def _seed_org(seed, conn):
    """An Account Object + an active VR; a picklist Field Industry (Technology
    active, Mining inactive) + a non-picklist Field Amount. Returns the seq."""
    v1 = seed.version()
    obj = seed.entity("Object", "Account", v1)
    vr = seed.entity("ValidationRule", "Account.RequireReason", v1,
                     attributes={"formula_text": _FORMULA, "error_message": "x"})
    seed.edge(vr, obj, "APPLIES_TO", "BEHAVIOR", v1)
    conn.execute(text(
        "INSERT INTO validation_rule_details (entity_id, object_entity_id, is_active) "
        "VALUES (CAST(:vr AS uuid), CAST(:o AS uuid), TRUE)"),
        {"vr": str(vr), "o": str(obj)})

    pvs = seed.entity("PicklistValueSet", "PVS.Industry", v1)
    industry = seed.entity("Field", "Account.Industry", v1)
    _field_details(conn, industry, obj, field_type="picklist", pvs_id=pvs)
    _pv(seed, conn, pvs, "Technology", v1, is_active=True, order=0)
    _pv(seed, conn, pvs, "Mining", v1, is_active=False, order=1)

    amount = seed.entity("Field", "Account.Amount", v1)
    _field_details(conn, amount, obj, field_type="number")  # no value set
    return v1


# --- resolves (SubjectResolver) --------------------------------------------

def test_resolves_present_and_absent(conn, seed):
    v1 = _seed_org(seed, conn)
    r = S8S1Reader(SemanticOrgModel(conn), at_seq=v1)
    assert r.resolves("Object", "Account") is True
    assert r.resolves("Field", "Account.Industry") is True
    assert r.resolves("Object", "GhostObject") is False


# --- vrs_for_object (VrReader) ---------------------------------------------

def test_vrs_for_object_returns_active_vr_with_formula(conn, seed):
    v1 = _seed_org(seed, conn)
    r = S8S1Reader(SemanticOrgModel(conn), at_seq=v1)
    vrs = r.vrs_for_object("Account")
    assert len(vrs) == 1
    assert vrs[0].is_active is True
    assert vrs[0].formula_text == _FORMULA


def test_vrs_for_object_unknown_object_is_empty(conn, seed):
    v1 = _seed_org(seed, conn)
    r = S8S1Reader(SemanticOrgModel(conn), at_seq=v1)
    assert r.vrs_for_object("GhostObject") == ()


# --- active_values (PicklistReader) ----------------------------------------

def test_active_values_returns_active_picklist_values(conn, seed):
    v1 = _seed_org(seed, conn)
    r = S8S1Reader(SemanticOrgModel(conn), at_seq=v1)
    # only the ACTIVE value (Technology); Mining is inactive -> excluded.
    assert r.active_values("Account", "Industry") == frozenset({"Technology"})


def test_active_values_non_picklist_field_is_none(conn, seed):
    v1 = _seed_org(seed, conn)
    r = S8S1Reader(SemanticOrgModel(conn), at_seq=v1)
    assert r.active_values("Account", "Amount") is None       # no value set


def test_active_values_missing_field_is_none(conn, seed):
    v1 = _seed_org(seed, conn)
    r = S8S1Reader(SemanticOrgModel(conn), at_seq=v1)
    assert r.active_values("Account", "GhostField") is None
