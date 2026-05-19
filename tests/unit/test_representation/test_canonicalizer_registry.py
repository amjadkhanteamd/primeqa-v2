"""Tests for the :class:`CanonicalizerRegistry` and
:func:`register_canonicalizer` decorator.

Per SPEC §6.3.9 Rule 6 (governance-critical infrastructure) +
D-059. Covers:

  - register / get / describe round-trip.
  - Duplicate registration raises :class:`ValueError` naming the
    existing function's module + qualname.
  - ``get`` returns ``None`` for unregistered slots.
  - The two B-δ built-in canonicalizers
    (:func:`canonicalize_state_transition` and
    :func:`canonicalize_automation_effect`) are registered after
    :mod:`primeqa.test_representation` import.
  - :func:`canonicalize` dispatches through the registry: custom
    canonicalizer used when registered; generic walk used otherwise.
  - ``__contains__`` / ``__len__`` protocols.
"""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict

from primeqa.test_representation import (
    BodyBase,
    CanonicalizerRegistry,
    default_canonicalizer_registry,
    register_canonicalizer,
)
from primeqa.test_representation.canonicalizers.automation_effect import (
    canonicalize_automation_effect,
)
from primeqa.test_representation.canonicalizers.state_transition import (
    canonicalize_state_transition,
)
from primeqa.test_representation.models.claims.data_behavior.automation_effect_claim import (
    AutomationEffectClaimBody,
)
from primeqa.test_representation.models.claims.data_behavior.state_transition_claim import (
    StateTransitionClaimBody,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

class _RegistryTestBody(BodyBase):
    """Test fixture body used only by registry tests."""

    model_config = ConfigDict(extra="forbid", frozen=False)
    body_schema_version: Literal[1] = 1
    kind: Literal["_test_registry_body"] = "_test_registry_body"
    payload: str = ""


def _fake_canonicalizer(body):
    """Fake canonicalizer for registry plumbing tests."""
    return {"fake": True, "payload": body.payload}


# ---------------------------------------------------------------------------
# Isolated registry — round-trip
# ---------------------------------------------------------------------------

class TestIsolatedRegistry:
    @pytest.fixture
    def registry(self) -> CanonicalizerRegistry:
        return CanonicalizerRegistry()

    def test_register_then_get(
        self, registry: CanonicalizerRegistry,
    ) -> None:
        registry.register(
            _RegistryTestBody, 1, _fake_canonicalizer,
            description="test fixture",
        )
        fn = registry.get(_RegistryTestBody, 1)
        assert fn is _fake_canonicalizer

    def test_get_returns_none_when_unregistered(
        self, registry: CanonicalizerRegistry,
    ) -> None:
        assert registry.get(_RegistryTestBody, 1) is None

    def test_describe_returns_audit_description(
        self, registry: CanonicalizerRegistry,
    ) -> None:
        registry.register(
            _RegistryTestBody, 1, _fake_canonicalizer,
            description="audit-trail description",
        )
        assert registry.describe(
            _RegistryTestBody, 1,
        ) == "audit-trail description"

    def test_describe_returns_none_when_unregistered(
        self, registry: CanonicalizerRegistry,
    ) -> None:
        assert registry.describe(_RegistryTestBody, 1) is None


# ---------------------------------------------------------------------------
# Duplicate registration
# ---------------------------------------------------------------------------

class TestDuplicateRegistration:
    def test_duplicate_raises_value_error(self) -> None:
        registry = CanonicalizerRegistry()
        registry.register(
            _RegistryTestBody, 1, _fake_canonicalizer,
            description="first",
        )
        with pytest.raises(ValueError) as exc_info:
            registry.register(
                _RegistryTestBody, 1, _fake_canonicalizer,
                description="second",
            )
        msg = str(exc_info.value)
        assert "duplicate" in msg.lower()
        # Names the existing entry's qualname for diagnosis:
        assert _fake_canonicalizer.__qualname__ in msg

    def test_different_version_same_class_ok(self) -> None:
        registry = CanonicalizerRegistry()
        registry.register(
            _RegistryTestBody, 1, _fake_canonicalizer, description="v1",
        )
        # Different identity_hash_version → different slot.
        registry.register(
            _RegistryTestBody, 2, _fake_canonicalizer, description="v2",
        )

    def test_different_class_same_version_ok(self) -> None:
        class _OtherBody(BodyBase):
            model_config = ConfigDict(extra="forbid", frozen=False)
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_other"] = "_test_other"

        registry = CanonicalizerRegistry()
        registry.register(
            _RegistryTestBody, 1, _fake_canonicalizer,
            description="rt",
        )
        registry.register(
            _OtherBody, 1, _fake_canonicalizer, description="other",
        )


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

class TestRegistryProtocols:
    @pytest.fixture
    def registry(self) -> CanonicalizerRegistry:
        return CanonicalizerRegistry()

    def test_contains_after_register(
        self, registry: CanonicalizerRegistry,
    ) -> None:
        assert (_RegistryTestBody, 1) not in registry
        registry.register(
            _RegistryTestBody, 1, _fake_canonicalizer, description="x",
        )
        assert (_RegistryTestBody, 1) in registry

    def test_contains_rejects_non_tuples(
        self, registry: CanonicalizerRegistry,
    ) -> None:
        assert _RegistryTestBody not in registry
        assert None not in registry

    def test_len(self, registry: CanonicalizerRegistry) -> None:
        assert len(registry) == 0
        registry.register(
            _RegistryTestBody, 1, _fake_canonicalizer, description="x",
        )
        assert len(registry) == 1
        registry.register(
            _RegistryTestBody, 2, _fake_canonicalizer, description="x2",
        )
        assert len(registry) == 2


# ---------------------------------------------------------------------------
# Default registry — built-in canonicalizers
# ---------------------------------------------------------------------------

class TestDefaultRegistryBuiltins:
    def test_state_transition_canonicalizer_registered(self) -> None:
        fn = default_canonicalizer_registry.get(
            StateTransitionClaimBody, 1,
        )
        assert fn is canonicalize_state_transition

    def test_automation_effect_canonicalizer_registered(self) -> None:
        fn = default_canonicalizer_registry.get(
            AutomationEffectClaimBody, 1,
        )
        assert fn is canonicalize_automation_effect

    def test_state_transition_description_present(self) -> None:
        desc = default_canonicalizer_registry.describe(
            StateTransitionClaimBody, 1,
        )
        assert desc is not None
        # Audit-trail style — references the C1-B resolution.
        assert "C1-B" in desc or "entity_id" in desc

    def test_automation_effect_description_present(self) -> None:
        desc = default_canonicalizer_registry.describe(
            AutomationEffectClaimBody, 1,
        )
        assert desc is not None
        assert "C1-B" in desc or "entity_id" in desc

    def test_default_registry_size_matches_track_c_shipped(self) -> None:
        """B-δ + Track C ship two canonicalizers — state-transition
        and automation-effect. Asserting the size catches any
        accidental extra registration."""
        assert len(default_canonicalizer_registry) == 2


# ---------------------------------------------------------------------------
# Decorator + dispatch via canonicalize()
# ---------------------------------------------------------------------------

class TestDecoratorAndDispatch:
    @pytest.fixture(autouse=True)
    def _cleanup_default(self):
        """Snapshot the default registry's keys and restore on
        teardown — keep the package-wide singleton clean."""
        before = dict(default_canonicalizer_registry._fns)  # type: ignore[attr-defined]
        before_desc = dict(
            default_canonicalizer_registry._descriptions,  # type: ignore[attr-defined]
        )
        yield
        # Remove anything added during the test.
        after = dict(default_canonicalizer_registry._fns)  # type: ignore[attr-defined]
        for key in after.keys() - before.keys():
            default_canonicalizer_registry._fns.pop(key, None)  # type: ignore[attr-defined]
            default_canonicalizer_registry._descriptions.pop(key, None)  # type: ignore[attr-defined]

    def test_decorator_registers_against_default(self) -> None:
        class _DecBody(BodyBase):
            model_config = ConfigDict(extra="forbid", frozen=False)
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_decorator_body"] = "_test_decorator_body"

        @register_canonicalizer(_DecBody, 1, description="decorator test")
        def _canon(body):
            return {"decorated": True}

        assert default_canonicalizer_registry.get(_DecBody, 1) is _canon

    def test_decorator_returns_function_unchanged(self) -> None:
        class _DecBody2(BodyBase):
            model_config = ConfigDict(extra="forbid", frozen=False)
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_decorator_body_2"] = "_test_decorator_body_2"

        @register_canonicalizer(_DecBody2, 1, description="x")
        def _canon(body):
            return {"x": True}

        # Function is still directly callable (decorator is
        # transparent).
        assert _canon(_DecBody2()) == {"x": True}

    def test_canonicalize_dispatches_through_registry(self) -> None:
        """Register a custom canonicalizer for a test body; then
        confirm :func:`canonicalize` routes through it instead of
        the generic walk."""
        from primeqa.test_representation import canonicalize

        class _DispatchBody(BodyBase):
            model_config = ConfigDict(extra="forbid", frozen=False)
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_dispatch_body"] = "_test_dispatch_body"
            x: int = 0

        @register_canonicalizer(
            _DispatchBody, 1, description="dispatch test",
        )
        def _canon(body):
            return {"routed": True, "value": body.x}

        out = canonicalize(_DispatchBody(x=42))
        assert out == {"routed": True, "value": 42}

    def test_canonicalize_falls_back_to_generic_when_unregistered(
        self,
    ) -> None:
        """A body class with no registered canonicalizer routes
        through the generic walk; ``kind`` etc. are emitted."""
        from primeqa.test_representation import canonicalize

        class _NoCustomBody(BodyBase):
            model_config = ConfigDict(extra="forbid", frozen=False)
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_no_custom"] = "_test_no_custom"
            x: int = 7

        out = canonicalize(_NoCustomBody())
        # Generic walk: kind included; body_schema_version excluded.
        assert out == {"kind": "_test_no_custom", "x": 7}
