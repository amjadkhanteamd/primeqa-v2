"""S4 manifest builder — manifests FROM an approved claim_set (LLD 3A-4 §a).

Membership by reference, never reconstructed (D-461 + D-281): the builder
reads the approved set's recorded member rows, stamps ``claim_set_id``
into the payload, and the processor later reads the SAME rows. The
recipe-per-claim → one-scan-per-surface collapse happens HERE; the
processor fans observations back out per member.

The two 3A-2 parked wirings land in this module:
  - the browser-plane enqueue consults RECIPE_MODES (declared table, not
    kind-name inference) before any job exists;
  - manifest catalogue pins are SOURCED from the S5 release + artifact
    rows, with sha256 equality asserted against the vendored engine —
    a mismatch refuses the build.

D-460: this module never interprets. It composes WHAT to scan; verdicts
live in ``primeqa/interpretation/ui_conformance.py`` only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid as _uuid_mod
from pathlib import Path
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from primeqa.execution_engine.modes import READ_ONLY, mode_for
from primeqa.test_representation.models.surface import (
    SurfaceNaturalKey,
    canonical_surface_key,
)

_REPO_ROOT = Path(__file__).parents[2]
_UI_INSPECTION_KIND = "ui-inspection"


class ManifestBuildError(ValueError):
    """A refused manifest build — the message names the exact cause."""


def _playwright_pin() -> str:
    """The playwright pin, read from requirements-browser.txt — the same
    source the worker image installs from (deterministic, never
    hand-carried)."""
    txt = (_REPO_ROOT / "requirements-browser.txt").read_text("utf-8")
    m = re.search(r"^playwright==([\w.]+)$", txt, re.MULTILINE)
    if not m:
        raise ManifestBuildError(
            "requirements-browser.txt carries no playwright pin")
    return m.group(1)


def _engine_pins(session: Session) -> dict:
    """The axe pins from the S5 artifact row, with the sha256 asserted
    against the vendored file (LLD §a: pins stop being hand-carried)."""
    from primeqa.knowledge.rule_registry import pinned_artifact

    art = pinned_artifact(session, "engine", "axe-core")
    if art is None:
        raise ManifestBuildError(
            "no pinned axe-core engine artifact in s5_artifacts")
    vendored = _REPO_ROOT / art.repo_path
    if not vendored.exists():
        raise ManifestBuildError(
            f"vendored engine missing at {art.repo_path}")
    actual = hashlib.sha256(vendored.read_bytes()).hexdigest()
    if actual != art.sha256:
        raise ManifestBuildError(
            f"engine hash mismatch: s5_artifacts pins {art.sha256[:12]}… "
            f"but {art.repo_path} hashes {actual[:12]}… — refusing the "
            "build (the pin and the vendored engine must agree)")
    return {"axe_version": art.version, "axe_sha256": art.sha256,
            "playwright_version": _playwright_pin(),
            # Phase 7: the spike-era pin restored — record what is
            # known (env-provided in the deployed services; None is an
            # honest "not recorded", and None->value counts as a moved
            # tool dimension).
            "worker_image_digest": os.environ.get(
                "PLIMSOL_WORKER_IMAGE_DIGEST")}


def engine_run_set(session: Session, release_id: int,
                   engine: str, engine_version: str) -> list:
    """The engine rule ids bound to the release's rules at this engine
    version — the manifest's RUN-SET pin (LLD_VERDICT_SEMANTICS §b.1).

    D-461 pinned the engine VERSION but not the engine RUN SET, so a
    rule the engine ships disabled was silently never evaluated. The
    builder resolves the set HERE (it may read S5); the worker receives
    it as manifest data and never derives it (it cannot read S5 —
    D-460)."""
    rows = session.execute(text("""
        SELECT DISTINCT b.engine_rule_id
        FROM s5_catalogue_release_members m
        JOIN s5_engine_bindings b
          ON b.rule_id = m.rule_id AND b.rule_version = m.rule_version
        WHERE m.release_id = :r AND b.engine = :e
          AND b.engine_version = :v
        ORDER BY b.engine_rule_id
    """), {"r": release_id, "e": engine, "v": engine_version}).fetchall()
    if not rows:
        raise ManifestBuildError(
            f"no {engine} {engine_version} bindings for catalogue release "
            f"{release_id} — refusing to build a manifest whose run set "
            "would be empty (an unpinned run set cannot attest a PASS)")
    return [r[0] for r in rows]


def capture_org_environment_snapshot(session: Session, sf_client) -> str:
    """Phase 7 (LLD §c): record the org environment AT MANIFEST BUILD
    time — the manifest records the world it was built for (the D-461
    pin philosophy). Immutable + hash-keyed: identical content reuses
    the existing snapshot row. Returns the snapshot id."""
    env = sf_client.fetch_org_environment()
    canonical = json.dumps(env, sort_keys=True, separators=(",", ":"),
                           default=str)
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    existing = session.execute(text(
        "SELECT id FROM org_environment_snapshots "
        "WHERE content_hash = :h"), {"h": content_hash}).scalar()
    if existing is not None:
        return str(existing)
    snap_id = str(_uuid_mod.uuid4())
    session.execute(text("""
        INSERT INTO org_environment_snapshots
            (id, platform_api_version, organization, packages,
             content_hash)
        VALUES (:i, :v, CAST(:o AS JSONB), CAST(:p AS JSONB), :h)
    """), {"i": snap_id, "v": env.get("platform_api_version"),
           "o": json.dumps(env.get("organization") or {}),
           "p": json.dumps(env.get("packages") or []),
           "h": content_hash})
    session.flush()
    return snap_id


def _viewport_dict(viewport: Optional[str]) -> Optional[dict]:
    if not viewport:
        return None
    m = re.fullmatch(r"(\d+)x(\d+)", viewport)
    if not m:
        raise ManifestBuildError(f"unparseable viewport {viewport!r}")
    return {"width": int(m.group(1)), "height": int(m.group(2))}


def surface_included(applicability: str, executable: bool,
                     capability: str) -> bool:
    """The collapse rule (LLD §a fan-out rule, step 1): a member
    contributes its surface iff APPLICABLE+executable, or its rule's
    capability in the PINNED release is HUMAN_WITH_CANDIDATE (the scan
    feeds candidates). HUMAN_ONLY / NOT_APPLICABLE / NOT-executable
    members contribute no surface."""
    if applicability == "APPLICABLE" and executable:
        return True
    return capability == "HUMAN_WITH_CANDIDATE"


def _release_capabilities(session: Session, release_id: int) -> dict:
    """rule_id -> automation_capability at the release's RECORDED
    versions (deterministic — the release is immutable membership)."""
    rows = session.execute(text("""
        SELECT m.rule_id, v.automation_capability
        FROM s5_catalogue_release_members m
        JOIN s5_rule_versions v
          ON v.rule_id = m.rule_id AND v.version = m.rule_version
        WHERE m.release_id = :r
    """), {"r": release_id}).fetchall()
    if not rows:
        raise ManifestBuildError(
            f"catalogue release {release_id} has no recorded members")
    return {r[0]: r[1] for r in rows}


def load_members_with_claims(session: Session, claim_set_id: UUID) -> dict:
    """The set row + its member rows joined to each member's CURRENT
    claim (rule id + surface). Shared by the builder and the processor —
    both read the SAME recorded membership."""
    srow = session.execute(text("""
        SELECT status, catalogue_release_id, persona_scope
        FROM claim_sets WHERE id = :i
    """), {"i": str(claim_set_id)}).fetchone()
    if srow is None:
        raise ManifestBuildError(f"claim_set {claim_set_id} does not exist")
    rows = session.execute(text("""
        SELECT m.test_id, m.applicability, m.executable,
               m.revoked_at IS NOT NULL,
               c.asserted_truth->>'plimsol_rule_id',
               c.asserted_truth->'surface'
        FROM claim_set_members m
        JOIN test_claims c
          ON c.test_id = m.test_id AND c.valid_to IS NULL
        WHERE m.claim_set_id = :i
        ORDER BY m.test_id
    """), {"i": str(claim_set_id)}).fetchall()
    members = []
    for r in rows:
        surface = SurfaceNaturalKey(**r[5])
        members.append({
            "test_id": str(r[0]), "applicability": r[1],
            "executable": r[2], "revoked": r[3],
            "plimsol_rule_id": r[4], "surface": surface,
            "surface_key": canonical_surface_key(surface),
        })
    return {"status": srow[0], "catalogue_release_id": srow[1],
            "persona_scope": srow[2], "members": members}


def build_manifest_for_claim_set(
    session: Session,
    *,
    claim_set_id: UUID,
    scheme: str = "https",
    stabilisation: Optional[dict] = None,
    auth: Optional[dict] = None,
    execution_mode: str = "claim-set",
    sf_client=None,
    org_env_snapshot_id: Optional[str] = None,
) -> str:
    """Build + persist an immutable Run Manifest from an APPROVED
    claim_set. Returns the manifest id. The payload records
    ``claim_set_id`` (membership by reference) and
    ``excluded_revoked`` (members revoked since approval — visible,
    never silent)."""
    from primeqa.browser_worker.manifest import create_manifest

    data = load_members_with_claims(session, claim_set_id)
    if data["status"] != "approved":
        raise ManifestBuildError(
            f"claim_set {claim_set_id} is {data['status']!r} — manifests "
            "build only from APPROVED sets (D3/F10)")
    caps = _release_capabilities(session, data["catalogue_release_id"])

    surfaces: dict[str, dict] = {}
    excluded_revoked = []
    for m in data["members"]:
        if m["revoked"]:
            excluded_revoked.append(m["test_id"])
            continue
        cap = caps.get(m["plimsol_rule_id"])
        if cap is None:
            raise ManifestBuildError(
                f"member rule {m['plimsol_rule_id']} is not in release "
                f"{data['catalogue_release_id']} — the set and its pin "
                "disagree")
        if not surface_included(m["applicability"], m["executable"], cap):
            continue
        key = m["surface_key"]
        if key not in surfaces:
            s = m["surface"]
            entry = {"key": key, "url": f"{scheme}://{s.site}{s.path}"}
            vp = _viewport_dict(s.viewport)
            if vp:
                entry["viewport"] = vp
            surfaces[key] = entry
    if not surfaces:
        raise ManifestBuildError(
            f"claim_set {claim_set_id} yields zero scannable surfaces — "
            "an empty manifest is never built")

    # Phase 7 (LLD §c): the org-environment pin. Captured live when a
    # client is supplied; a pre-captured id is accepted (fixtures /
    # planted flows); absent both, the pin records None — the
    # comparator treats it as "not captured", honestly.
    if sf_client is not None and org_env_snapshot_id is None:
        org_env_snapshot_id = capture_org_environment_snapshot(
            session, sf_client)

    pins = _engine_pins(session)
    run_set = engine_run_set(session, data["catalogue_release_id"],
                             "axe-core", pins["axe_version"])

    payload = {
        "claim_set_id": str(claim_set_id),
        "excluded_revoked": excluded_revoked,
        "surfaces": [surfaces[k] for k in sorted(surfaces)],
        "pins": {
            **pins,
            "catalogue_release_id": data["catalogue_release_id"],
            "engine_run_set": run_set,
            "engine_run_set_hash": hashlib.sha256(
                "\n".join(run_set).encode()).hexdigest(),
            "catalogue_content_hash": session.execute(text(
                "SELECT content_hash FROM s5_catalogue_releases "
                "WHERE id = :r"),
                {"r": data["catalogue_release_id"]}).scalar_one().strip(),
            "org_env_snapshot_id": org_env_snapshot_id,
        },
        "stabilisation": stabilisation or {},
        "execution": {"mode": execution_mode},
    }
    if auth is not None:
        payload["auth"] = auth
    return create_manifest(session, payload)


def enqueue_ui_run(session: Session, *, subject, claim_set_id: UUID,
                   scheme: str = "https",
                   stabilisation: Optional[dict] = None,
                   auth: Optional[dict] = None,
                   sf_client=None,
                   org_env_snapshot_id: Optional[str] = None) -> dict:
    """The enqueue boundary (LLD_PRODUCTIONISATION §c) — the D-245
    posture replicated for ui-inspection: authorize(subject, MEMBER)
    decides allowed (an AuthorizationError on deny — the route wrapper
    envelopes it as 403), the RECIPE_MODES consult stays inside
    enqueue_manifest_job (D6), and the act writes activity_log with the
    REAL actor. The org execution_policy chokepoint governs S4 org
    dispatch, not this plane — its three gates are tier + the declared
    mode table + the manifest invariant (D-461)."""
    from primeqa.core.authz import AuthorizationError, Tier, authorize

    allow, reason = authorize(subject, Tier.MEMBER)
    if not allow:
        raise AuthorizationError(reason)
    manifest_id = build_manifest_for_claim_set(
        session, claim_set_id=claim_set_id, scheme=scheme,
        stabilisation=stabilisation, auth=auth, sf_client=sf_client,
        org_env_snapshot_id=org_env_snapshot_id)
    job_id = enqueue_manifest_job(session, manifest_id)
    from primeqa.browser_worker.audit import record_event
    if isinstance(subject, dict):
        user_id = subject.get("user_id") or subject.get("id")
        subj_tenant = subject.get("tenant_id")
    else:
        user_id = getattr(subject, "user_id", None)
        subj_tenant = getattr(subject, "tenant_id", None)
    record_event(session, action="ui.run_enqueued",
                 details={"claim_set_id": str(claim_set_id),
                          "manifest_id": manifest_id,
                          "job_id": str(job_id)},
                 user_id=user_id, tenant_id=subj_tenant)
    return {"manifest_id": manifest_id, "job_id": str(job_id),
            "authorized": reason}


def enqueue_manifest_job(session: Session, manifest_id: str) -> str:
    """The browser-plane enqueue — consults RECIPE_MODES (D6/SAD A3)
    before any job exists: browser dispatch is authorized by the
    DECLARED mode table, never by kind-name inference. Raises
    PolicyError for an undeclared kind via mode_for."""
    from primeqa.browser_worker.manifest import enqueue_for_manifest

    mode = mode_for(_UI_INSPECTION_KIND)
    if mode != READ_ONLY:
        raise ManifestBuildError(
            f"{_UI_INSPECTION_KIND} declares mode {mode!r}; browser-plane "
            "dispatch is READ_ONLY-only")
    return enqueue_for_manifest(session, manifest_id)
