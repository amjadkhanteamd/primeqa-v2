"""Tests for coverage extraction.

Per SPEC §4.5 + §4.7.6 step 9 + D-058 §5.4. Verifies the walk
finds every :class:`IdentityBearingRef` in identity-bearing
content, classifies refs from ``asserted_truth`` as
``"subject"`` and refs from ``semantic_conditions`` as
``"condition"``, deduplicates by
``(entity_type, entity_id, reference_kind)``, and orders the
output deterministically.

Covers the four data-behavior claim bodies (B-β) plus the
shared conditions body — every body class whose canonicalizer
or coverage path was exercised in Track C.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from primeqa.test_representation import (
    AutomationEffectClaimBody,
    Condition,
    CoverageEntry,
    EventDescriptor,
    FieldChangeEffect,
    IdentityBearingRef,
    LiteralValue,
    LogicalRef,
    NullValue,
    PinnedRef,
    ProhibitionClaimBody,
    RejectionSignal,
    SemanticConditionsBody,
    StateDescriptor,
    StateTransitionClaimBody,
    SubstrateError,
    ValueClaimBody,
    extract_coverage,
)


# Stable UUIDs for clear test wiring.
EID_FIELD = UUID("00000000-0000-0000-0000-000000000001")
EID_FIELD_2 = UUID("00000000-0000-0000-0000-000000000002")
EID_OBJECT = UUID("00000000-0000-0000-0000-000000000010")
EID_FLOW = UUID("00000000-0000-0000-0000-000000000020")


def _ib(
    entity_type: str = "Field",
    entity_id: UUID = EID_FIELD,
    external_id: str = "Account.Industry",
) -> IdentityBearingRef:
    return IdentityBearingRef(
        entity_type=entity_type,
        entity_id=entity_id,
        version_seq=1,
        external_id=external_id,
    )


def _empty_conditions() -> SemanticConditionsBody:
    return SemanticConditionsBody()


# ---------------------------------------------------------------------------
# Single-body extraction per claim kind
# ---------------------------------------------------------------------------

class TestExtractCoverageValueClaim:
    def test_value_claim_subject_emitted(self) -> None:
        body = ValueClaimBody(
            subject=_ib(),
            expected_value=LiteralValue(value="Tech"),
        )
        entries = extract_coverage(body, _empty_conditions())
        assert entries == [
            CoverageEntry(
                entity_type="Field",
                entity_id=EID_FIELD,
                reference_kind="subject",
            ),
        ]


class TestExtractCoverageStateTransition:
    def test_subject_and_subject_fields_emitted(self) -> None:
        body = StateTransitionClaimBody(
            subject=_ib(entity_type="Object", entity_id=EID_OBJECT,
                        external_id="Opportunity"),
            subject_fields=[
                _ib(entity_type="Field", entity_id=EID_FIELD,
                    external_id="Opportunity.StageName"),
                _ib(entity_type="Field", entity_id=EID_FIELD_2,
                    external_id="Opportunity.CloseDate"),
            ],
            from_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="Prospecting"),
                "Opportunity.CloseDate": NullValue(),
            }),
            to_state=StateDescriptor(field_values={
                "Opportunity.StageName": LiteralValue(value="Closed Won"),
                "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
            }),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger",
                description="click close",
            ),
        )
        entries = extract_coverage(body, _empty_conditions())
        # All three (one Object + two Fields) emitted as subject:
        entity_ids = {e.entity_id for e in entries}
        assert entity_ids == {EID_OBJECT, EID_FIELD, EID_FIELD_2}
        assert all(e.reference_kind == "subject" for e in entries)
        # Object and Field both present (different entity_types):
        entity_types = {e.entity_type for e in entries}
        assert entity_types == {"Object", "Field"}


class TestExtractCoverageAutomationEffect:
    def test_automation_and_affected_fields_emitted(self) -> None:
        body = AutomationEffectClaimBody(
            automation=_ib(
                entity_type="Flow", entity_id=EID_FLOW,
                external_id="StampCloseDate",
            ),
            automation_primitive="flow",
            triggering_action=EventDescriptor(
                trigger_kind="data-mutation-trigger",
                description="record update",
            ),
            expected_effect=FieldChangeEffect(
                changes=StateDescriptor(field_values={
                    "Opportunity.CloseDate": LiteralValue(value="2026-12-31"),
                }),
            ),
            affected_fields=[
                _ib(entity_type="Field", entity_id=EID_FIELD_2,
                    external_id="Opportunity.CloseDate"),
            ],
        )
        entries = extract_coverage(body, _empty_conditions())
        entity_ids = {e.entity_id for e in entries}
        assert entity_ids == {EID_FLOW, EID_FIELD_2}
        assert all(e.reference_kind == "subject" for e in entries)


class TestExtractCoverageProhibition:
    def test_target_only_when_no_error_field(self) -> None:
        body = ProhibitionClaimBody(
            target=_ib(entity_type="Object", entity_id=EID_OBJECT,
                       external_id="Opportunity"),
            operation="delete",
            prohibition_mechanism="validation_rule",
            expected_rejection=RejectionSignal(error_code="X"),
        )
        entries = extract_coverage(body, _empty_conditions())
        entity_ids = {e.entity_id for e in entries}
        assert entity_ids == {EID_OBJECT}

    def test_target_and_error_field_when_present(self) -> None:
        body = ProhibitionClaimBody(
            target=_ib(entity_type="Object", entity_id=EID_OBJECT,
                       external_id="Opportunity"),
            operation="modify_field",
            prohibition_mechanism="validation_rule",
            expected_rejection=RejectionSignal(
                error_code="X",
                error_field=_ib(
                    entity_type="Field", entity_id=EID_FIELD,
                    external_id="Opportunity.StageName",
                ),
            ),
        )
        entries = extract_coverage(body, _empty_conditions())
        entity_ids = {e.entity_id for e in entries}
        assert entity_ids == {EID_OBJECT, EID_FIELD}


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

class TestExtractCoverageConditions:
    def test_empty_conditions_emits_no_condition_refs(self) -> None:
        body = ValueClaimBody(
            subject=_ib(), expected_value=LiteralValue(value="x"),
        )
        entries = extract_coverage(body, SemanticConditionsBody())
        assert all(e.reference_kind == "subject" for e in entries)

    def test_each_condition_subject_emitted_as_condition(self) -> None:
        body = ValueClaimBody(
            subject=_ib(entity_id=EID_FIELD),
            expected_value=LiteralValue(value="x"),
        )
        conditions = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(entity_id=EID_FIELD_2,
                             external_id="Account.Owner"),
                predicate="is_not_null",
            ),
            Condition(
                subject=_ib(entity_id=EID_OBJECT, entity_type="Object",
                             external_id="Account"),
                predicate="equals", value="Acme",
            ),
        ])
        entries = extract_coverage(body, conditions)
        subjects = {e.entity_id for e in entries if e.reference_kind == "subject"}
        condition_refs = {e.entity_id for e in entries if e.reference_kind == "condition"}
        assert subjects == {EID_FIELD}
        assert condition_refs == {EID_FIELD_2, EID_OBJECT}


# ---------------------------------------------------------------------------
# Combined extraction + classification
# ---------------------------------------------------------------------------

class TestExtractCoverageCombined:
    def test_subject_and_condition_classification_both_present(self) -> None:
        body = ValueClaimBody(
            subject=_ib(entity_id=EID_FIELD),
            expected_value=LiteralValue(value="x"),
        )
        conditions = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(entity_id=EID_FIELD_2),
                predicate="is_null",
            ),
        ])
        entries = extract_coverage(body, conditions)
        kinds = {e.reference_kind for e in entries}
        assert kinds == {"subject", "condition"}
        # Exactly one of each in this fixture.
        assert len([e for e in entries if e.reference_kind == "subject"]) == 1
        assert len([e for e in entries if e.reference_kind == "condition"]) == 1


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestExtractCoverageDeduplication:
    def test_same_entity_same_kind_emitted_once(self) -> None:
        """Same entity referenced twice in subject_fields (or any
        two slots tagged as subject) → one entry."""
        body = StateTransitionClaimBody(
            subject=_ib(entity_type="Object", entity_id=EID_OBJECT,
                        external_id="Opportunity"),
            subject_fields=[
                _ib(entity_id=EID_FIELD, external_id="Opp.StageName"),
                _ib(entity_id=EID_FIELD, external_id="Opp.StageName"),
            ],
            from_state=StateDescriptor(field_values={}),
            to_state=StateDescriptor(field_values={}),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        entries = extract_coverage(body, _empty_conditions())
        # Object + the duplicated Field = 2 distinct entries.
        assert len(entries) == 2
        entity_ids = [e.entity_id for e in entries]
        assert entity_ids.count(EID_FIELD) == 1

    def test_same_entity_id_different_entity_type_kept_separate(self) -> None:
        """Same entity_id but different entity_type → DIFFERENT
        coverage entries (entity_type is part of the dedup key).
        Pathological fixture, but the walk must not collapse
        across types."""
        body = StateTransitionClaimBody(
            subject=_ib(entity_type="Object", entity_id=EID_FIELD,
                        external_id="X"),
            subject_fields=[
                _ib(entity_type="Field", entity_id=EID_FIELD,
                    external_id="X.Y"),
            ],
            from_state=StateDescriptor(field_values={}),
            to_state=StateDescriptor(field_values={}),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        entries = extract_coverage(body, _empty_conditions())
        entity_types = {e.entity_type for e in entries}
        assert entity_types == {"Object", "Field"}
        assert len(entries) == 2

    def test_same_entity_subject_and_condition_kept_separate(self) -> None:
        """An entity appearing as both a subject ref AND a
        condition ref → TWO coverage entries (reference_kind is
        part of the dedup key)."""
        body = ValueClaimBody(
            subject=_ib(entity_id=EID_FIELD),
            expected_value=LiteralValue(value="x"),
        )
        conditions = SemanticConditionsBody(conditions=[
            Condition(
                subject=_ib(entity_id=EID_FIELD),
                predicate="is_not_null",
            ),
        ])
        entries = extract_coverage(body, conditions)
        kinds = sorted(e.reference_kind for e in entries)
        assert kinds == ["condition", "subject"]


# ---------------------------------------------------------------------------
# Defensive ref-type rejection
# ---------------------------------------------------------------------------

class TestExtractCoverageDefensiveRefRejection:
    def test_plain_pinned_ref_in_input_raises(self) -> None:
        """A plain :class:`PinnedRef` (operational) shouldn't
        appear in identity-bearing content; the walk fails
        loudly if it does."""
        # We can't construct a claim body with a PinnedRef in a
        # subject slot (Pydantic enforces IdentityBearingRef
        # there), so call the walk directly via a hand-crafted
        # container. The cleanest way is to invoke extract_coverage
        # internals on a fixture object.
        from primeqa.test_representation.coverage import _walk

        plain_pinned = PinnedRef(
            entity_type="Field",
            entity_id=EID_FIELD,
            version_seq=1,
            external_id="X.Y",
        )
        with pytest.raises(SubstrateError) as exc_info:
            _walk(plain_pinned, "subject", set(), "test")
        assert "PinnedRef" in str(exc_info.value)

    def test_logical_ref_in_input_raises(self) -> None:
        from primeqa.test_representation.coverage import _walk

        logical = LogicalRef(
            entity_type="Field", external_id="X.Y",
        )
        with pytest.raises(SubstrateError) as exc_info:
            _walk(logical, "subject", set(), "test")
        assert "LogicalRef" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Output stability / ordering
# ---------------------------------------------------------------------------

class TestExtractCoverageOrderStability:
    def test_output_sorted_by_entity_id_string(self) -> None:
        """Determinism: re-running extract_coverage on the same
        input must produce the same order, even when the input
        was assembled with different orderings."""
        body = StateTransitionClaimBody(
            subject=_ib(entity_type="Object", entity_id=EID_OBJECT,
                        external_id="Opportunity"),
            subject_fields=[
                _ib(entity_id=EID_FIELD_2, external_id="Opp.CloseDate"),
                _ib(entity_id=EID_FIELD, external_id="Opp.StageName"),
            ],
            from_state=StateDescriptor(field_values={}),
            to_state=StateDescriptor(field_values={}),
            triggering_event=EventDescriptor(
                trigger_kind="ui-trigger", description="x",
            ),
        )
        entries_a = extract_coverage(body, _empty_conditions())
        entries_b = extract_coverage(body, _empty_conditions())
        assert entries_a == entries_b
        # Stable by str(entity_id): UUIDs are compared as strings.
        ids = [str(e.entity_id) for e in entries_a]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# CoverageEntry shape
# ---------------------------------------------------------------------------

class TestCoverageEntryShape:
    def test_entry_is_frozen(self) -> None:
        entry = CoverageEntry(
            entity_type="Field", entity_id=EID_FIELD,
            reference_kind="subject",
        )
        with pytest.raises(Exception):
            entry.entity_type = "X"  # type: ignore[misc]

    def test_entries_hashable_for_set_dedup(self) -> None:
        """:class:`CoverageEntry` is a frozen dataclass, hence
        hashable. The extractor relies on this for its internal
        dedup set."""
        entry = CoverageEntry(
            entity_type="Field", entity_id=EID_FIELD,
            reference_kind="subject",
        )
        s = {entry, entry}
        assert len(s) == 1
