"""D-460 / SAD A10 worker-interpretation boundary — structural guards
(3A-4 §g: the 'verdict' string-ban, extended and merge-gated).

The browser worker produces ENGINE OBSERVATIONS only. These guards make
the boundary structural: the word "verdict" never appears in a worker
module, and no worker module imports the interpretation, knowledge, or
test-representation substrates (the worker receives pinned artifacts
and manifest payloads — nothing else)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BW = Path(__file__).parents[2] / "primeqa" / "browser_worker"
_SOURCES = [p for p in _BW.rglob("*.py")]


def test_worker_sources_exist():
    assert len(_SOURCES) >= 5      # the guard must be guarding something


def test_verdict_never_appears_in_worker_modules():
    offenders = [p.name for p in _SOURCES
                 if re.search(r"verdict", p.read_text("utf-8"),
                              re.IGNORECASE)]
    assert offenders == []


def test_worker_never_imports_interpretation_side_substrates():
    banned = re.compile(
        r"^\s*(?:from|import)\s+primeqa\.(?:interpretation|knowledge|"
        r"test_representation|intelligence)\b", re.MULTILINE)
    offenders = [p.name for p in _SOURCES
                 if banned.search(p.read_text("utf-8"))]
    assert offenders == []


def test_processor_is_the_only_verdict_home():
    # The claim-grain verdict writer exists exactly where the LLD says.
    from primeqa.interpretation import ui_conformance
    assert callable(ui_conformance.process_job)
    assert callable(ui_conformance.decide_verdict)
