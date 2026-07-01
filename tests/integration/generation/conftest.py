"""Integration harness for the substrate-3 refusal vertical (D-096).

Local PG, real S1 grounding. Stands up the substrate schema (alembic shared +
tenant), seeds S1 ``entities`` / ``edges`` across a logical_version
**committed** (so ``SemanticOrgModel`` reads and the persister's writes both
see them), and cleans the generation ledger between tests. Persistence runs
the production path (``get_tenant_connection``), so this conftest points
``DATABASE_URL`` at the local test DB and **safety-asserts** it is not the
.env Railway URL.

Skips cleanly if PG is unreachable.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

DEFAULT_TEST_DB_URL = "postgresql://localhost/primeqa_test_governance"
TEST_TENANT_ID = 1
# D-286: the test env whose connected_org the seed is provisioned for. Generation
# now resolves env→org and scopes S1 reads + the version pin to that org, so every
# run_generation / enqueue test threads this env, and the seed tags its entities +
# edges with the env's org. A single-org test world: scoped reads == org-blind
# reads (the predicate is vacuous on one org) — the no-op that proves single-org
# generation is byte-identical.
TEST_ENV_ID = 590
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _admin_url(url: str) -> str:
    prefix = url.rsplit("/", 1)[0] if "/" in url.rsplit("@", 1)[-1] else url
    return f"{prefix}/postgres"


def _pg_reachable(admin_url: str) -> tuple[bool, str]:
    try:
        eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        eng.dispose()
        return True, ""
    except OperationalError as e:
        return False, str(e)
    except Exception as e:  # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


def _truthy(v) -> bool:
    return bool(v) and str(v).lower() not in ("0", "false", "no", "")


def _alembic(db_url: str, *, mode: str, tenant_id=None) -> None:
    cmd = ["alembic", "-x", f"mode={mode}"]
    if tenant_id is not None:
        cmd += ["-x", f"tenant_id={tenant_id}"]
    cmd += ["upgrade", f"{mode}@head"]
    r = subprocess.run(cmd, cwd=str(_REPO_ROOT),
                       env={**os.environ, "DATABASE_URL": db_url},
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"alembic {mode} failed:\n{r.stdout}\n{r.stderr}")


@pytest.fixture(scope="session")
def test_db_url() -> str:
    return os.environ.get("GOVERNANCE_TEST_DB_URL", DEFAULT_TEST_DB_URL)


@pytest.fixture(scope="session", autouse=True)
def db_setup(test_db_url: str):
    admin_url = _admin_url(test_db_url)
    ok, err = _pg_reachable(admin_url)
    if not ok:
        pytest.skip(f"local PostgreSQL not reachable at {admin_url!r}: {err}",
                    allow_module_level=True)

    db_name = test_db_url.rsplit("/", 1)[-1]
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(text("SELECT 1 FROM pg_database WHERE datname=:n"),
                         {"n": db_name}).first():
            c.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()

    tgt = create_engine(test_db_url, isolation_level="AUTOCOMMIT")
    with tgt.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    tgt.dispose()

    _alembic(test_db_url, mode="shared")
    _alembic(test_db_url, mode="tenant", tenant_id=TEST_TENANT_ID)

    # Point get_tenant_connection (production path) at the local test DB and
    # reset the cached engine so it rebinds. SAFETY: never the Railway URL.
    os.environ["DATABASE_URL"] = test_db_url
    from primeqa.semantic import connection as _conn
    _conn._engine = None
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as c:
        eff = str(c.engine.url)
    assert "primeqa_test_governance" in eff or test_db_url.rsplit("/", 1)[-1] in eff, \
        f"SAFETY ABORT: get_tenant_connection bound to {eff!r}, not the local test DB"

    yield

    if not _truthy(os.environ.get("GOVERNANCE_KEEP_TEST_DB")):
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE datname=:n AND pid<>pg_backend_pid()"), {"n": db_name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


@pytest.fixture(autouse=True)
def _rebind_production_engine(db_setup, test_db_url):
    """Re-assert the production DB binding before EVERY generation test — robust
    to cross-suite co-running, regardless of test order.

    Only this suite repoints the process-global ``DATABASE_URL`` + resets
    ``primeqa.semantic.connection``'s cached engine (the test_representation /
    semantic suites use private ``create_engine`` fixtures and never touch these
    globals). ``db_setup`` does it once per session — fine under the current
    deterministic collection order, but fragile if anything (a co-running suite,
    or ambient ``os.environ`` at import) leaves the cached engine pointed at a
    different DB: a generation test would then read a DB without its S1 seed and
    refuse instead of draft. Re-pinning ``DATABASE_URL`` and dropping a drifted
    cached engine here makes every generation test deterministically resolve to
    the governance DB. No-op when the binding is already correct (the common
    case: it only disposes/rebuilds when the cached engine has actually drifted)."""
    from primeqa.semantic import connection as _conn
    os.environ["DATABASE_URL"] = test_db_url
    want_db = test_db_url.rsplit("/", 1)[-1]
    if _conn._engine is not None and _conn._engine.url.database != want_db:
        _conn._engine.dispose()
        _conn._engine = None
    yield


# ---------------------------------------------------------------------------
# Seed S1 entities/edges (committed, session-scoped, read-only for tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seeded(db_setup) -> dict:
    from primeqa.semantic.connection import get_tenant_connection

    # D-286: the org every seeded entity/edge is tagged with (a closure var set
    # inside the with-block, below, before any _entity/_edge call). Tagging is
    # ADDITIVE for the org-blind consumers (the verticals read `SemanticOrgModel(
    # conn)` org-blind → ignore connected_org_id → see the whole seed) and correct
    # for the org-scoped ones (run_generation / the pin resolve env→org → read
    # `WHERE connected_org_id = org_id`).
    org_id = None

    def _entity(conn, etype, api, vfrom, sf_id=None, attrs=None):
        return conn.execute(text(
            "INSERT INTO entities (entity_type, sf_id, sf_api_name, display_name, "
            "attributes, connected_org_id, valid_from_seq, valid_to_seq, last_synced_at) "
            "VALUES (:et,:sfid,:api,:api,CAST(:attrs AS jsonb),CAST(:org AS uuid),"
            ":vf,NULL,NOW()) RETURNING id"
        ), {"et": etype, "sfid": sf_id, "api": api, "vf": vfrom,
            "attrs": json.dumps(attrs or {}), "org": org_id}).scalar()

    def _edge(conn, src, tgt, etype, ecat, vfrom):
        conn.execute(text(
            "INSERT INTO edges (source_entity_id, target_entity_id, edge_type, "
            "edge_category, properties, connected_org_id, valid_from_seq, valid_to_seq) "
            "VALUES (CAST(:s AS uuid),CAST(:t AS uuid),:et,:ec,'{}'::jsonb,"
            "CAST(:org AS uuid),:vf,NULL)"
        ), {"s": str(src), "t": str(tgt), "et": etype, "ec": ecat, "vf": vfrom,
            "org": org_id})

    with get_tenant_connection(TEST_TENANT_ID) as conn:
        # D-286: provision the env's connected_org FIRST (FK target for the tagged
        # entities/edges), then bind it for the closures above.
        from primeqa.sync.credentials import ensure_connected_org_for_environment
        org_id = ensure_connected_org_for_environment(
            conn, TEST_ENV_ID, "https://gen-test.example.local")
        v1 = conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type, connected_org_id) "
            "VALUES (:n,'manual_checkpoint',CAST(:org AS uuid)) RETURNING version_seq"
        ), {"n": f"gov_v1_{uuid4().hex[:8]}", "org": org_id}).scalar()
        # bare Object — no VR / Flow / Field (drives no_org_constraint,
        # ontology_gap, ungrounded-claim refusals)
        account = _entity(conn, "Object", "Account", v1)
        # two same-named Objects -> ambiguous-reference
        _entity(conn, "Object", "Contact", v1)
        _entity(conn, "Object", "Contact", v1)
        # Object WITH a ValidationRule (APPLIES_TO) -> grounded positive control
        case = _entity(conn, "Object", "Case", v1)
        vr = _entity(conn, "ValidationRule", "Case.RequireReason", v1)
        _edge(conn, vr, case, "APPLIES_TO", "BEHAVIOR", v1)
        # Object WITH a Field (BELONGS_TO) -> a positive value-claim GROUNDS here
        # (unlike bare Account), so it reaches the D-105 emittability gate and
        # refuses emission-deferred rather than crashing finalize.
        invoice = _entity(conn, "Object", "Invoice", v1)
        amount = _entity(conn, "Field", "Invoice.Amount", v1)
        _edge(conn, amount, invoice, "BELONGS_TO", "STRUCTURAL", v1)
        # Object WITH a VR carrying a DERIVABLE formula (D-107): the negative is
        # Layer-2-VERIFIED (a violating value derives with certainty) -> no caveat.
        # Contrast with "Case" above, whose VR has empty attributes (no formula):
        # no behavioural reject recipe derives, so under D-293 a Case prohibition
        # REFUSES (behaviour-incomplete) rather than degrading to a caveated
        # inspection. Lead is the positive-emit control for prohibition negatives.
        lead = _entity(conn, "Object", "Lead", v1)
        lead_vr = _entity(conn, "ValidationRule", "Lead.RequireReason", v1,
                          attrs={"formula_text": "ISBLANK(Reason__c)"})
        _edge(conn, lead_vr, lead, "APPLIES_TO", "BEHAVIOR", v1)
        # D-293: a SECOND Object with a DERIVABLE-formula VR. The sibling read
        # (test_multi_recipe_vertical) needs TWO emittable prohibitions of the same
        # kind under one requirement; "Case" (the old non-derivable second member)
        # now refuses behaviour-incomplete, so a second derivable object is needed.
        quote = _entity(conn, "Object", "Quote", v1)
        quote_vr = _entity(conn, "ValidationRule", "Quote.NonNegativeTotal", v1,
                           attrs={"formula_text": "Total__c < 0"})
        _edge(conn, quote_vr, quote, "APPLIES_TO", "BEHAVIOR", v1)
        # Object WITH a record-triggered Flow (D-210): the automation-effect /
        # state-transition vertical's grounding fixture — Flow TRIGGERS_ON Order;
        # Order.Status__c is the to-state/effect field; Order_Log__c is the
        # cross-object effect target with its lookup back to Order.
        order = _entity(conn, "Object", "Order__c", v1)
        order_status = _entity(conn, "Field", "Order__c.Status__c", v1)
        _edge(conn, order_status, order, "BELONGS_TO", "STRUCTURAL", v1)
        # D-222: a second Order field — the STAGED trigger of the
        # state-transition pair (set at create; the org's automation reacts).
        order_stage = _entity(conn, "Field", "Order__c.Stage__c", v1)
        _edge(conn, order_stage, order, "BELONGS_TO", "STRUCTURAL", v1)
        # D-299: a third Order field so a MULTI-field (N=2) entry-condition
        # trigger can be proven end-to-end (Stage__c + Priority__c), with the
        # effect field Status__c staying org-produced.
        order_priority = _entity(conn, "Field", "Order__c.Priority__c", v1)
        _edge(conn, order_priority, order, "BELONGS_TO", "STRUCTURAL", v1)
        order_flow = _entity(conn, "Flow", "Stamp_Order_Status", v1)
        _edge(conn, order_flow, order, "TRIGGERS_ON", "BEHAVIOR", v1)
        # D-299: a SECOND Flow TRIGGERS_ON Order__c — the multi-flow fixture that
        # proves a requirement-NAMED automation binds THAT flow, not the
        # first-encountered one (env-59's Opportunity has three flows).
        order_flow2 = _entity(conn, "Flow", "Escalate_Order", v1)
        _edge(conn, order_flow2, order, "TRIGGERS_ON", "BEHAVIOR", v1)
        order_log = _entity(conn, "Object", "Order_Log__c", v1)
        log_lookup = _entity(conn, "Field", "Order_Log__c.Order__c", v1)
        _edge(conn, log_lookup, order_log, "BELONGS_TO", "STRUCTURAL", v1)
        log_level = _entity(conn, "Field", "Order_Log__c.Level__c", v1)
        _edge(conn, log_level, order_log, "BELONGS_TO", "STRUCTURAL", v1)
        # D-227: a Flow TRIGGERS_ON Order_Log__c — the parent-stamp vertical's
        # grounding (the trigger record's own lookup Order__c points at the
        # effect parent Order__c).
        log_flow = _entity(conn, "Flow", "Log_Effects", v1)
        _edge(conn, log_flow, order_log, "TRIGGERS_ON", "BEHAVIOR", v1)

    return {"v1": int(v1), "account": account, "case": case, "invoice": invoice,
            "org": str(org_id), "env": TEST_ENV_ID}    # D-286


def seed_org_id(conn) -> str:
    """D-286: the test seed's connected_org id (resolves ``TEST_ENV_ID``). Per-file
    helpers that insert a fresh ``logical_version`` / ``entities`` MUST tag them
    with this org, else the org-scoped reads + version pin (which filter
    ``connected_org_id = org``) won't see them."""
    from primeqa.sync.credentials import get_connected_org_for_environment
    return get_connected_org_for_environment(conn, TEST_ENV_ID)


@pytest.fixture(autouse=True)
def clean_ledger(db_setup):
    """Clear the generation ledger AND the substrate-2 test tables before each
    test (the S1 seed persists). The draft vertical writes real S2 claims/recipes
    via the Coordinator, so a clean S2 slate keeps emission tests isolated (and
    makes the dedup test's first emit genuinely fresh)."""
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(TEST_TENANT_ID) as conn:
        conn.execute(text("DELETE FROM llm_calls"))
        conn.execute(text("DELETE FROM generation_outcomes"))
        conn.execute(text("DELETE FROM generation_requests"))
        # substrate-2 tables (emission writes here); CASCADE handles FK order.
        conn.execute(text(
            "TRUNCATE test_claim_coverage, test_provenance, "
            "test_recipe_runtime_state, test_requirement_links, "
            "test_recipes, test_claims CASCADE"))
        # D-106.4 slice-3: clear the S3 job queue so the consumer's claim-oldest
        # semantics are deterministic per test (attempts first — FK child).
        conn.execute(text("DELETE FROM s3_generation_job_attempts"))
        conn.execute(text("DELETE FROM s3_generation_jobs"))
    yield


# ---------------------------------------------------------------------------
# Test builders (scripted LLM; real S1 grounding)
# ---------------------------------------------------------------------------

class FakeTurn:
    def __init__(self, blocks):
        self.content_blocks = blocks
        self.input_tokens = 10
        self.output_tokens = 5
        self.model = "claude-test"
        self.stop_reason = "tool_use"
        self.latency_ms = 7


def propose_turn(intent_input: dict) -> FakeTurn:
    return FakeTurn([{"type": "tool_use", "id": f"tu_{uuid4().hex[:6]}",
                      "name": "propose_semantic_intent", "input": intent_input}])


class FakeToolTurn:
    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0

    def __call__(self, *, messages, tools, tool_choice, system):
        i = self.calls
        self.calls += 1
        if i < len(self._turns):
            return self._turns[i]
        raise AssertionError("FakeToolTurn over-called")


def make_request(*, s1_version_seq: int, requirement_text: str = "the requirement"):
    from primeqa.generation.protocol import (
        GenerationRequest, SemanticContext, GovernanceContext, OperationalContext,
    )
    return GenerationRequest(
        request_id=uuid4(),
        semantic_context=SemanticContext(
            requirement_refs=[{"key": "R0", "text": requirement_text}],
            s1_version_seq=s1_version_seq, s1_version_name="gov_v1",
        ),
        governance_context=GovernanceContext(),
        operational_context=OperationalContext(),
    )


_UNSET = object()


def intent(*, archetype="data_behavior", claim_kind, polarity, sf_api_name,
           entity_type="Object", excerpt="the system shall reject the operation",
           field_name=None, expected_value=_UNSET):
    tsh = {"entity_type": entity_type, "sf_api_name": sf_api_name}
    if field_name is not None:               # value-claim: the named field (D-115.3)
        tsh["field_name"] = field_name
    if expected_value is not _UNSET:         # the requirement-sourced value (verbatim)
        tsh["expected_value"] = expected_value
    desc = {"archetype_hint": archetype, "polarity_hint": polarity,
            "target_subject_hint": tsh}
    if claim_kind is not None:
        desc["claim_kind_hint"] = claim_kind
    return {"requirement_excerpt": excerpt, "intent_descriptor": desc}


def rel_intent(*, edge_type, source, target, excerpt="Requirement assumes the relationship exists"):
    """A configuration metadata-relationship-claim intent (D-098.1). `source`
    and `target` are {entity_type, sf_api_name} dicts."""
    return {"requirement_excerpt": excerpt, "intent_descriptor": {
        "archetype_hint": "configuration", "polarity_hint": "positive",
        "claim_kind_hint": "metadata-relationship-claim",
        "target_subject_hint": {"edge_type": edge_type, "source": source, "target": target},
    }}


def query_outcome_rows(tenant_id=TEST_TENANT_ID):
    """Read the persisted ledger (fresh connection, after run)."""
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        reqs = conn.execute(text("SELECT request_id FROM generation_requests")).fetchall()
        outs = conn.execute(text(
            "SELECT outcome_id, request_id, outcome_kind, refusal_kind, "
            "explanation_hash, refusals, attempted_interpretation "
            "FROM generation_outcomes")).mappings().all()
        calls = conn.execute(text(
            "SELECT call_id, generation_outcome_id, operational_outcome, attempt_index "
            "FROM llm_calls")).mappings().all()
    return {"requests": reqs, "outcomes": outs, "llm_calls": calls}
