"""DomainPackProvider — thin facade over Library + Selector.

Exists so callers (currently just `primeqa.intelligence.generation`) can
resolve matching packs with one call. No Flask / app-layer imports —
pure library code, safe to instantiate from any context.

See `primeqa.intelligence.knowledge.domain_packs` for the core data
model and selection semantics.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from primeqa.intelligence.knowledge.domain_packs import (
    DomainPack,
    DomainPackLibrary,
    DomainPackSelector,
)

log = logging.getLogger(__name__)


class DomainPackProvider:
    """Resolve Domain Packs for a given requirement.

    Single public method `get_packs(...)` returns the ranked, budget-
    capped list of matching packs plus an attribution list shaped for
    the `llm_usage_log.context->'domain_packs_applied'` JSONB key.
    """

    def __init__(self, packs_dir: str):
        self._library = DomainPackLibrary(packs_dir)
        self._selector = DomainPackSelector(self._library)

    def get_packs(
        self,
        requirement_text: str,
        referenced_objects: Optional[List[str]] = None,
        max_tokens: int = 4000,
    ) -> Tuple[List[DomainPack], List[dict]]:
        """Return `(packs, attribution)`.

        - `packs` — list of DomainPack objects ready to pass into the
          prompt module via `context["domain_packs"]`.
        - `attribution` — list of `{"id": pack.id, "version": pack.version}`
          dicts ready to drop into `llm_usage_log.context["domain_packs_applied"]`.
          Shape matches what `test_plan_generation.build()` writes into
          its `context_for_log`; prompt module is the source of truth for
          that shape — this is a convenience for non-prompt callers.

        Empty lists when nothing matches (not None).
        """
        matches = self._selector.select(
            requirement_text=requirement_text,
            referenced_objects=referenced_objects,
            max_tokens=max_tokens,
        )
        packs = [m.pack for m in matches]
        attribution = [{"id": p.id, "version": p.version} for p in packs]
        return packs, attribution


__all__ = ["DomainPackProvider"]
