"""Integration tests for substrate-1's query interface (SPEC §12 / D-022),
read subset: ``current_version_seq`` / ``get_entities`` / ``get_related``.

Exercises the bitemporal as-of predicate (SPEC §6.2), the filter + direction
contracts, far-end as-of resolution, and the fail-loud
``VersionNotFoundError``. Runs against a local PG test DB (see conftest);
skips cleanly if PG is unreachable. Every test's writes roll back.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from primeqa.semantic.query import (
    Entity,
    RelatedEntity,
    SemanticOrgModel,
    VersionNotFoundError,
)


# ---------------------------------------------------------------------------
# current_version_seq
# ---------------------------------------------------------------------------

def test_current_version_seq_returns_max(conn, seed):
    v1 = seed.version()
    v2 = seed.version()
    assert v2 > v1
    s1 = SemanticOrgModel(conn)
    assert s1.current_version_seq() == v2


def test_current_version_seq_no_versions_raises(conn):
    # No versions seeded in this transaction — fail-loud, not a sentinel.
    s1 = SemanticOrgModel(conn)
    with pytest.raises(VersionNotFoundError):
        s1.current_version_seq()


# ---------------------------------------------------------------------------
# get_entities — existence + filters
# ---------------------------------------------------------------------------

def test_get_entities_existence_empty_and_nonempty(conn, seed):
    v1 = seed.version()
    seed.entity("Object", "Account", v1)
    s1 = SemanticOrgModel(conn)

    objs = s1.get_entities("Object", at_seq=v1)
    assert len(objs) == 1 and isinstance(objs[0], Entity)
    assert objs[0].entity_type == "Object" and objs[0].sf_api_name == "Account"

    # no Field entities seeded -> empty (existence == non-empty result)
    assert s1.get_entities("Field", at_seq=v1) == []
    # filter that matches nothing -> empty
    assert s1.get_entities("Object", at_seq=v1,
                           filters={"sf_api_name": "DoesNotExist"}) == []


def test_get_entities_each_filter(conn, seed):
    v1 = seed.version()
    e1 = seed.entity("Object", "Obj_A", v1, sf_id="001AAA", display_name="Alpha",
                     attributes={"k": 1})
    e2 = seed.entity("Object", "Obj_B", v1, sf_id="001BBB", display_name="Beta")
    s1 = SemanticOrgModel(conn)

    assert {e.id for e in s1.get_entities("Object", at_seq=v1)} == {e1, e2}
    assert [e.id for e in s1.get_entities("Object", at_seq=v1,
            filters={"sf_api_name": "Obj_A"})] == [e1]
    assert [e.id for e in s1.get_entities("Object", at_seq=v1,
            filters={"sf_id": "001BBB"})] == [e2]
    assert [e.id for e in s1.get_entities("Object", at_seq=v1,
            filters={"display_name": "Alpha"})] == [e1]
    # uuid-typed filter (cast path)
    assert [e.id for e in s1.get_entities("Object", at_seq=v1,
            filters={"id": e1})] == [e1]
    # property inspection
    assert s1.get_entities("Object", at_seq=v1,
                           filters={"id": e1})[0].attributes == {"k": 1}


def test_get_entities_unknown_filter_raises(conn, seed):
    v1 = seed.version()
    seed.entity("Object", "Account", v1)
    s1 = SemanticOrgModel(conn)
    with pytest.raises(ValueError):
        s1.get_entities("Object", at_seq=v1, filters={"attributes": "x"})


# ---------------------------------------------------------------------------
# get_entities — bitemporal as-of correctness (the core test)
# ---------------------------------------------------------------------------

def test_get_entities_as_of_supersession(conn, seed):
    v1 = seed.version()
    v2 = seed.version()
    # Same logical object across two versions: row A closes at v2, row B opens.
    a = seed.entity("Object", "Acct", v1, valid_to_seq=v2, attributes={"v": 1})
    b = seed.entity("Object", "Acct", v2, attributes={"v": 2})  # valid_to NULL
    s1 = SemanticOrgModel(conn)

    # As of v1: only A (valid_from=v1 <= v1, valid_to=v2 > v1).
    at_v1 = s1.get_entities("Object", at_seq=v1, filters={"sf_api_name": "Acct"})
    assert [e.id for e in at_v1] == [a]
    assert at_v1[0].valid_to_seq == v2 and at_v1[0].attributes == {"v": 1}

    # As of v2: only B. A is excluded because valid_to=v2 is NOT > v2 (strict).
    at_v2 = s1.get_entities("Object", at_seq=v2, filters={"sf_api_name": "Acct"})
    assert [e.id for e in at_v2] == [b]
    assert at_v2[0].valid_to_seq is None and at_v2[0].attributes == {"v": 2}


# ---------------------------------------------------------------------------
# get_related — direction, edge_types, far-end resolution
# ---------------------------------------------------------------------------

def test_get_related_direction_and_edge_types(conn, seed):
    v1 = seed.version()
    o = seed.entity("Object", "Account", v1)
    f = seed.entity("Field", "Account.Name", v1, attributes={"required": True})
    seed.edge(f, o, "BELONGS_TO", "STRUCTURAL", v1)  # F -> O
    s1 = SemanticOrgModel(conn)

    # outbound from the field: one edge, far end is the object.
    out = s1.get_related(f, ["BELONGS_TO"], "outbound", at_seq=v1)
    assert len(out) == 1 and isinstance(out[0], RelatedEntity)
    assert out[0].direction == "outbound" and out[0].edge_type == "BELONGS_TO"
    assert out[0].edge_category == "STRUCTURAL"
    assert out[0].entity.id == o and out[0].entity.entity_type == "Object"

    # inbound to the object: same edge, far end is the field (with attributes).
    inb = s1.get_related(o, ["BELONGS_TO"], "inbound", at_seq=v1)
    assert len(inb) == 1 and inb[0].direction == "inbound"
    assert inb[0].entity.id == f and inb[0].entity.attributes == {"required": True}

    # outbound from the object: nothing (object is not a BELONGS_TO source).
    assert s1.get_related(o, ["BELONGS_TO"], "outbound", at_seq=v1) == []
    # edge_types filter excludes non-matching types.
    assert s1.get_related(f, ["HAS_FIELD"], "outbound", at_seq=v1) == []
    # empty edge_types -> [].
    assert s1.get_related(f, [], "outbound", at_seq=v1) == []
    # both: the F->O edge surfaces once (F is its source).
    assert len(s1.get_related(f, ["BELONGS_TO"], "both", at_seq=v1)) == 1


def test_get_related_far_end_resolved_at_same_seq(conn, seed):
    v1 = seed.version()
    v2 = seed.version()
    f = seed.entity("Field", "Account.Name", v1)  # stable across both
    o1 = seed.entity("Object", "Account", v1, valid_to_seq=v2, attributes={"label": "v1"})
    o2 = seed.entity("Object", "Account", v2, attributes={"label": "v2"})
    seed.edge(f, o1, "BELONGS_TO", "STRUCTURAL", v1, valid_to_seq=v2)  # edge@v1 -> o1
    seed.edge(f, o2, "BELONGS_TO", "STRUCTURAL", v2)                    # edge@v2 -> o2
    s1 = SemanticOrgModel(conn)

    at_v1 = s1.get_related(f, ["BELONGS_TO"], "outbound", at_seq=v1)
    assert len(at_v1) == 1
    assert at_v1[0].entity.id == o1 and at_v1[0].entity.attributes == {"label": "v1"}

    at_v2 = s1.get_related(f, ["BELONGS_TO"], "outbound", at_seq=v2)
    assert len(at_v2) == 1
    assert at_v2[0].entity.id == o2 and at_v2[0].entity.attributes == {"label": "v2"}


def test_get_related_edge_as_of(conn, seed):
    v1 = seed.version()
    v2 = seed.version()
    o = seed.entity("Object", "Account", v1)  # valid across both
    f = seed.entity("Field", "Account.Name", v1)
    seed.edge(f, o, "BELONGS_TO", "STRUCTURAL", v1, valid_to_seq=v2)  # edge only at v1
    s1 = SemanticOrgModel(conn)

    assert len(s1.get_related(f, ["BELONGS_TO"], "outbound", at_seq=v1)) == 1
    # edge closed at v2 (valid_to=v2 not > v2) -> excluded.
    assert s1.get_related(f, ["BELONGS_TO"], "outbound", at_seq=v2) == []


def test_get_related_bad_direction_raises(conn, seed):
    v1 = seed.version()
    f = seed.entity("Field", "Account.Name", v1)
    s1 = SemanticOrgModel(conn)
    with pytest.raises(ValueError):
        s1.get_related(f, ["BELONGS_TO"], "sideways", at_seq=v1)


# ---------------------------------------------------------------------------
# Fail-loud version validation
# ---------------------------------------------------------------------------

def test_get_entities_unknown_at_seq_raises(conn, seed):
    seed.version()  # ensure the table is non-empty, but use an absent seq
    s1 = SemanticOrgModel(conn)
    with pytest.raises(VersionNotFoundError):
        s1.get_entities("Object", at_seq=987654321)


def test_get_related_validates_at_seq_before_empty_shortcut(conn):
    # Even with empty edge_types, an invalid pin must fail loud (validation
    # precedes the empty-edge_types short-circuit).
    s1 = SemanticOrgModel(conn)
    with pytest.raises(VersionNotFoundError):
        s1.get_related(uuid4(), [], "outbound", at_seq=987654321)


def test_validated_seq_is_cached(conn, seed):
    v1 = seed.version()
    s1 = SemanticOrgModel(conn)
    # First call validates + caches; second must not raise and must hit cache.
    s1.get_entities("Object", at_seq=v1)
    assert v1 in s1._validated_seqs
    s1.get_entities("Object", at_seq=v1)  # no raise


# ---------------------------------------------------------------------------
# get_picklist_values (D-119) — value-set value enumeration (the value-claim
# accepted-values read; the middle hop edges + get_entity_details can't cover)
# ---------------------------------------------------------------------------

def _pv(seed, conn, pvs_id, api_name, vfrom, vto=None, sort_order=0, is_active=True):
    """Seed a PicklistValue entity + its picklist_value_details row under a PVS."""
    eid = seed.entity("PicklistValue", f"PV.{api_name}", vfrom, valid_to_seq=vto)
    conn.execute(
        text(
            "INSERT INTO picklist_value_details "
            "(entity_id, picklist_value_set_entity_id, value_label, "
            " value_api_name, is_active, is_default, sort_order) "
            "VALUES (CAST(:eid AS uuid), CAST(:pvs AS uuid), :label, "
            " :api, :active, FALSE, :ord)"
        ),
        {"eid": str(eid), "pvs": str(pvs_id), "label": api_name,
         "api": api_name, "active": is_active, "ord": sort_order},
    )
    return eid


def test_get_picklist_values_ordered_with_flags(conn, seed):
    v1 = seed.version()
    pvs = seed.entity("PicklistValueSet", "SVS:Industry", v1)
    _pv(seed, conn, pvs, "Banking", v1, sort_order=1)
    _pv(seed, conn, pvs, "Agriculture", v1, sort_order=0)
    _pv(seed, conn, pvs, "Retired", v1, sort_order=2, is_active=False)
    s1 = SemanticOrgModel(conn)

    rows = s1.get_picklist_values(pvs, at_seq=v1)
    assert [r["value_api_name"] for r in rows] == ["Agriculture", "Banking", "Retired"]
    # flags surface so the caller (S3) filters is_active for the accepted set
    assert [r["is_active"] for r in rows] == [True, True, False]
    assert {"entity_id", "value_label", "is_default", "sort_order"} <= set(rows[0].keys())


def test_get_picklist_values_empty_for_no_values(conn, seed):
    v1 = seed.version()
    pvs = seed.entity("PicklistValueSet", "SVS:Empty", v1)
    s1 = SemanticOrgModel(conn)
    assert s1.get_picklist_values(pvs, at_seq=v1) == []
    # an unrelated/unknown id -> empty, not an error
    assert s1.get_picklist_values(uuid4(), at_seq=v1) == []


def test_get_picklist_values_as_of_supersession(conn, seed):
    v1 = seed.version()
    v2 = seed.version()
    pvs = seed.entity("PicklistValueSet", "SVS:Status", v1)
    _pv(seed, conn, pvs, "Open", v1, sort_order=0)             # spans both versions
    _pv(seed, conn, pvs, "Legacy", v1, vto=v2, sort_order=1)   # closes at v2
    _pv(seed, conn, pvs, "New", v2, sort_order=2)              # opens at v2
    s1 = SemanticOrgModel(conn)

    # As of v1: Open + Legacy (New not yet valid).
    assert {r["value_api_name"] for r in s1.get_picklist_values(pvs, at_seq=v1)} == {"Open", "Legacy"}
    # As of v2: Open + New (Legacy closed at v2 — strict > window excludes it).
    assert {r["value_api_name"] for r in s1.get_picklist_values(pvs, at_seq=v2)} == {"Open", "New"}


def test_get_picklist_values_validates_at_seq(conn, seed):
    seed.version()  # some version exists, but not the one queried
    s1 = SemanticOrgModel(conn)
    with pytest.raises(VersionNotFoundError):
        s1.get_picklist_values(uuid4(), at_seq=987654321)


def test_get_picklist_values_via_field_edge_chain(conn, seed):
    # The end-to-end chain S3 walks to enumerate a field's accepted values:
    # Field --HAS_PICKLIST_VALUES--> PicklistValueSet --get_picklist_values--> values.
    v1 = seed.version()
    field = seed.entity("Field", "Account.Industry", v1)
    pvs = seed.entity("PicklistValueSet", "SVS:Industry", v1)
    seed.edge(field, pvs, "HAS_PICKLIST_VALUES", "CONFIG", v1)
    _pv(seed, conn, pvs, "Banking", v1, sort_order=0)
    _pv(seed, conn, pvs, "Retail", v1, sort_order=1)
    s1 = SemanticOrgModel(conn)

    related = s1.get_related(field, ["HAS_PICKLIST_VALUES"], "outbound", at_seq=v1)
    assert len(related) == 1
    accepted = {r["value_api_name"]
                for r in s1.get_picklist_values(related[0].entity.id, at_seq=v1)
                if r["is_active"]}
    assert accepted == {"Banking", "Retail"}


# ---------------------------------------------------------------------------
# Phase-0 breadth-unblock readiness (D-120): permission-claim + config grounding
# already work over EXISTING primitives + already-synced edges/details — no new
# S1 primitive needed. These tests pin that so Phase 2 (S3 breadth) can rely on it.
# ---------------------------------------------------------------------------

def test_get_related_grants_field_access_surfaces_read_edit(conn, seed):
    # S3 grounds "Profile P grants read on Field F" by reading the
    # GRANTS_FIELD_ACCESS edge's properties — get_related already returns them.
    v1 = seed.version()
    profile = seed.entity("Profile", "Admin", v1)
    field = seed.entity("Field", "Account.AnnualRevenue", v1)
    seed.edge(profile, field, "GRANTS_FIELD_ACCESS", "PERMISSION", v1,
              properties={"can_read": True, "can_edit": False})
    s1 = SemanticOrgModel(conn)

    grants = s1.get_related(profile, ["GRANTS_FIELD_ACCESS"], "outbound", at_seq=v1)
    assert len(grants) == 1
    assert grants[0].entity.id == field
    assert grants[0].properties == {"can_read": True, "can_edit": False}


def test_get_related_grants_object_access_surfaces_flags(conn, seed):
    v1 = seed.version()
    ps = seed.entity("PermissionSet", "Sales_PS", v1)
    obj = seed.entity("Object", "Opportunity", v1)
    seed.edge(ps, obj, "GRANTS_OBJECT_ACCESS", "PERMISSION", v1,
              properties={"can_read": True, "can_create": True, "can_edit": False})
    s1 = SemanticOrgModel(conn)

    grants = s1.get_related(ps, ["GRANTS_OBJECT_ACCESS"], "outbound", at_seq=v1)
    assert len(grants) == 1
    assert grants[0].entity.entity_type == "Object" and grants[0].entity.id == obj
    assert grants[0].properties["can_create"] is True
    assert grants[0].properties["can_edit"] is False


def test_config_existence_grounding_via_get_entities(conn, seed):
    # config existence-claim ("object/field X exists") grounds on get_entities
    # (existence == a non-empty result) — no new S1 work. (config property-claim
    # rides get_entity_details, proven in test_s6_s1_reader.)
    v1 = seed.version()
    seed.entity("Object", "Custom_Obj__c", v1)
    s1 = SemanticOrgModel(conn)
    assert len(s1.get_entities("Object", at_seq=v1,
                               filters={"sf_api_name": "Custom_Obj__c"})) == 1
    assert s1.get_entities("Object", at_seq=v1,
                           filters={"sf_api_name": "Nope__c"}) == []


# ---------------------------------------------------------------------------
# Bulk reads (D-189) — the whole-org-in-one-query forms the metadata reader
# hydrates with. Each must equal the per-entity form it replaces.
# ---------------------------------------------------------------------------

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


def test_get_entity_details_bulk_keys_and_omits_no_detail_row(conn, seed):
    # Two objects: one WITH an object_details row, one WITHOUT. The bulk map is
    # keyed by entity_id and OMITS the entity that has no detail row (so the
    # reader's `.get(id, {})` supplies the default — the per-entity `od or {}`).
    v1 = seed.version()
    acct = seed.entity("Object", "Account", v1)
    _object_details(conn, acct, is_createable=True, is_custom=False)
    contact = seed.entity("Object", "Contact", v1)  # no object_details
    s1 = SemanticOrgModel(conn)

    details = s1.get_entity_details_bulk("Object", at_seq=v1)
    assert set(details) == {acct}                       # Contact omitted
    assert details[acct]["is_createable"] is True
    assert details[acct]["is_custom"] is False
    # equals the per-entity read for the present row; absent for the missing one.
    assert details[acct] == s1.get_entity_details(acct, at_seq=v1)
    assert details.get(contact, {}) == {}


def test_get_entity_details_bulk_unknown_type_is_empty(conn, seed):
    v1 = seed.version()
    s1 = SemanticOrgModel(conn)
    assert s1.get_entity_details_bulk("NoSuchType", at_seq=v1) == {}


def test_get_entity_details_bulk_as_of(conn, seed):
    # A field-detail row whose entity is superseded must drop out at the later seq.
    v1 = seed.version()
    v2 = seed.version()
    obj = seed.entity("Object", "Account", v1)
    f = seed.entity("Field", "Account.Old__c", v1, valid_to_seq=v2)  # closes at v2
    _field_details(conn, f, obj, field_type="string")
    s1 = SemanticOrgModel(conn)

    assert set(s1.get_entity_details_bulk("Field", at_seq=v1)) == {f}
    assert s1.get_entity_details_bulk("Field", at_seq=v2) == {}  # entity out of window


def test_get_entity_details_bulk_validates_at_seq(conn, seed):
    seed.version()
    s1 = SemanticOrgModel(conn)
    with pytest.raises(VersionNotFoundError):
        s1.get_entity_details_bulk("Object", at_seq=987654321)


def test_get_related_bulk_inbound_grouped_by_near(conn, seed):
    # Two objects, each with its own fields; inbound BELONGS_TO bulk groups by the
    # near (object) id, and each group equals the per-entity get_related.
    v1 = seed.version()
    acct = seed.entity("Object", "Account", v1)
    contact = seed.entity("Object", "Contact", v1)
    a_name = seed.entity("Field", "Account.Name", v1)
    a_rev = seed.entity("Field", "Account.Rev__c", v1)
    c_email = seed.entity("Field", "Contact.Email", v1)
    for f, o in [(a_name, acct), (a_rev, acct), (c_email, contact)]:
        seed.edge(f, o, "BELONGS_TO", "STRUCTURAL", v1)
    s1 = SemanticOrgModel(conn)

    grouped = s1.get_related_bulk(["BELONGS_TO"], "inbound", at_seq=v1)
    assert set(grouped) == {acct, contact}
    assert {r.entity.id for r in grouped[acct]} == {a_name, a_rev}
    assert {r.entity.id for r in grouped[contact]} == {c_email}
    # the grouped value equals the per-entity inbound read (same RelatedEntity shape)
    per_entity = s1.get_related(acct, ["BELONGS_TO"], "inbound", at_seq=v1)
    assert {r.entity.id for r in grouped[acct]} == {r.entity.id for r in per_entity}
    assert grouped[acct][0].direction == "inbound"


def test_get_related_bulk_far_entity_as_of(conn, seed):
    # The critical window: the far (field) entity is superseded across v2. At v1 the
    # field is present; at v2 it must be absent (the JOIN keeps _as_of('t')).
    v1 = seed.version()
    v2 = seed.version()
    obj = seed.entity("Object", "Account", v1)        # spans both
    f = seed.entity("Field", "Account.Name", v1, valid_to_seq=v2)  # field closes at v2
    seed.edge(f, obj, "BELONGS_TO", "STRUCTURAL", v1)  # edge spans (valid_to NULL)
    s1 = SemanticOrgModel(conn)

    at_v1 = s1.get_related_bulk(["BELONGS_TO"], "inbound", at_seq=v1)
    assert {r.entity.id for r in at_v1.get(obj, [])} == {f}
    # field out of window at v2 → the far entity drops, so the object has no group.
    at_v2 = s1.get_related_bulk(["BELONGS_TO"], "inbound", at_seq=v2)
    assert at_v2.get(obj, []) == []


def test_get_related_bulk_empty_and_bad_direction(conn, seed):
    v1 = seed.version()
    s1 = SemanticOrgModel(conn)
    assert s1.get_related_bulk([], "inbound", at_seq=v1) == {}
    with pytest.raises(ValueError):
        s1.get_related_bulk(["BELONGS_TO"], "both", at_seq=v1)


def test_get_related_bulk_validates_at_seq(conn, seed):
    seed.version()
    s1 = SemanticOrgModel(conn)
    with pytest.raises(VersionNotFoundError):
        s1.get_related_bulk(["BELONGS_TO"], "inbound", at_seq=987654321)


def test_get_picklist_values_bulk_grouped_and_ordered(conn, seed):
    # Two value sets; bulk groups by picklist_value_set_entity_id, each group
    # ordered by sort_order — equal to the per-entity get_picklist_values.
    v1 = seed.version()
    pvs_a = seed.entity("PicklistValueSet", "VS:A", v1)
    pvs_b = seed.entity("PicklistValueSet", "VS:B", v1)
    _pv(seed, conn, pvs_a, "Banking", v1, sort_order=1)
    _pv(seed, conn, pvs_a, "Agriculture", v1, sort_order=0)
    _pv(seed, conn, pvs_b, "Solo", v1, sort_order=0)
    s1 = SemanticOrgModel(conn)

    grouped = s1.get_picklist_values_bulk(at_seq=v1)
    assert set(grouped) == {pvs_a, pvs_b}
    assert [r["value_api_name"] for r in grouped[pvs_a]] == ["Agriculture", "Banking"]
    assert [r["value_api_name"] for r in grouped[pvs_b]] == ["Solo"]
    # per-row shape matches the per-entity read (pvs_id key is stripped per row)
    assert grouped[pvs_a] == s1.get_picklist_values(pvs_a, at_seq=v1)


def test_get_picklist_values_bulk_as_of(conn, seed):
    v1 = seed.version()
    v2 = seed.version()
    pvs = seed.entity("PicklistValueSet", "VS:Status", v1)
    _pv(seed, conn, pvs, "Open", v1, sort_order=0)             # spans both
    _pv(seed, conn, pvs, "Legacy", v1, vto=v2, sort_order=1)   # closes at v2
    s1 = SemanticOrgModel(conn)

    assert {r["value_api_name"] for r in s1.get_picklist_values_bulk(at_seq=v1)[pvs]} \
        == {"Open", "Legacy"}
    assert {r["value_api_name"] for r in s1.get_picklist_values_bulk(at_seq=v2)[pvs]} \
        == {"Open"}
