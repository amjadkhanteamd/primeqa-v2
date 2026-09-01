# VERIFICATION — BASELINE RUN B-1

Executed 2026-08-31 on **production** (gated: GO #1 for the writes, GO #2
for this record). The programme's **first attested conformance run**:
authenticated (vault TOTP), run-set pinned (D-466), catalogue release 3,
rendered under all three standards. B-1 **stands as the first
conformance baseline**; P-1 is superseded as baseline (it remains the
production-path proof, D-465).

Dump before any write: `prod_pre_B1_20260831_210835.dump` (pg_dump -Fc,
188 MB).

---

## a. The run

| step | record |
|---|---|
| enumerate | release 3 × inventory v1 × `customer` → draft claim_set `dca461b8`, **148 members** (74 × 2 surfaces), all APPLICABLE+executable; 4 claims created (PLM-A11Y-073/074 × 2 surfaces, verified by row count), 144 reused by identity hash |
| approve | AK (user 1): 148 claims + 4 recipes promoted; audited `s2.claim_set.approve` |
| manifest | `b8e60445` — pins verified in the stored row: **engine_run_set = 74 ids, hash `879fc38d…`** (byte-identical to the scratch fingerprint), release 3, axe 4.13.0, auth `vault`/`customer`, 2 surfaces; `org_env_snapshot_id` = null, honestly (no sf_client) |
| job | `471a9c35` — claimed by the Railway worker, **succeeded attempt 1** |
| login | exactly ONE for the batch: `LOGIN_SUBMITTED, MFA_SUBMITTED` (vault decrypt job-scoped) |
| surfaces | both `OK`; evidence **REFERENCED** on R2 (upload → DB write → verify → reference) |

The run is the direct production proof that the authenticated consume
path is repaired post-`5770b8b` (D-469): the identical job shape raised
`TypeError → failed_permanent` on every attempt before the fix.

Operational note, recorded because the transcript must be honest: the
first driver process hit a 5-minute timeout after the enumeration
commit; the approve had NOT committed (set still `draft`). The draft was
verified complete (148 members) and the sequence resumed in a fresh
process. No partial state existed at any point.

## b. Verdicts — the first attested decomposition

148 verdicts (`s6_ui_verdicts`), processing run recorded:

| verdict | reason | count |
|---|---|---|
| **PASS** | attested (`passes_ids` ∩ run set) | **66** |
| **FAIL** | violation | **3** |
| NOT_DETERMINED | `rule_inapplicable` | 75 |
| NOT_DETERMINED | `engine_incomplete` | 4 |

- **Zero** `legacy_unattested` / `rule_unattested` / `rule_not_executed`
  / NEEDS_HUMAN / unmapped engine ids. Production's first PASS rows —
  P-1 re-decided to 0 PASS under D-466; B-1 attests 66.
- **The pin worked, observed**: PLM-A11Y-064 (`target-size`,
  engine-disabled in axe 4.13.0) **PASS on both surfaces**;
  PLM-A11Y-073 (`duplicate-id`) PASS on both; PLM-A11Y-074
  (`duplicate-id-active`) PASS on tabset-2 and `rule_inapplicable` on
  /s. All three execute ONLY because D-466 pins an explicit run set.
- The 3 FAILs: PLM-A11Y-071 (landmark; both surfaces — the same real
  failure P-1 captured) and PLM-A11Y-030 (contrast-enhanced, 6 nodes,
  /s).
- `engine_incomplete` (4): `aria-valid-attr-value` (both surfaces),
  `color-contrast` (tabset-2), `color-contrast-enhanced` (tabset-2) —
  axe declined; the verdict says so instead of acquitting.

## c. Standard views — the first real coverage numbers

All three honesty headers carry: engine axe-core 4.13.0, catalogue
release 3, run-set hash `879fc38d…`, **`engine_run_set_size` 74** (P-1
rendered `None` — its manifest predates the pin), map-set id + content
hash, `denominator_complete=false` with the lower-bound limitation.

| standard | AUTOMATED | NOT_COVERED | criterion roll-up |
|---|---|---|---|
| WCAG 2.2 AA (set 1) | 26 | 2 | 6 PASS / 3 FAIL / 17 ND / 2 no-verdict |
| EN 301 549 V3.2.1 (set 2) | 22 | 2 | 4 PASS / 2 FAIL / 16 ND / 2 no-verdict |
| Section 508 2017 (set 3) | 20 | 0 | 3 PASS / 2 FAIL / 15 ND |

HUMAN_ONLY: 0 in all three. FAIL criteria: **1.3.1 + 2.4.1** in all
three standards (EN numbering 9.1.3.1 + 9.2.4.1) — one failure, three
renderings.

**The 1.4.6 line (ratified disposition).** WCAG22 additionally rolls up
a FAIL on **1.4.6 (AAA)** from PLM-A11Y-030 (`color-contrast-enhanced`).
This is the known **rule-derived-level residue now live**: migration 063
propagated the RULE's level tag to every criterion, so an AAA criterion
passes the A+AA scope gate. The view renders the data it holds,
honestly. The residue is **retired by Phase 5's per-criterion catalogue
levels — not by editing this run's record.**

## d. Phase 7 comparison — P-1 vs B-1

`compare_processing_runs(P-1 c70fa8e6, B-1 471a9c35)` → outcome
`completed`, comparison `7a4931f6`, 148 transition rows:

| transition | count | note |
|---|---|---|
| NOT_COMPARABLE | 142 | baseline side NOT_DETERMINED (indeterminate_side) |
| STILL_FAILING | 2 | PLM-A11Y-071 × both surfaces — FAIL↔FAIL |
| NEW_CLAIM | 4 | ACC-05 pair × 2 surfaces (new in release 3) |

**Comparator precision, ratified**: the brief expected NOT_COMPARABLE
across the board; the correct decomposition is 142/2/4. **A FAIL↔FAIL
pair is comparable across an otherwise indeterminate baseline** — P-1's
two FAILs are determinate verdicts, so those pairs compare (and still
fail), while the four release-3 claims have no baseline side at all.
The comparator being more precise than the expectation is the designed
behavior, now ratified.

**Tool drift reported**: `catalogue_release_id [2→3]`, catalogue content
hash moved, bindings hash moved. Env delta: `not_captured` on both
sides, honestly. P-1 is confirmed as **no baseline** for anything except
its two determinate FAILs.

## e. Hygiene

Secret scan (`gAAAA|otpauth|passw|totp|secret|bearer|fernet`,
case-insensitive) over every new production row — manifest, job payload,
2 results, 148 verdicts, 148 transitions, activity_log — **0 hits**.
Service log since boot: **0 hits**. Credentials never left the worker
frame. Audit: both human acts attributed to user 1.

## f. Egress

This boot printed `EGRESS_IP=152.55.184.196` — the **fifth** distinct
value (after 152.55.185.115, 152.55.184.211, 152.55.184.199,
152.55.184.236). **P-2/D9 is empirically settled: the egress is
unstable.** Any allowlisting-dependent customer needs a different
mechanism, not an IP pin.
