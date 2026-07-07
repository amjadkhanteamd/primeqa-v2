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
