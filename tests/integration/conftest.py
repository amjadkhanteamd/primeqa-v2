"""Integration-test conftest. Provides DB connection fixtures + cleanup.

Tests are slower than unit tests and depend on tenant_1 schema being fully
migrated (at least up to 20260427_0150). They use the actual Railway DB
via get_tenant_connection.
"""
import pytest


TENANT_ID = 1


@pytest.fixture
def tenant_id():
    return TENANT_ID


@pytest.fixture
def conn_factory():
    """Returns a callable producing a fresh transactional connection."""
    from primeqa.semantic.connection import get_tenant_connection
    return lambda: get_tenant_connection(TENANT_ID)


@pytest.fixture
def cleanup_test_entities(conn_factory):
    """After-test cleanup helper. Tests register prefixes they used;
    fixture deletes any matching entities + cascading state at teardown.

    Usage:
        def test_something(cleanup_test_entities):
            cleanup_test_entities.add('_test_xyz_')

    Cleanup runs as a 7-pass ordered deletion respecting all FK
    dependencies in the schema:
      Pass 1: edges from any test source (no CASCADE from entities)
      Pass 2: hot-reference-table rows where the no-CASCADE side
              (field_entity_id, picklist_value_entity_id) is a test
              entity. The CASCADE side is handled when the rule/RT
              entity itself is deleted.
      Pass 3: ValidationRule entities (CASCADE clears their
              detail rows + field_refs from rule_id side)
      Pass 4: RecordType entities (CASCADE clears detail + grants
              from rule_id side)
      Pass 5: Other "leaf" entity types (User, Field, Layout, Flow,
              PermissionSet, Profile, PicklistValue) — no other
              entity types have FKs pointing at them at this point
      Pass 6: Parent entity types (Object, PicklistValueSet)
      Pass 7: logical_versions test rows (matched by version_name prefix)

    The order matters because:
    - Fields cannot be deleted while ValidationRules reference them via
      field_refs (no CASCADE on field_entity_id)
    - PicklistValues cannot be deleted while RecordTypes grant them
      (no CASCADE on picklist_value_entity_id)
    - Objects cannot be deleted while Field/RT/Layout/VR detail rows
      reference them
    - PicklistValueSets cannot be deleted while PVs reference them
    """
    from sqlalchemy import text

    prefixes_to_clean: list[str] = []

    class _Cleaner:
        def add(self, prefix: str):
            prefixes_to_clean.append(prefix)

    yield _Cleaner()

    if not prefixes_to_clean:
        return

    # Pass 1: edges from any test source
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM edges WHERE source_entity_id IN (
                    SELECT id FROM entities WHERE sf_api_name LIKE :p
                )
            """), {"p": f"{pref}%"})

    # Pass 2: hot-reference-table rows where the no-CASCADE side is a test entity
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM validation_rule_field_refs
                WHERE field_entity_id IN (
                    SELECT id FROM entities WHERE sf_api_name LIKE :p
                )
            """), {"p": f"{pref}%"})
            conn.execute(text("""
                DELETE FROM record_type_picklist_value_grants
                WHERE picklist_value_entity_id IN (
                    SELECT id FROM entities WHERE sf_api_name LIKE :p
                )
            """), {"p": f"{pref}%"})

    # Pass 3: ValidationRule entities
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM entities
                WHERE sf_api_name LIKE :p AND entity_type = 'ValidationRule'
            """), {"p": f"{pref}%"})

    # Pass 4: RecordType entities
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM entities
                WHERE sf_api_name LIKE :p AND entity_type = 'RecordType'
            """), {"p": f"{pref}%"})

    # Pass 5: User entities (must precede Profile because
    # user_details.profile_entity_id is a no-cascade FK to Profile)
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM entities
                WHERE sf_api_name LIKE :p AND entity_type = 'User'
            """), {"p": f"{pref}%"})

    # Pass 6: other leaf entity types (Field/Layout/Flow have FKs to
    # Object; PicklistValue has FK to PicklistValueSet — handled in
    # parent pass; Profile/PermissionSet have no inbound entity FKs
    # at this point since User is already deleted)
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM entities
                WHERE sf_api_name LIKE :p
                  AND entity_type IN ('Field', 'Layout', 'Flow',
                                      'PermissionSet', 'Profile', 'PicklistValue')
            """), {"p": f"{pref}%"})

    # Pass 7: parent entity types
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM entities
                WHERE sf_api_name LIKE :p
                  AND entity_type IN ('Object', 'PicklistValueSet')
            """), {"p": f"{pref}%"})

    # Pass 8: logical_versions test rows
    with conn_factory() as conn:
        for pref in prefixes_to_clean:
            conn.execute(text("""
                DELETE FROM logical_versions WHERE version_name LIKE :p
            """), {"p": f"{pref}%"})
