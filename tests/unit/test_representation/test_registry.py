"""Tests for the substrate-2 body registry.

Per SPEC §4.4 + §4.7.1. The registry maps ``(kind,
body_schema_version)`` → ``Type[BodyBase]`` and is the substrate's
on-read dispatch mechanism.

Covers:

  - Register-then-lookup round-trip.
  - Unregistered ``(kind, version)`` raises
    :class:`SchemaIncompatibilityError` carrying structured context
    (the kind queried + the versions actually registered for it).
  - Multi-version support for a single kind, with ``versions_for``
    and ``get_latest_body_version`` exposing the registry's view.
  - Duplicate registration is rejected — two modules claiming the
    same ``(kind, version)`` slot is almost always a bug.
  - The :func:`register_body` decorator wires registration into
    import time and returns the class unchanged.
  - Test isolation: tests use a fresh :class:`BodyRegistry` instance
    so they don't pollute the package-wide
    :data:`default_registry`.
  - Membership / length / convenience module-level lookups.
"""
from __future__ import annotations

from typing import Literal

import pytest

from primeqa.test_representation.errors import SchemaIncompatibilityError
from primeqa.test_representation.models.common import BodyBase
from primeqa.test_representation.models.registry import (
    BodyRegistry,
    default_registry,
    get_body_model,
    get_latest_body_version,
    register_body,
)


# ---------------------------------------------------------------------------
# Fixture body models
# ---------------------------------------------------------------------------

class _ValueClaimV1(BodyBase):
    body_schema_version: Literal[1] = 1
    kind: Literal["value-claim"] = "value-claim"
    statement: str = ""


class _ValueClaimV2(BodyBase):
    body_schema_version: Literal[2] = 2
    kind: Literal["value-claim"] = "value-claim"
    statement: str = ""
    rationale: str = ""


class _DataRecipeV1(BodyBase):
    body_schema_version: Literal[1] = 1
    kind: Literal["data-recipe"] = "data-recipe"
    title: str = ""


@pytest.fixture
def registry() -> BodyRegistry:
    """Isolated registry per test. Critical — the package-wide
    :data:`default_registry` is empty in B-α and we keep it that
    way so registration tests can't leak."""
    return BodyRegistry()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class TestRegisterAndLookup:
    def test_register_then_get(self, registry: BodyRegistry) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        assert registry.get_body_model("value-claim", 1) is _ValueClaimV1

    def test_register_preserves_class_identity(
        self, registry: BodyRegistry,
    ) -> None:
        """Registry stores classes, not instances — callers receive
        the class and call model_validate themselves."""
        registry.register("value-claim", 1, _ValueClaimV1)
        cls = registry.get_body_model("value-claim", 1)
        instance = cls.model_validate({
            "body_schema_version": 1,
            "kind": "value-claim",
            "statement": "test",
        })
        assert isinstance(instance, _ValueClaimV1)
        assert instance.statement == "test"

    def test_register_accepts_string_version_as_int(
        self, registry: BodyRegistry,
    ) -> None:
        """Internal int() coercion — defensive against callers
        that hand a numpy int or a string-int (e.g., re-loaded from
        a config file)."""
        registry.register("value-claim", "1", _ValueClaimV1)  # type: ignore[arg-type]
        assert registry.get_body_model("value-claim", 1) is _ValueClaimV1
        assert registry.get_body_model("value-claim", "1") is _ValueClaimV1  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Missing entries — SchemaIncompatibilityError + structured context
# ---------------------------------------------------------------------------

class TestMissingEntries:
    def test_unknown_kind_raises_with_empty_versions(
        self, registry: BodyRegistry,
    ) -> None:
        with pytest.raises(SchemaIncompatibilityError) as exc_info:
            registry.get_body_model("value-claim", 1)
        err = exc_info.value
        assert err.context["kind"] == "value-claim"
        assert err.context["body_schema_version"] == 1
        assert err.context["available_versions"] == []

    def test_known_kind_unknown_version_raises_with_versions(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        registry.register("value-claim", 2, _ValueClaimV2)
        with pytest.raises(SchemaIncompatibilityError) as exc_info:
            registry.get_body_model("value-claim", 99)
        err = exc_info.value
        assert err.context["kind"] == "value-claim"
        assert err.context["body_schema_version"] == 99
        # Lists every version registered for this kind so the
        # caller can decide whether to defer (newer inbound) or
        # surface (older deprecated).
        assert err.context["available_versions"] == [1, 2]


# ---------------------------------------------------------------------------
# Multi-version + versions_for
# ---------------------------------------------------------------------------

class TestMultiVersion:
    def test_register_multiple_versions_for_same_kind(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        registry.register("value-claim", 2, _ValueClaimV2)
        assert registry.get_body_model("value-claim", 1) is _ValueClaimV1
        assert registry.get_body_model("value-claim", 2) is _ValueClaimV2

    def test_versions_for_returns_sorted(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 2, _ValueClaimV2)
        registry.register("value-claim", 1, _ValueClaimV1)
        # Inserted out-of-order; output must be sorted ascending
        # so callers can index .versions_for(...)[-1] for "latest".
        assert registry.versions_for("value-claim") == [1, 2]

    def test_versions_for_unknown_kind_is_empty(
        self, registry: BodyRegistry,
    ) -> None:
        assert registry.versions_for("never-registered") == []

    def test_versions_for_filters_by_kind(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        registry.register("data-recipe", 1, _DataRecipeV1)
        assert registry.versions_for("value-claim") == [1]
        assert registry.versions_for("data-recipe") == [1]


# ---------------------------------------------------------------------------
# get_latest_body_version
# ---------------------------------------------------------------------------

class TestGetLatestVersion:
    def test_latest_returns_max_version(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        registry.register("value-claim", 2, _ValueClaimV2)
        assert registry.get_latest_body_version("value-claim") == 2

    def test_latest_single_version(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        assert registry.get_latest_body_version("value-claim") == 1

    def test_latest_unknown_kind_raises(
        self, registry: BodyRegistry,
    ) -> None:
        with pytest.raises(SchemaIncompatibilityError) as exc_info:
            registry.get_latest_body_version("never-registered")
        err = exc_info.value
        assert err.context["kind"] == "never-registered"
        assert err.context["available_versions"] == []
        # No specific version was requested → context["body_schema_version"]
        # is None per the registry's contract.
        assert err.context["body_schema_version"] is None


# ---------------------------------------------------------------------------
# Duplicate registration
# ---------------------------------------------------------------------------

class TestDuplicateRegistration:
    def test_duplicate_kind_version_raises(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        with pytest.raises(ValueError) as exc_info:
            registry.register("value-claim", 1, _ValueClaimV1)
        msg = str(exc_info.value)
        assert "duplicate" in msg.lower()
        assert "value-claim" in msg

    def test_duplicate_message_names_existing_class(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        with pytest.raises(ValueError) as exc_info:
            registry.register("value-claim", 1, _ValueClaimV2)
        # Diagnostic value of the message: which module already
        # owns the slot.
        msg = str(exc_info.value)
        assert _ValueClaimV1.__qualname__ in msg

    def test_different_version_same_kind_ok(
        self, registry: BodyRegistry,
    ) -> None:
        """Two versions for the same kind are not duplicates."""
        registry.register("value-claim", 1, _ValueClaimV1)
        registry.register("value-claim", 2, _ValueClaimV2)  # no raise

    def test_different_kind_same_version_ok(
        self, registry: BodyRegistry,
    ) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        registry.register("data-recipe", 1, _DataRecipeV1)  # no raise


# ---------------------------------------------------------------------------
# Membership + size
# ---------------------------------------------------------------------------

class TestRegistryProtocols:
    def test_contains_known_pair(self, registry: BodyRegistry) -> None:
        registry.register("value-claim", 1, _ValueClaimV1)
        assert ("value-claim", 1) in registry

    def test_contains_unknown_pair(self, registry: BodyRegistry) -> None:
        assert ("value-claim", 1) not in registry

    def test_contains_rejects_non_tuples(
        self, registry: BodyRegistry,
    ) -> None:
        assert "value-claim" not in registry
        assert 1 not in registry
        assert None not in registry

    def test_contains_rejects_wrong_length_tuples(
        self, registry: BodyRegistry,
    ) -> None:
        assert ("value-claim",) not in registry  # type: ignore[operator]
        assert ("value-claim", 1, "extra") not in registry  # type: ignore[operator]

    def test_len_reports_registered_count(
        self, registry: BodyRegistry,
    ) -> None:
        assert len(registry) == 0
        registry.register("value-claim", 1, _ValueClaimV1)
        assert len(registry) == 1
        registry.register("value-claim", 2, _ValueClaimV2)
        assert len(registry) == 2
        registry.register("data-recipe", 1, _DataRecipeV1)
        assert len(registry) == 3


# ---------------------------------------------------------------------------
# register_body decorator + module-level convenience
#
# These touch the package-wide default_registry. We use fresh kinds
# (prefixed with a test marker) and clean up to keep the default
# registry pristine between runs.
# ---------------------------------------------------------------------------

class TestRegisterBodyDecorator:
    @pytest.fixture(autouse=True)
    def _cleanup_default_registry(self):
        """Snapshot the default registry's keys before each test
        and restore on teardown — protects the package-wide
        singleton from cross-test pollution."""
        before = dict(default_registry._models)  # type: ignore[attr-defined]
        yield
        # Remove anything added during the test.
        after = dict(default_registry._models)  # type: ignore[attr-defined]
        for key in after.keys() - before.keys():
            default_registry._models.pop(key, None)  # type: ignore[attr-defined]

    def test_decorator_registers_against_default(self) -> None:
        @register_body("_test_kind_decorator", 1)
        class _Body(BodyBase):
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_kind_decorator"] = "_test_kind_decorator"

        assert default_registry.get_body_model(
            "_test_kind_decorator", 1,
        ) is _Body

    def test_decorator_returns_class_unchanged(self) -> None:
        @register_body("_test_kind_returns", 1)
        class _Body(BodyBase):
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_kind_returns"] = "_test_kind_returns"

        # The decorator must be transparent — the class object the
        # caller gets back is the same class that was decorated.
        assert _Body is default_registry.get_body_model(
            "_test_kind_returns", 1,
        )
        # And it still constructs as a normal Pydantic model.
        instance = _Body()
        assert instance.kind == "_test_kind_returns"
        assert instance.body_schema_version == 1

    def test_module_level_get_body_model(self) -> None:
        @register_body("_test_module_lookup", 1)
        class _Body(BodyBase):
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_module_lookup"] = "_test_module_lookup"

        assert get_body_model("_test_module_lookup", 1) is _Body

    def test_module_level_get_latest_body_version(self) -> None:
        @register_body("_test_latest", 1)
        class _BodyV1(BodyBase):
            body_schema_version: Literal[1] = 1
            kind: Literal["_test_latest"] = "_test_latest"

        @register_body("_test_latest", 2)
        class _BodyV2(BodyBase):
            body_schema_version: Literal[2] = 2
            kind: Literal["_test_latest"] = "_test_latest"

        assert get_latest_body_version("_test_latest") == 2

    def test_module_level_unknown_raises(self) -> None:
        with pytest.raises(SchemaIncompatibilityError):
            get_body_model("_test_never_registered", 1)


# ---------------------------------------------------------------------------
# Default-registry hygiene — the package's top-level + non-body
# submodules MUST NOT register anything. Body modules register only
# when they're individually imported.
# ---------------------------------------------------------------------------

class TestDefaultRegistryHygiene:
    def test_root_package_import_does_not_register_bodies(self) -> None:
        """The contract: ``import primeqa.test_representation``
        (or any of its non-body submodules) must NOT pull in claim
        body modules as a side effect.

        Verified in a subprocess so the test isn't contaminated by
        the parent process's already-imported body modules (the
        other tests in this run import claim bodies via top-of-file
        imports; once those imports happen, ``default_registry``
        is populated for the rest of the process lifetime). The
        subprocess starts fresh.

        If this test breaks, a concrete body got imported by the
        package's __init__ path — find the import and gate it
        behind explicit module import per the registry's contract
        (bodies register at import time of the body module, not at
        import time of primeqa.test_representation)."""
        import subprocess
        import sys

        script = (
            "from primeqa.test_representation.models.registry "
            "import default_registry;"
            "import primeqa.test_representation;"
            "import primeqa.test_representation.errors;"
            "import primeqa.test_representation.models;"
            "import primeqa.test_representation.models.common;"
            "import primeqa.test_representation.models.references;"
            "import primeqa.test_representation.models.registry;"
            # claims/ is a placeholder package — importing it must
            # not pull in any archetype subpackage.
            "import primeqa.test_representation.models.claims;"
            "assert len(default_registry) == 0, "
            "f'package import polluted registry: "
            "{sorted(default_registry._models.keys())}'"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\n"
            f"stdout: {result.stdout.decode()}\n"
            f"stderr: {result.stderr.decode()}"
        )

    def test_in_process_registry_holds_expected_bodies(self) -> None:
        """In the live test process the data-behavior claim bodies
        and the conditions body have all been imported via the
        other test modules' top-of-file imports. They MUST be
        registered. This is the positive counterpart to the
        hygiene test above."""
        for key in (
            ("conditions", 1),
            ("value-claim", 1),
            ("state-transition-claim", 1),
            ("automation-effect-claim", 1),
            ("prohibition-claim", 1),
        ):
            assert key in default_registry, (
                f"expected {key!r} registered in default_registry "
                f"after B-β imports"
            )
