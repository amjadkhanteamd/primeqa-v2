"""Field-ladder parity — the single rule implementation must be byte-identical
to the production governance ladder over an adversarial input matrix, and the
solver's ``resolve_field`` must agree with both."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from primeqa.generation.governance_core import (
    EDGE_BELONGS, _resolve_subject_field_name)
from primeqa.resolution.candidates import resolve_field
from primeqa.resolution.field_ladder import resolve_field_name
from primeqa.resolution.symbols import FieldSymbol, ObjectSymbol

# (qualified_api_name, display_name) inventory of one object
INVENTORY = [
    ("PLS_FB_Order__c.PLS_FB_Priority__c", "Priority"),
    ("PLS_FB_Order__c.PLS_FB_Status__c", "Status"),
    ("PLS_FB_Order__c.PLS_FB_Order_Total__c", "Order Total"),
    ("PLS_FB_Order__c.PLS_FB_Tier__c", "Tier"),
    ("PLS_FB_Order__c.A_Priority_Flag__c", "Priority Flag"),
    ("PLS_FB_Order__c.Name", None),                     # no label
]

AMBIG_INVENTORY = [
    ("X__c.A_Priority__c", "Priority A"),
    ("X__c.B_Priority__c", "Priority B"),
]

CASES = [
    # rule 1: exact qualified is case-sensitive, returns proposal verbatim
    "PLS_FB_Order__c.PLS_FB_Priority__c",
    "pls_fb_order__c.pls_fb_priority__c",   # wrong case -> falls to bare rule
    # rule 2: unique bare ci
    "PLS_FB_Priority__c", "pls_fb_priority__c",
    # rule 3: unique suffix
    "Priority__c", "Status__c", "Total__c", "Order_Total__c",
    # rule 4: label (with/without __c, underscores)
    "Tier", "tier", "Order_Total", "Order Total__c", "Priority",
    # camel-case label must NOT match (production strips only __c/underscores)
    "OrderTotal", "OrderTotal__c",
    # dotted multi-segment: bare = LAST segment
    "A.B.PLS_FB_Tier__c",
    # misses / junk
    "Total_Value__c", "Line_Item_Count__c", "", None, 42, "Name",
]


def _neighborhood(inventory):
    rows = []
    for q, label in inventory:
        rows.append(SimpleNamespace(
            edge_type=EDGE_BELONGS,
            entity=SimpleNamespace(id=uuid4(), entity_type="Field",
                                   sf_api_name=q, display_name=label)))
    # noise the production filter must ignore
    rows.append(SimpleNamespace(
        edge_type="APPLIES_TO",
        entity=SimpleNamespace(id=uuid4(), entity_type="ValidationRule",
                               sf_api_name="PLS_FB_Order__c.VR01",
                               display_name="VR01")))
    return rows


def _object(inventory):
    flds = []
    for q, label in inventory:
        bare = q.rsplit(".", 1)[-1]
        flds.append(FieldSymbol(entity_id=uuid4(), api_name=bare,
                                qualified_api_name=q, label=label))
    return ObjectSymbol(entity_id=uuid4(), api_name=inventory[0][0].split(".")[0],
                        label="Obj", fields=tuple(flds))


def test_engine_matches_production_over_the_matrix():
    for inventory in (INVENTORY, AMBIG_INVENTORY, []):
        nb = _neighborhood(inventory)
        for name in CASES:
            prod = _resolve_subject_field_name(nb, name)
            engine = resolve_field_name(inventory, name)
            assert engine == prod, (
                f"parity break on {name!r} over {len(inventory)}-field "
                f"inventory: production={prod!r} engine={engine!r}")


def test_solver_resolve_field_agrees_with_the_engine():
    obj = _object(INVENTORY)
    for name in CASES:
        engine = resolve_field_name(INVENTORY, name)
        got = resolve_field(obj, name if isinstance(name, str) else name)
        assert (got.qualified_api_name if got else None) == engine, (
            f"solver/engine break on {name!r}")


def test_ambiguity_is_never_guessed():
    assert resolve_field_name(AMBIG_INVENTORY, "Priority__c") is None
    assert resolve_field_name(AMBIG_INVENTORY, "Priority A") == "X__c.A_Priority__c"


def test_known_semantics_pins():
    # exact-qualified verbatim return, case-sensitive
    assert resolve_field_name(INVENTORY,
                              "PLS_FB_Order__c.PLS_FB_Priority__c") == \
        "PLS_FB_Order__c.PLS_FB_Priority__c"
    # wrong-case qualified still lands via the bare rule
    assert resolve_field_name(INVENTORY,
                              "pls_fb_order__c.pls_fb_priority__c") == \
        "PLS_FB_Order__c.PLS_FB_Priority__c"
    # camel-case label does NOT match (no camel-splitting in production)
    assert resolve_field_name(INVENTORY, "OrderTotal") is None
    # the live-observed residue stays unresolved (F2's offer territory)
    assert resolve_field_name(INVENTORY, "Total_Value__c") is None