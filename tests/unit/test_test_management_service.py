"""Regression: TestManagementService must instantiate cleanly.

Guards the bug where the v1-retirement refactor (D-221.4, commit 7fd518f6) removed
the ``test_case_repo`` / ``suite_repo`` / ``review_repo`` constructor params but
left ``self.test_case_repo = test_case_repo`` (etc.) in the body — so EVERY
``TestManagementService(...)`` raised ``NameError: name 'test_case_repo' is not
defined`` at runtime, breaking manual requirement create + Jira import. No unit
test exercised the constructor, so it shipped silently.
"""
import pytest

# Alias the import so pytest doesn't try to collect the service class itself
# (its name starts with "Test").
from primeqa.test_management.service import TestManagementService as _Svc


def test_instantiates_with_the_two_required_repos():
    # The constructor must NOT reference any undefined name. Plain sentinels are
    # enough — __init__ only stores them.
    svc = _Svc(object(), object())
    assert svc.section_repo is not None
    assert svc.requirement_repo is not None
    assert svc.activity_repo is None


def test_activity_repo_is_optional_and_kept():
    sentinel = object()
    svc = _Svc(object(), object(), activity_repo=sentinel)
    assert svc.activity_repo is sentinel


@pytest.mark.parametrize("section_repo,requirement_repo", [
    (None, object()),
    (object(), None),
    (None, None),
])
def test_missing_required_repo_raises_typeerror(section_repo, requirement_repo):
    with pytest.raises(TypeError):
        _Svc(section_repo, requirement_repo)
