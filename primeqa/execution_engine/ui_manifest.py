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
import re
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
            "playwright_version": _playwright_pin()}


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

    payload = {
        "claim_set_id": str(claim_set_id),
        "excluded_revoked": excluded_revoked,
        "surfaces": [surfaces[k] for k in sorted(surfaces)],
        "pins": {
            **_engine_pins(session),
            "catalogue_release_id": data["catalogue_release_id"],
            "catalogue_content_hash": session.execute(text(
                "SELECT content_hash FROM s5_catalogue_releases "
                "WHERE id = :r"),
                {"r": data["catalogue_release_id"]}).scalar_one().strip(),
        },
        "stabilisation": stabilisation or {},
        "execution": {"mode": execution_mode},
    }
    if auth is not None:
        payload["auth"] = auth
    return create_manifest(session, payload)


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
