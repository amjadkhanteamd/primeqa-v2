"""Tests for the substrate-2 outward surface (B-ε wire-up).

Per SPEC §4.7.2 + §10.2 (outward surface). After
``import primeqa.test_representation``, consumers should be able
to:

  - Import every public name listed in ``__all__`` directly from
    the top level.
  - See the body registry fully populated (18 entries currently —
    conditions + 4 data-behavior claims + 1 configuration claim +
    6 triggers + execution-environment + 5 recipes).
  - Construct + serialize body instances using only the top-level
    namespace (no need to drill into the deeper module paths).

These contracts are the substrate's public-API guarantee for S3 /
S4 / S6 / S8 and the Coordinator.
"""
from __future__ import annotations

import importlib
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# Import surface
# ---------------------------------------------------------------------------

class TestRootImportSurface:
    def test_root_package_imports_successfully(self) -> None:
        """Bare ``import primeqa.test_representation`` works
        without raising."""
        mod = importlib.import_module("primeqa.test_representation")
        assert mod is not None
        assert hasattr(mod, "__all__")
        assert len(mod.__all__) > 0

    def test_all_declared_exports_importable(self) -> None:
        """Every name in :data:`__all__` is bound on the top-level
        module. Guards against typos in __all__ and against
        forgetting to import a name."""
        import primeqa.test_representation as pkg
        missing = [n for n in pkg.__all__ if not hasattr(pkg, n)]
        assert not missing, (
            f"__all__ declares names that aren't bound on the "
            f"package: {missing}"
        )

    def test_no_duplicates_in_all(self) -> None:
        """``__all__`` must not list the same name twice — that
        would indicate a copy-paste error in the export list."""
        import primeqa.test_representation as pkg
        duplicates = sorted({
            n for n in pkg.__all__ if pkg.__all__.count(n) > 1
        })
        assert not duplicates, (
            f"__all__ contains duplicates: {duplicates}"
        )

    def test_all_in_subprocess(self) -> None:
        """Importing the root package in a fresh Python process
        succeeds with no warnings or stderr output (no hidden
        side effects)."""
        script = (
            "import primeqa.test_representation as pkg;"
            "missing = [n for n in pkg.__all__ if not hasattr(pkg, n)];"
            "assert not missing, f'missing: {missing}';"
            "print(len(pkg.__all__))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stderr.decode()
        # Sanity: __all__ has the expected breadth.
        count = int(result.stdout.decode().strip())
        assert count >= 50, (
            f"__all__ shorter than expected: {count}"
        )


# ---------------------------------------------------------------------------
# Registry population on root import
# ---------------------------------------------------------------------------

EXPECTED_REGISTRATIONS = (
    # Conditions (B-β)
    ("conditions", 1),
    ("conditions", 2),                # D-330 — the cross-field clause (exceeds)
    # Data-behavior claim bodies (B-β)
    ("value-claim", 1),
    ("state-transition-claim", 1),
    ("automation-effect-claim", 1),
    ("automation-effect-claim", 2),         # D-307 — the absence case
    ("prohibition-claim", 1),
    ("prohibition-claim", 2),         # D-333 — the approval-action arc
    ("acceptance-claim", 1),                # D-305
    ("acceptance-claim", 2),                # D-306.1 — the update case (update_state)
    ("acceptance-claim", 3),                # D-333 — the approval-action arc
    # Configuration claim bodies (D-098 — the C debut; existence + property D-122)
    ("metadata-relationship-claim", 1),
    ("existence-claim", 1),
    ("property-claim", 1),
    # Permission claim bodies (D-123 — the permission-archetype debut)
    ("capability-claim", 1),
    # UI claim bodies (D-124 — the ui-archetype debut)
    ("layout-claim", 1),
    # Trigger bodies (B-γ; inspection-trigger added by D-099)
    ("inbound-trigger", 1),
    ("data-mutation-trigger", 1),
    ("ui-trigger", 1),
    ("time-trigger", 1),
    ("configuration-trigger", 1),
    ("inspection-trigger", 1),
    # Execution environment (B-δ)
    ("execution_environment", 1),
    # Recipe bodies (B-δ)
    ("data-recipe", 1),
    ("metadata-recipe", 1),
    ("ui-recipe", 1),
    ("event-subscription-recipe", 1),
    ("callout-intercept-recipe", 1),
)


class TestRegistryFullyPopulatedOnRootImport:
    def test_registry_size_matches_expected(self) -> None:
        from primeqa.test_representation import default_registry
        assert len(default_registry) == len(EXPECTED_REGISTRATIONS)

    @pytest.mark.parametrize("kind,version", EXPECTED_REGISTRATIONS)
    def test_each_expected_kind_registered(
        self, kind: str, version: int,
    ) -> None:
        from primeqa.test_representation import default_registry
        assert (kind, version) in default_registry, (
            f"{(kind, version)!r} missing from default_registry"
        )

    def test_registry_population_holds_in_fresh_subprocess(self) -> None:
        """In a fresh Python process: bare
        ``import primeqa.test_representation`` populates the
        registry with exactly the expected entries — no body
        modules need explicit importing."""
        expected_repr = sorted(EXPECTED_REGISTRATIONS)
        script = (
            "import primeqa.test_representation as pkg;"
            "actual = sorted(pkg.default_registry._models.keys());"
            f"expected = {expected_repr!r};"
            "assert actual == expected, "
            "f'mismatch: actual={actual!r} expected={expected!r}'"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, check=False,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\n"
            f"stdout: {result.stdout.decode()}\n"
            f"stderr: {result.stderr.decode()}"
        )


# ---------------------------------------------------------------------------
# Construct bodies via top-level imports
# ---------------------------------------------------------------------------

class TestConstructBodiesFromTopLevel:
    """For each of several sample body kinds, the consumer can
    import the body class + its required descriptors from the
    top-level package and successfully construct + serialize an
    instance. Sanity-check that the public surface is genuinely
    usable, not just importable."""

    def test_construct_value_claim_body(self) -> None:
        from uuid import uuid4

        from primeqa.test_representation import (
            IdentityBearingRef,
            LiteralValue,
            ValueClaimBody,
            get_body_model,
        )

        body = ValueClaimBody(
            subject=IdentityBearingRef(
                entity_type="Field",
                entity_id=uuid4(),
                version_seq=1,
                external_id="Account.Industry",
            ),
            expected_value=LiteralValue(value="Tech"),
        )
        # Round-trip through the registry-resolved class to
        # confirm the registry returns the same class object as
        # the direct top-level import.
        cls = get_body_model("value-claim", 1)
        assert cls is ValueClaimBody
        roundtrip = cls.model_validate(body.model_dump())
        assert roundtrip == body

    def test_construct_data_recipe_body(self) -> None:
        from primeqa.test_representation import (
            DataRecipeBody,
            LogicalRef,
        )

        body = DataRecipeBody.model_validate({
            "api_choice": "rest",
            "identity_context": "run_as_user",
            "execution_mechanism": "direct_api",
            "run_as_user": LogicalRef(
                entity_type="User",
                external_id="rep@example.com",
            ).model_dump(),
            "steps": [],
        })
        assert body.kind == "data-recipe"

    def test_construct_ui_trigger_body(self) -> None:
        from primeqa.test_representation import (
            PageContext,
            UITriggerBody,
        )

        body = UITriggerBody(
            action_type="button_click",
            page_context=PageContext(page_kind="record_detail"),
            target_element="Submit",
        )
        assert body.kind == "ui-trigger"

    def test_construct_callout_intercept_recipe(self) -> None:
        from primeqa.test_representation import (
            CalloutInterceptRecipeBody,
            MockResponse,
        )

        body = CalloutInterceptRecipeBody(
            interception_mechanism="mock_endpoint",
            mock_response=MockResponse(
                status_code=200, body={"ok": True},
            ),
            steps=[],
        )
        assert body.kind == "callout-intercept-recipe"

    def test_construct_execution_environment(self) -> None:
        from primeqa.test_representation import (
            AuthAssumption,
            ExecutionEnvironmentBody,
            FeatureAssumption,
        )

        body = ExecutionEnvironmentBody(
            org_kind="sandbox",
            salesforce_edition="enterprise",
            feature_assumptions=[
                FeatureAssumption(feature_name="CDC", required=True),
            ],
            auth_assumptions=[
                AuthAssumption(auth_kind="data_api_user"),
            ],
        )
        assert body.kind == "execution_environment"


# ---------------------------------------------------------------------------
# Structural invariant: non-body utility modules don't self-register
# ---------------------------------------------------------------------------

class TestNonBodyModulesDontSelfRegister:
    """The registry-populating side effect lives in well-known
    places: the top-level package ``__init__``, the
    ``models.claims`` package ``__init__``, the ``models.triggers``
    package ``__init__``, the ``models.recipes`` package
    ``__init__``, and each individual body module's
    ``@register_body`` decorator at module top level.

    The substrate's utility modules — :mod:`errors`,
    :mod:`models.common`, :mod:`models.references`,
    :mod:`models.registry`, the placeholder
    :mod:`models` ``__init__`` — must NOT internally import body
    modules. This is the contract that lets downstream code
    import a utility cheaply when it doesn't need bodies.

    Verified by source inspection: each utility module's source
    must not contain any ``from .conditions``, ``from .claims``,
    ``from .triggers``, ``from .recipes``, or
    ``from .environment`` import."""

    UTILITY_MODULES = (
        "primeqa/test_representation/errors.py",
        "primeqa/test_representation/models/__init__.py",
        "primeqa/test_representation/models/common.py",
        "primeqa/test_representation/models/references.py",
        "primeqa/test_representation/models/registry.py",
    )
    FORBIDDEN_FRAGMENTS = (
        "from primeqa.test_representation.models.conditions",
        "from primeqa.test_representation.models.claims",
        "from primeqa.test_representation.models.triggers",
        "from primeqa.test_representation.models.recipes",
        "from primeqa.test_representation.models.environment",
        "from primeqa.test_representation.models.primitives",
        # Relative-import equivalents.
        "from .conditions",
        "from .claims",
        "from .triggers",
        "from .recipes",
        "from .environment",
        "from .primitives",
    )

    @pytest.mark.parametrize("relpath", UTILITY_MODULES)
    def test_utility_module_has_no_body_imports(
        self, relpath: str,
    ) -> None:
        from pathlib import Path

        # The repo root is two parents up from this test file.
        repo_root = Path(__file__).resolve().parents[3]
        text = (repo_root / relpath).read_text()
        for fragment in self.FORBIDDEN_FRAGMENTS:
            assert fragment not in text, (
                f"{relpath} contains forbidden body import "
                f"{fragment!r} — registry-populating imports "
                f"must live in package __init__s and body modules, "
                f"not in utility modules"
            )
