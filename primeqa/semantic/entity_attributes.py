"""Substrate 1 — Tier 1 entity attribute schemas + the attributes read API.

**The storage contract (D-204, ratified).** `entities.attributes` JSONB
stores the sync's **normalized raw Tooling record** (`materialize.py` —
`json.dumps(e.normalized)`). Rows written by seeds or the pre-cutover
writer carry the DESIGNED sparse shape below instead, and — because SCD
Type 2 + content-hash change detection means a row keeps its birth shape
until its content changes — **both shapes coexist in the store
permanently**. Never assume one shape; never read per-type keys with
ad-hoc `attributes->>'...'` SQL or `.get(...)`.

**The read API.** Per-type keys are read through the shape-tolerant
extractors at the bottom of this module (`vr_formula_text`,
`vr_error_message` — designed key preferred, raw Tooling fallback;
D-203.1). The family grows as new per-type keys gain readers.

**What the schemas below are now.** The per-type `_EntityAttributes`
classes (D-025) document the DESIGNED key vocabulary the extractors
prefer, and the `TIER_1_ENTITIES` registry maps entity_type -> detail
table for the sync engine + query layer (mirrors TIER_1_EDGES).
`validate_entity_attributes` has no production writer since the
greenfield sync cutover (D-150+) — it remains as the schema-conformance
checker its unit suite exercises.

Per D-016: the DB-level CHECK on entities.attributes only enforces
`jsonb_typeof = 'object'`; structure beyond that is this module's
concern. Detail tables capture hot columns (queryable across the
population); attributes carries sparse per-row metadata.
"""

from __future__ import annotations

import re as _re
from typing import Optional, Type

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------
# Common base — strict, frozen, no extras
# ----------------------------------------------------------------------

class _EntityAttributes(BaseModel):
    """Common config for all entity-attribute schemas.

    Same discipline as _EdgeProperties in edges.py:
      - frozen: attributes are immutable after construction. Once a Salesforce
        Describe response is parsed and validated, the dict is write-once
        on the way to entities.attributes JSONB.
      - extra='forbid': reject unknown keys at the boundary. If sync code
        adds a typo'd attribute name, fail loud rather than silently land
        garbage in JSONB.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")


# ----------------------------------------------------------------------
# ObjectAttributes — sparse metadata for entity_type='Object'
# ----------------------------------------------------------------------

class ObjectAttributes(_EntityAttributes):
    """Sparse Object metadata living in entities.attributes JSONB.

    Per D-025: hot Object attributes (key_prefix, is_custom, the four CRUD
    flags) are columns on object_details. The remaining DescribeSObjectResult
    fields land here.

    Defaults reflect Salesforce realism: most objects are retrievable
    (you can fetch them by ID), few are mergeable/feed-enabled/history-tracked.
    Real values come from the sync engine reading DescribeSObjectResult; the
    defaults exist only so partial dicts validate during testing or for
    edge-case entities (e.g., virtual objects) where some fields are
    legitimately absent.

    Promotion rule (D-018, D-025): if any of these attributes starts being
    queried, filtered, or joined across entities by application code, it
    is promoted to a column on object_details in a follow-up migration. The
    JSONB is for sparse access by attribute name, not for cross-entity queries.
    """
    is_searchable: bool = False
    is_layoutable: bool = False
    is_mergeable: bool = False
    is_replicable: bool = False
    is_retrievable: bool = True
    is_undeletable: bool = False
    is_feed_enabled: bool = False
    is_history_tracked: bool = False
    plural_label: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)


# ----------------------------------------------------------------------
# FieldAttributes — sparse metadata for entity_type='Field'
# ----------------------------------------------------------------------

class FieldAttributes(_EntityAttributes):
    """Sparse Field metadata living in entities.attributes JSONB.

    Per D-025: hot Field attributes (object_entity_id, references_object_entity_id,
    field_type, the seven boolean flags, length/precision/scale) are columns
    on field_details. The remaining DescribeFieldResult metadata lands here.

    Boolean axes:
      - is_required vs is_nillable (column on field_details): distinct concepts.
        is_nillable is the database-level NULL constraint on the underlying
        Salesforce column; is_required is the UI/create-time enforcement set
        on the page layout. A field can be nillable=True but required=True,
        meaning the column allows NULL but the layout demands a value at
        create time.
      - is_groupable / is_aggregatable: type-dependent. Numerics tend to be
        aggregatable, picklists tend to be groupable. Default False because
        most fields are neither.
      - is_case_sensitive / is_html_formatted: text-type-only concerns; False
        for non-text fields.

    String axes:
      - default_value: literal or formula expression for the field default.
        NULL when the field has none.
      - formula: present on formula fields only. NULL for direct-entry fields.
      - inline_help_text: the on-hover help bubble (Salesforce caps at 510).
      - help_text: the longer field description (Salesforce caps at 1000).
      - relationship_name: API-name suffix for the relationship (e.g. 'Owner'
        for OwnerId). Set only on lookup/master-detail field types.
      - controller_name: name of the controlling field for dependent picklists.

    Defaults reflect Salesforce realism: most fields are not required, not
    groupable, not aggregatable, not case-sensitive, not HTML-formatted, and
    carry no default / formula / help / relationship / controller. Real values
    come from the sync engine reading DescribeFieldResult; the defaults exist
    so partial dicts validate during testing.

    Promotion rule (D-018, D-025): if any of these attributes starts being
    queried, filtered, or joined across entities by application code, it is
    promoted to a column on field_details in a follow-up migration.
    """
    is_required: bool = False
    is_groupable: bool = False
    is_aggregatable: bool = False
    is_case_sensitive: bool = False
    is_html_formatted: bool = False
    default_value: Optional[str] = Field(default=None, max_length=4000)
    formula: Optional[str] = Field(default=None, max_length=5000)
    inline_help_text: Optional[str] = Field(default=None, max_length=510)
    help_text: Optional[str] = Field(default=None, max_length=1000)
    relationship_name: Optional[str] = Field(default=None, max_length=80)
    controller_name: Optional[str] = Field(default=None, max_length=80)


class RecordTypeAttributes(_EntityAttributes):
    """Sparse RecordType metadata living in entities.attributes JSONB.

    Per D-025: hot RecordType attributes (object_entity_id, is_active,
    is_master) are columns on record_type_details. The remaining
    DescribeSObjectResult.recordTypeInfos / RecordType-related metadata
    lands here.

    RecordType has unusually little hot metadata; most of its semantic
    weight lives in outgoing edges (CONSTRAINS_PICKLIST_VALUES to
    PicklistValues, ASSIGNED_TO_PROFILE_RECORDTYPE to Profiles).
    The JSONB stays sparse on purpose.

    business_process_id is populated only for RecordTypes attached to
    Cases, Leads, Opportunities, or Solutions — those four objects
    support BusinessProcess. NULL/absent for all other RecordTypes.
    Stored as the Salesforce 18-char ID; if/when BusinessProcess becomes
    its own entity_type, this gets promoted to a column FK on a future
    detail-table revision (per D-025's promotion rule).
    """
    description: Optional[str] = Field(default=None, max_length=255)
    business_process_id: Optional[str] = Field(default=None, max_length=18)


class LayoutAttributes(_EntityAttributes):
    """Sparse Layout metadata living in entities.attributes JSONB.

    Per D-025: hot Layout attributes (object_entity_id, layout_type,
    layout_api_name, is_active) are columns on layout_details. The
    structural weight of a Layout — which fields appear in which sections
    at which positions — lives on INCLUDES_FIELD edges (D-019), not on
    this attribute schema.

    description is the user-facing Layout description text. Always optional;
    Salesforce only requires it for Lightning page layouts in some contexts.
    """
    description: Optional[str] = Field(default=None, max_length=255)


class PicklistValueAttributes(_EntityAttributes):
    """Sparse PicklistValue metadata living in entities.attributes JSONB.

    Per D-025: hot PicklistValue attributes (picklist_value_set_entity_id,
    value_label, value_api_name, is_active, is_default, sort_order) are
    columns on picklist_value_details. The remaining metadata lands here.

    color_code is the Salesforce color-picker hex code, used by some Lightning
    UI components to color-code picklist values (e.g., "Hot" lead = red,
    "Cold" = blue). Optional VARCHAR(7) for #RRGGBB format. Rare in most orgs
    but present on certain standard picklists like Lead Status.

    Most PicklistValue metadata is on the detail table because picklist values
    ARE their attributes — there is no edge structure to lean on. JSONB stays
    sparse intentionally; if more attributes start being queried, they get
    promoted to columns per D-025's promotion rule.
    """
    color_code: Optional[str] = Field(default=None, max_length=7)


class ProfileAttributes(_EntityAttributes):
    """Sparse Profile metadata living in entities.attributes JSONB.

    Per D-025: hot Profile attributes (is_active, is_custom, user_license_type)
    are columns on profile_details. Profile has no containment column —
    Profiles are top-level org entities referenced by edges (HAS_PROFILE
    from Users, GRANTS_OBJECT_ACCESS to Objects, GRANTS_FIELD_ACCESS to
    Fields, ASSIGNED_TO_PROFILE_RECORDTYPE from Layouts).

    user_type is distinct from user_license_type. License type is the
    Salesforce license tier ('Salesforce', 'Salesforce Platform', etc.)
    and is the queryable hot column. user_type is a finer-grained subdivision
    ('Standard', 'PowerCustomerSuccess', 'CsnOnly', etc.) that's rarely
    queried directly — most generation/diff paths care about license tier,
    not user-type subdivisions. Stays in JSONB until that changes.

    description is the user-facing Profile description. Optional.
    """
    description: Optional[str] = Field(default=None, max_length=255)
    user_type: Optional[str] = Field(default=None, max_length=40)


class PermissionSetAttributes(_EntityAttributes):
    """Sparse PermissionSet metadata living in entities.attributes JSONB.

    Per D-025: hot PermissionSet attributes (is_custom, license_type) are
    columns on permission_set_details. PermissionSet has no containment
    column — it's a top-level entity referenced by edges (HAS_PERMISSION_SET
    inbound from User, GRANTS_OBJECT_ACCESS to Object, GRANTS_FIELD_ACCESS
    to Field, INHERITS_PERMISSION_SET to other PermissionSets).

    namespace_prefix is the managed-package namespace prefix for
    PermissionSets installed via managed packages (e.g., "myPackage__").
    NULL/absent for org-native PermissionSets. Salesforce limits
    namespace prefixes to 15 characters. If managed-vs-unmanaged becomes
    a hot filter, the appropriate move is a partial index on this JSONB
    field, not a denormalized is_managed BOOLEAN column.

    description is the user-facing PermissionSet description. Optional.
    """
    description: Optional[str] = Field(default=None, max_length=255)
    namespace_prefix: Optional[str] = Field(default=None, max_length=15)


class UserAttributes(_EntityAttributes):
    """Sparse User metadata living in entities.attributes JSONB.

    Per D-025: hot User attributes (profile_entity_id, is_active, is_external,
    user_type) are columns on user_details. profile_entity_id is the
    assignment FK driving HAS_PROFILE — this is a PERMISSION-category edge
    per D-019, not STRUCTURAL containment. Users exist independently of any
    specific Profile in the bitemporal sense; assignment can change.

    email and username are privacy-sensitive personal identifiers. Kept in
    JSONB rather than promoted to columns:
      - Not core to graph reasoning (Users are referenced by id, not email)
      - Not joined across entities
      - Privacy-sensitive (GDPR) — JSONB makes it slightly harder to leak
        into logs and indexes
      - Low-frequency cross-population filters (no current "find users by
        email pattern" query — promote later if that emerges)
    Application-layer discipline: never expose email/username in logs or
    diff output by default. (Documented for Phase 2 sync engine.)

    time_zone_sid_key, locale_sid_key, language_locale_key — Salesforce
    locale identifiers (e.g., 'America/Los_Angeles', 'en_US'). Per-user
    attributes; not queried across population. Fixed-format Salesforce
    keys, capped at 40 chars to match SF's max.
    """
    email: Optional[str] = Field(default=None, max_length=254)
    username: Optional[str] = Field(default=None, max_length=80)
    time_zone_sid_key: Optional[str] = Field(default=None, max_length=40)
    locale_sid_key: Optional[str] = Field(default=None, max_length=40)
    language_locale_key: Optional[str] = Field(default=None, max_length=40)


class FlowAttributes(_EntityAttributes):
    """Sparse Flow metadata living in entities.attributes JSONB.

    Per D-025: hot Flow attributes (triggers_on_object_entity_id, flow_type,
    trigger_type, is_active, version_number) are columns on flow_details.
    Flow also has Tier 2 reservation columns (parsed_logic JSONB and
    interpreted_at_capability_level) per SPEC §9 — those live on the
    detail table, not on this attribute schema, because they're
    structurally first-class storage rather than sparse attributes.

    triggers_on_object_entity_id is a behavior FK driving the TRIGGERS_ON
    edge (BEHAVIOR-category per D-019, not STRUCTURAL containment). Flows
    don't 'belong to' Objects — they fire when records of those Objects
    change. Same shape as user_details.profile_entity_id (assignment FK
    driving HAS_PROFILE PERMISSION-category edge), under different category.

    description is the user-facing flow description. Optional.

    process_type is the legacy classification field on older flows.
    Distinct from flow_type for compatibility — flow_type covers the
    modern Salesforce taxonomy; process_type is what older
    process-builder migrations carry. May be redundant for new flows
    but kept for legacy Salesforce data.

    entry_condition_text is the raw text of the flow's entry condition
    formula (e.g., "ISCHANGED(Status) && Status = 'Closed'"). Tier 1
    stores this raw; Tier 2 sync will parse it into structured logic
    in flow_details.parsed_logic. The raw text stays here for audit and
    human-readable display even after Tier 2 parsing.
    """
    description: Optional[str] = Field(default=None, max_length=255)
    process_type: Optional[str] = Field(default=None, max_length=40)
    entry_condition_text: Optional[str] = Field(default=None, max_length=4000)


class ValidationRuleAttributes(_EntityAttributes):
    """Sparse ValidationRule metadata living in entities.attributes JSONB.

    Per D-025: hot ValidationRule attributes (object_entity_id, is_active)
    are columns on validation_rule_details. The rule's field references —
    which fields the formula reads, with PRIORVALUE/ISCHANGED/ISNEW
    annotations — live in validation_rule_field_refs (a hot reference
    table; not a D-025 detail table; see migration 20260427_0120 docstring).

    error_message is the user-facing string displayed when the rule fails
    (Salesforce caps these at 4000 chars in some contexts; we cap at 4000
    to match the more permissive bound).

    error_display_field is the API name of the field where the error
    anchors in the UI. NULL/absent means the error displays at the top
    of the page rather than next to a specific field. Capped at 80 chars
    to match Salesforce's API name length limits.

    formula_text is the raw formula text (e.g.,
    'Amount > 0 && PRIORVALUE(Status) <> Status'). Tier 1 stores raw;
    future Tier 2 may parse into structured logic. Capped at 8000 chars
    to match Salesforce's formula length limit.

    Why formula_text in JSONB rather than a hot column: it's per-rule,
    not queried across the population. Per D-025 promotion rule, JSONB
    is the right default. The field references INSIDE the formula are
    materialized into validation_rule_field_refs (queryable across
    population), and the raw text stays here for audit and human
    inspection.
    """
    error_message: Optional[str] = Field(default=None, max_length=4000)
    error_display_field: Optional[str] = Field(default=None, max_length=80)
    formula_text: Optional[str] = Field(default=None, max_length=8000)


# ----------------------------------------------------------------------
# Registry: TIER_1_ENTITIES
# ----------------------------------------------------------------------

class EntityTypeMetadata(BaseModel):
    """Metadata for one entity_type in the Tier 1 registry.

    - attributes_schema: Pydantic class for entities.attributes JSONB
    - detail_table: name of the per-type detail table holding hot columns

    Future fields might include: source SObject for sync (e.g., 'EntityDefinition'
    for Object), default sync frequency tier, capability_level requirement.
    Keeping minimal for now.
    """
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    attributes_schema: Type[BaseModel]
    detail_table: str


TIER_1_ENTITIES: dict[str, EntityTypeMetadata] = {
    "Object": EntityTypeMetadata(
        attributes_schema=ObjectAttributes,
        detail_table="object_details",
    ),
    "Field": EntityTypeMetadata(
        attributes_schema=FieldAttributes,
        detail_table="field_details",
    ),
    # Future entity types added here as detail tables ship:
    "RecordType": EntityTypeMetadata(
        attributes_schema=RecordTypeAttributes,
        detail_table="record_type_details",
    ),
    "Layout": EntityTypeMetadata(
        attributes_schema=LayoutAttributes,
        detail_table="layout_details",
    ),
    "ValidationRule": EntityTypeMetadata(
        attributes_schema=ValidationRuleAttributes,
        detail_table="validation_rule_details",
    ),
    "Flow": EntityTypeMetadata(
        attributes_schema=FlowAttributes,
        detail_table="flow_details",
    ),
    "Profile": EntityTypeMetadata(
        attributes_schema=ProfileAttributes,
        detail_table="profile_details",
    ),
    "PermissionSet": EntityTypeMetadata(
        attributes_schema=PermissionSetAttributes,
        detail_table="permission_set_details",
    ),
    "User": EntityTypeMetadata(
        attributes_schema=UserAttributes,
        detail_table="user_details",
    ),
    "PicklistValue": EntityTypeMetadata(
        attributes_schema=PicklistValueAttributes,
        detail_table="picklist_value_details",
    ),
}


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------

def get_entity_metadata(entity_type: str) -> EntityTypeMetadata:
    """Look up registry metadata for an entity_type. Raises KeyError on unknown."""
    if entity_type not in TIER_1_ENTITIES:
        raise KeyError(
            f"Unknown entity_type {entity_type!r}. Known types: "
            f"{sorted(TIER_1_ENTITIES.keys())}"
        )
    return TIER_1_ENTITIES[entity_type]


def validate_entity_attributes(entity_type: str, attributes: dict) -> dict:
    """Validate an attributes dict against the entity_type's schema.

    Returns a dict ready for INSERT into entities.attributes JSONB.
    Defaults are filled in; values are normalized (e.g., None -> default).

    Raises:
      KeyError if entity_type is unknown.
      pydantic.ValidationError if attributes don't match the schema.

    Empty dict is valid for any entity_type — all fields have defaults.
    """
    meta = get_entity_metadata(entity_type)
    instance = meta.attributes_schema(**attributes)
    # mode='json' produces a JSON-serializable dict (ready for JSONB).
    return instance.model_dump(mode="json")


def vr_formula_text(attributes: Optional[dict]) -> Optional[str]:
    """The raw error-condition formula from a ValidationRule entity's
    ``entities.attributes`` JSONB — tolerant of BOTH stored shapes:

      - the designed :class:`ValidationRuleAttributes` projection
        (``formula_text`` — seeds + pre-cutover sync rows);
      - the post-cutover sync's normalized raw Tooling record
        (``Metadata.errorConditionFormula`` — see
        ``sync/presentation.py``, which maps the formula into
        semantic_text but stores the raw record as attributes).

    Readers (S3's verified-vs-caveated gate, S6's cause attribution) must
    accept both — rows of both generations coexist in the store. D-203.1.
    """
    attrs = attributes or {}
    text = attrs.get("formula_text")
    if text:
        return text
    metadata = attrs.get("Metadata")
    if isinstance(metadata, dict):
        return metadata.get("errorConditionFormula")
    return None


def vr_error_message(attributes: Optional[dict]) -> Optional[str]:
    """The VR's user-facing error message — same two-shape tolerance as
    :func:`vr_formula_text` (``error_message`` designed / ``ErrorMessage``
    raw Tooling). D-203.1."""
    attrs = attributes or {}
    return attrs.get("error_message") or attrs.get("ErrorMessage")


def field_is_calculated(attributes: Optional[dict]) -> bool:
    """Whether the Field is a CALCULATED (formula / rollup) field — two-shape
    tolerant (D-203.1 idiom): designed ``is_calculated`` OR the raw describe
    ``calculated``. Missing → False (a plain field). D-304: the formula
    automation-primitive grounds on this."""
    attrs = attributes or {}
    for key in ("is_calculated", "calculated"):
        v = attrs.get(key)
        if v is not None:
            return bool(v)
    return False


def field_formula_text(attributes: Optional[dict]) -> Optional[str]:
    """The calculated field's formula source — two-shape tolerant: designed
    ``formula`` OR the raw describe ``calculatedFormula`` (the live env-59
    shape, probe-verified). ``None`` when absent/uncaptured. D-304 retained it
    unused (the vr-dialect parser could not parse value formulas); the req-302
    robustness arc armed it — governance's formula-expectation verification
    parses it in the VALUE dialect."""
    attrs = attributes or {}
    return attrs.get("formula") or attrs.get("calculatedFormula")


def field_treat_null_as_zero(attributes: Optional[dict]) -> bool:
    """The calculated field's ``formulaTreatNullNumberAsZero`` describe flag —
    the same two-shape-tolerant idiom (D-203.1); the raw describe serializes
    it as the STRING ``"True"``/``"False"`` in the live capture. Missing →
    False (the conservative read: an absent flag never turns nulls into
    zeros)."""
    attrs = attributes or {}
    v = attrs.get("formulaTreatNullNumberAsZero")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return False


def vr_is_active(attributes: Optional[dict]) -> bool:
    """Whether the ValidationRule is ACTIVE on the org — same shape tolerance
    as :func:`vr_formula_text` (D-203.1): designed ``is_active``; raw Tooling
    top-level ``Active``; ``Metadata.active``. **Missing → True** — an
    attribute-less row must not silently demote every negative to caveated
    (the presentation-layer default posture). D-301: an inactive rule cannot
    fire, so it must never ground a prohibition, tie in VR-to-claim alignment,
    or bind an error message."""
    attrs = attributes or {}
    for key in ("is_active", "Active"):
        v = attrs.get(key)
        if v is not None:
            return bool(v)
    metadata = attrs.get("Metadata")
    if isinstance(metadata, dict) and metadata.get("active") is not None:
        return bool(metadata["active"])
    return True


def _flow_md_list(v):
    """A Flow-Metadata list-or-single element → a list. Salesforce serializes a
    one-element collection as a bare dict; ``None``/other → empty. (Non-raising.)"""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v]
    return []


_FLOW_VALUE_SCALARS = ("stringValue", "numberValue", "booleanValue",
                       "dateValue", "dateTimeValue")


def _flow_assignment_value(value):
    """The literal scalar a Flow ``inputAssignment`` writes, out of the
    ``{stringValue|numberValue|booleanValue|dateValue|dateTimeValue|...}`` wrapper.
    An ``elementReference`` / formula assignment (no literal) → ``None`` (the caller
    cannot confirm a literal-value match, so it is treated as no-value). Only a
    hashable SCALAR is returned — a dict/list under a scalar key (malformed / future
    API drift) is treated as no-literal, so the caller can put the pair in a set
    without raising (the never-raises contract)."""
    if not isinstance(value, dict):
        return None
    for k in _FLOW_VALUE_SCALARS:
        v = value.get(k)
        if isinstance(v, (str, int, float, bool)):
            return v
    return None


def flow_effects(attributes: Optional[dict]) -> dict:
    """Parse a record-triggered Flow's raw ``Metadata`` into its normalized EFFECT
    set — what the Flow writes — so the automation-effect resolver can bind the Flow
    by its EFFECT instead of the LLM naming the org's internal Flow (D-318). Two
    effect shapes:

      - **same-record**: ``Metadata.recordUpdates[].inputAssignments[]`` →
        ``(field, value)`` where ``field`` is the BARE api-name (e.g.
        ``Risk_Rating__c``) and ``value`` is the assignment's literal scalar
        (``None`` for a formula/reference assignment with no literal).
      - **cross-object**: ``Metadata.recordCreates[].object`` → the created object's
        api-name (e.g. ``Task``).

    Returns ``{"same_record": frozenset[(field, value|None)],
               "cross_object": frozenset[object_api]}``. Absent / non-dict Metadata
    or any odd nesting → both empty (never raises — the D-203.1 shape-tolerant
    idiom). Values are returned UN-canonicalized: the caller applies the same
    identity canonicalization it uses on the claim's ``expected_value`` so both
    sides compare canonically (governance owns ``_identity_safe``; this stays
    import-free)."""
    attrs = attributes or {}
    md = attrs.get("Metadata")
    if not isinstance(md, dict):
        return {"same_record": frozenset(), "cross_object": frozenset()}
    same_record: set = set()
    for upd in _flow_md_list(md.get("recordUpdates")):
        if not isinstance(upd, dict):
            continue
        for assign in _flow_md_list(upd.get("inputAssignments")):
            if not isinstance(assign, dict):
                continue
            field = assign.get("field")
            if not isinstance(field, str) or not field:
                continue
            same_record.add((field.rsplit(".", 1)[-1],
                             _flow_assignment_value(assign.get("value"))))
    cross_object: set = set()
    for create in _flow_md_list(md.get("recordCreates")):
        if not isinstance(create, dict):
            continue
        obj = create.get("object")
        if isinstance(obj, str) and obj:
            cross_object.add(obj)
    return {"same_record": frozenset(same_record),
            "cross_object": frozenset(cross_object)}


# ----------------------------------------------------------------------
# Flow Behaviour IR (B1 arc) — bounded, behaviour-oriented parse of a
# record-triggered Flow's stored Metadata graph.
# ----------------------------------------------------------------------
#
# ``flow_behaviour`` reads the same raw ``attributes.Metadata`` that
# :func:`flow_effects` glances at, but models BEHAVIOURS — (trigger
# context, guard, effect) units — instead of a flat assignment set. Three
# explicit states preserve honest refusal downstream:
#
#   - **grounded**: the behaviour is fully within the bounded grammar —
#     a before-save, create-firing flow whose ENTIRE immediate path is a
#     chain of parseable single-rule decisions and literal ``$Record``
#     assignments. Only grounded behaviours may feed grounding/binding.
#   - **unsupported**: the parser RECOGNIZED structure that is outside
#     the currently-supported grammar (multi-outcome decisions, formula-
#     valued assignments, data lookups, actions, subflows, update-only
#     triggers, entry conditions, scheduled/async-only paths, …). Named
#     reasons; consumers must refuse, not guess.
#   - **opaque**: the parser could not follow the graph at all (unknown
#     element target, cycle, depth cap, malformed shapes). Consumers must
#     refuse.
#
# Conservatism rule: if ANY element on the walked immediate path falls
# outside the grammar, every behaviour on that path is demoted to
# unsupported — an unparsed later element could overwrite an earlier
# "understood" effect, so partial understanding of a path never grounds.
# (Never-raises; D-203.1 shape tolerance throughout.)

FLOW_BEHAVIOUR_IR_VERSION = 2

_FB_GROUNDED = "grounded"
_FB_UNSUPPORTED = "unsupported"
_FB_OPAQUE = "opaque"

# Decision-condition operators the bounded grammar understands. Kept
# deliberately small: FL-shaped null/equality guards on $Record fields.
_FB_GUARD_OPERATORS = frozenset({
    "IsNull", "EqualTo", "NotEqualTo",
    # C1 (ordered-decision slice): numeric ladder comparisons
    "GreaterThanOrEqualTo", "GreaterThan", "LessThanOrEqualTo", "LessThan"})

# Ordered-decision bounds (C1): first-match fan-out is walked per rule with
# the negation-context of every PRIOR rule; kept small and honest.
_FB_MAX_DECISION_RULES = 6
_FB_MAX_RULE_CONDITIONS = 4

# Flow-Metadata element lists that can appear as walk targets. Anything
# resolved into a list NOT in the supported set → unsupported reason.
_FB_ELEMENT_LISTS = (
    "assignments", "decisions", "recordCreates", "recordUpdates",
    "recordLookups", "recordDeletes", "recordRollbacks", "loops",
    "actionCalls", "subflows", "waits", "screens", "transforms",
    "collectionProcessors", "customErrors", "steps", "apexPluginCalls",
    "orchestratedStages",
)

_FB_MAX_WALK = 24  # generous bound; benchmark paths are <6 elements


def _fb_record_field(ref) -> Optional[str]:
    """``$Record.<Field>`` → bare field api-name; anything else → None."""
    if isinstance(ref, str) and ref.startswith("$Record."):
        rest = ref[len("$Record."):]
        if rest and "." not in rest:
            return rest
    return None


def _fb_guard_condition(cond) -> Optional[tuple]:
    """Parse one decision-rule condition into ``(field, operator, value)``.
    None when outside the bounded grammar (non-$Record left side, unknown
    operator, non-literal right value)."""
    if not isinstance(cond, dict):
        return None
    field = _fb_record_field(cond.get("leftValueReference"))
    operator = cond.get("operator")
    if field is None or operator not in _FB_GUARD_OPERATORS:
        return None
    value = _flow_assignment_value(cond.get("rightValue"))
    if value is None:
        return None
    return (field, operator, value)


# IR v2 (FL02 slice): the bounded TRANSFORM grammar — a formula-valued
# assignment grounds iff its expression is a nest of these string functions
# over ONE ``$Record.<Field>`` argument (e.g. ``UPPER(TRIM({!$Record.X__c}))``).
# Anything else stays ``non_literal_assignment_value`` (unchanged honesty).
_FB_TRANSFORM_FNS = frozenset({"UPPER", "LOWER", "TRIM"})
_FB_TRANSFORM_MAX_DEPTH = 3

_FB_TRANSFORM_CALL_RE = _re.compile(r"^\s*([A-Z]+)\s*\(\s*(.*?)\s*\)\s*$",
                                    _re.S)
_FB_TRANSFORM_ARG_RE = _re.compile(r"^\{!\$Record\.([A-Za-z0-9_]+)\}$")


def _fb_parse_transform_formula(expression) -> Optional[tuple]:
    """Parse a bounded transform formula into ``(chain, source_field)`` where
    ``chain`` is the APPLICATION-ORDER tuple (innermost first — for
    ``UPPER(TRIM(x))`` the chain is ``("TRIM", "UPPER")``) and
    ``source_field`` is the bare ``$Record`` field the nest reads. ``None``
    for anything outside the grammar (unknown function, depth > 3, a
    non-``$Record`` argument, extra arguments)."""
    if not isinstance(expression, str):
        return None
    rest = expression.strip()
    outer_to_inner: list = []
    for _ in range(_FB_TRANSFORM_MAX_DEPTH):
        m = _FB_TRANSFORM_CALL_RE.match(rest)
        if m is None:
            break
        fn, inner = m.group(1), m.group(2)
        if fn not in _FB_TRANSFORM_FNS or "," in inner:
            return None
        outer_to_inner.append(fn)
        rest = inner.strip()
    if not outer_to_inner:
        return None
    arg = _FB_TRANSFORM_ARG_RE.match(rest)
    if arg is None:
        return None
    return (tuple(reversed(outer_to_inner)), arg.group(1))


def apply_transform_chain(chain, value):
    """Apply an application-ordered transform chain to a string value —
    deterministic, the exact semantics Salesforce gives these functions on
    plain strings. Non-string values pass through unchanged (the caller owns
    type guarantees). Shared by the IR's consumers (emission computes the
    expected post-save value; the VR staged-state check evaluates rules on
    the post-transform value, the order-of-execution fact for before-save
    same-field transforms)."""
    if not isinstance(value, str):
        return value
    out = value
    for fn in chain:
        if fn == "UPPER":
            out = out.upper()
        elif fn == "LOWER":
            out = out.lower()
        elif fn == "TRIM":
            out = out.strip()
    return out


def _fb_entry_filter_guard(f) -> Optional[tuple]:
    """Parse one start-element entry filter into a guard tuple. Bounded to
    the ``IsNull`` shape (``{field, operator: "IsNull", value: {booleanValue}}``
    — the field is a BARE api-name on the trigger object). Anything else →
    ``None`` and the flow keeps today's ``entry_conditions_not_consumed``
    demotion."""
    if not isinstance(f, dict):
        return None
    field = f.get("field")
    if not isinstance(field, str) or not field or "." in field:
        return None
    if f.get("operator") != "IsNull":
        return None
    value = f.get("value")
    flag = value.get("booleanValue") if isinstance(value, dict) else None
    if not isinstance(flag, bool):
        return None
    return (field, "IsNull", flag)


def flow_behaviour(attributes: Optional[dict]) -> dict:
    """Parse a Flow's raw ``Metadata`` into the Flow Behaviour IR (v1).

    Returns a plain dict (JSON-shaped, schema-versioned)::

        {"ir_version": 2,
         "trigger": {"object": str|None, "save_phase": "before_save"|"after_save"|None,
                     "record_trigger_type": str|None,
                     "entry_filter_count": int, "requires_change_to_meet": bool,
                     "entry_filters_consumed": bool,   # IR v2 (FL02 slice)
                     "has_scheduled_paths": bool, "has_async_path": bool,
                     "is_record_triggered": bool},
         "behaviours": [
             {"state": "grounded"|"unsupported"|"opaque",
              "kind": "set_record_field"|"set_record_field_transform"|None,
              "guard": [[field, operator, value], ...],   # conjunction; incl.
                                                          # consumed entry filters
              "field": str|None, "value": scalar|None,
              "transform": [fn, ...],       # IR v2: application order (inner
              "source_field": str|None,     # first); () for literal writes
              "reasons": [str, ...]},
         ],
         "parse_status": "full"|"partial"|"opaque"|"empty"}

    IR v2 (the FL02 slice) adds the bounded TRANSFORM grammar: a formula-
    valued assignment grounds as ``set_record_field_transform`` iff its
    expression is a UPPER/LOWER/TRIM nest over exactly one ``$Record`` field;
    entry filters are CONSUMED into behaviour guards when every filter is the
    bounded IsNull shape (else the v1 flow-level demotion stands).

    Never raises. Absent/malformed Metadata → ``parse_status="opaque"`` with
    zero behaviours. The FIRST consumer is the automation-effect binder
    (``governance_core._flows_producing_effect``), which reads only
    ``state == "grounded"`` ``set_record_field`` behaviours; everything else
    exists so refusals stay honest and named."""
    out = {
        "ir_version": FLOW_BEHAVIOUR_IR_VERSION,
        "trigger": {
            "object": None, "save_phase": None, "record_trigger_type": None,
            "entry_filter_count": 0, "requires_change_to_meet": False,
            "entry_filters_consumed": False,
            "has_scheduled_paths": False, "has_async_path": False,
            "is_record_triggered": False,
        },
        "behaviours": [],
        "parse_status": "opaque",
    }
    attrs = attributes or {}
    md = attrs.get("Metadata")
    if not isinstance(md, dict):
        return out
    start = md.get("start")
    if not isinstance(start, dict):
        return out

    trigger_type = start.get("triggerType")
    save_phase = {"RecordBeforeSave": "before_save",
                  "RecordAfterSave": "after_save"}.get(trigger_type)
    scheduled = [p for p in _flow_md_list(start.get("scheduledPaths"))
                 if isinstance(p, dict)]
    trig = out["trigger"]
    trig["object"] = start.get("object") if isinstance(start.get("object"), str) else None
    trig["save_phase"] = save_phase
    trig["record_trigger_type"] = (start.get("recordTriggerType")
                                   if isinstance(start.get("recordTriggerType"), str)
                                   else None)
    trig["entry_filter_count"] = len([f for f in _flow_md_list(start.get("filters"))
                                      if isinstance(f, dict)])
    trig["requires_change_to_meet"] = bool(
        start.get("doesRequireRecordChangedToMeetCriteria"))
    trig["has_async_path"] = any(p.get("pathType") == "AsyncAfterCommit"
                                 for p in scheduled)
    trig["has_scheduled_paths"] = any(p.get("pathType") != "AsyncAfterCommit"
                                      for p in scheduled)
    trig["is_record_triggered"] = save_phase is not None and trig["object"] is not None

    # IR v2: consume entry filters when EVERY one parses in the bounded
    # IsNull shape — they become part of each behaviour's guard (the fire
    # condition the staging must satisfy). Any unparseable filter keeps
    # today's flow-level ``entry_conditions_not_consumed`` demotion.
    entry_filters = [f for f in _flow_md_list(start.get("filters"))
                     if isinstance(f, dict)]
    entry_guard_parsed = [_fb_entry_filter_guard(f) for f in entry_filters]
    entry_guard: tuple = ()
    if entry_filters and all(g is not None for g in entry_guard_parsed) \
            and start.get("filterLogic") in (None, "and"):
        entry_guard = tuple(entry_guard_parsed)
        trig["entry_filters_consumed"] = True

    def _behaviour(state, kind=None, guard=(), field=None, value=None,
                   reasons=(), transform=(), source_field=None, negated=()):
        return {"state": state, "kind": kind,
                "guard": [list(g) for g in entry_guard + tuple(guard)],
                # C1: the negation-context — each entry means NOT(f op v);
                # first-match ordered decisions put every PRIOR rule's
                # condition here for the later arms and the default arm.
                "negated_guards": [list(g) for g in negated],
                "field": field, "value": value,
                "transform": list(transform), "source_field": source_field,
                "reasons": list(reasons)}

    # ── flow-level gates: not record-triggered / no immediate path ──
    if not trig["is_record_triggered"]:
        out["behaviours"].append(_behaviour(
            _FB_UNSUPPORTED, reasons=["not_record_triggered"]))
        out["parse_status"] = "partial"
        return out

    connector = start.get("connector")
    target = (connector or {}).get("targetReference") \
        if isinstance(connector, dict) else None
    if not target:
        reasons = ["no_immediate_path"]
        if trig["has_async_path"]:
            reasons.append("async_path_only")
        if trig["has_scheduled_paths"]:
            reasons.append("scheduled_path_only")
        out["behaviours"].append(_behaviour(_FB_UNSUPPORTED, reasons=reasons))
        out["parse_status"] = "partial"
        return out

    # ── formula index: name → formula element (transform grammar source) ──
    formulas_by_name: dict = {
        f["name"]: f for f in _flow_md_list(md.get("formulas"))
        if isinstance(f, dict) and isinstance(f.get("name"), str)}

    # ── element index: name → (list_name, element) ──
    index: dict = {}
    for list_name in _FB_ELEMENT_LISTS:
        for el in _flow_md_list(md.get(list_name)):
            if isinstance(el, dict) and isinstance(el.get("name"), str):
                index[el["name"]] = (list_name, el)

    # ── bounded walk of the immediate path ──
    #
    # C1 (ordered-decision slice): the walk is a linear stepper that FANS OUT
    # exactly once at a decision — each rule's connector path (and the default
    # path) is walked independently with that arm's guard + the negation-
    # context of every PRIOR rule (first-match semantics made explicit).
    # Conservatism is PER PATH for the fan-out arms: sibling branches are
    # mutually exclusive at run time, so an out-of-grammar element on band 3's
    # path cannot overwrite band 1's effect — band 1 stays grounded while
    # band 3 demotes with its own reasons. Flow-level demotions (filters,
    # phase, trigger type) still apply to every behaviour.
    behaviours: list = []
    demote_reasons: list = []
    opaque_any = False
    grounded_possible = True   # set False only by pre-walk global gates

    def _walk_linear(start_target, guard, negated):
        """Walk one linear path (no further decisions) to completion.
        Returns (path_behaviours, path_reasons, path_opaque)."""
        pb: list = []
        pr: list = []
        p_opaque = False
        seen: set = set()
        steps = 0
        target = start_target
        while target:
            steps += 1
            if steps > _FB_MAX_WALK or target in seen:
                p_opaque = True
                pr.append("walk_bound_exceeded" if steps > _FB_MAX_WALK
                          else "cycle_detected")
                break
            seen.add(target)
            entry = index.get(target)
            if entry is None:
                p_opaque = True
                pr.append(f"unknown_element:{target}")
                break
            list_name, el = entry
            if list_name == "assignments":
                for item in _flow_md_list(el.get("assignmentItems")):
                    if not isinstance(item, dict):
                        pr.append("malformed_assignment_item")
                        continue
                    field = _fb_record_field(item.get("assignToReference"))
                    op = item.get("operator")
                    value = _flow_assignment_value(item.get("value"))
                    reasons = []
                    if field is None:
                        reasons.append("non_record_assignment_target")
                    if op != "Assign":
                        reasons.append(f"non_assign_operator:{op}")
                    transform = None
                    if value is None and not reasons:
                        val = item.get("value")
                        ref = (val or {}).get("elementReference") \
                            if isinstance(val, dict) else None
                        formula = formulas_by_name.get(ref) if ref else None
                        transform = _fb_parse_transform_formula(
                            (formula or {}).get("expression"))
                    if value is None and transform is None:
                        reasons.append("non_literal_assignment_value")
                    if reasons:
                        pb.append(_behaviour(
                            _FB_UNSUPPORTED, kind="set_record_field",
                            guard=guard, negated=negated, field=field,
                            reasons=reasons))
                        pr.extend(reasons)
                    elif transform is not None:
                        chain, source_field = transform
                        pb.append(_behaviour(
                            _FB_GROUNDED, kind="set_record_field_transform",
                            guard=guard, negated=negated, field=field,
                            transform=chain, source_field=source_field))
                    else:
                        pb.append(_behaviour(
                            _FB_GROUNDED, kind="set_record_field",
                            guard=guard, negated=negated, field=field,
                            value=value))
                connector = el.get("connector")
                target = (connector or {}).get("targetReference") \
                    if isinstance(connector, dict) else None
            elif list_name == "decisions":
                # a SECOND decision on any path is outside the grammar
                pr.append("nested_decision")
                pb.append(_behaviour(
                    _FB_UNSUPPORTED, guard=guard, negated=negated,
                    reasons=["nested_decision"]))
                break
            else:
                reason = f"element_outside_grammar:{list_name}"
                pr.append(reason)
                pb.append(_behaviour(_FB_UNSUPPORTED, guard=guard,
                                     negated=negated, reasons=[reason]))
                break
        # per-path conservatism: any reason on this path demotes ITS grounded
        # behaviours (a later element on the SAME path could overwrite)
        if pr:
            state = _FB_OPAQUE if p_opaque else _FB_UNSUPPORTED
            for b in pb:
                if b["state"] == _FB_GROUNDED:
                    b["state"] = state
                b["reasons"] = sorted(set(b["reasons"]) | set(pr))
        return pb, pr, p_opaque

    def _parse_rule(rule):
        """One decision rule -> (conds tuple) or (None) when out of grammar."""
        logic = rule.get("conditionLogic")
        conds = [c for c in _flow_md_list(rule.get("conditions"))
                 if isinstance(c, dict)]
        if logic not in (None, "and") or not conds \
                or len(conds) > _FB_MAX_RULE_CONDITIONS:
            return None
        parsed = [_fb_guard_condition(c) for c in conds]
        if any(pc is None for pc in parsed):
            return None
        return tuple(parsed)

    # step to (at most) the first decision; then fan out
    pre_behaviours: list = []
    seen0: set = set()
    steps0 = 0
    while target:
        steps0 += 1
        if steps0 > _FB_MAX_WALK or target in seen0:
            opaque_any = True
            demote_reasons.append("walk_bound_exceeded" if steps0 > _FB_MAX_WALK
                                  else "cycle_detected")
            target = None
            break
        seen0.add(target)
        entry = index.get(target)
        if entry is None:
            opaque_any = True
            demote_reasons.append(f"unknown_element:{target}")
            target = None
            break
        list_name, el = entry
        if list_name == "assignments":
            # an unconditional assignment BEFORE any decision — walk it via
            # the linear walker from here and stop (it consumes the rest).
            pb, pr, p_op = _walk_linear(target, (), ())
            pre_behaviours.extend(pb)
            demote_reasons.extend(pr)
            opaque_any = opaque_any or p_op
            target = None
        elif list_name == "decisions":
            rules = [r for r in _flow_md_list(el.get("rules"))
                     if isinstance(r, dict)]
            default_conn = el.get("defaultConnector")
            default_target = (default_conn or {}).get("targetReference") \
                if isinstance(default_conn, dict) else None
            if not rules:
                demote_reasons.append("decision_without_rules")
                pre_behaviours.append(_behaviour(
                    _FB_UNSUPPORTED, reasons=["decision_without_rules"]))
                target = None
                break
            if len(rules) > _FB_MAX_DECISION_RULES:
                demote_reasons.append("decision_rule_bound_exceeded")
                pre_behaviours.append(_behaviour(
                    _FB_UNSUPPORTED, reasons=["decision_rule_bound_exceeded"]))
                target = None
                break
            parsed_rules = [_parse_rule(r) for r in rules]
            if any(pr_ is None for pr_ in parsed_rules):
                demote_reasons.append("unparseable_guard_condition")
                pre_behaviours.append(_behaviour(
                    _FB_UNSUPPORTED, reasons=["unparseable_guard_condition"]))
                target = None
                break
            # C1: first-match negation-context is sound only when every
            # PRIOR rule is single-condition (¬(A∧B) is a disjunction the
            # guard grammar does not carry). Multi-rule decisions therefore
            # require all rules single-condition; the single-rule decision
            # keeps the conjunctive grammar (FL01 unchanged).
            if len(rules) > 1 and any(len(pr_) != 1 for pr_ in parsed_rules):
                demote_reasons.append(
                    "multi_condition_rule_in_ordered_decision")
                pre_behaviours.append(_behaviour(
                    _FB_UNSUPPORTED,
                    reasons=["multi_condition_rule_in_ordered_decision"]))
                target = None
                break
            arms = []
            for i, rule in enumerate(rules):
                rule_conn = rule.get("connector")
                arm_target = (rule_conn or {}).get("targetReference") \
                    if isinstance(rule_conn, dict) else None
                negated = tuple(pr_[0] for pr_ in parsed_rules[:i])
                arms.append((arm_target, parsed_rules[i], negated))
            if default_target:
                arms.append((default_target,
                             (),
                             tuple(pr_[0] for pr_ in parsed_rules)
                             if all(len(pr_) == 1 for pr_ in parsed_rules)
                             else None))
            for arm_target, arm_guard, arm_negated in arms:
                if arm_negated is None:
                    demote_reasons.append(
                        "multi_condition_rule_in_ordered_decision")
                    pre_behaviours.append(_behaviour(
                        _FB_UNSUPPORTED,
                        reasons=["multi_condition_rule_in_ordered_decision"]))
                    continue
                if not arm_target:
                    continue        # an armless rule writes nothing
                pb, pr, p_op = _walk_linear(arm_target, arm_guard, arm_negated)
                behaviours.extend(pb)
                opaque_any = opaque_any or p_op
            target = None
        else:
            # A recognized element type outside the bounded grammar.
            reason = f"element_outside_grammar:{list_name}"
            demote_reasons.append(reason)
            pre_behaviours.append(_behaviour(_FB_UNSUPPORTED, reasons=[reason]))
            target = None

    # unconditional-path behaviours join the fan-out arms' behaviours
    behaviours = pre_behaviours + behaviours

    # ── flow-level demotions of otherwise-grounded behaviours ──
    if trig["entry_filter_count"] and not trig["entry_filters_consumed"]:
        demote_reasons.append("entry_conditions_not_consumed")
    if trig["requires_change_to_meet"]:
        demote_reasons.append("updated_to_meet_not_consumed")
    # C2 (after-save admission): the save phase is no longer a flow-level
    # demotion — S4 reads post-commit, so after-save writes are observable
    # exactly like before-save ones. The phase remains a grounding PROPERTY:
    # the transform projection enforces before_save itself (an after-save
    # transform's raw witness would face the validation rules BEFORE the
    # flow runs — unconstructible), and future order-of-execution reasoning
    # (B3) reads trigger.save_phase.
    if trig["record_trigger_type"] not in ("Create", "CreateAndUpdate"):
        demote_reasons.append(
            f"trigger_type_not_in_grammar:{trig['record_trigger_type']}")

    if demote_reasons:
        # GLOBAL demotions (flow-level gates + the pre-decision segment):
        # per-path reasons were already applied inside _walk_linear — sibling
        # fan-out arms are mutually exclusive at run time, so they demote
        # independently (the C1 refinement of the conservatism rule).
        state = _FB_OPAQUE if opaque_any else _FB_UNSUPPORTED
        if not behaviours:
            # An anomalous walk that produced no behaviours must still be
            # visible (an unknown target/cycle is not an "empty" flow).
            behaviours.append(_behaviour(state, reasons=demote_reasons))
        for b in behaviours:
            if b["state"] == _FB_GROUNDED:
                b["state"] = state
            elif opaque_any and b["state"] == _FB_UNSUPPORTED:
                b["state"] = _FB_OPAQUE
            # Flow-level demotions are facts about every behaviour, not just
            # the previously-grounded ones.
            b["reasons"] = sorted(set(b["reasons"]) | set(demote_reasons))

    if not behaviours:
        out["behaviours"] = []
        out["parse_status"] = "empty"
        return out
    out["behaviours"] = behaviours
    states = {b["state"] for b in behaviours}
    if states == {_FB_GROUNDED}:
        out["parse_status"] = "full"
    elif _FB_GROUNDED not in states and (opaque_any or states == {_FB_OPAQUE}):
        # opacity with NO surviving grounded arm — nothing usable
        out["parse_status"] = "opaque"
    else:
        # mixed: grounded arms survive beside demoted siblings (C1), or
        # pure-unsupported (the pre-C1 partial)
        out["parse_status"] = "partial"
    return out


def flow_grounded_transforms(attributes: Optional[dict]) -> tuple:
    """The binder-facing projection of the IR's GROUNDED transform behaviours
    (IR v2 / FL02 slice): a deterministic tuple of dicts
    ``{"field", "transform", "source_field", "guard"}`` — the field the Flow
    verifiably REWRITES on the triggering record, the application-order
    function chain, the field it reads, and the fire guard (consumed entry
    filters + decision guard). Unsupported/opaque behaviours contribute
    nothing (honest refusal upstream). BEFORE-SAVE ONLY (enforced here since
    C2 admitted after-save flows to the walk): an after-save transform's raw
    witness would face the validation rules BEFORE the flow runs, so no
    passing test is constructible — the run-before-validation-rules
    order-of-execution fact this projection's consumers rely on holds by
    construction."""
    ir = flow_behaviour(attributes)
    if ir["trigger"]["save_phase"] != "before_save":
        return ()
    out = [
        {"field": b["field"], "transform": tuple(b["transform"]),
         "source_field": b["source_field"],
         "guard": tuple(tuple(g) for g in b["guard"])}
        for b in ir["behaviours"]
        if b["state"] == _FB_GROUNDED
        and b["kind"] == "set_record_field_transform"
        and b["field"] is not None
    ]
    out.sort(key=lambda d: (d["field"], d["transform"]))
    return tuple(out)


def flow_grounded_same_record_effects(attributes: Optional[dict]) -> frozenset:
    """The binder-facing projection of :func:`flow_behaviour`: the
    ``(bare_field, literal_value)`` pairs this Flow VERIFIABLY writes to the
    triggering record via GROUNDED behaviours only. Unsupported/opaque
    behaviours contribute nothing — honest refusal is preserved upstream.
    Shape-compatible with ``flow_effects()["same_record"]`` so the
    automation-effect binder consumes both with one code path."""
    ir = flow_behaviour(attributes)
    return frozenset(
        (b["field"], b["value"]) for b in ir["behaviours"]
        if b["state"] == _FB_GROUNDED and b["kind"] == "set_record_field"
        and b["field"] is not None
    )


def flow_grounded_guarded_effects(attributes: Optional[dict]) -> tuple:
    """The guard-aware sibling of :func:`flow_grounded_same_record_effects`
    (C3): each GROUNDED literal behaviour with its full fire condition —
    ``{"field", "value", "guard", "negated_guards"}``, both condition sets as
    tuples of ``(field, operator, value)`` triples. Order is the IR's walk
    order (for an ordered decision, rule order — the first-match fact).
    Consumers derive the create state that makes a SPECIFIC arm fire; the
    frozenset projection remains the binder's match surface."""
    ir = flow_behaviour(attributes)
    return tuple(
        {"field": b["field"], "value": b["value"],
         "guard": tuple(tuple(g) for g in b["guard"]),
         "negated_guards": tuple(tuple(g) for g in b["negated_guards"])}
        for b in ir["behaviours"]
        if b["state"] == _FB_GROUNDED and b["kind"] == "set_record_field"
        and b["field"] is not None
    )
