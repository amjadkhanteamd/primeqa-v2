"""MetadataS1Reader — S1-backed metadata for the v1 read-switch (D-159, Step 3.2).

Translates the S1 semantic org model into the ``meta_*`` read-interface
(``MetaObject``/``MetaField``/``MetaValidationRule`` duck-types) that v1's
generation context (and, from 3.4, the validator) consume through
``MetadataAccessor``. Reads S1 **only** through its typed query interface
(``SemanticOrgModel.get_entities`` + the **bulk** reads ``get_entity_details_bulk`` /
``get_related_bulk`` / ``get_picklist_values_bulk`` — D-189; no S1-local SQL).

**Eager-hydrated, in ~6 bulk queries (D-189).** :func:`build_metadata_s1_reader`
opens one tenant connection, pins ``at_seq = current_version_seq()``, and loads the
whole org's metadata into frozen dataclasses — a pure in-memory snapshot for the
reader's lifetime (sidesteps the connection closing across the generation call). The
hydration reads each shape in ONE query (objects, object-details, field-details,
field→object edges, picklist values, VRs, VR→object edges) rather than a per-entity
round-trip per object / field / VR / picklist set — the prior per-entity loop issued
tens of thousands of queries on a real org. **Best-effort:** a tenant with no S1
versions (``VersionNotFoundError``) or any read error → ``None``, so the accessor
falls back to ``meta_*`` (the parallel-run safety).

The per-field CRUD flags (``is_createable``/``is_updateable``) read ``field_details``
with a default of ``True`` — **absent in 3.2** (→ True, the descriptive-generation
approximation; the validator does NOT read S1 until those flags are real) and
**real once the 3.3 columns land** (``get_entity_details`` does ``SELECT *``, so it
picks them up). The picklist values (the validator's ``picklist_value_not_allowed``
WARNING) are populated in 3.4 (D-161) via the
``field_details.picklist_value_set_entity_id`` → ``get_picklist_values`` 2-hop — so
the validator reads S1 at true parity (every rule the ``meta_*`` reader served).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

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
    label: Optional[str] = None   # SF object label (Entity.display_name); picker display


@dataclass(frozen=True)
class _S1Field:
    api_name: str
    field_type: Optional[str]
    is_required: bool
    is_custom: bool
    is_createable: bool
    is_updateable: bool
    meta_object_id: Any        # the S1 Object entity UUID (== _S1Object.id)
    reference_to: Optional[str] = None       # (deferred) SOQL relationship checks
    picklist_values: Any = ()                # D-161: list[str] value api-names (()=none)
    label: Optional[str] = None              # SF field label (Entity.display_name); picker display


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


def build_metadata_s1_reader(tenant_id, *, at_seq=None, environment_id=None):
    """Eager-hydrate the current S1 metadata into a :class:`MetadataS1Reader`.
    Best-effort: returns ``None`` on an empty S1 (``VersionNotFoundError``) or any
    error — the accessor then falls back to ``meta_*``.

    per-org Slice 3b: an optional ``environment_id`` scopes the hydration to that
    environment's org (resolved via ``get_connected_org_for_environment``). When it
    is ``None`` (or no connected_org is provisioned), the reader stays org-blind —
    the safe default for these advisory whole-tenant metadata pickers (no fail-loud
    here, unlike the S4/S6 execution path)."""
    try:
        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.semantic.query import SemanticOrgModel
        from primeqa.sync.credentials import get_connected_org_for_environment
        with get_tenant_connection(tenant_id) as conn:
            org_id = (get_connected_org_for_environment(conn, environment_id)
                      if environment_id is not None else None)
            model = SemanticOrgModel(conn, connected_org_id=org_id)
            seq = at_seq if at_seq is not None else model.current_version_seq()
            return hydrate_metadata_s1_reader(model, seq)
    except Exception as exc:                          # empty S1 / read error
        log.warning("S1 metadata reader unavailable for tenant %s: %s "
                    "(falling back to meta_*)", tenant_id, exc)
        return None


def hydrate_metadata_s1_reader(model, seq) -> MetadataS1Reader:
    """Pure: translate S1 (`SemanticOrgModel` at version `seq`) → the snapshot.
    Directly testable on a tenant-scoped connection (no own connection).

    D-189: the whole org is read in ~6 **bulk** queries (one per shape) instead of a
    per-entity round-trip per object / field / VR / picklist set — the prior
    per-entity loop issued tens of thousands of queries on a real org and was killed
    mid-hydration. The construction logic below is **unchanged**; only the data
    source moved from per-entity calls to pre-fetched bulk maps. Byte-for-byte
    identical output, pinned by the golden-equivalence test.
    """
    object_entities = sorted(model.get_entities("Object", at_seq=seq),
                             key=lambda x: x.sf_api_name or "")
    object_details_by_id = model.get_entity_details_bulk("Object", at_seq=seq)
    field_details_by_id = model.get_entity_details_bulk("Field", at_seq=seq)
    fields_by_obj_edges = model.get_related_bulk([_BELONGS_TO], "inbound", at_seq=seq)
    picklist_by_set = model.get_picklist_values_bulk(at_seq=seq)
    vr_entities = model.get_entities("ValidationRule", at_seq=seq)
    vr_obj_edges = model.get_related_bulk([_APPLIES_TO], "outbound", at_seq=seq)

    objects = []
    fields_by_obj = {}
    for e in object_entities:
        od = object_details_by_id.get(e.id, {})   # absent detail row → {} (was: ...or {})
        objects.append(_S1Object(
            id=e.id, api_name=e.sf_api_name, label=e.display_name,
            is_createable=bool(od.get("is_createable", True)),
            is_custom=bool(od.get("is_custom", False))))
        obj_api = e.sf_api_name or ""     # stripped from qualified field names below
        flds = []
        # Field loop stays NESTED in the object loop so obj_api is the field's true
        # PARENT (inbound BELONGS_TO groups by near = the Object id). Do not flatten.
        for r in fields_by_obj_edges.get(e.id, []):
            fe = r.entity
            if fe.entity_type != "Field":
                continue
            fd = field_details_by_id.get(fe.id, {})
            attrs = fe.attributes or {}
            # S1 stores a Field's sf_api_name object-qualified ("Account.Name",
            # per sync materialize._extract_external_id's f"{parent}.{name}"); v1
            # meta_* + every test step use the bare name ("Name"). Strip the parent
            # prefix — the exact inverse — so the validator's obj_fields[fname]
            # lookup matches bare-name steps and the generation context lists bare
            # field names (D-161.1).
            fe_api = fe.sf_api_name or ""
            bare = (fe_api[len(obj_api) + 1:]
                    if obj_api and fe_api.startswith(obj_api + ".") else fe_api)
            # Picklist values via the 2-hop (D-161): field_details carries the
            # picklist_value_set_entity_id FK (NULL for non-picklist fields);
            # the bulk map enumerates each set at `seq`. Emit a **list** of value
            # api-names — the validator's _picklist_values needs isinstance(pv, list)
            # (a tuple is skipped); empty stays () so a field with no captured values
            # is skipped (no false-positive). The bulk map keys by real UUID, while
            # the FK value may arrive as UUID or str — coerce before the lookup.
            pvs_id = fd.get("picklist_value_set_entity_id")
            picklist_values = ()
            if pvs_id:
                picklist_values = [
                    pv["value_api_name"]
                    for pv in picklist_by_set.get(UUID(str(pvs_id)), [])
                    if pv.get("value_api_name")
                ]
            flds.append(_S1Field(
                api_name=bare,
                label=fe.display_name,
                field_type=fd.get("field_type"),
                is_required=bool(attrs.get("is_required", False)),
                is_custom=bool(fd.get("is_custom", False)),
                # real since 3.3 added the field_details columns (SELECT * picks them
                # up; absent → True, SF's permissive default).
                is_createable=bool(fd.get("is_createable", True)),
                is_updateable=bool(fd.get("is_updateable", True)),
                picklist_values=picklist_values,
                meta_object_id=e.id))
        fields_by_obj[e.id] = tuple(sorted(flds, key=lambda f: f.api_name or ""))

    vrs = []
    for vr in vr_entities:
        obj_ref = None
        for r in vr_obj_edges.get(vr.id, []):
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
