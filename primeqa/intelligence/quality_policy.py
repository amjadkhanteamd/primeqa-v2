"""Step 5 — the Release Quality Policy (LLD_STEP_5_QUALITY_POLICY §a, §b, §e).

The policy is a CLOSED declarative vocabulary: a rule is
``axis × condition × effect × scope`` (+ one numeric ``parameter`` where the
condition declares one). No expressions, no connectives — the F8 discipline
applied to policy. Every word is CHECKed at the table and refused here before
the table. The engine (:func:`evaluate`) is PURE: it reads the policy and the
assembled evidence (:mod:`primeqa.intelligence.quality_evidence`) and emits the
recommendation with ONE evidence line per axis naming the triggered rule and
its effect. Gate logic lives here and nowhere else — templates and routes
render this output.

Ruling 2: BLOCK's recommendation word splits by cause — a graded BLOCK is
``no_go``; an ungraded BLOCK (or any REVIEW) is ``cannot_determine`` (D9:
unknown blocks GO and CONDITIONAL GO; D-482: unknown alone never condemns).

The store section reads and writes the tenant tables (``quality_policies``,
``quality_policy_rules``, ``quality_waivers``); the CLI is the v1 authoring
surface (ruling 3: a human activates; the seed is a DRAFT).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# the vocabulary (mirrored by the migration's CHECKs)
# --------------------------------------------------------------------------

AXES = ("functional", "conformance", "regressions", "waivers", "human_reviews",
        "environments", "grading")
EVIDENCE_AXES = AXES[:6]          # the six lines of the card; grading is the seventh, "Nothing ungraded"
EFFECTS = ("ALLOW", "CONDITIONAL", "REVIEW", "BLOCK")
_EFFECT_RANK = {e: i for i, e in enumerate(EFFECTS)}
SCOPES = ("release", "item")

# axis -> {condition: (reads-a-parameter?, sentence)}
CONDITIONS = {
    "functional": {
        "failure_present": (False, "a counted latest run failed or errored on an approved claim in scope"),
        "pass_rate_below": (True, "the pass rate over counted runs is below the parameter"),
    },
    "conformance": {
        "level_a_failed": (False, "a FAIL verdict on a level-A criterion of the ACTIVE map set"),
        "level_aa_failed": (False, "a FAIL verdict on a level-AA criterion of the ACTIVE map set"),
        "level_aaa_failed": (False, "a FAIL verdict on a level-AAA criterion of the ACTIVE map set"),
        "not_determined_present": (False, "a NOT_DETERMINED verdict on the latest processing run"),
    },
    "regressions": {
        "new_fail_present": (False, "a NEW_FAIL transition with drift subtracted, or a functional pass→fail"),
        "tool_drift_only": (False, "the latest comparison moved on tool pins and nothing else"),
        "not_comparable_present": (False, "a NOT_COMPARABLE pair in the latest comparison"),
    },
    "waivers": {
        "active_waiver_covers_item": (False, "an unexpired, unrevoked waiver names the item"),
    },
    "human_reviews": {
        "needs_human_pending": (False, "a NEEDS_HUMAN verdict or HUMAN_REVIEW member with no active waiver"),
    },
    "environments": {
        "environment_drift": (False, "a counted run reads STALE on a target"),
        "scope_not_current": (False, "an item NEVER_RUN or CANNOT_DETERMINE on a target"),
        "production_target_ungraded_on_metadata": (
            False, "a production read-only target holds no current run for an admitted inspection check"),
    },
    "grading": {
        "ungraded_present": (False, "an input the engine cannot grade"),
    },
}
ADMITTED_PAIRS = frozenset((a, c) for a, cs in CONDITIONS.items() for c in cs)
# the conditions whose BLOCK is GRADED (→ no_go); ungraded_present's BLOCK → cannot_determine
GRADED_CONDITIONS = frozenset(c for a, cs in CONDITIONS.items() for c in cs if c != "ungraded_present")

RECOMMENDATIONS = ("go", "conditional_go", "no_go", "cannot_determine")
_CONFIDENCE = {"no_go": 0.90, "cannot_determine": 0.0, "conditional_go": 0.75, "go": 0.95}


class PolicyError(ValueError):
    """A refused policy word or act — the message names the cause."""


@dataclass(frozen=True)
class Rule:
    position: int
    axis: str
    condition: str
    effect: str
    scope: str = "release"
    parameter: Optional[float] = None
    note: str = ""

    def __post_init__(self):
        if self.axis not in AXES:
            raise PolicyError(f"unknown axis {self.axis!r}")
        if (self.axis, self.condition) not in ADMITTED_PAIRS:
            raise PolicyError(f"condition {self.condition!r} is not admitted on axis {self.axis!r}")
        if self.effect not in EFFECTS:
            raise PolicyError(f"unknown effect {self.effect!r}")
        if self.scope not in SCOPES:
            raise PolicyError(f"unknown scope {self.scope!r}")
        takes_param = CONDITIONS[self.axis][self.condition][0]
        if takes_param and self.parameter is None:
            raise PolicyError(f"condition {self.condition!r} declares a parameter; none given")
        if not takes_param and self.parameter is not None:
            raise PolicyError(f"condition {self.condition!r} takes no parameter")
        if takes_param and not (0 <= float(self.parameter) <= 100):
            raise PolicyError(f"parameter {self.parameter!r} out of range for {self.condition!r}")

    @property
    def words(self) -> str:
        p = f" ({self.parameter:g})" if self.parameter is not None else ""
        return f"{self.axis} · {self.condition}{p} → {self.effect}" + (" (item)" if self.scope == "item" else "")

    def as_dict(self) -> dict:
        return {"position": self.position, "axis": self.axis, "condition": self.condition,
                "parameter": self.parameter, "effect": self.effect, "scope": self.scope, "note": self.note}


@dataclass(frozen=True)
class Policy:
    id: Optional[str]
    name: str
    version: int
    status: str
    rules: tuple = ()
    note: str = ""
    first_used_at: Optional[str] = None
    created_by: Optional[int] = None
    activated_by: Optional[int] = None
    activated_at: Optional[str] = None

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "version": self.version, "status": self.status,
                "note": self.note, "first_used_at": self.first_used_at,
                "rules": [r.as_dict() for r in self.rules]}

    @property
    def label(self) -> str:
        return f"{self.name} v{self.version}"


# --------------------------------------------------------------------------
# the engine (pure)
# --------------------------------------------------------------------------

def _observations(ev: dict) -> dict:
    """Evidence → the closed set of condition booleans (+ the pass rate). The
    evidence shape is :func:`primeqa.intelligence.quality_evidence.assemble`'s."""
    f, c, r = ev.get("functional") or {}, ev.get("conformance") or {}, ev.get("regressions") or {}
    w, h, e, g = (ev.get("waivers") or {}, ev.get("human_reviews") or {},
                  ev.get("environments") or {}, ev.get("grading") or {})
    lv = c.get("failed_by_level") or {}
    return {
        "failure_present": (f.get("failed") or 0) + (f.get("errored") or 0) > 0,
        "pass_rate": f.get("pass_rate") if (f.get("counted") or 0) > 0 else None,
        "level_a_failed": (lv.get("A") or 0) > 0,
        "level_aa_failed": (lv.get("AA") or 0) > 0,
        "level_aaa_failed": (lv.get("AAA") or 0) > 0,
        "not_determined_present": (c.get("not_determined") or 0) > 0,
        "new_fail_present": (r.get("new_fail") or 0) + (r.get("functional_new_fail") or 0) > 0,
        "tool_drift_only": bool(r.get("tool_drift_only")),
        "not_comparable_present": (r.get("not_comparable") or 0) > 0,
        "active_waiver_covers_item": (w.get("applied") or 0) > 0,
        "needs_human_pending": (h.get("pending") or 0) > 0,
        "environment_drift": (e.get("drift") or 0) > 0,
        "scope_not_current": (e.get("non_current") or 0) > 0,
        "production_target_ungraded_on_metadata": (e.get("production_ungraded_on_metadata") or 0) > 0,
        "ungraded_present": (g.get("count") or 0) > 0,
    }


def _holds(rule: Rule, obs: dict) -> bool:
    if rule.condition == "pass_rate_below":
        return obs["pass_rate"] is not None and float(obs["pass_rate"]) < float(rule.parameter)
    return bool(obs.get(rule.condition))


def evaluate(policy: Policy, evidence: dict, *, now: Optional[datetime] = None) -> dict:
    """The policy over the evidence → ``{recommendation, confidence, effect,
    evidence_lines[7], triggered_rules[], ungraded[], policy{}, plan_id,
    observations{}, evaluated_at}``. Deterministic; no I/O."""
    if policy.status != "active":
        raise PolicyError(f"policy {policy.label} is {policy.status}, not active — activate one before grading")
    obs = _observations(evidence)
    triggered = [r for r in policy.rules if _holds(r, obs)]
    lines, effects = [], []
    for axis in AXES:
        ax = evidence.get(axis) or {}
        on_axis = [r for r in triggered if r.axis == axis]
        release_eff = [r.effect for r in on_axis if r.scope == "release"]
        line_eff = max(release_eff, key=_EFFECT_RANK.get) if release_eff else None
        lines.append({
            "axis": axis,
            "observed": ax.get("observed") or ("Nothing ungraded." if axis == "grading" else "no evidence"),
            "rules": [{"position": r.position, "words": r.words, "effect": r.effect, "scope": r.scope}
                      for r in on_axis],
            "rule": (f"rule {on_axis[0].position}: {on_axis[0].words}" if len(on_axis) == 1
                     else (", ".join(f"rule {r.position}" for r in on_axis) if on_axis else "no rule triggered")),
            "effect": line_eff or ("ALLOW" if [r for r in on_axis if r.scope == "item"] else None),
            "items": ax.get("items") or [],
        })
        effects.extend((r.effect, r.condition) for r in on_axis if r.scope == "release")
    graded_block = any(e == "BLOCK" and c in GRADED_CONDITIONS for e, c in effects)
    ungraded_block = any(e == "BLOCK" and c not in GRADED_CONDITIONS for e, c in effects)
    review = any(e == "REVIEW" for e, _ in effects)
    conditional = any(e == "CONDITIONAL" for e, _ in effects)
    if graded_block:
        rec, why = "no_go", "a graded BLOCK"
    elif ungraded_block or review:
        rec, why = "cannot_determine", ("an ungraded BLOCK" if ungraded_block else "a REVIEW")
    elif conditional:
        rec, why = "conditional_go", "a CONDITIONAL"
    else:
        rec, why = "go", "no rule blocked, reviewed or conditioned"
    overall = max((e for e, _ in effects), key=_EFFECT_RANK.get) if effects else "ALLOW"
    return {
        "recommendation": rec, "confidence": _CONFIDENCE[rec], "effect": overall, "because": why,
        "evidence_lines": lines,
        "triggered_rules": [r.as_dict() for r in triggered],
        "ungraded": (evidence.get("grading") or {}).get("items") or [],
        "policy": {"id": policy.id, "name": policy.name, "version": policy.version, "label": policy.label},
        "plan_id": (evidence.get("plan") or {}).get("id"),
        "plan": evidence.get("plan"),
        "observations": obs,
        "evaluated_at": (now or datetime.now(timezone.utc)).isoformat(),
    }


# --------------------------------------------------------------------------
# the store (tenant session)
# --------------------------------------------------------------------------

def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _rules_for(session: Session, policy_id: str) -> tuple:
    rows = session.execute(text("""
        SELECT position, axis, condition, parameter, effect, scope, note
        FROM quality_policy_rules WHERE policy_id = CAST(:p AS uuid) ORDER BY position
    """), {"p": str(policy_id)}).fetchall()
    return tuple(Rule(position=r[0], axis=r[1], condition=r[2],
                      parameter=(float(r[3]) if r[3] is not None else None),
                      effect=r[4], scope=r[5], note=r[6] or "") for r in rows)


def _policy_from_row(session: Session, r) -> Policy:
    return Policy(id=str(r[0]), name=r[1], version=r[2], status=r[3], note=r[4] or "",
                  first_used_at=_iso(r[5]), created_by=r[6], activated_by=r[7], activated_at=_iso(r[8]),
                  rules=_rules_for(session, str(r[0])))


_SELECT = ("SELECT id, name, version, status, note, first_used_at, created_by, activated_by, activated_at "
           "FROM quality_policies")


def get_policy(session: Session, policy_id: str) -> Optional[Policy]:
    r = session.execute(text(_SELECT + " WHERE id = CAST(:p AS uuid)"), {"p": str(policy_id)}).fetchone()
    return _policy_from_row(session, r) if r else None


def active_policy(session: Session) -> Optional[Policy]:
    r = session.execute(text(_SELECT + " WHERE status = 'active'")).fetchone()
    return _policy_from_row(session, r) if r else None


def list_policies(session: Session) -> list[Policy]:
    rows = session.execute(text(_SELECT + " ORDER BY name, version DESC")).fetchall()
    return [_policy_from_row(session, r) for r in rows]


def _audit(session: Session, *, tenant_id: Optional[int], user_id: Optional[int], action: str, details: dict) -> None:
    if tenant_id is None:
        return
    try:
        with session.begin_nested():          # the tenant-only harness has no public.activity_log
            session.execute(text("""
                INSERT INTO public.activity_log (tenant_id, user_id, action, entity_type, entity_id, details)
                VALUES (:t, :u, :a, 'quality_policy', NULL, CAST(:d AS JSONB))
            """), {"t": int(tenant_id), "u": user_id, "a": action, "d": json.dumps(details, default=str)})
    except Exception as exc:  # noqa: BLE001
        log.warning("quality policy audit row not written (%s): %s", action, exc)


def create_version(session: Session, *, name: str, rules, note: str = "", created_by: Optional[int],
                   tenant_id: Optional[int] = None) -> Policy:
    """A new DRAFT version of ``name`` (max version + 1) with ``rules``
    (validated here before the table)."""
    rules = tuple(r if isinstance(r, Rule) else Rule(**r) for r in rules)
    if not rules:
        raise PolicyError("a policy needs at least one rule")
    if len({r.position for r in rules}) != len(rules):
        raise PolicyError("rule positions must be distinct")
    version = session.execute(text(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM quality_policies WHERE name = :n"), {"n": name}).scalar()
    pid = session.execute(text("""
        INSERT INTO quality_policies (name, version, status, note, created_by)
        VALUES (:n, :v, 'draft', :note, :c) RETURNING CAST(id AS text)
    """), {"n": name, "v": version, "note": note, "c": created_by}).scalar()
    for r in rules:
        session.execute(text("""
            INSERT INTO quality_policy_rules (policy_id, position, axis, condition, parameter, effect, scope, note)
            VALUES (CAST(:p AS uuid), :pos, :a, :c, :param, :e, :s, :n)
        """), {"p": pid, "pos": r.position, "a": r.axis, "c": r.condition, "param": r.parameter,
               "e": r.effect, "s": r.scope, "n": r.note})
    _audit(session, tenant_id=tenant_id, user_id=created_by, action="quality_policy.create",
           details={"policy_id": pid, "name": name, "version": version, "rules": len(rules)})
    session.flush()
    return get_policy(session, pid)


def activate(session: Session, *, policy_id: str, user_id: int, tenant_id: Optional[int] = None) -> Policy:
    """draft → active; the previously active version retires (both audited
    with the real actor). Ruling 3: a human act, never the migration's."""
    p = get_policy(session, policy_id)
    if p is None:
        raise PolicyError(f"no policy {policy_id}")
    if p.status != "draft":
        raise PolicyError(f"policy {p.label} is {p.status}; only a draft activates")
    prev = active_policy(session)
    now = datetime.now(timezone.utc)
    if prev is not None:
        session.execute(text("""
            UPDATE quality_policies SET status = 'retired', retired_by = :u, retired_at = :t
            WHERE id = CAST(:p AS uuid)
        """), {"u": user_id, "t": now, "p": prev.id})
        _audit(session, tenant_id=tenant_id, user_id=user_id, action="quality_policy.retire",
               details={"policy_id": prev.id, "name": prev.name, "version": prev.version,
                        "succeeded_by": p.id})
    session.execute(text("""
        UPDATE quality_policies SET status = 'active', activated_by = :u, activated_at = :t
        WHERE id = CAST(:p AS uuid)
    """), {"u": user_id, "t": now, "p": p.id})
    _audit(session, tenant_id=tenant_id, user_id=user_id, action="quality_policy.activate",
           details={"policy_id": p.id, "name": p.name, "version": p.version,
                    "retired": (prev.id if prev else None)})
    session.flush()
    return get_policy(session, p.id)


def retire(session: Session, *, policy_id: str, user_id: int, tenant_id: Optional[int] = None) -> Policy:
    p = get_policy(session, policy_id)
    if p is None or p.status != "active":
        raise PolicyError(f"policy {policy_id} is not active")
    session.execute(text("""
        UPDATE quality_policies SET status = 'retired', retired_by = :u, retired_at = :t
        WHERE id = CAST(:p AS uuid)
    """), {"u": user_id, "t": datetime.now(timezone.utc), "p": p.id})
    _audit(session, tenant_id=tenant_id, user_id=user_id, action="quality_policy.retire",
           details={"policy_id": p.id, "name": p.name, "version": p.version, "succeeded_by": None})
    session.flush()
    return get_policy(session, p.id)


def mark_used(session: Session, *, policy_id: str) -> None:
    """The FIRST decision that grades under a policy freezes it (idempotent)."""
    session.execute(text("""
        UPDATE quality_policies SET first_used_at = now()
        WHERE id = CAST(:p AS uuid) AND first_used_at IS NULL
    """), {"p": str(policy_id)})


# --- waivers (§c) ------------------------------------------------------------

WAIVER_ITEM_KINDS = ("claim", "rule", "surface")
WAIVER_AXES = ("functional", "conformance", "human_reviews")


def record_waiver(session: Session, *, release_id: Optional[int], item_kind: str, item_ref: str, axis: str,
                  reviewer_user_id: int, reason: str, expires_at: datetime, created_by: int,
                  tenant_id: Optional[int] = None) -> dict:
    """Never auto-created: a named human accepting a known state, with an
    expiry (ruling 4: no expiry, no waiver)."""
    if item_kind not in WAIVER_ITEM_KINDS:
        raise PolicyError(f"unknown waiver item kind {item_kind!r}")
    if axis not in WAIVER_AXES:
        raise PolicyError(f"a waiver applies on {WAIVER_AXES}, not {axis!r}")
    if not (item_ref or "").strip():
        raise PolicyError("a waiver names its item")
    if not (reason or "").strip():
        raise PolicyError("a waiver carries its reason")
    if expires_at is None:
        raise PolicyError("no expiry, no waiver")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise PolicyError("a waiver's expiry lies in the future")
    wid = session.execute(text("""
        INSERT INTO quality_waivers (release_id, item_kind, item_ref, axis, reviewer_user_id, reason, expires_at, created_by)
        VALUES (:r, :k, :i, :a, :rev, :why, :x, :c) RETURNING CAST(id AS text)
    """), {"r": release_id, "k": item_kind, "i": item_ref.strip(), "a": axis, "rev": reviewer_user_id,
           "why": reason.strip(), "x": expires_at, "c": created_by}).scalar()
    _audit(session, tenant_id=tenant_id, user_id=created_by, action="quality_waiver.record",
           details={"waiver_id": wid, "release_id": release_id, "item_kind": item_kind, "item_ref": item_ref,
                    "axis": axis, "reviewer_user_id": reviewer_user_id, "expires_at": expires_at.isoformat()})
    session.flush()
    return get_waiver(session, wid)


def revoke_waiver(session: Session, *, waiver_id: str, user_id: int, reason: str = "",
                  tenant_id: Optional[int] = None) -> dict:
    w = get_waiver(session, waiver_id)
    if w is None:
        raise PolicyError(f"no waiver {waiver_id}")
    if w["revoked_at"] is not None:
        raise PolicyError("that waiver is already revoked")
    session.execute(text("""
        UPDATE quality_waivers SET revoked_by = :u, revoked_at = :t, revocation_reason = :r
        WHERE id = CAST(:w AS uuid)
    """), {"u": user_id, "t": datetime.now(timezone.utc), "r": reason or None, "w": str(waiver_id)})
    _audit(session, tenant_id=tenant_id, user_id=user_id, action="quality_waiver.revoke",
           details={"waiver_id": str(waiver_id), "reason": reason})
    session.flush()
    return get_waiver(session, waiver_id)


_WAIVER_SELECT = """
    SELECT CAST(id AS text), release_id, item_kind, item_ref, axis, reviewer_user_id, reason, expires_at,
           created_by, created_at, revoked_by, revoked_at, revocation_reason
    FROM quality_waivers
"""


def _waiver_row(r, now: datetime) -> dict:
    d = {"id": r[0], "release_id": r[1], "item_kind": r[2], "item_ref": r[3], "axis": r[4],
         "reviewer_user_id": r[5], "reason": r[6], "expires_at": r[7], "created_by": r[8], "created_at": r[9],
         "revoked_by": r[10], "revoked_at": r[11], "revocation_reason": r[12]}
    d["state"] = ("revoked" if d["revoked_at"] is not None
                  else ("expired" if d["expires_at"] <= now else "active"))
    for k in ("expires_at", "created_at", "revoked_at"):
        d[k] = _iso(d[k])
    return d


def get_waiver(session: Session, waiver_id: str, *, now: Optional[datetime] = None) -> Optional[dict]:
    r = session.execute(text(_WAIVER_SELECT + " WHERE id = CAST(:w AS uuid)"), {"w": str(waiver_id)}).fetchone()
    return _waiver_row(r, now or datetime.now(timezone.utc)) if r else None


def waivers_for_release(session: Session, release_id: Optional[int], *, now: Optional[datetime] = None) -> list[dict]:
    """The release's waivers plus the tenant-wide ones (release_id NULL), every
    state (the evidence line counts active ones and names the expired)."""
    now = now or datetime.now(timezone.utc)
    rows = session.execute(text(_WAIVER_SELECT + """
        WHERE release_id = :r OR release_id IS NULL ORDER BY created_at DESC
    """), {"r": release_id}).fetchall()
    return [_waiver_row(r, now) for r in rows]


# --------------------------------------------------------------------------
# the CLI — v1 authoring (ruling 3; §f: authoring is Settings-side, CLI in v1)
# --------------------------------------------------------------------------

def _with_tenant(tenant_id: int, fn):
    from primeqa.semantic.connection import get_tenant_connection
    with get_tenant_connection(tenant_id) as conn:
        s = Session(bind=conn)
        try:
            out = fn(s)
            s.flush()
            return out
        finally:
            s.close()


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m primeqa.intelligence.quality_policy",
                                     description="Release Quality Policy — v1 authoring (Step 5)")
    parser.add_argument("--tenant-id", type=int, required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="every policy version with its rules; the active one first")
    a = sub.add_parser("activate", help="draft → active (retires the previous active); an audited human act")
    a.add_argument("policy_id"); a.add_argument("--user-id", type=int, required=True)
    r = sub.add_parser("retire", help="active → retired")
    r.add_argument("policy_id"); r.add_argument("--user-id", type=int, required=True)
    n = sub.add_parser("new-version", help="a new DRAFT version from a JSON rules file")
    n.add_argument("--name", required=True); n.add_argument("--rules", required=True, help="JSON list of rules")
    n.add_argument("--note", default=""); n.add_argument("--user-id", type=int, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "show":
        out = _with_tenant(args.tenant_id, lambda s: [p.as_dict() for p in
                                                      sorted(list_policies(s), key=lambda p: p.status != "active")])
    elif args.cmd == "activate":
        out = _with_tenant(args.tenant_id, lambda s: activate(
            s, policy_id=args.policy_id, user_id=args.user_id, tenant_id=args.tenant_id).as_dict())
    elif args.cmd == "retire":
        out = _with_tenant(args.tenant_id, lambda s: retire(
            s, policy_id=args.policy_id, user_id=args.user_id, tenant_id=args.tenant_id).as_dict())
    else:
        with open(args.rules) as fh:
            rules = json.load(fh)
        out = _with_tenant(args.tenant_id, lambda s: create_version(
            s, name=args.name, rules=rules, note=args.note, created_by=args.user_id,
            tenant_id=args.tenant_id).as_dict())
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
