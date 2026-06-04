"""Integration: MetadataS1Reader — S1 → the v1 meta_* read-shape (D-159, Step 3.2).

Seeds an isolated S1 graph (an Object + object_details; a required Field + a custom
picklist Field, each with field_details + a BELONGS_TO edge; a VR APPLIES_TO the
object), hydrates the reader through ``SemanticOrgModel``, and asserts the
duck-types + ordering + that the reader drives ``_build_metadata_context``
(the generation read-path that 3.2 routes to S1). Reuses the semantic package's
transactional ``conn`` + ``seed`` fixtures (every write rolls back).
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.metadata.s1_reader import (
    build_metadata_s1_reader,
    hydrate_metadata_s1_reader,
)
from primeqa.semantic.query import SemanticOrgModel


def _object_details(conn, obj_id, *, is_createable, is_custom):
    conn.execute(text(
        "INSERT INTO object_details (entity_id, is_createable, is_custom) "
        "VALUES (CAST(:e AS uuid), :c, :cu)"),
        {"e": str(obj_id), "c": is_createable, "cu": is_custom})


def _field_details(conn, field_id, obj_id, *, field_type, is_custom):
    conn.execute(text(
        "INSERT INTO field_details "
        "(entity_id, object_entity_id, field_type, is_custom) "
        "VALUES (CAST(:f AS uuid), CAST(:o AS uuid), :ft, :cu)"),
        {"f": str(field_id), "o": str(obj_id), "ft": field_type, "cu": is_custom})


def _seed_org(seed, conn):
    """Account (createable) + Name (required string) + Industry__c (custom
    picklist) + a VR. Returns ``(seq, obj_id)``."""
    v1 = seed.version()
    obj = seed.entity("Object", "Account", v1)
    _object_details(conn, obj, is_createable=True, is_custom=False)

    name = seed.entity("Field", "Account.Name", v1,
                       attributes={"is_required": True})
    _field_details(conn, name, obj, field_type="string", is_custom=False)
    seed.edge(name, obj, "BELONGS_TO", "STRUCTURAL", v1)

    industry = seed.entity("Field", "Account.Industry__c", v1,
                           attributes={"is_required": False})
    _field_details(conn, industry, obj, field_type="picklist", is_custom=True)
    seed.edge(industry, obj, "BELONGS_TO", "STRUCTURAL", v1)

    vr = seed.entity("ValidationRule", "Account.RequireName", v1,
                     attributes={"error_message": "Name required"})
    seed.edge(vr, obj, "APPLIES_TO", "BEHAVIOR", v1)
    return v1, obj


def test_hydrate_objects(conn, seed):
    v1, obj = _seed_org(seed, conn)
    reader = hydrate_metadata_s1_reader(SemanticOrgModel(conn), v1)
    objs = reader.get_objects()
    assert [o.api_name for o in objs] == ["Account"]
    assert objs[0].is_createable is True and objs[0].is_custom is False
    assert str(objs[0].id) == str(obj)


def test_hydrate_fields_ordered_and_typed(conn, seed):
    v1, obj = _seed_org(seed, conn)
    reader = hydrate_metadata_s1_reader(SemanticOrgModel(conn), v1)
    fields = reader.get_fields(object_id=reader.get_objects()[0].id)
    # D-161.1: the reader emits BARE field api-names (strips the object prefix the
    # S1 sync stores — `{Object}.{field}`), matching v1 meta_* + bare-name steps.
    assert [f.api_name for f in fields] == ["Industry__c", "Name"]
    by_name = {f.api_name: f for f in fields}
    assert by_name["Name"].is_required is True
    assert by_name["Name"].field_type == "string"
    assert by_name["Industry__c"].is_custom is True
    # is_createable defaults True when field_details carries no explicit value
    # (this seed omits it; 3.3's column server-defaults true).
    assert all(f.is_createable is True for f in fields)
    assert all(str(f.meta_object_id) == str(obj) for f in fields)


def test_hydrate_validation_rules(conn, seed):
    v1, obj = _seed_org(seed, conn)
    reader = hydrate_metadata_s1_reader(SemanticOrgModel(conn), v1)
    vrs = reader.get_validation_rules()
    assert len(vrs) == 1
    assert vrs[0].rule_name == "Account.RequireName"
    assert vrs[0].error_message == "Name required"
    assert vrs[0].meta_object.api_name == "Account"


def test_reader_drives_build_metadata_context(conn, seed):
    # The reader produces exactly what generation's _build_metadata_context needs
    # — the deterministic descriptive text (the substance of meta_*-parity).
    v1, obj = _seed_org(seed, conn)
    reader = hydrate_metadata_s1_reader(SemanticOrgModel(conn), v1)
    from primeqa.intelligence.generation import TestCaseGenerator
    gen = TestCaseGenerator.__new__(TestCaseGenerator)   # bypass __init__
    gen.metadata_repo = reader
    ctx = gen._build_metadata_context(None)
    assert ctx["objects"] == [
        "Account [required: Name] [custom: Industry__c]"]
    # VR naming stays object-qualified (D-161.1: no validator rule reads VR names;
    # generation-context VR-list text only).
    assert ctx["validation_rules"] == [
        "Account.Account.RequireName: Name required"]


def test_build_reader_best_effort_on_bad_tenant():
    # tenant_id <= 0 → get_tenant_connection raises → best-effort returns None.
    assert build_metadata_s1_reader(-1) is None
