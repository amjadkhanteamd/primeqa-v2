# VERIFICATION — Verdict Semantics (the D-465 fix slice)

Executed 2026-08-28. Implements `LLD_VERDICT_SEMANTICS.md`: PASS
requires positive evidence that the rule ran. Local and scratch except
for ONE read-only production read (the P-1 re-decide, §A below) —
**no production row was written and no Railway act was performed.**

Re-runnable: `tests/unit/test_representation/test_verdict_semantics.py`
(pure, merge-gated); `tests/integration/test_prod_vault.py::test_f_*`
(scratch, run-set pin); `tests/browser_worker/test_run_set_determinism.py`
(gated on `SPIKE_BROWSER=1`).

---

## THE EVIDENCE LAW (acceptance item 9)

Stated verbatim in the module docstring of
`primeqa/interpretation/ui_conformance.py` and pinned by a test:

> **A verdict asserts only what the STORED EVIDENCE attests.** Offline
> analysis ABOUT a record is not evidence WITHIN it: we can read the
> pinned engine artifact and work out which rules it ships disabled, and
> that analysis belongs in a report — but it cannot promote or demote a
> verdict, because the observation itself attests nothing about those
> rules.

This is why `legacy_unattested` holds even where the disabled subset is
offline-identifiable. D-465 §9(b) named P-1's 12 never-executed rules by
parsing the vendored engine — that naming is legitimate **reporting**
and appears in the transcript; it may not become a verdict reason,
because P-1's observations attest nothing about those rules. The record,
not our knowledge of the record, is what a verdict may cite.

The same docstring now carries the acquit half of the arm-H posture:
the processor "refuses to convict" AND "refuses to acquit on the same
ignorance".

## What changed

| file | change |
|---|---|
| `interpretation/ui_conformance.py` | new `_decide_non_violation`: no-mapped-violation is no longer PASS by default. Order: engine INCOMPLETE → attestation presence → run-set membership → attested pass → inapplicable → unattested. Five named NOT_DETERMINED reasons. Evidence law in the docstring. |
| `execution_engine/ui_manifest.py` | new `engine_run_set()`; pins gain `engine_run_set` + `engine_run_set_hash`. Refuses to build when the release has no bindings ("an unpinned run set cannot attest a PASS"). |
| `browser_worker/manifest.py` | `enqueue_for_manifest` copies the pinned run set into the job payload — the worker receives it as DATA. |
| `browser_worker/consume.py` | threads `run_set` from the job payload into every scan. |
| `browser_worker/spike.py` | `axe.run(document, {runOnly: {type:'rule', values: ids}})` when a run set is given; observation retains `passes_ids`, `inapplicable_ids`, `run_set` (**rule ids only**). |

**D-460 boundary.** The worker never derives the run set — it cannot
read S5. The builder resolves it; the manifest pins it; the job payload
carries it. The observation delta is rule IDs only, the same additive
class as the approved `incomplete` passthrough (4244ed1). Both boundary
guards stay green — and the string-ban caught this work honestly: citing
the LLD's filename inside worker modules tripped it, and the comments
were reworded rather than the guard weakened.

## Acceptance §e, item by item

**1. Disabled/unrun bound rule → NOT_DETERMINED, not PASS.** A rule
outside the pinned run set yields `rule_not_executed`; the same rule
inside the run set and attested yields PASS. *(unit)*

**2. Incomplete → NOT_DETERMINED with candidates under AUTO; the SAME
observation under `HUMAN_WITH_CANDIDATE` → NEEDS_HUMAN with the same
candidates.** *(unit — both rows of the §a matrix)* Note the precedence
pinned by test: an `incomplete` for the rule wins even when a pass
attestation is also present, because the engine said it could not
determine that rule on that surface.

**3. Inapplicable → `rule_inapplicable`, never PASS.** *(unit)*

**4. Genuine attested pass → PASS**, with the attesting engine id in the
basis (`attested_by`), so the verdict is re-verifiable from the evidence
bundle. *(unit + live below)*

**5. In the run set but unattested → `rule_unattested`.** *(unit)*

**6. Re-decide of P-1 reproduces the published decomposition** — §A.

**7. Live end-to-end on fixtures with attestation** — §B.

**8. Boundary guards + full merge-gate suite green** — §C.

**9. The evidence law** — above.

**10. Determinism regression** — §B.

---

## §A — P-1 RE-DECIDE (read-only against production)

P-1's stored observations, claim_set members and engine bindings were
read from production and `decide_verdict` was run over them in memory.
**Production verdict rows were not rewritten**: this branch is unmerged,
so the actual re-decide is a post-merge gated act. This proves the
outcome without mutating prod from a review branch.

```
members re-decided: 144
verdicts: {'NOT_DETERMINED': 142, 'FAIL': 2}
NOT_DETERMINED reasons: {'legacy_unattested': 139, 'engine_incomplete': 3}
matches the published decomposition: True
PASS verdicts: 0  (never a retroactive pass)
```

Exactly the LLD's prediction and exactly `VERIFICATION_P1.md` §9:
**2 FAIL / 3 engine_incomplete / 139 legacy_unattested / 0 PASS.**
The 2 FAILs survive because a violation IS positive evidence and is
retained in full; the 3 `engine_incomplete` are recoverable because
`incomplete` has been retained since 4244ed1; everything else is
honestly unattested.

## §B — Determinism + attested pass (real browser, fixtures)

Two scans of `fixture-bad.html` under the same pinned run set
`[audio-caption, image-alt, label, region, target-size]`:

```
scan A: run_set=['audio-caption','image-alt','label','region','target-size']
        violations=['image-alt','label']  passes_ids=['region','target-size']
        inapplicable_ids=['audio-caption']
scan B: identical on all four

run_set identical:      True
passes_ids identical:   True
inapplicable identical: True
```

**The run-set pin is now part of what determinism means** — two runs
agree not only on findings but on WHICH RULES WERE EVALUATED.

`target-size` — a rule axe 4.13.0 ships `enabled:false`, and one of the
six that produced P-1's silent passes — **actually ran** under the pin
and is attested. A companion test pins the pre-fix behaviour as the
regression it was: with no run set, `target-size` appears in no engine
bucket at all and `run_set` is `null`.

Verdicts over the attested observation:

```
image-alt        -> FAIL             (violation)
label            -> FAIL             (violation)
region           -> PASS             (attested_by ['region'])
target-size      -> PASS             (attested_by ['target-size'])
audio-caption    -> NOT_DETERMINED   (rule_inapplicable)
aria-hidden-body -> NOT_DETERMINED   (rule_not_executed)   [outside the run set]
```

## §C — Suites

- **Unit: 4,896 passed** (was 4,885; +11). Two pre-existing tests changed meaning and were
  updated rather than deleted: the 3A-4 "PASS on no mapped violation"
  test is now
  `test_no_mapped_violation_without_attestation_is_not_a_pass`
  (asserting `legacy_unattested`), with a new sibling asserting PASS
  **with** attestation. That pair is the regression record for the
  defect.
- **DB-real: 6 passed** on the prod-vault suite including the new
  `test_f_manifest_pins_the_engine_run_set` — 72 bound ids pinned, hash
  reproducible, and the set present in the job payload.
- **Browser-gated: 3 passed** (`SPIKE_BROWSER=1`).
- Worker boundary guards (verdict string-ban, import-ban): green.

## Residual, stated plainly

- **P-1's production verdict rows still read 142 PASS / 2 FAIL.** They
  are corrected by re-running the processor after this merges — a gated
  act, not part of this slice. `VERIFICATION_P1.md` §9 and D-465 already
  carry the honest decomposition, so the record is not misleading in the
  meantime.
- **The first attested run becomes the programme's first true
  conformance baseline.** Comparisons against P-1 will read
  `NOT_COMPARABLE (indeterminate_side)` rather than a false
  `STILL_PASSING` — correct, since P-1 established the production path,
  not a baseline.
- Non-goals untouched: no Aura ownership markers, no `landed_url`, no
  `ran_by`, no evidence-bucket separation, no re-run of P-1 (all their
  own FIX-PLAN entries).
