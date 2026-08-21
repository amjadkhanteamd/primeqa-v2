#!/usr/bin/env python3
"""check_decision_numbers.py — guard against decision-number races in the ledger.

Twice now, two workstreams took the same next number and committed
sequentially minutes apart (D-302, repaired by the D-303 note; D-448,
repaired by commit 4aefab4 renaming to D-458). The ledger is append-only,
so a collision can only be repaired by a follow-up note — this check makes
the collision loud before commit instead.

Grammar: a decision heading is a line matching `^## D-<num>` where <num>
is a dotted decimal (458, 110.3, 425.1). Any line starting `## D` that
does not parse this way fails the run (never skipped silently). Headings
that do not start with `## D` (e.g. "## Phase 3 additions") are outside
the grammar and not judged.

Rules — judged only for numbers ABOVE the baseline (the ledger's highest
number on the day this check landed; everything at or below is history,
including its six deliberate design/close pairs and the two repaired
races):

  R1'. A heading whose number is new must not duplicate any number already
       in the file. Skipped numbers are fine (parallel sessions may
       allocate ahead).
  R2'. A heading whose number duplicates an earlier heading must carry a
       continuation marker in its title — the existing vocabulary:
       "(close)", "CLOSE", "(design)", "Result", "REALIZED", "(cont.)".

Exit 0 with a one-line pass when clean; exit 1 with every finding
(both headings, line numbers, the colliding number) otherwise. A missing
or unreadable file is a failure, not a skip.
"""
from __future__ import annotations

import argparse
import re
import sys

BASELINE = "458"
"""Highest decision number in the ledger when this checker landed
(2026-08-21). Nothing at or below it is judged."""

DEFAULT_LEDGER = "docs/architecture/DECISIONS_LOG.md"

_HEADING_RE = re.compile(r"^## D-(?P<num>\d+(?:\.\d+)*)(?P<rest>.*)$")

# The continuation vocabulary already in use in the ledger. Word-bounded /
# parenthesised so e.g. "CLOSED" or "Results" do not accidentally qualify.
_MARKER_RE = re.compile(
    r"\((?:close|design|cont\.)\)|\bCLOSE\b|\bResult\b|\bREALIZED\b"
)


def _num_key(num: str) -> tuple[int, ...]:
    return tuple(int(p) for p in num.split("."))


def check_lines(lines, baseline: str = BASELINE):
    """Pure check. Returns a list of finding strings (empty = clean)."""
    findings = []
    baseline_key = _num_key(baseline)
    seen: dict[str, tuple[int, str]] = {}  # num -> (first line no, heading)

    for lineno, line in enumerate(lines, start=1):
        if not line.startswith("## D"):
            continue
        if line.startswith("## D-NNN"):
            continue  # the preamble's documented format template, not an entry
        m = _HEADING_RE.match(line)
        if m is None:
            findings.append(
                f"UNPARSEABLE heading at line {lineno}: {line.strip()[:120]!r}"
                " — expected '## D-<number>'"
            )
            continue
        num = m.group("num")
        if num not in seen:
            seen[num] = (lineno, line.rstrip())
            continue
        # Duplicate. Judged only above the baseline.
        if _num_key(num) <= baseline_key:
            continue
        if _MARKER_RE.search(m.group("rest")):
            continue  # R2' satisfied: explicit continuation of the same decision
        first_lineno, first_line = seen[num]
        findings.append(
            f"COLLISION on D-{num}: duplicate heading without a continuation "
            f"marker.\n"
            f"  line {first_lineno}: {first_line[:160]}\n"
            f"  line {lineno}: {line.rstrip()[:160]}\n"
            f"  Fix: if this is a follow-up entry for the same decision, add a "
            f"marker ((close)/CLOSE/(design)/Result/REALIZED/(cont.)); if it is "
            f"a new decision, take the next free number."
        )
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default=DEFAULT_LEDGER)
    ap.add_argument("--baseline", default=BASELINE,
                    help="highest pre-existing number; nothing at or below "
                         "it is judged (default: %(default)s)")
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        print(f"FAIL: cannot read {args.file}: {exc}")
        return 1

    findings = check_lines(lines, baseline=args.baseline)
    if findings:
        for f in findings:
            print(f)
        return 1
    print(f"decision numbers OK ({args.file}, baseline D-{args.baseline})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
