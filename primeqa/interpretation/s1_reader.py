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

from primeqa.interpretation.attribution import VrMeta
from primeqa.semantic.query import SemanticOrgModel

_APPLIES_TO = "APPLIES_TO"


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
            out.append(VrMeta(
                name=vr.sf_api_name or vr.display_name or str(vr.id),
                is_active=bool(details.get("is_active", True)),
                formula_text=attrs.get("formula_text"),
                error_message=attrs.get("error_message"),
            ))
        return tuple(out)
