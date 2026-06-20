"""1b.2 per-category delta-fetch allow-list + gating.

A delta-safe category replaces its full re-fetch with: (1) a ``LastModifiedDate
> watermark`` delta of adds/mods, fed through the existing normalize/diff/SCD-2
write, plus (2) a MANDATORY deletion reconcile (a ``LastModifiedDate`` delta
cannot see deletions). The two are one inseparable unit — a category that cannot
do BOTH stays full-fetch.

GOVERNING RULE — fail toward full-fetch on any doubt. A category is delta-safe
ONLY if it is (a) fetched via Tooling SOQL, (b) carries a reliable
``LastModifiedDate``, AND (c) its full current id set can be listed cheaply
(``SELECT Id``) for the reconcile. Anything Describe-based, Metadata-API-based,
or unconfirmed on any axis stays full-fetch.

Realized allow-list (1b.2, VR-only): only ``ValidationRule`` ships delta. Profile
(also delta-capable) and the rest are deferred to later slices; everything else
is structurally excluded (REST describe / no LastModifiedDate / no cheap id list /
version-supersession complexity). See DECISIONS_LOG D-251.

The "since" (the org's ``setup_audit_watermark``) is read at gate time and placed
on ``ctx.delta_since`` by the engine — ONLY on a FRESH run with a non-NULL
watermark (D-250). A resume, or a NULL watermark, leaves it None → full-fetch.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle at runtime (context imports nothing here)
    from primeqa.sync.context import SyncContext


# The conservative delta-safe allow-list. Hardcoded (never derived at runtime) so
# adding a category is a reviewed, deliberate change. VR-only for 1b.2.
DELTA_SAFE_ENTITY_TYPES: frozenset[str] = frozenset({"ValidationRule"})


def is_delta_safe(entity_type: str) -> bool:
    """True iff ``entity_type`` is on the hardcoded delta-safe allow-list."""
    return entity_type in DELTA_SAFE_ENTITY_TYPES


def delta_since_for(
    ctx: "SyncContext", entity_type: str,
) -> datetime | None:
    """The delta "since" watermark to use for ``entity_type``, or None to
    full-fetch.

    Returns a datetime ONLY when BOTH hold: the category is on the allow-list
    AND a valid delta window (``ctx.delta_since``) is present (a fresh run with a
    non-NULL watermark). Otherwise None — the single fail-safe funnel for "not
    delta-safe" (off-list), "no watermark", and "resume" alike. A phase that gets
    None full-fetches exactly as before.
    """
    if entity_type not in DELTA_SAFE_ENTITY_TYPES:
        return None
    return getattr(ctx, "delta_since", None)
