"""Shared in-memory fixture world for resolution unit tests (DB-free).

Mirrors the pls_fb_benchmark_v1 naming trap: a standard ``Order`` object and
the org's real ``PLS_FB_Order__c`` (prefix-namespaced fields), so the
Order__c / Order / PLS_FB_Order__c cases are all exercisable.
"""
from __future__ import annotations

from uuid import uuid4

from primeqa.resolution.symbols import FieldSymbol, ObjectSymbol, SymbolTable


def fld(bare: str, obj: str, label: str, ftype: str = "string",
        values: tuple = ()) -> FieldSymbol:
    return FieldSymbol(entity_id=uuid4(), api_name=bare,
                       qualified_api_name=f"{obj}.{bare}", label=label,
                       field_type=ftype, picklist_values=values)


def standard_order() -> ObjectSymbol:
    return ObjectSymbol(
        entity_id=uuid4(), api_name="Order", label="Order", is_custom=False,
        fields=(
            fld("Status", "Order", "Status", "picklist",
                (("Draft", "Draft"), ("Activated", "Activated"))),
            fld("TotalAmount", "Order", "Order Amount", "currency"),
        ))


def fb_order() -> ObjectSymbol:
    return ObjectSymbol(
        entity_id=uuid4(), api_name="PLS_FB_Order__c", label="PLS FB Order",
        is_custom=True,
        fields=(
            fld("PLS_FB_Priority__c", "PLS_FB_Order__c", "Priority",
                "picklist", (("Low", "Low"), ("High", "High"))),
            fld("PLS_FB_Status__c", "PLS_FB_Order__c", "Status", "picklist",
                (("Draft", "Draft"), ("Submitted", "Submitted"))),
            fld("PLS_FB_Amount__c", "PLS_FB_Order__c", "Amount", "currency"),
            fld("PLS_FB_Tier__c", "PLS_FB_Order__c", "Tier", "picklist",
                (("Bronze", "Bronze"), ("Gold", "Gold"))),
        ))


def work_order() -> ObjectSymbol:
    return ObjectSymbol(
        entity_id=uuid4(), api_name="WorkOrder", label="Work Order",
        is_custom=False,
        fields=(fld("Status", "WorkOrder", "Status", "picklist",
                    (("New", "New"), ("Closed", "Closed"))),))


def table(*objects: ObjectSymbol, at_seq: int = 7) -> SymbolTable:
    objs = list(objects) or [standard_order(), fb_order(), work_order()]
    return SymbolTable(objs, at_seq=at_seq)
