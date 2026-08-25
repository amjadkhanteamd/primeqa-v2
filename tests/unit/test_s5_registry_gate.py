"""3A-1 gate tests (pure, network-free, merge-gate suite).

1. HASH EQUALITY (LLD §5): store-referenced artifact == the vendored file ==
   the seed fixture's recorded sha == the literal in the seed SQL. The
   manifest pin later reads the same store row, so this chain IS the pin's
   integrity anchor.
2. Fixture well-formedness: frozen deterministic PLM ids, no WCAG-2.2-removed
   4.1.1 criteria, capability vocabulary.
3. Lifecycle pure guards: transition table, bootstrap non-repeatability,
   superadmin gate — no DB needed (refusals fire before any SQL).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_AXE = _ROOT / "primeqa" / "browser_worker" / "vendor" / "axe.min.js"
_FIXTURE = _ROOT / "migrations" / "seeds" / "s5_rule_seed_axe4130_wcag22.json"
_SEED_SQL = _ROOT / "migrations" / "063_s5_rule_seed.sql"


def _fixture():
    return json.loads(_FIXTURE.read_text())


def test_hash_equality_artifact_fixture_seed_sql():
    file_sha = hashlib.sha256(_AXE.read_bytes()).hexdigest()
    fx = _fixture()
    assert fx["derivation"]["artifact_sha256"] == file_sha, \
        "fixture sha != hashed vendored artifact"
    assert file_sha in _SEED_SQL.read_text(), \
        "seed SQL does not pin the artifact sha"
    # and the spike's own record agrees (vendor/VERSIONS.md)
    versions_md = (_AXE.parent / "VERSIONS.md").read_text()
    assert file_sha in versions_md


def test_fixture_ids_frozen_deterministic_and_wellformed():
    fx = _fixture()
    rules = fx["rules"]
    assert len(rules) >= 60                      # the axe4130/WCAG22 seed set
    ids = [r["plm_id"] for r in rules]
    assert len(set(ids)) == len(ids)             # unique
    shape = re.compile(r"^PLM-A11Y-\d{3}$")
    assert all(shape.match(i) for i in ids)
    # deterministic assignment: engine ids sorted lexicographically = id order
    engine_ids = [r["engine_rule_id"] for r in rules]
    assert engine_ids == sorted(engine_ids)
    assert ids == [f"PLM-A11Y-{n:03d}" for n in range(1, len(rules) + 1)]
    # WCAG 2.2 removed 4.1.1: it must appear nowhere
    for r in rules:
        assert all(c["criterion"] != "4.1.1" for c in r["wcag22"])
        assert r["automation_capability"] in (
            "AUTO", "AUTO_WITH_ACTION", "HUMAN_WITH_CANDIDATE", "HUMAN_ONLY")
        assert r["wcag22"], f"{r['plm_id']} has no criteria"
    # the 4.1.1 exclusion is recorded, not silent
    assert fx["derivation"]["wcag411_exclusion"]["engine_rules_affected"]


class _NoSQLSession:
    def execute(self, *a, **k):
        raise AssertionError("refusal must fire before any SQL")


def test_transition_table_is_strictly_sequential():
    from primeqa.knowledge.rule_lifecycle import _TRANSITIONS
    assert _TRANSITIONS == {"DRAFT": "REVIEW", "REVIEW": "APPROVED",
                            "APPROVED": "VERSIONED", "VERSIONED": "ACTIVE",
                            "ACTIVE": "RETIRED"}


def test_bootstrap_guard_refuses_direct_to_active_without_provenance():
    from primeqa.knowledge.rule_lifecycle import LifecycleError, _insert_version
    with pytest.raises(LifecycleError) as ei:
        _insert_version(_NoSQLSession(), "PLM-A11Y-999", 1, "n", "d",
                        "AUTO", False, state="ACTIVE",
                        seed_provenance=None, actor_user_id=1)
    assert "bootstrap" in str(ei.value)
    # non-bootstrap provenance is equally refused
    with pytest.raises(LifecycleError):
        _insert_version(_NoSQLSession(), "PLM-A11Y-999", 1, "n", "d",
                        "AUTO", False, state="ACTIVE",
                        seed_provenance={"bootstrap": False}, actor_user_id=1)
    # any non-DRAFT state is refused, not just ACTIVE
    with pytest.raises(LifecycleError):
        _insert_version(_NoSQLSession(), "PLM-A11Y-999", 1, "n", "d",
                        "AUTO", False, state="APPROVED",
                        seed_provenance=None, actor_user_id=1)


def test_lifecycle_writes_are_superadmin_only():
    from primeqa.knowledge.rule_lifecycle import LifecycleError, create_rule
    with pytest.raises(LifecycleError) as ei:
        create_rule(_NoSQLSession(), rule_id="PLM-A11Y-999", name="n",
                    description="d", automation_capability="AUTO",
                    human_review_required=False, actor_user_id=1,
                    actor_tenant_id=1, actor_role="admin")
    assert "superadmin" in str(ei.value)
