"""Exhaustive unit tests for the role ladder + authorize() (D-245, Phase 1).

Pure functions, no I/O. These are the regression oracle for the ladder semantics;
every (role, min_tier) cell is asserted explicitly.
"""
import pytest

from primeqa.core.authz import (
    Tier,
    ROLE_TO_TIER,
    AuthorizationError,
    rank,
    authorize,
    floor_tier,
)

ALL_ROLES = ["viewer", "ba", "tester", "admin", "superadmin"]
ALL_TIERS = [Tier.VIEWER, Tier.MEMBER, Tier.ADMIN, Tier.SUPERADMIN]


class TestTierLadder:
    def test_strict_ordering(self):
        assert Tier.VIEWER < Tier.MEMBER < Tier.ADMIN < Tier.SUPERADMIN

    def test_int_values_are_the_ranks(self):
        assert (int(Tier.VIEWER), int(Tier.MEMBER), int(Tier.ADMIN), int(Tier.SUPERADMIN)) == (1, 2, 3, 4)

    def test_compares_with_bare_int(self):
        assert Tier.ADMIN >= 2 and Tier.MEMBER < 3


class TestRank:
    @pytest.mark.parametrize("role,expected", [
        ("viewer", Tier.VIEWER),
        ("ba", Tier.MEMBER),
        ("tester", Tier.MEMBER),
        ("admin", Tier.ADMIN),
        ("superadmin", Tier.SUPERADMIN),
    ])
    def test_known_roles(self, role, expected):
        assert rank(role) is expected

    def test_ba_and_tester_are_the_same_tier(self):
        assert rank("ba") == rank("tester") == Tier.MEMBER

    @pytest.mark.parametrize("bad", [None, "", "root", "owner", "VIEWER_X", "  "])
    def test_unknown_or_missing_fails_low_to_viewer(self, bad):
        # Fail LOW — never escalate an unrecognised role to a higher tier.
        assert rank(bad) is Tier.VIEWER

    @pytest.mark.parametrize("variant", ["Admin", "ADMIN", "  admin ", "AdMiN"])
    def test_case_and_whitespace_insensitive(self, variant):
        assert rank(variant) is Tier.ADMIN

    def test_mapping_covers_exactly_the_five_db_roles(self):
        assert set(ROLE_TO_TIER) == set(ALL_ROLES)


def _expected_allow(role: str, min_tier: Tier) -> bool:
    return rank(role) >= min_tier


class TestAuthorizeMatrix:
    """Every (role × min_tier) cell, for dict / object / str subjects."""

    @pytest.mark.parametrize("role", ALL_ROLES)
    @pytest.mark.parametrize("min_tier", ALL_TIERS)
    def test_dict_subject(self, role, min_tier):
        allow, reason = authorize({"role": role, "id": 7, "tenant_id": 1}, min_tier)
        assert allow is _expected_allow(role, min_tier)
        assert reason.startswith("allow" if allow else "deny")

    @pytest.mark.parametrize("role", ALL_ROLES)
    @pytest.mark.parametrize("min_tier", ALL_TIERS)
    def test_object_subject(self, role, min_tier):
        subj = type("U", (), {"role": role})()
        allow, _ = authorize(subj, min_tier)
        assert allow is _expected_allow(role, min_tier)

    @pytest.mark.parametrize("role", ALL_ROLES)
    @pytest.mark.parametrize("min_tier", ALL_TIERS)
    def test_str_subject(self, role, min_tier):
        allow, _ = authorize(role, min_tier)
        assert allow is _expected_allow(role, min_tier)

    def test_min_tier_accepts_bare_int(self):
        assert authorize("admin", 2)[0] is True
        assert authorize("viewer", 3)[0] is False


class TestSuperadminGodMode:
    @pytest.mark.parametrize("min_tier", ALL_TIERS)
    def test_superadmin_passes_every_tier(self, min_tier):
        allow, _ = authorize({"role": "superadmin"}, min_tier)
        assert allow is True


class TestKeyBoundaries:
    def test_member_allowed_at_member_denied_at_admin(self):
        assert authorize("tester", Tier.MEMBER)[0] is True
        assert authorize("tester", Tier.ADMIN)[0] is False
        assert authorize("ba", Tier.MEMBER)[0] is True
        assert authorize("ba", Tier.ADMIN)[0] is False

    def test_viewer_only_clears_viewer(self):
        assert authorize("viewer", Tier.VIEWER)[0] is True
        assert authorize("viewer", Tier.MEMBER)[0] is False

    def test_admin_clears_up_to_admin_not_superadmin(self):
        assert authorize("admin", Tier.ADMIN)[0] is True
        assert authorize("admin", Tier.SUPERADMIN)[0] is False

    def test_missing_subject_denied_above_viewer(self):
        assert authorize(None, Tier.MEMBER)[0] is False
        assert authorize({}, Tier.MEMBER)[0] is False  # dict with no 'role'

    def test_deny_reason_names_required_tier(self):
        allow, reason = authorize("viewer", Tier.ADMIN)
        assert allow is False
        assert "ADMIN" in reason and "deny" in reason


class TestAuthorizationError:
    def test_carries_reason(self):
        err = AuthorizationError("below tier")
        assert err.reason == "below tier"
        assert str(err) == "below tier"

    def test_default_reason(self):
        assert AuthorizationError().reason == "not authorized"


class TestFloorTier:
    """D-245 Phase 6: legacy role lists become `authorize(floor_tier(roles))`.

    The floor is the lowest tier among the listed roles; everyone at-or-above
    passes. These cover every list that actually appears at a call site plus the
    invariant that the conversion preserves the old admitted set EXCEPT for the
    one intended `ba` widening on `tester`-without-`ba` lists.
    """

    @pytest.mark.parametrize("roles,expected", [
        (("admin",),                              Tier.ADMIN),
        (("admin", "superadmin"),                 Tier.ADMIN),
        (("superadmin",),                         Tier.SUPERADMIN),
        (("admin", "tester"),                     Tier.MEMBER),
        (("admin", "tester", "superadmin"),       Tier.MEMBER),
        (("admin", "ba", "tester", "superadmin"), Tier.MEMBER),
    ])
    def test_real_call_site_lists(self, roles, expected):
        assert floor_tier(roles) is expected

    def test_superadmin_in_list_is_redundant(self):
        assert floor_tier(("admin",)) is floor_tier(("admin", "superadmin"))

    def test_empty_list_admits_only_god(self):
        # role not in [] -> deny-all-but-superadmin; floor is the ladder top.
        assert floor_tier(()) is Tier.SUPERADMIN

    def test_lowest_role_wins(self):
        assert floor_tier(("admin", "viewer")) is Tier.VIEWER

    @pytest.mark.parametrize("roles", [
        ("admin",), ("admin", "superadmin"), ("superadmin",),
        ("admin", "ba", "tester", "superadmin"),
    ])
    def test_non_tester_gap_lists_preserve_admitted_set(self, roles):
        # For every role, floor-tier authorize() must match the old
        # "role in list OR superadmin" semantics — these lists have no
        # tester-without-ba gap, so the sets are identical.
        floor = floor_tier(roles)
        for role in ALL_ROLES:
            old_allows = role in roles or role == "superadmin"
            new_allows = authorize(role, floor)[0]
            assert new_allows is old_allows, (role, roles)

    def test_tester_without_ba_widens_to_ba_only(self):
        # The one intended behavior change: a list naming tester (not ba) now
        # also admits ba. Everything else is unchanged.
        roles = ("admin", "tester")
        floor = floor_tier(roles)
        assert authorize("ba", floor)[0] is True          # widened
        assert authorize("tester", floor)[0] is True       # unchanged
        assert authorize("admin", floor)[0] is True        # unchanged
        assert authorize("superadmin", floor)[0] is True   # god-mode
        assert authorize("viewer", floor)[0] is False      # still denied
