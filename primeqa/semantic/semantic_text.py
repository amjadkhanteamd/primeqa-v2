"""Substrate 1 Phase 2 — per-entity-type deterministic semantic-text templating (per D-046).

Companion to normalization.py:
  - normalize() + hash_normalized() produce the change-detection signal
  - to_semantic_text() produces the embedding input

D-046: AI for translation, not invention. semantic_text is templated
deterministically from the entity's clean-shape dict. No LLM call here.
The downstream embedding model (D-043 OpenAI text-embedding-3-small)
turns this string into the vector that lives on entities.embedding.

Same input always produces same output. Stable across Python runs.

Public API:
  to_semantic_text(entity_type, entity_data) -> str

Input contract (`entity_data`):
  A clean-shape dict produced by the sync layer (Phase 2 step 4.6).
  This is NOT the raw Salesforce describe response. It combines:
    - Hot columns from detail tables (e.g., field_type, is_active)
    - Sparse JSONB attributes (e.g., description, formula_text)
    - Resolved names for entity_id FKs (e.g., 'object_name' rather
      than 'object_entity_id')
    - Derived counts/lists computed from edges (e.g., 'obj_perm_count')

  All fields treated as optional. Missing or None values produce
  graceful defaults like "no description provided" rather than
  literal "None" in the output.

Output:
  Natural-language string, target 50-200 tokens, capped under 1000
  characters in practice. Suitable as input to an embedding model.
"""
from __future__ import annotations

from typing import Any


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _yes_no(value: Any, yes: str = "yes", no: str = "no") -> str:
    """Render a boolean value as a word. None or non-bool returns no."""
    if value is True:
        return yes
    return no


def _custom_word(is_custom: Any) -> str:
    return "custom" if is_custom is True else "standard"


def _str_or_default(value: Any, default: str) -> str:
    """Return value as string if non-empty, else the default."""
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    return s


def _strip_terminal_punct(text: str) -> str:
    """Strip trailing punctuation (.!?) from text, stripping whitespace
    first. Returns empty string if input is None or empty.

    Used for user-content values embedded in template positions where the
    template adds its own terminating period, to avoid double-period
    artifacts like "Description: Account object..".
    """
    if not text:
        return ""
    return text.rstrip().rstrip(".!?").rstrip()


def _str_or_default_clean(value: Any, default: str) -> str:
    """_str_or_default + terminal-punct strip on real (non-default) values.

    Use for fields embedded in templates that add a terminating period.
    Defaults like 'no description provided' are punctuation-free, so the
    strip is safe to apply uniformly.
    """
    return _strip_terminal_punct(_str_or_default(value, default))


def _join_or_default(items: Any, default: str, max_items: int = 10) -> str:
    """Render a list as 'a, b, c' with truncation. Empty/None → default."""
    if not items:
        return default
    if not isinstance(items, list):
        return default
    str_items = [str(x) for x in items if x is not None and str(x).strip()]
    if not str_items:
        return default
    if len(str_items) <= max_items:
        return ", ".join(str_items)
    return ", ".join(str_items[:max_items]) + f", and {len(str_items) - max_items} more"


# ----------------------------------------------------------------------
# Per-type templating
# ----------------------------------------------------------------------

def _to_text_object(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    label = _str_or_default(d.get("label"), name)
    is_custom_word = _custom_word(d.get("is_custom"))
    description = _str_or_default_clean(d.get("description"), "no description provided")
    key_fields = _strip_terminal_punct(
        _join_or_default(d.get("key_field_names"), "none listed")
    )
    return (
        f"Salesforce Object '{name}' (label: '{label}'). "
        f"Custom: {is_custom_word}. "
        f"Description: {description}. "
        f"Key fields: {key_fields}."
    )


def _to_text_field(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    object_name = _str_or_default(d.get("object_name"), "(unknown object)")
    field_type = _str_or_default(d.get("type"), "(unknown type)")
    label = _str_or_default(d.get("label"), name)
    help_text = _str_or_default_clean(d.get("help_text"), "no help text provided")
    is_custom_word = "Custom field" if d.get("is_custom") is True else "Standard field"
    required_word = "Required" if d.get("is_required") is True else "Optional"

    # Reference / picklist clause
    ref_clause = ""
    if field_type == "reference":
        ref_to = d.get("reference_to")
        if ref_to:
            ref_clause = f" References {ref_to}."
    elif field_type == "picklist":
        pvs = d.get("picklist_value_set_name")
        if pvs:
            ref_clause = f" Allowed values managed by global value set '{pvs}'."

    return (
        f"Salesforce Field '{name}' on {object_name}. "
        f"Type: {field_type}. "
        f"Label: '{label}'. "
        f"Help text: {help_text}. "
        f"{is_custom_word}. {required_word}.{ref_clause}"
    )


def _to_text_record_type(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    object_name = _str_or_default(d.get("object_name"), "(unknown object)")
    label = _str_or_default(d.get("label"), name)
    is_active_word = _yes_no(d.get("is_active"))
    description = _str_or_default_clean(d.get("description"), "no description provided")
    return (
        f"Salesforce RecordType '{name}' on {object_name} (label: '{label}'). "
        f"Active: {is_active_word}. "
        f"Description: {description}."
    )


def _to_text_layout(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    object_name = _str_or_default(d.get("object_name"), "(unknown object)")
    section_count = d.get("section_count")
    if not isinstance(section_count, int) or section_count < 0:
        section_count = 0
    section_names = d.get("section_names") or []
    if isinstance(section_names, list):
        sorted_names = sorted(str(n) for n in section_names if n is not None)
    else:
        sorted_names = []
    section_names_joined = _strip_terminal_punct(
        _join_or_default(sorted_names, "none listed")
    )
    return (
        f"Salesforce Layout '{name}' on {object_name}. "
        f"Sections: {section_count}. "
        f"Section names: {section_names_joined}."
    )


def _to_text_picklist_value_set(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    label = _str_or_default(d.get("label"), name)
    restricted_word = _yes_no(d.get("is_restricted"))
    global_word = _yes_no(d.get("is_global_value_set"))
    description = _str_or_default_clean(d.get("description"), "no description provided")
    return (
        f"Salesforce PicklistValueSet '{name}' (label: '{label}'). "
        f"Restricted: {restricted_word}. Global: {global_word}. "
        f"Description: {description}."
    )


def _to_text_picklist_value(d: dict[str, Any]) -> str:
    value = _str_or_default(d.get("value"), "(unnamed)")
    set_name = _str_or_default(d.get("set_name"), "(unknown set)")
    is_active_word = _yes_no(d.get("is_active"))
    is_default_word = _yes_no(d.get("is_default"))
    return (
        f"Salesforce PicklistValue '{value}' in set '{set_name}'. "
        f"Active: {is_active_word}. Default: {is_default_word}."
    )


def _to_text_profile(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    license_ = _str_or_default(d.get("license"), "no license listed")
    description = _str_or_default_clean(d.get("description"), "no description provided")
    obj_perm = d.get("obj_perm_count")
    if not isinstance(obj_perm, int) or obj_perm < 0:
        obj_perm = 0
    field_perm = d.get("field_perm_count")
    if not isinstance(field_perm, int) or field_perm < 0:
        field_perm = 0
    return (
        f"Salesforce Profile '{name}'. User license: {license_}. "
        f"Description: {description}. "
        f"Object permissions count: {obj_perm}. "
        f"Field permissions count: {field_perm}."
    )


def _to_text_permission_set(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    license_ = _str_or_default(d.get("license"), "no license listed")
    description = _str_or_default_clean(d.get("description"), "no description provided")
    obj_perm = d.get("obj_perm_count")
    if not isinstance(obj_perm, int) or obj_perm < 0:
        obj_perm = 0
    field_perm = d.get("field_perm_count")
    if not isinstance(field_perm, int) or field_perm < 0:
        field_perm = 0
    parent = d.get("parent_profile")
    parent_clause = ""
    if parent:
        parent_clause = f" Parent profile: '{parent}'."
    return (
        f"Salesforce PermissionSet '{name}'. User license: {license_}. "
        f"Description: {description}.{parent_clause} "
        f"Object permissions count: {obj_perm}. "
        f"Field permissions count: {field_perm}."
    )


def _to_text_user(d: dict[str, Any]) -> str:
    username = _str_or_default(d.get("username"), "(no username)")
    full_name = _str_or_default(d.get("full_name"), "(no full name)")
    profile_name = _str_or_default(d.get("profile_name"), "no profile listed")
    is_active_word = _yes_no(d.get("is_active"))
    email = _str_or_default(d.get("email"), "no email provided")
    return (
        f"Salesforce User '{username}' (full name: '{full_name}'). "
        f"Profile: {profile_name}. Active: {is_active_word}. "
        f"Email: {email}."
    )


def _to_text_validation_rule(d: dict[str, Any]) -> str:
    """Most important type for downstream attribution: Substrate 4
    matches Salesforce error responses against these. Include the
    formula text verbatim — it's the high-signal content for retrieval."""
    name = _str_or_default(d.get("name"), "(unnamed)")
    object_name = _str_or_default(d.get("object_name"), "(unknown object)")
    is_active_word = _yes_no(d.get("is_active"))
    error_message = _str_or_default_clean(d.get("error_message"), "no error message provided")
    formula_text = _str_or_default_clean(d.get("formula_text"), "no formula provided")
    error_display_field = _str_or_default(
        d.get("error_display_field"), "top of page"
    )
    return (
        f"Salesforce ValidationRule '{name}' on {object_name}. "
        f"Active: {is_active_word}. "
        f"Error message: '{error_message}'. "
        f"Formula: {formula_text}. "
        f"Error display field: {error_display_field}."
    )


def _to_text_flow(d: dict[str, Any]) -> str:
    name = _str_or_default(d.get("name"), "(unnamed)")
    process_type = _str_or_default(d.get("process_type"), "(unknown type)")
    is_active_word = _yes_no(d.get("is_active"))
    description = _str_or_default_clean(d.get("description"), "no description provided")
    triggers_on = _str_or_default(d.get("triggers_on_object"), "none")
    trigger_type = _str_or_default(d.get("trigger_type"), "none")
    return (
        f"Salesforce Flow '{name}'. Type: {process_type}. "
        f"Active: {is_active_word}. "
        f"Description: {description}. "
        f"Triggers on: {triggers_on}. "
        f"Trigger type: {trigger_type}."
    )


def _to_text_approval_process(d: dict[str, Any]) -> str:
    """D-308: the approval-process definition in plain words."""
    name = _str_or_default(d.get("name"), "(unnamed)")
    is_active_word = _yes_no(d.get("is_active"))
    target = _str_or_default(d.get("target_object"), "(unknown object)")
    description = _str_or_default_clean(
        d.get("description"), "no description provided")
    return (
        f"Salesforce Approval Process '{name}'. "
        f"Active: {is_active_word}. "
        f"Runs on: {target}. "
        f"Description: {description}."
    )


def _to_text_lwc_bundle(d: dict[str, Any]) -> str:
    """3A-5: the Lightning component bundle in plain words."""
    name = _str_or_default(d.get("name"), "(unnamed)")
    description = _str_or_default_clean(
        d.get("description"), "no description provided")
    files = d.get("files") or []
    file_list = ", ".join(f for f in files if f) or "(no source files)"
    return (
        f"Lightning web component bundle '{name}'. "
        f"Description: {description}. "
        f"Source files: {file_list}."
    )


# ----------------------------------------------------------------------
# Dispatcher
# ----------------------------------------------------------------------

_TEMPLATERS: dict[str, Any] = {
    "Object":           _to_text_object,
    "Field":            _to_text_field,
    "RecordType":       _to_text_record_type,
    "Layout":           _to_text_layout,
    "PicklistValueSet": _to_text_picklist_value_set,
    "PicklistValue":    _to_text_picklist_value,
    "Profile":          _to_text_profile,
    "PermissionSet":    _to_text_permission_set,
    "User":             _to_text_user,
    "ValidationRule":   _to_text_validation_rule,
    "Flow":             _to_text_flow,
    "ApprovalProcess":  _to_text_approval_process,   # D-308
    "LightningComponentBundle": _to_text_lwc_bundle,  # 3A-5
}


def to_semantic_text(entity_type: str, entity_data: dict[str, Any]) -> str:
    """Produce a natural-language representation of the entity.

    Output is suitable as input to an embedding model. Deterministic:
    same input always produces same output. No LLM calls.

    Raises ValueError if entity_type is unknown.
    """
    fn = _TEMPLATERS.get(entity_type)
    if fn is None:
        raise ValueError(
            f"Unknown entity_type: {entity_type!r}. "
            f"Known types: {sorted(_TEMPLATERS.keys())}"
        )
    return fn(entity_data)
