"""Integration: the v1 validator reading S1 at true parity (D-161 / D-161.1, Step 3.4).

Seeds an S1 org with the metadata the validator's rules need — a createable
Object, a createable string field, a read-only (non-createable) field, and a
picklist field whose value set is reachable via the
``field_details.picklist_value_set_entity_id`` 2-hop — hydrates the
``MetadataS1Reader``, and drives ``TestCaseValidator`` directly off the reader.
Asserts each rule fires off S1 exactly as it would off ``meta_*``:
``object_not_found`` / ``field_not_found`` / ``field_not_createable`` (CRITICAL) +
``picklist_value_not_allowed`` (WARNING), and a clean step yields nothing.

Steps reference fields by **bare** name (``field_values: {"Name": …}``) — the
shape real test cases use — so this is the parity assertion the 3.2 reader test
should have made (D-161.1: the reader strips the object prefix the S1 sync stores).
Reuses the semantic ``conn`` + ``seed`` fixtures (every write rolls back).
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.metadata_bridge.s1_reader import hydrate_metadata_s1_reader
from primeqa.semantic.query import SemanticOrgModel


def _object_details(conn, obj_id, *, is_createable, is_custom):
    conn.execute(text(
        "INSERT INTO object_details (entity_id, is_createable, is_custom) "
        "VALUES (CAST(:e AS uuid), :c, :cu)"),
        {"e": str(obj_id), "c": is_createable, "cu": is_custom})


def _field_details(conn, field_id, obj_id, *, field_type, is_custom,
                   is_createable=True, pvs_id=None):
    conn.execute(text(
        "INSERT INTO field_details "
        "(entity_id, object_entity_id, field_type, is_custom, is_createable, "
        " picklist_value_set_entity_id) "
        "VALUES (CAST(:f AS uuid), CAST(:o AS uuid), :ft, :cu, :cr, "
        " CAST(:pvs AS uuid))"),
        {"f": str(field_id), "o": str(obj_id), "ft": field_type,
         "cu": is_custom, "cr": is_createable,
         "pvs": str(pvs_id) if pvs_id else None})


def _pv(seed, conn, pvs_id, api_name, vfrom, sort_order=0):
    """Seed a PicklistValue entity + its picklist_value_details row under a PVS."""
    eid = seed.entity("PicklistValue", f"PV.{api_name}", vfrom)
    conn.execute(text(
        "INSERT INTO picklist_value_details "
        "(entity_id, picklist_value_set_entity_id, value_label, value_api_name, "
        " is_active, is_default, sort_order) "
        "VALUES (CAST(:eid AS uuid), CAST(:pvs AS uuid), :label, :api, "
        " TRUE, FALSE, :ord)"),
        {"eid": str(eid), "pvs": str(pvs_id), "label": api_name,
         "api": api_name, "ord": sort_order})
    return eid


def _seed_org(seed, conn):
    """Account + Name (createable string) + ReadOnly__c (non-createable string) +
    Industry__c (picklist: Technology/Finance). Field entities are seeded
    OBJECT-QUALIFIED (``Account.Name`` — as the real sync's _extract_external_id
    stores them); the reader strips the prefix to the bare name. Returns
    ``(seq, obj_id)``."""
    v1 = seed.version()
    obj = seed.entity("Object", "Account", v1)
    _object_details(conn, obj, is_createable=True, is_custom=False)

    name = seed.entity("Field", "Account.Name", v1,
                       attributes={"is_required": True})
    _field_details(conn, name, obj, field_type="string", is_custom=False)
    seed.edge(name, obj, "BELONGS_TO", "STRUCTURAL", v1)

    readonly = seed.entity("Field", "Account.ReadOnly__c", v1)
    _field_details(conn, readonly, obj, field_type="string", is_custom=True,
                   is_createable=False)
    seed.edge(readonly, obj, "BELONGS_TO", "STRUCTURAL", v1)

    industry = seed.entity("Field", "Account.Industry__c", v1)
    pvs = seed.entity("PicklistValueSet", "Account.Industry__c.VS", v1)
    _pv(seed, conn, pvs, "Technology", v1, sort_order=0)
    _pv(seed, conn, pvs, "Finance", v1, sort_order=1)
    _field_details(conn, industry, obj, field_type="picklist", is_custom=True,
                   pvs_id=pvs)
    seed.edge(industry, obj, "BELONGS_TO", "STRUCTURAL", v1)
    return v1, obj


def _validator(conn, seq):
    # Function-local import: the module-level name would trip pytest's
    # Test*-prefixed collection heuristic on the TestCaseValidator class.
    from primeqa.intelligence.validator import TestCaseValidator
    reader = hydrate_metadata_s1_reader(SemanticOrgModel(conn), seq)
    # meta_version_id must be truthy or the validator skips hydration; the reader
    # ignores its value (it is pinned to the S1 seq it was hydrated at).
    return TestCaseValidator(reader, meta_version_id=1)


def _rules(report):
    return {(i["rule"], i["severity"]) for i in report["issues"]}


def test_reader_strips_prefix_and_populates_picklist(conn, seed):
    # The D-161.1 + D-161 surface in one place: BARE field names + the 2-hop.
    v1, obj = _seed_org(seed, conn)
    reader = hydrate_metadata_s1_reader(SemanticOrgModel(conn), v1)
    by_name = {f.api_name: f for f in reader.get_fields(object_id=obj)}
    assert set(by_name) == {"Name", "ReadOnly__c", "Industry__c"}
    assert by_name["ReadOnly__c"].is_createable is False
    # picklist values land as a LIST (the validator's _picklist_values needs
    # isinstance(pv, list)); non-picklist fields stay the empty tuple (skipped).
    assert set(by_name["Industry__c"].picklist_values) == {"Technology", "Finance"}
    assert isinstance(by_name["Industry__c"].picklist_values, list)
    assert by_name["Name"].picklist_values == ()


def test_object_not_found_critical(conn, seed):
    v1, _ = _seed_org(seed, conn)
    report = _validator(conn, v1).validate([
        {"step_order": 1, "action": "create", "target_object": "Ghost__c",
         "field_values": {"Name": "x"}}])
    assert ("object_not_found", "critical") in _rules(report)
    assert report["status"] == "critical"


def test_field_not_found_critical(conn, seed):
    # Bare "Bogus__c" is absent — this would FALSE-positive on every field if the
    # reader leaked qualified names (the D-161.1 regression guard).
    v1, _ = _seed_org(seed, conn)
    report = _validator(conn, v1).validate([
        {"step_order": 1, "action": "create", "target_object": "Account",
         "field_values": {"Bogus__c": "x"}}])
    assert ("field_not_found", "critical") in _rules(report)


def test_field_not_createable_critical(conn, seed):
    # The GAP-1 payoff: 3.3's real is_createable=False reaches the validator off S1.
    v1, _ = _seed_org(seed, conn)
    report = _validator(conn, v1).validate([
        {"step_order": 1, "action": "create", "target_object": "Account",
         "field_values": {"ReadOnly__c": "x"}}])
    assert ("field_not_createable", "critical") in _rules(report)


def test_picklist_value_not_allowed_warning(conn, seed):
    # The 2-hop payoff: a value outside the seeded set is flagged (WARNING).
    v1, _ = _seed_org(seed, conn)
    report = _validator(conn, v1).validate([
        {"step_order": 1, "action": "create", "target_object": "Account",
         "field_values": {"Industry__c": "Banking"}}])
    assert ("picklist_value_not_allowed", "warning") in _rules(report)
    assert report["status"] == "warnings"


def test_clean_step_no_issues(conn, seed):
    # Createable, existing, in-set bare-name field values -> nothing flagged.
    v1, _ = _seed_org(seed, conn)
    report = _validator(conn, v1).validate([
        {"step_order": 1, "action": "create", "target_object": "Account",
         "state_ref": "$a",
         "field_values": {"Name": "Acme", "Industry__c": "Technology"}}])
    assert report["issues"] == []
    assert report["status"] == "ok"
