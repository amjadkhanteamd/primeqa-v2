#!/usr/bin/env python3
"""PASS 2 — the hostile tenant, planted on SCRATCH and removable.

Every shape the brief names, built through the product's own services where a
service exists and by direct insert where NO producer exists (a state the
readers handle that nothing writes is itself a finding to note). Everything
minted is recorded in a manifest so `remove` deletes what was MINTED, not only
what was inserted (D-490's lesson).

Populated shapes live in tenant 1 (it has environments, orgs, sequences);
tenant 2 stays empty for the zero-state shapes.

    DATABASE_URL=scratch ... python scripts/audit/hostile_world.py plant  manifest.json ids.json
    DATABASE_URL=scratch ... python scripts/audit/hostile_world.py remove manifest.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text  # noqa: E402

T = 1
ADMIN = 15          # the audit's admin user on scratch
ENV = 4             # an active sandbox on scratch


def _pub():
    import primeqa.core.models  # noqa: F401
    import primeqa.db as dbm
    dbm.init_db(os.environ["DATABASE_URL"])
    return dbm.SessionLocal()


def plant(manifest_path, ids_path):
    from sqlalchemy.orm import Session
    from primeqa.semantic.connection import get_tenant_connection
    from primeqa.test_representation import SemanticTransactionCoordinator
    from primeqa.generation.emission import _inspection_recipe
    from tests.integration.test_representation._fixtures import empty_conditions, make_value_claim

    M = {"requirements": [], "releases": [], "claims": [], "links": [], "runs": [], "targets": [],
         "decisions": [], "claim_sets": [], "inventory_versions": [], "policies": [], "waivers": [],
         "plans": [], "environments": [], "groups": [], "connections": [], "sections": [],
         "proposals": [], "surface_links": [], "notes": []}
    ids = {}
    db = _pub()
    coord = SemanticTransactionCoordinator()

    # ---- public: section, requirements, releases, an INACTIVE environment, a group, a connection
    sid = db.execute(text("INSERT INTO sections (tenant_id, name, created_by) VALUES (1, 'AUD-hostile', :u) RETURNING id"), {"u": ADMIN}).scalar()
    M["sections"].append(sid); ids["section_id"] = sid

    def req(key, summary):
        rid = db.execute(text("INSERT INTO requirements (tenant_id, section_id, source, created_by, jira_summary, external_key) "
                              "VALUES (1, :s, 'manual', :u, :t, :k) RETURNING id"), {"s": sid, "u": ADMIN, "t": summary, "k": key}).scalar()
        M["requirements"].append(rid); return rid

    r79 = req("AUD-79", "one requirement with 79 claims")
    rdep = req("AUD-DEP", "every claim deprecated")
    rnocov = req("AUD-NOCOV", "a claim with no coverage rows and an unstamped run")
    rsurf = req("AUD-SURF", "a surface declared then unlinked")
    r500 = req("AUD-500", "five hundred claims")
    ids["req_id"] = r79

    env_inactive = db.execute(text(
        "INSERT INTO environments (tenant_id, name, env_type, sf_instance_url, sf_api_version, is_active, created_by) "
        "SELECT tenant_id, 'AUD-inactive', env_type, sf_instance_url, sf_api_version, false, :u FROM environments WHERE id = :e RETURNING id"),
        {"u": ADMIN, "e": ENV}).scalar()
    M["environments"].append(env_inactive); ids["env_inactive"] = env_inactive; ids["env_id"] = ENV; ids["environment_id"] = ENV

    gid = db.execute(text("INSERT INTO groups (tenant_id, name) VALUES (1, 'AUD-group') RETURNING id")).scalar()
    M["groups"].append(gid); ids["group_id"] = gid
    cid = db.execute(text("INSERT INTO connections (tenant_id, connection_type, name, config, created_by) "
                          "VALUES (1, 'jira', 'AUD-jira', '{\"base_url\": \"https://example.invalid\"}'::jsonb, :u) RETURNING id"), {"u": ADMIN}).scalar()
    M["connections"].append(cid); ids["conn_id"] = cid

    def rel(name, req_ids):
        rid = db.execute(text("INSERT INTO releases (tenant_id, name, version_tag, status, created_by) "
                              "VALUES (1, :n, 'aud', 'planning', :u) RETURNING id"), {"n": name, "u": ADMIN}).scalar()
        for q in req_ids:
            db.execute(text("INSERT INTO release_requirements (release_id, requirement_id, added_by) VALUES (:r, :q, :u)"), {"r": rid, "q": q, "u": ADMIN})
        M["releases"].append(rid); return rid

    rel_noscope = rel("AUD-release-no-scope", [])
    rel_inactive = rel("AUD-release-inactive-env", [r79])
    ids["release_id"] = rel_inactive; ids["release_noscope"] = rel_noscope
    did = db.execute(text("INSERT INTO release_decisions (release_id, recommendation, confidence, reasoning, criteria_met, recommended_by) "
                          "VALUES (:r, 'cannot_determine', 0, '\"audit fixture\"'::jsonb, '[]'::jsonb, 'ai') RETURNING id"), {"r": rel_inactive}).scalar()
    M["decisions"].append(did); ids["decision_id"] = did
    db.commit()

    # ---- tenant schema: claims, links, runs, targets, sets, inventory, policy, waiver, proposal
    with get_tenant_connection(T) as conn:
        s = Session(bind=conn)

        def claim(approve=True, deprecate=False, with_recipe=False):
            cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior", claim_kind="value-claim",
                                   asserted_truth=make_value_claim(value="Tech"), semantic_conditions=empty_conditions())
            if with_recipe:
                t, r, e = _inspection_recipe(read_entity_type="Object", read_external_id="Lead", capture_field="APPLIES_TO", env_detail="audit")
                rr = coord.write_recipe(s, actor="s3", recipe_id=None, claim_test_id=cr.test_id, trigger_kind="inspection-trigger",
                                        recipe_kind="metadata-recipe", causal_initiation=t, observation_realization=r,
                                        execution_environment=e, claim_version_seq=cr.version_seq)
                if approve:
                    coord.promote_recipe_to_approved(s, actor="human", recipe_id=rr.recipe_id, version_seq=rr.version_seq)
            if approve:
                coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
            if deprecate:
                coord.deprecate_claim(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq, reason="audit: deprecated")
            M["claims"].append(str(cr.test_id)); return cr

        def link(cr, key):
            coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira", external_key=key, link_kind="generated_from")
            M["links"].append([str(cr.test_id), key])

        for _ in range(79):
            link(claim(approve=True), "AUD-79")
        for _ in range(5):
            link(claim(approve=True, deprecate=True), "AUD-DEP")
        nocov = claim(approve=True, with_recipe=True); link(nocov, "AUD-NOCOV")
        ids["test_id"] = str(nocov.test_id)
        surf_claim = claim(approve=True); link(surf_claim, "AUD-SURF")
        for _ in range(500):
            link(claim(approve=False), "AUD-500")
        s.flush()
        # runs with NO stamp on the no-coverage claim (and NO coverage rows for it)
        for i in range(2):
            rid = uuid4()
            conn.execute(text(
                "INSERT INTO s4_execution_runs (run_id, recipe_id, recipe_version_seq, claim_test_id, environment_id, outcome, started_at, finished_at, evidence) "
                "SELECT CAST(:r AS uuid), recipe_id, version_seq, claim_test_id, :e, CAST('passed' AS run_outcome), now(), now(), '{}'::jsonb "
                "FROM test_recipes WHERE claim_test_id = CAST(:c AS uuid) AND valid_to IS NULL LIMIT 1"),
                {"r": str(rid), "e": ENV, "c": str(nocov.test_id)})
            M["runs"].append(str(rid))
        ids["run_id"] = M["runs"][0]
        # a repair proposal on that run (for proposal_id routes)
        pid = conn.execute(text(
            "INSERT INTO repair_proposals (run_id, claim_test_id, environment_id, verdict, proposal_kind) "
            "VALUES (CAST(:r AS uuid), CAST(:c AS uuid), :e, 'creation_rejected', 'rerun') RETURNING id"),
            {"r": M["runs"][0], "c": str(nocov.test_id), "e": ENV}).scalar()
        M["proposals"].append(pid); ids["proposal_id"] = pid
        # release scoped to an INACTIVE environment
        conn.execute(text("INSERT INTO release_targets (release_id, environment_id, declared_by, declared_at, active) VALUES (:r, :e, :u, now(), true)"),
                     {"r": rel_inactive, "e": env_inactive, "u": ADMIN})
        M["targets"].append([rel_inactive, env_inactive])
        # a REVOKED claim set: no producer writes status='revoked' (the reader handles it) — cloned from an approved set
        src = conn.execute(text("SELECT CAST(id AS text) FROM claim_sets WHERE status='approved' ORDER BY created_at DESC LIMIT 1")).scalar()
        new_set = str(uuid4())
        conn.execute(text("INSERT INTO claim_sets (id, persona_scope, inventory_version, catalogue_release_id, standard_profile, status, created_by, created_at, member_count) "
                          "SELECT CAST(:n AS uuid), persona_scope, inventory_version, catalogue_release_id, standard_profile, 'revoked', :u, now(), member_count "
                          "FROM claim_sets WHERE id = CAST(:s AS uuid)"), {"n": new_set, "s": src, "u": ADMIN})
        conn.execute(text("INSERT INTO claim_set_members (claim_set_id, test_id, applicability, executable, revoked_at, revoked_by) "
                          "SELECT CAST(:n AS uuid), test_id, applicability, executable, now(), :u FROM claim_set_members WHERE claim_set_id = CAST(:s AS uuid)"),
                     {"n": new_set, "s": src, "u": ADMIN})
        M["claim_sets"].append(new_set); ids["claim_set_id"] = new_set
        M["notes"].append("claim_sets.status='revoked' is read by claim_sets.py:223 and written by nothing — planted by direct UPDATE-shape insert")
        # a SUPERSEDED inventory version (two versions for one org; the first is superseded by the second)
        from primeqa.test_representation.claim_sets import create_inventory_version
        org = conn.execute(text("SELECT CAST(id AS text) FROM connected_orgs LIMIT 1")).scalar()
        # the five v1 identity fields a member carries (site, path, persona_scope + optional)
        members = [{"site": "aud-superseded.example.com", "path": "/aud", "persona_scope": "aud"}]
        try:
            v1 = create_inventory_version(s, members=members, created_by=ADMIN, notes="AUD inventory v1", connected_org_id=org)
            v2 = create_inventory_version(s, members=members, created_by=ADMIN, notes="AUD inventory v2 (supersedes v1)", connected_org_id=org)
            M["inventory_versions"] += [v1, v2]; ids["inventory_superseded"] = v1; ids["inventory_current"] = v2
        except Exception as exc:  # noqa: BLE001 — recorded, the sweep continues
            M["notes"].append("inventory version could not be minted through the service: %s: %s" % (type(exc).__name__, str(exc)[:160]))
        # a POLICY WITH NO RULES: the service refuses ('a policy needs at least one rule') — direct insert, draft
        pol = conn.execute(text("INSERT INTO quality_policies (name, version, status, note) VALUES ('AUD-no-rules', 1, 'draft', 'audit: zero rules') RETURNING CAST(id AS text)")).scalar()
        M["policies"].append(pol); ids["policy_id"] = pol
        M["notes"].append("quality_policies row with zero rules: the service refuses it; direct insert — the readers must cope")
        # an EXPIRED waiver: the service refuses a past expiry; the CHECK only requires expires_at > created_at
        wid = conn.execute(text(
            "INSERT INTO quality_waivers (release_id, item_kind, item_ref, axis, reviewer_user_id, reason, expires_at, created_by, created_at) "
            "VALUES (:r, 'claim', :c, 'functional', :u, 'audit: expired', now() - interval '1 day', :u, now() - interval '3 days') RETURNING CAST(id AS text)"),
            {"r": rel_inactive, "c": str(nocov.test_id), "u": ADMIN}).scalar()
        M["waivers"].append(wid); ids["waiver_id"] = wid
        # a SURFACE declared then UNLINKED
        s.flush(); s.close()
    from primeqa.intelligence.requirement_surface_console import declare_surface, unlink_surface
    # a surface can only be declared from the ACTIVE inventory (the highest version
    # with an approved claim set): pick one of its members
    with get_tenant_connection(T) as conn:
        active_member = conn.execute(text(
            "SELECT surface_key FROM ui_surface_inventory_members WHERE inventory_version = "
            "(SELECT MAX(inventory_version) FROM claim_sets WHERE status = 'approved') LIMIT 1")).scalar()
    # the link needs an ESTABLISHED identity for the requirement key (Step 1)
    from primeqa.test_representation.identity import establish_for_key
    with get_tenant_connection(T) as conn:
        try:
            establish_for_key(conn, "AUD-SURF", established_by=ADMIN)
        except Exception as exc:  # noqa: BLE001
            M["notes"].append("identity for AUD-SURF not established: %s: %s" % (type(exc).__name__, str(exc)[:120]))
    d = declare_surface(T, requirement_key="AUD-SURF", surface_key=active_member or "none", user_id=ADMIN)
    link_id = (d.get("link") or {}).get("link_id") or d.get("link_id")
    if link_id:
        u = unlink_surface(T, link_id=str(link_id), requirement_key="AUD-SURF", user_id=ADMIN, reason="audit: unlinked")
        M["surface_links"].append(str(link_id)); ids["link_id"] = str(link_id)
        M["notes"].append("surface declared then unlinked: declare=%s unlink=%s" % (d.get("ok"), u.get("ok")))
    else:
        M["notes"].append("declare_surface returned %s" % json.dumps(d, default=str)[:200])
    # PLANS: one never executed, one executed twice (the second refused)
    from primeqa.intelligence.run_plan_console import create_plan, run_plan
    p_never = create_plan(T, scope={"scope_kind": "requirement", "key": "AUD-79", "environment_id": ENV}, user_id=ADMIN, user_role="admin")
    p_twice = create_plan(T, scope={"scope_kind": "requirement", "key": "AUD-NOCOV", "environment_id": ENV}, user_id=ADMIN, user_role="admin")
    ids["plan_id"] = p_never.get("plan_id"); ids["plan_never"] = p_never.get("plan_id"); ids["plan_twice"] = p_twice.get("plan_id")
    M["plans"] += [x for x in (p_never.get("plan_id"), p_twice.get("plan_id")) if x]
    if p_twice.get("ok"):
        first = run_plan(T, plan_id=p_twice["plan_id"], user_id=ADMIN, user_role="admin")
        second = run_plan(T, plan_id=p_twice["plan_id"], user_id=ADMIN, user_role="admin")
        M["notes"].append("plan executed twice: first ok=%s jobs=%s; second ok=%s reason=%s" % (
            first.get("ok"), len((first.get("receipt") or {}).get("s4_jobs", [])), second.get("ok"), second.get("reason")))
        with get_tenant_connection(T) as conn:
            for (jid,) in conn.execute(text("SELECT id FROM s4_execution_jobs WHERE CAST(plan_id AS text) = :p"), {"p": p_twice["plan_id"]}).all():
                M.setdefault("jobs", []).append(jid); ids["job_id"] = jid
    # RUN 2 — a requirement whose IDENTITY is a non-Jira key (the AUD-014 latent shape):
    # source 'jira' with a jira_key, DECORATED with a different external_key (the
    # decorate affordance, views.py POST /requirements external_key=). Readers that
    # derive the key from jira_key look under the wrong key.
    rident = db.execute(text("INSERT INTO requirements (tenant_id, section_id, source, created_by, jira_summary, external_key, jira_key) "
                             "VALUES (1, :s, 'jira', :u, 'identity decorated: external_key differs from jira_key', 'AUD-IDENT', 'AUD-JK-1') RETURNING id"),
                        {"s": sid, "u": ADMIN}).scalar()
    M["requirements"].append(rident); ids["req_ident"] = rident
    rel_ident = rel("AUD-release-ident", [rident]); ids["release_ident"] = rel_ident
    db.commit()
    with get_tenant_connection(T) as conn:
        s = Session(bind=conn)
        for _ in range(2):
            cr = coord.write_claim(s, actor="s3", test_id=None, archetype="data_behavior", claim_kind="value-claim",
                                   asserted_truth=make_value_claim(value="Tech"), semantic_conditions=empty_conditions())
            coord.promote_claim_to_approved(s, actor="human", test_id=cr.test_id, version_seq=cr.version_seq)
            coord.link_requirement(s, actor="s3", test_id=cr.test_id, external_system="jira", external_key="AUD-IDENT", link_kind="generated_from")
            M["claims"].append(str(cr.test_id)); M["links"].append([str(cr.test_id), "AUD-IDENT"])
        s.flush(); s.close()
        # RUN 2 — ORPHAN run_id rows (the AUD-028 shape) in the three sibling tables that
        # carry a run_id with no foreign key; s6_interpretations itself now refuses (D-497)
        orphan = str(uuid4()); ids["orphan_run_id"] = orphan
        conn.execute(text("INSERT INTO s4_created_records (run_id, sobject, record_id, created_seq, cleaned, created_at, environment_id) "
                          "VALUES (CAST(:r AS uuid), 'Lead', 'AUD-ORPHAN-REC', 1, false, now(), :e)"), {"r": orphan, "e": ENV})
        conn.execute(text("INSERT INTO s6_reinterpretations (run_id, reinterpreted_at, code_version, verdict, envelope_reconstructed) "
                          "VALUES (CAST(:r AS uuid), now(), 'audit', 'passed', false)"), {"r": orphan})
        opid = conn.execute(text("INSERT INTO repair_proposals (run_id, claim_test_id, environment_id, verdict, proposal_kind, gate_verdict) "
                                 "VALUES (CAST(:r AS uuid), CAST(:c AS uuid), :e, 'creation_rejected', 'recipe_edit', 'SPECULATIVE') RETURNING id"),
                            {"r": orphan, "c": str(nocov.test_id), "e": ENV}).scalar()   # one ACTIVE proposal per (claim, kind): a different kind
        M["proposals"].append(opid); ids["orphan_proposal_id"] = opid
        M.setdefault("orphans", []).append(orphan)
        M["notes"].append("orphan run_id %s planted in s4_created_records, s6_reinterpretations, repair_proposals (no FK on any of the three; AUD-028)" % orphan[:8])
        try:
            conn.execute(text("SAVEPOINT s6o"))
            conn.execute(text("INSERT INTO s6_interpretations (run_id, recipe_id, claim_test_id, outcome, verdict, detail) "
                              "VALUES (CAST(:r AS uuid), CAST(:rc AS uuid), CAST(:c AS uuid), CAST('passed' AS run_outcome), 'passed', '{}'::jsonb)"),
                         {"r": orphan, "rc": str(uuid4()), "c": str(nocov.test_id)})
            conn.execute(text("RELEASE SAVEPOINT s6o"))
            M["notes"].append("s6_interpretations ACCEPTED an orphan verdict — the D-497 FK is missing on this database")
        except Exception as exc:  # noqa: BLE001
            conn.execute(text("ROLLBACK TO SAVEPOINT s6o"))
            M["notes"].append("s6_interpretations refused the orphan verdict (%s) — the D-497 FK holds" % type(exc).__name__)
    ids["user_id"] = ADMIN
    db.close()
    json.dump(M, open(manifest_path, "w"), indent=1, default=str)
    json.dump(ids, open(ids_path, "w"), indent=1, default=str)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in M.items() if k != "notes"}, indent=1))
    for n in M["notes"]:
        print("  note:", n)


def remove(manifest_path):
    """Statement by statement in AUTOCOMMIT under the replica role — a failed
    statement can no longer poison the rest (the September trap: one Postgres
    transaction, `except: pass` per table, nothing committed)."""
    from sqlalchemy import create_engine
    M = json.load(open(manifest_path))
    eng = create_engine(os.environ["DATABASE_URL"], isolation_level="AUTOCOMMIT")
    done, failed = 0, []

    def run(conn, sql, params=None):
        nonlocal done
        try:
            conn.execute(text(sql), params or {}); done += 1
        except Exception as exc:  # noqa: BLE001
            failed.append("%s: %s" % (sql[:60], type(exc).__name__))
    keys = ["AUD-79", "AUD-DEP", "AUD-NOCOV", "AUD-SURF", "AUD-500", "AUD-IDENT"]
    with eng.connect() as conn:
        conn.execute(text("SET session_replication_role = replica"))
        conn.execute(text("SET search_path TO tenant_%d, public" % T))
        if M.get("jobs"):
            run(conn, "DELETE FROM s4_execution_jobs WHERE id = ANY(:j)", {"j": M["jobs"]})
        for pid in M["plans"]:
            run(conn, "DELETE FROM run_plans WHERE id = CAST(:p AS uuid)", {"p": pid})
        for w in M["waivers"]:
            run(conn, "DELETE FROM quality_waivers WHERE id = CAST(:w AS uuid)", {"w": w})
        for p in M["policies"]:
            run(conn, "DELETE FROM quality_policy_rules WHERE policy_id = CAST(:p AS uuid)", {"p": p})
            run(conn, "DELETE FROM quality_policies WHERE id = CAST(:p AS uuid)", {"p": p})
        for cs in M["claim_sets"]:
            run(conn, "DELETE FROM claim_set_members WHERE claim_set_id = CAST(:c AS uuid)", {"c": cs})
            run(conn, "DELETE FROM claim_sets WHERE id = CAST(:c AS uuid)", {"c": cs})
        for v in M["inventory_versions"]:
            run(conn, "DELETE FROM logical_versions WHERE version_seq = :v", {"v": v})
        for pid in M["proposals"]:
            run(conn, "DELETE FROM repair_proposals WHERE id = :p", {"p": pid})
        for o in M.get("orphans", []):
            run(conn, "DELETE FROM s4_created_records WHERE run_id = CAST(:r AS uuid)", {"r": o})
            run(conn, "DELETE FROM s6_reinterpretations WHERE run_id = CAST(:r AS uuid)", {"r": o})
            run(conn, "DELETE FROM s6_interpretations WHERE run_id = CAST(:r AS uuid)", {"r": o})
        for r in M["runs"]:
            run(conn, "DELETE FROM s6_interpretations WHERE run_id = CAST(:r AS uuid)", {"r": r})
            run(conn, "DELETE FROM s4_execution_runs WHERE run_id = CAST(:r AS uuid)", {"r": r})
        for rel, env in M["targets"]:
            run(conn, "DELETE FROM release_targets WHERE release_id = :r AND environment_id = :e", {"r": rel, "e": env})
        for lid in M["surface_links"]:
            run(conn, "DELETE FROM requirement_surface_declarations WHERE CAST(link_id AS text) = :l", {"l": lid})
        run(conn, "DELETE FROM test_requirement_links WHERE external_key = ANY(:k)", {"k": keys})
        run(conn, "DELETE FROM requirement_identities WHERE external_key = ANY(:k)", {"k": keys})
        if M["claims"]:
            run(conn, "DELETE FROM s4_execution_jobs WHERE CAST(test_id AS text) = ANY(:c)", {"c": M["claims"]})
            run(conn, "DELETE FROM test_claim_coverage WHERE CAST(claim_test_id AS text) = ANY(:c)", {"c": M["claims"]})
            run(conn, "DELETE FROM test_recipes WHERE CAST(claim_test_id AS text) = ANY(:c)", {"c": M["claims"]})
            run(conn, "DELETE FROM test_provenance WHERE CAST(claim_test_id AS text) = ANY(:c)", {"c": M["claims"]})
            run(conn, "DELETE FROM s8_grounding_validity WHERE CAST(test_id AS text) = ANY(:c)", {"c": M["claims"]})
            run(conn, "DELETE FROM test_claims WHERE CAST(test_id AS text) = ANY(:c)", {"c": M["claims"]})
        for did in M["decisions"]:
            run(conn, "DELETE FROM public.release_decisions WHERE id = :d", {"d": did})
        for rid in M["releases"]:
            run(conn, "DELETE FROM public.release_decisions WHERE release_id = :r", {"r": rid})
            run(conn, "DELETE FROM public.release_requirements WHERE release_id = :r", {"r": rid})
            run(conn, "DELETE FROM public.releases WHERE id = :r", {"r": rid})
        for rid in M["requirements"]:
            run(conn, "DELETE FROM public.requirements WHERE id = :r", {"r": rid})
        for gid in M["groups"]:
            run(conn, "DELETE FROM public.group_environments WHERE group_id = :g", {"g": gid})
            run(conn, "DELETE FROM public.groups WHERE id = :g", {"g": gid})
        for cid in M["connections"]:
            run(conn, "DELETE FROM public.connections WHERE id = :c", {"c": cid})
        for eid in M["environments"]:
            run(conn, "DELETE FROM public.environments WHERE id = :e", {"e": eid})
        for sid in M["sections"]:
            run(conn, "DELETE FROM public.sections WHERE id = :s", {"s": sid})
        residue = {
            "links": conn.execute(text("SELECT count(*) FROM test_requirement_links WHERE external_key = ANY(:k)"), {"k": keys}).scalar(),
            "claims": conn.execute(text("SELECT count(*) FROM test_claims WHERE CAST(test_id AS text) = ANY(:c)"), {"c": M["claims"] or ["x"]}).scalar(),
            "requirements": conn.execute(text("SELECT count(*) FROM public.requirements WHERE external_key LIKE 'AUD-%'")).scalar(),
            "releases": conn.execute(text("SELECT count(*) FROM public.releases WHERE name LIKE 'AUD-%'")).scalar(),
            "orphans": conn.execute(text("SELECT count(*) FROM s4_created_records WHERE record_id = 'AUD-ORPHAN-REC'")).scalar(),
        }
    print("removed: %d statements ok, %d failed %s; residue %s" % (done, len(failed), failed[:5], residue))


if __name__ == "__main__":
    {"plant": lambda: plant(sys.argv[2], sys.argv[3]), "remove": lambda: remove(sys.argv[2])}[sys.argv[1]]()
