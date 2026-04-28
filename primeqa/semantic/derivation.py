"""Substrate 1 — Containment-as-column derivation logic (per D-017).

D-017 establishes that detail-table columns and hot-reference-table rows are
the AUTHORITATIVE SOURCE for the relationships they represent; derived edges
in the edges table are PROJECTIONS of those columns. This module computes
the projection.

Per D-019: 8 of 14 Tier 1 edge types are derived_from_column=True. Each
gets a per-source helper here. The orchestrator edges_for_entity dispatches
on entity_type to call the right helpers and assemble the full edge set
for an entity at the current point in time.

Architecture (per design walkthrough, settled with TA second-opinion):
  1. Per-source pure functions (_edges_from_*_row): row dict -> list of
     edge dicts. No DB access, no side effects.
  2. edges_for_entity(entity_id, conn): reads all source rows for the
     entity (detail row + any hot reference rows), calls per-source
     functions, assembles the complete expected edge set. Pure read.
  3. supersede_and_derive(entity_id, new_seq, conn): the lifecycle
     primitive. When sync engine has just superseded an entity (inserted
     new entities row with new valid_from_seq, set old entity's
     valid_to_seq), this:
       a) marks the old entity's currently-active edges as
          valid_to_seq = new_seq;
       b) computes edges_for_entity for the new (current) entity row;
       c) inserts those edges with valid_from_seq = new_seq.
     Idempotent: re-running with same (entity_id, new_seq) produces same
     final state. Safe to retry on partial failure.
  4. verify_derivation_integrity(conn): periodic audit. Scans all
     currently-valid entities, computes expected edges, compares to
     actual. Returns discrepancies. Does NOT auto-repair (repair is
     a deliberate decision, not automatic).

Design constraints honored:
  - Pydantic edge property schemas from edges.py validate every edge
    dict before INSERT (D-016 boundary discipline).
  - Bitemporal supersession only — edges are never DELETE'd by this
    module, only marked superseded via valid_to_seq.
  - Synchronous within sync batch (Decision 3 in design doc).
  - Single source of truth: detail tables. No row written to edges
    here represents data that isn't already authoritative on a detail
    table or hot reference table row.
"""

from __future__ import annotations

import logging
from typing import Optional, Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from primeqa.semantic.edges import (
    TIER_1_EDGES,
    validate_edge_properties,
)


logger = logging.getLogger(__name__)


# ======================================================================
# SECTION 1: Core helpers
# ======================================================================

def _read_entity_row(entity_id: UUID, conn: Connection) -> dict:
    """Read the entities row for entity_id. Raises if not found."""
    row = conn.execute(text("""
        SELECT id, entity_type, sf_api_name, attributes, valid_from_seq, valid_to_seq
        FROM entities WHERE id = :eid
    """), {"eid": entity_id}).mappings().fetchone()
    if row is None:
        raise ValueError(f"Entity {entity_id} not found")
    return dict(row)


def _read_detail_row(table_name: str, entity_id: UUID, conn: Connection) -> Optional[dict]:
    """Read the detail row for entity_id from table_name (e.g., 'field_details').
    Returns None if no detail row exists."""
    # table_name comes from TIER_1_ENTITIES registry, which is a closed set;
    # safe to interpolate into SQL.
    row = conn.execute(
        text(f"SELECT * FROM {table_name} WHERE entity_id = :eid"),
        {"eid": entity_id},
    ).mappings().fetchone()
    return dict(row) if row else None


def _read_validation_rule_field_refs(
    rule_entity_id: UUID, conn: Connection,
) -> list[dict]:
    """Read field_refs rows for a ValidationRule. Returns list of dicts."""
    rows = conn.execute(text("""
        SELECT validation_rule_entity_id, field_entity_id, reference_type,
               is_priorvalue, is_ischanged, is_isnew
        FROM validation_rule_field_refs
        WHERE validation_rule_entity_id = :rid
    """), {"rid": rule_entity_id}).mappings().fetchall()
    return [dict(r) for r in rows]


def _read_record_type_picklist_value_grants(
    record_type_entity_id: UUID, conn: Connection,
) -> list[dict]:
    """Read picklist value grants for a RecordType. Returns list of dicts."""
    rows = conn.execute(text("""
        SELECT record_type_entity_id, picklist_value_entity_id
        FROM record_type_picklist_value_grants
        WHERE record_type_entity_id = :rid
    """), {"rid": record_type_entity_id}).mappings().fetchall()
    return [dict(r) for r in rows]


def _make_edge_dict(
    source_entity_id: UUID,
    target_entity_id: UUID,
    edge_type: str,
    properties: dict,
) -> dict:
    """Construct an edge dict (without valid_from_seq — caller fills that).
    Validates properties against the edge_type's Pydantic schema via
    validate_edge_properties from edges.py."""
    validated = validate_edge_properties(edge_type, properties)
    meta = TIER_1_EDGES[edge_type]
    return {
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id,
        "edge_type": edge_type,
        "edge_category": meta.category,
        "properties": validated,
    }


# ======================================================================
# SECTION 2: Per-source edge generators (pure functions)
# ======================================================================
# Each function takes an entity_id (and possibly attribute / aux row data),
# returns a list of edge dicts. No DB writes, no side effects.

def _edges_from_field_row(
    entity_id: UUID, detail: dict, attributes: dict,
) -> list[dict]:
    """Field row produces 1-3 edges:
    - BELONGS_TO (always; source for STRUCTURAL containment)
    - HAS_RELATIONSHIP_TO (if references_object_entity_id NOT NULL)
    - HAS_PICKLIST_VALUES (if picklist_value_set_entity_id NOT NULL)
    """
    edges = []
    edges.append(_make_edge_dict(
        source_entity_id=entity_id,
        target_entity_id=detail["object_entity_id"],
        edge_type="BELONGS_TO",
        properties={},
    ))
    if detail.get("references_object_entity_id") is not None:
        edges.append(_make_edge_dict(
            source_entity_id=entity_id,
            target_entity_id=detail["references_object_entity_id"],
            edge_type="HAS_RELATIONSHIP_TO",
            properties={},
        ))
    if detail.get("picklist_value_set_entity_id") is not None:
        edges.append(_make_edge_dict(
            source_entity_id=entity_id,
            target_entity_id=detail["picklist_value_set_entity_id"],
            edge_type="HAS_PICKLIST_VALUES",
            properties={},
        ))
    return edges


def _edges_from_record_type_row(
    entity_id: UUID, detail: dict, attributes: dict,
    grants: list[dict],
) -> list[dict]:
    """RecordType row produces:
    - BELONGS_TO to its Object (always)
    - One CONSTRAINS_PICKLIST_VALUES edge per record_type_picklist_value_grants row.
    """
    edges = []
    edges.append(_make_edge_dict(
        source_entity_id=entity_id,
        target_entity_id=detail["object_entity_id"],
        edge_type="BELONGS_TO",
        properties={},
    ))
    for g in grants:
        edges.append(_make_edge_dict(
            source_entity_id=entity_id,
            target_entity_id=g["picklist_value_entity_id"],
            edge_type="CONSTRAINS_PICKLIST_VALUES",
            properties={},
        ))
    return edges


def _edges_from_layout_row(
    entity_id: UUID, detail: dict, attributes: dict,
) -> list[dict]:
    """Layout row produces 1 edge: BELONGS_TO to its Object."""
    return [_make_edge_dict(
        source_entity_id=entity_id,
        target_entity_id=detail["object_entity_id"],
        edge_type="BELONGS_TO",
        properties={},
    )]


def _edges_from_validation_rule_row(
    entity_id: UUID, detail: dict, attributes: dict,
    field_refs: list[dict],
) -> list[dict]:
    """ValidationRule row produces:
    - BELONGS_TO to Object (STRUCTURAL containment)
    - APPLIES_TO to same Object (BEHAVIOR — different category, same target)
    - One REFERENCES edge per validation_rule_field_refs row, with
      properties from the row (reference_type + 3 booleans).
    """
    edges = []
    obj_id = detail["object_entity_id"]
    edges.append(_make_edge_dict(
        source_entity_id=entity_id, target_entity_id=obj_id,
        edge_type="BELONGS_TO", properties={},
    ))
    edges.append(_make_edge_dict(
        source_entity_id=entity_id, target_entity_id=obj_id,
        edge_type="APPLIES_TO", properties={},
    ))
    for ref in field_refs:
        edges.append(_make_edge_dict(
            source_entity_id=entity_id,
            target_entity_id=ref["field_entity_id"],
            edge_type="REFERENCES",
            properties={
                "reference_type": ref["reference_type"],
                "is_priorvalue": ref["is_priorvalue"],
                "is_ischanged": ref["is_ischanged"],
                "is_isnew": ref["is_isnew"],
            },
        ))
    return edges


def _edges_from_picklist_value_row(
    entity_id: UUID, detail: dict, attributes: dict,
) -> list[dict]:
    """PicklistValue row produces 1 edge: BELONGS_TO to its PicklistValueSet."""
    return [_make_edge_dict(
        source_entity_id=entity_id,
        target_entity_id=detail["picklist_value_set_entity_id"],
        edge_type="BELONGS_TO",
        properties={},
    )]


def _edges_from_user_row(
    entity_id: UUID, detail: dict, attributes: dict,
) -> list[dict]:
    """User row produces 1 edge: HAS_PROFILE to assigned Profile.
    (Note: HAS_PROFILE is PERMISSION-category per D-019, not STRUCTURAL.
    profile_entity_id is an assignment FK, not containment.)"""
    return [_make_edge_dict(
        source_entity_id=entity_id,
        target_entity_id=detail["profile_entity_id"],
        edge_type="HAS_PROFILE",
        properties={},
    )]


def _edges_from_flow_row(
    entity_id: UUID, detail: dict, attributes: dict,
) -> list[dict]:
    """Flow row produces 0 or 1 edge: TRIGGERS_ON to Object,
    only if triggers_on_object_entity_id IS NOT NULL.
    Properties carry trigger_type and condition_text from FlowAttributes.
    """
    if detail.get("triggers_on_object_entity_id") is None:
        return []
    trigger_type = detail.get("trigger_type")
    if trigger_type is None:
        # If there's a trigger object but no trigger_type, that's anomalous.
        # Skip rather than fail — this can happen with legacy data.
        logger.warning(
            "Flow %s has triggers_on_object_entity_id but no trigger_type; skipping TRIGGERS_ON edge",
            entity_id,
        )
        return []
    properties = {"trigger_type": trigger_type}
    condition_text = attributes.get("entry_condition_text")
    if condition_text:
        properties["condition_text"] = condition_text
    return [_make_edge_dict(
        source_entity_id=entity_id,
        target_entity_id=detail["triggers_on_object_entity_id"],
        edge_type="TRIGGERS_ON",
        properties=properties,
    )]


# ======================================================================
# SECTION 3: edges_for_entity dispatcher
# ======================================================================

# Maps entity_type -> detail table name. Mirrors TIER_1_ENTITIES.detail_table
# but kept here as a separate constant for explicitness in this module.
_ENTITY_TYPE_TO_DETAIL_TABLE = {
    "Object":         None,  # Object has detail table but no derived edges sourced from it
    "Field":          "field_details",
    "RecordType":     "record_type_details",
    "Layout":         "layout_details",
    "ValidationRule": "validation_rule_details",
    "PicklistValue":  "picklist_value_details",
    "PicklistValueSet": None,  # PVS has no detail table; no derived edges from it
    "Profile":        None,   # Profile has detail table but no derived edges sourced from it
    "PermissionSet":  None,   # Same
    "User":           "user_details",
    "Flow":           "flow_details",
}


def edges_for_entity(entity_id: UUID, conn: Connection) -> list[dict]:
    """Read this entity's source rows and return the edge dicts they imply.
    Stateless; doesn't read or write the edges table.
    Returned dicts lack valid_from_seq — caller fills that based on context.
    """
    entity = _read_entity_row(entity_id, conn)
    entity_type = entity["entity_type"]
    attributes = entity["attributes"] or {}

    # Entity types with no derived-edge sources return empty.
    if entity_type not in _ENTITY_TYPE_TO_DETAIL_TABLE or _ENTITY_TYPE_TO_DETAIL_TABLE[entity_type] is None:
        return []

    detail_table = _ENTITY_TYPE_TO_DETAIL_TABLE[entity_type]
    detail = _read_detail_row(detail_table, entity_id, conn)
    if detail is None:
        # Detail row missing for an entity that should have one.
        # In a well-formed system this shouldn't happen; log and return empty.
        logger.warning(
            "Entity %s of type %s has no row in %s; deriving 0 edges",
            entity_id, entity_type, detail_table,
        )
        return []

    if entity_type == "Field":
        return _edges_from_field_row(entity_id, detail, attributes)
    elif entity_type == "RecordType":
        grants = _read_record_type_picklist_value_grants(entity_id, conn)
        return _edges_from_record_type_row(entity_id, detail, attributes, grants)
    elif entity_type == "Layout":
        return _edges_from_layout_row(entity_id, detail, attributes)
    elif entity_type == "ValidationRule":
        field_refs = _read_validation_rule_field_refs(entity_id, conn)
        return _edges_from_validation_rule_row(entity_id, detail, attributes, field_refs)
    elif entity_type == "PicklistValue":
        return _edges_from_picklist_value_row(entity_id, detail, attributes)
    elif entity_type == "User":
        return _edges_from_user_row(entity_id, detail, attributes)
    elif entity_type == "Flow":
        return _edges_from_flow_row(entity_id, detail, attributes)
    else:
        # Defensive: shouldn't reach here given the gating above.
        return []


# ======================================================================
# SECTION 4: supersede_and_derive (lifecycle primitive)
# ======================================================================

def supersede_and_derive(
    entity_id: UUID, new_seq: int, conn: Connection,
) -> dict:
    """Lifecycle primitive: when an entity has been superseded (new entities
    row with valid_from_seq=new_seq, old row marked valid_to_seq=new_seq),
    update its derived edges to match.

    Steps:
      1) Mark this entity's currently-active edges (where source_entity_id
         = entity_id AND valid_to_seq IS NULL) as valid_to_seq = new_seq.
         EXCEPTION: if an active edge already has valid_to_seq = new_seq
         (shouldn't be possible per WHERE clause, but safety belt) we skip.
      2) Compute edges_for_entity at the current state.
      3) For each computed edge, check if an identical active edge already
         exists (source, target, edge_type, properties JSONB equal).
         - If yes: leave alone (idempotency — re-deriving an unchanged
           edge mid-batch shouldn't double-write).
         - If no: INSERT with valid_from_seq = new_seq.

    Returns: {'superseded': int, 'inserted': int, 'unchanged': int}

    Idempotency: calling this twice with the same (entity_id, new_seq)
    produces the same final state. After the first call, step 1 finds
    no active edges for this entity to supersede (already done), step 3
    finds the new edges already match, so 'unchanged' increments and no
    duplicates are written.

    Concurrency: this function should be called within an outer
    transaction held by the sync engine. If two sync processes attempt
    supersede_and_derive on the same entity concurrently, the unique
    index on (source, target, edge_type) WHERE valid_to_seq IS NULL
    AND edge_type != 'REFERENCES' will reject the second insert. For
    REFERENCES edges, concurrent inserts could produce duplicates;
    sync engine should serialize per-entity to avoid this.
    """
    counts = {"superseded": 0, "inserted": 0, "unchanged": 0}

    # Step 1: supersede currently-active edges from this source entity.
    result = conn.execute(text("""
        UPDATE edges
        SET valid_to_seq = :new_seq
        WHERE source_entity_id = :eid
          AND valid_to_seq IS NULL
          AND valid_from_seq < :new_seq
        RETURNING id
    """), {"eid": entity_id, "new_seq": new_seq})
    superseded_ids = [r[0] for r in result.fetchall()]
    counts["superseded"] = len(superseded_ids)

    # Step 2: compute desired edges from current detail state.
    desired_edges = edges_for_entity(entity_id, conn)

    # Step 3: insert each, skipping any that already exist as active.
    # "Already exists" check uses (source, target, edge_type, properties)
    # for non-REFERENCES; for REFERENCES we additionally compare
    # reference_type since multiple REFERENCES per pair are legitimate.
    for edge in desired_edges:
        # Check for an existing active edge matching this one.
        # Use JSONB equality for properties.
        existing = conn.execute(text("""
            SELECT id FROM edges
            WHERE source_entity_id = :s
              AND target_entity_id = :t
              AND edge_type = :et
              AND properties = CAST(:p AS JSONB)
              AND valid_to_seq IS NULL
            LIMIT 1
        """), {
            "s": edge["source_entity_id"],
            "t": edge["target_entity_id"],
            "et": edge["edge_type"],
            "p": _json_dumps(edge["properties"]),
        }).fetchone()

        if existing is not None:
            counts["unchanged"] += 1
            continue

        conn.execute(text("""
            INSERT INTO edges
                (source_entity_id, target_entity_id, edge_type, edge_category,
                 properties, valid_from_seq)
            VALUES (:s, :t, :et, :ec, CAST(:p AS JSONB), :seq)
        """), {
            "s": edge["source_entity_id"],
            "t": edge["target_entity_id"],
            "et": edge["edge_type"],
            "ec": edge["edge_category"],
            "p": _json_dumps(edge["properties"]),
            "seq": new_seq,
        })
        counts["inserted"] += 1

    return counts


def _json_dumps(obj) -> str:
    """JSON encoding for JSONB params. Module-private helper; kept simple."""
    import json
    return json.dumps(obj, sort_keys=True, default=str)


# ======================================================================
# SECTION 5: verify_derivation_integrity (audit, periodic)
# ======================================================================

def verify_derivation_integrity(conn: Connection) -> list[dict]:
    """Scan all currently-valid entities, compute expected edges, compare
    to actual currently-active edges. Return a list of discrepancies.

    Each discrepancy dict has:
      - entity_id
      - entity_type
      - kind: 'missing' (expected edge not present) or 'extra'
              (active edge not in expected set)
      - edge_type, target_entity_id, properties

    Does NOT auto-repair. Repair (re-running supersede_and_derive at the
    current version_seq) is a deliberate decision, not automatic.

    Performance: this function is O(N) over currently-valid entities.
    Suitable for periodic background runs (e.g., nightly), not for
    request-path use.
    """
    discrepancies: list[dict] = []

    entity_ids = [
        r[0] for r in conn.execute(text("""
            SELECT id FROM entities WHERE valid_to_seq IS NULL
        """)).fetchall()
    ]

    for eid in entity_ids:
        try:
            expected_edges = edges_for_entity(eid, conn)
        except Exception as e:
            logger.warning("verify: error computing edges_for_entity %s: %s", eid, e)
            continue

        # Read actual active edges for this source.
        actual_rows = conn.execute(text("""
            SELECT target_entity_id, edge_type, properties
            FROM edges
            WHERE source_entity_id = :eid AND valid_to_seq IS NULL
        """), {"eid": eid}).mappings().fetchall()
        actual = [dict(r) for r in actual_rows]

        # Index expected by (target, edge_type, properties).
        def edge_key(e: dict) -> tuple:
            return (str(e["target_entity_id"] if "target_entity_id" in e else e["target_entity_id"]),
                    e["edge_type"],
                    _json_dumps(e["properties"] if "properties" in e else e["properties"]))

        expected_keys = {edge_key(e) for e in expected_edges}
        actual_keys = {edge_key(a) for a in actual}

        # Read entity_type for reporting.
        et_row = conn.execute(text("""
            SELECT entity_type FROM entities WHERE id = :eid
        """), {"eid": eid}).fetchone()
        entity_type = et_row[0] if et_row else "<unknown>"

        # Missing: in expected, not in actual.
        for e in expected_edges:
            if edge_key(e) not in actual_keys:
                discrepancies.append({
                    "entity_id": str(eid),
                    "entity_type": entity_type,
                    "kind": "missing",
                    "edge_type": e["edge_type"],
                    "target_entity_id": str(e["target_entity_id"]),
                    "properties": e["properties"],
                })
        # Extra: in actual, not in expected.
        for a in actual:
            if edge_key(a) not in expected_keys:
                discrepancies.append({
                    "entity_id": str(eid),
                    "entity_type": entity_type,
                    "kind": "extra",
                    "edge_type": a["edge_type"],
                    "target_entity_id": str(a["target_entity_id"]),
                    "properties": a["properties"],
                })

    return discrepancies


# ======================================================================
# SECTION 6: Module sanity test (invocable as script)
# ======================================================================

if __name__ == "__main__":
    # Invoked with: PYTHONPATH=. python -m primeqa.semantic.derivation
    # Smokes the public API end-to-end through get_tenant_connection.

    import json
    from primeqa.semantic.connection import get_tenant_connection

    TENANT_ID = 1

    print("=" * 70)
    print("derivation.py module smoke test")
    print("=" * 70)

    # Phase 1: setup. Object + Field with relationship + Field with picklist set.
    with get_tenant_connection(TENANT_ID) as conn:
        seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
        print(f"\n[setup] Using valid_from_seq = {seq}")

        obj_id = conn.execute(text("""
            INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
            VALUES ('Object', '_drv_smoke_account', :s, NOW()) RETURNING id
        """), {"s": seq}).scalar()
        ref_obj_id = conn.execute(text("""
            INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
            VALUES ('Object', '_drv_smoke_user_obj', :s, NOW()) RETURNING id
        """), {"s": seq}).scalar()
        pvs_id = conn.execute(text("""
            INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
            VALUES ('PicklistValueSet', '_drv_smoke_industry_set', :s, NOW()) RETURNING id
        """), {"s": seq}).scalar()
        # Field 1: lookup field with reference + no picklist
        field_lookup_id = conn.execute(text("""
            INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
            VALUES ('Field', '_drv_smoke_OwnerId', :s, NOW()) RETURNING id
        """), {"s": seq}).scalar()
        # Field 2: picklist field with no reference + picklist set
        field_picklist_id = conn.execute(text("""
            INSERT INTO entities (entity_type, sf_api_name, valid_from_seq, last_synced_at)
            VALUES ('Field', '_drv_smoke_Industry', :s, NOW()) RETURNING id
        """), {"s": seq}).scalar()

        conn.execute(text("""
            INSERT INTO field_details
                (entity_id, object_entity_id, references_object_entity_id,
                 field_type, is_custom, is_unique, is_external_id, is_nillable,
                 is_calculated, is_filterable, is_sortable)
            VALUES (:f, :o, :ref, 'lookup', FALSE, FALSE, FALSE, TRUE,
                    FALSE, TRUE, TRUE)
        """), {"f": field_lookup_id, "o": obj_id, "ref": ref_obj_id})
        conn.execute(text("""
            INSERT INTO field_details
                (entity_id, object_entity_id, picklist_value_set_entity_id,
                 field_type, is_custom, is_unique, is_external_id, is_nillable,
                 is_calculated, is_filterable, is_sortable)
            VALUES (:f, :o, :pvs, 'picklist', FALSE, FALSE, FALSE, TRUE,
                    FALSE, TRUE, TRUE)
        """), {"f": field_picklist_id, "o": obj_id, "pvs": pvs_id})
        print(f"[setup] Created Object, ref-Object, PVS, lookup Field, picklist Field with detail rows")

    # Phase 2: edges_for_entity for each. Verify expected counts and edges.
    with get_tenant_connection(TENANT_ID) as conn:
        # Lookup field: should produce BELONGS_TO + HAS_RELATIONSHIP_TO (2 edges)
        edges = edges_for_entity(field_lookup_id, conn)
        edge_types = sorted(e["edge_type"] for e in edges)
        assert len(edges) == 2, f"FAIL: lookup field should produce 2 edges, got {len(edges)}: {edges}"
        assert edge_types == ["BELONGS_TO", "HAS_RELATIONSHIP_TO"], f"FAIL: got types {edge_types}"
        print(f"[T2a] Lookup field -> {edge_types}")

        # Picklist field: should produce BELONGS_TO + HAS_PICKLIST_VALUES (2 edges)
        edges = edges_for_entity(field_picklist_id, conn)
        edge_types = sorted(e["edge_type"] for e in edges)
        assert len(edges) == 2, f"FAIL: picklist field should produce 2 edges, got {len(edges)}: {edges}"
        assert edge_types == ["BELONGS_TO", "HAS_PICKLIST_VALUES"], f"FAIL: got types {edge_types}"
        print(f"[T2b] Picklist field -> {edge_types}")

        # Object: no derived edges (Object is target, not source, in derivation paths)
        edges = edges_for_entity(obj_id, conn)
        assert len(edges) == 0, f"FAIL: Object should produce 0 edges, got {len(edges)}"
        print(f"[T2c] Object -> 0 edges (correct)")

    # Phase 3: supersede_and_derive on lookup field at current seq (no actual
    # supersession happened — we just call it as if). Initial state: no edges
    # in DB for this entity. Should insert 2.
    with get_tenant_connection(TENANT_ID) as conn:
        seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
        counts = supersede_and_derive(field_lookup_id, seq, conn)
        print(f"[T3a] supersede_and_derive(lookup field, seq={seq}): {counts}")
        assert counts == {"superseded": 0, "inserted": 2, "unchanged": 0}, \
            f"FAIL: expected 0 superseded / 2 inserted / 0 unchanged, got {counts}"

    # Phase 4: idempotency. Re-call supersede_and_derive at the same seq.
    # Expected: 0 superseded (no active edges with seq < seq), 0 inserted
    # (existing edges match desired set), 2 unchanged.
    with get_tenant_connection(TENANT_ID) as conn:
        seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
        counts = supersede_and_derive(field_lookup_id, seq, conn)
        print(f"[T4] Re-call (idempotency) supersede_and_derive: {counts}")
        assert counts == {"superseded": 0, "inserted": 0, "unchanged": 2}, \
            f"FAIL: idempotency violated: {counts}"

    # Phase 5: now derive for picklist field too (so it has edges in the DB).
    with get_tenant_connection(TENANT_ID) as conn:
        seq = conn.execute(text("SELECT MAX(version_seq) FROM logical_versions")).scalar()
        counts = supersede_and_derive(field_picklist_id, seq, conn)
        print(f"[T5] supersede_and_derive(picklist field): {counts}")
        assert counts["inserted"] == 2

    # Phase 6: verify_derivation_integrity should report no discrepancies
    # for the entities we've derived, but might report 'missing' for entities
    # that exist but haven't been derived yet (Object, ref-Object, PVS in
    # this smoke).
    with get_tenant_connection(TENANT_ID) as conn:
        discrepancies = verify_derivation_integrity(conn)
        # Filter to only our smoke entities
        smoke_eids = {str(field_lookup_id), str(field_picklist_id), str(obj_id),
                       str(ref_obj_id), str(pvs_id)}
        smoke_disc = [d for d in discrepancies if d["entity_id"] in smoke_eids]
        # Object/ref-Object/PVS produce 0 expected edges and have 0 actual,
        # so no discrepancies. Lookup and picklist fields have all their
        # edges. So smoke_disc should be empty.
        assert len(smoke_disc) == 0, \
            f"FAIL: expected 0 smoke discrepancies, got {len(smoke_disc)}: {smoke_disc}"
        print(f"[T6] verify_derivation_integrity: 0 smoke discrepancies (good)")

    # Phase 7: introduce a deliberate inconsistency — manually supersede an
    # edge in the DB without updating the source. verify should catch it.
    # Need a logical_version with seq > 1 first (Phase 0 genesis is seq=1
    # and edges were inserted with valid_from_seq=1; the CHECK constraint
    # requires valid_to_seq > valid_from_seq strictly).
    with get_tenant_connection(TENANT_ID) as conn:
        new_seq = conn.execute(text("""
            INSERT INTO logical_versions (version_name, version_type, description)
            VALUES ('_drv_smoke_test_v2', 'manual_checkpoint',
                    'derivation smoke Phase 7 supersession test')
            RETURNING version_seq
        """)).scalar()
        print(f"[T7-prep] Created logical_version seq={new_seq} for supersession test")

    with get_tenant_connection(TENANT_ID) as conn:
        # Mark one of the lookup field's edges as superseded "manually"
        result = conn.execute(text("""
            UPDATE edges
            SET valid_to_seq = :new_seq
            WHERE source_entity_id = :eid AND edge_type = 'BELONGS_TO'
              AND valid_to_seq IS NULL
            RETURNING id
        """), {"eid": field_lookup_id, "new_seq": new_seq})
        affected = result.fetchall()
        assert len(affected) == 1, f"Expected to mark 1 edge; got {len(affected)}"
        print(f"[T7-prep] Manually superseded BELONGS_TO edge for lookup field at seq={new_seq}")

    with get_tenant_connection(TENANT_ID) as conn:
        discrepancies = verify_derivation_integrity(conn)
        smoke_disc = [
            d for d in discrepancies
            if d["entity_id"] == str(field_lookup_id) and d["kind"] == "missing"
            and d["edge_type"] == "BELONGS_TO"
        ]
        assert len(smoke_disc) == 1, \
            f"FAIL: expected 1 missing BELONGS_TO for lookup field, got {len(smoke_disc)}"
        print(f"[T7] verify_derivation_integrity caught 1 'missing' BELONGS_TO for lookup field (correct)")

    # Cleanup. Delete fields first (CASCADE clears their edges + detail rows),
    # then PVS, then Objects.
    with get_tenant_connection(TENANT_ID) as conn:
        # Delete edges first since they reference entities (no CASCADE there)
        conn.execute(text("""
            DELETE FROM edges
            WHERE source_entity_id IN (
                SELECT id FROM entities WHERE sf_api_name LIKE '_drv_smoke_%'
            )
        """))
        conn.execute(text("""
            DELETE FROM entities
            WHERE sf_api_name IN ('_drv_smoke_OwnerId', '_drv_smoke_Industry')
        """))
    with get_tenant_connection(TENANT_ID) as conn:
        conn.execute(text("DELETE FROM entities WHERE sf_api_name = '_drv_smoke_industry_set'"))
        conn.execute(text("""
            DELETE FROM entities
            WHERE sf_api_name IN ('_drv_smoke_account', '_drv_smoke_user_obj')
        """))
    with get_tenant_connection(TENANT_ID) as conn:
        residue = conn.execute(text("""
            SELECT count(*) FROM entities WHERE sf_api_name LIKE '_drv_smoke_%'
        """)).scalar()
        assert residue == 0, f"FAIL: {residue} entities leaked"

    print("\n" + "=" * 70)
    print("derivation.py module smoke test: PASS")
    print("=" * 70)
