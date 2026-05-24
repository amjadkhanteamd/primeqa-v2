"""Substrate-3 prompt management (D-089 / D-103).

A versioned, frozen prompt registry. The working ``base.md`` + ``fragments/*.md``
author the *next* version; each shipped version is frozen as a composed artifact
under ``versions/`` and recoverable forever (replay determinism), guarded by a
recorded content hash. The runtime reads a FROZEN version via
:func:`registry.get`; it never recomposes a shipped version from the working
source (D-103.1).
"""
from primeqa.generation.prompts import registry

__all__ = ["registry"]
