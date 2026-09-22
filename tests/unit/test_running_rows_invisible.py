"""Round 4, part A (AUD-028): a RUNNING run row is invisible to the product.

The run row now exists BEFORE the first record is provisioned (outcome
``running``, ``finished_at`` NULL) so an interrupted run leaves a failed run,
never an orphan. Before this, no row existed until finalize — so the visibility
every reader had was "finalized runs only". This gate keeps that visibility:
EVERY SQL statement in ``primeqa/`` that reads ``s4_execution_runs`` (FROM or
JOIN) carries the predicate ``finished_at IS NOT NULL`` (``result_store.FINALIZED``).
The store's own writers (open / finalize / interrupt / reap) live in
``result_store.py``, which is the one file excluded.

The sweep reads string constants through the AST — implicit concatenation and
f-strings included — so a reader added tomorrow without the predicate fails the
day it appears. The gate is shown able to fail: a planted module with a bare
reader is reported, and one with the predicate is not.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
PKG = REPO / "primeqa"
READS = re.compile(r"\b(FROM|JOIN)\s+s4_execution_runs\b", re.I)
EXCLUDED = {"result_store.py"}   # the store's own writers
PREDICATE = "finished_at IS NOT NULL"


def _constants(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node, node.value
    elif isinstance(node, ast.JoinedStr):
        yield node, "".join(v.value for v in node.values
                            if isinstance(v, ast.Constant) and isinstance(v.value, str))
    else:
        for child in ast.iter_child_nodes(node):
            yield from _constants(child)


def readers_without_predicate(root: pathlib.Path) -> list[str]:
    """Every ``file:line`` whose SQL string reads the run table without the
    finalized predicate."""
    out = []
    for path in sorted(root.rglob("*.py")):
        if path.name in EXCLUDED:
            continue
        seen = set()
        for node, s in _constants(ast.parse(path.read_text())):
            if not READS.search(s) or node.lineno in seen:
                continue
            seen.add(node.lineno)
            if PREDICATE not in s:
                out.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    return out


def test_every_reader_of_the_run_table_sees_finalized_rows_only():
    bare = readers_without_predicate(PKG)
    assert bare == [], (
        "these statements read s4_execution_runs without `finished_at IS NOT NULL` — "
        "a running row (the write-ahead of round 4) would be read as a run:\n  "
        + "\n  ".join(bare))


def test_the_predicate_is_the_store_s_own_word():
    from primeqa.execution_engine import result_store as rs
    assert rs.FINALIZED == PREDICATE
    assert rs.RUNNING == "running"


def test_the_sweep_reads_enough_statements():
    """The gate is worth nothing if it reads three strings; the product reads
    the run table from every substrate that consumes evidence."""
    total = 0
    for path in sorted(PKG.rglob("*.py")):
        if path.name in EXCLUDED:
            continue
        seen = set()
        for node, s in _constants(ast.parse(path.read_text())):
            if READS.search(s) and node.lineno not in seen:
                seen.add(node.lineno)
                total += 1
    assert total >= 35, f"the sweep found {total} reader statements — it did not read the package"


def test_a_planted_bare_reader_is_reported_and_a_guarded_one_is_not(tmp_path):
    pkg = tmp_path / "planted"
    pkg.mkdir()
    (pkg / "bare.py").write_text(
        'SQL = ("SELECT run_id FROM s4_execution_runs r "\n'
        '       "WHERE r.environment_id = :env")\n')
    (pkg / "fstr.py").write_text(
        'T = "x"\nSQL = f"SELECT 1 FROM s4_execution_runs WHERE outcome = {T}"\n')
    (pkg / "guarded.py").write_text(
        'SQL = ("SELECT run_id FROM s4_execution_runs r "\n'
        '       "WHERE r.environment_id = :env AND r.finished_at IS NOT NULL")\n')
    (pkg / "joined.py").write_text(
        'SQL = ("SELECT 1 FROM s6_interpretations i "\n'
        '       "JOIN s4_execution_runs r ON r.run_id = i.run_id AND r.finished_at IS NOT NULL")\n')
    (pkg / "result_store.py").write_text(
        'SQL = "UPDATE s4_execution_runs SET outcome = 1 FROM s4_execution_runs x"\n')
    got = readers_without_predicate(pkg)
    assert got == ["planted/bare.py:1", "planted/fstr.py:2"], got
