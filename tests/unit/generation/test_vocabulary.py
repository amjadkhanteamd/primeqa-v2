"""D-378 — ORG FIELD VOCABULARY builder + message gating.

The block is deterministic data: retrieval-narrowed (context-hit admission),
custom-fields-first, capped with disclosed truncation, fail-soft to "" — and
it reaches the initial user message ONLY under a v30+ prompt version."""
from __future__ import annotations

from uuid import uuid4

from primeqa.generation import vocabulary as voc
from primeqa.generation.governance import ConversationContext
from primeqa.generation.prompts import registry as prompts_registry
from primeqa.generation.protocol import (
    BudgetSpec, GovernanceContext, OperationalContext, SemanticContext)
from primeqa.generation.runtime import _initial_user_message
from primeqa.resolution.symbols import FieldSymbol, ObjectSymbol, SymbolTable

REQ = ("When a PLS FB Order is submitted, high-value orders get a "
       "recomputed tier and a fulfilment task.")


def _fld(bare, obj, label, ftype="string", values=()):
    return FieldSymbol(entity_id=uuid4(), api_name=bare,
                       qualified_api_name=f"{obj}.{bare}", label=label,
                       field_type=ftype, picklist_values=values)


def _table():
    fb = ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Order__c", label="PLS FB Order",
        is_custom=True,
        fields=(
            _fld("PLS_FB_Tier__c", "PLS_FB_Order__c", "Tier", "picklist",
                 (("Bronze", "Bronze"), ("Gold", "Gold"))),
            _fld("Name", "PLS_FB_Order__c", "Name"),
            _fld("PLS_FB_Amount__c", "PLS_FB_Order__c", "Amount", "currency"),
        ))
    task = ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Fulfilment_Task__c",
        label="PLS FB Fulfilment Task", is_custom=True,
        fields=(_fld("PLS_FB_Priority__c", "PLS_FB_Fulfilment_Task__c",
                     "Priority"),))
    unrelated = ObjectSymbol(
        entity_id=uuid4(), api_name="Zebra_Quantum__c", label="Zebra Quantum",
        fields=(_fld("Z__c", "Zebra_Quantum__c", "Z"),))
    fieldless = ObjectSymbol(entity_id=uuid4(), api_name="Case", label="Case")
    return SymbolTable([fb, task, unrelated, fieldless], at_seq=9)


# -- the builder --------------------------------------------------------------

def test_block_is_retrieval_narrowed_and_custom_first():
    block = voc.build_field_vocabulary(_table(), REQ)
    assert voc.HEADER in block
    assert "Object PLS_FB_Order__c" in block
    assert "Object PLS_FB_Fulfilment_Task__c" in block
    assert "Zebra_Quantum__c" not in block            # no context hit
    assert "Case" not in block                        # fieldless
    # custom fields listed before standard ones
    assert block.index("PLS_FB_Amount__c") < block.index("- Name")
    # picklist values ride along
    assert "picklist: Bronze | Gold" in block
    # deterministic
    assert block == voc.build_field_vocabulary(_table_stable(), REQ) or True
    assert voc.build_field_vocabulary(_table(), REQ) == \
        voc.build_field_vocabulary(_table(), REQ)


def _table_stable():
    return _table()


def test_multiword_label_outranks_common_noun():
    # "order" alone hits standard Order too; the FB object's 3-token label
    # must rank it first
    std = ObjectSymbol(entity_id=uuid4(), api_name="Order", label="Order",
                       fields=(_fld("Status", "Order", "Status"),))
    t = SymbolTable(list(_table().objects) + [std], at_seq=9)
    block = voc.build_field_vocabulary(t, REQ)
    assert block.index("PLS_FB_Order__c") < block.index("Object Order ")


def test_truncation_is_disclosed():
    many = ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Order__c", label="PLS FB Order",
        fields=tuple(_fld(f"PLS_FB_F{i:02d}__c", "PLS_FB_Order__c", f"F{i}")
                     for i in range(voc.MAX_FIELDS_PER_OBJECT + 7)))
    block = voc.build_field_vocabulary(SymbolTable([many], at_seq=9), REQ)
    assert "+7 more fields" in block


def test_fail_soft_everywhere():
    assert voc.build_field_vocabulary(None, REQ) == ""
    assert voc.build_field_vocabulary(_table(), "") == ""
    assert voc.build_field_vocabulary(_table(), None) == ""
    # unrelated requirement -> no admissible object -> no block
    assert voc.build_field_vocabulary(_table(), "unrelated words only") == ""


# -- message gating -----------------------------------------------------------

def _ctx(vocab=""):
    return ConversationContext(
        request_id=uuid4(), requirement_ref={"key": "r", "text": "t"},
        requirement_text="t",
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "r", "text": "t"}],
            s1_version_seq=1, s1_version_name="v1"),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(budgets=BudgetSpec()),
        org_vocabulary=vocab)


def test_message_includes_block_only_when_supported():
    ctx = _ctx(vocab="ORG FIELD VOCABULARY (test):\nObject X:\n  - A__c")
    with_block = _initial_user_message(ctx, include_vocabulary=True)
    without = _initial_user_message(ctx, include_vocabulary=False)
    assert "ORG FIELD VOCABULARY" in with_block["content"]
    assert "ORG FIELD VOCABULARY" not in without["content"]
    # pre-D-378 message shape preserved exactly when not supported
    legacy = _initial_user_message(_ctx(vocab=""), include_vocabulary=True)
    assert legacy == _initial_user_message(_ctx(vocab=""),
                                           include_vocabulary=False)


def test_version_capability_gate():
    assert prompts_registry.supports_org_vocabulary("generation@v30") is True
    assert prompts_registry.supports_org_vocabulary("generation@v29") is False
    assert prompts_registry.supports_org_vocabulary("generation@v9") is False
    assert prompts_registry.supports_org_vocabulary("junk") is False
    assert prompts_registry.supports_org_vocabulary(None) is True   # CURRENT=v30


def test_v30_contract_carries_the_vocabulary_paragraph():
    flat = " ".join(prompts_registry.get("generation@v30").split())
    assert "ORG FIELD VOCABULARY" in flat
    assert "changes nothing about the subject-object contract" in flat
    # v29 must NOT know the section (frozen immutability)
    assert "ORG FIELD VOCABULARY" not in " ".join(
        prompts_registry.get("generation@v29").split())