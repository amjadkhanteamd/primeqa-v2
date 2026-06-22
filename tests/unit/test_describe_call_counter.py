"""Phase-1 close-out #4a — describe-class API call counter.

Red-proofs:
  * The SF client counts EVERY describe-class call (global describe, per-object
    describe, bulk /composite/batch per chunk, describe/layouts) and NOTHING else
    (SOQL / tooling queries don't count) — accurate, no double-count, no miss.
  * increment_run_counters references the describe_calls column ONLY when the
    delta is non-zero (so the embeddings/summaries worker path can't regress on a
    tenant whose schema lacks the column).
"""
import pytest

pytestmark = pytest.mark.unit


class _Resp:
    """A stand-in HTTP response whose .json() satisfies every describe parser."""
    def json(self):
        return {
            "sobjects": [],
            "fields": [],
            "results": [],     # /composite/batch body
            "layouts": [],
            "records": [],
        }


def _client():
    from primeqa.integrations.sf_client import SalesforceClient
    c = SalesforceClient(
        instance_url="https://example.my.salesforce.com",
        client_id="cid", client_secret="sec", refresh_token="rt",
    )
    c._request = lambda *a, **k: _Resp()      # no network
    c._query_all = lambda *a, **k: []         # tooling SOQL → no describe calls
    return c


class TestSfClientDescribeCounter:
    def test_starts_at_zero(self):
        assert _client().describe_calls == 0

    def test_global_describe_counts_one(self):
        c = _client()
        c.fetch_objects()
        assert c.describe_calls == 1

    def test_per_object_describe_counts_one(self):
        c = _client()
        c.fetch_fields_for_object("Account")
        assert c.describe_calls == 1

    def test_bulk_counts_once_per_chunk(self):
        c = _client()
        # 30 objects → 2 chunks of 25 → 2 /composite/batch describe calls.
        c.fetch_fields_for_objects_bulk([f"Obj{i}" for i in range(30)])
        assert c.describe_calls == 2

    def test_bulk_single_chunk(self):
        c = _client()
        c.fetch_fields_for_objects_bulk([f"Obj{i}" for i in range(10)])
        assert c.describe_calls == 1

    def test_bulk_empty_makes_no_call(self):
        c = _client()
        c.fetch_fields_for_objects_bulk([])
        assert c.describe_calls == 0

    def test_layouts_describe_counts_one(self):
        c = _client()
        c.fetch_layouts_for_object("Account")
        assert c.describe_calls == 1

    def test_tooling_queries_not_counted(self):
        c = _client()
        c.fetch_global_value_sets()
        c.fetch_standard_value_sets(labels=None)
        assert c.describe_calls == 0

    def test_no_double_count_no_miss(self):
        c = _client()
        c.fetch_objects()                                  # +1
        c.fetch_fields_for_object("Account")               # +1
        c.fetch_fields_for_objects_bulk([f"O{i}" for i in range(10)])  # +1
        c.fetch_layouts_for_object("Account")              # +1
        c.fetch_global_value_sets()                        # +0 (tooling)
        assert c.describe_calls == 4


class _RecordingSession:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, stmt, params=None):
        self.sql = str(stmt)
        self.params = params


class TestIncrementRunCountersDescribeColumn:
    def test_all_zero_is_noop(self):
        from primeqa.sync.readiness import increment_run_counters
        s = _RecordingSession()
        increment_run_counters(s, "org-1")
        assert s.sql is None                               # no round-trip

    def test_embedding_path_does_not_touch_describe_column(self):
        from primeqa.sync.readiness import increment_run_counters
        s = _RecordingSession()
        increment_run_counters(s, "org-1", embeddings_delta=3)
        assert s.sql is not None
        assert "describe_calls" not in s.sql               # no regression for unmigrated tenants
        assert "dc" not in (s.params or {})

    def test_describe_delta_references_column(self):
        from primeqa.sync.readiness import increment_run_counters
        s = _RecordingSession()
        increment_run_counters(s, "org-1", describe_calls_delta=7)
        assert "describe_calls = describe_calls + :dc" in s.sql
        assert s.params["dc"] == 7
