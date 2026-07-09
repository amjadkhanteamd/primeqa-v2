"""D-347 constraint-aware fixture solver (the Constraint IR satisfaction operation)."""
from primeqa.generation.fixture import solve_fixture
from primeqa.semantic.formula import parse
from primeqa.semantic.formula.eval import evaluate

_PICK = {"field_type": "picklist",
         "picklist_values": ("Draft", "Approved", "Contract Review"),
         "is_createable": True, "is_updateable": True}
_TEXT = {"field_type": "string", "is_createable": True, "is_updateable": True}
META = {"S__c": _PICK, "A__c": _TEXT, "B__c": _TEXT}


def test_solve_fixture_fires_target_with_no_siblings():
    target = 'ISPICKVAL(S__c, "Approved") && ISBLANK(A__c)'
    asg = solve_fixture(target, [], META)
    assert asg and evaluate(parse(target), asg) is True


def test_solve_fixture_isolates_sibling_off_target():
    # both fire on Approved; the sibling adds an OFF-target blank field -> falsify it
    # (set B__c non-blank) so ONLY the target fires. Uses run-time semantics
    # (absent B__c defaults to blank -> the sibling WOULD fire).
    target = 'ISPICKVAL(S__c, "Approved") && ISBLANK(A__c)'
    sibling = 'ISPICKVAL(S__c, "Approved") && ISBLANK(B__c)'
    asg = solve_fixture(target, [sibling], META)
    assert asg is not None
    assert evaluate(parse(target), asg) is True           # target still fires
    assert evaluate(parse(sibling), asg) is False          # sibling isolated
    assert "B__c" in asg and asg["B__c"] not in (None, "")  # falsified off-target


def test_solve_fixture_unsat_when_sibling_only_touches_target_fields():
    # the sibling fires on a TARGET field (A__c, held blank by the target) and can
    # only be silenced by changing it -> un-isolatable -> UNSAT (None).
    target = 'ISPICKVAL(S__c, "Approved") && ISBLANK(A__c)'
    sibling = 'ISBLANK(A__c)'
    assert solve_fixture(target, [sibling], META) is None


def test_solve_fixture_target_underivable_is_none():
    # an org-state target (ISCHANGED) is not create-derivable -> None.
    assert solve_fixture("ISCHANGED(X__c)", [], META) is None


def test_solve_fixture_skips_unparseable_sibling():
    # an unparseable sibling (TODAY comparison) is left to run-time R1, not guessed.
    target = 'ISPICKVAL(S__c, "Approved") && ISBLANK(A__c)'
    asg = solve_fixture(target, ["D__c < TODAY()"], META)
    assert asg and evaluate(parse(target), asg) is True
