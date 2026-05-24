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


# ---------------------------------------------------------------------------
# Seed S1 entities/edges (committed, session-scoped, read-only for tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seeded(db_setup) -> dict:
    from primeqa.semantic.connection import get_tenant_connection

    def _entity(conn, etype, api, vfrom, sf_id=None):
        return conn.execute(text(
            "INSERT INTO entities (entity_type, sf_id, sf_api_name, display_name, "
            "attributes, valid_from_seq, valid_to_seq, last_synced_at) "
            "VALUES (:et,:sfid,:api,:api,'{}'::jsonb,:vf,NULL,NOW()) RETURNING id"
        ), {"et": etype, "sfid": sf_id, "api": api, "vf": vfrom}).scalar()

    def _edge(conn, src, tgt, etype, ecat, vfrom):
        conn.execute(text(
            "INSERT INTO edges (source_entity_id, target_entity_id, edge_type, "
            "edge_category, properties, valid_from_seq, valid_to_seq) "
            "VALUES (CAST(:s AS uuid),CAST(:t AS uuid),:et,:ec,'{}'::jsonb,:vf,NULL)"
        ), {"s": str(src), "t": str(tgt), "et": etype, "ec": ecat, "vf": vfrom})

    with get_tenant_connection(TEST_TENANT_ID) as conn:
        v1 = conn.execute(text(
            "INSERT INTO logical_versions (version_name, version_type) "
            "VALUES (:n,'manual_checkpoint') RETURNING version_seq"
        ), {"n": f"gov_v1_{uuid4().hex[:8]}"}).scalar()
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

    return {"v1": int(v1), "account": account, "case": case, "invoice": invoice}


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


def intent(*, archetype="data_behavior", claim_kind, polarity, sf_api_name,
           entity_type="Object", excerpt="the system shall reject the operation"):
    desc = {"archetype_hint": archetype, "polarity_hint": polarity,
            "target_subject_hint": {"entity_type": entity_type, "sf_api_name": sf_api_name}}
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
