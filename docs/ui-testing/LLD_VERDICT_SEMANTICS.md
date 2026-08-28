# LLD — Verdict Semantics: the vacuous-green law applied to conformance

Status: DESIGN (this commit); implementation follows on its own GO.
Branch: `fix-verdict-semantics` (from main @6002d16, P-1 closed).
Opened by D-465's material caveat. Derives from: D-460 (worker–
interpretation boundary), D-461 (manifest pins), the 3A-4 LLD (verdict
semantics §c), and the standing platform posture that a mechanism which
did not run cannot decide an outcome.

**The defect, stated once.** P-1 produced 142 PASS. Of those, 3 were
engine INCOMPLETE, 12 were rules the engine never executed, and ≥51
cannot correspond to any retained engine pass. `PASS` was being emitted
on the ABSENCE of a violation rather than on the PRESENCE of evidence
that the rule ran and found nothing. The 3A-4 module docstring says "a
processor that cannot prove what it saw refuses to convict"; the
converse was unimplemented — it also **acquitted** on the same
ignorance. This slice makes PASS require positive evidence.

---

## a. INCOMPLETE handling

**Rule:** for a bound rule, an engine `incomplete` entry yields
**`NOT_DETERMINED`** with `reason = "engine_incomplete"` and the
engine's incomplete nodes attached as candidates. **Never PASS.**

Today `decide_verdict`'s AUTO branch reads only
`obs["violations"]`; `incomplete` is consulted solely on the
`HUMAN_WITH_CANDIDATE` branch, which is unreachable while the catalogue
is 72/72 `AUTO`. The change is one branch in
`interpretation/ui_conformance.py`, evaluated BEFORE the
no-mapped-violation → PASS path:

```
AUTO branch, in order:
  1. no engine binding for the rule            -> NOT_DETERMINED (unmapped_dependency)   [today]
  2. missing fingerprint                       -> NOT_DETERMINED (missing_fingerprint)   [today]
  3. mapped VIOLATION present                  -> FAIL / NOT_DETERMINED(unresolvable)    [today]
  4. mapped INCOMPLETE present                 -> NOT_DETERMINED (engine_incomplete)     [NEW]
  5. rule not attested as run (§b)             -> NOT_DETERMINED (rule_not_executed |
                                                  rule_unattested | legacy_unattested)   [NEW]
  6. attested pass                             -> PASS
```

**Interaction with `HUMAN_WITH_CANDIDATE`.** `incomplete` IS the
Class-3 feed the spike identified, and both branches now consume it —
they differ in what the answer MEANS, not in the input:

| capability | on a mapped `incomplete` | why |
|---|---|---|
| `AUTO` | `NOT_DETERMINED` (engine_incomplete), candidates attached | the engine was supposed to decide and could not; nobody is queued to look |
| `HUMAN_WITH_CANDIDATE` | `NEEDS_HUMAN`, candidates attached (unchanged) | a human IS the decider; the candidates are their queue |

So the same engine signal routes to "unproven" or "awaiting a human"
purely by whether a human decider is declared. No rule silently changes
capability; `NEEDS_HUMAN` remains unfired in production and unreachable
until a non-AUTO rule is bound (recorded in D-465 §10.6).

## b. RULE-EXECUTION PROVENANCE — the vacuous-pass class

**The law: PASS requires POSITIVE evidence that the rule ran.** Absence
of a violation is not evidence of conformance when the rule may never
have executed. Two mechanisms, both required:

**b.1 — Pin the run set, and run exactly it.** `axe.run(document)` with
no options uses axe's default set and silently skips every rule marked
`enabled: false` (9 such rules in the vendored 4.13.0; 6 of them bound
to release 2). The engine call becomes:

```
axe.run(document, {runOnly: {type: "rule", values: [<bound engine ids>]}})
```

**Where the run set comes from — the D-460 constraint.** The worker must
NOT consult S5 (the boundary guard forbids importing `knowledge`). The
bound engine ids therefore ride the **manifest** as a pin, computed by
the manifest builder (which already reads the catalogue release):
`pins.engine_run_set` = the sorted engine ids bound to the release's
rules, plus `pins.engine_run_set_hash`. The worker reads its manifest
and passes the list through — the same posture as every other pin. This
also strengthens D-461's determinism claim, which today pins the engine
VERSION but not the engine RUN SET.

*Consequence made explicit:* a rule disabled in the engine but named in
`runOnly` is re-enabled by that call — which is the intent (the
catalogue, not the engine's default, decides what Plimsol asserts). A
rule that the engine cannot run at all surfaces in axe's own error/
inapplicable reporting and lands in §b.2's unattested class.

**b.2 — Retain per-rule attestation.** Even with `runOnly`, the record
must be able to say WHICH rules the engine actually reported on. The
observation retains rule IDs (not the full node payloads) for both
`passes` and `inapplicable`. The processor then requires, for PASS:

- the rule's engine id is in `pins.engine_run_set`, AND
- that engine id appears in the observation's attested `passes` ids.

Otherwise:

| condition | verdict | reason |
|---|---|---|
| engine id absent from `pins.engine_run_set` | NOT_DETERMINED | `rule_not_executed` |
| in the run set, but absent from `passes`/`inapplicable` attestation | NOT_DETERMINED | `rule_unattested` |
| in the run set, attested `inapplicable` | NOT_DETERMINED | `rule_inapplicable` |
| observation predates this fix (no attestation fields) | NOT_DETERMINED | `legacy_unattested` |

**`inapplicable` is NOT a pass.** "No `<audio>` element on this page"
means the criterion was not exercised, not that the surface conforms.
Recording it as its own reason keeps the vacuous class visible instead
of dissolving it into PASS — the whole point of this slice.

## c. Observation payload delta (worker-side, additive)

`spike.py` gains, inside `engine_observations`:

```
"passes_ids":        [ids...],   # rule ids only, never the node payloads
"inapplicable_ids":  [ids...],
"run_set":           [ids...],   # what runOnly was actually given
```

`passes_count` / `violations` / `incomplete` are unchanged, so every
existing reader keeps working.

**Class + boundary check.** This is the SAME class as the already-
approved `incomplete` passthrough (commit 4244ed1): raw engine bucket
IDs, passed through verbatim, with no judgment applied in the worker.
The worker still computes no verdict, imports no interpretation or
knowledge module, and the string-ban plus import-ban guards must stay
green over the change.

**Size.** IDs only: ~72 short strings across the three lists, well under
2 KB per surface — negligible beside the existing screenshot and
fingerprint payloads. Retaining full `passes` ENTRIES (with node lists)
was rejected: it multiplies the observation by an order of magnitude for
data the verdict does not need.

## d. Re-processing existing verdicts

The processor is already idempotent per `(job_id, test_id)`
(UPSERT), so re-decision is a re-run over the STORED observation — no
re-scan, no browser, no org contact.

- Where the stored observation supports a determination, it is used:
  `violations` are retained in full, so **FAIL survives re-decision**;
  `incomplete` is retained since 4244ed1, so those rules re-decide to
  `NOT_DETERMINED (engine_incomplete)` with candidates.
- Where the observation carries no attestation — **every run before this
  fix, P-1 included** — each PASS becomes
  **`NOT_DETERMINED (legacy_unattested)`**. Verdicts are **never**
  retroactively PASS: a record that cannot attest cannot acquit.
- The disabled-rule subset can still be identified OFFLINE from the
  pinned engine artifact (that is how D-465 §9(b) named its 12), but the
  verdict REASON stays `legacy_unattested`, because the observation
  itself attests nothing. Reporting may cite the offline analysis; the
  verdict may not.

**P-1's own verdicts are re-decided this way.** Expected outcome, which
the acceptance pins: `2 FAIL` (unchanged), `3 NOT_DETERMINED
(engine_incomplete)`, `139 NOT_DETERMINED (legacy_unattested)` — and
zero PASS, because P-1's observations predate attestation. The published
decomposition in `VERIFICATION_P1.md` §9 is what the re-decide must
reproduce; D-465 stands as the historical record of what the run
reported at the time.

**Comparator consequence (Phase 7).** A `NOT_DETERMINED` side is already
an indeterminate side, so any future comparison against the P-1 run
yields `NOT_COMPARABLE (indeterminate_side)` rather than a false
`STILL_PASSING`. That is the correct reading: P-1 established the
production path, not a conformance baseline. The first attested run
becomes the first true baseline.

## e. Acceptance

Fixture-driven, deterministic, no org contact:

1. **Disabled-rule bound → NOT_DETERMINED, not PASS.** A bound rule
   whose engine id is outside `pins.engine_run_set` → `rule_not_executed`.
   Same rule inside the run set and attested → PASS. (The direct
   regression for D-465 §9(b).)
2. **Incomplete → NOT_DETERMINED with candidates** under `AUTO`; the
   same observation under `HUMAN_WITH_CANDIDATE` → `NEEDS_HUMAN` with
   the same candidates. (The §a matrix, both rows.)
3. **Inapplicable → NOT_DETERMINED (`rule_inapplicable`)**, never PASS.
4. **Genuine attested pass → PASS**, with the attesting engine id in the
   basis so the verdict is re-verifiable from the evidence bundle.
5. **Unattested-but-in-run-set → `rule_unattested`** (the engine ran the
   set but reported nothing for this rule).
6. **Re-decide of P-1's stored run reproduces the published
   decomposition** exactly: 2 FAIL, 3 `engine_incomplete`, 139
   `legacy_unattested`, 0 PASS.
7. **Live-shaped end-to-end on fixtures**: a scan under the new engine
   call, with attestation retained, producing at least one PASS whose
   attestation is present in the stored observation.
8. Worker boundary guards green; full merge-gate suite green.

## f. Non-goals

- No new rules, no catalogue changes, no re-cut of release 2.
- No Aura ownership-marker work (its own FIX-PLAN entry).
- **No re-run of P-1.** The fix re-decides stored observations; it does
  not re-execute the production scan.
- No `landed_url`, no `ran_by` worker identity, no evidence-bucket
  separation (all separate FIX-PLAN entries).
- No report-UI work beyond whatever the existing verdict listing shows
  by reading the new reasons.
