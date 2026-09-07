"""Step 1 — the requirement IDENTITY (LLD_STEP_1_PROVENANCE §a, §b).

Fork 1, ratified lean B: the link key is FIRST-CLASS IDENTITY.
``(external_system, external_key)`` names a requirement; a
``requirements`` row is optional DECORATION of that identity, and
``requirements.id`` is a row id that is never displayed.

The TA invariants this module enforces:

1. **immutable once established** — the key columns are refused on
   UPDATE by a table trigger (migration ``20260908_0010``); nothing here
   ever re-keys.
2. **unique within tenant scope** — the identity PK is per tenant schema;
   the decorating row's key is unique among LIVE rows (migration 072).
3. **origin represented separately** — ``origin`` lives on the identity,
   never on a row (31 of the 42 identities on tenant 1 have no row).
4. **display id is not identity** — :func:`display_key` returns the key
   verbatim; no ``#N`` form exists.
5. **a missing decorated record is a referential GAP** — :func:`list_identities`
   reports ``decorated=False``, never "missing identity".
6. **never silently re-key** — :func:`establish` inserts or leaves alone
   (``ON CONFLICT DO NOTHING``); the backfill asserts key-set equality
   before it commits.

Origin is classified BY EVIDENCE and never guessed
(:func:`classify_origin`, pure). An identity the evidence cannot place
is ``CANNOT_CLASSIFY`` — a state the operator resolves, not a default.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

log = logging.getLogger(__name__)

CLASSIFIER_VERSION = "origin@v1"

JIRA = "jira"
MANUAL = "manual"
FIXTURE = "fixture"
PROBE = "probe"
CANNOT_CLASSIFY = "CANNOT_CLASSIFY"
ORIGINS = (JIRA, MANUAL, FIXTURE, PROBE, CANNOT_CLASSIFY)

#: Origins hidden from every DEFAULT view (§d). Gaps are NEVER hidden.
HIDDEN_BY_DEFAULT = (FIXTURE, PROBE)

#: Declared fixture prefixes — the keys minted by the lever-ladder and
#: live-eval campaigns whose scripts were never committed (scratch only).
#: This is DECLARED DATA, each family cited to the entry that records it;
#: it is not a heuristic about shape.
FIXTURE_PREFIXES = {
    "REQ-ARC-": "D-333",
    "REQ-D299-": "D-299.2",
    "REQ-D300-": "D-300.2",
    "REQ-L7A-": "D-302",
    "REQ-L7B-": "D-304.1",
    "REQ-L7C-": "D-305.1",
    "REQ-L7D-": "D-305.1",
    "REQ-L7E-": "D-305.1",
    "REQ-L7F-": "D-428",
    "REQ-L7G-": "D-428",
}

#: EXPLICIT origin overrides — ruling R-probe (AK, 2026-09-07). Prose in a
#: summary is not evidence, so an identity that the evidence rules place
#: elsewhere lands here only by a named decision carrying its reason.
_OVERRIDES = {
    "req-322": {
        "origin": PROBE,
        "reason": "the PLS TA probe-lifecycle campaign — a probe, not a "
                  "customer requirement; the row's manual source is an "
                  "artefact of how probes were authored",
        "cited": "D-457",
    },
}

_REQ_N = re.compile(r"^req-(\d+)$")


@dataclass(frozen=True)
class OriginClassification:
    """The verdict plus the RULE that produced it — never a bare label."""
    origin: str
    evidence: dict

    def as_row(self, *, established_by: str) -> dict:
        return {"origin": self.origin, "origin_evidence": self.evidence,
                "established_by": established_by,
                "classifier_version": CLASSIFIER_VERSION}


def display_key(external_key: str) -> str:
    """Invariant 4: the display form IS the key. One format everywhere.

    Exists as a named function so no surface re-invents ``#282`` — the
    grep for a second display derivation lands here."""
    return external_key


def key_for_requirement_row(row) -> str:
    """The identity a ``requirements`` row decorates.

    ``external_key`` when the row carries it (post-072); otherwise the
    pre-Step-1 derivation, byte-identical — a test database that predates
    072 still resolves. Accepts an ORM row or a mapping."""
    get = (row.get if isinstance(row, dict) else lambda k, d=None: getattr(row, k, d))
    key = get("external_key")
    if key:
        return key
    return get("jira_key") or f"req-{get('id')}"


def classify_origin(external_key: str, *, requirement_row=None) -> OriginClassification:
    """PURE. The evidence rules, in order (§b):

    * an EXPLICIT recorded override (ruling R-probe) — reason + citation;
    * a declared fixture prefix → ``fixture``;
    * a LIVE requirements row: ``source='jira'`` with a ``jira_key`` →
      ``jira``; ``source='manual'`` → ``manual``;
    * else ``CANNOT_CLASSIFY``.

    Ruling R-jira-gap: a Jira-SHAPED key with no row classifies
    ``CANNOT_CLASSIFY`` — a Jira key is a fact of the ROW, not of the
    string. A re-import decorates the identity and sets ``jira`` then."""
    override = _OVERRIDES.get(external_key)
    if override:
        return OriginClassification(override["origin"], {
            "rule": "override", "reason": override["reason"],
            "cited": override["cited"]})
    for prefix, cited in FIXTURE_PREFIXES.items():
        if external_key.startswith(prefix):
            return OriginClassification(FIXTURE, {
                "rule": "fixture_prefix", "prefix": prefix, "cited": cited})
    if requirement_row is not None:
        get = (requirement_row.get if isinstance(requirement_row, dict)
               else lambda k, d=None: getattr(requirement_row, k, d))
        source, jira_key = get("source"), get("jira_key")
        rid = get("id")
        if source == JIRA and jira_key:
            return OriginClassification(JIRA, {
                "rule": "requirement_row", "requirement_id": rid,
                "source": JIRA, "jira_key": jira_key})
        if source == MANUAL:
            return OriginClassification(MANUAL, {
                "rule": "requirement_row", "requirement_id": rid,
                "source": MANUAL})
    return OriginClassification(CANNOT_CLASSIFY, {"rule": "none"})


# ---------------------------------------------------------------------------
# Establishment (impure) — insert-or-leave-alone. NEVER an update.
# ---------------------------------------------------------------------------

def establish(conn, external_key: str, *, origin: str, evidence: dict,
              established_by: str, external_system: str = JIRA) -> bool:
    """Establish the identity if it does not exist. Returns True when a
    row was written. ``ON CONFLICT DO NOTHING``: an established identity
    is never re-classified behind the operator's back (invariants 1, 6)."""
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}")
    res = conn.execute(text(
        "INSERT INTO requirement_identities "
        "(external_system, external_key, origin, origin_evidence, "
        " established_by, classifier_version) "
        "VALUES (CAST(:sys AS external_system), :key, :origin, "
        "        CAST(:ev AS JSONB), :by, :ver) "
        "ON CONFLICT (external_system, external_key) DO NOTHING"),
        {"sys": external_system, "key": external_key, "origin": origin,
         "ev": json.dumps(evidence), "by": established_by,
         "ver": CLASSIFIER_VERSION})
    return bool(res.rowcount)


def establish_for_key(conn, external_key: str, *, requirement_row=None,
                      established_by: str, declared_origin: Optional[str] = None,
                      external_system: str = JIRA) -> bool:
    """Establish an identity for a key seen at runtime (an S3 link write, a
    Jira import, a manual create). A caller-DECLARED origin is honoured and
    recorded as such; otherwise the evidence rules decide. An unplaceable
    key lands ``CANNOT_CLASSIFY`` LOUDLY — never silently."""
    if declared_origin:
        if declared_origin not in ORIGINS:
            raise ValueError(f"unknown declared origin {declared_origin!r}")
        cls = OriginClassification(declared_origin, {
            "rule": "declared", "by": established_by})
    else:
        cls = classify_origin(external_key, requirement_row=requirement_row)
    if cls.origin == CANNOT_CLASSIFY:
        log.warning("requirement identity %r established as CANNOT_CLASSIFY "
                    "(no evidence places it) — established_by=%s",
                    external_key, established_by)
    return establish(conn, external_key, origin=cls.origin,
                     evidence=cls.evidence, established_by=established_by,
                     external_system=external_system)


def origins_for_keys(conn, keys) -> dict:
    """``{external_key: {origin, evidence, established_by}}`` for the given
    keys. Missing keys are simply absent — a caller renders an
    unestablished identity as ``CANNOT_CLASSIFY`` without inventing a row."""
    keys = [k for k in (keys or []) if k]
    if not keys:
        return {}
    rows = conn.execute(text(
        "SELECT external_key, origin, origin_evidence, established_by "
        "FROM requirement_identities WHERE external_key = ANY(:keys)"),
        {"keys": list(keys)}).mappings().all()
    return {r["external_key"]: {"origin": r["origin"],
                                "evidence": r["origin_evidence"],
                                "established_by": r["established_by"]}
            for r in rows}


_IDENTITY_CENSUS_SQL = (
    "SELECT l.external_key AS key, "
    "       COUNT(DISTINCT l.test_id) AS claims, "
    "       COUNT(DISTINCT l.test_id) FILTER "
    "         (WHERE c.status = 'approved') AS approved_claims "
    "FROM test_requirement_links l "
    "LEFT JOIN test_claims c ON c.test_id = l.test_id AND c.valid_to IS NULL "
    "WHERE l.link_kind IN ('generated_from', 'verifies') "
    "GROUP BY l.external_key")


def link_key_census(conn) -> dict:
    """``{key: {claims, approved_claims}}`` over the link table — the
    identity population as the substrate holds it."""
    return {r["key"]: {"claims": int(r["claims"]),
                       "approved_claims": int(r["approved_claims"] or 0)}
            for r in conn.execute(text(_IDENTITY_CENSUS_SQL)).mappings().all()}
