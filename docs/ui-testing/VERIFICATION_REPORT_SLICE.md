# VERIFICATION — the report slice (the first screen)

Executed 2026-09-03 on scratch (`plimsol_3a3`) with a REAL browser over
the REAL Flask app for the fixture screenshots. **No production row was
written and no Railway act was performed** (one read-only export: the
stored P-1→B-1 comparison rows copied into scratch, the B-1 precedent).
Branch `report-slice` (from main @`0b7fb64`). Merge gated.

Re-runnable: `tests/integration/test_report_slice.py` (the bridge,
scratch); `tests/integration/test_report_pages.py` (the pages through
the real app — its OWN pytest invocation with `REPORT_PAGES=1`, because
importing the app binds engines to `DATABASE_URL`; the module refuses
to run bound to the wrong database).

---

## Design (LLD-lite — SDLC v3 item 2, entered by D-474, not drift)

**One read-only demo surface, three pages, zero new semantics.** The
web tier gains a console bridge
(`intelligence/ui_report_console.py`, the `s4_execution_console`
pattern: best-effort, `{available: bool}`, never raises into a render)
and four MEMBER+ routes over it. Nothing here computes a verdict, runs
a comparison, or enqueues anything — the pages read what the substrate
recorded, through the same read functions the substrate already trusts
(`standard_view` verbatim for every header and denominator).

- **The run view** (`/ui-report`, `/ui-report/runs/<job>`): pick a
  processed run → the verdict listing (rule id + title, surface,
  verdict + reason, ownership + bundle, evidence links). Filters:
  verdict, standard — which PROJECTS the listing through the chosen
  standard's ACTIVE map set (or a CUSTOM profile's ratified rules) —
  and surface. The honesty header is `standard_view`'s, verbatim.
- **The comparison view** (`/ui-report/compare`): baseline + candidate
  → the stored comparison's transitions grouped by the eight-row
  taxonomy, tool drift shown SUBTRACTED with the moved dimensions
  named, causal candidates with confidence and scope=surface
  transparency, and NOT_COMPARABLE rows carrying their RECORDED reason
  (`causal.reason` on the transition row — read, never derived). A pair
  with no stored comparison renders an honest empty state: this page
  computes nothing.
- **Coverage** (`/ui-report/coverage`): the Part 3 read rendered —
  N of M per standard (platform three + every ACTIVE `CUSTOM:` profile),
  the NOT_COVERED list, and the refusal ledger beside it.
- **Evidence (the bearer rule)**: signed URLs are minted ON DEMAND by
  an HTMX fragment (`/ui-report/evidence`) for the authorised session's
  tenant via `evidence.sign_url` (foreign-tenant keys refused +
  audited), returned response-body only. The page render never inlines
  a signature; no logger in the bridge touches a URL (structurally
  asserted). A web tier without the store's credentials degrades to a
  stated note, never a 500.

Rejected alternative: computing a comparison on demand when none is
stored — rejected because the pages are read-only by design and a
comparison is a recorded act (D-281 posture), not a render side-effect.

## a. The bridge, asserted against the recorded facts (scratch)

- Runs list: B-1 with `vault`, 2 surfaces, release 3, verdict counts
  `{FAIL 3, PASS 66, NOT_DETERMINED 79}`, newest first. (P-1 sits
  outside the 50-row window on a scratch carrying ~136 planted suite
  worlds; its reads are asserted through the comparison.)
- Run report (WCAG22 over B-1): header `ratified_catalogue`,
  `complete: true`, run set 74; denominator `21 of 55`; the projection
  holds **144** rows — the ACC-05 pair's 4 verdicts are EN/508-only and
  correctly excluded; FAIL filter → exactly B-1's three FAILs
  (PLM-A11Y-030 + PLM-A11Y-071×2), titles joined, evidence REFERENCED;
  surface filter halves; pages partition at the 50 cap.
- `CUSTOM:acme` projection: `ratified_profile` header and an honestly
  EMPTY listing (B-1 predates every custom rule); `CUSTOM:ghost`
  carries the refusal as `header_error`, not a 500.
- Comparison (P-1 → B-1, the stored run): counts 142/2/4, tool drift =
  {catalogue_release_id, catalogue_content_hash, bindings_hash},
  env delta `not_captured` both sides, all 142 NOT_COMPARABLE rows
  carry `indeterminate_side`, STILL_FAILING = PLM-A11Y-071 only. The
  reverse (unstored) pair: `found: false`, "read-only" named.
- Coverage: 21/55, 21/50, 19/38 + `CUSTOM:acme` (data-driven against
  the scratch's ACTIVE rules); 34 NOT_COVERED for WCAG22; refusal
  panels present on public AND profile standards with the
  token-vs-literal case visible.
- Evidence links: dict-shaped `evidence_keys` (screenshot/observation)
  minted when the store is configured; the stated degradation note when
  not; a missing surface is `found: false`.

## b. The pages, through the real app (separate invocation, 5 tests)

MEMBER sees all three pages (200 + content markers); a viewer is
redirected on every route including the evidence fragment. The run page
carries "ratified_catalogue", "21 of 55", both FAIL rules, and **no
`X-Amz-Signature` anywhere in the page render** (minting is
fragment-only). The compare page groups the taxonomy, names the moved
tool dimensions, and renders the honest empty state for the unstored
direction. The coverage page shows every denominator + the refusal
panel.

## c. Fixture screenshots (this directory: `report-slice-fixtures/`)

| page | file |
|---|---|
| Runs index | `runs_index.png` |
| Verdict listing (B-1, WCAG22) | `run_verdicts.png` — honesty header, FAIL-first, 144-row projection, page 1 of 3 |
| Release comparison (P-1→B-1) | `comparison.png` — drift banner, taxonomy chips, NOT_COMPARABLE reasons |
| Coverage | `coverage.png` — four standards, N of M, NOT_COVERED, refusal panels |

(The repeated refusal rows visible in `coverage.png` are scratch
suite-replay residue — each DB-real re-run ledgers its probe refusals
again. Real tenants ledger a refusal once per authoring act. A
report-layer dedupe-by-thread is a residual below.)

## d. Rules held (brief §d)

- **Read-only**: the slice's only writes are presigned-URL mints
  (external, ephemeral). No INSERT/UPDATE/DELETE anywhere in the bridge
  or routes; the comparison page refuses to compute.
- **Worker untouched**: no `browser_worker/` file in the diff; the
  D-460 string/import bans and the D-469 call-site sweep run green in
  the unit set (4,961).
- Views follow the web-tier precedent (`s4_list`/`s4_detail` idiom:
  `require_tier(Tier.MEMBER)`, `ctx()`, base.html + breadcrumbs +
  empty-state components, 50-row page cap).

## e. Suites (D-468)

- **Unit: 4,961 passed** (no unit surface changed).
- **DB-real: 57 passed** across all eleven suites (+7 report bridge).
  The two skips are the Part 2/3 idempotent-replay guards, first-run
  proven.
- **Pages: 5 passed** (own invocation, documented above).
- **Browser-gated: 63 passed, 11 skipped.**

## Residual, stated plainly

- The runs list is a 50-row window (newest first) — enough for the
  pilot's run count by orders of magnitude; pagination when a tenant
  outgrows it.
- Refusal rows render as ledgered; a dedupe-by-`guideline_thread_id`
  in the report layer would tidy re-authored refusals (display concern
  only — the ledger is append-only by design).
- Evidence minting on the deployed web tier requires the store's
  read credentials (`EVIDENCE_S3_*`) on that service — an AK ops
  decision at merge time; the page degrades honestly without them.
- No writes, no scheduling, no UI for authoring — those are later
  sequence items (D-474).
