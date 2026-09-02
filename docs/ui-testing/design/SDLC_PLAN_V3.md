# UI Testing Programme — SDLC Plan v3
Supersedes plan v2 (2026-08-10, never committed; v2's phase list is
reproduced in the table below as the baseline being updated). Status
date: 2026-09-03 (status lines verified against the repo record at
commit time). Changes from v2: phase 7 ran early (TA-directed, D-464);
productionisation inserted and closed (P-1, D-465/D-470);
verdict-semantics fix inserted (D-466); phase 6 PARKED behind the
single-persona pilot loop (this revision); a report/UI slice enters as
logged scope (this revision — it was never a v2 phase).

## Phase record
| # | Phase | State | Evidence |
|---|---|---|---|
| 0 | VR lane close | DONE | pre-programme (D-430..D-457 arc) |
| 1 | Decisions + client answers | DONE | D1–D9 (Gate 2 package: TAD_V1, HLD §4) |
| 2 | Browser runtime spike | DONE | SPIKE_SCOREBOARD.md, Gate 2 |
| 3A/3B | Substrate | DONE | PHASE3A_TA_ACCEPTANCE.md, D-463 |
| 4 | Multi-standard views | DONE | D-467, VERIFICATION_PHASE4.md |
| 5 | Criterion catalogue + customer authoring | COMPLETE (D-472, D-473, D-475; Part 3 merged @4df366a) | VERIFICATION_PHASE5_PART1/2/3.md |
| 7 | Release detection + attribution | DONE (early, TA-directed) | PHASE7_TA_ACCEPTANCE.md, D-464 |
| P | Productionisation + P-1 | DONE; P-2 open (egress unstable, five observations) | D-465, D-470, VERIFICATION_P1/BASELINE_B1.md |
| 6 | Persona comparison | PARKED (this revision) | see D-entry |
| 8 | Component attribution + fix routing | attribution DONE (phase 7); routing OPEN | |
| 9 | Mode B keyboard | PARKED (unchanged) | |

## Sequence from here (the single-persona pilot loop)
1. Phase 5 Part 3 — DONE (merged @4df366a; tenant 20260903_0010 live).
2. REPORT/UI SLICE (new logged scope): the verdict listing and the
   release-comparison view, read-only over existing data. The demo
   surface. Enters by this plan revision, not by drift.
3. SCHEDULING: per-release runs without manual invocation — what
   "per-release conformance testing" requires for the paying client.
4. INVENTORY GROWTH: a representative surface set on the real portal
   (inventory v2) so coverage numbers stop being two-page numbers.
5. Then, in client-driven order: phase 6 (unparked by a second-audience
   need), phase 8 routing, P-2/D9 (on an allowlisting client), Mode B.

## Parking rationale (phase 6)
A second persona widens a two-page demo; the pilot needs depth on one:
scan → verdicts → release comparison → attribution. Persona sits inside
the frozen identity (D2), so deferral costs zero rework. Revisit
trigger: a pilot client with a second audience.
