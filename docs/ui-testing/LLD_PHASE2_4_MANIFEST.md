# LLD — Phase 2.4: Run Manifest + determinism proof (arms A and C)

Status: DESIGN (this commit); implementation follows on GO.
Scope anchor: UI programme HLD/SAD/TAD v1.1, step 2.4.
Branch: `phase-ui-s2-spike`. Builds on 2.3
(`docs/ui-testing/LLD_PHASE2_3_QUEUE.md`, migration `20260821_0010`).

Naming contract unchanged: the worker records ENGINE OBSERVATIONS and
computes fingerprints as OBSERVATIONS; it judges nothing. Comparison
labels live in the result-processing side (`compare.py`), never in the
worker. The word "verdict" appears nowhere in this feature.

## Table: `s4_ui_run_manifests` (tenant schemas)

```sql
CREATE TABLE s4_ui_run_manifests (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**IMMUTABLE by convention**: the code exposes no update path —
`manifest.py` ships `create_manifest` + `get_manifest` +
`enqueue_for_manifest` and nothing else; no SQL `UPDATE`/`DELETE` against
this table exists anywhere in `primeqa/browser_worker/`. A test pins both
facts (module surface scan + source scan). No DB-level trigger at spike
grade — convention plus test, promoted later if the table outlives the
spike.

**Payload shape** (all execution-relevant facts pinned at creation):

```json
{
  "surfaces": [
    {"key": "fixture-a", "url": "http://127.0.0.1:8642/fixture-a.html",
     "viewport": {"width": 1440, "height": 900}, "locale": "en-US"}
  ],
  "pins": {
    "axe_version": "4.13.0",
    "axe_sha256": "c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1",
    "playwright_version": "1.62.0",
    "worker_image_digest": null
  },
  "stabilisation": {"quiet_ms": 500, "max_wait_s": 30},
  "execution": {"mode": "manual-spike"}
}
```

- `pins.axe_version` / `pins.axe_sha256` come from
  `primeqa/browser_worker/vendor/VERSIONS.md`; `playwright_version` from
  `requirements-browser.txt`; `worker_image_digest` nullable (the local
  spike has no image digest; the Railway service fills it later).
- **Write discipline — the D-281 idiom lifted to run scope**: the
  manifest is written and COMMITTED in its own transaction BEFORE any job
  is enqueued. Precedent: D-281's `s4_runall_batch_manifests` (migration
  `20260627_0010`), where `_write_batch_manifest` opens its own
  `get_tenant_connection` and commits BEFORE the probe loop — a
  correctness requirement, not a nicety: work that crashes after the
  manifest exists is still attributable to a persisted expected-state;
  work without a manifest is unknowable. Here identically: a job row can
  never exist before the manifest it references (also enforced by the
  NOT NULL FK below).

## `s4_ui_inspection_jobs` gains `manifest_id`

```sql
ALTER TABLE s4_ui_inspection_jobs
    ADD COLUMN manifest_id UUID NOT NULL
    REFERENCES s4_ui_run_manifests (id);
```

NOT NULL is safe: production has never had these tables (the 2.3 chain is
unapplied there — MIGRATE-FIRST lands at branch merge), and the local
spike DB is throwaway — it is RECREATED for 2.4 rather than backfilled.
No `ON DELETE` action: manifests are immutable and never deleted; a
delete attempt on a referenced manifest fails, which is correct.

## KEY DECISION — results stay job-scoped (recorded verbatim)

Results stay job-scoped — `UNIQUE (job_id, surface_key)` is UNCHANGED
from 2.3. A manifest re-execution is a NEW job referencing the same
`manifest_id`; arm A compares result sets ACROSS two jobs of one
manifest. A manifest-scoped unique key would upsert the second execution
over the first and destroy the comparison. (This corrects the earlier
draft's constraint-evolution note — the 2.3 LLD's "the unique key then
becomes (manifest_id, surface_key, …)" forward note is superseded by
this decision.)

## Fingerprint (worker side — an OBSERVATION, judged nowhere in the worker)

`scan_page` gains a fingerprint phase after the axe run: a normalised
semantic fingerprint of the stabilised DOM, computed in-page via
`page.evaluate`, returned as part of the scan result and therefore stored
inside the result row's `observation` JSONB (no new result columns at
spike grade; a typed column is a later promotion if querying needs it).

**Included** (per element, bottom-up):
- element role — explicit `role` attribute, else the implicit role of a
  fixed semantic-tag map (button, a[href]→link, nav, main, header,
  footer, form, img, h1–h6, ul/ol/li, table/tr/td/th, input-by-type,
  select, textarea, label, dialog, section/article/aside);
- accessible name — attribute-borne (`aria-label`, `alt`, `title`,
  `placeholder`) or the associated `<label>` text for form controls.
  Label-association text is an accessible NAME, not free text — the
  text-value exclusion below targets content, not names;
- custom-element tag names (any tag containing `-`);
- parent-child relations — each node's digest hashes over its children's
  digests. Non-semantic wrappers (bare div/span without a role) emit no
  node; their children attach to the nearest emitted ancestor, so layout
  re-wrapping does not move the fingerprint.

**Excluded**: text values (content text nodes), generated ids,
timestamps, class lists, styles.

Accessible-name text (including label-association text) is included
because it is semantic — it is what assistive technology announces; page
text content remains excluded as data. A dynamic accessible name
therefore legitimately yields NOT_COMPARABLE — intended behaviour, not a
defect.

**Digest**: per-node canonical string
`{role}|{name}|{custom_tag}|[sorted child digests]`, sha256 bottom-up;
the surface fingerprint is the root sha256. Child digests are SORTED so
sibling reordering does not change the fingerprint; ancestry still binds
(a child moved to a different parent changes both digests).

**Stored on the result row** (inside `observation`):

```json
"fingerprint": {
  "sha256": "…",
  "summary": {"element_count": 17,
              "roles": {"link": 4, "button": 1, "img": 2},
              "named": [["button", "Submit"], ["img", "Team photo"]]}
}
```

`summary` is the compact delta-display source (role counts + up to 50
(role, name) pairs); `compare.py` diffs summaries, never re-walks a DOM.

## `compare_jobs(job_a, job_b)` — result-processing side, pure function

Inputs: the two job rows (id, manifest_id), their manifests' pins, and
their result rows. No DB access inside the function — callers fetch, the
function judges. Output: `{comparable, reason?, surfaces: {key: {label,
detail?}}}` with labels:

1. `job_a.manifest_id != job_b.manifest_id` → **refuse,
   `NOT_SAME_MANIFEST`** — no surface comparison at all.
2. Pins integrity → **`TOOL_DRIFT`** (defensive; pins live in the shared
   manifest so this asserts integrity, it can only fire on data
   corruption or a worker that ignored its manifest):
   any result row whose observed `axe_version` ≠ the manifest's pinned
   `axe_version`, or a surface whose observed `browser_version` differs
   between the two jobs.
3. Per surface, in manifest order:
   - either side missing the row, `observation.status != "OK"`, or no
     fingerprint → **`NOT_COMPARABLE`** (detail: which side, why);
   - fingerprints differ → **`NOT_COMPARABLE`** with a fingerprint delta
     (role-count diffs + named-pair set difference from the summaries) —
     the page under the two runs was not the same page; comparing
     observations would be dishonest;
   - fingerprints equal, observation sets differ → **`DIFFERS`** (causal
     assessment is Phase 7; the spike stops at the honest label);
   - equal → **`SAME`**.

**Observation set** (the DIFFERS predicate): per surface, the mapping
`{violation rule id → node count}` plus the (violations, passes,
incomplete) count triple. Two runs are `SAME` iff both are equal.
Node-target selectors are NOT compared at spike grade (axe target paths
can embed generated ids — excluded from fingerprints for the same
reason).

## Fixture strategy — loopback `http.server`, not `data:` URLs

**Choice: a loopback `http.server` (127.0.0.1:8642) serving fixture files
the spike owns.** Two reasons, the first decisive:

1. **Arm C requires mutation OUTSIDE the manifest.** A `data:` URL embeds
   the page content in the URL, i.e. inside the manifest payload —
   mutating the fixture would mutate the manifest, which is exactly the
   shape arm C forbids (the manifest stays fixed; the world changes
   behind it). A served file swaps content behind a stable URL.
2. **`networkidle` semantics.** A `data:` navigation performs no network
   request at all (and chromium gives `data:` pages an opaque origin with
   blocked subresource loads), so the stabilise phase's
   `wait_for_load_state("networkidle")` would be exercised trivially —
   the fixture would not rehearse the production scan path. Loopback HTTP
   drives a real request → real load → real idle transition.

Mechanics: canonical fixtures live in
`tests/browser_worker/fixtures/` (committed): `fixture-a.html`,
`fixture-b.html`, and `fixture-b-mutated.html` — the arm C fixture is
`fixture-b` with ONE accessibility-relevant mutation (the `<label>` of
one input removed: structure loses a node, the input loses its
accessible name, and axe gains a label violation). The server serves a
SCRATCH COPY of the fixtures dir; arm C overwrites the served
`fixture-b.html` with the mutated file mid-arm. The repo files never
change during a run. Fixed port 8642 because the manifest pins the URL —
a random port would make a manifest unrepeatable across serve restarts.
Fixtures are static HTML, no JS, no external assets — deterministic by
construction.

## Verification (design of the proof; kill criteria binding)

- **ARM A (determinism)**: manifest M over both fixtures → job J1 →
  consume → job J2 (same M) → consume → `compare_jobs(J1, J2)` → every
  surface `SAME`, identical observation counts, identical fingerprints.
  The full cycle runs TWICE.
- **ARM C (world-change honesty)**: manifest M2 → J3 against fixture-b
  v1 → the SERVED fixture is swapped to the mutated version (a content
  change outside the manifest) → J4 same manifest → compare → the mutated
  surface `NOT_COMPARABLE` with a fingerprint delta naming the changed
  structure; the untouched surface `SAME`.
- **KILL CRITERIA (enforced, not softened)**: if arm A yields `DIFFERS`
  on any surface, STOP and HOLD with the raw observation diff — no
  retries-until-same, no tolerance. That outcome falsifies the design
  (either the fixtures, the stabilise phase, or the engine is
  non-deterministic) and needs root cause before anything else builds.

## Non-goals of 2.4

- No verdicts — comparison labels are observations about observations.
- No claim kinds, no BodyRegistry registration.
- No dispatch changes (`_authorize_dispatch` untouched).
- No Environment object — pins only, inside the manifest payload.
- No portal login (2.2 is AK-side).
- No scheduler/worker wiring — manual consume only; the service CMD
  stays `sleep infinity`.
