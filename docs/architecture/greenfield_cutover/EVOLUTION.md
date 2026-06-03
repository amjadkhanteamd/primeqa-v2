# Greenfield Cutover — Evolution Log

Append-only. One entry per session that made substantive changes to the cutover design.

---

## 2026-06-03 — Cutover opened + designed, docs-led (D-145 / D-146)

The greenfield cutover — where the substrate spine (S1–S8) replaces the live v1 product and the v1 `meta_*` tables are dropped — was, until now, **undesigned** (its intent scattered across D-012 / D-065 / D-074 / D-111 / D-134) and **not executable as one phase** (verified: `meta_*` is the live store; the S1 sync writer exists but has no production trigger; no `meta_*`→S1 migration; the substrate outputs aren't surfaced in the product). Phase 7 opened it **docs-led** (mirroring how S5 opened, D-134): author the missing design + sequence it, at zero production risk, so the cutover then runs slice-by-slice off the design.

**The SPEC (D-145).** `SPEC.md` consolidates the v1→substrate **disposition map** (REPLACE/ABSORB/DROP/MIGRATE/RELOCATE/RETIRE/STAY, each row D-cited), fixes the **migration strategy** (greenfield **re-sync** from Salesforce, *not* a `meta_*`→S1 backfill — D-012), reconciles the **schema topology** (the realized per-tenant alembic schema — D-015 built; D-023's "begins in public" milestone superseded), and the **non-goals** (the MIGRATE tables → post-cutover; `llm_calls` stays; S4 F2/F4–F7 + the S8 mechanics not cutover-blocking). It also reconciles the terminology: D-012's "Phase 4 cutover" and "the Phase-7 greenfield cutover" name the same event.

**The SEQUENCE (D-146).** `SEQUENCE.md` lays out the cutover as **ordered, gated steps** — reversible-before-irreversible, additive-before-substitutive — each with an entry-gate / exit-gate / rollback, and the one irreversible act (the `meta_*` drop) **strictly last**, gated on a clean parallel-run window + S1 verified as the prod data source: *S1-sync prod trigger → relocations → additive substrate consumers → v1 read-path switch (flagged) → parallel-run validation → `meta_*` drop*. Every "deferred to the cutover" item across the substrate docs (S5 relocation + forward-seam, D-134; S6 UI consumer + clustering dashboard, D-137; GO/NO-GO folding, D-111; the S3-ledger retirement, D-074) is folded into the step it belongs to — the ~dozen scattered deferrals become one tracked checklist (the SEQUENCE coverage table).

**Boundary.** Docs only — no code, no migration, no v1 behavior change (inert deploy). The cutover *execution* is deferred to later phases, each running one gated step. On `phase-15-greenfield-cutover` (D-147 close).
