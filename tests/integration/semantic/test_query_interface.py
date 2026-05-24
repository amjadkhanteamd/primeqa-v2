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
