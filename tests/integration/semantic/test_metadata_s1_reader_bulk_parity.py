"""Golden-equivalence: the D-189 bulk hydration == the pre-D-189 per-entity one.

The D-189 rewrite of ``hydrate_metadata_s1_reader`` swaps tens-of-thousands of
per-entity reads for ~6 bulk queries. This test proves it is a **byte-for-byte
drop-in** by vendoring a copy of the ORIGINAL per-entity hydration
(:func:`_legacy_hydrate`, using only the retained per-entity primitives
``get_entity_details`` / ``get_related`` / ``get_picklist_values``) and asserting
the two readers serialize identically — ``get_objects`` / ``get_fields``
(whole-org AND per-object) / ``get_validation_rules``, including each field's
``picklist_values`` value **and its container type** (so a list→tuple drift fails).

Seeds a deliberately adversarial org: an object with no ``object_details`` row, an
object with no fields, an object with a read-only + required + picklist field, a
picklist set with 2 values and a separate picklist field whose set has 0
values-at-seq, a VR with and a VR without an ``APPLIES_TO`` object, and a second
version that supersedes a field — so the equivalence holds across the as-of window
too. Reuses the semantic ``conn`` + ``seed`` fixtures (every write rolls back).
"""
from __future__ import annotations

from sqlalchemy import text

from primeqa.metadata_bridge.s1_reader import (
    MetadataS1Reader,
    _APPLIES_TO,
    _BELONGS_TO,
    _S1Field,
    _S1Object,
    _S1ObjRef,
    _S1ValidationRule,
    hydrate_metadata_s1_reader,
)
from primeqa.semantic.query import SemanticOrgModel


# --- the pre-D-189 per-entity hydration, vendored verbatim ------------------

def _legacy_hydrate(model, seq) -> MetadataS1Reader:
    """A copy of the original per-entity ``hydrate_metadata_s1_reader`` body
    (uses ``get_entity_details`` / ``get_related`` / ``get_picklist_values``
    per entity). The oracle the bulk rewrite must match exactly."""
    objects = []
    fields_by_obj = {}
    for e in sorted(model.get_entities("Object", at_seq=seq),
                    key=lambda x: x.sf_api_name or ""):
        od = model.get_entity_details(e.id, at_seq=seq) or {}
        objects.append(_S1Object(
            id=e.id, api_name=e.sf_api_name, label=e.display_name,
            is_createable=bool(od.get("is_createable", True)),
            is_custom=bool(od.get("is_custom", False))))
        obj_api = e.sf_api_name or ""
        flds = []
        for r in model.get_related(e.id, edge_types=[_BELONGS_TO],
                                   direction="inbound", at_seq=seq):
            fe = r.entity
            if fe.entity_type != "Field":
                continue
            fd = model.get_entity_details(fe.id, at_seq=seq) or {}
            attrs = fe.attributes or {}
            fe_api = fe.sf_api_name or ""
            bare = (fe_api[len(obj_api) + 1:]
                    if obj_api and fe_api.startswith(obj_api + ".") else fe_api)
            pvs_id = fd.get("picklist_value_set_entity_id")
            picklist_values = ()
            if pvs_id:
                picklist_values = [
                    pv["value_api_name"]
                    for pv in model.get_picklist_values(pvs_id, at_seq=seq)
                    if pv.get("value_api_name")
                ]
            flds.append(_S1Field(
                api_name=bare,
                label=fe.display_name,
                field_type=fd.get("field_type"),
                is_required=bool(attrs.get("is_required", False)),
                is_custom=bool(fd.get("is_custom", False)),
                is_createable=bool(fd.get("is_createable", True)),
                is_updateable=bool(fd.get("is_updateable", True)),
                picklist_values=picklist_values,
                meta_object_id=e.id))
        fields_by_obj[e.id] = tuple(sorted(flds, key=lambda f: f.api_name or ""))

    vrs = []
    for vr in model.get_entities("ValidationRule", at_seq=seq):
        obj_ref = None
        for r in model.get_related(vr.id, edge_types=[_APPLIES_TO],
                                   direction="outbound", at_seq=seq):
            if r.entity.entity_type == "Object":
                obj_ref = _S1ObjRef(api_name=r.entity.sf_api_name)
                break
        attrs = vr.attributes or {}
        vrs.append(_S1ValidationRule(
            rule_name=vr.sf_api_name or vr.display_name or str(vr.id),
            error_message=attrs.get("error_message"),
            meta_object=obj_ref))
    vrs.sort(key=lambda v: ((v.meta_object.api_name if v.meta_object else ""),
                            v.rule_name or ""))
    return MetadataS1Reader(objects, fields_by_obj, vrs)


# --- detail-row seeding helpers (the *_details tables, keyed by entity_id) ---

def _object_details(conn, obj_id, *, is_createable=True, is_custom=False):
    conn.execute(text(
        "INSERT INTO object_details (entity_id, is_createable, is_custom) "
        "VALUES (CAST(:e AS uuid), :c, :cu)"),
        {"e": str(obj_id), "c": is_createable, "cu": is_custom})


def _field_details(conn, field_id, obj_id, *, field_type="string", is_custom=False,
                   is_createable=True, is_updateable=True, pvs_id=None):
    conn.execute(text(
        "INSERT INTO field_details "
        "(entity_id, object_entity_id, field_type, is_custom, is_createable, "
        " is_updateable, picklist_value_set_entity_id) "
        "VALUES (CAST(:f AS uuid), CAST(:o AS uuid), :ft, :cu, :cr, :up, "
        " CAST(:pvs AS uuid))"),
        {"f": str(field_id), "o": str(obj_id), "ft": field_type, "cu": is_custom,
         "cr": is_createable, "up": is_updateable,
         "pvs": str(pvs_id) if pvs_id else None})


def _pv(seed, conn, pvs_id, api_name, vfrom, sort_order=0):
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


def _seed_rich_org(seed, conn):
    """An adversarial org spanning v1→v2. Returns ``(v1, v2)``."""
    v1 = seed.version()
    v2 = seed.version()

    # Object A (Account): object_details + a required string, a read-only custom
    # field (superseded at v2), and a picklist field with 2 values.
    acct = seed.entity("Object", "Account", v1)
    _object_details(conn, acct, is_createable=True, is_custom=False)

    name = seed.entity("Field", "Account.Name", v1, attributes={"is_required": True})
    _field_details(conn, name, acct, field_type="string", is_custom=False)
    seed.edge(name, acct, "BELONGS_TO", "STRUCTURAL", v1)

    # ReadOnly__c: present at v1 (is_createable False), superseded at v2 by a row
    # with is_createable True — exercises the far-entity as-of in both readers.
    ro1 = seed.entity("Field", "Account.ReadOnly__c", v1, valid_to_seq=v2)
    _field_details(conn, ro1, acct, field_type="string", is_custom=True,
                   is_createable=False)
    seed.edge(ro1, acct, "BELONGS_TO", "STRUCTURAL", v1, valid_to_seq=v2)
    ro2 = seed.entity("Field", "Account.ReadOnly__c", v2)
    _field_details(conn, ro2, acct, field_type="string", is_custom=True,
                   is_createable=True)
    seed.edge(ro2, acct, "BELONGS_TO", "STRUCTURAL", v2)

    industry = seed.entity("Field", "Account.Industry__c", v1)
    pvs = seed.entity("PicklistValueSet", "Account.Industry__c.VS", v1)
    _pv(seed, conn, pvs, "Technology", v1, sort_order=0)
    _pv(seed, conn, pvs, "Finance", v1, sort_order=1)
    _field_details(conn, industry, acct, field_type="picklist", is_custom=True,
                   pvs_id=pvs)
    seed.edge(industry, acct, "BELONGS_TO", "STRUCTURAL", v1)

    # Object B (Contact): NO object_details row; one picklist field whose value
    # set has ZERO values-at-seq (→ picklist_values stays ()).
    contact = seed.entity("Object", "Contact", v1)
    leadsrc = seed.entity("Field", "Contact.LeadSource__c", v1)
    empty_pvs = seed.entity("PicklistValueSet", "Contact.LeadSource__c.VS", v1)
    _field_details(conn, leadsrc, contact, field_type="picklist", is_custom=True,
                   pvs_id=empty_pvs)
    seed.edge(leadsrc, contact, "BELONGS_TO", "STRUCTURAL", v1)

    # Object C (Lead): object_details but ZERO fields (→ fields_by_obj[id] == ()).
    lead = seed.entity("Object", "Lead", v1)
    _object_details(conn, lead, is_createable=True, is_custom=True)

    # VR with APPLIES_TO Account, and a VR with no object edge (meta_object None).
    vr1 = seed.entity("ValidationRule", "Account.RequireName", v1,
                      attributes={"error_message": "Name required"})
    seed.edge(vr1, acct, "APPLIES_TO", "BEHAVIOR", v1)
    seed.entity("ValidationRule", "Orphan.NoObject", v1,
                attributes={"error_message": "orphan"})  # no APPLIES_TO edge
    return v1, v2


# --- serialization: every dataclass field, including picklist container TYPE ---

def _field_tuple(f):
    return (
        f.api_name, f.label, f.field_type, f.is_required, f.is_custom,
        f.is_createable, f.is_updateable, str(f.meta_object_id), f.reference_to,
        # value AND container type — a list→tuple drift (the validator's
        # isinstance(pv, list) gate) must fail the comparison.
        (type(f.picklist_values).__name__, tuple(f.picklist_values)),
    )


def _reader_to_tuples(reader):
    objs = [(str(o.id), o.api_name, o.label, o.is_createable, o.is_custom)
            for o in reader.get_objects()]
    whole = [_field_tuple(f) for f in reader.get_fields()]
    per_obj = {str(o.id): [_field_tuple(f) for f in reader.get_fields(object_id=o.id)]
               for o in reader.get_objects()}
    vrs = [(v.rule_name, v.error_message,
            v.meta_object.api_name if v.meta_object else None)
           for v in reader.get_validation_rules()]
    return {"objects": objs, "fields_whole": whole,
            "fields_per_obj": per_obj, "vrs": vrs}


def test_bulk_hydration_equals_legacy_at_each_version(conn, seed):
    v1, v2 = _seed_rich_org(seed, conn)
    for seq in (v1, v2):
        new = hydrate_metadata_s1_reader(SemanticOrgModel(conn), seq)
        legacy = _legacy_hydrate(SemanticOrgModel(conn), seq)
        assert _reader_to_tuples(new) == _reader_to_tuples(legacy), \
            f"bulk vs per-entity hydration diverged at seq={seq}"


def test_bulk_hydration_structural_cases(conn, seed):
    # Pin the specific adversarial shapes directly (not just via equivalence), so a
    # regression names the broken case rather than a giant dict diff.
    v1, _ = _seed_rich_org(seed, conn)
    reader = hydrate_metadata_s1_reader(SemanticOrgModel(conn), v1)
    objs = {o.api_name: o for o in reader.get_objects()}

    # (b) object with no object_details → defaults (is_createable True, custom False)
    assert objs["Contact"].is_createable is True and objs["Contact"].is_custom is False
    # object WITH details keeps its values
    assert objs["Lead"].is_custom is True

    # (e) zero-field object → empty tuple
    assert reader.get_fields(object_id=objs["Lead"].id) == []

    # picklist shapes (each preserved byte-for-byte from the per-entity reader):
    acct_fields = {f.api_name: f for f in reader.get_fields(object_id=objs["Account"].id)}
    contact_fields = {f.api_name: f for f in reader.get_fields(object_id=objs["Contact"].id)}
    # - field with a value set AND values → list[str] of the value api-names
    assert isinstance(acct_fields["Industry__c"].picklist_values, list)
    assert set(acct_fields["Industry__c"].picklist_values) == {"Technology", "Finance"}
    # - field with NO picklist set (pvs_id falsy) → the empty TUPLE () (skipped)
    assert acct_fields["Name"].picklist_values == ()
    assert isinstance(acct_fields["Name"].picklist_values, tuple)
    # - field WITH a value set but 0 values-at-seq → the empty LIST [] (pre-existing
    #   behavior, identical in both readers — the `if pvs_id:` branch yields []).
    assert contact_fields["LeadSource__c"].picklist_values == []

    # (g) label = Entity.display_name (the seed defaults display_name to the api
    #     name; in production it's the SF object/field label). The picker reads it.
    assert objs["Account"].label == "Account"
    assert acct_fields["Name"].label == "Account.Name"

    # (f) VR without an APPLIES_TO object → meta_object None; with → the object
    vrs = {v.rule_name: v for v in reader.get_validation_rules()}
    assert vrs["Orphan.NoObject"].meta_object is None
    assert vrs["Account.RequireName"].meta_object.api_name == "Account"
