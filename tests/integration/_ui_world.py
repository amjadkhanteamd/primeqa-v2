"""Round 4, part C (AUD-052): ONE remover for the UI claim-set world a suite
plants through the real services (an inventory, an enumerated + approved
claim set and its claims, manifests, inspection jobs and results, verdicts,
processing and comparison runs, schedules) — so every suite cleans up what it
planted and the tree leaves zero residue.

Dependency order, from the leaves up, keyed by the ids a suite captured at
plant time (never by "everything"): the s6 comparison transitions and runs,
the verdicts and processing runs, the inspection results and jobs, the run
manifests (linked by ``payload->>'claim_set_id'``), the schedules, the surface
link claims, the members (whose test ids name the claims), every table keyed
by those test ids, the claim sets, the inventory members (whose surface refs
name the materialised entities) and inventories, the entities and their edges.
Tables with never-delete triggers (run plans, release targets, quality
policies and waivers) are not touched: a suite that needs them plants inside
a transaction it rolls back.

Every DELETE is scoped to the ids given; a table absent from the schema is
skipped (``to_regclass``); nothing here raises past a missing table.
"""
from __future__ import annotations

from sqlalchemy import text


def _exists(conn, table: str) -> bool:
    return conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar()


def _ids(rows) -> list[str]:
    return [str(r[0]) for r in rows]


def remove_claim_set_world(conn, *, claim_set_ids=(), inventory_versions=(), release_ids=(),
                           tenant_id: int = 1) -> dict:
    """Remove everything the given claim sets / inventories / public releases
    own. Returns ``{table: rows deleted}`` for the record."""
    out: dict = {}

    def run(table, sql, **params):
        if not _exists(conn, table):
            return
        n = conn.execute(text(sql), params).rowcount or 0
        if n:
            out[table] = out.get(table, 0) + n

    sets = [str(s) for s in claim_set_ids]
    invs = [int(v) for v in inventory_versions]

    if sets:
        # the manifests (payload-linked) and the jobs/results/verdict runs under them
        manifests = _ids(conn.execute(text(
            "SELECT id FROM s4_ui_run_manifests WHERE payload->>'claim_set_id' = ANY(:s)"), {"s": sets}).fetchall()) \
            if _exists(conn, "s4_ui_run_manifests") else []
        jobs = _ids(conn.execute(text(
            "SELECT id FROM s4_ui_inspection_jobs WHERE CAST(manifest_id AS text) = ANY(:m)"), {"m": manifests}).fetchall()) \
            if manifests and _exists(conn, "s4_ui_inspection_jobs") else []
        comparisons = _ids(conn.execute(text(
            "SELECT id FROM s6_ui_comparison_runs WHERE CAST(baseline_claim_set_id AS text) = ANY(:s) "
            "OR CAST(candidate_claim_set_id AS text) = ANY(:s) OR CAST(baseline_job_id AS text) = ANY(:j) "
            "OR CAST(candidate_job_id AS text) = ANY(:j)"), {"s": sets, "j": jobs}).fetchall()) \
            if _exists(conn, "s6_ui_comparison_runs") else []
        run("s6_ui_verdict_transitions", "DELETE FROM s6_ui_verdict_transitions WHERE CAST(comparison_id AS text) = ANY(:c)", c=comparisons)
        run("s6_ui_comparison_runs", "DELETE FROM s6_ui_comparison_runs WHERE CAST(id AS text) = ANY(:c)", c=comparisons)
        run("s6_ui_verdicts", "DELETE FROM s6_ui_verdicts WHERE CAST(claim_set_id AS text) = ANY(:s) OR CAST(job_id AS text) = ANY(:j)", s=sets, j=jobs)
        run("s6_ui_processing_runs", "DELETE FROM s6_ui_processing_runs WHERE CAST(claim_set_id AS text) = ANY(:s) OR CAST(job_id AS text) = ANY(:j)", s=sets, j=jobs)
        run("s4_ui_inspection_results", "DELETE FROM s4_ui_inspection_results WHERE CAST(job_id AS text) = ANY(:j)", j=jobs)
        run("s4_ui_inspection_jobs", "DELETE FROM s4_ui_inspection_jobs WHERE CAST(id AS text) = ANY(:j)", j=jobs)
        run("s4_ui_run_manifests", "DELETE FROM s4_ui_run_manifests WHERE CAST(id AS text) = ANY(:m)", m=manifests)
        run("ui_run_schedules", "DELETE FROM ui_run_schedules WHERE CAST(claim_set_id AS text) = ANY(:s)", s=sets)
        run("requirement_surface_link_claims", "DELETE FROM requirement_surface_link_claims WHERE CAST(claim_set_id AS text) = ANY(:s)", s=sets)
        # the members name the claims — every table keyed by those test ids, then the claims
        tests = _ids(conn.execute(text(
            "SELECT DISTINCT test_id FROM claim_set_members WHERE CAST(claim_set_id AS text) = ANY(:s)"), {"s": sets}).fetchall())
        run("claim_set_members", "DELETE FROM claim_set_members WHERE CAST(claim_set_id AS text) = ANY(:s)", s=sets)
        if tests:
            # a claim that ANOTHER (surviving) set still holds stays: claims dedupe across sets
            still_held = set(_ids(conn.execute(text(
                "SELECT DISTINCT test_id FROM claim_set_members WHERE CAST(test_id AS text) = ANY(:t)"), {"t": tests}).fetchall()))
            tests = [t for t in tests if t not in still_held]
        for table, col in (("claim_quarantine", "test_id"), ("s8_grounding_validity", "test_id"),
                           ("test_requirement_links", "test_id"), ("test_claim_coverage", "claim_test_id"),
                           ("test_provenance", "claim_test_id"), ("test_recipes", "claim_test_id"),
                           ("s6_interpretations", "claim_test_id"), ("repair_proposals", "claim_test_id"),
                           ("s4_runall_batch_manifests", "claim_test_id"), ("s4_execution_jobs", "test_id"),
                           ("test_claims", "test_id")):
            if tests:
                run(table, f"DELETE FROM {table} WHERE CAST({col} AS text) = ANY(:t)", t=tests)
        run("claim_sets", "DELETE FROM claim_sets WHERE CAST(id AS text) = ANY(:s)", s=sets)

    if invs:
        # what still refers to these inventories must be gone first (claim sets of other suites are not ours)
        surfaces = _ids(conn.execute(text(
            "SELECT DISTINCT surface_entity_ref FROM ui_surface_inventory_members "
            "WHERE inventory_version = ANY(:v) AND surface_entity_ref IS NOT NULL"), {"v": invs}).fetchall()) \
            if _exists(conn, "ui_surface_inventory_members") else []
        run("ui_surface_inventory_members", "DELETE FROM ui_surface_inventory_members WHERE inventory_version = ANY(:v)", v=invs)
        run("ui_surface_inventories", "DELETE FROM ui_surface_inventories WHERE inventory_version = ANY(:v)", v=invs)
        if surfaces:
            # a materialised surface entity another inventory still uses stays
            still = set(_ids(conn.execute(text(
                "SELECT DISTINCT surface_entity_ref FROM ui_surface_inventory_members "
                "WHERE CAST(surface_entity_ref AS text) = ANY(:e)"), {"e": surfaces}).fetchall()))
            gone = [e for e in surfaces if e not in still]
            if gone:
                run("edges", "DELETE FROM edges WHERE CAST(source_entity_id AS text) = ANY(:e) OR CAST(target_entity_id AS text) = ANY(:e)", e=gone)
                run("entities", "DELETE FROM entities WHERE CAST(id AS text) = ANY(:e)", e=gone)
        # the materialisation's logical version is named by the inventory number, which is
        # MAX+1 and so REUSED once the inventory is gone — the row must go with it, and so
        # must any entity that version still owns (entities.valid_from_seq is a foreign key)
        # unless another inventory's member still points at it
        names = [f"surface-materialization-inv{v}" for v in invs]
        seqs = [r[0] for r in conn.execute(text(
            "SELECT version_seq FROM logical_versions WHERE version_name = ANY(:n)"), {"n": names})] \
            if _exists(conn, "logical_versions") else []
        if seqs:
            owned = _ids(conn.execute(text(
                "SELECT e.id FROM entities e WHERE e.valid_from_seq = ANY(:q) AND NOT EXISTS ("
                "  SELECT 1 FROM ui_surface_inventory_members m WHERE m.surface_entity_ref = e.id)"), {"q": seqs}).fetchall())
            if owned:
                run("edges", "DELETE FROM edges WHERE CAST(source_entity_id AS text) = ANY(:e) OR CAST(target_entity_id AS text) = ANY(:e)", e=owned)
                run("entities", "DELETE FROM entities WHERE CAST(id AS text) = ANY(:e)", e=owned)
            run("logical_versions", "DELETE FROM logical_versions WHERE version_seq = ANY(:q)", q=seqs)

    rels = [int(r) for r in release_ids]
    if rels:
        for table in ("release_requirements", "release_decisions", "release_environment_snapshots"):
            run(f"public.{table}", f"DELETE FROM public.{table} WHERE release_id = ANY(:r)", r=rels)
        run("public.releases", "DELETE FROM public.releases WHERE id = ANY(:r) AND tenant_id = :t", r=rels, t=tenant_id)
    return out
