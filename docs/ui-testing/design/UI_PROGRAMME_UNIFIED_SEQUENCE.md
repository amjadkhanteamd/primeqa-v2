# UI programme — unified sequence
**Date:** 2026-09-06 · **Status:** DRAFT for TA sitting · reconciles `PLIMSOL_SURFACE_PLAN.md` (2026-08-18, CC-verified, never built) with the TA-ratified integration ruling (2026-09-06).

## 1. The two plans are one plan
The surface plan diagnosed the functional lane's review surface: navigation mirrors the schema, four surfaces render the same four facts four ways, stale greens render as present facts. The integration ruling settled how conformance joins those surfaces: requirement-anchored, kind-differentiated, Releases as the decision point, setup under Settings. Same skeleton. The surface plan supplies the **data-hygiene phases the integration brief skipped**; the ruling supplies the **planner, policy, and conformance panel the surface plan predates**.

**The conformance lane already implements every honesty primitive the surface plan asks of the functional lane** — declared inventory (fixture separation), frozen natural key (identity), manifest pins + org snapshots (the run stamp), NOT_COMPARABLE + drift subtraction (staleness). The functional lane adopts the same primitives; it does not invent parallels.

## 2. Binding rule carried from the surface plan
**Data-layer phases before layout.** A collapse over dishonest data moves the dishonesty to fewer pages. Every layout step below waits on the data step it renders.

## 3. Unified sequence
| # | Step | From | Gate |
|---|---|---|---|
| 0 | Commit both plans; TA sitting on §5 | — | AK |
| A | **Repair-proposal gate**: gate creation (not only apply); three-verdict classification DERIVED / SPECULATIVE / SEMANTIC reusing `coverage_flag.py`; bare keys fail closed as SEMANTIC; confidence removed from the operator surface AND from the automatic gate; both flags on one page; `trust_threshold_medium` wired or removed. Panel dormant until landed | surface §4 | Fork 4 |
| B | **Decision-engine pin**: replace the tenant-wide max-sequence read (latent wrong GO). One staleness resolver, owned by the run stamp; the engine reads run-level readiness | surface §2A/§7 | Fork 2 → precondition of TA condition 4 |
| 1 | **Provenance + identity**: `external_key` ruled (Fork 1); origin representable (`fixture` / `probe` / `CANNOT_CLASSIFY` backfill); default views hide fixtures with a visible hidden-count; one identity format; dangling keys rendered as referential gaps | surface §6 | Fork 1 |
| 2 | **Contemporaneity**: org sequence stamped per run (executor already computes it); readiness NEVER_RUN / STALE / CURRENT / CANNOT_DETERMINE split from outcome; targeted staleness via `test_claim_coverage` (never global); both environments stamped; 726 legacy runs render CANNOT_DETERMINE | surface §7 | Fork 3 |
| 3 | **Requirement→surface link** with provenance (DECLARED v1) + materialised `verifies` links — hangs off the Fork-1 identity ruling | integration b | after 1 |
| 4 | **Run Planner** (TA condition 3): entry points = requirement, release, schedule; Run Tests page removed — the planner's PLAN view is the launcher's honest successor | both | Fork 5 |
| 5 | **Release Quality Policy** (TA condition 4) = `decision_criteria` grown into the policy object, evaluated by DecisionEngine over the Step-B resolver; `Evaluate GO/NO-GO` refuses on stale or never-run scope and names them; "Run the scope" on the release; one release exercised through every state | surface §9 + integration e | after B, 2 |
| 6 | **Layout collapse + integration pages**: Requirements (absorbs Test Library as "all claims" + review filter with badge; conformance card; planner Run), Releases (absorbs Run Tests; gate; readiness), Results (kind filter; root-cause default grouping; UI run view; Dashboard fold VERIFY FIRST), Settings (Conformance setup; org *reference*; Tools→Reference; Ask back to main), **org-state band** on environment control, release page, Results | surface §8 + integration d/f | after 1–5 |

## 4. What the ruling changes in the surface plan
- Fork 5 gains an answer shape: a launcher is not a destination, but a **plan is** — the planner renders what will run before it runs, from the requirement or release context.
- "My Reviews" as a *surface* survives (TA); as a *nav slot* it folds (surface plan). Badge + filter reconciles both.
- Fork 2's lean ("the run stamp owns staleness") is what the conformance lane already does (org snapshot pinned at manifest build) — consistency, not preference.

## 5. TA sitting — one package
Forks 1–5 from `PLIMSOL_SURFACE_PLAN.md` §11 (leans stand: 1→B, 2→run stamp, 3→ship the grey, 4→stop creation, 5→planner-as-successor), plus two reconciliations: Reviews slot vs surface; Dashboard fold pending Phase-0 verification. The `ui-integration` LLD proceeds as design; its implementation waits on Steps A–2.

## 6. Verified figures (surface plan §12, HEAD 414c9020, read-only) — re-verify before build
922 coverage rows / 393 claims; 726 runs, none stamped; 1014 create-step keys, 105 bare; 34 link keys — 7 `req-N` (1 dangling), 6 Jira, 21 fixture/probe; confidence column = LLM self-report, read by the automatic gate; generation/grounding/drift all org-scoped; decision-engine pin org-blind on one branch.
