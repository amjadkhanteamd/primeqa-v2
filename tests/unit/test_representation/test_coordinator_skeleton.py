"""Tests for the Semantic Transaction Coordinator skeleton.

Per Track D-β.1. Verifies the class shape, dataclass result
shapes, and that the write methods are stubs pointing to the
correct implementing sub-tracks. The actual write-flow
implementations land in:

  - :meth:`SemanticTransactionCoordinator.write_claim` →
    Track D-β.2.
  - :meth:`SemanticTransactionCoordinator.write_recipe` →
    Track D-β.3.
"""
from __future__ import annotations

import inspect
from uuid import UUID, uuid4

import pytest

from primeqa.test_representation import (
    LiteralValue,
    SemanticConditionsBody,
    SemanticTransactionCoordinator,
    ValueClaimBody,
    WriteClaimResult,
    WriteRecipeResult,
)


# ---------------------------------------------------------------------------
# Class importable + instantiable
# ---------------------------------------------------------------------------

class TestCoordinatorClass:
    def test_importable_from_top_level(self) -> None:
        # Sanity: the import at the top of this file already
        # exercises the import path. Affirm the class binding.
        assert SemanticTransactionCoordinator is not None

    def test_instantiable_with_no_args(self) -> None:
        coord = SemanticTransactionCoordinator()
        assert coord is not None

    def test_no_per_instance_state(self) -> None:
        """The Coordinator is stateless across calls; two
        instances should behave identically. Construction
        contract per the class docstring."""
        c1 = SemanticTransactionCoordinator()
        c2 = SemanticTransactionCoordinator()
        # Two different instances (no singleton pattern).
        assert c1 is not c2
        # Neither carries observable per-instance state.
        # __dict__ should be empty (no attributes set in __init__).
        assert vars(c1) == vars(c2) == {}


# ---------------------------------------------------------------------------
# Stub methods raise NotImplementedError with the right pointer
# ---------------------------------------------------------------------------

class TestCoordinatorWriteClaimImplemented:
    """At Track D-β.1 these asserted write_claim raised
    :class:`NotImplementedError`. At D-β.2 write_claim is
    implemented; the tests confirm the method is no longer a
    stub (importable, callable — the integration tests under
    ``tests/integration/test_representation/`` exercise the
    actual 11-step orchestration against a real DB)."""

    def test_method_is_implemented(self) -> None:
        """write_claim should not raise NotImplementedError. A
        call with invalid arguments raises the appropriate
        validation error from the implementation (here:
        ValueError on the archetype check), NOT
        NotImplementedError."""
        coord = SemanticTransactionCoordinator()
        with pytest.raises(Exception) as exc_info:
            coord.write_claim(
                session=None,  # type: ignore[arg-type]
                actor="human",
                test_id=None,
                archetype="bogus_archetype",
                claim_kind="value-claim",
                asserted_truth=None,  # type: ignore[arg-type]
                semantic_conditions=None,  # type: ignore[arg-type]
            )
        # The implementation reaches step 1 (discriminator
        # validation) and raises ValueError on the unknown
        # archetype before any DB activity.
        assert not isinstance(exc_info.value, NotImplementedError)


class TestCoordinatorWriteRecipeImplemented:
    """At Track D-β.1 these asserted write_recipe raised
    :class:`NotImplementedError`. At D-β.3 write_recipe is
    implemented; the tests confirm the method is no longer a
    stub (importable, callable — the integration tests under
    ``tests/integration/test_representation/`` exercise the
    actual orchestration against a real DB)."""

    def test_method_is_implemented(self) -> None:
        """write_recipe should not raise NotImplementedError. A
        call with invalid arguments raises the appropriate
        validation error from the implementation (here:
        ValueError on the trigger_kind check), NOT
        NotImplementedError."""
        coord = SemanticTransactionCoordinator()
        with pytest.raises(Exception) as exc_info:
            coord.write_recipe(
                session=None,  # type: ignore[arg-type]
                actor="human",
                recipe_id=None,
                claim_test_id=uuid4(),
                trigger_kind="bogus-trigger",
                recipe_kind="data-recipe",
                causal_initiation=None,  # type: ignore[arg-type]
                observation_realization=None,  # type: ignore[arg-type]
                execution_environment=None,  # type: ignore[arg-type]
            )
        # Step 1 (discriminator validation) raises ValueError
        # before any DB activity.
        assert not isinstance(exc_info.value, NotImplementedError)


# ---------------------------------------------------------------------------
# Method signatures (pin the v1 interface)
# ---------------------------------------------------------------------------

class TestCoordinatorSignatures:
    def test_write_claim_parameter_names(self) -> None:
        """Pin the v1 signature. Track D-β.2 implements the body
        but must not rename the parameters. D-285 (Slice 4f.0)
        appends ``strategy_kind`` (keyword-only, default None) —
        an additive tail param; the prior names are unchanged."""
        sig = inspect.signature(
            SemanticTransactionCoordinator.write_claim,
        )
        # ``self`` is first, then ``session``, then keyword-only.
        params = list(sig.parameters.keys())
        assert params == [
            "self", "session",
            "actor", "test_id",
            "archetype", "claim_kind",
            "asserted_truth", "semantic_conditions",
            "strategy_kind",
        ]

    def test_write_claim_keyword_only_after_session(self) -> None:
        """Per the prompt's signature: ``session`` is positional;
        everything after is keyword-only (the ``*`` in the
        declaration). Catches accidental positional-arg
        regressions that would silently break callers."""
        sig = inspect.signature(
            SemanticTransactionCoordinator.write_claim,
        )
        for name in ("actor", "test_id", "archetype", "claim_kind",
                     "asserted_truth", "semantic_conditions"):
            assert (
                sig.parameters[name].kind
                == inspect.Parameter.KEYWORD_ONLY
            ), f"parameter {name!r} should be keyword-only"

    def test_write_recipe_parameter_names(self) -> None:
        """The D-β.1 skeleton pinned the parameter list; D-β.3
        added one optional kwarg (``claim_version_seq``) per SPEC
        §6.4 for the recipe-to-claim version-pinning case; D-228
        added ``priority`` so S3 can mint fallback secondaries
        below the primary. The prior-pinned parameters MUST remain
        in the same positions — new optional kwargs append to the
        end."""
        sig = inspect.signature(
            SemanticTransactionCoordinator.write_recipe,
        )
        params = list(sig.parameters.keys())
        assert params == [
            "self", "session",
            "actor", "recipe_id",
            "claim_test_id",
            "trigger_kind", "recipe_kind",
            "causal_initiation", "observation_realization",
            "execution_environment",
            "claim_version_seq",  # added in D-β.3
            "priority",           # added in D-228
        ]

    def test_write_recipe_keyword_only_after_session(self) -> None:
        sig = inspect.signature(
            SemanticTransactionCoordinator.write_recipe,
        )
        for name in (
            "actor", "recipe_id", "claim_test_id",
            "trigger_kind", "recipe_kind",
            "causal_initiation", "observation_realization",
            "execution_environment",
            "claim_version_seq",
        ):
            assert (
                sig.parameters[name].kind
                == inspect.Parameter.KEYWORD_ONLY
            )


# ---------------------------------------------------------------------------
# Result types are dataclasses with the documented fields
# ---------------------------------------------------------------------------

class TestWriteClaimResultShape:
    def test_constructible_with_all_fields(self) -> None:
        test_id = uuid4()
        result = WriteClaimResult(
            test_id=test_id,
            version_seq=1,
            identity_hash="0" * 64,
            status="draft",
            was_noop=False,
        )
        assert result.test_id == test_id
        assert result.version_seq == 1
        assert result.identity_hash == "0" * 64
        assert result.status == "draft"
        assert result.was_noop is False

    def test_is_frozen(self) -> None:
        result = WriteClaimResult(
            test_id=uuid4(),
            version_seq=1,
            identity_hash="0" * 64,
            status="draft",
            was_noop=False,
        )
        with pytest.raises(Exception):
            result.version_seq = 2  # type: ignore[misc]

    def test_was_noop_flag_models_spec_section_7_7(self) -> None:
        """SPEC §7.7: S3 same-hash regeneration is a no-op skip.
        ``was_noop=True`` flags this case in the result; metadata
        fields describe the EXISTING version, not a fresh write."""
        result = WriteClaimResult(
            test_id=uuid4(),
            version_seq=42,
            identity_hash="abc" + "0" * 61,
            status="approved",
            was_noop=True,
        )
        assert result.was_noop is True


class TestWriteRecipeResultShape:
    def test_constructible_with_all_fields(self) -> None:
        recipe_id = uuid4()
        result = WriteRecipeResult(
            recipe_id=recipe_id,
            version_seq=3,
            status="generated_unapproved",
        )
        assert result.recipe_id == recipe_id
        assert result.version_seq == 3
        assert result.status == "generated_unapproved"

    def test_is_frozen(self) -> None:
        result = WriteRecipeResult(
            recipe_id=uuid4(),
            version_seq=1,
            status="active",
        )
        with pytest.raises(Exception):
            result.status = "deprecated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Top-level re-exports
# ---------------------------------------------------------------------------

class TestTopLevelExports:
    @pytest.mark.parametrize("name", [
        "SemanticTransactionCoordinator",
        "WriteClaimResult",
        "WriteRecipeResult",
    ])
    def test_exported_from_top_level(self, name: str) -> None:
        import primeqa.test_representation as pkg
        assert name in pkg.__all__
        assert hasattr(pkg, name)
