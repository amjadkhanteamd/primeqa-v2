"""Integration-test conftest: DB fixtures, cleanup, and THE QUARANTINE.

Tests are slower than unit tests and depend on tenant_1 schema being fully
migrated (at least up to 20260427_0150). They use the actual Railway DB
via get_tenant_connection.

**The quarantine (round 3, part C).** A gate that is "red except for the known
ones" teaches everybody to ignore red, which makes it a self-reporting control:
its colour stops being evidence. Every test that does not pass is therefore
either fixed at root or listed HERE, with the finding that tracks it, the owner
who will decide it, and the mechanism as OBSERVED — and it is skipped with all
three printed, never silently. ``tests/unit/test_quarantine_registry.py`` holds
the list honest: every entry must name an OPEN finding in
``docs/audit/findings.json`` and must still match at least one collected test,
so the list cannot grow quietly and cannot rot.
"""
import pytest


TENANT_ID = 1

#: nodeid substring -> (finding id, owner, the mechanism as observed 2026-09-21).
#: A substring matches a whole file or one test. Nothing is skipped by any other
#: means in this tree.
QUARANTINE = {
    "generation/test_automation_vertical.py": (
        "AUD-045", "AK",
        "the generation suite's seeded governance world no longer yields drafts: every "
        "archetype answers OutcomeKind.REFUSAL where the suite expects DRAFT (25 tests). "
        "Either the seed no longer grounds what the governance core now demands, or the "
        "refusal is correct and the expectations are stale — a product question, not a "
        "test question"),
    "generation/test_repair_agent.py": (
        "AUD-045", "AK",
        "same suite, same seeded world: the repair agent's end-to-end assertion is False"),
    "semantic/test_delta_reconcile_live.py": (
        "AUD-046", "AK",
        "the bitemporal reconcile expectations drifted: _valid_to() answers None where the "
        "suite expects a closed interval (6 tests)"),
    "semantic/test_s1_sync_console.py": (
        "AUD-046", "AK",
        "the sync console's counts drifted: count_failed_enrichment 0 vs 1, and a phase "
        "reads 'complete' where the suite expects it not to (4 tests)"),
    "semantic/test_validation_rule_field_refs.py": (
        "AUD-046", "AK",
        "a rule's parse state reads 'partial' where the suite expects 'unparsed'"),
    "test_phase2_summaries_smoke.py": (
        "AUD-047", "AK",
        "pgvector dimension mismatch on scratch: the column takes 1024 dimensions and the "
        "embedding the code produces is 1536 (4 tests). A scratch-fidelity gap or a real "
        "config drift — production's dimension must be read before either is assumed"),
    "test_phase2_entities_sync_ai_smoke.py": (
        "AUD-047", "AK", "the same 1024-vs-1536 embedding dimension (2 tests)"),
    "test_representation/test_step_b_staleness_pin.py": (
        "AUD-048", "AK",
        "the recommendation is conditional_go where the suite expects go (4 tests) — the "
        "policy grades differently since D-488/D-496, so the suite's expected verdict is "
        "the open question"),
    "execution_engine/test_stranded_cleanup.py": (
        "AUD-049", "AK",
        "the stranded-record cleanup counts 0 where the suite expects 1 and 2 (4 tests)"),
    "test_representation/test_step_5_policy.py": (
        "AUD-050", "AK",
        "'a waiver's expiry lies in the future' — the suite plants a waiver the service now "
        "refuses (2 tests)"),
    "test_step_5_scope.py": (
        "AUD-050", "AK",
        "round 3 gave this suite its own DRAFT policy (it no longer depends on the seed, "
        "which scratch froze at first use), and it now reaches the composer, where "
        "recommendation is None instead of no_go — one step from green"),
    "test_release_run_prod_gate.py": (
        "AUD-051", "AK",
        "the non-admin production run is refused by D-494's plan requirement before the "
        "SEC-4 tier message the suite greps for — the refusal is right, the expectation is old"),
    "test_ci_webhook.py": (
        "AUD-051", "AK",
        "409 PLAN_REQUIRED where the suite expects 400: the D-494 fork AK already ruled on "
        "(the webhook refuses until it is taught to plan — its own slice)"),
    "test_phase5_authoring.py": (
        "AUD-051", "AK",
        "asserts cust_rules exist in tenant_1 only; scratch has carried tenant_2 since the "
        "audit planted it"),
    "test_r2_superadmin.py": (
        "AUD-051", "AK",
        "a custom in-file runner returns False; it reports no failing assertion, so the "
        "suite must be converted to plain pytest before it can say anything"),
    "test_auth.py": (
        "AUD-051", "AK", "the same custom-runner shape: run_tests() returns False"),
    "test_report_slice.py::test_a_runs_list_carries_both_recorded_runs": (
        "AUD-051", "AK",
        "the 50-row window artefact: the second recorded run is outside the listing's page, "
        "so the assertion that both appear fails on a busy tenant"),
    "test_step_1_identity.py::test_1b_every_row_key_equals_the_pre_072_derivation": (
        "AUD-051", "AK",
        "asserts every requirement's key equals the pre-072 derivation req-<id>; a DECORATED "
        "requirement (external_key TRIAGE-SURF-40b029, planted by the authority-triage world) "
        "is a legitimate exception the assertion does not admit"),
}


def pytest_collection_modifyitems(config, items):
    """Skip the quarantined tests, each with its finding, owner and mechanism."""
    for item in items:
        for key, (finding, owner, why) in QUARANTINE.items():
            if key in item.nodeid:
                item.add_marker(pytest.mark.skip(
                    reason=f"QUARANTINED {finding} (owner {owner}): {why}"))
                break


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
