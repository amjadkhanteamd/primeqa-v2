"""F2 (D-377) — structural value evidence on field offers.

A candidate whose ACTIVE picklist carries the intent's staged value floats to
the top of the admitted offer; ordering is byte-identical when no value is
staged / nothing carries it / S1 fails. Re-rank only — admission is untouched
and nothing is ever silently substituted."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation import recovery as rec
from primeqa.generation.governance_core import (
    EDGE_BELONGS, _field_recovery_tail, _value_support_rerank)

SUBJ = "PLS_FB_Order__c"


def _row(bare, label, ent_id=None):
    return SimpleNamespace(
        edge_type=EDGE_BELONGS,
        entity=SimpleNamespace(id=ent_id or uuid4(), entity_type="Field",
                               sf_api_name=f"{SUBJ}.{bare}",
                               display_name=label))


class S1Stub:
    """Field-details + picklist reads for exactly one picklist field."""

    def __init__(self, picklist_field_id, values, active=True):
        self._fid = picklist_field_id
        self._pvs = uuid4()
        self._values = values
        self._active = active
        self.reads = 0

    def get_entity_details(self, ent_id, at_seq):
        self.reads += 1
        if ent_id == self._fid:
            return {"picklist_value_set_entity_id": self._pvs}
        return {}

    def get_picklist_values(self, pvs_id, at_seq):
        return [{"value_api_name": v, "value_label": v,
                 "is_active": self._active} for v in self._values]


def _world():
    tier_id = uuid4()
    nbhd = [
        _row("PLS_FB_Tier__c", "Tier", tier_id),          # picklist w/ Gold
        _row("PLS_FB_Order_Total__c", "Order Total"),     # currency
        _row("PLS_FB_Priority__c", "Priority"),
    ]
    return nbhd, S1Stub(tier_id, ("Bronze", "Gold"))


def _cands(pool_rows, proposed):
    pool = [(r.entity.sf_api_name, r.entity.display_name) for r in pool_rows]
    return rec.rank_candidates(proposed, pool)


def test_value_supported_candidate_floats_to_top():
    nbhd, s1 = _world()
    cands = _cands(nbhd, "Commercial_Tier__c")
    assert cands                                     # lexically admitted
    ordered, supported = _value_support_rerank(s1, nbhd, cands, "Gold", 7)
    assert ordered[0].sf_api_name == f"{SUBJ}.PLS_FB_Tier__c"
    assert supported == {f"{SUBJ}.PLS_FB_Tier__c"}
    # label-ci match works too
    _, supported_ci = _value_support_rerank(s1, nbhd, cands, " gold ", 7)
    assert supported_ci == {f"{SUBJ}.PLS_FB_Tier__c"}


def test_no_value_or_no_support_is_byte_identical():
    nbhd, s1 = _world()
    cands = _cands(nbhd, "Commercial_Tier__c")
    assert _value_support_rerank(s1, nbhd, cands, None, 7) == (cands, frozenset())
    assert _value_support_rerank(s1, nbhd, cands, "Platinum", 7) == (
        cands, frozenset())
    assert _value_support_rerank(None, nbhd, cands, "Gold", 7) == (
        cands, frozenset())
    assert _value_support_rerank(s1, nbhd, cands, "Gold", None) == (
        cands, frozenset())


def test_inactive_values_do_not_support():
    nbhd, _ = _world()
    tier_id = nbhd[0].entity.id
    s1 = S1Stub(tier_id, ("Gold",), active=False)
    cands = _cands(nbhd, "Commercial_Tier__c")
    assert _value_support_rerank(s1, nbhd, cands, "Gold", 7) == (
        cands, frozenset())


def test_s1_failure_never_breaks_the_offer():
    nbhd, _ = _world()

    class Boom:
        def get_entity_details(self, *a, **k):
            raise RuntimeError("s1 down")

    cands = _cands(nbhd, "Commercial_Tier__c")
    assert _value_support_rerank(Boom(), nbhd, cands, "Gold", 7) == (
        cands, frozenset())


def test_field_recovery_tail_marks_value_support():
    nbhd, s1 = _world()
    tail, offer = _field_recovery_tail(
        ["Commercial_Tier__c"], nbhd, s1=s1, staged_value="Gold", at_seq=7)
    assert offer is not None
    top = offer["candidates"][0]
    assert top["sf_api_name"] == f"{SUBJ}.PLS_FB_Tier__c"
    assert top["value_support"] is True
    assert all("value_support" not in c for c in offer["candidates"][1:])
    # the model-facing tail lists the supported candidate first
    assert tail.index("PLS_FB_Tier__c") < tail.index("Order_Total__c") \
        if "Order_Total__c" in tail else True


def test_field_recovery_tail_without_value_is_pre_f2_identical():
    nbhd, s1 = _world()
    plain = _field_recovery_tail(["Commercial_Tier__c"], nbhd)
    with_s1_no_value = _field_recovery_tail(
        ["Commercial_Tier__c"], nbhd, s1=s1, staged_value=None, at_seq=7)
    assert plain == with_s1_no_value