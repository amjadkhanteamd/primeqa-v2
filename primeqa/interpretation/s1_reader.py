"""Production `S1VrReader` (D-111.1 slice 2b) — reads a subject Object's
validation rules through S1's query interface.

The inter-substrate read-through pattern (S6-3): S6 reads S1's VR metadata
**through S1's typed query interface** (`SemanticOrgModel`), never a raw S6-local
query over S1's tables. It owns no SQL of its own — S1 owns the reads
(`get_entities` / `get_related` / `get_entity_details`). It composes them:

  1. `get_entities("Object", filters={sf_api_name})` → the subject Object;
  2. `get_related(object, ["APPLIES_TO"], "inbound")` → the VRs that apply to it
     (the VR is the edge *source*, the Object the target, so inbound), each
     carrying `attributes` → ``formula_text`` + ``error_message``;
  3. `get_entity_details(vr)` → the detail row → ``is_active`` (the one `VrMeta`
     field the entities/attributes read does not surface — the D-111.1 reason
     S1's read API gained `get_entity_details`).

Attribution reads the **current** S1 version by default (so a *now*-inactive or
*now*-edited VR is detectable — the point of the drift / inactive causes).
"""
from __future__ import annotations

from typing import Optional

from primeqa.interpretation.attribution import FieldMeta, FlowMeta, VrMeta
from primeqa.semantic.entity_attributes import vr_error_message, vr_formula_text
from primeqa.semantic.query import SemanticOrgModel

_APPLIES_TO = "APPLIES_TO"
_TRIGGERS_ON = "TRIGGERS_ON"     # Flow -> Object (BEHAVIOR); inbound to the object
_BELONGS_TO = "BELONGS_TO"       # Field -> Object (STRUCTURAL); inbound to the object


class S1ValidationRuleReader:
    """An :class:`~primeqa.interpretation.attribution.S1VrReader` backed by S1's
    ``SemanticOrgModel``. Construct over an already-tenant-scoped connection
    (``get_tenant_connection``)."""

    def __init__(self, model: SemanticOrgModel, *, at_seq: Optional[int] = None):
        self._model = model
        self._at_seq = at_seq          # pin; default = current version

    def vrs_for_object(self, subject_external_id: str) -> tuple[VrMeta, ...]:
        seq = (self._at_seq if self._at_seq is not None
               else self._model.current_version_seq())

        objs = self._model.get_entities(
            "Object", at_seq=seq, filters={"sf_api_name": subject_external_id})
        if not objs:
            return ()
        obj = objs[0]

        related = self._model.get_related(
            obj.id, edge_types=[_APPLIES_TO], direction="inbound", at_seq=seq)

        out: list[VrMeta] = []
        for r in related:
            vr = r.entity
            if vr.entity_type != "ValidationRule":
                continue
            details = self._model.get_entity_details(vr.id, at_seq=seq) or {}
            attrs = vr.attributes or {}
            # Shape-tolerant (D-203.1): rows from both sync generations coexist
            # (designed formula_text vs raw Metadata.errorConditionFormula).
            out.append(VrMeta(
                name=vr.sf_api_name or vr.display_name or str(vr.id),
                is_active=bool(details.get("is_active", True)),
                formula_text=vr_formula_text(attrs),
                error_message=vr_error_message(attrs),
            ))
        return tuple(out)

    def flows_for_object(self, subject_external_id: str) -> tuple[FlowMeta, ...]:
        """The Flows that ``TRIGGERS_ON`` ``subject_external_id`` + their active
        state (D-229) — the grounding for the automation/state verticals. Same
        compose-S1's-reads shape as ``vrs_for_object`` (Flow is the edge source,
        the Object the target → inbound), with ``is_active`` off
        ``flow_details``."""
        seq = (self._at_seq if self._at_seq is not None
               else self._model.current_version_seq())
        obj = self._object(subject_external_id, seq)
        if obj is None:
            return ()
        related = self._model.get_related(
            obj.id, edge_types=[_TRIGGERS_ON], direction="inbound", at_seq=seq)
        out: list[FlowMeta] = []
        for r in related:
            flow = r.entity
            if flow.entity_type != "Flow":
                continue
            details = self._model.get_entity_details(flow.id, at_seq=seq) or {}
            out.append(FlowMeta(
                name=flow.sf_api_name or flow.display_name or str(flow.id),
                is_active=bool(details.get("is_active", True)),
                trigger_type=details.get("trigger_type"),  # D-241: BeforeSave/AfterSave/None
            ))
        return tuple(out)

    def field_meta(
        self, object_external_id: str, field_external_id: str,
    ) -> Optional[FieldMeta]:
        """The createability of one field on ``object_external_id`` (D-229) — the
        `value_not_persisted` signal. Evidence names fields BARE (``Amount__c``);
        S1 stores them object-qualified (``Invoice__c.Amount__c``), so the match
        is tolerant of both shapes. Returns ``None`` when the field is unknown
        (the caller passes through rather than guessing)."""
        seq = (self._at_seq if self._at_seq is not None
               else self._model.current_version_seq())
        obj = self._object(object_external_id, seq)
        if obj is None:
            return None
        related = self._model.get_related(
            obj.id, edge_types=[_BELONGS_TO], direction="inbound", at_seq=seq)
        bare = field_external_id.split(".")[-1]
        for r in related:
            fld = r.entity
            if fld.entity_type != "Field":
                continue
            api = fld.sf_api_name or ""
            if api == field_external_id or api.split(".")[-1] == bare:
                details = self._model.get_entity_details(fld.id, at_seq=seq) or {}
                return FieldMeta(
                    name=api or fld.display_name or str(fld.id),
                    is_createable=bool(details.get("is_createable", True)),
                )
        return None

    def _object(self, external_id: str, seq: int):
        objs = self._model.get_entities(
            "Object", at_seq=seq, filters={"sf_api_name": external_id})
        return objs[0] if objs else None
