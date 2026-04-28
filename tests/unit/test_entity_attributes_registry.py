"""Registry round-trip tests for primeqa.semantic.entity_attributes.

Test scaffolding ground-truthed against actual schemas.
Per inspection:
- Most schemas have all-None defaults
- ObjectAttributes has 7 booleans defaulting False, 1 (is_retrievable)
  defaulting True, plus 2 None strings (plural_label, description)
- FieldAttributes has 5 booleans defaulting False, plus 6 None strings
- No 'label' field on any schema; ObjectAttributes uses 'plural_label'
"""
import json
import pytest

from primeqa.semantic.entity_attributes import (
    TIER_1_ENTITIES,
    validate_entity_attributes,
    get_entity_metadata,
)
from pydantic import ValidationError


pytestmark = pytest.mark.unit


EXPECTED_ENTITY_TYPES = {
    "Object", "Field", "RecordType", "Layout", "PicklistValue",
    "Profile", "PermissionSet", "User", "Flow", "ValidationRule",
}

# Entity types whose schemas have all-None defaults (per inspection)
ENTITY_TYPES_ALL_NONE_DEFAULTS = {
    "Flow", "Layout", "PermissionSet", "PicklistValue",
    "Profile", "RecordType", "User", "ValidationRule",
}


class TestRegistryCompleteness:
    def test_all_tier_1_types_registered(self):
        assert set(TIER_1_ENTITIES.keys()) == EXPECTED_ENTITY_TYPES

    def test_get_entity_metadata_resolves_each(self):
        for et in EXPECTED_ENTITY_TYPES:
            meta = get_entity_metadata(et)
            assert meta.attributes_schema is not None
            assert meta.detail_table is not None

    def test_unknown_entity_type_raises(self):
        with pytest.raises(Exception):
            get_entity_metadata("NotARealType")


class TestEmptyDictValidation:
    """Every entity_type validates with empty dict; defaults vary by schema."""

    @pytest.mark.parametrize("entity_type", sorted(EXPECTED_ENTITY_TYPES))
    def test_empty_dict_validates_to_dict(self, entity_type):
        result = validate_entity_attributes(entity_type, {})
        assert isinstance(result, dict)

    @pytest.mark.parametrize("entity_type", sorted(ENTITY_TYPES_ALL_NONE_DEFAULTS))
    def test_all_none_default_schemas(self, entity_type):
        result = validate_entity_attributes(entity_type, {})
        for key, value in result.items():
            assert value is None, \
                f"{entity_type}.{key} expected None, got {value!r}"

    def test_object_has_boolean_defaults(self):
        # ObjectAttributes: 7 booleans default False, is_retrievable True
        result = validate_entity_attributes("Object", {})
        # Must have these keys with these specific defaults
        assert result["is_retrievable"] is True
        # Spot-check — at least one False default exists
        false_defaults = [k for k, v in result.items() if v is False]
        assert len(false_defaults) >= 5, \
            f"Object expected several False booleans, got: {false_defaults}"

    def test_field_has_boolean_defaults(self):
        # FieldAttributes: 5 booleans default False
        result = validate_entity_attributes("Field", {})
        false_defaults = [k for k, v in result.items() if v is False]
        assert len(false_defaults) >= 4, \
            f"Field expected several False booleans, got: {false_defaults}"


class TestUnknownAttributeRejection:
    """Every entity_type rejects unknown attributes (extra='forbid')."""

    @pytest.mark.parametrize("entity_type", sorted(EXPECTED_ENTITY_TYPES))
    def test_unknown_attr_raises(self, entity_type):
        with pytest.raises(ValidationError):
            validate_entity_attributes(entity_type, {"made_up_field_xyz": "value"})


class TestJSONRoundTrip:
    """Validated dict survives JSON encode/decode unchanged."""

    @pytest.mark.parametrize("entity_type", sorted(EXPECTED_ENTITY_TYPES))
    def test_empty_dict_round_trip(self, entity_type):
        result = validate_entity_attributes(entity_type, {})
        decoded = json.loads(json.dumps(result))
        assert decoded == result


class TestSpecificFieldCaps:
    """Spot-check field length caps using REAL field names from inspection."""

    def test_object_plural_label_cap(self):
        # ObjectAttributes.plural_label is max 255 per inspection
        with pytest.raises(ValidationError):
            validate_entity_attributes("Object", {"plural_label": "x" * 300})

    def test_object_description_cap(self):
        # ObjectAttributes.description is max 4000
        with pytest.raises(ValidationError):
            validate_entity_attributes("Object", {"description": "x" * 5000})

    def test_validation_rule_formula_text_cap(self):
        # ValidationRuleAttributes.formula_text is max 8000
        with pytest.raises(ValidationError):
            validate_entity_attributes("ValidationRule", {"formula_text": "x" * 9000})

    def test_flow_description_cap(self):
        # FlowAttributes.description is max 255
        with pytest.raises(ValidationError):
            validate_entity_attributes("Flow", {"description": "x" * 300})


class TestPartialDictPreservesDefaults:
    """Setting one attribute leaves others at their default value."""

    def test_object_with_plural_label(self):
        result = validate_entity_attributes("Object", {"plural_label": "Accounts"})
        assert result["plural_label"] == "Accounts"
        # is_retrievable should still be True, others False or None
        assert result["is_retrievable"] is True

    def test_validation_rule_with_error_message(self):
        result = validate_entity_attributes("ValidationRule", {"error_message": "Required"})
        assert result["error_message"] == "Required"
        assert result.get("formula_text") is None

    def test_flow_with_description(self):
        result = validate_entity_attributes("Flow", {"description": "My Flow"})
        assert result["description"] == "My Flow"
        assert result.get("process_type") is None
