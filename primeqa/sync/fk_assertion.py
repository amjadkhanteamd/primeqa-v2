"""ENTITY_ORDER vs schema-FK topological-sort assertion.

Per PHASE_2_STEP_4_SYNC_DESIGN.md §4 "FK-topological-sort assertion".

Schema shape note (substrate-1):

Substrate-1's schema uses a single canonical `entities` table with
an `entity_type` column rather than per-type tables. All detail
tables FK to `entities.id` without distinguishing entity types at
the schema level. As a result, no schema-level FK encodes an
entity-type-to-entity-type ordering constraint (e.g., "Field rows
reference Object rows" exists semantically but `field_details.
object_entity_id → entities.id` is type-agnostic).

The assertion below therefore logs a warning rather than raising
in this codebase. The assertion machinery remains in place so that
if the schema ever evolves to per-type tables (or to typed FKs via
generated columns), the ordering check becomes meaningful again.
EntityOrderViolation continues to be raised if and only if a
real ordering violation is detected.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect

from primeqa.sync.exceptions import EntityOrderViolation


logger = logging.getLogger(__name__)


# ENTITY_ORDER: 11 entity-materializing phases. The 10 Tier-1
# detail-table entity types per SPEC §9 (Object, Field, RecordType,
# Layout, ValidationRule, Flow, Profile, PermissionSet, User,
# PicklistValue) PLUS PicklistValueSet — an entity-materializing
# phase that intentionally has no detail table (its full shape
# lives in entities.attributes JSONB; see the Object-cycle
# corrections-log entry).
#
# FlowDefinition is NOT modeled as a separate entity. Substrate-1's
# bitemporal supersession IS the native versioning mechanism for
# Flow: the Flow entity is keyed by stable identity (DeveloperName),
# and each new version deployment supersedes the prior Flow record
# with updated attributes — entity history IS version history.
# FlowDefinition exists only as fetch-time parent context that the
# Flow phase consumes (DeveloperName, ActiveVersionId,
# ManageableState). fetch_flow_definitions() remains as a
# substrate-1 Tooling fetcher; it just isn't a phase. See
# corrections-log §20 (resolves the SPEC.md §9 vs. ENTITY_ORDER
# contradiction).
ENTITY_ORDER: tuple[str, ...] = (
    "Object",
    "PicklistValueSet",
    "PicklistValue",
    "Field",
    "RecordType",
    "Layout",
    "ValidationRule",
    "Profile",
    "PermissionSet",
    "User",
    "Flow",
)


# Optional explicit mapping from entity types to per-type tables, used
# only if a future schema introduces per-type tables. Empty for the
# current single-entities-table schema.
ENTITY_TYPE_TO_TABLE: dict[str, str] = {}


def assert_entity_order_respects_schema_fks(
    engine: Any,
    tenant_schema: str,
) -> None:
    """Verify the hardcoded ENTITY_ORDER respects the actual FK
    declarations in the schema.

    For every FK from an entity-type-A table to an entity-type-B
    table (where both tables represent entity types in
    ENTITY_TYPE_TO_TABLE), B must come strictly before A in
    ENTITY_ORDER. If any FK violates this, raises
    EntityOrderViolation.

    Substrate-1 uses a single `entities` table for all entity
    types (no per-type tables); ENTITY_TYPE_TO_TABLE is empty
    by default and the check is effectively a no-op + warning.
    The assertion call still runs at engine init so a future
    schema migration introducing per-type tables doesn't
    silently bypass the check.

    Args:
        engine: SQLAlchemy Engine (or anything inspect() accepts).
        tenant_schema: e.g., 'tenant_1'. Used to scope inspection.
    """
    if not ENTITY_TYPE_TO_TABLE:
        logger.warning(
            "ENTITY_TYPE_TO_TABLE is empty; schema appears to use the "
            "substrate-1 single-entities-table pattern. ENTITY_ORDER "
            "ordering is enforced logically only — no schema-level FK "
            "violation is detectable. Per PHASE_2_STEP_4_SYNC_DESIGN.md "
            "§4."
        )
        return

    inspector = inspect(engine)

    # Build {table -> ENTITY_ORDER index} for tables present in the
    # mapping.
    table_to_order_index: dict[str, int] = {}
    for entity_type, table_name in ENTITY_TYPE_TO_TABLE.items():
        if entity_type not in ENTITY_ORDER:
            raise EntityOrderViolation(
                f"ENTITY_TYPE_TO_TABLE maps unknown entity type "
                f"{entity_type!r}; not in ENTITY_ORDER."
            )
        table_to_order_index[table_name] = ENTITY_ORDER.index(entity_type)

    # Inspect FKs on each entity-type table.
    violations: list[str] = []
    for table_name, a_idx in table_to_order_index.items():
        try:
            fks = inspector.get_foreign_keys(table_name, schema=tenant_schema)
        except Exception as e:
            # Table not present in schema — surface but don't fail; the
            # mapping is a design-time declaration, may be partial.
            logger.warning(
                "Cannot inspect FKs on %s.%s: %s",
                tenant_schema, table_name, e,
            )
            continue
        for fk in fks:
            referred = fk.get("referred_table")
            if referred not in table_to_order_index:
                # FK to a non-entity-table (e.g., logical_versions,
                # connected_orgs) — not an entity-order concern.
                continue
            b_idx = table_to_order_index[referred]
            # For FK A → B, B must come before A.
            if b_idx >= a_idx:
                # ENTITY_ORDER violation: A depends on B, but B is
                # ordered same-or-later than A.
                a_type = ENTITY_ORDER[a_idx]
                b_type = ENTITY_ORDER[b_idx]
                violations.append(
                    f"FK {table_name}({fk.get('constrained_columns')}) -> "
                    f"{referred}({fk.get('referred_columns')}): "
                    f"ENTITY_ORDER places {a_type} (index {a_idx}) "
                    f"before-or-same-as {b_type} (index {b_idx}), but "
                    f"the FK requires {b_type} to come first."
                )

    if violations:
        raise EntityOrderViolation(
            "ENTITY_ORDER violates schema FK dependencies:\n  - "
            + "\n  - ".join(violations)
        )
