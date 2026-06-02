"""Substrate-1 consumer query interface — first increment of SPEC §12.

Realizes the read subset of the designed five-primitive ``SemanticOrgModel``
(SPEC §12 / D-022) that Substrate-3's admissibility grounding consumes:

  - :meth:`SemanticOrgModel.current_version_seq` — the version pin (primitive 5)
  - :meth:`SemanticOrgModel.get_entities`        — existence + property
    inspection (primitive 1)
  - :meth:`SemanticOrgModel.get_related`         — single-hop typed-edge
    lookup (primitive 2)

D-022's invariants hold:

  - **Version-aware access only.** Every read takes ``at_seq``; there is no
    ``at_seq=None`` "current" shortcut — a consumer calls
    :meth:`current_version_seq` once and threads the result.
  - **Explicit direction** on edge lookups (``inbound | outbound | both``).
  - **No raw SQL across the boundary.** Consumers call these typed methods;
    the SQL lives here, *inside* S1. The accessor runs over an
    already-tenant-scoped connection from
    :func:`primeqa.semantic.connection.get_tenant_connection` (SPEC §5.4) —
    it sets no ``search_path`` of its own.

Bitemporal as-of read (SPEC §6.2): a row is valid at version V iff
``valid_from_seq <= V AND (valid_to_seq IS NULL OR valid_to_seq > V)``.

Deferred to later increments — each lands with the consumer slice that first
exercises it (D-022: "full ergonomics emerge during Substrate 3 design"):

  - ``query_entities`` — condition-based / descriptive-selector resolution
    (feeds ``ambiguous-reference`` / ``ambiguous_target_resolution``).
  - ``traverse`` — multi-hop graph walk.
  - ``diff_for_entities`` / ``diff_impact`` / ``diff_window`` (+ ``TraversalSpec``
    / ``Change``) — the diff engine (SPEC §11 / D-021).

Per D-022 these are **omitted from the class entirely** (not stubbed) until
built — the class exposes only what is real this increment.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class VersionNotFoundError(Exception):
    """An ``at_seq`` is not present in ``logical_versions``.

    Fail-loud per S1's purged/missing-version discipline (D-021): an invalid
    version pin must not silently masquerade as "no rows exist." Raised by the
    read primitives before they query, and by :meth:`SemanticOrgModel.
    current_version_seq` when the tenant has no versions at all.
    """

    def __init__(self, version_seq: Optional[int] = None) -> None:
        if version_seq is None:
            message = "no logical_versions exist for this tenant"
        else:
            message = f"version_seq {version_seq} not found in logical_versions"
        super().__init__(message)
        self.version_seq = version_seq


# ---------------------------------------------------------------------------
# Return shapes — frozen dataclasses (matching the SPEC's Change / TraversalSpec
# idiom; immutable read projections). S1-owned; the SPEC names these return
# types (§12) but leaves them shape-undefined — defined here, grounded in the
# entities / edges columns (SPEC §6.2 / §6.3).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Entity:
    """An ``entities`` row as-of a version (SPEC §6.2)."""

    id: UUID
    entity_type: str
    sf_id: Optional[str]
    sf_api_name: Optional[str]
    display_name: Optional[str]
    attributes: dict[str, Any]
    valid_from_seq: int
    valid_to_seq: Optional[int]  # None == currently valid


@dataclass(frozen=True)
class RelatedEntity:
    """A single-hop edge plus its far-end :class:`Entity`, both as-of the
    same version (SPEC §6.3)."""

    edge_id: UUID
    edge_type: str
    edge_category: str  # STRUCTURAL | CONFIG | PERMISSION | BEHAVIOR (D-017)
    direction: str      # 'inbound' | 'outbound' relative to the queried entity
    properties: dict[str, Any]
    entity: Entity


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Whitelisted exact-match filter columns for get_entities. Caller dict keys are
# checked against this tuple so they never reach SQL as raw identifiers.
# Attribute-JSONB conditions are query_entities' job (a later increment).
_ENTITY_FILTER_COLUMNS = ("id", "sf_id", "sf_api_name", "display_name")
# id is uuid-typed; it gets a ::uuid cast so a str value binds correctly.
_UUID_FILTER_COLUMNS = ("id",)

_VALID_DIRECTIONS = ("inbound", "outbound", "both")


def _as_of(alias: str) -> str:
    """The bitemporal as-of predicate for a table ``alias``, against the bound
    ``:at_seq`` param. ``alias`` is a trusted module literal, never caller
    input."""
    return (
        f"{alias}.valid_from_seq <= :at_seq "
        f"AND ({alias}.valid_to_seq IS NULL OR {alias}.valid_to_seq > :at_seq)"
    )


def _as_uuid(value: Any) -> UUID:
    """Coerce a driver value (uuid or str) to :class:`UUID` — robust to whether
    psycopg2 UUID typecasting is registered."""
    return value if isinstance(value, UUID) else UUID(str(value))


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a JSONB column value (dict or json str) to a dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _detail_table_for(entity_type: str) -> Optional[str]:
    """The per-type detail-table name from the trusted Tier-1 registry, or None
    for an unknown entity_type. Used by :meth:`SemanticOrgModel.get_entity_details`
    — the name is registry-sourced, never caller input."""
    from primeqa.semantic.entity_attributes import get_entity_metadata
    try:
        return get_entity_metadata(entity_type).detail_table
    except KeyError:
        return None


def _row_to_entity(m: Any) -> Entity:
    return Entity(
        id=_as_uuid(m["id"]),
        entity_type=m["entity_type"],
        sf_id=m["sf_id"],
        sf_api_name=m["sf_api_name"],
        display_name=m["display_name"],
        attributes=_as_dict(m["attributes"]),
        valid_from_seq=int(m["valid_from_seq"]),
        valid_to_seq=None if m["valid_to_seq"] is None else int(m["valid_to_seq"]),
    )


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

class SemanticOrgModel:
    """Substrate-1's consumer query interface (SPEC §12 / D-022) — read subset.

    Construct over an already-scoped connection::

        from primeqa.semantic.connection import get_tenant_connection
        from primeqa.semantic.query import SemanticOrgModel

        with get_tenant_connection(tenant_id) as conn:
            s1 = SemanticOrgModel(conn)
            at = s1.current_version_seq()
            objs = s1.get_entities("Object", at_seq=at,
                                   filters={"sf_api_name": "Account"})
            rules = s1.get_related(objs[0].id, ["HAS_VALIDATION_RULE"],
                                   "outbound", at_seq=at)

    The model holds the connection for its lifetime. Validated ``version_seq``
    values are cached on the instance so repeated reads at the same pin don't
    re-check ``logical_versions``.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._validated_seqs: set[int] = set()

    # -- primitive 5 -----------------------------------------------------
    def current_version_seq(self) -> int:
        """The latest ``version_seq`` (``MAX`` over ``logical_versions``).

        Raises :class:`VersionNotFoundError` if the tenant has no versions —
        there is nothing to pin (fail-loud rather than returning a sentinel).
        """
        seq = self._conn.execute(
            text("SELECT MAX(version_seq) FROM logical_versions")
        ).scalar()
        if seq is None:
            raise VersionNotFoundError()
        seq = int(seq)
        self._validated_seqs.add(seq)
        return seq

    # -- version-pin validation (instance-cached) ------------------------
    def _validate_version(self, at_seq: int) -> None:
        if at_seq in self._validated_seqs:
            return
        found = self._conn.execute(
            text("SELECT 1 FROM logical_versions WHERE version_seq = :at_seq"),
            {"at_seq": at_seq},
        ).scalar()
        if found is None:
            raise VersionNotFoundError(at_seq)
        self._validated_seqs.add(at_seq)

    # -- primitive 1 -----------------------------------------------------
    def get_entities(
        self,
        entity_type: str,
        at_seq: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[Entity]:
        """Entities of ``entity_type`` valid at ``at_seq``.

        ``filters`` is exact-match over a whitelist (``id`` / ``sf_id`` /
        ``sf_api_name`` / ``display_name``); an unknown key raises
        ``ValueError`` (fail-loud, never silently ignored). Existence is a
        non-empty result; property inspection is each :attr:`Entity.attributes`.
        """
        self._validate_version(at_seq)

        params: dict[str, Any] = {"at_seq": at_seq, "entity_type": entity_type}
        clauses = ["entity_type = :entity_type", _as_of("entities")]

        if filters:
            for key, value in filters.items():
                if key not in _ENTITY_FILTER_COLUMNS:
                    raise ValueError(
                        f"unsupported get_entities filter {key!r}; "
                        f"allowed: {_ENTITY_FILTER_COLUMNS}"
                    )
                if key in _UUID_FILTER_COLUMNS:
                    clauses.append(f"{key} = CAST(:f_{key} AS uuid)")
                    params[f"f_{key}"] = str(value)
                else:
                    clauses.append(f"{key} = :f_{key}")
                    params[f"f_{key}"] = value

        sql = (
            "SELECT id, entity_type, sf_id, sf_api_name, display_name, "
            "attributes, valid_from_seq, valid_to_seq "
            "FROM entities WHERE " + " AND ".join(clauses)
        )
        rows = self._conn.execute(text(sql), params).mappings().all()
        return [_row_to_entity(m) for m in rows]

    # -- primitive 2 -----------------------------------------------------
    def get_related(
        self,
        entity_id: UUID,
        edge_types: list[str],
        direction: str,
        at_seq: int,
    ) -> list[RelatedEntity]:
        """Single-hop edges of ``edge_types`` incident to ``entity_id`` at
        ``at_seq``.

        ``direction`` is one of ``inbound`` / ``outbound`` / ``both`` (relative
        to ``entity_id``). The far-end entity is resolved as-of the *same*
        ``at_seq``. An empty ``edge_types`` returns ``[]`` (zero types requested
        — but ``at_seq`` is still validated fail-loud first).
        """
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {_VALID_DIRECTIONS}, got {direction!r}"
            )
        self._validate_version(at_seq)
        if not edge_types:
            return []

        params: dict[str, Any] = {
            "at_seq": at_seq,
            "entity_id": str(entity_id),
            "edge_types": list(edge_types),
        }

        selects: list[str] = []
        if direction in ("outbound", "both"):
            selects.append(self._related_select("outbound"))
        if direction in ("inbound", "both"):
            selects.append(self._related_select("inbound"))
        sql = " UNION ALL ".join(selects)

        rows = self._conn.execute(text(sql), params).mappings().all()
        return [self._row_to_related(m) for m in rows]

    @staticmethod
    def _related_select(which: str) -> str:
        # 'outbound': the queried entity is the edge source; far end = target.
        # 'inbound':  the queried entity is the edge target; far end = source.
        near = "source_entity_id" if which == "outbound" else "target_entity_id"
        far = "target_entity_id" if which == "outbound" else "source_entity_id"
        return (
            "SELECT e.id AS edge_id, e.edge_type, e.edge_category, e.properties, "
            f"'{which}' AS direction, "
            "t.id, t.entity_type, t.sf_id, t.sf_api_name, t.display_name, "
            "t.attributes, t.valid_from_seq, t.valid_to_seq "
            "FROM edges e "
            f"JOIN entities t ON t.id = e.{far} AND {_as_of('t')} "
            f"WHERE e.{near} = CAST(:entity_id AS uuid) "
            "AND e.edge_type = ANY(:edge_types) "
            f"AND {_as_of('e')}"
        )

    @staticmethod
    def _row_to_related(m: Any) -> RelatedEntity:
        return RelatedEntity(
            edge_id=_as_uuid(m["edge_id"]),
            edge_type=m["edge_type"],
            edge_category=m["edge_category"],
            direction=m["direction"],
            properties=_as_dict(m["properties"]),
            entity=_row_to_entity(m),
        )

    # -- detail-table access (consumer-driven increment; D-022 / D-111.1) ----
    def get_entity_details(
        self,
        entity_id: UUID,
        at_seq: int,
    ) -> Optional[dict[str, Any]]:
        """The entity's per-type **detail-table** row — the hot columns that
        live *outside* ``attributes`` (e.g. a ValidationRule's ``is_active``),
        or ``None`` when the type has no detail table or no row.

        Surfaces detail columns the ``entities`` / ``attributes`` read does not.
        Per D-022, deferred reads land with the consumer slice that first
        exercises them — Substrate-6 attribution (D-111.1) is the first consumer
        of detail-column access. Detail rows are keyed 1:1 by ``entity_id``
        (the version is already pinned by the id the caller obtained as-of
        ``at_seq``); ``at_seq`` is validated for a consistent fail-loud contract
        with the other reads. The detail-table name comes from the **trusted**
        Tier-1 registry, never caller input (no raw-SQL injection surface).
        """
        self._validate_version(at_seq)
        type_row = self._conn.execute(
            text("SELECT entity_type FROM entities "
                 "WHERE id = CAST(:eid AS uuid)"),
            {"eid": str(entity_id)},
        ).mappings().first()
        if type_row is None:
            return None
        detail_table = _detail_table_for(type_row["entity_type"])
        if detail_table is None:
            return None
        row = self._conn.execute(
            text(f"SELECT * FROM {detail_table} "  # noqa: S608 — table from trusted registry
                 "WHERE entity_id = CAST(:eid AS uuid)"),
            {"eid": str(entity_id)},
        ).mappings().first()
        return dict(row) if row is not None else None

    # -- detail read: a value-set's values (D-119) -----------------------
    def get_picklist_values(
        self,
        picklist_value_set_id: UUID,
        at_seq: int,
    ) -> list[dict[str, Any]]:
        """The PicklistValue rows of a PicklistValueSet, valid at ``at_seq`` and
        ordered by ``sort_order`` — each a ``picklist_value_details`` row
        (``entity_id`` / ``value_api_name`` / ``value_label`` / ``is_active`` /
        ``is_default`` / ``sort_order``). ``[]`` when the set has no values (or
        the id is not a value set).

        The middle hop of value-claim accepted-values grounding (D-119): a
        PicklistValueSet's PicklistValue children are linked only by the
        ``picklist_value_details.picklist_value_set_entity_id`` FK — there is no
        containment *edge* (the D-019 edge taxonomy is locked at 14 types) and
        ``get_entities`` filters only identity columns, so this targeted read is
        the way to enumerate them. The detail table carries no version columns,
        so the as-of window applies to each value's ``entities`` row (the
        ``get_entities`` idiom): a superseded value drops out at an earlier
        ``at_seq``. The table name is a trusted module literal — no caller-input
        SQL surface (consistent with ``get_entity_details``).
        """
        self._validate_version(at_seq)
        sql = (
            "SELECT pvd.entity_id, pvd.value_api_name, pvd.value_label, "
            "pvd.is_active, pvd.is_default, pvd.sort_order "
            "FROM picklist_value_details pvd "
            "JOIN entities e ON e.id = pvd.entity_id "
            "WHERE pvd.picklist_value_set_entity_id = CAST(:pvs_id AS uuid) "
            "AND " + _as_of("e") + " "
            "ORDER BY pvd.sort_order"
        )
        rows = self._conn.execute(
            text(sql),
            {"pvs_id": str(picklist_value_set_id), "at_seq": at_seq},
        ).mappings().all()
        return [dict(r) for r in rows]
