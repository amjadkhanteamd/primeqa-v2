"""per-org Slice 5, Leg A (D-259) — model diff red-proofs (pure, no DB).

``diff_org_models`` partitions two orgs' active entity models into
only_in_a / only_in_b / differs / same, keyed on (entity_type, sf_api_name) with
``last_seed_hash`` as the content signal. The pure partitioner ``_diff_maps`` is
tested directly with synthetic hash-maps; ``diff_org_models`` is tested through a
fake connection (no DB). The end-to-end live counts (env-59 vs org#2) are proven
at push.
"""
from collections import namedtuple

import pytest

pytestmark = pytest.mark.unit

from primeqa.semantic.diff import _diff_maps, diff_org_models
from primeqa.semantic.entity_attributes import TIER_1_ENTITIES

# (entity_type, sf_api_name) -> last_seed_hash
_A = {
    ("Object", "Account"): "h-account",        # same in both
    ("Object", "Contact"): "h-contact-A",      # differs (different hash)
    ("Object", "OnlyA__c"): "h-onlya",         # only in A
    ("Field", "Account.Name"): "h-name",       # same in both
}
_B = {
    ("Object", "Account"): "h-account",        # same
    ("Object", "Contact"): "h-contact-B",      # differs
    ("Object", "OnlyB__c"): "h-onlyb",         # only in B
    ("Field", "Account.Name"): "h-name",       # same
}


def test_four_buckets():
    d = _diff_maps(_A, _B, "org-a", "org-b")
    t = d["totals"]
    assert t == {"only_in_a": 1, "only_in_b": 1, "differs": 1, "same": 2,
                 "count_a": 4, "count_b": 4}
    assert d["only_in_a"] == [{"entity_type": "Object", "sf_api_name": "OnlyA__c"}]
    assert d["only_in_b"] == [{"entity_type": "Object", "sf_api_name": "OnlyB__c"}]
    assert d["differs"] == [{"entity_type": "Object", "sf_api_name": "Contact",
                             "hash_a": "h-contact-A", "hash_b": "h-contact-B"}]
    assert d["org_a"] == "org-a" and d["org_b"] == "org-b"


def test_partition_identity():
    """only_a + same + differs == |A|; only_b + same + differs == |B|."""
    t = _diff_maps(_A, _B, "a", "b")["totals"]
    assert t["only_in_a"] + t["same"] + t["differs"] == t["count_a"] == len(_A)
    assert t["only_in_b"] + t["same"] + t["differs"] == t["count_b"] == len(_B)


def test_by_entity_type_seeded_and_counted():
    d = _diff_maps(_A, _B, "a", "b")
    bt = d["by_entity_type"]
    # every TIER_1 type present (stable UI shape) even with zero entities
    for et in TIER_1_ENTITIES.keys():
        assert et in bt
    assert bt["Object"] == {"only_in_a": 1, "only_in_b": 1, "differs": 1, "same": 1}
    assert bt["Field"] == {"only_in_a": 0, "only_in_b": 0, "differs": 0, "same": 1}
    # a type with no entities in either org stays all-zero
    assert bt["Flow"] == {"only_in_a": 0, "only_in_b": 0, "differs": 0, "same": 0}


def test_empty_org_a():
    d = _diff_maps({}, _B, "a", "b")
    t = d["totals"]
    assert t["only_in_b"] == len(_B) == t["count_b"]
    assert t["only_in_a"] == 0 and t["same"] == 0 and t["differs"] == 0
    assert t["count_a"] == 0


def test_empty_org_b():
    d = _diff_maps(_A, {}, "a", "b")
    t = d["totals"]
    assert t["only_in_a"] == len(_A) == t["count_a"]
    assert t["only_in_b"] == 0 and t["same"] == 0 and t["differs"] == 0


def test_identical_orgs_all_same_zero_differs():
    d = _diff_maps(_A, dict(_A), "a", "b")
    t = d["totals"]
    assert t["same"] == len(_A) and t["differs"] == 0
    assert t["only_in_a"] == 0 and t["only_in_b"] == 0


def test_both_empty():
    t = _diff_maps({}, {}, "a", "b")["totals"]
    assert t == {"only_in_a": 0, "only_in_b": 0, "differs": 0, "same": 0,
                 "count_a": 0, "count_b": 0}


# --- diff_org_models through a fake connection (read wiring, no DB) -----------

_FakeRow = namedtuple("_FakeRow", "entity_type sf_api_name last_seed_hash")


class _FakeConn:
    """Returns synthetic entity rows per ``:org`` param; records nothing else."""

    def __init__(self, by_org):
        self._by_org = by_org            # {org_str: [(type, name, hash), ...]}
        self._pending = []

    def execute(self, stmt, params=None):
        org = str((params or {})["org"])
        self._pending = [_FakeRow(*t) for t in self._by_org.get(org, [])]
        return self

    def fetchall(self):
        return self._pending


def test_diff_org_models_reads_each_org_then_partitions():
    conn = _FakeConn({
        "org-a": [("Object", "Account", "h1"), ("Object", "OnlyA__c", "h2")],
        "org-b": [("Object", "Account", "h1-CHANGED"), ("Object", "OnlyB__c", "h3")],
    })
    d = diff_org_models(conn, "org-a", "org-b")
    assert d["totals"] == {"only_in_a": 1, "only_in_b": 1, "differs": 1, "same": 0,
                           "count_a": 2, "count_b": 2}
    assert d["differs"][0]["sf_api_name"] == "Account"
    assert d["only_in_a"][0]["sf_api_name"] == "OnlyA__c"
    assert d["only_in_b"][0]["sf_api_name"] == "OnlyB__c"
