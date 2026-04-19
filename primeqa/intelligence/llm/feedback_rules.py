"""Aggregate raw quality signals into prompt-ready rules + dashboard rollups.

The architect's sharpest callout: raw signals dumped into the prompt
become noise. What works is **rules** — natural-language imperatives the
model can act on — with concrete recent examples.

Bad (previous Phase 4 behaviour):
    "The user rejected a prior draft: {reason: wrong_field}"
    "Hallucinated field Account.Last_E..."
    "Runtime failure: MALFORMED_ID: $test_account"

Good (what this module produces):
    ### Common mistakes to avoid in this tenant's past generations:
    - Do not reference fields that aren't present in the metadata above.
      Recent misses: Account.Last_Engagement_Date, Opportunity.Forecast_Category.
    - Every $var must be set by a prior step's state_ref.
      Recent miss: "MALFORMED_ID: $test_account".
    - Don't write to read-only fields in create steps.

This file has two public entry points:

  build_rules_block(tenant_id, window_days=30) → str
    Pre-rendered prompt block. Empty string when no signals — prompt
    includes unconditionally and the model ignores empty lines.

  top_recurring_issues(tenant_id, days) → List[dict]
    Same aggregation powering the dashboard's "top 5 recurring issues"
    list. One source of truth for what the AI is getting wrong.

  correction_rate(tenant_id, days) → dict
    The north-star metric:
        (user_edited + ba_rejected + user_thumbs_down) / generated_TCs
    Computed against test_cases.generation_batch_id so the denominator
    matches AI-generated TCs, not all TCs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from primeqa.intelligence.llm import feedback

log = logging.getLogger(__name__)


# ---- Rule-key classifier --------------------------------------------------
#
# Map each signal into a logical rule key so we can aggregate (e.g.) a
# validator-flagged field + a BA-rejected "wrong_field" + a runtime field
# error under the same "only use fields in metadata" rule.

_RULE_FIELD_NOT_FOUND = "field_not_found"
_RULE_FIELD_NOT_CREATEABLE = "field_not_createable"
_RULE_FIELD_NOT_UPDATEABLE = "field_not_updateable"
_RULE_OBJECT_NOT_FOUND = "object_not_found"
_RULE_UNRESOLVED_STATE_REF = "unresolved_state_ref"
_RULE_WRONG_OBJECT_OR_FIELD = "wrong_object_or_field"
_RULE_INVALID_STEPS = "invalid_steps"
_RULE_MISSING_COVERAGE = "missing_coverage"
_RULE_REDUNDANT = "redundant"
_RULE_REGENERATION_CHURN = "regeneration_churn"
_RULE_RUNTIME_FAILURE = "runtime_failure"
_RULE_GENERIC_REJECTION = "generic_rejection"


def _classify_signal(signal: Dict[str, Any]) -> str:
    """Return the rule key this signal maps to.

    `signal` is the dict shape returned by `feedback.recent_for_tenant`:
      {signal_type, severity, detail, captured_at}
    """
    stype = signal.get("signal_type")
    detail = signal.get("detail") or {}

    # Validator signals carry an explicit rule in detail — re-use it
    # verbatim when known.
    if stype in (feedback.SIGNAL_VALIDATION_CRITICAL,
                 feedback.SIGNAL_VALIDATION_WARNING):
        return detail.get("rule") or _RULE_WRONG_OBJECT_OR_FIELD

    # Human signals with a reason.
    if stype in (feedback.SIGNAL_USER_THUMBS_DOWN, feedback.SIGNAL_BA_REJECTED):
        reason = detail.get("reason")
        if reason == feedback.REASON_WRONG_OBJECT_OR_FIELD:
            return _RULE_WRONG_OBJECT_OR_FIELD
        if reason == feedback.REASON_INVALID_STEPS:
            return _RULE_INVALID_STEPS
        if reason == feedback.REASON_MISSING_COVERAGE:
            return _RULE_MISSING_COVERAGE
        if reason == feedback.REASON_REDUNDANT:
            return _RULE_REDUNDANT
        return _RULE_GENERIC_REJECTION

    if stype == feedback.SIGNAL_USER_EDITED:
        # Implicit: we don't know WHY the user edited. Fold into the
        # generic "AI output needed correction" bucket. Lower weight
        # than explicit signals.
        return _RULE_GENERIC_REJECTION

    if stype == feedback.SIGNAL_EXECUTION_FAILED:
        # Error text often contains field/object clues — classify via
        # substring match so the rules block stays actionable.
        err = (detail.get("error") or "").lower()
        if "state_ref" in err or "unresolved" in err or err.startswith("$"):
            return _RULE_UNRESOLVED_STATE_REF
        return _RULE_RUNTIME_FAILURE

    if stype == feedback.SIGNAL_REGENERATED_SOON:
        return _RULE_REGENERATION_CHURN

    return _RULE_GENERIC_REJECTION


# ---- Natural-language rule templates --------------------------------------
#
# These go into the prompt verbatim. The architect's point: imperatives
# beat observations. "Do not reference fields not present in metadata"
# works; "the user rejected a test case" does not.

_RULE_TEXTS: Dict[str, str] = {
    _RULE_FIELD_NOT_FOUND: (
        "Only reference fields that exist in the metadata above. "
        "Do not invent fields by transforming English phrases."
    ),
    _RULE_FIELD_NOT_CREATEABLE: (
        "Do not write to read-only fields in `create` steps. "
        "Formula fields, system fields (`CreatedDate`, `Id`), and "
        "rollup summaries are read-only."
    ),
    _RULE_FIELD_NOT_UPDATEABLE: (
        "Do not write to non-updateable fields in `update` steps."
    ),
    _RULE_OBJECT_NOT_FOUND: (
        "Only reference objects (`target_object`, SOQL `FROM`) listed "
        "in the metadata above."
    ),
    _RULE_UNRESOLVED_STATE_REF: (
        "Every `$var` used in a later step MUST be set by a prior step's "
        "`state_ref`. Unresolved references fail fast at runtime."
    ),
    _RULE_WRONG_OBJECT_OR_FIELD: (
        "Use the exact object and field API names from the metadata. "
        "Similar-sounding names (e.g. `Last_Contacted` vs `LastActivityDate`) "
        "are not interchangeable."
    ),
    _RULE_INVALID_STEPS: (
        "Validate that each step's action + target combination is "
        "actually achievable on the target object (e.g. `convert` only "
        "applies to `Lead`; `delete` respects cascade rules)."
    ),
    _RULE_MISSING_COVERAGE: (
        "Include boundary, negative-validation, and edge-case coverage "
        "whenever the requirement describes thresholds, required fields, "
        "or validation logic."
    ),
    _RULE_REDUNDANT: (
        "Do not produce two test cases that cover the same scenario "
        "under different titles. Each coverage_type slot should test a "
        "distinct angle."
    ),
    _RULE_REGENERATION_CHURN: (
        "Past drafts for similar requirements were regenerated quickly "
        "— produce a stronger, more specific plan on the first attempt."
    ),
    _RULE_RUNTIME_FAILURE: (
        "Past generations produced tests that failed at runtime with "
        "Salesforce errors. Prefer fields + objects you can verify from "
        "the metadata above."
    ),
    _RULE_GENERIC_REJECTION: (
        "Past test cases for this tenant were rejected by reviewers. "
        "Double-check that each test has a clear, correct purpose."
    ),
}


# ---- Example extraction ---------------------------------------------------
#
# For each rule, grab up to 3 concrete recent examples so the prompt has
# specific "don't do this" references (not abstract rules only).

def _extract_example(signal: Dict[str, Any]) -> str:
    detail = signal.get("detail") or {}
    stype = signal.get("signal_type")

    if stype in (feedback.SIGNAL_VALIDATION_CRITICAL,
                 feedback.SIGNAL_VALIDATION_WARNING):
        obj = detail.get("object", "?")
        field = detail.get("field")
        if field:
            return f"{obj}.{field}"
        return detail.get("message", "")[:80]

    if stype == feedback.SIGNAL_EXECUTION_FAILED:
        return (detail.get("error") or "")[:100]

    if stype == feedback.SIGNAL_USER_THUMBS_DOWN:
        txt = detail.get("reason_text")
        if txt:
            return txt[:100]
        return detail.get("reason", "") or ""

    if stype == feedback.SIGNAL_BA_REJECTED:
        return (detail.get("reason_text")
                or detail.get("reason")
                or detail.get("feedback", ""))[:100]

    if stype == feedback.SIGNAL_USER_EDITED:
        # Implicit — we don't have a specific example; just note the TC id.
        tc_id = detail.get("tc_id") or signal.get("test_case_id")
        return f"TC #{tc_id}" if tc_id else ""

    return ""


# ---- Public entry points --------------------------------------------------

def build_rules_block(
    tenant_id: int,
    *,
    window_days: int = 30,
    max_rules: int = 5,
    max_examples_per_rule: int = 3,
    min_signal_count: int = 1,
    db=None,
) -> str:
    """Assemble a prompt-ready 'Common mistakes to avoid' block.

    Returns an empty string when there are no signals (caller can inject
    unconditionally). Otherwise returns a newline-delimited block ready
    to drop into the dynamic part of a prompt.

    Rule ranking: frequency × severity weight (high=3, medium=2, low=1).
    Ties broken by recency of the latest signal in the group.
    """
    # Pull a bigger window than the default prompt-feed (Phase 4 used 7
    # days / limit 5). For the rules block we want stability, not
    # recency-only — 30 days with top-5 rules gives a reliable signal
    # without being so old that it dilutes recent improvements.
    signals = feedback.recent_for_tenant(
        tenant_id,
        limit=max_rules * 8,     # over-fetch to leave room after grouping
        window_days=window_days,
        min_severity="medium",
        exclude_positive=True,
        db=db,
    )
    if not signals:
        return ""

    # Group by rule key.
    sev_weight = {"high": 3, "medium": 2, "low": 1}
    groups: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "weight": 0, "examples": [], "latest": ""},
    )
    for sig in signals:
        rule = _classify_signal(sig)
        g = groups[rule]
        g["count"] += 1
        g["weight"] += sev_weight.get(sig.get("severity", "medium"), 2)
        ex = _extract_example(sig)
        if ex and ex not in g["examples"]:
            g["examples"].append(ex)
        # Track latest seen (signals are pre-sorted newest-first, so the
        # first one we encounter for a group is the latest).
        if not g["latest"]:
            g["latest"] = sig.get("captured_at", "") or ""

    # Filter + rank: weight desc, count desc, latest desc.
    ranked: List[Tuple[str, Dict[str, Any]]] = [
        (rule, g) for rule, g in groups.items()
        if g["count"] >= min_signal_count
    ]
    ranked.sort(key=lambda x: (x[1]["weight"], x[1]["count"], x[1]["latest"]),
                reverse=True)
    ranked = ranked[:max_rules]

    # Render.
    lines = ["", "### Common mistakes to avoid (from past generations in this tenant):"]
    for rule, g in ranked:
        rule_text = _RULE_TEXTS.get(rule, rule.replace("_", " ").capitalize())
        examples = g["examples"][:max_examples_per_rule]
        if examples:
            ex_str = "; ".join(examples)
            lines.append(f"- {rule_text}  Recent misses: {ex_str}.")
        else:
            lines.append(f"- {rule_text}")
    lines.append("")  # trailing newline for clean concatenation
    return "\n".join(lines)


def top_recurring_issues(
    tenant_id: int,
    *,
    window_days: int = 30,
    limit: int = 5,
    db=None,
) -> List[Dict[str, Any]]:
    """Dashboard-facing aggregation. Same grouping as the prompt block
    but returns structured rows for rendering in a table."""
    signals = feedback.recent_for_tenant(
        tenant_id,
        limit=limit * 8,
        window_days=window_days,
        min_severity="medium",
        exclude_positive=True,
        db=db,
    )
    if not signals:
        return []

    sev_weight = {"high": 3, "medium": 2, "low": 1}
    groups: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "weight": 0, "examples": [],
                 "signal_types": set()},
    )
    for sig in signals:
        rule = _classify_signal(sig)
        g = groups[rule]
        g["count"] += 1
        g["weight"] += sev_weight.get(sig.get("severity", "medium"), 2)
        g["signal_types"].add(sig.get("signal_type"))
        ex = _extract_example(sig)
        if ex and ex not in g["examples"]:
            g["examples"].append(ex)

    rows = []
    for rule, g in groups.items():
        rows.append({
            "rule": rule,
            "label": _RULE_TEXTS.get(rule, rule.replace("_", " ").capitalize()),
            "count": g["count"],
            "weight": g["weight"],
            "examples": g["examples"][:3],
            "signal_types": sorted(g["signal_types"]),
        })
    rows.sort(key=lambda r: (r["weight"], r["count"]), reverse=True)
    return rows[:limit]


# ---- Correction rate (the north-star quality metric) ----------------------

def correction_rate(
    db,
    tenant_id: int,
    *,
    days: int = 30,
) -> Dict[str, Any]:
    """The north-star: what fraction of AI-generated TCs needed human
    correction in the window?

    Numerator:   distinct test_case_ids with ANY of {user_edited,
                 ba_rejected, user_thumbs_down} in the window.
    Denominator: AI-generated TCs created in the window (those with a
                 `generation_batch_id`).

    Returns a dict with corrected / total / rate / delta (window-over-
    window) so the dashboard can draw an arrow + trend.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as sql

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    prev_start = now - timedelta(days=days * 2)

    # Single round-trip: compute all four numbers (current + previous
    # window for both denom + corrected) in one SELECT using conditional
    # aggregation. At Railway's ~650ms RTT, 4 queries → 1 saves ~2 sec.
    # test_cases has no created_at; we use updated_at as a proxy (AI TCs
    # are rarely edited post-creation, so updated_at ≈ created_at).
    row = db.execute(sql("""
        WITH tc_in_window AS (
          SELECT id,
            CASE WHEN updated_at >= :start THEN 1 ELSE 0 END AS in_cur,
            CASE WHEN updated_at >= :prev_start AND updated_at < :start THEN 1 ELSE 0 END AS in_prev
          FROM test_cases
          WHERE tenant_id = :tid
            AND generation_batch_id IS NOT NULL
            AND deleted_at IS NULL
            AND updated_at >= :prev_start
        ),
        corrections AS (
          SELECT DISTINCT test_case_id,
            MIN(CASE WHEN captured_at >= :start THEN 1 ELSE 0 END) AS ignore_a,
            MAX(CASE WHEN captured_at >= :start THEN 1 ELSE 0 END) AS hit_cur,
            MAX(CASE WHEN captured_at >= :prev_start AND captured_at < :start THEN 1 ELSE 0 END) AS hit_prev
          FROM generation_quality_signals
          WHERE tenant_id = :tid
            AND captured_at >= :prev_start
            AND test_case_id IS NOT NULL
            AND signal_type IN ('user_edited', 'ba_rejected', 'user_thumbs_down')
          GROUP BY test_case_id
        )
        SELECT
          COALESCE(SUM(tc_in_window.in_cur), 0)::int  AS denom,
          COALESCE(SUM(tc_in_window.in_prev), 0)::int AS prev_denom,
          (SELECT COALESCE(SUM(hit_cur), 0)::int  FROM corrections) AS corrected,
          (SELECT COALESCE(SUM(hit_prev), 0)::int FROM corrections) AS prev_corrected
        FROM tc_in_window
    """), {"tid": tenant_id, "start": start, "prev_start": prev_start}).one()._mapping

    denom = row["denom"] or 0
    prev_denom = row["prev_denom"] or 0
    corrected = row["corrected"] or 0
    prev_corrected = row["prev_corrected"] or 0

    rate = (corrected / denom) if denom else 0.0
    prev_rate = (prev_corrected / prev_denom) if prev_denom else None
    delta = (rate - prev_rate) if prev_rate is not None else None

    return {
        "days": days,
        "corrected": int(corrected),
        "total": int(denom),
        "rate": round(rate, 4),
        "prev_rate": round(prev_rate, 4) if prev_rate is not None else None,
        "delta": round(delta, 4) if delta is not None else None,
    }
