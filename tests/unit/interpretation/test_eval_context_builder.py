"""D-439: the attribution-side EvalContext builder pins.

The one that matters: MORE THAN ONE mutation step → prior-state ambiguity →
no org-state pair (functions stay NonEvaluable). Plus create/update shapes
and the duck-typed resolver pickup.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from primeqa.execution_engine.evidence import (
    CleanupRecord, CreateAttemptEvidence, RunEvidence, UpdateAttemptEvidence)
from primeqa.interpretation.attribution import _eval_context

pytestmark = pytest.mark.unit

_T = datetime(2026, 8, 10, tzinfo=timezone.utc)


def _create(values, ordinal=0):
    return CreateAttemptEvidence(
        step_id=f"create{ordinal}", ordinal=ordinal, sobject="Opportunity",
        field_values=values, http_status=201, success=True, error_code=None,
        message=None, rejection_body=(), matched=None,
        cleanup=CleanupRecord(attempted=False), started_at=_T, finished_at=_T,
        duration_ms=1)


def _update(changes, ordinal=1):
    return UpdateAttemptEvidence(
        step_id=f"update{ordinal}", ordinal=ordinal, sobject="Opportunity",
        record_id="001xx0000000001", field_changes=changes, http_status=204,
        success=True, error_code=None, message=None, rejection_body=(),
        matched=None, started_at=_T, finished_at=_T, duration_ms=1)


def _run(steps):
    return RunEvidence(
        run_id=uuid4(), recipe_id=uuid4(), recipe_version_seq=1,
        claim_test_id=uuid4(), claim_version_seq=None, environment_id=59,
        api_choice="rest", outcome="failed", started_at=_T, finished_at=_T,
        steps=tuple(steps))


class _S1WithResolver:
    def record_type_developer_name(self, rid):
        return "PLS_BM_Enterprise"


class _S1Bare:
    pass


def test_update_context_carries_the_setup_create_as_prior_state():
    create = _create({"Stage__c": "Draft", "Amount__c": 5})
    update = _update({"Stage__c": "Approved"})
    ctx = _eval_context(update, _run([create, update]), _S1Bare())
    assert ctx.is_create is False
    assert ctx.prior_state == {"Stage__c": "Draft", "Amount__c": 5}


def test_create_context_has_no_prior_state():
    create = _create({"Amount__c": 10001})
    ctx = _eval_context(create, _run([create]), _S1Bare())
    assert ctx.is_create is True
    assert ctx.prior_state is None


def test_more_than_one_mutation_step_drops_the_pair():
    """Prior-state ambiguity → no org-state context — the pinned guard."""
    create = _create({"Stage__c": "Draft"})
    u1 = _update({"Stage__c": "A"}, ordinal=1)
    u2 = _update({"Stage__c": "B"}, ordinal=2)
    ctx = _eval_context(u2, _run([create, u1, u2]), _S1Bare())
    assert ctx.is_create is None
    assert ctx.prior_state is None


def test_resolver_is_picked_up_duck_typed():
    create = _create({})
    ctx = _eval_context(create, _run([create]), _S1WithResolver())
    assert ctx.record_type_developer_name is not None
    assert ctx.record_type_developer_name("012") == "PLS_BM_Enterprise"
    ctx_bare = _eval_context(create, _run([create]), _S1Bare())
    assert ctx_bare.record_type_developer_name is None
