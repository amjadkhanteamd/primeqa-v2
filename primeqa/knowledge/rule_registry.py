"""S5 Rule Registry — READ API (LLD 3A-1 §4; HLD DE-01).

Platform-global catalogue reads (public schema — reachable from any tenant
context via search_path, and from core sessions directly). RESULT-PROCESSOR
side only: workers never import this module (SAD A10 hard boundary; the
worker receives the pinned engine artifact and nothing else).

Writes happen ONLY through knowledge/rule_lifecycle.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text


@dataclass(frozen=True)
class RuleRead:
    rule_id: str
    version: int
    name: str
    description: str
    automation_capability: str
    human_review_required: bool
    state: str
    criteria: tuple = field(default_factory=tuple)   # ((standard, criterion, level), ...)


@dataclass(frozen=True)
class ArtifactRead:
    kind: str
    name: str
    version: str
    sha256: str
    repo_path: str


def active_rules_for_profile(session, standard: str) -> list[RuleRead]:
    """S3 enumeration's feed (DE-05): every ACTIVE rule version mapped to the
    given standard, with its criteria. Deterministic order by rule_id."""
    rows = session.execute(text("""
        SELECT v.rule_id, v.version, v.name, v.description,
               v.automation_capability, v.human_review_required, v.state,
               m.standard, m.criterion, m.level
        FROM s5_rule_versions v
        JOIN s5_standard_maps m
          ON m.rule_id = v.rule_id AND m.rule_version = v.version
        WHERE v.state = 'ACTIVE' AND m.standard = :std
        ORDER BY v.rule_id, m.criterion
    """), {"std": standard}).fetchall()
    by_rule: dict = {}
    for r in rows:
        key = (r[0], r[1])
        entry = by_rule.setdefault(key, {"row": r, "criteria": []})
        entry["criteria"].append((r[7], r[8], r[9]))
    out = []
    for (rid, ver), e in sorted(by_rule.items()):
        r = e["row"]
        out.append(RuleRead(rid, ver, r[2], r[3], r[4], r[5], r[6],
                            tuple(e["criteria"])))
    return out


def rule(session, rule_id: str, version: int | None = None) -> RuleRead | None:
    """Result-processor / UI metadata read. Default: the ACTIVE version."""
    if version is None:
        row = session.execute(text("""
            SELECT rule_id, version, name, description, automation_capability,
                   human_review_required, state
            FROM s5_rule_versions
            WHERE rule_id = :rid AND state = 'ACTIVE'
        """), {"rid": rule_id}).fetchone()
    else:
        row = session.execute(text("""
            SELECT rule_id, version, name, description, automation_capability,
                   human_review_required, state
            FROM s5_rule_versions WHERE rule_id = :rid AND version = :v
        """), {"rid": rule_id, "v": version}).fetchone()
    if row is None:
        return None
    crits = session.execute(text("""
        SELECT standard, criterion, level FROM s5_standard_maps
        WHERE rule_id = :rid AND rule_version = :v
        ORDER BY standard, criterion
    """), {"rid": row[0], "v": row[1]}).fetchall()
    return RuleRead(row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                    tuple((c[0], c[1], c[2]) for c in crits))


def bindings_for_engine(session, engine: str, engine_version: str) -> dict:
    """The observation->rule resolution map: engine_rule_id ->
    [(rule_id, version), ...] over ACTIVE rule versions only. Engine rule
    ids ABSENT from this dict are UNMAPPED — the caller must surface them
    honestly (see resolve_engine_rules), never drop them."""
    rows = session.execute(text("""
        SELECT b.engine_rule_id, b.rule_id, b.rule_version
        FROM s5_engine_bindings b
        JOIN s5_rule_versions v
          ON v.rule_id = b.rule_id AND v.version = b.rule_version
        WHERE b.engine = :e AND b.engine_version = :ev AND v.state = 'ACTIVE'
        ORDER BY b.engine_rule_id, b.rule_id
    """), {"e": engine, "ev": engine_version}).fetchall()
    out: dict = {}
    for engine_rule_id, rid, ver in rows:
        out.setdefault(engine_rule_id, []).append((rid, ver))
    return out


def resolve_engine_rules(session, engine: str, engine_version: str,
                         engine_rule_ids: list) -> dict:
    """Partition observed engine rule ids into mapped/unmapped:
    {"mapped": {engine_rule_id: [(rule_id, version)]}, "unmapped": [ids]}.
    UNMAPPED is a first-class, reportable outcome — never a silent drop."""
    bindings = bindings_for_engine(session, engine, engine_version)
    mapped, unmapped = {}, []
    for eid in engine_rule_ids:
        if eid in bindings:
            mapped[eid] = bindings[eid]
        else:
            unmapped.append(eid)
    return {"mapped": mapped, "unmapped": sorted(set(unmapped))}


def pinned_artifact(session, kind: str, name: str) -> ArtifactRead | None:
    """Manifest building reads the pin here (latest version row for the
    kind+name; versions are unique by the s5_artifacts_unique constraint)."""
    row = session.execute(text("""
        SELECT kind, name, version, sha256, repo_path FROM s5_artifacts
        WHERE kind = :k AND name = :n ORDER BY created_at DESC, id DESC LIMIT 1
    """), {"k": kind, "n": name}).fetchone()
    return ArtifactRead(row[0], row[1], row[2], row[3].strip(), row[4]) if row else None


def release(session, release_id: int) -> dict | None:
    """A catalogue release + its RECORDED membership (D-281 law: read the
    recorded rows, never recompute from state timestamps)."""
    rel = session.execute(text("""
        SELECT id, notes, content_hash, created_at
        FROM s5_catalogue_releases WHERE id = :i
    """), {"i": release_id}).fetchone()
    if rel is None:
        return None
    members = session.execute(text("""
        SELECT rule_id, rule_version FROM s5_catalogue_release_members
        WHERE release_id = :i ORDER BY rule_id
    """), {"i": release_id}).fetchall()
    return {"release_id": rel[0], "notes": rel[1],
            "content_hash": rel[2].strip(), "created_at": rel[3],
            "members": [(m[0], m[1]) for m in members]}
