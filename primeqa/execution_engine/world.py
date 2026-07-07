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

# The shared D-107 formula AST (R1's VR-satisfaction pass). Aliased so the
# node names can't collide with S4's own vocabulary; using the parser from S4
# is not a new seam — world.py already reads primeqa.semantic
# (entity_attributes) for the picklist gate.
from primeqa.semantic.formula import (
    And as _FAnd,
    Comparison as _FComparison,
    FieldRef as _FFieldRef,
    FunctionCall as _FFunctionCall,
    Literal as _FLiteral,
    Not as _FNot,
    NotParsed as _FNotParsed,
    Or as _FOr,
)

_BELONGS_TO = "BELONGS_TO"
_APPLIES_TO = "APPLIES_TO"    # ValidationRule → Object (the VR-gating read)

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


@dataclass(frozen=True)
class WorldPlan:
    """A pre-resolved, DETACHED plan for one create's operational world (D-230.2).

    The read side of :func:`construct_world`, frozen up front by :func:`plan_world`
    (pure S1 reads) so the build side (:func:`build_world`, the live Salesforce
    creates) can run holding NO DB connection — the async data-path bracket. Plain
    data only (dict of scalars + a recursive tuple tree), so it survives the select
    bracket's connection close.

    ``parent_plans`` is the required-parent provisioning tree: each
    ``(child_field_api, parent_api, WorldPlan)`` is a lookup/master-detail parent
    that :func:`build_world` will create + thread into the child's lookup."""

    object_api: str
    scalar_filler: dict[str, Any]
    unfillable: tuple[str, ...]
    parent_plans: tuple = ()   # ((child_field_api, parent_api, WorldPlan), ...)


def resolve_operational_padding(
    object_api: str, semantic_fields: set[str], *, s1, at_seq: int,
    semantic_values: Optional[dict] = None,
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

    ``semantic_values`` (req-302 robustness, R1 — the D-119 "plan-time
    evaluator" DEFERRED_ITEM armed): the staged ``{qualified_field: value}``
    pairs of the create. When given, a VR-SATISFACTION pass runs after the
    filler is built: an active VR that is ARMED by the staged state (its
    formula pins a staged value) and rejects for a parseable padding-owned
    deficiency — a bare boolean, an ISBLANK'd blank field — gets that
    deficiency satisfied (KYC_Complete__c True, Credit_Score__c filled) so the
    create is not rejected for PADDING's shortfall (the AmbiguousRejection /
    setup_rejection class). ``None`` (every legacy caller) disables the pass —
    byte-identical behavior."""
    objs = s1.get_entities(
        "Object", at_seq=at_seq, filters={"sf_api_name": object_api})
    if not objs:
        # Object not in S1 — nothing to pad; the live create surfaces the real
        # outcome and S6 interprets the absence.
        return PaddingResult(filler={}, unfillable=())
    obj = objs[0]

    fields = s1.get_related(
        obj.id, edge_types=[_BELONGS_TO], direction="inbound", at_seq=at_seq)
    # The object's ACTIVE VR formulas — the padding-side gate check: a picklist
    # value a VR names as a literal is likely entry-gated (the AmbiguousRejection
    # class: a field-less business rejection of PADDING, not the value under
    # test), so _picklist_value prefers an unmentioned value. Best-effort — an
    # unreadable VR set degrades to today's blind pick, never to unfillable.
    gated_formulas = _active_vr_formulas(obj.id, s1=s1, at_seq=at_seq)

    filler: dict[str, Any] = {}
    unfillable: list[str] = []
    required_refs: list[tuple[str, str]] = []
    # R1: every WRITABLE non-semantic scalar, keyed by its bare lower-cased
    # name (formulas speak bare names) — including the NILLABLE fields the
    # requiredness loop skips (a VR may demand an optional field be populated:
    # Credit_Score__c is exactly that case).
    paddable_index: dict[str, tuple[str, dict]] = {}
    for r in fields:
        fld = r.entity
        if fld.entity_type != "Field":
            continue
        api = fld.sf_api_name
        if not api or api in semantic_fields:     # k16 — never a recipe-set field
            continue
        details = s1.get_entity_details(fld.id, at_seq=at_seq) or {}
        if (not details.get("is_calculated", False)
                and details.get("is_createable", True)
                and details.get("references_object_entity_id") is None):
            paddable_index[api.rsplit(".", 1)[-1].lower()] = (api, details)
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
        value = _fill_value(details, s1, at_seq, gated_formulas=gated_formulas)
        if value is _UNFILLABLE:
            unfillable.append(api)
        else:
            filler[api] = value

    if semantic_values:
        filler.update(_vr_satisfaction_filler(
            gated_formulas, semantic_values, paddable_index,
            s1=s1, at_seq=at_seq))

    return PaddingResult(
        filler=filler, unfillable=tuple(sorted(unfillable)),
        required_refs=tuple(sorted(required_refs, key=lambda t: t[0])))


def _sf_field(name: str, sobject: str) -> str:
    """An S1 *qualified* field name (``{Object}.{field}``) → its bare Salesforce
    API name (``{field}``). S1 names fields object-qualified for graph uniqueness
    (``sync.phases`` field phase); the live REST / SOQL API speaks **bare** names.
    A name without the ``{sobject}.`` self-prefix — already bare, or a relationship
    path like ``Owner.Name`` — passes through unchanged."""
    return name.removeprefix(f"{sobject}.")


def _sf_fields(field_values: dict, sobject: str) -> dict:
    """Bare-ify the keys of a create payload (recipe field(s) + operational
    padding) for the live create. Lives here (not data_executor) so the
    provisioned-parent create below can use it without an import cycle —
    D-227.5: the parent create used to POST qualified keys, which Salesforce
    rejects, mis-reading as an unfillable world."""
    return {_sf_field(k, sobject): v for k, v in field_values.items()}


def plan_world(
    object_api: str, semantic_fields: set[str], *, s1, at_seq: int,
    semantic_values: Optional[dict] = None,
    _visited=frozenset(), _depth: int = 0,
) -> WorldPlan:
    """Pre-resolve the operational world for a create on ``object_api`` into a
    detached :class:`WorldPlan` — the READ side of :func:`construct_world` (D-230.2),
    walked read-only up front so the build side can run holding no DB connection.

    Walks the SAME bounded recursion as ``construct_world``: the pure
    :func:`resolve_operational_padding` leaf + the required-ref parent chain. Decides
    the plan-time ``unfillable`` (too-deep / cycle / parent-not-in-S1 / a parent
    subtree that is itself unbuildable) and omits the Salesforce-defaulted refs
    (User/Group). Makes ONLY S1 reads through ``s1`` — no ``client``, no creates."""
    padding = resolve_operational_padding(
        object_api, semantic_fields, s1=s1, at_seq=at_seq,
        semantic_values=semantic_values)
    unfillable = list(padding.unfillable)
    parent_plans: list = []

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
        # Recurse READ-ONLY for the parent's OWN required scalars + parents (no
        # semantic field — a provisioned parent is pure operational padding).
        parent_plan = plan_world(
            parent_api, set(), s1=s1, at_seq=at_seq,
            _visited=_visited | {ref_object_entity_id}, _depth=_depth + 1)
        if parent_plan.unfillable:
            unfillable.append(child_field_api)        # parent subtree unbuildable — propagate
            continue
        parent_plans.append((child_field_api, parent_api, parent_plan))

    return WorldPlan(
        object_api=object_api, scalar_filler=dict(padding.filler),
        unfillable=tuple(sorted(set(unfillable))), parent_plans=tuple(parent_plans))


def build_world(world_plan: WorldPlan, *, client, tracker):
    """Build the operational world from a pre-resolved :class:`WorldPlan` and return
    ``(scalar_filler, parent_filler, unfillable)`` — the BUILD side of
    :func:`construct_world` (D-230.2), making NO S1 reads (it takes the plan, not the
    reader, so it is async-bracket-safe).

    Walks the plan's parent tree depth-first (a parent created only after its own
    subtree, recurse-then-create — same order as ``construct_world``), creates each
    required parent on the live org via ``client``, threads the new id into the
    child's lookup, and records it on ``tracker`` in creation order (parent before
    child, so reverse teardown deletes child first). Returns the plan-time
    ``unfillable`` plus any build-time unfillable (the org rejected a parent create).
    A parent whose own subtree is unfillable is skipped (never half-built); a
    transport failure raises ``SFClientError`` (the caller tears down what was built).

    Note (D-230.2): a parent branch that is unfillable at PLAN time is never reached
    here, so — unlike the old interleaved ``construct_world`` — the buildable part of
    a doomed subtree is not created-then-torn-down. Strictly fewer wasted creates;
    identical ``(filler, unfillable)`` outcome."""
    unfillable = list(world_plan.unfillable)
    parent_filler: dict[str, Any] = {}

    for child_field_api, parent_api, parent_plan in world_plan.parent_plans:
        p_scalar, p_parent, p_unfillable = build_world(
            parent_plan, client=client, tracker=tracker)
        if p_unfillable:
            unfillable.append(child_field_api)        # parent unbuildable — propagate
            continue
        # D-227.5: bare-ify like the top-level create path — the live API
        # speaks bare names; qualified keys get rejected and mis-read as an
        # unfillable world (the be56416d live error).
        env = client.create(parent_api,
                            _sf_fields({**p_scalar, **p_parent}, parent_api))
        if not env.get("success"):
            unfillable.append(child_field_api)        # org rejected the parent create
            continue
        tracker.record(parent_api, env["record_id"])  # creation order: parent first
        parent_filler[child_field_api] = env["record_id"]

    return world_plan.scalar_filler, parent_filler, tuple(sorted(set(unfillable)))


def construct_world(
    object_api: str, semantic_fields: set[str], *, s1, client, tracker, at_seq: int,
    semantic_values: Optional[dict] = None,
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
    ``SFClientError`` (the caller catches it + tears down what was built).

    D-230.2: now composes the pure :func:`plan_world` (S1 read-walk → a detached
    :class:`WorldPlan`) + :func:`build_world` (the live creates) — same signature and
    behavior, so callers are unchanged; the split lets the async data path pre-resolve
    the reads under the select bracket and build with no DB connection held."""
    return build_world(
        plan_world(object_api, semantic_fields, s1=s1, at_seq=at_seq,
                   semantic_values=semantic_values,
                   _visited=_visited, _depth=_depth),
        client=client, tracker=tracker)


def _fill_value(details: dict, s1, at_seq: int, gated_formulas=()):
    """A type-valid filler for a required scalar / simple-picklist, or the
    ``_UNFILLABLE`` sentinel. Format-aware for the constrained text types so the
    filler does not itself trip a format check (which would mis-read downstream as
    a value rejection). ``gated_formulas`` (the padded object's active VR formula
    texts) steers the picklist pick away from VR-gated values."""
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
        return _picklist_value(details, s1, at_seq, gated_formulas=gated_formulas)
    # reference is handled by the caller (references_object_entity_id); every
    # other type (multipicklist, id, address, location, base64, time, anyType, …)
    # is not synthesizable in slice 1.
    return _UNFILLABLE


def _picklist_value(details: dict, s1, at_seq: int, gated_formulas=()):
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

    ``gated_formulas`` (the object's active VR formula texts) filters the pick:
    a candidate value one of them names as a quoted literal (``'v'`` or ``"v"``
    — both quoting styles occur live) is likely entry-gated (e.g. a stage with
    prerequisites), so the FIRST candidate no formula mentions wins, in today's
    default-then-sort-order precedence. When every candidate is mentioned — or
    no formulas were readable — this falls back to today's exact pick, so
    behavior is never worse and never newly unfillable. Deterministic: a pure
    function of the S1 state at ``at_seq``.
    """
    pvs_id = details.get("picklist_value_set_entity_id")
    if not pvs_id:
        return _UNFILLABLE
    rows = s1.get_picklist_values(pvs_id, at_seq=at_seq)
    default: Optional[str] = None
    actives: list[str] = []
    for d in rows:                          # already ordered by sort_order
        if not d.get("is_active", True):
            continue
        name = d.get("value_api_name")
        if not name:
            continue
        if d.get("is_default", False) and default is None:
            default = name                  # an explicit default wins
        else:
            actives.append(name)
    ordered = ([default] if default is not None else []) + actives
    if not ordered:
        return _UNFILLABLE
    for name in ordered:
        if not _vr_gated(name, gated_formulas):
            return name
    return ordered[0]                       # all gated — today's exact pick


def _active_vr_formulas(object_entity_id, *, s1, at_seq: int) -> tuple:
    """The formula texts of the ACTIVE ValidationRules that APPLY_TO the padded
    object — the picklist gate check's input. Best-effort: any read/shape error
    (including a port that doesn't serve APPLIES_TO) → ``()``, degrading to
    today's blind pick, never blocking padding."""
    try:
        from primeqa.semantic.entity_attributes import vr_formula_text, vr_is_active
        rows = s1.get_related(
            object_entity_id, edge_types=[_APPLIES_TO], direction="inbound",
            at_seq=at_seq)
        out = []
        for r in rows:
            ent = getattr(r, "entity", None)
            if ent is None or getattr(ent, "entity_type", None) != "ValidationRule":
                continue
            attrs = getattr(ent, "attributes", None)
            if not vr_is_active(attrs):
                continue
            formula = vr_formula_text(attrs)
            if formula:
                out.append(formula)
        return tuple(out)
    except Exception:
        return ()


def _vr_satisfaction_filler(formulas, semantic_values: dict,
                            paddable_index: dict, *, s1, at_seq: int) -> dict:
    """R1 (req-302 robustness): ``{qualified_api: value}`` demands that keep
    the staged create from being rejected for a PADDING-owned deficiency.

    For each active VR formula (sorted — deterministic), parsed with the
    shared D-107 parser:
      - the VR must be AND-rooted and ARMED by the staged state — at least
        one conjunct provably TRUE from the semantic values alone
        (``ISPICKVAL(StageName, 'Credit Assessment')`` with that exact staged
        value; an ``=`` comparison; a staged bare boolean). Never satisfy a
        rule the staged create does not arm.
      - the first non-pin conjunct that is FALSIFIABLE on padding-owned
        fields yields the demands: a bare boolean → its non-firing polarity
        (the VR's own structure names the value — metadata-derived, never
        invented); ``ISBLANK(f)``/``ISNULL(f)`` → the existing type-derived
        ``_fill_value`` constant; ``OR(...)`` needs ALL its operands
        falsified; a nested ``AND(...)`` needs any ONE.
    k16 hard guard: a demand on a semantically staged field is refused at the
    leaf — padding never writes the value under test. Cross-VR conflicts drop
    the field entirely (an honest setup_rejection beats a guess). Anything
    unparseable / unfalsifiable → no demand — today's behavior, the org's own
    rejection message stays the honest surface."""
    from primeqa.semantic.formula import parse as _parse

    staged_bare = {str(k).rsplit(".", 1)[-1].lower(): v
                   for k, v in (semantic_values or {}).items()}
    demands: dict[str, tuple[str, Any]] = {}
    dropped: set[str] = set()
    for text in sorted(formulas or ()):
        try:
            ast = _parse(text)
            if isinstance(ast, _FNotParsed) or not isinstance(ast, _FAnd):
                continue
            if not any(_pin_true(c, staged_bare) for c in ast.operands):
                continue
            fix = None
            for c in ast.operands:
                if _pin_true(c, staged_bare):
                    continue
                fix = _falsify(c, staged_bare, paddable_index,
                               s1=s1, at_seq=at_seq, formulas=formulas)
                if fix is not None:
                    break
            if not fix:
                continue
            for bare, (api, val) in fix.items():
                if bare in dropped:
                    continue
                prev = demands.get(bare)
                if prev is not None and prev[1] != val:
                    demands.pop(bare, None)       # conflicting demands — fall
                    dropped.add(bare)             # back to the blind default
                    continue
                demands[bare] = (api, val)
        except Exception:
            continue                              # best-effort, never blocks
    return {api: val for api, val in demands.values()}


def _pin_true(node, staged_bare: dict) -> bool:
    """Is this conjunct provably TRUE from the staged semantic values alone?
    (The 'armed by the staged state' check.)"""
    if isinstance(node, _FFunctionCall) and node.name == "ISPICKVAL":
        if (len(node.args) == 2 and isinstance(node.args[0], _FFieldRef)
                and not node.args[0].is_dotted
                and isinstance(node.args[1], _FLiteral)):
            staged = staged_bare.get(node.args[0].path[0].lower(), _PIN_MISS)
            return staged is not _PIN_MISS and staged == node.args[1].value
        return False
    if isinstance(node, _FComparison) and node.op == "=":
        pair = None
        if isinstance(node.left, _FFieldRef) and isinstance(node.right, _FLiteral):
            pair = (node.left, node.right)
        elif isinstance(node.right, _FFieldRef) and isinstance(node.left, _FLiteral):
            pair = (node.right, node.left)
        if pair is None or pair[0].is_dotted:
            return False
        staged = staged_bare.get(pair[0].path[0].lower(), _PIN_MISS)
        return staged is not _PIN_MISS and staged == pair[1].value
    if isinstance(node, _FFieldRef) and not node.is_dotted:
        return staged_bare.get(node.path[0].lower()) is True
    if isinstance(node, _FNot) and isinstance(node.operand, _FFieldRef) \
            and not node.operand.is_dotted:
        return staged_bare.get(node.operand.path[0].lower()) is False
    return False


_PIN_MISS = object()


def _already_false(node, staged_bare: dict) -> bool:
    """Is this subtree provably FALSE under the staged semantic values alone?
    The falsifier's counterpart of :func:`_pin_true` (R1.1): an OR arm that
    the staged state already falsifies needs NO padding demand — refusing it
    at the k16 leaf minted wrong-reds (req-302: staged Credit_Score=600
    already falsifies ``ISBLANK(Credit_Score__c)``, but the whole
    ``OR(NOT(KYC_Complete__c), ISBLANK(Credit_Score__c))`` bailed on that arm,
    KYC never got padded, and the armed VR rejected the create). Conservative:
    only the shapes :func:`_pin_true` models, provable from staged values."""
    if isinstance(node, _FFunctionCall) and node.name in ("ISBLANK", "ISNULL"):
        if (len(node.args) == 1 and isinstance(node.args[0], _FFieldRef)
                and not node.args[0].is_dotted):
            staged = staged_bare.get(node.args[0].path[0].lower(), _PIN_MISS)
            return staged is not _PIN_MISS and staged not in (None, "")
        return False
    if isinstance(node, _FFunctionCall) and node.name == "ISPICKVAL":
        if (len(node.args) == 2 and isinstance(node.args[0], _FFieldRef)
                and not node.args[0].is_dotted
                and isinstance(node.args[1], _FLiteral)):
            staged = staged_bare.get(node.args[0].path[0].lower(), _PIN_MISS)
            return staged is not _PIN_MISS and staged != node.args[1].value
        return False
    if isinstance(node, _FFieldRef) and not node.is_dotted:
        staged = staged_bare.get(node.path[0].lower(), _PIN_MISS)
        return staged is not _PIN_MISS and staged is False
    if isinstance(node, _FNot) and isinstance(node.operand, _FFieldRef) \
            and not node.operand.is_dotted:
        staged = staged_bare.get(node.operand.path[0].lower(), _PIN_MISS)
        return staged is not _PIN_MISS and staged is True
    return False


def _falsify(node, staged_bare: dict, paddable_index: dict, *,
             s1, at_seq: int, formulas):
    """``{bare: (qualified_api, value)}`` that makes this subtree FALSE using
    only padding-owned fields, or ``None`` when it can't be done safely."""
    if isinstance(node, _FFieldRef) and not node.is_dotted:
        return _bool_demand(node.path[0], False, staged_bare, paddable_index)
    if isinstance(node, _FNot) and isinstance(node.operand, _FFieldRef) \
            and not node.operand.is_dotted:
        return _bool_demand(node.operand.path[0], True, staged_bare,
                            paddable_index)
    if isinstance(node, _FFunctionCall) and node.name in ("ISBLANK", "ISNULL"):
        if (len(node.args) == 1 and isinstance(node.args[0], _FFieldRef)
                and not node.args[0].is_dotted):
            bare = node.args[0].path[0].lower()
            if bare in staged_bare:               # k16 — never touch staged
                return None
            meta = paddable_index.get(bare)
            if meta is None:
                return None
            api, details = meta
            value = _fill_value(details, s1, at_seq, gated_formulas=formulas)
            if value is _UNFILLABLE:
                return None
            return {bare: (api, value)}
        return None
    if isinstance(node, _FOr):
        merged: dict = {}
        for op in node.operands:
            # R1.1: an arm ALREADY provably false under the staged values
            # needs no demand — refusing at k16 for it minted wrong-reds
            # (req-302: staged Credit_Score=600 already falsifies
            # ISBLANK(Credit_Score__c); the old code bailed the whole OR
            # instead of skipping the arm, so KYC never got padded and the
            # armed VR rejected the create).
            if _already_false(op, staged_bare):
                continue
            sub = _falsify(op, staged_bare, paddable_index,
                           s1=s1, at_seq=at_seq, formulas=formulas)
            if sub is None:
                return None                       # every live OR arm must falsify
            for bare, pair in sub.items():
                if bare in merged and merged[bare][1] != pair[1]:
                    return None                   # self-conflicting — bail
                merged[bare] = pair
        # {} when every arm is already false / demand-free — the conjunct is
        # SAFE with no demands (the caller's falsy-check skips it), distinct
        # from None (unfalsifiable — the caller tries the next conjunct).
        return merged
    if isinstance(node, _FAnd):
        for op in node.operands:                  # one false conjunct suffices
            sub = _falsify(op, staged_bare, paddable_index,
                           s1=s1, at_seq=at_seq, formulas=formulas)
            if sub is not None:
                return sub
        return None
    return None                                   # comparisons, ISPICKVAL, …


def _bool_demand(field_name: str, value: bool, staged_bare: dict,
                 paddable_index: dict):
    bare = field_name.lower()
    if bare in staged_bare:                       # k16 — never touch staged
        return None
    meta = paddable_index.get(bare)
    if meta is None:
        return None
    api, details = meta
    if (details.get("field_type") or "").lower() != "boolean":
        return None
    return {bare: (api, value)}


def _vr_gated(value: str, formulas) -> bool:
    """Does any active VR formula name this picklist value as a quoted literal?
    Checks BOTH quoting styles — live formulas carry ``"Credit Assessment"``
    (double) and ``'Closed Lost'`` (single). Substring-on-quoted-literal is the
    deliberate fidelity: no formula parsing, no evaluation (the full plan-time
    evaluator is deferred — S4 DEFERRED_ITEMS)."""
    for f in formulas or ():
        if f"'{value}'" in f or f'"{value}"' in f:
            return True
    return False
