"""SymbolTable — an in-memory, version-pinned projection of one org's objects,
fields, and picklist values, hydrated in bulk from S1.

Mirrors the ``metadata_bridge.s1_reader`` eager-hydration pattern (D-189: one
bulk query per shape, never per-entity round-trips; ~5,900 entities/org make
in-memory hydration cheap). Read-only; no caller may mutate a table.

Field naming: S1 stores Field ``sf_api_name`` object-qualified
("Obj__c.Field__c"); symbols carry both the qualified form and the bare form
(the prefix-strip inverse, same as the bridge).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldSymbol:
    entity_id: UUID
    api_name: str                      # bare ("PLS_FB_Priority__c")
    qualified_api_name: str            # "PLS_FB_Order__c.PLS_FB_Priority__c"
    label: Optional[str] = None
    field_type: Optional[str] = None
    # (value_api_name, value_label) pairs; () for non-picklist fields
    picklist_values: tuple[tuple[str, Optional[str]], ...] = field(default=())
    references_object: Optional[str] = None   # lookup/MD target object api name


@dataclass(frozen=True)
class ObjectSymbol:
    entity_id: UUID
    api_name: str
    label: Optional[str] = None
    is_custom: bool = False
    fields: tuple[FieldSymbol, ...] = field(default=())


class SymbolTable:
    """Objects keyed for exact lookup; iteration order is api-name-sorted
    (deterministic)."""

    def __init__(self, objects: list[ObjectSymbol], *, at_seq: int,
                 connected_org_id: Optional[UUID] = None):
        self._objects = tuple(sorted(objects, key=lambda o: o.api_name or ""))
        self._by_api = {o.api_name.lower(): o for o in self._objects if o.api_name}
        self.at_seq = at_seq
        self.connected_org_id = connected_org_id

    @property
    def objects(self) -> tuple[ObjectSymbol, ...]:
        return self._objects

    def by_api(self, api_name: Optional[str]) -> Optional[ObjectSymbol]:
        if not api_name:
            return None
        return self._by_api.get(api_name.lower())


def hydrate_symbol_table(model, at_seq: int) -> SymbolTable:
    """Pure translation: S1 (``SemanticOrgModel`` at ``at_seq``) → SymbolTable,
    in 5 bulk queries. Relationships come from ``field_details.
    references_object_entity_id`` (the containment-FK source of truth) rather
    than the derived HAS_RELATIONSHIP_TO edge."""
    object_entities = sorted(model.get_entities("Object", at_seq=at_seq),
                             key=lambda e: e.sf_api_name or "")
    object_details = model.get_entity_details_bulk("Object", at_seq=at_seq)
    field_details = model.get_entity_details_bulk("Field", at_seq=at_seq)
    fields_by_obj = model.get_related_bulk(["BELONGS_TO"], "inbound", at_seq=at_seq)
    picklists = model.get_picklist_values_bulk(at_seq=at_seq)

    obj_api_by_id = {e.id: (e.sf_api_name or "") for e in object_entities}

    objects: list[ObjectSymbol] = []
    for e in object_entities:
        od = object_details.get(e.id, {})
        obj_api = e.sf_api_name or ""
        flds: list[FieldSymbol] = []
        for r in fields_by_obj.get(e.id, []):
            fe = r.entity
            if fe.entity_type != "Field":
                continue
            fd = field_details.get(fe.id, {})
            fe_api = fe.sf_api_name or ""
            bare = (fe_api[len(obj_api) + 1:]
                    if obj_api and fe_api.startswith(obj_api + ".") else fe_api)
            values: tuple[tuple[str, Optional[str]], ...] = ()
            pvs_id = fd.get("picklist_value_set_entity_id")
            if pvs_id:
                values = tuple(
                    (pv["value_api_name"], pv.get("value_label"))
                    for pv in picklists.get(UUID(str(pvs_id)), [])
                    if pv.get("value_api_name"))
            ref_obj_id = fd.get("references_object_entity_id")
            ref_api = None
            if ref_obj_id:
                try:
                    ref_api = obj_api_by_id.get(UUID(str(ref_obj_id)))
                except (ValueError, TypeError):
                    ref_api = None
            flds.append(FieldSymbol(
                entity_id=fe.id, api_name=bare, qualified_api_name=fe_api,
                label=fe.display_name, field_type=fd.get("field_type"),
                picklist_values=values, references_object=ref_api))
        objects.append(ObjectSymbol(
            entity_id=e.id, api_name=obj_api, label=e.display_name,
            is_custom=bool(od.get("is_custom", False)),
            fields=tuple(sorted(flds, key=lambda f: f.api_name or ""))))
    return SymbolTable(objects, at_seq=at_seq,
                       connected_org_id=getattr(model, "connected_org_id", None))
