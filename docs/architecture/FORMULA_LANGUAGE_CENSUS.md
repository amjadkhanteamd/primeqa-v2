# FORMULA_LANGUAGE_CENSUS.md — the Salesforce formula language vs Plimsol's parser and evaluator

> **Status: reference document, written 2026-08-10 on branch
> `docs-formula-language-census` at main `6ed0515`.** Every PARSE/EVALUATE
> cell below is **VERIFIED by running the construct through the real
> `primeqa.semantic.formula` parser and evaluator** (the census harness —
> method in §6); no cell is inferred from reading code. SOUND cells compare
> the observed behaviour against Salesforce semantics and are tagged
> VERIFIED (with a source), LIVE-CORROBORATED (env-59 run evidence), or
> **UNKNOWN-NEEDS-ORG** where the Salesforce side cannot be established
> without running the construct against an org. §4 is judgment and is marked
> ESTIMATED throughout.
>
> **Why this exists.** Detection (D-434/D-437) needs no formula knowledge —
> it diffs captured text. Attribution needs it for exactly one decision:
> *did this rule fire or not*. And the D-337 pre-emission guard needs it to
> refuse claims that provably violate a rule before they ship. Formula
> coverage therefore determines which failures can be explained and which
> bad claims get refused. The eight "shape families" describe env-59's 52
> rules; **they are not a taxonomy of the language** — this census
> establishes the real boundary.

**The three questions, answered up front:**

* **What works?** Bare-field comparisons against literals (all seven
  operators), the logical connectives (Kleene three-valued), ISBLANK,
  ISNULL-on-numbers, ISPICKVAL against non-empty literals, and — since
  D-439, with an evaluation context — ISNEW, ISCHANGED-on-update,
  PRIORVALUE (composed), field-vs-field numeric, and
  `RecordType.DeveloperName` resolution. **11 construct groups, category A.**
* **What is dangerous?** Three confirmed category-B members — ISNULL on
  TEXT fields, `ISPICKVAL(f, "")` as a blank test, and the cross-cutting
  payload-model gap (absent-from-payload is treated as blank, but an
  automation may have written the field org-side) — plus two
  case-sensitivity probes that are conditional-B until org-verified. §2.B
  names every failure mode.
* **Where does explanation legitimately stop?** Category D: `$`-globals
  (executing identity, org config), cross-object parent *data* traversal,
  VLOOKUP/custom-metadata lookups, time-of-day/timezone clock semantics,
  ISCLONE, and org-computed operands (formula fields, rollups, encrypted,
  geolocation, multi-currency). **These are not gaps to close; a declared
  unknown with a stated reason is the correct terminal answer.** ~15
  construct groups.

**Category counts (construct-group granularity, tallied from §1):
A = 11 · B = 3 confirmed + 2 conditional · C = 14 · D = 15.**

---

## 1. The construct census

Columns: **PARSE** (vr dialect, the one attribution and D-337 use) ·
**EVALUATE** (what the evaluator returned in the harness) · **SOUND**
(can it produce a WRONG verdict rather than an honest unknown?) ·
**CAT**egory. `NE[…]` = NonEvaluable with that reason. All rows harness-run.

### 1.1 Operators

| Construct | PARSE | EVALUATE | SOUND | CAT |
|---|---|---|---|---|
| `< > <= >= = <> !=` field-vs-literal (number) | ok (`!=` normalised to `<>`) | True/False | sound; string/bool payloads vs number literal → NE (honest); None → NE | **A** |
| `=` / `<>` field-vs-literal (text) | ok | True/False, **case-sensitive** (`'x'` vs `'X'` → False) | SF formula text comparison believed case-sensitive but **UNKNOWN-NEEDS-ORG**; if SF is case-insensitive this is a wrong-verdict path | **A** (conditional-B on the case probe) |
| `=` field-vs-TRUE/FALSE (checkbox) | ok | True/False | sound | **A** |
| Field-vs-field numeric (`A__c > B__c`) | ok | True/False (D-439) | sound: None/absent/non-numeric/bool → NE; SF blank-vs-zero ambiguity unreachable by construction | **A** |
| `&&` `\|\|` and `AND()` `OR()` `NOT()` | ok | Kleene three-valued | sound — Kleene only *adds* honest unknowns; a determinable side still resolves | **A** |
| Parentheses / nesting | ok | correct | sound | **A** |
| Arithmetic `+ - * /` (incl. date arithmetic `CloseDate - 7`) | **NotParsed** (`trailing tokens at '+'` etc.) | — | honest | **C** (deterministic; medium — parser + numeric/date eval; the *value* dialect already parses arithmetic) |
| Exponentiation `^` | **NotParsed** (`unexpected character '^'`) | — | honest | **C** (small) |
| Concatenation `&` | **NotParsed** (`unexpected character '&'`) | — | honest | **C** (small, with the string family) |
| Constant formula `TRUE` / `false` | ok | NE[`constant boolean predicate`] | honest (12 env-59 dead rules are literal `false`) | **C** (trivial) |
| Bare boolean field / `NOT(field)` | ok | NE[`bare field predicate (type-uncertain)`] | honest | **C** (small — needs S1 field-type to confirm checkbox) |
| Percent-typed operands in comparisons | ok | compares in API/display space | **sound, LIVE-CORROBORATED**: VR02's covering claims stage `Discount=20` (API) and the org FIRES `> 0.20` — impossible in fraction space (0.20 > 0.20 = False) — so VR formulas compare percent in display space, the same space as our payloads | **A** |

### 1.2 Logical / state functions

| Construct | PARSE | EVALUATE | SOUND | CAT |
|---|---|---|---|---|
| `ISBLANK` | ok | None/`""`/absent → True; `0` → False | matches SF (0 is not blank); **cross-cutting caveat §2.B-3** (absent-from-payload vs org state) | **A** |
| `ISNULL` on number operands | ok | as ISBLANK | matches SF for numbers — all 6 env-59 uses are on `double`/`percent` fields (verified via `field_details.field_type`) | **A** |
| `ISNULL` on TEXT operands | ok | `""`/None → **True** | **UNSOUND — B.** SF documented: *"Text fields are never null, so using ISNULL() with a text field always returns false"* — we return True on empty text → wrong-direction verdict | **B** |
| `ISPICKVAL(f, "nonempty")` | ok | equality against the payload value; absent → False | matches SF blank≠literal; **case probe** (`"approved"` vs `"Approved"` → False) is **UNKNOWN-NEEDS-ORG** → conditional-B | **A** (conditional-B on case) |
| `ISPICKVAL(f, "")` blank test | ok | blank → **False** | **UNSOUND — B.** SF's own behaviour for the empty-literal blank test is inconsistent/disputed in its ecosystem (the recommended idiom is `ISBLANK(TEXT(f))` precisely because of it); if SF returns True on blank, we produce a wrong verdict. Should be forced NE until org-verified | **B** |
| `ISNEW()` | ok | ctx-create → True; ctx-update → False; no ctx → NE | sound (semantics source-verified, D-439) | **A** |
| `ISCHANGED(f)` update-context | ok | pair compare; absence either side → NE; no ctx → NE | sound; **>1 mutation step → NE by the pinned guard** (D-441: the guard refused a real wrong verdict on VR05's 3-step claim) | **A** |
| `ISCHANGED(f)` create-context | ok | **NE** (`refusing to guess`) | honest — SF create semantics not conclusively verified (D-439); closable by verification alone | **C** |
| `PRIORVALUE` composed (`ISPICKVAL(PRIORVALUE(f), lit)`) | ok | resolves prior (create → current, source-verified) | sound (D-439) | **A** |
| `PRIORVALUE` standalone comparison (`PRIORVALUE(f) = 'x'`) | ok | NE[`comparison without a single field + literal`] | honest | **C** (small — same composition machinery) |
| `IF` / `CASE` (vr dialect) | **NotParsed** (`unknown function`) | — | honest; the value dialect already parses `IF` | **C** (small port) |
| `NULLVALUE` / `BLANKVALUE` / `ISNUMBER` | **NotParsed** | — | honest | **C** (small) |
| `ISCLONE()` | **NotParsed** | — | honest — and clone context does not exist in run evidence | **D** |

### 1.3 Text functions

| Construct | PARSE | EVALUATE | SOUND | CAT |
|---|---|---|---|---|
| `TEXT` `VALUE` `LEN` `LEFT` `RIGHT` `MID` `FIND` `SUBSTITUTE` `TRIM` `LOWER` `UPPER` `BEGINS` `CONTAINS` | **NotParsed** (`unknown function`) | — | honest | **C** (medium as a family; each needs an SF-semantic pin when armed: `FIND` is 1-indexed/0-on-miss, `TEXT(picklist)` yields the API value, locale/number formatting for `TEXT(number)`) |
| `INCLUDES` (multi-select) | **NotParsed** | — | honest | **C** (small-medium; semicolon-set semantics) |
| `REGEX` | **ok** (D-344) | NE[`function REGEX not evaluable`]; Kleene guards still resolve (`NOT(ISBLANK(blank)) && …` → False) | honest — deliberately inert: D-344 kept "the Salesforce-vs-Python regex-semantics risk out of attribution". Arming needs 4 guards (fullmatch, `\\`-unescape, Java-syntax → NE, blank → NE) or it becomes B | **C** (small, guard-as-test) |
| `HYPERLINK` `IMAGE` `BR` `CASESAFEID` | **NotParsed** | — | honest; presentation functions, near-zero VR relevance | **C** (trivial, low value) |

### 1.4 Date / time

| Construct | PARSE | EVALUATE | SOUND | CAT |
|---|---|---|---|---|
| `TODAY()` in comparison | **ok** (D-344) | NE[`comparison without a single field + literal`] | honest. Arming needs date-parse + an injected **run-date** clock (attribution-time TODAY = wrong verdicts near boundaries) — and the corpus's covering claims stage RelativeDate TOKENS (dict payloads → NE regardless), an S4 evidence arc (D-424 pattern), not evaluator work | **C** (two-part) |
| `DATE` `DATEVALUE` `DAY` `MONTH` `YEAR` `WEEKDAY` `ADDMONTHS` | **NotParsed** | — | honest; deterministic date math given a date value | **C** (small-medium) |
| `NOW` `TIMEVALUE` `DATETIMEVALUE` `HOUR` `MINUTE` `SECOND` | **NotParsed** | — | honest — and time-of-day + **timezone semantics depend on runtime clock/user TZ**, which a staged payload does not carry | **D** |
| RelativeDate token payloads (`{"$relative_date": …}`) | n/a (payload side) | NE | honest | **C** via the S4 realized-value evidence arc |

### 1.5 Number functions

| Construct | PARSE | EVALUATE | SOUND | CAT |
|---|---|---|---|---|
| `ABS` `CEILING` `FLOOR` `ROUND` `MOD` `MAX` `MIN` `SQRT` `LOG` `LN` `EXP` | **NotParsed** | — | honest; deterministic. Named hazard for arming: SF `ROUND` is round-half-up; Python's built-in `round` is banker's — armed naively this family becomes B | **C** (small-medium) |
| `DISTANCE` / `GEOLOCATION` | **NotParsed** | — | honest — geolocation compound fields are not represented in payloads | **D** |

### 1.6 Cross-object and global variables

| Construct | PARSE | EVALUATE | SOUND | CAT |
|---|---|---|---|---|
| `RecordType.DeveloperName = "lit"` | ok | resolves via injected S1 resolver (D-439); no resolver / no `RecordTypeId` / unresolvable → NE | sound (metadata-resolvable — the one dotted ref whose target lives in S1, not in a parent record) | **A** |
| Parent traversal (`Account.Name`, `Account.Owner.Profile.Name`, `X__r.F__c`) | ok (dotted refs parse) | NE[`cross-object ref …`] | honest — **parent record DATA does not exist at evaluation time** | **D** |
| `VLOOKUP`, `$CustomMetadata`, `$Setup` | **NotParsed** (`$` / unknown function) | — | honest — runtime org-data/config lookups | **D** |
| `$User` `$Profile` `$Organization` `$Permission` `$System` `$ObjectType` `$Label` `$Api` | **NotParsed** (`unexpected character '$'`) | — | honest — **executing-user identity and org configuration are not payload facts.** Note: `$User.Id` alone could someday resolve via the run-as identity arc (D-415, parked) — a policy choice, not a payload fact | **D** |

### 1.7 Field types as operands (payload-side)

| Operand type | Behaviour | SOUND | CAT |
|---|---|---|---|
| number / text / boolean / picklist API values | evaluate directly | per rows above | A |
| percent | display-space compare | **LIVE-CORROBORATED sound** (§1.1) | A |
| formula fields | never in a posted payload (computed org-side) | honest — absent → NE on comparison | **D** |
| roll-up summaries | computed org-side from children | honest | **D** |
| encrypted fields | masked; representative value unknowable | honest | **D** |
| geolocation compounds | not represented | honest | **D** |
| currency under multi-currency | conversion rates are org config | honest | **D** |
| long text areas | behave as text operands where compared | as text | A (as operand) |

---

## 2. The four categories

### A. EVALUABLE AND SOUND — 11 construct groups

Comparisons (number; text-with-case-caveat; boolean), field-vs-field
numeric, logical connectives (both spellings, Kleene), parentheses,
ISBLANK, ISNULL-on-numbers, ISPICKVAL-vs-non-empty-literal,
ISNEW/ISCHANGED-update/PRIORVALUE-composed (with EvalContext),
RecordType.DeveloperName (with resolver), percent operands.

### B. EVALUABLE BUT UNSOUND — 3 confirmed + 2 conditional. **The dangerous category.**

1. **`ISNULL` on TEXT-typed operands.** SF documented: *"Text fields are
   never null"* → always False; we return True on `""`/None. Wrong-direction
   verdict. Zero env-59 instances (all 6 local uses are number-typed — §3),
   so the exposure is prospective. Fix direction: field-type-aware ISNULL
   (S1 knows the type) or force NE on text operands until then.
2. **`ISPICKVAL(field, "")` as a blank test.** We evaluate blank → False;
   Salesforce's own empty-literal behaviour is inconsistent enough that its
   ecosystem's recommended blank test is `ISBLANK(TEXT(f))`. If SF returns
   True on blank, we produce a wrong verdict. Zero env-59 instances. Fix
   direction: force NE for the empty-literal case until **org-verified**
   (UNKNOWN-NEEDS-ORG).
3. **The payload-model gap (cross-cutting, the deepest B).** The evaluator's
   state is the *posted* payload (create ⊕ changes). A field absent from it
   evaluates as blank — but the org record at graded time may hold an
   **automation-written value** (the D-425 `before_save_automation_overwrote`
   cause exists because automations demonstrably mutate posted values). Any
   ISBLANK/comparison over such a field can be confidently wrong. This is
   not a construct defect; it is the evaluation model's boundary. Honest
   close: before-state/read-back capture (the logged D-203 residual) — until
   then this caveat applies to every category-A verdict on fields the
   payload does not set.
4. *(conditional)* **ISPICKVAL case sensitivity** — ours is case-sensitive;
   SF side UNKNOWN-NEEDS-ORG. If SF compares case-insensitively,
   mismatched-case literals produce wrong verdicts.
5. *(conditional)* **Text `=`/`<>` case sensitivity** — same shape;
   believed case-sensitive in SF formulas, UNKNOWN-NEEDS-ORG.

### C. PARSES (or trivially parseable), NOT EVALUABLE — 14 groups, closable

With rough effort: REGEX (small, 4 guards-as-tests); TODAY comparisons
(small-medium + the S4 token arc); PRIORVALUE-standalone (small);
bare-boolean-field (small, S1 type); constant TRUE/FALSE (trivial);
ISCHANGED-on-create (verification only); IF/CASE port (small); arithmetic +
date arithmetic (medium); `&` concat (small); `^` (small); string family
TEXT/VALUE/LEN/…/CONTAINS (medium, per-function pins); INCLUDES
(small-medium); NULLVALUE/BLANKVALUE/ISNUMBER (small); number family
(small-medium, ROUND hazard); DATE/date-part family (small-medium);
HYPERLINK/IMAGE/BR/CASESAFEID (trivial, low value).

### D. NOT EVALUABLE IN PRINCIPLE from a staged payload — 15 groups. **The most important output.**

`$User`, `$Profile`, `$Organization`, `$Permission`, `$System`,
`$ObjectType`, `$Label`, `$Api` (executing identity / org config); parent
DATA traversal; VLOOKUP; `$CustomMetadata`/`$Setup`; NOW/TIMEVALUE/
DATETIMEVALUE/HOUR/MINUTE/SECOND (+ timezone semantics); ISCLONE; formula
fields as operands; roll-up summaries; encrypted fields; geolocation +
DISTANCE; multi-currency conversion. **For each, a declared unknown with
the stated reason is the correct terminal answer** — the attribution
vocabulary already carries it (`vr_formula_indeterminate` names the rule and
says why). The one deliberate exception recorded: `RecordType.DeveloperName`
looked like D but was metadata-resolvable (D-439) — the test for D is
"does the fact live anywhere Plimsol can already see", and for these
fifteen it does not.

---

## 3. Corpus cross-check (env-59, 52 active rules — all queries this date)

**Construct frequency (rules containing):** AND 20 · ISBLANK 19 ·
ISPICKVAL 18 · NOT 15 · ISCHANGED 6 · OR 6 · ISNULL 6 · REGEX 5 · TODAY 3 ·
TEXT 2 · PRIORVALUE 1. Operators: `&&` 10 · `>` 8 · dotted-ref 8 · `<>` 5 ·
`||` 5 · `$`-global 3 · `<` 2 · `=` 1 · `!=` 1 · `<=` 1 ·
`RecordType.DeveloperName` 1.

**The 5 NotParsed rules and what defeats each:** sfFma
`FullNameUpdatePrevention` / `DataTypeUpdatePrevention` /
`DataFlowDirectionUpdatePrevention` → `unexpected character '$'`
(`$User.Id <> CreatedById` — category D); CHANNEL_ORDERS `Special_Fields` →
`unknown function 'Text'`; CHANNEL_ORDERS `OrderTypeRequired` →
`unknown function 'TEXT'` (category C, string family).

**Category B/C constructs present in env-59 — the concrete near-term gaps:**
- **C**: REGEX (5 rules; 2 org-native — PLS_FB_VR01 with 2 approved claims,
  VR09 with none), TODAY (3 rules: VR06 [3 approved claims, token-blocked],
  VR10's OR-disjunct [Kleene-bypassed], Close_Date [no claims]), TEXT (2
  managed rules), ISCHANGED-on-create (no covering create-graded claim
  today).
- **B**: **none of the three confirmed B members has an env-59 instance** —
  all 6 ISNULL uses are number-typed (verified per-field:
  `double`/`percent`), and no rule uses the `ISPICKVAL(f,"")` idiom. The
  cross-cutting payload-model gap (B-3) applies latently to every rule whose
  fields an automation also writes.

**Category A that env-59 never exercises — untested capability, which is
not the same as working capability:** `!=` (1 managed rule, unexercised by
any claim), boolean-literal equality, `<=`/`<` shapes (mostly managed),
ISNEW (parses nowhere in the corpus — no active rule uses it), and
field-vs-field beyond the single `Loan_Exceeds` instance. These evaluate
correctly in the harness but have never produced a live verdict.

---

## 4. Real-world exposure — **ESTIMATED, judgment, reasoning shown**

**Common vs rare among B/C/D** (basis: published validation-rule pattern
collections, the managed-package rules in this very org as a proxy for
enterprise packages, and practitioner experience — marked judgment):
- **Common in enterprise orgs:** cross-object parent traversal (D),
  `$User`/`$Profile`/`$Permission` (D), `TEXT(picklist)` comparisons (C),
  CONTAINS/BEGINS (C), TODAY/date comparisons (C), ISBLANK/ISPICKVAL/
  comparisons (A), arithmetic (C), IF/CASE (C), ISNEW/ISCHANGED/PRIORVALUE
  (A with context).
- **Moderate:** REGEX (C), INCLUDES (C), NULLVALUE/BLANKVALUE (C), number
  functions (C), VLOOKUP (D), custom metadata/settings (D).
- **Rare:** DISTANCE/GEOLOCATION (D), ISCLONE (D), HYPERLINK/IMAGE/BR (C),
  encrypted-field operands (D), `^` (C).

**Expected attribution coverage for an arbitrary enterprise org's VRs
(ESTIMATED):**
- **Today (A only): roughly 35–50%** of rules fully evaluable for the
  fire/no-fire decision. Assumptions: simple required-field and
  state-guard rules (ISBLANK/ISPICKVAL/comparisons/logical, now org-state)
  dominate real rule sets; env-59's org-native set runs ~85% evaluable but
  is fixture-clean, while its managed packages (a fairer enterprise proxy)
  run ~50% — the range brackets those.
- **With the C backlog armed** (strings + TEXT + date-compare + IF/CASE +
  arithmetic + REGEX): **roughly 65–80%**.
- **Ceiling imposed by D: roughly 70–85%** — i.e. **15–30% of enterprise
  rules terminate at a declared unknown legitimately** (globals,
  cross-object data, runtime clock, org-computed operands). A rule mixing D
  constructs with evaluable ones still often resolves via Kleene (a True
  disjunct or False conjunct decides) — VR10's TODAY disjunct is the live
  example — which is why the ceiling is not simply 100% minus D-frequency.
- **A wrong number stated as measured is worse than an honest range**: these
  are ESTIMATES with the stated assumptions, and the env-59 numbers (47/52
  parse = 90%; all 8 local families detection-covered) measure one
  fixture-shaped org, not the market.

---

## 5. The D-337 consequence

The pre-emission guard (`vr_conflict`) refuses a staged bundle only when it
can **prove** the staged values fire an active rule; anything unprovable is
**admitted**. Verified this date: the guard calls the evaluator **without
an EvalContext** — D-439's arming applies to attribution only — so at
emission time the org-state, RecordType and field-vs-field families are
*still* unproven-admits, along with every C and D construct.

**Currently silently admitted (construct → what the guard does):**
- Org-state (ISCHANGED/PRIORVALUE/ISNEW): NE at the gate → **admit**.
- TODAY/temporal: NE → **admit**. **This class has already shipped
  self-defeating claims**: the deprecated acceptance trio
  `09d08502`/`d0c5aaaf`/`f856a064` failed live on VR06 (staging a
  past/blank start date on an Approved transition), and `79bc47e5`
  (cross-field Kleene-unset, the fourth) is the recorded D-337
  admits-on-unproven specimen — with `f63c41b8` its regenerated draft
  twin, held unapproved.
- REGEX: NE → **admit**; a staged non-matching value on an acceptance path
  would fire PLS_FB_VR01/VR09 — same class, no live specimen (D-344's
  derivation deliberately produces certain non-matches on the prohibition
  side only).
- Cross-object / `$`-globals: NotParsed → zero extracted fields → the rule
  is invisible to the gate → **admit**. The sfFma `$User.Id <> CreatedById`
  rules would fire on ANY staged update to those objects by the integration
  user — self-defeating by construction — currently moot only because no
  claims target those objects.
- ISNULL-on-text / ISPICKVAL-blank (the B members): at the gate these can
  produce a **false proof** as well as a false pass — the only constructs
  where the guard could wrongly *refuse* a valid claim (Kleene `_fires`
  treats unset as unknown, which contains but does not eliminate this).

**Not fixed here.** The census records the boundary; the named follow-ups
remain D-431's owners (evaluator families), the S4 token arc (temporal), and
the D-337 gate-context decision (whether emission-time staging should carry
an EvalContext of its own — a design call, since staged transitions do know
their intended prior state).

---

## 6. Method appendix

- **Harness:** every §1 row executed through `primeqa.semantic.formula.parse`
  (vr dialect; value-dialect spot-checks for IF/arithmetic) and
  `evaluate(..., context=EvalContext(...))` variants (no-context /
  create / update / resolver), at main `6ed0515`. 115 cases; the harness
  lives in the session scratchpad (never committed, per the scratch rule).
- **SF-side sources:** ISNULL-on-text — the documented "text fields are
  never null" rule (Salesforce ISBLANK/ISNULL reference and ecosystem
  documentation); ISPICKVAL-blank — ecosystem-documented inconsistency and
  the `ISBLANK(TEXT())` recommended idiom; ISNEW/PRIORVALUE create
  semantics — the D-439 verify-first sources, pinned in
  `tests/unit/test_formula_eval_d439.py`; REGEX full-match — D-344.
  Percent display-space — live corroboration from VR02's firing behaviour
  on staged API values (impossible under fraction space).
- **Corpus queries:** current-version `entities`/`field_details` reads on
  env-59 (`902850e3`), this date; frequency = rules *containing* a
  construct, not occurrence counts.
- **Terminology:** UNKNOWN-NEEDS-ORG = the Salesforce side can only be
  established by running the construct against an org; deliberately NOT
  inferred (this census performed no org writes).
