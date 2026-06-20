"""FAITHFUL real-DB proof for the 1b.2 ValidationRule deletion reconcile.

Mocks nothing — seeds real ``entities`` + ``edges`` rows in the isolated test
schema, runs the REAL ``reconcile_deletions_by_sf_id`` (the actual SCD-2 close +
edge close SQL), and asserts the ACTUAL post-reconcile model state. The per-test
transaction rolls back at teardown (conftest ``conn`` fixture).

Two cases form the safety proof:
  * WITH the reconcile → the VR absent from Salesforce's current id set is
    superseded (valid_to_seq closed) AND its edges are closed; the surviving VR +
    its edges are untouched.
  * WITHOUT the reconcile (red-on-disable) → the same deleted VR stays
    currently-valid. This is the test that proves a delta is UNSAFE without its
    reconcile: deletion is invisible to a LastModifiedDate delta, so only the
    reconcile catches it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock

from sqlalchemy import text

from primeqa.integrations.exceptions import SFIncompletePaginationError
from primeqa.sync import phases
from primeqa.sync.materialize import reconcile_deletions_by_sf_id
from primeqa.sync.result import PhaseResult


def _make_org(conn) -> str:
    return str(conn.execute(text(
        "INSERT INTO connected_orgs (org_type, sf_instance_url, label) "
        "VALUES ('sandbox', 'https://t.my.salesforce.com', 'delta-test-org') "
        "RETURNING id"
    )).scalar())


def _scope_to_org(conn, org_id: str, entity_ids: list) -> None:
    conn.execute(text(
        "UPDATE entities SET last_synced_from_org_id = CAST(:o AS uuid) "
        "WHERE id = ANY(CAST(:ids AS uuid[]))"
    ), {"o": org_id, "ids": [str(e) for e in entity_ids]})


def _valid_to(conn, entity_id) -> int | None:
    return conn.execute(text(
        "SELECT valid_to_seq FROM entities WHERE id = CAST(:id AS uuid)"
    ), {"id": str(entity_id)}).scalar()


def _edge_valid_to(conn, edge_id) -> int | None:
    return conn.execute(text(
        "SELECT valid_to_seq FROM edges WHERE id = CAST(:id AS uuid)"
    ), {"id": str(edge_id)}).scalar()


def test_reconcile_supersedes_deleted_vr_and_its_edges(conn, seed) -> None:
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    keep = seed.entity("ValidationRule", "Account.Keep", valid_from_seq=v0, sf_id="VRKEEP")
    deleted = seed.entity("ValidationRule", "Account.Del", valid_from_seq=v0, sf_id="VRDEL")
    _scope_to_org(conn, org, [obj, keep, deleted])
    del_edge = seed.edge(deleted, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)
    keep_edge = seed.edge(keep, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)

    v1 = seed.version()  # the reconcile run's logical version
    ctx = SimpleNamespace(connected_org_id=org, logical_version_seq=v1)
    result = PhaseResult(entity_type="ValidationRule")

    # Salesforce's full current id set has ONLY the kept VR — the deleted VR is
    # absent (it was removed in SF). The Object id is irrelevant (reconcile is
    # entity_type-scoped to ValidationRule).
    n = reconcile_deletions_by_sf_id(conn, ctx, "ValidationRule", {"VRKEEP"}, result)

    assert n == 1
    assert result.entities_superseded == 1
    # the deleted VR is superseded (closed at the reconcile version) ...
    assert _valid_to(conn, deleted) == v1
    # ... and its edge is closed (no dangling active edge off a superseded entity)
    assert _edge_valid_to(conn, del_edge) == v1
    # the surviving VR + its edge are untouched
    assert _valid_to(conn, keep) is None
    assert _edge_valid_to(conn, keep_edge) is None


def test_red_on_disable_without_reconcile_the_deletion_is_missed(conn, seed) -> None:
    # RED-ON-DISABLE: identical setup, but the reconcile is DISABLED (not called).
    # The VR deleted in Salesforce stays currently-valid — proving the reconcile
    # is exactly the mechanism that makes the delta safe. (On the pre-1b.2 code,
    # and on the full-fetch path, this is the standing behavior: deletions are
    # never detected.)
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    deleted = seed.entity("ValidationRule", "Account.Del", valid_from_seq=v0, sf_id="VRDEL")
    _scope_to_org(conn, org, [obj, deleted])
    del_edge = seed.edge(deleted, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)

    # --- reconcile intentionally NOT invoked ---

    assert _valid_to(conn, deleted) is None      # deletion missed: still valid
    assert _edge_valid_to(conn, del_edge) is None

    # And to confirm the assertion above is the ONLY thing standing between
    # "missed" and "caught": running the reconcile now flips it.
    v1 = seed.version()
    ctx = SimpleNamespace(connected_org_id=org, logical_version_seq=v1)
    reconcile_deletions_by_sf_id(
        conn, ctx, "ValidationRule", set(), PhaseResult(entity_type="ValidationRule"))
    assert _valid_to(conn, deleted) == v1        # now caught


def test_reconcile_noop_when_all_present(conn, seed) -> None:
    # Nothing absent from the SF id set → nothing superseded (the steady state:
    # a delta run that deleted nothing must not close any live entity).
    org = _make_org(conn)
    v0 = seed.version()
    keep = seed.entity("ValidationRule", "Account.Keep", valid_from_seq=v0, sf_id="VRKEEP")
    _scope_to_org(conn, org, [keep])
    v1 = seed.version()
    ctx = SimpleNamespace(connected_org_id=org, logical_version_seq=v1)
    result = PhaseResult(entity_type="ValidationRule")
    n = reconcile_deletions_by_sf_id(conn, ctx, "ValidationRule", {"VRKEEP"}, result)
    assert n == 0
    assert _valid_to(conn, keep) is None


def test_phase_reconcile_uses_full_id_set_not_the_delta_subset(conn, seed) -> None:
    # PHASE-WIRING / NO-FALSE-DELETION guard. Drive the REAL phase_validation_rule
    # end-to-end with the delta path active. The delta fetch returns a SUBSET that
    # EXCLUDES present VRs (here: empty — nothing modified, so A and B are present
    # but delta-excluded); the FULL current id set (fetch_validation_rule_ids)
    # omits only the genuinely-deleted VR (C). The reconcile must close ONLY C and
    # leave A + B active.
    #
    # CATASTROPHIC case this guards: if the reconcile took its present-set from the
    # DELTA fetch (the subset) instead of fetch_validation_rule_ids (the full set),
    # every present-but-delta-excluded VR (A and B) would be wrongly superseded.
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJ")
    a = seed.entity("ValidationRule", "Account.A", valid_from_seq=v0, sf_id="VRA")
    b = seed.entity("ValidationRule", "Account.B", valid_from_seq=v0, sf_id="VRB")
    c = seed.entity("ValidationRule", "Account.C", valid_from_seq=v0, sf_id="VRC")
    _scope_to_org(conn, org, [obj, a, b, c])

    v1 = seed.version()
    sf = MagicMock(name="sf_client")
    sf.fetch_validation_rules.return_value = []            # delta: nothing modified
    sf.fetch_validation_rule_ids.return_value = {"VRA", "VRB"}  # FULL set: C deleted
    ctx = SimpleNamespace(
        sf_client=sf, connected_org_id=org, logical_version_seq=v1,
        delta_since=datetime(2026, 6, 1, tzinfo=timezone.utc))  # delta path ON

    phases.phase_validation_rule(ctx, conn)

    # delta path ran: delta fetch with the watermark + the FULL id-list for reconcile
    sf.fetch_validation_rules.assert_called_once_with(modified_since=ctx.delta_since)
    sf.fetch_validation_rule_ids.assert_called_once()
    # genuinely-absent (C) → superseded ...
    assert _valid_to(conn, c) == v1
    # ... present-but-delta-excluded (A, B) → STILL ACTIVE (the proving assertion:
    # the reconcile used the full id set {VRA,VRB}, not the empty delta subset).
    assert _valid_to(conn, a) is None
    assert _valid_to(conn, b) is None


def test_reconcile_is_org_scoped(conn, seed) -> None:
    # A VR another org sourced (different last_synced_from_org_id) is NEVER
    # superseded by this org's fetch, even when absent from this org's id set —
    # the multi-org safety guard.
    org_a = _make_org(conn)
    org_b = _make_org(conn)
    v0 = seed.version()
    other = seed.entity("ValidationRule", "Account.Other", valid_from_seq=v0, sf_id="VROTHER")
    _scope_to_org(conn, org_b, [other])          # belongs to org B
    v1 = seed.version()
    ctx = SimpleNamespace(connected_org_id=org_a, logical_version_seq=v1)
    # org A reconciles with an EMPTY present set — but `other` is org B's, so it
    # must survive.
    n = reconcile_deletions_by_sf_id(
        conn, ctx, "ValidationRule", set(), PhaseResult(entity_type="ValidationRule"))
    assert n == 0
    assert _valid_to(conn, other) is None


# ----------------------------------------------------------------------
# D-253 backstop #1 — full-fetch deletion reconcile + empty/partial REFUSAL on
# BOTH paths. These drive the REAL phase_validation_rule end-to-end (mock only
# the SF boundary). delta_since=None → the full-fetch branch.
# ----------------------------------------------------------------------
def _full_fetch_ctx(org, vseq, *, ids=None, ids_side_effect=None, raw=None):
    sf = MagicMock(name="sf_client")
    sf.fetch_validation_rules.return_value = raw if raw is not None else []
    if ids_side_effect is not None:
        sf.fetch_validation_rule_ids.side_effect = ids_side_effect
    else:
        sf.fetch_validation_rule_ids.return_value = ids if ids is not None else set()
    ctx = SimpleNamespace(sf_client=sf, connected_org_id=org,
                          logical_version_seq=vseq, delta_since=None)
    return ctx, sf


def _delta_ctx(org, vseq, *, ids=None, ids_side_effect=None):
    sf = MagicMock(name="sf_client")
    sf.fetch_validation_rules.return_value = []          # nothing modified
    if ids_side_effect is not None:
        sf.fetch_validation_rule_ids.side_effect = ids_side_effect
    else:
        sf.fetch_validation_rule_ids.return_value = ids if ids is not None else set()
    ctx = SimpleNamespace(sf_client=sf, connected_org_id=org,
                          logical_version_seq=vseq,
                          delta_since=datetime(2026, 6, 1, tzinfo=timezone.utc))
    return ctx, sf


def test_full_fetch_closes_deleted_vr_and_edges(conn, seed) -> None:
    # Backstop #1: on the FULL-FETCH path (delta_since=None) the reconcile now
    # runs — a VR absent from the full SELECT Id set is superseded + its edges
    # closed; survivors untouched. (Pre-D-253 the full-fetch path never detected
    # entity deletions.)
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    keep = seed.entity("ValidationRule", "Account.Keep", valid_from_seq=v0, sf_id="VRKEEP")
    deleted = seed.entity("ValidationRule", "Account.Del", valid_from_seq=v0, sf_id="VRDEL")
    _scope_to_org(conn, org, [obj, keep, deleted])
    del_edge = seed.edge(deleted, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)
    keep_edge = seed.edge(keep, obj, "BELONGS_TO", "STRUCTURAL", valid_from_seq=v0)

    v1 = seed.version()
    ctx, sf = _full_fetch_ctx(org, v1, ids={"VRKEEP"})   # Del gone from SF
    phases.phase_validation_rule(ctx, conn)

    sf.fetch_validation_rules.assert_called_once_with()   # full fetch, no modified_since
    sf.fetch_validation_rule_ids.assert_called_once()     # the gated id-list
    assert _valid_to(conn, deleted) == v1                 # superseded
    assert _edge_valid_to(conn, del_edge) == v1           # its edge closed
    assert _valid_to(conn, keep) is None                  # survivor untouched
    assert _edge_valid_to(conn, keep_edge) is None


def test_full_fetch_red_on_disable_reconcile_is_the_mechanism(conn, seed) -> None:
    # RED-ON-DISABLE (full-fetch): with the reconcile disabled the deletion is
    # missed (the full-fetch path's materialize never detects it); enabling it
    # flips it — proving the reconcile is the mechanism on this path.
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    deleted = seed.entity("ValidationRule", "Account.Del", valid_from_seq=v0, sf_id="VRDEL")
    _scope_to_org(conn, org, [obj, deleted])

    v1 = seed.version()
    ctx, sf = _full_fetch_ctx(org, v1, ids={"VRKEEP"})    # Del absent from SF
    with mock.patch.object(phases, "reconcile_deletions_by_sf_id") as rec_noop:
        phases.phase_validation_rule(ctx, conn)
        rec_noop.assert_called_once()                     # gate passed (reconcile reached) ...
    assert _valid_to(conn, deleted) is None               # ...but disabled → Del still valid

    phases.phase_validation_rule(ctx, conn)               # now with the REAL reconcile
    assert _valid_to(conn, deleted) == v1                 # caught


def test_full_fetch_partial_id_fetch_refuses(conn, seed) -> None:
    # PARTIAL → REFUSE: the completeness-gated id-fetch RAISES (the require_complete
    # malformed-cursor case) → the phase funnel sets present_ids=None → NO
    # reconcile. Nothing is mass-closed despite a modeled VR looking absent.
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    a = seed.entity("ValidationRule", "Account.A", valid_from_seq=v0, sf_id="VRA")
    b = seed.entity("ValidationRule", "Account.B", valid_from_seq=v0, sf_id="VRB")
    _scope_to_org(conn, org, [obj, a, b])

    v1 = seed.version()
    ctx, sf = _full_fetch_ctx(
        org, v1, ids_side_effect=SFIncompletePaginationError("partial"))
    phases.phase_validation_rule(ctx, conn)

    assert _valid_to(conn, a) is None                     # refused: NOT closed
    assert _valid_to(conn, b) is None


def test_full_fetch_empty_id_fetch_refuses(conn, seed) -> None:
    # EMPTY → REFUSE (the catastrophic guard): an empty id-fetch (set()) must NOT
    # mass-close every active VR — `if present_ids:` is falsy on set().
    org = _make_org(conn)
    v0 = seed.version()
    obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    a = seed.entity("ValidationRule", "Account.A", valid_from_seq=v0, sf_id="VRA")
    b = seed.entity("ValidationRule", "Account.B", valid_from_seq=v0, sf_id="VRB")
    _scope_to_org(conn, org, [obj, a, b])

    v1 = seed.version()
    ctx, sf = _full_fetch_ctx(org, v1, ids=set())         # empty id-fetch
    phases.phase_validation_rule(ctx, conn)

    assert _valid_to(conn, a) is None                     # NOT mass-closed
    assert _valid_to(conn, b) is None


def test_full_fetch_uses_unfiltered_id_set_not_scope_filtered(conn, seed) -> None:
    # SCOPE-FILTER TRAP: present_ids is the UNFILTERED SELECT Id superset. A VR
    # whose parent Object is OUT of sync scope (so it's filtered from materialize)
    # but PRESENT in SF must NOT be superseded — only the genuinely-absent VR
    # closes. (If present_ids came from the scope-filtered set, the out-of-scope
    # VR would be wrongly mass-closed.)
    org = _make_org(conn)
    v0 = seed.version()
    acct = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJACC")
    # NOTE: no Object entity for 'ManagedObj' → its VR is OUT of synced scope.
    in_scope = seed.entity("ValidationRule", "Account.InScope", valid_from_seq=v0, sf_id="VRIN")
    out_scope = seed.entity("ValidationRule", "ManagedObj.OutScope", valid_from_seq=v0, sf_id="VROUT")
    deleted = seed.entity("ValidationRule", "Account.Del", valid_from_seq=v0, sf_id="VRDEL")
    _scope_to_org(conn, org, [acct, in_scope, out_scope, deleted])

    v1 = seed.version()
    # the UNFILTERED full id-set: InScope + OutScope present in SF; Del gone.
    ctx, sf = _full_fetch_ctx(org, v1, ids={"VRIN", "VROUT"})
    phases.phase_validation_rule(ctx, conn)

    assert _valid_to(conn, deleted) == v1                 # genuinely absent → closed
    assert _valid_to(conn, in_scope) is None              # present + in scope → active
    assert _valid_to(conn, out_scope) is None             # present but out-of-scope → STILL active


def test_delta_empty_id_fetch_refuses(conn, seed) -> None:
    # LATENT-HOLE CLOSURE (delta path): shipped 1b.2 fed an ungated id-fetch to
    # the reconcile — an empty id-fetch would have mass-closed. D-253 closes it:
    # `if present_ids:` refuses set() on the delta path too.
    org = _make_org(conn)
    v0 = seed.version()
    a = seed.entity("ValidationRule", "Account.A", valid_from_seq=v0, sf_id="VRA")
    _scope_to_org(conn, org, [a])
    v1 = seed.version()
    ctx, sf = _delta_ctx(org, v1, ids=set())              # empty id-fetch, delta path
    phases.phase_validation_rule(ctx, conn)
    assert _valid_to(conn, a) is None                     # NOT mass-closed on the delta path


def test_delta_partial_id_fetch_refuses(conn, seed) -> None:
    # Delta path, PARTIAL: the id-fetch RAISES → the delta try/except falls back to
    # full-fetch, whose id-fetch ALSO raises → present_ids=None → no reconcile.
    org = _make_org(conn)
    v0 = seed.version()
    a = seed.entity("ValidationRule", "Account.A", valid_from_seq=v0, sf_id="VRA")
    _scope_to_org(conn, org, [a])
    v1 = seed.version()
    ctx, sf = _delta_ctx(
        org, v1, ids_side_effect=SFIncompletePaginationError("partial"))
    phases.phase_validation_rule(ctx, conn)
    assert _valid_to(conn, a) is None                     # refused: NOT closed
