"""UI Pass 2 — live sync progress bar (Option A) red-proofs.

Pins the PURE pieces: the 11-phase order (the bar's structure lane), the
enrichment done/total shaping (the enrichment lane), and the exact N-of-11
server-render expression the template uses. The poll/DB wiring + the in-browser
JS update are covered by the live red-proofs.
"""
import pytest
from jinja2 import Environment

pytestmark = pytest.mark.unit

from primeqa.metadata_bridge.s1_sync_console import phase_order
from primeqa.sync.readiness import count_enrichment_progress


# --- phase_order: the single source for the bar's 11 phases ------------------

def test_phase_order_is_entity_order_11():
    po = phase_order()
    assert po == ["Object", "PicklistValueSet", "PicklistValue", "Field",
                  "RecordType", "Layout", "ValidationRule", "Profile",
                  "PermissionSet", "User", "Flow"]
    assert len(po) == 11


# --- count_enrichment_progress shaping (the enrichment lane) ------------------

class _FakeResult:
    def __init__(self, row): self._row = row
    def fetchone(self): return self._row


class _FakeSession:
    def __init__(self, row): self._row = row
    def execute(self, *_a, **_k): return _FakeResult(self._row)


def test_enrichment_progress_shapes_done_total():
    out = count_enrichment_progress(_FakeSession((340, 512)), "org-uuid")
    assert out == {"done": 340, "total": 512}


def test_enrichment_progress_zero_total_is_valid():
    assert count_enrichment_progress(_FakeSession((0, 0)), "o") == {"done": 0, "total": 0}


def test_enrichment_progress_none_row_falls_back():
    assert count_enrichment_progress(_FakeSession(None), "o") == {"done": 0, "total": 0}


def test_enrichment_progress_null_counts_coerce_to_zero():
    assert count_enrichment_progress(_FakeSession((None, None)), "o") == {"done": 0, "total": 0}


# --- the exact N-of-11 server-render expression (the structure lane) ----------
# Mirrors detail.html: _dp = (sync_phases.index(_lcp)+1) if (lcp in phases) else 0.
# HONEST: it is the COMPLETED-phase count from last_completed_phase — never an
# inferred currently-running phase.

_EXPR = ("{% set _np=(sync_phases|length) or 11 %}"
         "{% set _dp=(sync_phases.index(_lcp)+1) if (sync_phases and _lcp and _lcp in sync_phases) else 0 %}"
         "{{ _dp }}/{{ _np }}")


@pytest.mark.parametrize("lcp,expected", [
    ("Object", "1/11"),
    ("Field", "4/11"),
    ("ValidationRule", "7/11"),
    ("Flow", "11/11"),     # last structural phase -> Structure complete
    (None, "0/11"),        # not started / no recorded phase
    ("", "0/11"),
    ("bogus_phase", "0/11"),  # unknown phase name -> 0 (never crashes/guesses)
])
def test_n_of_11_from_last_completed_phase(lcp, expected):
    tmpl = Environment().from_string(_EXPR)
    assert tmpl.render(sync_phases=phase_order(), _lcp=lcp) == expected
