"""MetadataS1Reader — S1-backed metadata for the v1 read-switch (D-159, Step 3.2).

Translates the S1 semantic org model into the ``meta_*`` read-interface
(``MetaObject``/``MetaField``/``MetaValidationRule`` duck-types) that v1's
generation context (and, from 3.4, the validator) consume through
``MetadataAccessor``. Reads S1 **only** through its typed query interface
(``SemanticOrgModel.get_entities`` / ``get_related`` / ``get_entity_details`` —
the S6/S8 ``s1_reader`` pattern; no S1-local SQL).

**Eager-hydrated.** :func:`build_metadata_s1_reader` opens one tenant connection,
pins ``at_seq = current_version_seq()``, and loads the whole org's metadata into
frozen dataclasses — a pure in-memory snapshot for the reader's lifetime
(sidesteps the connection closing across the generation call). **Best-effort:** a
tenant with no S1 versions (``VersionNotFoundError``) or any read error → ``None``,
so the accessor falls back to ``meta_*`` (the parallel-run safety).

The per-field CRUD flags (``is_createable``/``is_updateable``) read ``field_details``
with a default of ``True`` — **absent in 3.2** (→ True, the descriptive-generation
approximation; the validator does NOT read S1 until those flags are real) and
**real once the 3.3 columns land** (``get_entity_details`` does ``SELECT *``, so it
picks them up) — so the reader is unchanged between 3.2 and 3.4.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)

_BELONGS_TO = "BELONGS_TO"     # Field → Object (structural); fields = inbound to obj
_APPLIES_TO = "APPLIES_TO"     # ValidationRule → Object; object = outbound from VR


@dataclass(frozen=True)
class _S1ObjRef:
    api_name: str


@dataclass(frozen=True)
class _S1Object:
    id: Any                    # the S1 Object entity UUID
    api_name: str
    is_createable: bool
    is_custom: bool


@dataclass(frozen=True)
class _S1Field:
    api_name: str
    field_type: Optional[str]
    is_required: bool
    is_custom: bool
    is_createable: bool
    is_updateable: bool
    meta_object_id: Any        # the S1 Object entity UUID (== _S1Object.id)
    reference_to: Optional[str] = None       # 3.4 populates for lookups
    picklist_values: tuple = ()              # 3.4 populates (the validator's need)


@dataclass(frozen=True)
class _S1ValidationRule:
    rule_name: str
    error_message: Optional[str]
    meta_object: Optional[_S1ObjRef]


class MetadataS1Reader:
    """An in-memory snapshot of one org's S1 metadata exposing the ``meta_*``
    read-interface (duck-typed). ``meta_version_id`` args are ignored — the
    snapshot is pinned to the S1 version it was hydrated at (D-158)."""

    def __init__(self, objects, fields_by_obj, vrs):
        self._objects = tuple(objects)                # sorted by api_name
        self._fields_by_obj = dict(fields_by_obj)     # {obj_id: tuple[_S1Field]}
        self._all_fields = tuple(
            f for fs in fields_by_obj.values() for f in fs)
        self._vrs = tuple(vrs)                        # sorted by (object, rule)

    def get_objects(self, meta_version_id=None):
        return list(self._objects)

    def get_fields(self, meta_version_id=None, object_id=None):
        if object_id is None:
            return list(self._all_fields)
        return list(self._fields_by_obj.get(object_id, ()))

    def get_validation_rules(self, meta_version_id=None, object_id=None):
        return list(self._vrs)

    def get_object_by_api_name(self, meta_version_id, api_name):
        for o in self._objects:
            if o.api_name == api_name:
                return o
        return None


def build_metadata_s1_reader(tenant_id, *, at_seq=None):
    """Eager-hydrate the current S1 metadata into a :class:`MetadataS1Reader`.
    Best-effort: returns ``None`` on an empty S1 (``VersionNotFoundError``) or any
    error — the accessor then falls back to ``meta_*``."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.semantic.query import SemanticOrgModel
        with get_tenant_connection(tenant_id) as conn:
            model = SemanticOrgModel(conn)
            seq = at_seq if at_seq is not None else model.current_version_seq()
            return hydrate_metadata_s1_reader(model, seq)
    except Exception as exc:                          # empty S1 / read error
        log.warning("S1 metadata reader unavailable for tenant %s: %s "
                    "(falling back to meta_*)", tenant_id, exc)
        return None


def hydrate_metadata_s1_reader(model, seq) -> MetadataS1Reader:
    """Pure: translate S1 (`SemanticOrgModel` at version `seq`) → the snapshot.
    Directly testable on a tenant-scoped connection (no own connection)."""
    objects = []
    fields_by_obj = {}
    for e in sorted(model.get_entities("Object", at_seq=seq),
                    key=lambda x: x.sf_api_name or ""):
        od = model.get_entity_details(e.id, at_seq=seq) or {}
        objects.append(_S1Object(
            id=e.id, api_name=e.sf_api_name,
            is_createable=bool(od.get("is_createable", True)),
            is_custom=bool(od.get("is_custom", False))))
        flds = []
        for r in model.get_related(e.id, edge_types=[_BELONGS_TO],
                                   direction="inbound", at_seq=seq):
            fe = r.entity
            if fe.entity_type != "Field":
                continue
            fd = model.get_entity_details(fe.id, at_seq=seq) or {}
            attrs = fe.attributes or {}
            flds.append(_S1Field(
                api_name=fe.sf_api_name,
                field_type=fd.get("field_type"),
                is_required=bool(attrs.get("is_required", False)),
                is_custom=bool(fd.get("is_custom", False)),
                # absent in 3.2 (→ True, the descriptive approximation); real once
                # 3.3 adds the field_details columns (SELECT * picks them up).
                is_createable=bool(fd.get("is_createable", True)),
                is_updateable=bool(fd.get("is_updateable", True)),
                meta_object_id=e.id))
        fields_by_obj[e.id] = tuple(sorted(flds, key=lambda f: f.api_name or ""))

    vrs = []
    for vr in model.get_entities("ValidationRule", at_seq=seq):
        obj_ref = None
        for r in model.get_related(vr.id, edge_types=[_APPLIES_TO],
                                   direction="outbound", at_seq=seq):
            if r.entity.entity_type == "Object":
                obj_ref = _S1ObjRef(api_name=r.entity.sf_api_name)
                break
        attrs = vr.attributes or {}
        vrs.append(_S1ValidationRule(
            rule_name=vr.sf_api_name or vr.display_name or str(vr.id),
            error_message=attrs.get("error_message"),
            meta_object=obj_ref))
    vrs.sort(key=lambda v: ((v.meta_object.api_name if v.meta_object else ""),
                            v.rule_name or ""))

    return MetadataS1Reader(objects, fields_by_obj, vrs)
