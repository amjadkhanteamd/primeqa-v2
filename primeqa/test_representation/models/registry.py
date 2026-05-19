"""Body-schema registry.

Per SPEC §4.4 + §4.7.1. The registry maps ``(kind,
body_schema_version)`` to a Pydantic model class so the substrate
can decode a JSONB body it reads from the DB into the right body
model.

Every persisted body carries both keys (DB-level key-presence is
enforced by Track A migration ``20260518_1014_create_substrate_2_tables``);
on read, the substrate looks up the body's declared
``(kind, body_schema_version)`` in this registry, gets back the
Pydantic model class, and calls ``model_validate`` on the JSONB
payload.

Registration is import-time via the :func:`register_body`
decorator. The registry is empty in B-α — concrete body models
arrive in sub-tracks B-β through B-ε. Tests can register lightweight
fixture models locally without affecting the package-wide
registry instance (see :class:`BodyRegistry` for the construction
contract).
"""
from __future__ import annotations

from typing import Callable, Type, TypeVar

from primeqa.test_representation.errors import SchemaIncompatibilityError
from primeqa.test_representation.models.common import BodyBase


B = TypeVar("B", bound=BodyBase)


class BodyRegistry:
    """Maps ``(kind, body_schema_version)`` → :class:`BodyBase`
    subclass.

    Multiple instances are supported (tests construct their own to
    avoid polluting the package-wide registry); the package-wide
    singleton is :data:`default_registry`.

    The registry stores ``BodyBase`` subclasses, not instances —
    callers receive the class and call ``model_validate`` to
    decode payloads.
    """

    def __init__(self) -> None:
        self._models: dict[tuple[str, int], Type[BodyBase]] = {}

    def register(
        self,
        kind: str,
        body_schema_version: int,
        model: Type[BodyBase],
    ) -> None:
        """Register ``model`` for the given ``(kind, version)``.

        Raises ``ValueError`` if a model is already registered for
        the same ``(kind, body_schema_version)``: duplicate
        registration is almost always a bug (two modules claiming
        the same slot) and should fail loudly rather than silently
        win/lose by import order.
        """
        key = (kind, int(body_schema_version))
        if key in self._models:
            existing = self._models[key]
            raise ValueError(
                f"duplicate registration for kind={kind!r} "
                f"body_schema_version={body_schema_version!r}: "
                f"already registered as {existing.__module__}."
                f"{existing.__qualname__}"
            )
        self._models[key] = model

    def get_body_model(
        self, kind: str, body_schema_version: int,
    ) -> Type[BodyBase]:
        """Return the Pydantic model class for the given
        ``(kind, body_schema_version)``.

        Raises :class:`SchemaIncompatibilityError` if no model is
        registered. The error carries ``kind``, the requested
        ``body_schema_version``, and the list of versions
        currently known for this kind (may be empty) so the caller
        can decide whether to defer (newer version inbound), fail
        (older version no longer supported), or alert (unknown
        kind entirely).
        """
        key = (kind, int(body_schema_version))
        model = self._models.get(key)
        if model is None:
            available = self.versions_for(kind)
            raise SchemaIncompatibilityError(
                f"no body model registered for kind={kind!r} "
                f"body_schema_version={body_schema_version!r} "
                f"(available versions for this kind: "
                f"{available if available else 'none'})",
                kind=kind,
                body_schema_version=int(body_schema_version),
                available_versions=available,
            )
        return model

    def get_latest_body_version(self, kind: str) -> int:
        """Return the highest registered ``body_schema_version``
        for ``kind``.

        Used by writers that want "latest known model" semantics —
        e.g., a generator producing a new body picks the latest
        version automatically. Raises
        :class:`SchemaIncompatibilityError` if no versions are
        registered for ``kind`` (writers have no fallback to pick
        and should surface this).
        """
        versions = self.versions_for(kind)
        if not versions:
            raise SchemaIncompatibilityError(
                f"no body model registered for kind={kind!r}; "
                f"cannot determine latest body_schema_version",
                kind=kind,
                body_schema_version=None,
                available_versions=[],
            )
        return max(versions)

    def versions_for(self, kind: str) -> list[int]:
        """Return the sorted list of registered
        ``body_schema_version`` integers for ``kind``. Empty list
        if ``kind`` is unknown."""
        return sorted(v for (k, v) in self._models if k == kind)

    def __contains__(self, key: object) -> bool:
        """Support ``("value-claim", 1) in registry`` membership
        checks for tests and diagnostic code."""
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        kind, version = key
        return (kind, int(version)) in self._models

    def __len__(self) -> int:
        return len(self._models)


# ---------------------------------------------------------------------------
# Package-wide default registry + decorator
# ---------------------------------------------------------------------------

default_registry = BodyRegistry()
"""The package-wide :class:`BodyRegistry`. Concrete body modules
register against this via :func:`register_body` at import time.

Tests that need isolation construct their own :class:`BodyRegistry`
instance and pass it explicitly to the code under test rather than
polluting this singleton."""


def register_body(
    kind: str, body_schema_version: int,
) -> Callable[[Type[B]], Type[B]]:
    """Decorator: register the decorated :class:`BodyBase`
    subclass for ``(kind, body_schema_version)`` in the package-
    wide :data:`default_registry`.

    Usage (will appear in B-β through B-ε)::

        from primeqa.test_representation.models.common import BodyBase
        from primeqa.test_representation.models.registry import register_body

        @register_body("value-claim", 1)
        class ValueClaimBodyV1(BodyBase):
            body_schema_version: Literal[1] = 1
            kind: Literal["value-claim"] = "value-claim"
            ...

    The decorator returns the class unchanged so it can be used in
    standard Python inheritance/instantiation chains. Registration
    happens as a side effect at module import time; downstream
    code should import the body module before any read path that
    might encounter that ``(kind, version)``.
    """

    def _decorator(cls: Type[B]) -> Type[B]:
        default_registry.register(kind, body_schema_version, cls)
        return cls

    return _decorator


def get_body_model(
    kind: str, body_schema_version: int,
) -> Type[BodyBase]:
    """Module-level convenience: look up against
    :data:`default_registry`. See
    :meth:`BodyRegistry.get_body_model`."""
    return default_registry.get_body_model(kind, body_schema_version)


def get_latest_body_version(kind: str) -> int:
    """Module-level convenience: look up against
    :data:`default_registry`. See
    :meth:`BodyRegistry.get_latest_body_version`."""
    return default_registry.get_latest_body_version(kind)
