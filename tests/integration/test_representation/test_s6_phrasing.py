"""Integration: S6 phrasing cache — set_phrasing + get_or_phrase (D-117).

The substrate's pure `set_phrasing` writer + the v1 `get_or_phrase` cache-or-phrase
helper, over the `s6_interpretations` store. The enricher's `llm_call` is stubbed
(no credits). The `20260601_0020` phrasing-column migration applies via the
package's `alembic upgrade head` setup. Reuses the per-test transactional
`session` fixture.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from primeqa.intelligence import interpretation_phrasing as ip
from primeqa.interpretation.model import Cause, Interpretation
from primeqa.interpretation.result_store import (
    S6Interpretation, persist_interpretation, set_phrasing)


def _interp() -> Interpretation:
    return Interpretation(
        run_id=uuid4(), recipe_id=uuid4(), claim_test_id=uuid4(),
        outcome="failed", verdict="prohibition_not_enforced", attribution="x",
        cause=Cause(cause_kind="enforcement_gap", vr_name="VR_A"))


def _fake_resp(parsed):
    r = MagicMock()
    r.parsed_content = parsed
    r.model = "haiku"
    r.prompt_version = "interpretation_phrasing@v1"
    return r


_GOOD = {"headline": "h", "explanation": "e"}
_PATCH = "primeqa.intelligence.interpretation_phrasing.llm_call"


def test_set_phrasing_writes_the_column(session):
    i = _interp()
    persist_interpretation(session, i)
    set_phrasing(session, i.run_id,
                 {"headline": "H", "explanation": "E", "model": "m"})
    session.flush()
    row = session.query(S6Interpretation).filter_by(run_id=i.run_id).one()
    assert row.phrasing["headline"] == "H"


def test_get_or_phrase_cache_miss_phrases_and_caches(session):
    i = _interp()
    persist_interpretation(session, i)
    session.flush()
    with patch(_PATCH, return_value=_fake_resp(_GOOD)) as spy:
        out = ip.get_or_phrase(session, i, tenant_id=1, api_key="k")
    assert out["headline"] == "h"
    assert spy.call_count == 1
    row = session.query(S6Interpretation).filter_by(run_id=i.run_id).one()
    assert row.phrasing["headline"] == "h"             # cached on the row


def test_get_or_phrase_cache_hit_skips_llm(session):
    i = _interp()
    persist_interpretation(session, i)
    set_phrasing(session, i.run_id, {"headline": "cached", "explanation": "e"})
    session.flush()
    with patch(_PATCH) as spy:
        out = ip.get_or_phrase(session, i, tenant_id=1, api_key="k")
    assert out["headline"] == "cached"
    spy.assert_not_called()                             # cache hit -> no LLM call


def test_get_or_phrase_failure_caches_nothing(session):
    i = _interp()
    persist_interpretation(session, i)
    session.flush()
    with patch(_PATCH, return_value=_fake_resp("not a dict")):
        out = ip.get_or_phrase(session, i, tenant_id=1, api_key="k")
    assert out is None
    row = session.query(S6Interpretation).filter_by(run_id=i.run_id).one()
    assert row.phrasing is None                         # best-effort: nothing cached
