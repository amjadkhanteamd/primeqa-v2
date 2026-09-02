"""Customer rule authoring — tenant store writes
(LLD_PHASE5_AUTHORING §f/§g/§i; D-471).

The authoring path: prose guideline -> an LLM (or a human) drafts
STRICTLY in the vocabulary -> ``cust_grammar.validate`` refuses or
validates BEFORE any human sees it as a rule -> human reviews/approves
-> the s5 lifecycle SHAPE, unchanged (§i): DRAFT -> REVIEW -> APPROVED
-> VERSIONED -> ACTIVE -> RETIRED, immutable when ACTIVE, real-actor
audit on every transition. The LLM never decides a verdict and never
authors an ACTIVE rule.

Refusal is a feature (§f): every guideline outcome — drafted or refused
— is a ledger row, and the refusal carries its class, the reason in the
customer's terms, and the nearest expressible partial. The three
reviewer-judged classes (`not_observable`, `belongs_to_public_catalogue`,
`ambiguous_guideline`) are recorded through ``record_refusal`` directly;
the validator assigns the three mechanical ones.

Authority: these are the TENANT's rules — ADMIN tier gates every write
(superadmin passes by rank, as everywhere). All tables live in the
tenant schema; the session's search_path scopes every statement.

THE RATIFICATION CONFLICT GATE (§h), honestly sized: contradiction
against another ACTIVE **custom** rule on the same profile criterion is
detected MECHANICALLY (the negation pairs of a closed vocabulary make
that decidable); contradiction against an ACTIVE **catalogue** rule is
not mechanically decidable (the engine's semantics are not in this
vocabulary), so APPROVED requires the reviewer's explicit
``reviewed_no_conflict=True`` and records which catalogue rules share
the bound WCAG criterion, so the confirmation is informed, never a
rubber stamp.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from primeqa.core.authz import Tier, rank
from primeqa.knowledge.cust_grammar import (
    NEGATIVE_FORMS, Refusal, ValidatedRule)

_TRANSITIONS = {
    "DRAFT": "REVIEW",
    "REVIEW": "APPROVED",
    "APPROVED": "VERSIONED",
    "VERSIONED": "ACTIVE",
    "ACTIVE": "RETIRED",
}

_NEGATION_PAIRS = {("equals", "not_equals"), ("not_equals", "equals"),
                   ("member_of", "not_member_of"),
                   ("not_member_of", "member_of"),
                   ("present", "absent"), ("absent", "present")}


class AuthoringError(RuntimeError):
    """An illegal authoring operation — never a silent no-op."""


def _require_admin(actor_role: str) -> None:
    if rank(actor_role) < Tier.ADMIN:
        raise AuthoringError(
            "custom-rule authoring writes are tenant-admin actions: "
            "admin or superadmin only")


def _audit(session, actor_tenant_id: int, actor_user_id: int, action: str,
           rule_id: str | None, details: dict) -> None:
    from primeqa.core.repository import ActivityLogRepository
    if rule_id is not None:
        details = {**details, "rule_id": rule_id}
    ActivityLogRepository(session).log_activity(
        actor_tenant_id, actor_user_id, action, "cust_rule", None, details)


# ---------------------------------------------------------------------------
# Minting + the ledger
# ---------------------------------------------------------------------------

def next_cust_rule_id(session) -> str:
    row = session.execute(text(
        "SELECT MAX(rule_id) FROM cust_rules")).scalar()
    n = int(row.rsplit("-", 1)[1]) + 1 if row else 1
    if n > 99999:
        raise AuthoringError("PLM-CUST namespace exhausted (99,999)")
    return f"PLM-CUST-{n:05d}"


def record_refusal(session, *, guideline_thread_id: str, prose: str,
                   refusal: Refusal | None = None,
                   refusal_class: str | None = None,
                   refusal_reason: str | None = None,
                   nearest_expressible=None,
                   actor_user_id: int, actor_tenant_id: int,
                   actor_role: str) -> int:
    """One refused guideline -> one ledger row. Mechanical refusals pass
    the grammar's Refusal; reviewer-judged classes pass class + reason
    directly. Never a dead end: it renders beside NOT_COVERED (§f)."""
    _require_admin(actor_role)
    if refusal is not None:
        refusal_class = refusal.refusal_class
        refusal_reason = refusal.reason
        nearest_expressible = refusal.nearest_expressible
    if not refusal_class:
        raise AuthoringError("a refusal needs its class")
    row = session.execute(text("""
        INSERT INTO cust_authoring_ledger
            (guideline_thread_id, prose, outcome, refusal_class,
             refusal_reason, nearest_expressible, actor_user_id)
        VALUES (:g, :p, 'refused', :c, :r, CAST(:n AS JSONB), :actor)
        RETURNING id
    """), {"g": guideline_thread_id, "p": prose, "c": refusal_class,
           "r": refusal_reason,
           "n": json.dumps(nearest_expressible),
           "actor": actor_user_id}).fetchone()
    _audit(session, actor_tenant_id, actor_user_id,
           "cust.authoring.refused", None,
           {"guideline_thread_id": guideline_thread_id,
            "refusal_class": refusal_class})
    session.commit()
    return int(row[0])


def draft_rule(session, rule: ValidatedRule, *, prose: str,
               actor_user_id: int, actor_tenant_id: int,
               actor_role: str) -> dict:
    """A grammar-validated rule -> a minted id, a DRAFT version, the
    normalised predicate rows, and a 'drafted' ledger row — one act.
    Token-set pins must exist BEFORE the draft (a pin to nothing is a
    refusal, not a dangling reference)."""
    _require_admin(actor_role)
    for pin in rule.token_set_pins:
        hit = session.execute(text("""
            SELECT 1 FROM cust_token_sets
            WHERE set_key = :k AND version = :v
        """), {"k": pin["token_set"], "v": pin["version"]}).fetchone()
        if hit is None:
            raise AuthoringError(
                f"token set {pin['token_set']} v{pin['version']} is not "
                "registered — pin the value domain before the rule")
    rule_id = next_cust_rule_id(session)
    session.execute(text(
        "INSERT INTO cust_rules (rule_id, created_by) VALUES (:r, :u)"),
        {"r": rule_id, "u": actor_user_id})
    content = rule.content()
    session.execute(text("""
        INSERT INTO cust_rule_versions
            (rule_id, version, name, guideline_thread_id, state,
             definition, content_hash, census_schema_version, created_by)
        VALUES (:r, 1, :n, :g, 'DRAFT', CAST(:d AS JSONB), :h, :cs, :u)
    """), {"r": rule_id, "n": rule.name, "g": rule.guideline_thread_id,
           "d": json.dumps(content, sort_keys=True),
           "h": rule.content_hash(),
           "cs": rule.census_schema_version, "u": actor_user_id})
    rows = [("selector", i, t["term"], {"value": t.get("value")})
            for i, t in enumerate(rule.selector)]
    rows.append(("predicate", 0, rule.predicate["form"],
                 {k: v for k, v in rule.predicate.items() if k != "form"}))
    if rule.applicability:
        rows.append(("applicability", 0, rule.applicability["gate"],
                     {"term": rule.applicability["term"]}))
    for slot, ordinal, term, operand in rows:
        session.execute(text("""
            INSERT INTO cust_predicates
                (rule_id, rule_version, slot, ordinal, term, operand)
            VALUES (:r, 1, :s, :o, :t, CAST(:op AS JSONB))
        """), {"r": rule_id, "s": slot, "o": ordinal, "t": term,
               "op": json.dumps(operand, sort_keys=True)})
    session.execute(text("""
        INSERT INTO cust_authoring_ledger
            (guideline_thread_id, prose, outcome, rule_id, actor_user_id)
        VALUES (:g, :p, 'drafted', :r, :actor)
    """), {"g": rule.guideline_thread_id, "p": prose, "r": rule_id,
           "actor": actor_user_id})
    _audit(session, actor_tenant_id, actor_user_id, "cust.rule.draft",
           rule_id, {"content_hash": rule.content_hash(),
                     "guideline_thread_id": rule.guideline_thread_id})
    session.commit()
    return {"rule_id": rule_id, "version": 1,
            "content_hash": rule.content_hash()}


def create_token_set(session, *, set_key: str, version: int, tokens: list,
                     notes: str = "", actor_user_id: int,
                     actor_tenant_id: int, actor_role: str) -> None:
    """Immutable versioned value domain. A new list is a new version —
    rules pinned to the old one keep meaning what they meant (§h)."""
    _require_admin(actor_role)
    if not tokens:
        raise AuthoringError("an empty token set is refused")
    if len(tokens) > 200:
        raise AuthoringError("token sets are size-capped at 200 (§e.3)")
    session.execute(text("""
        INSERT INTO cust_token_sets
            (set_key, version, tokens, notes, created_by)
        VALUES (:k, :v, CAST(:t AS JSONB), :n, :u)
    """), {"k": set_key, "v": version, "t": json.dumps(list(tokens)),
           "n": notes, "u": actor_user_id})
    _audit(session, actor_tenant_id, actor_user_id, "cust.token_set.create",
           None, {"set_key": set_key, "version": version,
                  "size": len(tokens)})
    session.commit()


# ---------------------------------------------------------------------------
# The lifecycle (the s5 shape, unchanged — §i)
# ---------------------------------------------------------------------------

def transition(session, *, rule_id: str, version: int, to_state: str,
               actor_user_id: int, actor_tenant_id: int, actor_role: str,
               reviewed_no_conflict: bool = False) -> None:
    _require_admin(actor_role)
    row = session.execute(text("""
        SELECT state, definition FROM cust_rule_versions
        WHERE rule_id = :r AND version = :v FOR UPDATE
    """), {"r": rule_id, "v": version}).fetchone()
    if row is None:
        raise AuthoringError(f"no such rule version {rule_id} v{version}")
    state, definition = row
    if _TRANSITIONS.get(state) != to_state:
        raise AuthoringError(
            f"illegal transition {state} -> {to_state} (strictly "
            f"sequential: {' -> '.join(_TRANSITIONS)} -> RETIRED)")

    extra: dict = {}
    if to_state == "APPROVED":
        conflict = _mechanical_conflict(session, rule_id, definition)
        if conflict:
            raise AuthoringError(
                "ratification conflict gate (§h): this predicate is the "
                f"NEGATION of ACTIVE custom rule {conflict['rule_id']} on "
                f"profile criterion {conflict['criterion']!r} — two ACTIVE "
                "rules that cannot both pass the same node is a "
                "contradiction, refused")
        overlap = _catalogue_overlap(session, definition)
        if not reviewed_no_conflict:
            raise AuthoringError(
                "APPROVED requires the reviewer's explicit "
                "reviewed_no_conflict=True; catalogue rules sharing the "
                f"bound criterion: {overlap or 'none'} — engine semantics "
                "are not mechanically comparable, so the human confirms")
        session.execute(text("""
            UPDATE cust_rule_versions
            SET state='APPROVED', reviewed_by=:u, reviewed_at=NOW(),
                reviewed_no_conflict=TRUE,
                state_changed_by=:u, state_changed_at=NOW(),
                definition = definition || CAST(:ov AS JSONB)
            WHERE rule_id=:r AND version=:v
        """), {"u": actor_user_id, "r": rule_id, "v": version,
               "ov": json.dumps({"catalogue_overlap_at_review": overlap})})
        extra = {"catalogue_overlap": overlap}
    else:
        session.execute(text("""
            UPDATE cust_rule_versions
            SET state=:st, state_changed_by=:u, state_changed_at=NOW()
            WHERE rule_id=:r AND version=:v
        """), {"st": to_state, "u": actor_user_id, "r": rule_id,
               "v": version})
    _audit(session, actor_tenant_id, actor_user_id, "cust.rule.transition",
           rule_id, {"version": version, "from": state, "to": to_state,
                     **extra})
    session.commit()


def _mechanical_conflict(session, rule_id: str, definition: dict):
    """The decidable contradiction: an ACTIVE custom rule on the SAME
    profile criterion with the SAME selector and the NEGATED predicate
    form over the same fact/operand."""
    pred = definition.get("predicate") or {}
    others = session.execute(text("""
        SELECT rule_id, definition FROM cust_rule_versions
        WHERE state = 'ACTIVE' AND rule_id <> :r
    """), {"r": rule_id}).fetchall()
    for other_id, other in others:
        oc = (other.get("criterion") or {}).get("profile")
        if oc != (definition.get("criterion") or {}).get("profile"):
            continue
        if other.get("selector") != definition.get("selector"):
            continue
        op = other.get("predicate") or {}
        if (pred.get("form"), op.get("form")) not in _NEGATION_PAIRS:
            continue
        same_fact = pred.get("fact") == op.get("fact")
        same_operand = (
            pred.get("literal") == op.get("literal")
            and pred.get("token_set") == op.get("token_set"))
        if same_fact and same_operand:
            return {"rule_id": other_id,
                    "criterion": oc}
    return None


def _catalogue_overlap(session, definition: dict) -> list:
    sc = (definition.get("criterion") or {}).get("binds_wcag_sc")
    if not sc:
        return []
    rows = session.execute(text("""
        SELECT DISTINCT m.rule_id
        FROM s5_standard_maps m
        JOIN s5_standard_map_sets s ON s.id = m.map_set_id
             AND s.state = 'ACTIVE' AND s.standard = 'WCAG22'
        JOIN s5_rule_versions v
             ON v.rule_id = m.rule_id AND v.version = m.rule_version
             AND v.state = 'ACTIVE'
        WHERE m.criterion = :c
        ORDER BY m.rule_id
    """), {"c": sc}).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# The tenant release union (D-281: recorded at cut time)
# ---------------------------------------------------------------------------

def record_tenant_release(session, *, platform_release_id: int,
                          actor_user_id: int, actor_tenant_id: int,
                          actor_role: str) -> dict:
    """Snapshot the tenant's ACTIVE custom rules as members of the named
    platform release. Enumeration reads THIS record, never 'current
    ACTIVE' (D-281). Re-recording the same release is refused — a
    membership snapshot is one act."""
    _require_admin(actor_role)
    exists = session.execute(text("""
        SELECT COUNT(*) FROM cust_release_members
        WHERE platform_release_id = :p
    """), {"p": platform_release_id}).scalar_one()
    if exists:
        raise AuthoringError(
            f"tenant membership for release {platform_release_id} is "
            f"already recorded ({exists} rules) — membership is recorded "
            "once, never re-cut (D-281)")
    rows = session.execute(text("""
        INSERT INTO cust_release_members
            (platform_release_id, rule_id, rule_version, recorded_by)
        SELECT :p, rule_id, version, :u FROM cust_rule_versions
        WHERE state = 'ACTIVE'
        RETURNING rule_id, rule_version
    """), {"p": platform_release_id, "u": actor_user_id}).fetchall()
    _audit(session, actor_tenant_id, actor_user_id,
           "cust.release.record_union", None,
           {"platform_release_id": platform_release_id,
            "members": [f"{r[0]}v{r[1]}" for r in rows]})
    session.commit()
    return {"platform_release_id": platform_release_id,
            "members": len(rows)}


# ---------------------------------------------------------------------------
# Reads for enumeration and evaluation
# ---------------------------------------------------------------------------

def tenant_release_members(session, platform_release_id: int) -> list:
    """The RECORDED union rows with the staleness law applied: a
    recorded version that is no longer the rule's ACTIVE version refuses
    enumeration, exactly as the platform release does."""
    rows = session.execute(text("""
        SELECT m.rule_id, m.rule_version, v.name, v.state
        FROM cust_release_members m
        JOIN cust_rule_versions v
             ON v.rule_id = m.rule_id AND v.version = m.rule_version
        WHERE m.platform_release_id = :p
        ORDER BY m.rule_id
    """), {"p": platform_release_id}).fetchall()
    out = []
    for rule_id, ver, name, state in rows:
        if state != "ACTIVE":
            raise AuthoringError(
                f"stale tenant membership — {rule_id} v{ver} is recorded "
                f"for release {platform_release_id} but is {state}; "
                "re-cut against a fresh release rather than enumerating "
                "a retired rule")
        out.append({"rule_id": rule_id, "version": ver, "name": name,
                    "automation_capability": "AUTO"})
    return out


def load_active_content(session, rule_id: str) -> dict | None:
    row = session.execute(text("""
        SELECT definition, census_schema_version FROM cust_rule_versions
        WHERE rule_id = :r AND state = 'ACTIVE'
    """), {"r": rule_id}).fetchone()
    if row is None:
        return None
    content = dict(row[0])
    content["census_schema_version"] = row[1]
    return content


def load_token_sets(session, pins: list) -> dict:
    """{(key, version): tokens} for the evaluator."""
    out = {}
    for pin in pins or []:
        row = session.execute(text("""
            SELECT tokens FROM cust_token_sets
            WHERE set_key = :k AND version = :v
        """), {"k": pin["token_set"], "v": pin["version"]}).fetchone()
        if row is not None:
            out[(pin["token_set"], pin["version"])] = list(row[0])
    return out


# ---------------------------------------------------------------------------
# The customer profile set (Part 3 — §g lean, ratified): the tenant's own
# guideline headings as a standard-like denominator, same lifecycle shape.
# ---------------------------------------------------------------------------

_PROFILE_TRANSITIONS = {
    "DRAFT": "REVIEW",
    "REVIEW": "APPROVED",
    "APPROVED": "ACTIVE",
    "ACTIVE": "RETIRED",
}


def create_profile_set(session, *, profile_key: str, revision: int = 1,
                       notes: str = "", provenance: dict | None = None,
                       actor_user_id: int, actor_tenant_id: int,
                       actor_role: str) -> int:
    _require_admin(actor_role)
    row = session.execute(text("""
        INSERT INTO cust_profile_sets
            (profile_key, revision, state, notes, provenance, created_by)
        VALUES (:k, :r, 'DRAFT', :n, CAST(:p AS JSONB), :u)
        RETURNING id
    """), {"k": profile_key, "r": revision, "n": notes,
           "p": json.dumps(provenance or {}), "u": actor_user_id}).fetchone()
    _audit(session, actor_tenant_id, actor_user_id,
           "cust.profile_set.create", None,
           {"profile_key": profile_key, "revision": revision,
            "set_id": row[0]})
    session.commit()
    return int(row[0])


def add_profile_criterion(session, *, set_id: int, criterion: str,
                          title: str = "", actor_user_id: int,
                          actor_tenant_id: int, actor_role: str) -> None:
    """One guideline HEADING into a DRAFT set — content freeze from
    REVIEW onward, exactly as everywhere else."""
    _require_admin(actor_role)
    st = session.execute(text(
        "SELECT state, profile_key FROM cust_profile_sets WHERE id=:i"),
        {"i": set_id}).fetchone()
    if st is None:
        raise AuthoringError(f"no such profile set {set_id}")
    if st[0] != "DRAFT":
        raise AuthoringError(
            f"criterion authoring requires the SET in DRAFT; profile set "
            f"{set_id} is {st[0]} — content is frozen from REVIEW onward")
    nxt = session.execute(text(
        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM cust_profile_criteria "
        "WHERE set_id=:i"), {"i": set_id}).scalar_one()
    session.execute(text("""
        INSERT INTO cust_profile_criteria (set_id, criterion, title, ordinal)
        VALUES (:i, :c, :t, :o)
    """), {"i": set_id, "c": criterion, "t": title, "o": nxt})
    _audit(session, actor_tenant_id, actor_user_id,
           "cust.profile_set.add_criterion", None,
           {"set_id": set_id, "profile_key": st[1], "criterion": criterion})
    session.commit()


def transition_profile_set(session, *, set_id: int, to_state: str,
                           actor_user_id: int, actor_tenant_id: int,
                           actor_role: str) -> None:
    """APPROVED freezes a content hash over the ordered headings and
    refuses an empty set; ACTIVE atomically retires the profile key's
    previous ACTIVE revision (the single-ACTIVE index makes the swap
    atomic-or-refused)."""
    import hashlib as _hashlib

    _require_admin(actor_role)
    row = session.execute(text(
        "SELECT state, profile_key FROM cust_profile_sets WHERE id=:i "
        "FOR UPDATE"), {"i": set_id}).fetchone()
    if row is None:
        raise AuthoringError(f"no such profile set {set_id}")
    state, key = row
    if _PROFILE_TRANSITIONS.get(state) != to_state:
        raise AuthoringError(
            f"illegal profile-set transition {state} -> {to_state}")
    if to_state == "APPROVED":
        crit = session.execute(text("""
            SELECT criterion, COALESCE(title, ''), ordinal
            FROM cust_profile_criteria WHERE set_id=:i
            ORDER BY ordinal, criterion"""), {"i": set_id}).fetchall()
        if not crit:
            raise AuthoringError(
                f"refusing to approve an EMPTY profile set {set_id}")
        digest = _hashlib.sha256("\n".join(
            "|".join(str(c) for c in r) for r in crit).encode()).hexdigest()
        session.execute(text("""
            UPDATE cust_profile_sets
            SET state='APPROVED', reviewed_by=:u, reviewed_at=NOW(),
                content_hash=:h WHERE id=:i
        """), {"u": actor_user_id, "h": digest, "i": set_id})
    elif to_state == "ACTIVE":
        session.execute(text("""
            UPDATE cust_profile_sets SET state='RETIRED'
            WHERE profile_key=:k AND state='ACTIVE' AND id <> :i
        """), {"k": key, "i": set_id})
        session.execute(text(
            "UPDATE cust_profile_sets SET state='ACTIVE', activated_at=NOW() "
            "WHERE id=:i"), {"i": set_id})
    else:
        session.execute(text(
            "UPDATE cust_profile_sets SET state=:st WHERE id=:i"),
            {"st": to_state, "i": set_id})
    _audit(session, actor_tenant_id, actor_user_id,
           "cust.profile_set.transition", None,
           {"set_id": set_id, "profile_key": key, "from": state,
            "to": to_state})
    session.commit()
