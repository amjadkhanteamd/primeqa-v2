# LLD — Step 3: the requirement → surface link (TA R2: declared first, derived later, provenance distinguishable)

**Status: DESIGN — GO (AK, 2026-09-09; all five forks on the leans, with the inventory lifecycle flag recorded as fork 3's successor).** Branch `step-3-requirement-surface`
from `main` @ 383ee2f (Step 2 live, D-484). Mock approved by AK (outside
the repo — no mock file exists under `docs/`; this LLD describes the card
in the brief's terms and the fixture screenshots ride the build push; the
merge is gated on AK seeing them, per the standing UI rule).

What this step does, in one line: a human DECLARES that a requirement is
verified on a portal surface; the declaration is a first-class, provenanced
row; declaring MATERIALISES `verifies` links from the requirement to every
conformance claim on that surface in the active claim set, so the existing
readers (the requirement page's test plan, the walk-the-plan sequence, the
release scope) see the conformance checks with no reader rewrite; unlink
is an explicit state change with provenance; DERIVED is reserved
vocabulary that v1 cannot write.

## 0. Pre-flight facts, cited (all read this session)

| fact | where |
|---|---|
| `read_requirement_claims(tenant_id, requirement_key)` → `_read_claims` → `coord.list_tests_by_requirement(session, external_system="jira", external_key=requirement_key, link_kind=COVERAGE_LINK_KINDS)`; deprecated claims excluded; sort `(archetype, claim_kind, test_id)`. `_read_test_sequence` mirrors it. | `intelligence/s3_generation_console.py:85-135, 138-160, 160-190` |
| `COVERAGE_LINK_KINDS = ("generated_from", "verifies")` — "which requirement generated this claim" stays `generated_from`; `verifies` is the human-curation kind. | `test_representation/coordinator.py:250-269` |
| The decision engine's `_claim_test_ids` reads the SAME kinds via `list_tests_by_requirements(session, external_system="jira", external_keys=…, link_kind=COVERAGE_LINK_KINDS)` — a `verifies` link therefore enters Area 2 (the requirement page), the plan sequence, AND Area 5 (release scope / the decision engine) with no reader change. | `intelligence/substrate_decision.py:238-252` |
| `coord.link_requirement(session, *, actor, test_id, external_system, external_key, link_kind, external_version=None)`: PK `(test_id, external_system, external_key, link_kind)`; idempotent (same PK → no-op; original `linked_by` / `linked_at` preserved); `actor='s4'` refused; `external_system` must be in `_VALID_EXTERNAL_SYSTEMS = {"jira"}`; the row's `linked_by = actor` — the actor KIND (`ActorKind = Literal["human","s3","s8","s4"]`), never a user id. | `coordinator.py:2037-2150, 2129, 2647`; `authority.py:44` |
| `coord.unlink_requirement(...)` → `session.delete(existing)`. The S2 link table `test_requirement_links(test_id, external_system, external_key, external_version, link_kind, linked_at, linked_by)` has **no active / deactivated column**. | `coordinator.py:2152-2200`; `models_db.py:552-580`; DDL `20260518_1014:434-435` |
| Identity (Step 1): `requirements.external_key` (072) and the tenant table `requirement_identities(external_system, external_key) PK`, origin CHECK `jira / manual / fixture / probe / CANNOT_CLASSIFY`. The requirement page's key is `req_key = _requirement_to_ref(req)["key"]` = `key_for_requirement_row(row)` = `external_key`. Every requirement link on every origin is written with `external_system="jira"` (the Step 1 FIX PLAN misnomer — not changed here). | `alembic/…/20260908_0010:31-44`; `views.py:2718`; `intelligence/s3_enqueue.py:15-26`; `identity.py:106` |
| Inventory: `ui_surface_inventories(inventory_version PK, notes, created_by, created_at)` — **no active flag**; `ui_surface_inventory_members(inventory_version, surface_key, site, path, persona_scope, record_context_ref, viewport, display_name, notes, auth_required, surface_entity_ref) PK (inventory_version, surface_key)`; membership recorded once at the cut; `surface_key = canonical_surface_key(SurfaceNaturalKey)` — the frozen five-field string. Production: v1 (2 members, 2026-08-27), v2 (6 members, 2026-09-03), every `record_context_ref` NULL. | `alembic/…/20260825_0020:28-52`; `claim_sets.py:50-123`; `models/surface.py:25-60` |
| A conformance claim carries the frozen surface natural key in its body: `asserted_truth = {"kind": "conformance-claim", "surface": {site, path, persona_scope, record_context_ref, viewport}, "plimsol_rule_id": "PLM-A11Y-001", "body_schema_version": 1}`; the manifest builder re-derives the key with `SurfaceNaturalKey(**asserted_truth->'surface')`. | read on production (claim e56a6b82…); `execution_engine/ui_manifest.py:195-213` |
| Claim sets: `claim_sets(id, persona_scope, inventory_version, catalogue_release_id, standard_profile, status draft/approved/revoked, created_*, approved_*, member_count)`; `claim_set_members(claim_set_id, test_id, applicability APPLICABLE/NOT_APPLICABLE/HUMAN_REVIEW, executable, revoked_at, revoked_by)`. Production: e9fda797 approved on v2, 444 members = 74 rules × 6 surfaces, all APPLICABLE. **Zero requirement links exist on any conformance claim today** — the gap this step fills. | `alembic/…/20260825_0020:53-90`; read on production |
| The card's numbers: `s6_ui_processing_runs(job_id, manifest_id, claim_set_id, …, verdict_counts, surface_statuses, processed_at)`; `s6_ui_verdicts(job_id, surface_key, claim_set_id, test_id, plimsol_rule_id, verdict ∈ PASS / FAIL / NOT_DETERMINED, …)`. Latest on production: job 368f09eb over e9fda797 (2026-09-04): PASS 191 / FAIL 12 / NOT_DETERMINED 241; per surface e.g. `/s`: 33 / 2 / 39. | read on production |
| The release-scope environment finding: `_ENVS_WITH_EVIDENCE_SQL` = DISTINCT `environment_id` over `s4_execution_runs` for the scope's claims — no active filter; BOTH the engine's per-environment loop (`:904-923`) and `release_scope_readiness` (`:1002`) enumerate through `_environments_with_evidence`. `EnvironmentRepository.list_environments` filters `Environment.is_active == True`. Env 78 "Prod1": inactive, `is_production`, `read_only`. | `substrate_decision.py:266-277, 904, 1002`; `core/repository.py:173-179`; VERIFICATION_STEP_2 §k.1; FIX PLAN 2026-09-08 |
| Gates and page pattern: `/requirements/decorate` and `/requirements/<id>/run-substrate` are `@require_tier(Tier.MEMBER)`; the page's HTMX shape is `hx-post` → fragment, `hx-target` + `hx-swap="outerHTML"`; CSRF auto-injected for htmx. Component kit: `_buttons`, `_empty_state`, `_readiness` (macro style), `_drawer`, `confirm_modal`. The test-plan card is a collapsible `<details data-collapse-key="req-panel-plan">`. | `views.py:2625-2626, 2894-2895`; `templates/requirements/detail.html:79-80, 331-372` |
| Tenant alembic head `20260909_0010` (Step 2). ActorKind has no per-user identity. | `alembic/versions/tenant/` |

## a. THE LINK TABLE (tenant; migration `20260910_0010`, ADDITIVE)

```
requirement_surface_links
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid()
  external_system      external_system NOT NULL DEFAULT 'jira'        -- the identity's system half (Step 1 PK)
  requirement_key      TEXT NOT NULL                                   -- the identity (external_key), verbatim
  surface_key          TEXT NOT NULL                                   -- the frozen canonical key
  inventory_version    INTEGER NOT NULL
  source               TEXT NOT NULL
      CONSTRAINT requirement_surface_links_source_known
          CHECK (source IN ('DECLARED', 'DERIVED', 'VERIFIED_DERIVED'))   -- the vocabulary (TA R2)
      CONSTRAINT requirement_surface_links_v1_declared_only
          CHECK (source = 'DECLARED')                                     -- the v1 write guard
  declared_by          INTEGER NOT NULL                                 -- public.users.id (the actor)
  declared_at          TIMESTAMPTZ NOT NULL DEFAULT now()
  active               BOOLEAN NOT NULL DEFAULT TRUE
  deactivated_by       INTEGER
  deactivated_at       TIMESTAMPTZ
  deactivation_reason  TEXT
  CONSTRAINT requirement_surface_links_deactivation_complete
      CHECK ((active AND deactivated_by IS NULL AND deactivated_at IS NULL)
             OR (NOT active AND deactivated_by IS NOT NULL AND deactivated_at IS NOT NULL))
  FOREIGN KEY (inventory_version, surface_key)
      REFERENCES ui_surface_inventory_members (inventory_version, surface_key)
  FOREIGN KEY (external_system, requirement_key)
      REFERENCES requirement_identities (external_system, external_key)
  UNIQUE INDEX uq_requirement_surface_links_active
      ON (external_system, requirement_key, surface_key, inventory_version) WHERE active
  INDEX ix_requirement_surface_links_requirement ON (external_system, requirement_key) WHERE active
  TRIGGER requirement_surface_links_no_delete        BEFORE DELETE → RAISE (unlink is a state change, never a delete)
  TRIGGER requirement_surface_links_immutable        BEFORE UPDATE → RAISE when any of
                                                     (external_system, requirement_key, surface_key,
                                                      inventory_version, source, declared_by, declared_at) changes

requirement_surface_link_claims                      -- the materialisation ledger (provenance of every S2 link this step wrote)
  link_id              UUID NOT NULL REFERENCES requirement_surface_links (id)
  test_id              UUID NOT NULL                                   -- the conformance claim (S2 test identity)
  claim_set_id         UUID NOT NULL REFERENCES claim_sets (id)        -- the active set the claim was found in
  created_link         BOOLEAN NOT NULL                                -- TRUE: this declaration CREATED the S2 verifies row;
                                                                       -- FALSE: it already existed (idempotent no-op) — never ours to remove
  materialised_by      INTEGER NOT NULL
  materialised_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  deactivated_by       INTEGER
  deactivated_at       TIMESTAMPTZ
  PRIMARY KEY (link_id, test_id)
  TRIGGER requirement_surface_link_claims_no_delete  BEFORE DELETE → RAISE
```

Rules the schema enforces, and why:

- **Identity, not a display id.** `requirement_key` is the Step 1 identity, verbatim, and the FK to `requirement_identities` means a requirement with no established identity (the four purge-made gaps) cannot be declared on — the page says "decorate first" (Step 1's own affordance). Nothing is minted.
- **The frozen surface.** The FK to `ui_surface_inventory_members` pins the declaration to a recorded member of a recorded inventory version. A surface that is not in the inventory cannot be declared; a declaration never outlives its inventory row (the inventory is immutable, so this is permanent).
- **DECLARED only at v1; the vocabulary admitted.** Two CHECKs: the first names the three-word vocabulary so the domain is defined once (TA R2 "provenance distinguishable"); the second is the v1 write guard named for what it is — the step that introduces derivation drops `…_v1_declared_only` under its own D-entry, widening nothing else. The service refuses any `source` other than `DECLARED` before the DB does. Test: a direct `INSERT … source='DERIVED'` raises `CheckViolation`.
- **Unlink is a state change.** `active=false` + `deactivated_by/at/reason`, complete-or-absent by CHECK; DELETE refused by trigger; the identity columns immutable by trigger. A re-declaration after an unlink is a NEW row (the partial unique index only guards ACTIVE duplicates), so the history reads as a sequence of acts.
- **The actor is a user id**, as `claim_sets.created_by / approved_by` already record in this slice family (no cross-schema FK to `public.users`; the id is recorded, the name is rendered by lookup). The S2 `verifies` row's `linked_by` records the actor KIND `"human"` — S2's own convention; the user-level provenance lives here.

### Fork 1 — the §b premise vs S2's link API (a correction to the brief)

The brief's §b says "on unlink, links are deactivated with provenance, never deleted". The S2 `verifies` row has no state column and S2's `unlink_requirement` is a hard delete (cited above). Two ways to honour the sentence:

- **(A, lean)** Provenance lives on Step 3's own tables: the declaration row is deactivated (never deleted) and the materialisation ledger records every S2 link the declaration created and when it was deactivated; the S2 `verifies` row itself is removed through S2's own API on unlink so that every reader (`read_requirement_claims`, the sequence, `_claim_test_ids`) drops the claims at once — no reader change, the substrate boundary respected. "Never deleted" holds for the declaration and its ledger; the S2 row is S2's runtime projection of the declaration.
- **(B)** Add an `active` column to `test_requirement_links` and teach every reader to filter it. That is an S2 schema + SPEC §9 change and a change to four readers — beyond this step and HOLD-worthy on its own.

Lean (A). The ledger's `created_link` flag guarantees unlink removes ONLY rows this declaration created: a `verifies` link a human curated independently on the same claim + requirement is the same S2 PK, would be a no-op at declare (`was_noop=True`, ledgered `created_link=false`) and is left in place at unlink.

## b. MATERIALISATION (`test_representation/surface_links.py` — S2, beside `claim_sets.py`)

The service lives in S2 because it composes S2's own objects (inventory, claim sets, claims, requirement links); the intelligence console (§c) reads it; views call it. Functions (all take a tenant `Session`):

- `active_inventory_version(session) -> int | None` — **the derivation rule (Fork 3, ruled):** the highest `inventory_version` that has an `approved`, unrevoked claim set. There is no active flag on inventories today (cited); the rule is stated, tested, and shown on the card ("inventory v2 — the active inventory: the highest version with an approved claim set"). **Its recorded successor is the inventory lifecycle flag (TA condition 5)**: when the inventory carries its own lifecycle state, `active_inventory_version` reads that flag and the derivation retires under that step's entry; nothing here is shaped against it (the card and the picker take the version from the one function).
- `active_claim_set(session, inventory_version) -> uuid | None` — the latest-approved (`approved_at DESC`) unrevoked claim set on that inventory version (e9fda797 today).
- `surface_claims(session, *, claim_set_id, surface_key) -> list[ClaimOnSurface{test_id, applicability, executable}]` — unrevoked members whose latest claim body's `surface` canonicalises (`SurfaceNaturalKey(**surface)` → `canonical_surface_key`) to `surface_key`; the same derivation the manifest builder makes. Every applicability is included — a HUMAN_REVIEW claim is still a check on the surface; the card counts it as human review.
- `declare(session, *, requirement_key, surface_key, actor_user_id, external_system="jira") -> DeclareResult{link_id, created, materialised, already_linked}`:
  1. refuse unless `surface_key` is a member of the ACTIVE inventory version (`SurfaceNotDeclarable(reason="not_in_active_inventory")`), or if its member row has `record_context_ref IS NOT NULL` (`reason="record_context"`), or if the requirement has no identity row (`reason="no_identity"`);
  2. if an ACTIVE declaration for the triple exists → return it with `created=False` and re-materialise (idempotent — a second declare is a re-materialisation, never a duplicate);
  3. else INSERT the `DECLARED` row;
  4. materialise: for every claim on the surface in the active claim set, `coord.link_requirement(session, actor="human", test_id=…, external_system, external_key=requirement_key, link_kind="verifies")` (no `external_version` — that column is the external requirement's version, not ours) and write the ledger row with `created_link = not result.was_noop`.
- `rematerialise(session, *, inventory_version=None, requirement_key=None, actor_user_id) -> int` — for every ACTIVE declaration in scope, re-resolve the active claim set for the declaration's inventory version and link any surface claim not yet ledgered. **Add-only (Fork 5)**: a claim that left the active set (member revoked) keeps its `verifies` row — removing it silently would be an auto-unlink, the very thing the brief reserves; the readers already exclude DEPRECATED claims (D-269), and a human unlink or a claim deprecation are the two recorded ways a check leaves the plan. Called (i) inside `declare`, and (ii) from `approve_claim_set` after the promotion (S2-internal) so that a superseding claim set on the same inventory version is followed without a human remembering to — **the link follows the surface, not the claim version**: the declaration is (requirement × surface × inventory version); the claims on that surface are whatever the active claim set says they are; a claim's own version supersession changes nothing (S2 links are per `test_id`).
  - A NEW inventory version does not move declarations: they stay on their version, active, and the card labels them with it ("inventory v1 — not the active inventory"); the picker offers ACTIVE members only; declaring the same surface again on the new version is a new act (a new row on the new version).
- `unlink(session, *, link_id, actor_user_id, reason) -> UnlinkResult{removed_links, kept_links}` — UPDATE the declaration (`active=false` + provenance); for every ledger row with `created_link` and no `deactivated_at`: `coord.unlink_requirement(actor="human", …, link_kind="verifies")` and stamp the ledger row; rows with `created_link=false` are stamped but their S2 row is left (not ours). Idempotent on an inactive link (returns the recorded state).
- `declared_surfaces(session, *, requirement_key) -> list[DeclaredSurface]` and `picker_candidates(session, *, requirement_key) -> list[SurfaceCandidate]` — the reads §c renders (active inventory members, minus active declarations for this requirement, minus record-context).

Authority: the coordinator calls carry `actor="human"` (a person declared); `s4` is never the actor. Every write is one transaction: the declaration row, the S2 links and the ledger commit together or not at all.

## c. THE CARD + PICKER (per the mock)

**Read side** — `intelligence/requirement_surface_console.py`, the s3-console pattern (`{available: bool}`, never raises into a render):

- `read_requirement_surfaces(tenant_id, requirement_key) -> {available, active_inventory_version, active_claim_set_id, latest_job_id, latest_processed_at, declared: [DeclaredSurface + split], counts}` where for each ACTIVE declaration: `display_name`, `surface_key`, `inventory_version`, `is_active_inventory`, `source` (always DECLARED at v1), `declared_by` (id + name by lookup), `declared_at`, `checks` (claims on the surface in the active set), `human_review` (HUMAN_REVIEW members among them), and the verdict split over the latest processing run of the active claim set: `pass / fail / not_determined`, or `no_run: true`. `counts` = the substantive header line: `surfaces / checks / failures / human_review` summed over the declarations on the active inventory.
- `picker_candidates(tenant_id, requirement_key)` → the active inventory's members minus declared minus record-context, with `display_name`, `surface_key`, `path`.

**Render** — a new collapsible card **"Conformance surfaces"** directly after the Test plan card (Area 2), `data-collapse-key="req-panel-surfaces"`, `id="conformance-surfaces"`:

- header: `N surfaces · C checks · F failing · H human review` (the count line is substantive, from the read above; zero declarations → the kit's `empty_state` "No surfaces declared — declaring a surface links its conformance checks to this requirement" with the "+ Declare surface" CTA);
- one row per declared surface: `display_name`, the canonical key (mono, small), a **DECLARED** chip, "by *name* · *date*", an inventory tag ("inventory v2"; "v1 — not the active inventory" when older), the verdict split as four small counts (PASS / FAIL / not determined / human review) or "no conformance run yet", and an explicit **Unlink** (confirm modal via `confirm_modal` + a reason field; MEMBER+). The DERIVED / VERIFIED_DERIVED chip styles exist in the template's vocabulary but v1 never renders them because no such row can exist.
- **"+ Declare surface"** (MEMBER+): `hx-get="/requirements/<id>/surfaces/picker"` swaps an inline panel under the header (`hx-target="#surface-picker" hx-swap="outerHTML"`) listing the candidates with a **Declare** button each (`hx-post="/requirements/<id>/surfaces"` with `surface_key`). Candidates come from the ACTIVE inventory only; already-declared and record-context surfaces are absent (§b `picker_candidates`); an empty candidate list says why ("every active surface is declared" / "no active inventory").
- **Fork 4 — after a declare or unlink:** the response sends `HX-Redirect` to `/requirements/<id>#conformance-surfaces` (a full re-render with a flash). Rationale: Area 2, the walk-the-plan chips, the readiness cells and the card must all agree after a declaration, and the brief's own rule is "Area 2 renders the conformance claims with no page rewrite" — a full re-render reuses every existing path untouched. The alternative (an out-of-band swap of the test-plan `<details>`) would need the test-plan block extracted into a partial; not lean. No-JS: the same routes work as plain POST → redirect (as `run-substrate` does).

**Routes** (views.py; D-245 declaration: minimum tier MEMBER, no environment policy — nothing executes):

| route | tier | behaviour |
|---|---|---|
| `GET /requirements/<id>/surfaces/picker` | MEMBER | the inline picker fragment |
| `POST /requirements/<id>/surfaces` (form `surface_key`) | MEMBER | `declare` → flash "Declared *name* — N checks linked" → `HX-Redirect` / redirect; refusals flash the reason (not in active inventory / record-context / no identity) |
| `POST /requirements/<id>/surfaces/<uuid:link_id>/unlink` (form `reason`) | MEMBER | `unlink` → flash "Unlinked *name* — N checks removed" → redirect; a link of another requirement → 404 |

Viewers see the card read-only (no CTA, no Unlink). Every write goes through the service with the acting user's id; every act is one transaction; `activity_log` receives one row per declare / unlink (the service layer's rule for admin actions).

## d. THE RELEASE-SCOPE INTERIM (the env-78 finding)

`_environments_with_evidence(session, test_ids, *, tenant_id)` gains the active filter at its one seam — both callers (the engine's per-environment loop at `:904` and `release_scope_readiness` at `:1002`) enumerate through it, so the scope check and the decision's branches agree:

```sql
SELECT DISTINCT r.environment_id FROM s4_execution_runs r
JOIN public.environments e ON e.id = r.environment_id
WHERE CAST(r.claim_test_id AS text) = ANY(:tids) AND e.tenant_id = :tenant_id AND e.is_active
ORDER BY r.environment_id
```

(the same predicate `EnvironmentRepository.list_environments` applies, `core/repository.py:177`). Effect on release 16: environments {59}; 19 items, all CURRENT after AK's run of 2026-09-08 — Evaluate would proceed (AK's act, not this step's). Legacy evidence on an inactive environment is not erased: it stays in the runs list and on the run pages; it stops holding a release's scope.

**Recorded deferral.** This is the interim. The declared-target model — a release NAMES the environments it must be current in, and the Run Planner resolves the required environments — is **Step 4's** (D-479 sequence). **Step-5 policy note:** a production `read_only` environment can hold **metadata-inspection evidence only** — `_authorize_dispatch` refuses every non-inspection recipe on `read_only` (`execution_engine/run.py:140-153`) — so a declared production target is satisfiable only by inspection recipes; whether such a target counts as "current" on inspection evidence alone is Step 5's policy ruling, not decided here.

## e. NON-GOALS

No derived suggestions (DERIVED / VERIFIED_DERIVED are reserved words with a v1 write guard); no Run Planner; no environment policy; no layout collapse of the requirement page; no change to conformance semantics (rules, applicability, verdicts, the standard views); no change to S2's link table or its readers; no active-inventory pointer (a derivation, stated); no backfill — every declaration is a human act from this step on.

## f. Blast radius

| touched | how |
|---|---|
| tenant schema | two new tables + two triggers (ADDITIVE, `20260910_0010`) |
| S2 `test_representation/surface_links.py` | new; `claim_sets.approve_claim_set` gains one call: `rematerialise(inventory_version=…)` after the promotion |
| `intelligence/requirement_surface_console.py` | new read console |
| `intelligence/substrate_decision.py` | `_environments_with_evidence` gains the active filter + `tenant_id` (both callers pass it) |
| `views.py` + templates | three routes; the card + picker partials; the requirement page includes the card after the test plan |
| not touched | the coordinator, the readers (`read_requirement_claims`, sequence, `_claim_test_ids`), the decision ladder, conformance enumeration / manifests / verdicts, the runs surfaces |

## g. Verification plan (the brief's list, restated as tests)

DB-real on the local-PG harness (`tests/integration/test_representation/test_step_3_surface_links.py`; fixture: an inventory version via `create_inventory_version`, conformance claims written with the real body (`coord.write_claim(archetype="ui", claim_kind="conformance-claim", …)`), a claim set via `create_claim_set` + `approve_claim_set`, an identity via `identity.establish`):

1. **declare → the links appear where the readers look:** `list_tests_by_requirement(…, link_kind=COVERAGE_LINK_KINDS)` (what `read_requirement_claims` calls) returns every conformance claim on the surface; `_claim_test_ids` (the engine's read) returns them too — Area 2 and Area 5 see them with no reader change.
2. **idempotent:** a second declare creates no row, no duplicate link; ledger rows unchanged.
3. **unlink deactivates with provenance:** the declaration row `active=false` with actor/time/reason; the ledger rows stamped; the S2 `verifies` rows this declaration created are gone (the readers drop the claims); a pre-existing independent `verifies` link on the same claim is left; `DELETE` on either table raises; the identity columns refuse UPDATE.
4. **DERIVED cannot be written:** the service raises before the DB; a direct `INSERT … source='DERIVED'` raises `CheckViolation` (and `VERIFIED_DERIVED` likewise); `source='DECLARED'` passes both CHECKs.
5. **the link follows the surface:** approve a superseding claim set on the same inventory version with one extra claim on the declared surface → its link appears (rematerialise from `approve_claim_set`); a member revoked from the set keeps its link (add-only, stated).
6. **picker exclusions:** candidates exclude declared surfaces, record-context members (`record_context_ref` set), and every member of a non-active inventory version; refusals name their reason.
7. **release scope names active environments only:** with evidence on an inactive environment, `release_scope_readiness` and the engine's env loop both enumerate the active one only; on production after the merge (read-only): release 16's scope names env 59 only.
8. **screens** over the real app on scratch (fixture screenshots in `docs/ui-testing/step-3-fixtures/`): the card with declared rows, DECLARED chip, actor + date, verdict split, the count line; the picker over the active inventory; the empty state; the unlink confirm; Area 2 showing the conformance claims after a declaration. Compared against the approved mock at the merge gate.

Plus the D-468 set: unit, `test_representation` (local PG), the DB-real corpus on clean scratch, pages, browser-gated.

## h. Migration classification (for the runbook)

`20260910_0010` — ADDITIVE: two new empty tables, two triggers, three indexes; no data write; DROPs only in the downgrade. Dumpless under D-476. Every declaration is a RUNTIME row through the product path. The reader window is safe in both directions this time: no existing table changes shape, and the new code reads its own tables only after they exist (the console is best-effort — `available=false` before the migration, never a 500); the migration still goes first under D-285.

## Residual, stated plainly

- The `external_system="jira"` misnomer on every requirement link is carried, not fixed (Step 1 FIX PLAN); the new table records the identity's system half so the day it is fixed, nothing here re-keys.
- No active-inventory pointer: the derivation "highest version with an approved unrevoked claim set" is the rule until a pointer exists.
- Rematerialisation is add-only; a check leaves a requirement's plan by human unlink or claim deprecation only.
- The count line's verdict split reads the LATEST processing run of the active claim set; a surface with no run says so.

## Forks for the GO

1. **Fork 1** (§a) — provenance on Step 3's tables, S2 row removed through S2's API on unlink. Lean A. **Ruled so.**
2. **Fork 2** (§a) — vocabulary CHECK + v1 write-guard CHECK (dropped by the step that derives). Lean both. **Ruled so.**
3. **Fork 3** (§b) — active inventory = derivation (highest version with an approved unrevoked claim set). Lean derivation, rule recorded; successor = the inventory lifecycle flag (TA condition 5). **Ruled so.**
4. **Fork 4** (§c) — after declare/unlink, `HX-Redirect` full re-render rather than an out-of-band swap. Lean redirect. **Ruled so.**
5. **Fork 5** (§b) — rematerialise add-only. Lean add-only. **Ruled so.**
