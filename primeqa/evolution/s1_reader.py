"""Production tri-port S1 reader (D-143) — backs the three grounding-validity
ports over one tenant-scoped ``SemanticOrgModel``.

The inter-substrate read-through pattern (mirrors ``interpretation/s1_reader.py``,
the S6 precedent): S8 reads S1 **through S1's typed query interface**, never a raw
S8-local query. One reader implements all three ports the composition (D-141)
takes — :class:`~primeqa.evolution.claim_grounding.SubjectResolver` (``resolves``),
:class:`~primeqa.evolution.recipe_grounding.VrReader` (``vrs_for_object``), and
:class:`~primeqa.evolution.field_value_grounding.PicklistReader`
(``active_values``) — so the recompute passes ``subjects=vrs=picklists=reader``.

Pins ``at_seq`` once (default ``current_version_seq()``) and threads it through
every read, so a whole recompute pass reads S1 at one consistent version. S8 owns
its VR shape (:class:`_GroundingVr`) — no ``VrMeta`` import (parallel-siblings,
D-112).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from primeqa.semantic.query import SemanticOrgModel

_APPLIES_TO = "APPLIES_TO"


@dataclass(frozen=True)
class _GroundingVr:
    """S8-local VR shape for the recipe-grounding ``VrReader`` port (duck-typed:
    ``is_active`` + ``formula_text``). S8 owns it — S6's ``VrMeta`` satisfies the
    shape but S8 does not import it (parallel-siblings, D-112)."""

    is_active: bool
    formula_text: Optional[str]


class S8S1Reader:
    """The production tri-port reader. Construct over an already-tenant-scoped
    connection's ``SemanticOrgModel`` (``get_tenant_connection`` → the recompute
    wrapper). Implements ``resolves`` / ``vrs_for_object`` / ``active_values``."""

    def __init__(self, model: SemanticOrgModel, *, at_seq: Optional[int] = None):
        self._model = model
        self._at_seq = at_seq          # pin; default = current version (lazy).

    def _seq(self) -> int:
        if self._at_seq is None:
            self._at_seq = self._model.current_version_seq()
        return self._at_seq

    # -- SubjectResolver (claim-grounding) -----------------------------------
    def resolves(self, entity_type: str, external_id: str) -> bool:
        """Does ``(entity_type, external_id)`` resolve in the current org? — a
        non-empty ``get_entities`` by ``sf_api_name`` at the pinned seq."""
        return bool(self._model.get_entities(
            entity_type, at_seq=self._seq(),
            filters={"sf_api_name": external_id}))

    # -- VrReader (recipe-grounding) -----------------------------------------
    def vrs_for_object(self, subject_external_id: str) -> tuple[_GroundingVr, ...]:
        """The validation rules applying to an Object (the ``APPLIES_TO`` path) —
        the exact S1ValidationRuleReader composition, returning S8-local
        ``_GroundingVr`` (``is_active`` + ``formula_text``)."""
        seq = self._seq()
        objs = self._model.get_entities(
            "Object", at_seq=seq, filters={"sf_api_name": subject_external_id})
        if not objs:
            return ()
        related = self._model.get_related(
            objs[0].id, edge_types=[_APPLIES_TO], direction="inbound", at_seq=seq)
        out: list[_GroundingVr] = []
        for r in related:
            vr = r.entity
            if vr.entity_type != "ValidationRule":
                continue
            details = self._model.get_entity_details(vr.id, at_seq=seq) or {}
            attrs = vr.attributes or {}
            out.append(_GroundingVr(
                is_active=bool(details.get("is_active", True)),
                formula_text=attrs.get("formula_text")))
        return tuple(out)

    # -- PicklistReader (field-value-validity) -------------------------------
    def active_values(
        self, object_external_id: str, field_api: str,
    ) -> Optional[frozenset]:
        """The field's current active picklist ``value_api_name``s, or **None**
        when the field is not a constrained picklist (the leg then skips it).
        Field → ``field_details.picklist_value_set_entity_id`` → the active value
        set. Returns None when the Field is gone (the field-gone case is
        claim-grounding's, not this leg's)."""
        seq = self._seq()
        external = (field_api if "." in field_api
                    else f"{object_external_id}.{field_api}")
        fields = self._model.get_entities(
            "Field", at_seq=seq, filters={"sf_api_name": external})
        if not fields:
            return None
        details = self._model.get_entity_details(fields[0].id, at_seq=seq) or {}
        pvs_id = details.get("picklist_value_set_entity_id")
        if not pvs_id:
            return None                       # not a constrained picklist — skip
        values = self._model.get_picklist_values(pvs_id, at_seq=seq)
        return frozenset(
            v["value_api_name"] for v in values if v.get("is_active"))
