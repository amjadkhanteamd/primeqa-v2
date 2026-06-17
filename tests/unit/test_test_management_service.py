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


# --- _extract_acceptance_criteria --------------------------------------------
# Guards the duplication bug: a Jira import with no dedicated "acceptance" custom
# field used to fall back to `description`, copying the whole description into
# acceptance_criteria so the requirement detail page rendered it twice. The
# fallback must now be None ("no AC"); the description still lives in
# jira_description.

def test_extract_ac_returns_custom_acceptance_field_when_present():
    fields = {"description": "the desc", "customfield_acceptance": "AC text"}
    assert _Svc._extract_acceptance_criteria(fields) == "AC text"


def test_extract_ac_matches_acceptance_key_case_insensitively():
    fields = {"Acceptance Criteria": "given/when/then"}
    assert _Svc._extract_acceptance_criteria(fields) == "given/when/then"


def test_extract_ac_falls_back_to_none_not_description():
    # No acceptance field -> None (the bug returned fields["description"]).
    fields = {"summary": "S", "description": "a long description"}
    assert _Svc._extract_acceptance_criteria(fields) is None


def test_extract_ac_empty_fields_returns_none():
    assert _Svc._extract_acceptance_criteria({}) is None


def test_extract_ac_ignores_empty_acceptance_field():
    # An acceptance-named field that is falsy is not a real AC -> fall through.
    fields = {"customfield_acceptance": "", "description": "d"}
    assert _Svc._extract_acceptance_criteria(fields) is None
