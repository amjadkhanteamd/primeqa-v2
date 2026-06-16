"""Unit tests for the Phase-3 environment-scope primitive (D-245).

DB-free: ``EnvironmentRepository.is_environment_accessible`` only consults
``list_environments`` (the chokepoint), which we monkeypatch to a fixed
accessible set. Proves membership + int-coercion + None handling.
"""
from primeqa.core.repository import EnvironmentRepository


class _Env:
    def __init__(self, i):
        self.id = i


def _repo(monkeypatch, accessible_ids):
    repo = EnvironmentRepository.__new__(EnvironmentRepository)  # bypass __init__ (no db)
    monkeypatch.setattr(repo, "list_environments",
                        lambda t, u, r: [_Env(i) for i in accessible_ids])
    return repo


def test_in_scope_allowed(monkeypatch):
    repo = _repo(monkeypatch, [5, 7])
    assert repo.is_environment_accessible(1, 2, "tester", 5) is True
    assert repo.is_environment_accessible(1, 2, "tester", 7) is True


def test_out_of_scope_denied(monkeypatch):
    repo = _repo(monkeypatch, [5, 7])
    assert repo.is_environment_accessible(1, 2, "tester", 9) is False


def test_none_denied(monkeypatch):
    repo = _repo(monkeypatch, [5])
    assert repo.is_environment_accessible(1, 2, "tester", None) is False


def test_str_coercion(monkeypatch):
    repo = _repo(monkeypatch, [5])
    assert repo.is_environment_accessible(1, 2, "tester", "5") is True
    assert repo.is_environment_accessible(1, 2, "tester", "abc") is False


def test_empty_accessible_set_denies_all(monkeypatch):
    repo = _repo(monkeypatch, [])
    assert repo.is_environment_accessible(1, 2, "viewer", 5) is False


def test_admin_scoping_is_delegated_to_list_environments(monkeypatch):
    # is_environment_accessible doesn't special-case admin — it trusts
    # list_environments (which returns ALL envs for admin/superadmin).
    repo = _repo(monkeypatch, [1, 2, 3, 99])  # simulate admin's full set
    assert repo.is_environment_accessible(1, 1, "admin", 99) is True
