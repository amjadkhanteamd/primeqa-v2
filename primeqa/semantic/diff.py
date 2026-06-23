"""Per-org MODEL diff (per-org Slice 5, Leg A) — a pure, read-only structural
comparison of two orgs' currently-active S1 entity models.

:func:`diff_org_models` reads each org's active entities once::

    SELECT entity_type, sf_api_name, last_seed_hash
    FROM entities
    WHERE connected_org_id = :org AND valid_to_seq IS NULL AND sf_api_name IS NOT NULL

keys them on ``(entity_type, sf_api_name)`` — the sync's own per-org dedup key
(D-258); universal, since ``sf_api_name`` is populated for every entity while
``sf_id`` is NULL for ``Object`` + synthetic types — and partitions the union of
both orgs' keys into four buckets:

  * ``only_in_a`` — key present in A, absent in B
  * ``only_in_b`` — key present in B, absent in A
  * ``differs``   — key in BOTH, DIFFERENT ``last_seed_hash`` (same identity,
                    diverged content)
  * ``same``      — key in both, identical ``last_seed_hash``

The content signal is **``last_seed_hash``** — the canonical change-hash
(``hash_normalized``) the S1 sync bucketing already uses for change detection —
NOT the ``attributes`` JSONB (cheaper, and exactly the sync's own notion of
"changed"). One bulk read per org; the diff is pure set arithmetic in Python.

**Entity-level only for v1.** Edge/detail diff is DEFERRED — ``get_related`` /
``get_entity_details`` are per-entity (a graph diff is N round-trips), so a
relationship/detail-level diff is a later increment.

**Pure read.** No ``SemanticOrgModel`` write, no INSERT/UPDATE, no schema, no
migration. The output is plain dicts/lists — serializable for a UI. The ``same``
bucket is reported as a COUNT only (it can be thousands of identical rows — not a
useful delta list); the three delta buckets carry their key lists for drill-down.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

from primeqa.semantic.entity_attributes import TIER_1_ENTITIES

# One bulk read per org — the canonical change-hash for every currently-active
# entity, org-scoped on the stable partition key (D-258). ``sf_api_name IS NOT
# NULL`` is a safety guard (it is always populated for synced entities); the
# (entity_type, sf_api_name) pair is unique per org among active rows (the per-org
# dedup invariant), so the read maps cleanly to one hash per key.
_ORG_ENTITIES_SQL = text(
    "SELECT entity_type, sf_api_name, last_seed_hash "
    "FROM entities "
    "WHERE connected_org_id = :org "
    "  AND valid_to_seq IS NULL "
    "  AND sf_api_name IS NOT NULL"
)


def _read_org_entities(conn, org) -> dict[tuple[str, str], Optional[str]]:
    """Pure read: ``{(entity_type, sf_api_name): last_seed_hash}`` for the org's
    currently-active entities. One query. ``(entity_type, sf_api_name)`` is unique
    per org among active rows, so the dict never clobbers."""
    rows = conn.execute(_ORG_ENTITIES_SQL, {"org": str(org)}).fetchall()
    return {(r.entity_type, r.sf_api_name): r.last_seed_hash for r in rows}


def _empty_type_counts() -> dict[str, int]:
    return {"only_in_a": 0, "only_in_b": 0, "differs": 0, "same": 0}


def _diff_maps(
    a: dict[tuple[str, str], Optional[str]],
    b: dict[tuple[str, str], Optional[str]],
    org_a, org_b,
) -> dict[str, Any]:
    """Pure: partition two ``{(entity_type, sf_api_name): last_seed_hash}`` maps
    into the structured diff. No DB — directly unit-testable.

    Partition identity holds by construction::

        only_in_a + same + differs == len(a)
        only_in_b + same + differs == len(b)
    """
    keys_a, keys_b = set(a), set(b)
    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    both = keys_a & keys_b
    differs = sorted(k for k in both if a[k] != b[k])
    same = both - set(differs)

    # Per-entity-type counts — seed EVERY TIER_1 type so the shape is stable for a
    # UI even when a type has zero entities in both orgs.
    by_type: dict[str, dict[str, int]] = {
        et: _empty_type_counts() for et in sorted(TIER_1_ENTITIES.keys())
    }

    def _bump(key: tuple[str, str], bucket: str) -> None:
        by_type.setdefault(key[0], _empty_type_counts())[bucket] += 1

    for k in only_a:
        _bump(k, "only_in_a")
    for k in only_b:
        _bump(k, "only_in_b")
    for k in differs:
        _bump(k, "differs")
    for k in same:
        _bump(k, "same")

    totals = {
        "only_in_a": len(only_a),
        "only_in_b": len(only_b),
        "differs": len(differs),
        "same": len(same),
        "count_a": len(keys_a),
        "count_b": len(keys_b),
    }
    return {
        "org_a": str(org_a),
        "org_b": str(org_b),
        "totals": totals,
        "by_entity_type": by_type,
        "only_in_a": [{"entity_type": et, "sf_api_name": n} for (et, n) in only_a],
        "only_in_b": [{"entity_type": et, "sf_api_name": n} for (et, n) in only_b],
        "differs": [
            {"entity_type": et, "sf_api_name": n,
             "hash_a": a[(et, n)], "hash_b": b[(et, n)]}
            for (et, n) in differs
        ],
    }


def diff_org_models(conn, org_a, org_b) -> dict[str, Any]:
    """Structural diff of two orgs' currently-active S1 entity models (Leg A).

    ``conn`` is an open tenant-scoped connection; ``org_a`` / ``org_b`` are
    ``connected_orgs.id`` values. Returns a serializable dict::

        {
          "org_a", "org_b",
          "totals": {only_in_a, only_in_b, differs, same, count_a, count_b},
          "by_entity_type": {entity_type: {only_in_a, only_in_b, differs, same}},
          "only_in_a": [{entity_type, sf_api_name}, ...],
          "only_in_b": [{entity_type, sf_api_name}, ...],
          "differs":   [{entity_type, sf_api_name, hash_a, hash_b}, ...],
        }

    Entity-level only (v1) — keyed ``(entity_type, sf_api_name)``, content signal
    ``last_seed_hash``. Edge/detail diff deferred. Pure read — no writes anywhere.
    """
    a = _read_org_entities(conn, org_a)
    b = _read_org_entities(conn, org_b)
    return _diff_maps(a, b, org_a, org_b)
