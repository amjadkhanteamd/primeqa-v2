"""Construct the operational world for a positive create (D-115 side B, k16).

S4 must put a *valid* record on the live org to verify a requirement's field
value persists — and the create call is the easy part. Before it can create, S4
fills the **operational padding**: the target object's required-on-create fields
that the recipe does *not* set, with type-valid filler. This is the k16 boundary
made real — S4 resolves operational validity (which fields must be present, with
what kind of value) but **never** the semantic value under test:

    writable padding set = {required-on-create fields that are *createable*}
                           − {Salesforce-managed system / audit fields}
                           − {owner / queue references (Salesforce defaults them)}
                           − {the semantic field-under-test (recipe-set)}

Required **lookup / master-detail parents** are no longer fenced off (F6.2): the
``construct_world`` entrypoint recursively *builds* the parent record and threads
its id into the child's lookup (except owner/queue references, which Salesforce
defaults). The semantic field is structurally excluded, so there is no code path
by which S4 writes the field it is verifying.

**"Required on create" for a REST create = ``is_nillable == False`` AND
``is_createable == True``** — the database-level NOT-NULL that the caller is also
*allowed* to set. The page-layout ``is_required`` (S1 attributes JSONB) is UI-only
and does not gate an API create. Crucially, ``is_nillable=False`` alone is *not*
enough: Salesforce-managed audit / system fields (``CreatedById``, ``CreatedDate``,
``SystemModstamp``, …) are NOT-NULL yet ``is_createable=False`` — setting them gets
the whole create rejected, so they are excluded by the createable check. Calculated
fields (formula / rollup) are not writable (excluded). Required **references**
(lookup / master-detail) are collected as ``required_refs`` for ``construct_world``
to build (F6.2). Field types S4 cannot synthesize make the recipe **unrunnable** —
collected into ``unfillable`` so the executor errors *honestly* rather than guessing
a value (scalars / simple-picklist are filled directly).

Reads S1 through the ``SemanticOrgModel`` port (the S6-3 read-through pattern):
``get_entities`` → ``get_related(BELONGS_TO)`` → ``get_entity_details``. No raw
SQL of its own; no v1 imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

_BELONGS_TO = "BELONGS_TO"

# Field types S4 fills with a type-valid scalar (the §3 fence: scalars +
# simple-picklist). ``field_type`` is Salesforce's ``DescribeField.type`` written
# verbatim by ``sync.detail_mappers``. Types absent here (reference / lookup,
# multipicklist, id, address, location, base64, anyType, time, encryptedstring,
# …) are unfillable in slice 1 — the executor errors rather than guessing.
_TEXTUAL = frozenset({"string", "text", "textarea", "combobox"})
_NUMERIC = frozenset({"int", "integer", "double", "currency", "percent"})

# Sentinel: the field exists + is required, but slice 1 cannot synthesize a value
# for its type. Distinct from "no value needed" (which simply skips the field).
_UNFILLABLE = object()

# F6.2 parent provisioning: construct_world builds required lookup/master-detail
# parents up to this many ancestor levels deep. Real required-parent chains are 1–2
# hops; deeper is almost always a cycle (caught by the visited-set guard) or a
# misconfigured org. A chain past this bound fails honestly (→ unfillable), never a
# partial write. One named constant — trivially tuned if a real deep chain appears.
MAX_PARENT_DEPTH = 3

# Reference targets that Salesforce **defaults on create** (the running user / a
# queue): a required ``OwnerId`` is ``is_createable=True`` + ``is_nillable=False``
# yet Salesforce fills it if omitted, so constructing a User/Group to own a test
# record is both wrong and impractical. We omit these references (let Salesforce
# default them) rather than build them. A genuinely-required *non*-defaulted User
# lookup would then be omitted too → an honest pre-create rejection, never a
# wrongly-built User. The principled fix (capture Salesforce's ``defaultedOnCreate``
# in S1) is deferred to its own slice; this is the pragmatic stand-in.
_DEFAULTED_REF_OBJECTS = frozenset({"User", "Group"})


@dataclass(frozen=True)
class PaddingResult:
    """The operational padding for one positive create.

    ``filler`` is the ``{field_api: value}`` of required **scalars** S4 merges into
    the create. ``required_refs`` names the required **lookup / master-detail**
    fields as ``(field_api, referenced_object_entity_id)`` — the parents
    ``construct_world`` must build + thread (F6.2). ``unfillable`` names required
    fields S4 cannot synthesize (a type the filler does not cover) — a non-empty
    ``unfillable`` makes the recipe unrunnable (the executor errors pre-create,
    never guesses)."""

    filler: dict[str, Any]
    unfillable: tuple[str, ...]
    required_refs: tuple[tuple[str, str], ...] = ()


def resolve_operational_padding(
    object_api: str, semantic_fields: set[str], *, s1, at_seq: int,
) -> PaddingResult:
    """Compute the operational padding for a create on ``object_api`` whose
    recipe-set fields are ``semantic_fields`` (k16 — never padded, so S4 cannot
    touch the value(s) under test).

    Reads requiredness + types from S1 via ``s1`` (a ``SemanticOrgModel``-shaped
    port: ``get_entities`` / ``get_related`` / ``get_entity_details``) at version
    ``at_seq``. Returns a :class:`PaddingResult`. Does **not** raise on a missing
    object — that surfaces as an empty filler (the create attempt against the
    live org then yields the real outcome, captured as evidence); only a genuine
    S1 read error propagates.
    """
    objs = s1.get_entities(
        "Object", at_seq=at_seq, filters={"sf_api_name": object_api})
    if not objs:
        # Object not in S1 — nothing to pad; the live create surfaces the real
        # outcome and S6 interprets the absence.
        return PaddingResult(filler={}, unfillable=())
    obj = objs[0]

    fields = s1.get_related(
        obj.id, edge_types=[_BELONGS_TO], direction="inbound", at_seq=at_seq)

    filler: dict[str, Any] = {}
    unfillable: list[str] = []
    required_refs: list[tuple[str, str]] = []
    for r in fields:
        fld = r.entity
        if fld.entity_type != "Field":
            continue
        api = fld.sf_api_name
        if not api or api in semantic_fields:     # k16 — never a recipe-set field
            continue
        details = s1.get_entity_details(fld.id, at_seq=at_seq) or {}
        # Required-on-create (REST) = NOT nillable. The page-layout is_required is
        # UI-only and does not gate an API create.
        if details.get("is_nillable", True):
            continue                              # optional / defaulted — skip
        if details.get("is_calculated", False):
            continue                              # formula / rollup — not writable
        if not details.get("is_createable", True):
            continue                              # Salesforce-managed on create (audit /
            #                                       system: CreatedById, CreatedDate,
            #                                       SystemModstamp, …) — never settable
        # Required references (lookup / master-detail): no longer fenced — F6.2's
        # construct_world builds the parent. Collected with the referenced Object's
        # entity id so the caller can construct it + thread the new id here.
        ref = details.get("references_object_entity_id")
        if ref is not None:
            required_refs.append((api, ref))
            continue
        value = _fill_value(details, s1, at_seq)
        if value is _UNFILLABLE:
            unfillable.append(api)
        else:
            filler[api] = value

    return PaddingResult(
        filler=filler, unfillable=tuple(sorted(unfillable)),
        required_refs=tuple(sorted(required_refs, key=lambda t: t[0])))


def construct_world(
    object_api: str, semantic_fields: set[str], *, s1, client, tracker, at_seq: int,
    _visited=frozenset(), _depth: int = 0,
):
    """Construct the full operational world for a create on ``object_api`` and
    return ``(scalar_filler, parent_filler, unfillable)``.

    Extends the pure :func:`resolve_operational_padding` leaf with **F6.2 parent
    provisioning**: each required lookup / master-detail field is satisfied by
    *recursively building the referenced parent record* on the live org (via the
    injected ``client``) and threading the new parent id into the child's lookup
    (``parent_filler[field_api] = parent_id``). Every created record is appended to
    ``tracker`` in **creation order** (a parent before the child that needs it), so
    the caller's reverse-order teardown deletes children before parents.

    Unlike the leaf resolver this has **org side-effects** (it creates parents). A
    parent is created only *after* its own subtree fully resolves
    (recurse-then-create), so a doomed branch (cyclic / too deep / unsynthesizable)
    leaves **zero** records — the failure propagates up as ``unfillable`` before any
    create. When a sibling ref fails *after* an earlier sibling was built, the built
    record is on ``tracker`` and the caller tears it down on the unfillable path.

    Bounded by :data:`MAX_PARENT_DEPTH`; a self-referential or N-hop cyclic required
    lookup is caught by the ``_visited`` set of referenced-Object entity ids (→
    ``unfillable``, never infinite recursion). A transport failure mid-build raises
    ``SFClientError`` (the caller catches it + tears down what was built)."""
    padding = resolve_operational_padding(
        object_api, semantic_fields, s1=s1, at_seq=at_seq)
    scalar_filler = dict(padding.filler)
    parent_filler: dict[str, Any] = {}
    unfillable = list(padding.unfillable)

    for child_field_api, ref_object_entity_id in padding.required_refs:
        if _depth >= MAX_PARENT_DEPTH:
            unfillable.append(child_field_api)        # chain too deep — honest stop
            continue
        if ref_object_entity_id in _visited:
            unfillable.append(child_field_api)        # cycle — honest stop
            continue
        parent_objs = s1.get_entities(
            "Object", at_seq=at_seq, filters={"id": ref_object_entity_id})
        if not parent_objs:
            unfillable.append(child_field_api)        # parent Object not in S1
            continue
        parent_api = parent_objs[0].sf_api_name
        if parent_api in _DEFAULTED_REF_OBJECTS:
            continue          # owner/queue (User/Group) — Salesforce defaults it; omit
        # Recurse for the parent's OWN required scalars + parents (no semantic
        # field — a provisioned parent is pure operational padding).
        p_scalar, p_parent, p_unfillable = construct_world(
            parent_api, set(), s1=s1, client=client, tracker=tracker, at_seq=at_seq,
            _visited=_visited | {ref_object_entity_id}, _depth=_depth + 1)
        if p_unfillable:
            unfillable.append(child_field_api)        # parent unbuildable — propagate
            continue
        env = client.create(parent_api, {**p_scalar, **p_parent})
        if not env.get("success"):
            unfillable.append(child_field_api)        # org rejected the parent create
            continue
        tracker.record(parent_api, env["record_id"])  # creation order: parent first
        parent_filler[child_field_api] = env["record_id"]

    return scalar_filler, parent_filler, tuple(sorted(set(unfillable)))


def _fill_value(details: dict, s1, at_seq: int):
    """A type-valid filler for a required scalar / simple-picklist, or the
    ``_UNFILLABLE`` sentinel. Format-aware for the constrained text types so the
    filler does not itself trip a format check (which would mis-read downstream as
    a value rejection)."""
    ftype = (details.get("field_type") or "").lower()
    if ftype in _NUMERIC:
        return 1
    if ftype == "boolean":
        return False
    if ftype == "date":
        return date.today().isoformat()
    if ftype == "datetime":
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if ftype == "email":
        return "pqa@example.com"
    if ftype == "url":
        return "https://example.com"
    if ftype == "phone":
        return "5555550100"
    if ftype in _TEXTUAL:
        length = details.get("length") or 0
        val = "PQA"
        return val[:length] if 0 < length < len(val) else val
    if ftype == "picklist":
        return _picklist_value(details, s1, at_seq)
    # reference is handled by the caller (references_object_entity_id); every
    # other type (multipicklist, id, address, location, base64, time, anyType, …)
    # is not synthesizable in slice 1.
    return _UNFILLABLE


def _picklist_value(details: dict, s1, at_seq: int):
    """The default (or first active, by sort order) value of a simple
    (value-set-backed) picklist, or ``_UNFILLABLE`` when the value set / an active
    value is not readable (an inline picklist whose set S1 did not capture —
    deferred).

    Values are enumerated via ``s1.get_picklist_values`` (the D-119 primitive):
    a PicklistValueSet's values hang off the
    ``picklist_value_details.picklist_value_set_entity_id`` FK — there is NO
    containment edge (the D-019 taxonomy is locked at 14 types), so the prior
    BELONGS_TO ``get_related`` walk here could never return values against the
    real store (only its test stubs satisfied it; surfaced live by the D-203
    proof when StageName padding found a linked-but-"empty" set). D-204.2.
    """
    pvs_id = details.get("picklist_value_set_entity_id")
    if not pvs_id:
        return _UNFILLABLE
    rows = s1.get_picklist_values(pvs_id, at_seq=at_seq)
    first_active: Optional[str] = None
    for d in rows:                          # already ordered by sort_order
        if not d.get("is_active", True):
            continue
        name = d.get("value_api_name")
        if not name:
            continue
        if d.get("is_default", False):
            return name                     # an explicit default wins
        if first_active is None:
            first_active = name
    if first_active is not None:
        return first_active
    return _UNFILLABLE
