"""S6 UI-conformance result processor — engine observations → verdicts
(LLD 3A-4 §b/§c/§e).

THE BOUNDARY (D-460 / SAD A10): this module is the ONLY place UI
verdicts are computed. The browser worker produces engine observations
and nothing else; this processor runs service-side, after the fact, over
stored observation rows — the worker does not know it exists. Any
mapping, applicability, ownership, or verdict logic belongs here and
never in ``primeqa/browser_worker``.

Deterministic and LLM-free. The honest remainder is a first-class
output: unmapped engine ids are counted and recorded (never dropped,
never judged); surfaces whose scan did not complete produce STATUSES,
not verdicts; members the processor cannot judge are listed with their
reason. A processor that cannot prove what it saw refuses to convict
(arm H): an unresolvable element or unmapped dependency is
NOT_DETERMINED, never FAIL.

**AND IT REFUSES TO ACQUIT ON THE SAME IGNORANCE**
(LLD_VERDICT_SEMANTICS, opened by D-465). PASS requires POSITIVE
evidence that the rule RAN and found nothing — never merely the absence
of a violation. A rule the engine reported INCOMPLETE, a rule outside
the manifest-pinned run set, a rule the observation does not attest,
and a rule reported inapplicable are each NOT_DETERMINED with their own
named reason. A mechanism that did not run cannot acquit.

**THE EVIDENCE LAW.** A verdict asserts only what the STORED EVIDENCE
attests. Offline analysis ABOUT a record is not evidence WITHIN it: we
can read the pinned engine artifact and work out which rules it ships
disabled, and that analysis belongs in a report — but it cannot promote
or demote a verdict, because the observation itself attests nothing
about those rules. This is exactly why a pre-attestation observation
re-decides to ``legacy_unattested`` even where the disabled subset is
identifiable by hand: the record, not our knowledge of the record, is
what a verdict may cite.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid as _uuid_mod
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

PASS = "PASS"
FAIL = "FAIL"
NEEDS_HUMAN = "NEEDS_HUMAN"
NOT_DETERMINED = "NOT_DETERMINED"

_ENGINE = "axe-core"


class ProcessingError(ValueError):
    """A refused processing run — the message names the exact cause."""


# ---------------------------------------------------------------------------
# DE-11 ownership — processor-side by principle (LLD §e): fingerprints
# are observations (structure the worker saw); ownership is
# interpretation (whose structure it is).
# ---------------------------------------------------------------------------

_CLIENT_COMPONENT = re.compile(r"<(c-[\w-]+)|(?:^|[\s\"'>])(c-[\w-]+)")
_PLATFORM_MARKERS = re.compile(
    r"<(?:lightning|force|flexipage|laf|one)-[\w-]+|class=\"[^\"]*slds-")


def bundle_developer_name(tag: str) -> str:
    """The deterministic LWC mapping (LLD 3A-5 §d): DOM tag
    ``c-loan-widget`` → bundle DeveloperName ``loanWidget`` — strip the
    ``c-`` namespace prefix, kebab→camel."""
    parts = tag.removeprefix("c-").split("-")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def classify_ownership(node: dict, resolve_bundle=None):
    """(origin marker, resolved bundle entity id | None) for ONE failing
    element from its stored evidence fragment. Conservative: UNKNOWN is
    an honest answer, never upgraded by guesswork.

    Per the 2026-08-26 ruling (LLD 3A-5 §d): **CONFIRMED requires
    resolution** — the ``c-*`` tag maps to a synced
    LightningComponentBundle entity via ``resolve_bundle(developer_name)
    → entity_id | None``. **No resolution ⇒ PROBABLE unconditionally**
    (client-namespace markup we cannot attribute), including when no
    resolver is supplied or the org has no bundle rows. The 3A-4
    spike-grade CONFIRMED-on-marker behavior is corrected here as a
    signed-design conformance fix.

      - ``c-*`` markup + the tag resolves → CONFIRMED (+ the bundle id);
      - ``c-*`` markup, unresolved → PROBABLE;
      - ``lightning-*``/``force-*``/``flexipage-*`` markup or ``slds-``
        classes → PROBABLE (platform-standard chrome);
      - anything else → UNKNOWN.
    """
    fragment = " ".join(filter(None, (
        node.get("html") or "",
        " ".join(str(t) for t in (node.get("target") or [])),
    )))
    m = _CLIENT_COMPONENT.search(fragment)
    if m:
        tag = m.group(1) or m.group(2)
        if resolve_bundle is not None:
            bundle_id = resolve_bundle(bundle_developer_name(tag))
            if bundle_id is not None:
                return "CONFIRMED", str(bundle_id)
        return "PROBABLE", None
    if _PLATFORM_MARKERS.search(fragment):
        return "PROBABLE", None
    return "UNKNOWN", None


def _resolvable(node: dict) -> bool:
    return bool(node.get("html") or node.get("target"))


# ---------------------------------------------------------------------------
# The verdict decision — pure (LLD §c, exact semantics)
# ---------------------------------------------------------------------------

def decide_verdict(
    *,
    applicability: str,
    executable: bool,
    capability: str,
    rule_engine_ids: frozenset,
    observation: dict,
) -> tuple[Optional[str], dict]:
    """(verdict, basis) for one member against one COMPLETED surface
    observation — or (None, {"no_verdict_reason": …}) for members this
    slice never judges. The caller has already established the scan
    completed (statuses are not verdicts)."""
    if applicability == "NOT_APPLICABLE":
        return None, {"no_verdict_reason": "not_applicable"}
    if applicability == "APPLICABLE" and not executable:
        return None, {"no_verdict_reason": "not_executable_mode_b"}
    if capability == "HUMAN_ONLY":
        return None, {"no_verdict_reason": "human_only_no_engine_input"}

    obs = observation.get("engine_observations") or {}
    if capability == "HUMAN_WITH_CANDIDATE":
        incomplete = obs.get("incomplete")
        if incomplete is not None:
            candidates = [i for i in incomplete
                          if i.get("id") in rule_engine_ids]
            return NEEDS_HUMAN, {"candidates": candidates}
        # The spike observation schema records incomplete_count only —
        # honest, never fabricated (see LLD §g / the HOLD note).
        return NEEDS_HUMAN, {
            "candidates_unavailable": True,
            "incomplete_count": obs.get("incomplete_count", 0)}

    # AUTO from here.
    if not rule_engine_ids:
        return NOT_DETERMINED, {"reason": "unmapped_dependency",
                                "detail": "no engine binding for this "
                                          "rule at the pinned engine"}
    if not (observation.get("fingerprint") or {}).get("sha256"):
        return NOT_DETERMINED, {"reason": "missing_fingerprint"}

    mapped = [v for v in (obs.get("violations") or [])
              if v.get("id") in rule_engine_ids]
    if not mapped:
        return _decide_non_violation(rule_engine_ids, obs)
    nodes = [n for v in mapped for n in (v.get("nodes") or [])]
    resolvable = [n for n in nodes if _resolvable(n)]
    if not resolvable:
        return NOT_DETERMINED, {
            "reason": "unresolvable_element",
            "engine_ids": sorted({v["id"] for v in mapped}),
            "node_count": len(nodes)}
    return FAIL, {
        "engine_ids": sorted({v["id"] for v in mapped}),
        "nodes": [{"html": n.get("html"), "target": n.get("target")}
                  for n in resolvable[:10]],
    }


def _decide_non_violation(rule_engine_ids: frozenset, obs: dict):
    """No mapped violation — which is NOT yet a pass. Establish, in
    order, that the engine could not decide, that the rule ran, and that
    the observation attests it (LLD_VERDICT_SEMANTICS §a + §b.2)."""
    ids = set(rule_engine_ids)

    # (a) the engine reported it could not determine this rule
    incomplete = obs.get("incomplete")
    if incomplete is not None:
        candidates = [i for i in incomplete if i.get("id") in ids]
        if candidates:
            return NOT_DETERMINED, {
                "reason": "engine_incomplete",
                "engine_ids": sorted({c["id"] for c in candidates}),
                "candidates": candidates}

    # (b.2) attestation. An observation with no retained pass ids can
    # attest nothing — every run before this fix. Never a PASS.
    attested = obs.get("passes_ids")
    if attested is None:
        return NOT_DETERMINED, {
            "reason": "legacy_unattested",
            "detail": "observation predates rule-execution attestation; "
                      "absence of a violation is not evidence the rule ran",
            "engine_ids": sorted(ids)}

    # (b.1) the rule was not in the manifest-pinned run set
    run_set = obs.get("run_set")
    if run_set is not None and not (ids & set(run_set)):
        return NOT_DETERMINED, {
            "reason": "rule_not_executed",
            "detail": "engine id outside the manifest-pinned run set",
            "engine_ids": sorted(ids)}

    hit = ids & set(attested)
    if hit:
        return PASS, {"attested_by": sorted(hit),
                      "engine_ids_checked": sorted(ids)}

    inapplicable = ids & set(obs.get("inapplicable_ids") or ())
    if inapplicable:
        # Not exercised on this surface — visible as its own class, never
        # dissolved into PASS.
        return NOT_DETERMINED, {"reason": "rule_inapplicable",
                                "engine_ids": sorted(inapplicable)}

    return NOT_DETERMINED, {
        "reason": "rule_unattested",
        "detail": "in the run set, but the engine reported neither a "
                  "pass nor an inapplicable for it",
        "engine_ids": sorted(ids)}


# ---------------------------------------------------------------------------
# The processor
# ---------------------------------------------------------------------------

def _bindings(session: Session, engine_version: str) -> tuple[dict, dict, str]:
    """(engine_id -> [(rule, ver)], plm_rule -> frozenset(engine_ids),
    bindings snapshot hash)."""
    from primeqa.knowledge.rule_registry import bindings_for_engine

    fwd = bindings_for_engine(session, _ENGINE, engine_version)
    rev: dict[str, set] = {}
    for engine_id, rules in fwd.items():
        for rule_id, _ver in rules:
            rev.setdefault(rule_id, set()).add(engine_id)
    snapshot = hashlib.sha256(json.dumps(
        {k: sorted(str(r) for r in v) for k, v in sorted(fwd.items())},
        sort_keys=True).encode()).hexdigest()
    return fwd, {k: frozenset(v) for k, v in rev.items()}, snapshot


_decide_engine_backed = decide_verdict     # the 3A-4 path, unchanged


def _decide_custom(session: Session, rule_id: str, observation: dict,
                   pins: dict):
    """Resolve the tenant's ACTIVE content and evaluate it over the
    census (cust_evaluation). Returns (verdict, basis) or
    (None, {no_verdict_reason}) when the rule is not ACTIVE here."""
    from primeqa.interpretation.cust_evaluation import evaluate_rule
    from primeqa.knowledge.cust_authoring import (
        load_active_content, load_token_sets)

    content = load_active_content(session, rule_id)
    if content is None:
        return None, {"no_verdict_reason": "custom_rule_not_active"}
    token_sets = load_token_sets(session, content.get("token_set_pins"))
    census_pins = (pins or {}).get("census") or {}

    def resolve_bundle_tag(tag: str):
        # c-loan-card -> loanCard: the namespace prefix drops, kebab
        # camel-cases. Exactly-one resolution or None — ambiguity is
        # never a guessed owner (the classify_ownership law).
        parts = tag.split("-")[1:]
        if not parts:
            return None
        name = parts[0] + "".join(w.capitalize() for w in parts[1:])
        rows = session.execute(text("""
            SELECT sf_api_name FROM entities
            WHERE entity_type = 'LightningComponentBundle'
              AND sf_api_name = :n AND valid_to_seq IS NULL
        """), {"n": name}).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    return evaluate_rule(
        content, observation, token_sets=token_sets,
        resolve_bundle_tag=resolve_bundle_tag,
        epsilon_px=float(census_pins.get("length_epsilon_px", 0.5)))


def process_job(session: Session, *, job_id: UUID) -> dict:
    """Process one scan job's stored observations into verdicts.

    Idempotent: UPSERT on (job_id, test_id) — reprocessing rewrites
    byte-identical rows from the same deterministic inputs; a changed
    outcome is attributable via the recorded bindings snapshot hash.
    """
    from primeqa.browser_worker.manifest import get_manifest
    from primeqa.execution_engine.ui_manifest import (
        _release_capabilities, load_members_with_claims)

    jrow = session.execute(text(
        "SELECT manifest_id FROM s4_ui_inspection_jobs WHERE id = :i"),
        {"i": str(job_id)}).fetchone()
    if jrow is None:
        raise ProcessingError(f"job {job_id} does not exist")
    manifest_id = str(jrow[0])
    payload = get_manifest(session, manifest_id)["payload"]
    claim_set_id = payload.get("claim_set_id")
    if not claim_set_id:
        raise ProcessingError(
            f"job {job_id}'s manifest is not claim_set-built — 3A-4 "
            "processes claim_set manifests only")
    pins = payload.get("pins") or {}
    engine_version = pins.get("axe_version")
    if not engine_version:
        raise ProcessingError("manifest pins carry no axe_version")

    data = load_members_with_claims(session, UUID(claim_set_id))
    caps = _release_capabilities(session, data["catalogue_release_id"])
    _fwd, rev, bindings_hash = _bindings(session, engine_version)

    def resolve_bundle(developer_name: str):
        # Exactly-one resolution or nothing: two current bundles with
        # the same DeveloperName (multi-org) make attribution ambiguous
        # — that is a PROBABLE, never a guessed CONFIRMED.
        rows = session.execute(text("""
            SELECT id FROM entities
            WHERE entity_type = 'LightningComponentBundle'
              AND sf_api_name = :n AND valid_to_seq IS NULL
        """), {"n": developer_name}).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    results = {r[0]: {"observation": r[1], "evidence_state": r[2]}
               for r in session.execute(text("""
                   SELECT surface_key, observation, evidence_state
                   FROM s4_ui_inspection_results WHERE job_id = :i
               """), {"i": str(job_id)}).fetchall()}

    surface_statuses = {k: (v["observation"] or {}).get("status", "MISSING")
                        for k, v in results.items()}
    known_engine_ids = set(_fwd.keys())
    unmapped: set[str] = set()
    for v in results.values():
        obs = (v["observation"] or {}).get("engine_observations") or {}
        observed = {viol.get("id") for viol in (obs.get("violations") or [])}
        observed |= {i.get("id") for i in (obs.get("incomplete") or [])}
        unmapped |= {i for i in observed if i and i not in known_engine_ids}

    verdict_counts: dict[str, int] = {}
    no_verdict: dict[str, str] = {}
    written = 0
    for m in data["members"]:
        if m["revoked"]:
            no_verdict[m["test_id"]] = "revoked"
            continue
        result = results.get(m["surface_key"])
        if result is None:
            no_verdict[m["test_id"]] = "surface_not_in_job"
            continue
        observation = result["observation"] or {}
        status = observation.get("status")
        if status != "OK":
            # A run that couldn't look is not a run that judged.
            no_verdict[m["test_id"]] = f"surface_status:{status}"
            continue
        if m["plimsol_rule_id"].startswith("PLM-CUST-"):
            # Phase 5 Part 2 (§h): a CUSTOM rule decides from the census,
            # never from the engine. Same processor, same verdict table,
            # same evidence law — a different attestation source.
            verdict, basis = _decide_custom(
                session, m["plimsol_rule_id"], observation, pins)
            if verdict is None:
                no_verdict[m["test_id"]] = basis["no_verdict_reason"]
                continue
        else:
            capability = caps.get(m["plimsol_rule_id"], "AUTO")
            verdict, basis = _decide_engine_backed(
                applicability=m["applicability"], executable=m["executable"],
                capability=capability,
                rule_engine_ids=rev.get(m["plimsol_rule_id"], frozenset()),
                observation=observation)
            if verdict is None:
                no_verdict[m["test_id"]] = basis["no_verdict_reason"]
                continue
        ownership = None
        owner_bundle_ref = None
        if verdict == FAIL and not m["plimsol_rule_id"].startswith("PLM-CUST-"):
            ownership, owner_bundle_ref = classify_ownership(
                basis["nodes"][0], resolve_bundle)
        session.execute(text("""
            INSERT INTO s6_ui_verdicts
                (id, manifest_id, job_id, surface_key, claim_set_id,
                 test_id, plimsol_rule_id, verdict, verdict_basis,
                 ownership, owner_bundle_ref, evidence_state_at_write,
                 processed_at)
            VALUES (:id, :m, :j, :sk, :cs, :t, :r, :v,
                    CAST(:b AS JSONB), :o, :obr, :e, NOW())
            ON CONFLICT (job_id, test_id) DO UPDATE SET
                verdict = EXCLUDED.verdict,
                verdict_basis = EXCLUDED.verdict_basis,
                ownership = EXCLUDED.ownership,
                owner_bundle_ref = EXCLUDED.owner_bundle_ref,
                evidence_state_at_write = EXCLUDED.evidence_state_at_write,
                processed_at = NOW()
        """), {"id": str(_uuid_mod.uuid4()), "m": manifest_id,
               "j": str(job_id), "sk": m["surface_key"],
               "cs": claim_set_id, "t": m["test_id"],
               "r": m["plimsol_rule_id"], "v": verdict,
               "b": json.dumps(basis, sort_keys=True), "o": ownership,
               "obr": owner_bundle_ref,
               "e": result["evidence_state"]})
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        written += 1

    session.execute(text("""
        INSERT INTO s6_ui_processing_runs
            (job_id, manifest_id, claim_set_id, engine, engine_version,
             bindings_hash, unmapped_engine_ids, surface_statuses,
             verdict_counts, no_verdict_members, processed_at)
        VALUES (:j, :m, :cs, :en, :ev, :bh, CAST(:u AS JSONB),
                CAST(:ss AS JSONB), CAST(:vc AS JSONB),
                CAST(:nv AS JSONB), NOW())
        ON CONFLICT (job_id) DO UPDATE SET
            bindings_hash = EXCLUDED.bindings_hash,
            unmapped_engine_ids = EXCLUDED.unmapped_engine_ids,
            surface_statuses = EXCLUDED.surface_statuses,
            verdict_counts = EXCLUDED.verdict_counts,
            no_verdict_members = EXCLUDED.no_verdict_members,
            processed_at = NOW()
    """), {"j": str(job_id), "m": manifest_id, "cs": claim_set_id,
           "en": _ENGINE, "ev": engine_version, "bh": bindings_hash,
           "u": json.dumps(sorted(unmapped)),
           "ss": json.dumps(surface_statuses, sort_keys=True),
           "vc": json.dumps(verdict_counts, sort_keys=True),
           "nv": json.dumps(no_verdict, sort_keys=True)})
    session.flush()
    return {"job_id": str(job_id), "claim_set_id": claim_set_id,
            "verdicts_written": written, "verdict_counts": verdict_counts,
            "unmapped_engine_ids": sorted(unmapped),
            "surface_statuses": surface_statuses,
            "no_verdict_members": len(no_verdict)}


def list_verdicts(session: Session, *, claim_set_id: UUID,
                  verdict: Optional[str] = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
    """The minimal verdict listing (LLD §h) — claim identity, verdict,
    ownership, evidence. ``evidence_complete`` JOINs the LIVE result-row
    evidence state (2.5 law): a verdict over sub-VERIFIED evidence is
    NEVER presented evidence-complete, whatever was true at write."""
    where = "v.claim_set_id = :cs"
    params: dict = {"cs": str(claim_set_id),
                    "lim": min(limit, 50), "off": offset}
    if verdict:
        where += " AND v.verdict = :v"
        params["v"] = verdict
    rows = session.execute(text(f"""
        SELECT v.test_id, v.plimsol_rule_id, v.surface_key, v.verdict,
               v.ownership, v.verdict_basis, v.job_id,
               r.evidence_state, r.evidence_keys, v.owner_bundle_ref
        FROM s6_ui_verdicts v
        LEFT JOIN s4_ui_inspection_results r
          ON r.job_id = v.job_id AND r.surface_key = v.surface_key
        WHERE {where}
        ORDER BY v.verdict, v.plimsol_rule_id, v.surface_key
        LIMIT :lim OFFSET :off
    """), params).fetchall()
    return [{
        "test_id": str(r[0]), "plimsol_rule_id": r[1],
        "surface_key": r[2], "verdict": r[3], "ownership": r[4],
        "verdict_basis": r[5], "job_id": str(r[6]),
        "evidence_state": r[7],
        "evidence_complete": r[7] == "REFERENCED",
        "evidence_keys": r[8],
        "owner_bundle_ref": str(r[9]) if r[9] else None,
    } for r in rows]
