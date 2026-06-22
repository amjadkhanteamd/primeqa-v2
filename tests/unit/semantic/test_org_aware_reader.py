"""per-org Slice 2 (D-256) — SemanticOrgModel org-scoping, SQL-shape red-proofs.

Offline (recording connection, no DB). Two guards:

  * REGRESSION — org=None (the default) produces SQL byte-identical to the
    pre-Slice-2 reader: NO ``connected_org_id`` in ANY read, no ``:org`` param.
    This is the hard backward-compat contract — every existing org-blind consumer
    sees the same SQL → the same results.
  * MECHANISM — org=X scopes EVERY entity/edge/version read: the predicate
    ``connected_org_id = CAST(:org AS uuid)`` is present on the right alias and
    ``:org`` is bound to X. (Detail tables carry no org column — they are scoped
    transitively through the entities join; the predicate targets that join.)

The end-to-end *behavioral* discrimination (org=A returns only A's rows) is the
integration test ``tests/integration/semantic/test_org_aware_reader_live.py``;
here we prove the query the reader emits.
"""
import pytest
from uuid import uuid4

pytestmark = pytest.mark.unit

from primeqa.semantic.query import SemanticOrgModel

ORG = "11111111-2222-3333-4444-555555555555"
_SCOPE = "connected_org_id = CAST(:org AS uuid)"


class _Rec:
    """Records every (sql, params) execute; returns benign empties."""

    def __init__(self):
        self.calls = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return self

    def scalar(self):
        return 7  # a version exists (current_version_seq / _validate_version)

    def mappings(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


def _exercise(org):
    """Run every read method once; return the recorded (sql, params) calls."""
    rec = _Rec()
    m = SemanticOrgModel(rec, connected_org_id=org)
    m.current_version_seq()        # validates + caches seq 7
    rec.calls.clear()              # drop the version probe; keep the reads below
    eid = uuid4()
    m.get_entities("Object", at_seq=7, filters={"sf_api_name": "Account"})
    m.get_related(eid, ["BELONGS_TO"], "both", at_seq=7)
    m.get_entity_details(eid, at_seq=7)
    m.get_picklist_values(eid, at_seq=7)
    m.get_entity_details_bulk("Object", at_seq=7)
    m.get_related_bulk(["BELONGS_TO"], "inbound", at_seq=7)
    m.get_picklist_values_bulk(at_seq=7)
    return rec.calls


class TestConstructorBackwardCompat:
    def test_org_defaults_to_none(self):
        # Every one of the 13 consumers constructs SemanticOrgModel(conn) — the
        # added param must be optional and default to org-blind.
        m = SemanticOrgModel(_Rec())
        assert m._org is None

    def test_current_version_seq_unscoped_when_blind(self):
        rec = _Rec()
        SemanticOrgModel(rec).current_version_seq()
        sql, params = rec.calls[-1]
        assert "MAX(version_seq) FROM logical_versions" in sql
        assert "connected_org_id" not in sql
        assert "org" not in params

    def test_current_version_seq_scoped_when_bound(self):
        rec = _Rec()
        SemanticOrgModel(rec, connected_org_id=ORG).current_version_seq()
        sql, params = rec.calls[-1]
        assert "WHERE logical_versions." + _SCOPE in sql
        assert params["org"] == ORG


class TestRegressionOrgBlindIdentical:
    def test_no_org_predicate_in_any_read(self):
        calls = _exercise(None)
        assert len(calls) == 7  # the seven reads after current_version_seq
        offenders = [sql for sql, _ in calls if "connected_org_id" in sql]
        assert offenders == [], f"org leaked into org-blind SQL:\n{offenders}"

    def test_no_org_param_bound(self):
        calls = _exercise(None)
        assert all("org" not in p for _, p in calls)


class TestMechanismEveryReadScoped:
    def test_every_read_carries_the_org_predicate_and_param(self):
        calls = _exercise(ORG)
        assert len(calls) == 7
        for sql, params in calls:
            assert _SCOPE in sql, f"read NOT org-scoped:\n{sql}"
            assert params.get("org") == ORG

    def test_entities_scoped_on_entities_alias(self):
        rec = _Rec()
        SemanticOrgModel(rec, connected_org_id=ORG).get_entities("Object", at_seq=7)
        sql = rec.calls[-1][0]
        assert "entities." + _SCOPE in sql

    def test_edges_scoped_on_edge_alias(self):
        rec = _Rec()
        SemanticOrgModel(rec, connected_org_id=ORG).get_related_bulk(
            ["BELONGS_TO"], "inbound", at_seq=7)
        sql = rec.calls[-1][0]
        assert "e." + _SCOPE in sql  # the edge alias

    def test_detail_bulk_scoped_via_entities_join(self):
        # Detail tables have no org column (D-025) — the scope rides the join.
        rec = _Rec()
        SemanticOrgModel(rec, connected_org_id=ORG).get_entity_details_bulk(
            "Object", at_seq=7)
        sql = rec.calls[-1][0]
        assert "JOIN entities e" in sql and "e." + _SCOPE in sql
