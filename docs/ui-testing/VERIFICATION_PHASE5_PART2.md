# VERIFICATION — Phase 5 Part 2: customer rule authoring

Executed 2026-09-02 on scratch (`plimsol_3a3`: public 062–068, tenant
chain upgraded `20260826_0020 → 20260902_0010`) plus a REAL browser for
the census capture. **No production row was written and no Railway act
was performed.** Branch `phase-5-part2` (cut from merged main
@`d2ea01d`, so the D-469 consume-contract tests run here — 8/8). Merge
gated.

Re-runnable: `tests/unit/test_representation/test_phase5_grammar.py` +
`test_phase5_evaluator.py` (pure, merge-gated);
`tests/integration/test_phase5_authoring.py` (scratch);
`tests/browser_worker/test_census_capture.py` (`SPIKE_BROWSER=1`, local
`data:` page — no network).

---

## What landed

| file | change |
|---|---|
| `knowledge/census_schema.py` | the census PIN: schema v1, the closed property list (14), the attribute allowlist (23), node cap 1500, length epsilon 0.5px — resolved by the builder, handed to the worker as DATA |
| `browser_worker/spike.py` | capture phase g: `_CENSUS_JS` — role/name/heading/custom-tag/allowlisted attrs/pinned styles/bounding box per semantic node; open shadow roots pierced and counted; traversal mode recorded (`synthetic_aura`/`native_open`/`light_only`); node cap and capture errors recorded. CAPTURE ONLY — no rule known, nothing judged (D-460) |
| `browser_worker/manifest.py`, `consume.py` | the census pin travels exactly as `engine_run_set` does: pins → job payload → `_scan_kwargs` → scan. Every signature and call site updated together; the D-469 contract sweep binds them |
| `execution_engine/ui_manifest.py` | `pins.census` beside the run-set pin — two runs agree on WHAT was captured |
| `knowledge/cust_grammar.py` | the F8 ceiling executable: ten forms (11 tokens; equals/not_equals one boolean-arity pair), six selector terms ≤4 AND-joined, one gate, mandatory population, versioned token-set pins; idref REFUSED by name as the reserved extension; connectives/CSS/regex refused with class + nearest_expressible |
| `interpretation/cust_evaluation.py` | the S6 evaluator: the §h verdict table + the §e.6 hard invariant, literally; normalisation (sRGB tuples, px epsilon, font lists) versioned with the schema |
| `knowledge/cust_authoring.py` | mint (5-digit ids), ledger (drafted/refused × 6 classes), the s5 lifecycle SHAPE reused (§i), the ratification conflict gate, token sets, the tenant release UNION recorded at cut (D-281) |
| `interpretation/ui_conformance.py` | `process_job` branches on `PLM-CUST-`: tenant content + census evaluation, same verdict table, same upsert |
| `generation/enumeration.py` | the tenant union: platform release members ∪ `cust_release_members` recorded for that release; stale recorded versions refuse |
| `test_representation/.../conformance_claim.py` | `_RULE_ID_SHAPE` widened ONCE to `^(PLM-A11Y-[0-9]{3}\|PLM-CUST-[0-9]{5})$` — shape validation only; identity hashes cover values, so nothing re-hashes |
| `alembic/.../20260902_0010_phase5_cust_authoring.py` | the tenant store: `cust_rules`, `cust_rule_versions` (single-ACTIVE index), `cust_predicates`, `cust_token_sets`, `cust_authoring_ledger` (refusal-shape CHECK), `cust_release_members` |
| `migrations/068_s5_cust_namespace.sql` | the RULED CHECK widening, once: A11Y@3 or CUST@5, nothing else. Applied to scratch twice; idempotent |

## a. The matrix (the briefed verification, executed)

- **Ten forms, each with a witnessed PASS, a FAIL, and its
  NOT_DETERMINED case** — all eleven tokens exercised
  (`test_phase5_evaluator.py`): membership pair (fact_not_captured on a
  missing style slot / an unresolved token-set pin), equality pair,
  presence pair (`absent` witnessed by the RECORDED attribute bag,
  never by silence — §e.4), geometry pair (missing box →
  fact_not_captured; epsilon absorbs `13.9993px`), count family
  (`at_most`/`equals` refuse to attest on an unfinished walk).
- **Empty match set → `no_match_set`** — never PASS; the D-465/D-466
  vacuous-pass class closed for custom rules too.
- **The node-cap demonstration**: a `surface_lacks` gate over a
  complete census MAY suppress (`rule_inapplicable`); the same gate
  over a census that hit its cap decides NOTHING —
  `NOT_DETERMINED(census_incomplete)` in BOTH configurations, with the
  failed condition named. Implemented to the ratified sentence: "on an
  incomplete one it decides nothing" — stricter than suppression-only
  refusal, and the first evaluator draft was corrected to it during
  this build.
- **A witnessed violation still FAILs on a partial walk** —
  incompleteness never acquits; it only blocks PASS and suppression.
- **`census_unattested`** (older schema, or no census at all) and
  **`traversal_mode_mismatch`** (Aura synthetic vs native shadow)
  decide themselves, never a guess.
- **The grammar validator rejects an LLM draft with a connective**:
  `{"and": [...]}` dies with `needs_prohibited_operator` and the split
  offered as `nearest_expressible` — before any human sees it as a
  rule. OR/NOT/nesting/`when` die wherever they hide, including inside
  the gate.
- **A refusal for each class**: the three mechanical classes are
  produced by the validator (prohibited operator; uncaptured
  property/attribute; focus-state facts → `needs_interaction`); all six
  are ledgered on scratch, including the **token-vs-literal case
  verbatim** — "must consume the token, never the literal" refused as
  `needs_capability_not_captured` with the palette-membership rule
  offered as the honest partial (§f).
- **Tenant isolation**: all six `cust_*` tables exist in `tenant_1`
  and ONLY there; the public CHECK (`s5_rules_id_shape_v2`) refuses
  `PLM-CUST` at three or six digits and unknown families; the tenant
  CHECK pins five digits.
- **PLM-CUST-00001 minted through the real lifecycle into a tenant
  release**: grammar-validated draft → `cust_rules` + version 1 DRAFT +
  normalised predicate rows + 'drafted' ledger row → REVIEW → APPROVED
  (refused without the reviewer's explicit `reviewed_no_conflict`;
  the WCAG-2.5.5 catalogue overlap recorded so the confirmation is
  informed) → VERSIONED → ACTIVE (single-ACTIVE index) → recorded into
  the release-3 union (`record_tenant_release`; re-recording refused —
  D-281). Enumeration over release 3 × inventory v1 × customer then
  yields **(74 + N_custom) × 2 members** with the custom claims present
  by identity; `process_job`'s custom branch decides PASS / FAIL /
  `no_match_set` from planted censuses through the real path.
- **The conflict gate**: two ACTIVE custom rules with the same selector
  and criterion whose predicates are a negation pair
  (`present`/`absent` on the same attribute) — the second is REFUSED at
  APPROVED.
- **Census capture, real browser**: a local `data:` page under the
  pinned config — schema v1 echoed, traversal mode recorded, heading
  level 1, the button's allowlisted attributes and all 14 pinned
  properties captured raw, numeric bounding box, ancestor chain; a
  30-button page under `node_cap=10` records `cap_hit=true, n=10`.
  Without a census pin: no capture, no phase — the pin decides.

## b. Suites (D-468)

- **Unit: 4,961 passed** (+47: 15 grammar, 24 evaluator, and the
  D-469 contract tests running against the new signatures — the
  package-wide sweep binds every new `census=` call site).
- **DB-real: 47 passed** across all nine suites (the seven prior +
  Phase 5 catalogue + Phase 5 authoring). The one skip is the mint
  test's replay guard: minting is one act; on a scratch that already
  holds PLM-CUST-00001 it skips rather than re-minting. Its first run
  passed and the scratch state is its evidence.
- **Browser-gated: 63 passed, 11 skipped** (+2 census).

## Residual, stated plainly

- **The `CUSTOM:<profile>` standard set is NOT rendered yet.** Rule
  versions carry `criterion.profile`; the profile-set view (§g's lean)
  and the "refusals beside NOT_COVERED" report surface are the next
  slice — the DATA for both is recorded now (ledger + criterion maps).
- **The catalogue-contradiction check is honestly sized**: mechanical
  refusal for custom-vs-custom negation pairs; for custom-vs-catalogue
  the engine's semantics are not in this vocabulary, so APPROVED
  requires the reviewer's explicit confirmation WITH the overlapping
  catalogue rules recorded on the version. A mechanical
  cross-vocabulary check would be a guess dressed as a proof.
- **`owned_by_bundle` resolves tag→bundle by the mechanical kebab→camel
  convention, exactly-one-or-None** — ambiguity is never a guessed
  owner; unresolved terms decide `fact_not_captured`, never a match.
- Census `style` values are stored RAW (evidence law); normalisation is
  evaluator-side and versioned with the schema.
- Non-goals untouched: no UI, no Mode B predicates, no customer engine
  rules, no scheduling, no cross-tenant sharing.
- MIGRATE-FIRST at merge: public 068 + tenant `20260902_0010` (both
  applied to scratch here; scratch upgrade transcript is this slice's
  proof of the upgrade path production will take).
