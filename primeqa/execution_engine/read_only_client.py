"""L1 — the read-only client surface (D-245, Phase 4a).

The metadata-inspection path may touch **only** SOQL reads, never a mutation
verb. This Protocol makes that structural, not emergent: it exposes only
``query`` / ``query_data``. ``ToolingReadClient`` satisfies it (it has both);
``DataMutationClient`` does **not** (it has no ``query_data`` and exposes
``create`` / ``update`` / ``delete``). So annotating
``execute_metadata_inspection(client: ReadOnlyClient)`` makes passing a mutation
client to the read path a *type error* — the L4 conformance guarantee (no write
reachable from the inspection path) is enforced by the type, not left to review.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ReadOnlyClient(Protocol):
    """A Salesforce client restricted to reads. Methods only (so
    ``issubclass`` works under ``runtime_checkable``)."""

    def query(self, soql: str) -> list[dict]:
        ...

    def query_data(self, soql: str) -> list[dict]:
        ...
