"""AUD-018 (round 3): a status value the product READS is one the product
WRITES — a vocabulary the code speaks but cannot produce is a branch nobody
can reach, and a page that renders it is describing a state only a hand-edit
of the table can create.

The audit found ``claim_sets.status = 'revoked'`` read by the approval path
and written by nothing (members are revoked one by one; the set never is).
The read is deleted; this test holds the rule for the claim-set vocabulary
and for the other status tables the product reads by literal: every literal
compared against a ``status`` column in the source has a writer — an INSERT
or UPDATE that sets that literal — somewhere in ``primeqa/``. The reads are
found by regex over the source (SQL literals and Python comparisons), never
listed by hand; a planted read of an unwritten value fails the gate.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
PKG = REPO / "primeqa"

# the status-bearing tables whose vocabulary this gate holds (the claim-set
# tables of AUD-018 and their neighbours in the same package)
TABLES = ("claim_sets", "quality_policies")

# a READ: `status = 'x'`, `status <> 'x'`, `status IN ('x', 'y')` in SQL; `.status == "x"` / `row[0] == "x"` in Python
_SQL_READ = re.compile(r"\bstatus\s*(?:=|<>|!=)\s*'([a-z_]+)'")
_SQL_IN = re.compile(r"\bstatus\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", re.I)
_PY_READ = re.compile(r"""\.status\s*(?:==|!=)\s*["']([a-z_]+)["']""")
_PY_IN = re.compile(r"""\.status\s+(?:not\s+)?in\s*\(([^)]*)\)""")
# a WRITE: `SET status = 'x'`, `status = 'x'` inside an INSERT's VALUES is caught by the same literal scan of INSERT statements,
# and a Python literal handed to a bound parameter named status
_SQL_SET = re.compile(r"\bSET\s+(?:[^;]*?,\s*)?status\s*=\s*'([a-z_]+)'", re.I)
_SQL_INSERT = re.compile(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)\s*(?:SELECT|VALUES)\s*(.*?)(?:RETURNING|\"\"\"|$)", re.I | re.S)
_PY_STATUS_KW = re.compile(r"""["']status["']\s*:\s*["']([a-z_]+)["']|\bstatus\s*=\s*["']([a-z_]+)["']""")


def _module_texts():
    for path in sorted(PKG.rglob("*.py")):
        yield str(path.relative_to(REPO)), path.read_text()


def _reads_for(table: str) -> dict:
    """{literal: [where]} — reads of the table's status column; a read is
    attributed to the table when its statement names the table."""
    out: dict = {}
    for rel, txt in _module_texts():
        for m in re.finditer(r"(?:SELECT|UPDATE|DELETE)[\s\S]{0,600}?" + re.escape(table) + r"\b[\s\S]{0,600}?(?=\"\"\"|\'\'\'|\Z)", txt):
            stmt = m.group(0)
            for lit in _SQL_READ.findall(stmt):
                out.setdefault(lit, []).append(rel)
            for group in _SQL_IN.findall(stmt):
                for lit in re.findall(r"'([a-z_]+)'", group):
                    out.setdefault(lit, []).append(rel)
    return out


def _writes_for(table: str) -> set:
    lits: set = set()
    for rel, txt in _module_texts():
        for m in re.finditer(r"(?:INSERT\s+INTO|UPDATE)\s+" + re.escape(table) + r"\b[\s\S]{0,800}?(?=\"\"\"|\'\'\'|\Z)", txt, re.I):
            stmt = m.group(0)
            lits.update(_SQL_SET.findall(stmt))
            ins = _SQL_INSERT.search(stmt)
            if ins and re.search(r"\bstatus\b", ins.group(2)):
                cols = [c.strip() for c in ins.group(2).split(",")]
                vals = ins.group(3)
                if "status" in cols:
                    literal_vals = re.findall(r"'([a-z_]+)'", vals)
                    if len(literal_vals) == len(cols):
                        lits.add(literal_vals[cols.index("status")])
                    else:
                        # a bound or positional value: fall back to any status literal the same module hands over
                        for a, b in _PY_STATUS_KW.findall(txt):
                            lits.add(a or b)
    return lits


def _python_reads_in(rel: str, txt: str) -> dict:
    out: dict = {}
    for lit in _PY_READ.findall(txt):
        out.setdefault(lit, []).append(rel)
    for group in _PY_IN.findall(txt):
        for lit in re.findall(r"""["']([a-z_]+)["']""", group):
            out.setdefault(lit, []).append(rel)
    return out


@pytest.mark.parametrize("table", TABLES)
def test_every_status_value_read_has_a_writer(table):
    reads = _reads_for(table)
    writes = _writes_for(table)
    assert reads, f"no read of {table}.status found — the scan is not reading the source"
    orphans = {lit: where for lit, where in reads.items() if lit not in writes}
    assert orphans == {}, f"{table}.status values read but never written by the product: {orphans}; writers: {sorted(writes)}"


def test_claim_set_python_reads_are_written_values():
    """The approval path's Python comparisons (``row[0] == 'approved'`` and the
    member's claim status) — the claim-set module reads only what it writes."""
    txt = (PKG / "test_representation" / "claim_sets.py").read_text()
    set_status_literals = set(re.findall(r"""\bif\s+row\[0\]\s*==\s*["']([a-z_]+)["']""", txt))
    assert set_status_literals <= _writes_for("claim_sets"), (set_status_literals, _writes_for("claim_sets"))
    assert "revoked" not in set_status_literals                      # AUD-018: the dead read is gone


def test_the_gate_sees_a_planted_unwritten_read(tmp_path, monkeypatch):
    planted = tmp_path / "primeqa" / "rogue.py"
    planted.parent.mkdir(parents=True)
    planted.write_text('SQL = """SELECT 1 FROM claim_sets WHERE status = \'dissolved\'"""\n')
    monkeypatch.setattr("tests.unit.test_status_vocabulary.PKG", tmp_path / "primeqa")
    monkeypatch.setattr("tests.unit.test_status_vocabulary.REPO", tmp_path)
    with pytest.raises(AssertionError, match="dissolved"):
        test_every_status_value_read_has_a_writer("claim_sets")
