"""Triage batch (2026-09-19), DB-real on scratch: the four invariants that were
"guarded by convention, not by constraint" now hold at the TABLE, and the
class cannot reopen unnoticed.

  1. Every tenant table with a ``run_id`` column is CLASSIFIED: it carries a
     foreign key to ``s4_execution_runs``, or it is named in the ledger below
     with the reason it does not (yet). A new ``run_id`` column that is
     neither FAILS this gate — the pyflakes-gate shape (AUD-026's class).
  2. Each constraint the migration ``20260919_0010`` adds is present, and is
     proven by ATTEMPTING the forbidden write inside a rolled-back
     transaction with a planted fixture (a proof matching zero rows is
     INVALID): an orphan verdict, a run deleted from under its verdict, a
     revocation and a removal with an empty reason, a SPECULATIVE / SEMANTIC
     / unclassified proposal stamped approved or applied.

Runs against ``S3A3_TEST_DATABASE_URL`` (the scratch DB, tenant_1 migrated
to head); skips without it.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL")]
SCHEMA = "tenant_1"

# The census ledger: every tenant table whose run_id is NOT (yet) a foreign key
# to s4_execution_runs, with the reason. Adding a table here is a deliberate,
# reviewed act; a run_id column absent from both the FK set and this ledger
# fails the gate.
RUN_ID_WITHOUT_FK = {
    "s4_execution_runs": "the parent: run_id is its primary key",
    "s4_created_records": "AUD-026 sibling — cleanup ledger of records a run created; FK deferred (3,382 rows on production, zero orphans not yet proven); findings.json",
    "s6_reinterpretations": "AUD-026 sibling — re-interpretations of a run; FK deferred (50 rows on production); findings.json",
    "repair_proposals": "AUD-026 sibling — the proposal's originating run; FK deferred (140 rows on production); findings.json",
}


@pytest.fixture(scope="module")
def eng():
    return create_engine(DB)


def _attempt(eng, sql, params=None, *, setup=(), precheck=None):
    """Plant fixtures under the replica role, attempt the write under the
    origin role (every trigger fires), roll everything back. Returns the
    refusing exception's class name, or None when the write went through."""
    with eng.connect() as c:
        tx = c.begin()
        try:
            c.execute(text(f"SET LOCAL search_path TO {SCHEMA}, public"))
            c.execute(text("SET LOCAL session_replication_role = replica"))
            for s, p in setup:
                c.execute(text(s), p or {})
            c.execute(text("SET LOCAL session_replication_role = origin"))
            if precheck is not None:
                assert c.execute(text(precheck), params or {}).scalar(), "INVALID PROOF — the target matches 0 rows"
            c.execute(text(sql), params or {})
            return None
        except AssertionError:
            raise
        except Exception as ex:  # noqa: BLE001
            return type(getattr(ex, "orig", ex)).__name__
        finally:
            tx.rollback()


@pytest.fixture(scope="module")
def fx(eng):
    with eng.connect() as c:
        c.execute(text(f"SET search_path TO {SCHEMA}, public"))
        run = c.execute(text("SELECT CAST(claim_test_id AS text), CAST(recipe_id AS text), recipe_version_seq, "
                             "claim_version_seq, environment_id FROM s4_execution_runs LIMIT 1")).first()
        release = c.execute(text("SELECT id FROM public.releases ORDER BY id LIMIT 1")).scalar()
    if not run or not release:
        pytest.skip("scratch needs one run and one release to build fixtures from")
    run_id, wid, tid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    f = {"claim": run[0], "recipe": run[1], "env": run[4], "run_id": run_id, "wid": wid, "tid": tid, "release": release}
    f["RUN"] = ("INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, claim_version_seq, "
                "environment_id, outcome, started_at, finished_at, evidence) VALUES (CAST(:r AS uuid), CAST(:rc AS uuid), :rv, "
                "CAST(:c AS uuid), :cv, :e, 'passed', now(), now(), '{}'::jsonb)",
                {"r": run_id, "rc": run[1], "rv": run[2], "c": run[0], "cv": run[3], "e": run[4]})
    f["VERDICT"] = ("INSERT INTO s6_interpretations (run_id, recipe_id, claim_test_id, outcome, verdict, detail) VALUES "
                    "(CAST(:r AS uuid), CAST(:rc AS uuid), CAST(:c AS uuid), CAST('passed' AS run_outcome), 'passed', '{}'::jsonb)",
                    {"r": run_id, "rc": run[1], "c": run[0]})
    f["WAIVER"] = ("INSERT INTO quality_waivers (id, release_id, item_kind, item_ref, axis, reviewer_user_id, reason, expires_at, created_by, created_at) "
                   "VALUES (CAST(:w AS uuid), :rel, 'claim', :c, 'functional', 15, 'gate fixture', now() + interval '1 day', 15, now())",
                   {"w": wid, "rel": release, "c": run[0]})
    f["TARGET"] = ("INSERT INTO release_targets (id, release_id, environment_id, declared_by, declared_at, active) "
                   "VALUES (CAST(:t AS uuid), :rel, :e, 15, now(), true)", {"t": tid, "rel": release, "e": run[4]})

    def prop(pid, verdict, status="proposed", grounding=None):
        return ("INSERT INTO repair_proposals (id, run_id, claim_test_id, environment_id, verdict, proposal_kind, status, gate_verdict, grounding_source) "
                "VALUES (:id, CAST(:r AS uuid), CAST(:c AS uuid), :e, 'creation_rejected', 'recipe_edit', :st, :gv, CAST(:gs AS jsonb))",
                {"id": pid, "r": run_id, "c": run[0], "e": run[4], "st": status, "gv": verdict, "gs": grounding})
    f["prop"] = prop
    return f


# --- 1. the census gate -------------------------------------------------------

def test_every_run_id_column_is_a_foreign_key_or_ledgered(eng):
    with eng.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT table_name FROM information_schema.columns WHERE table_schema = :s AND column_name = 'run_id' ORDER BY 1"),
            {"s": SCHEMA}).fetchall()]
        assert len(tables) >= 5, "the census read almost nothing — is the schema migrated?"
        unclassified, ledgered_but_fk = [], []
        for t in tables:
            fk = c.execute(text("""
                SELECT confrelid::regclass::text FROM pg_constraint
                WHERE conrelid = CAST(:t AS regclass) AND contype = 'f'
                  AND 'run_id' = ANY(SELECT attname FROM pg_attribute WHERE attrelid = conrelid AND attnum = ANY(conkey))
            """), {"t": f"{SCHEMA}.{t}"}).scalar()
            if fk and fk.endswith("s4_execution_runs"):
                if t in RUN_ID_WITHOUT_FK:
                    ledgered_but_fk.append(t)       # the ledger is stale — trim it
                continue
            if t not in RUN_ID_WITHOUT_FK:
                unclassified.append(t)
    assert unclassified == [], (f"run_id columns with no FK to s4_execution_runs and no ledger entry: {unclassified} — "
                                "add the foreign key, or add the table to RUN_ID_WITHOUT_FK with the reason")
    assert ledgered_but_fk == [], f"ledger entries that now carry the FK (remove them): {ledgered_but_fk}"


def test_the_ledger_names_only_real_tables(eng):
    with eng.connect() as c:
        real = {r[0] for r in c.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"), {"s": SCHEMA}).fetchall()}
    assert set(RUN_ID_WITHOUT_FK) <= real, set(RUN_ID_WITHOUT_FK) - real


# --- 2. the constraints, attempted for real -------------------------------------

def test_a_verdict_cannot_exist_without_its_run(eng, fx):
    v_sql, v_p = fx["VERDICT"]
    assert _attempt(eng, v_sql, {**v_p, "r": str(uuid.uuid4())}) == "ForeignKeyViolation"
    assert _attempt(eng, "DELETE FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)", {"r": fx["run_id"]},
                    setup=[fx["RUN"], fx["VERDICT"]],
                    precheck="SELECT count(*) FROM s6_interpretations WHERE run_id = CAST(:r AS uuid)") == "ForeignKeyViolation"
    assert _attempt(eng, v_sql, v_p, setup=[fx["RUN"]]) is None          # with its run: allowed


@pytest.mark.parametrize("why", ["", "   ", None])
def test_a_revocation_with_an_empty_reason_is_refused_at_the_table(eng, fx, why):
    assert _attempt(eng, "UPDATE quality_waivers SET revoked_by = 14, revoked_at = now(), revocation_reason = :why WHERE id = CAST(:w AS uuid)",
                    {"w": fx["wid"], "why": why}, setup=[fx["WAIVER"]],
                    precheck="SELECT count(*) FROM quality_waivers WHERE id = CAST(:w AS uuid) AND revoked_at IS NULL") == "CheckViolation"


def test_a_revocation_with_a_reason_is_allowed(eng, fx):
    assert _attempt(eng, "UPDATE quality_waivers SET revoked_by = 14, revoked_at = now(), revocation_reason = 'no longer accepted' WHERE id = CAST(:w AS uuid)",
                    {"w": fx["wid"]}, setup=[fx["WAIVER"]],
                    precheck="SELECT count(*) FROM quality_waivers WHERE id = CAST(:w AS uuid) AND revoked_at IS NULL") is None


@pytest.mark.parametrize("why", ["", "  ", None])
def test_a_target_removal_with_an_empty_reason_is_refused_at_the_table(eng, fx, why):
    assert _attempt(eng, "UPDATE release_targets SET active = FALSE, deactivated_by = 14, deactivated_at = now(), deactivation_reason = :why WHERE id = CAST(:t AS uuid)",
                    {"t": fx["tid"], "why": why}, setup=[fx["TARGET"]],
                    precheck="SELECT count(*) FROM release_targets WHERE id = CAST(:t AS uuid) AND active") == "CheckViolation"


def test_a_target_removal_with_a_reason_is_allowed(eng, fx):
    assert _attempt(eng, "UPDATE release_targets SET active = FALSE, deactivated_by = 14, deactivated_at = now(), deactivation_reason = 'wrong org' WHERE id = CAST(:t AS uuid)",
                    {"t": fx["tid"]}, setup=[fx["TARGET"]],
                    precheck="SELECT count(*) FROM release_targets WHERE id = CAST(:t AS uuid) AND active") is None


@pytest.mark.parametrize("verdict", ["SPECULATIVE", "SEMANTIC", None])
@pytest.mark.parametrize("status", ["approved", "applied"])
def test_only_a_derived_proposal_enters_approved_or_applied(eng, fx, verdict, status):
    assert _attempt(eng, "UPDATE repair_proposals SET status = :st WHERE id = :p", {"p": 990001, "st": status},
                    setup=[fx["RUN"], fx["prop"](990001, verdict)],
                    precheck="SELECT count(*) FROM repair_proposals WHERE id = :p") == "RaiseException"


def test_a_derived_proposal_without_grounding_is_refused_and_with_grounding_allowed(eng, fx):
    assert _attempt(eng, "UPDATE repair_proposals SET status = 'applied' WHERE id = :p", {"p": 990002},
                    setup=[fx["RUN"], fx["prop"](990002, "DERIVED")],
                    precheck="SELECT count(*) FROM repair_proposals WHERE id = :p") == "RaiseException"
    assert _attempt(eng, "UPDATE repair_proposals SET status = 'applied' WHERE id = :p", {"p": 990005},
                    setup=[fx["RUN"], fx["prop"](990005, "DERIVED", grounding='{"rule": "R1"}')],
                    precheck="SELECT count(*) FROM repair_proposals WHERE id = :p") is None


def test_a_speculative_proposal_may_still_be_rejected(eng, fx):
    assert _attempt(eng, "UPDATE repair_proposals SET status = 'rejected' WHERE id = :p", {"p": 990004},
                    setup=[fx["RUN"], fx["prop"](990004, "SPECULATIVE")],
                    precheck="SELECT count(*) FROM repair_proposals WHERE id = :p") is None


def test_a_row_inserted_directly_as_applied_faces_the_same_guard(eng, fx):
    sql, p = fx["prop"](990003, "SPECULATIVE", "applied")
    assert _attempt(eng, sql, p, setup=[fx["RUN"]]) == "RaiseException"


# --- triage round 2 (AUD-037): an unlinked surface says why, at the table ---------------------

@pytest.fixture(scope="module")
def link_fx(eng):
    with eng.connect() as c:
        c.execute(text(f"SET search_path TO {SCHEMA}, public"))
        member = c.execute(text("SELECT surface_key, inventory_version FROM ui_surface_inventory_members ORDER BY inventory_version DESC LIMIT 1")).first()
    if not member:
        pytest.skip("scratch needs one inventory member")
    lid = str(uuid.uuid4())
    # the link references an identity (FK): plant one, under the replica role, in the same rolled-back transaction
    ident = ("INSERT INTO requirement_identities (external_system, external_key, origin, origin_evidence, established_at, established_by, classifier_version) "
             "VALUES ('jira', 'GATE-SURF', 'manual', '{}'::jsonb, now(), 15, 'gate') ON CONFLICT DO NOTHING", {})
    link = ("INSERT INTO requirement_surface_links (id, external_system, requirement_key, surface_key, inventory_version, source, declared_by, declared_at, active) "
            "VALUES (CAST(:l AS uuid), 'jira', 'GATE-SURF', :s, :v, 'DECLARED', 15, now(), true)", {"l": lid, "s": member[0], "v": member[1]})
    return {"lid": lid, "LINK": link, "IDENT": ident}


@pytest.mark.parametrize("why", ["", "  ", None])
def test_a_surface_unlink_with_an_empty_reason_is_refused_at_the_table(eng, link_fx, why):
    assert _attempt(eng, "UPDATE requirement_surface_links SET active = FALSE, deactivated_by = 14, deactivated_at = now(), deactivation_reason = :why WHERE id = CAST(:l AS uuid)",
                    {"l": link_fx["lid"], "why": why}, setup=[link_fx["IDENT"], link_fx["LINK"]],
                    precheck="SELECT count(*) FROM requirement_surface_links WHERE id = CAST(:l AS uuid) AND active") == "CheckViolation"


def test_a_surface_unlink_with_a_reason_is_allowed(eng, link_fx):
    assert _attempt(eng, "UPDATE requirement_surface_links SET active = FALSE, deactivated_by = 14, deactivated_at = now(), deactivation_reason = 'retired' WHERE id = CAST(:l AS uuid)",
                    {"l": link_fx["lid"]}, setup=[link_fx["IDENT"], link_fx["LINK"]],
                    precheck="SELECT count(*) FROM requirement_surface_links WHERE id = CAST(:l AS uuid) AND active") is None
