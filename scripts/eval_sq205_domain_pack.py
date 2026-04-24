"""Domain Packs eval — SQ-205 Opus baseline vs Sonnet + case_escalation.

A/B test the Domain Packs claim: does Sonnet with the case_escalation pack
match or exceed Opus quality on the canonical HIGH-tier requirement?

Runs two real Anthropic calls via the LLM gateway. Meant to be invoked:

    railway run --service primeqa-v2 python scripts/eval_sq205_domain_pack.py

Railway injects DATABASE_URL, the Anthropic key via ConnectionRepository's
Fernet-decrypted connection config, and CREDENTIAL_ENCRYPTION_KEY matching
the local encrypted creds. Running it locally with a dummy key will fail
at connection decrypt; that's expected.

Output goes to stdout as a side-by-side table. Paste into the PR
description under `## Eval: SQ-205 Opus baseline vs Sonnet + case_escalation pack`.
"""

from __future__ import annotations

import os
import sys
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from primeqa.app import app  # noqa: F401 — initialises ORM mappers
from primeqa.db import SessionLocal
from primeqa.core.models import Environment
from primeqa.core.repository import ConnectionRepository
from primeqa.metadata.repository import MetadataRepository
from primeqa.intelligence.llm import llm_call, LLMError
from primeqa.intelligence.knowledge.domain_pack_provider import DomainPackProvider
from primeqa.intelligence.validator import TestCaseValidator
from primeqa.test_management.models import Requirement


TENANT_ID = 1
OPUS = "claude-opus-4-7"
SONNET = "claude-sonnet-4-5-20250929"


def _pick_env_and_connection(db):
    env = (
        db.query(Environment)
        .filter(
            Environment.tenant_id == TENANT_ID,
            Environment.is_active.is_(True),
            Environment.current_meta_version_id.isnot(None),
            Environment.llm_connection_id.isnot(None),
        )
        .first()
    )
    if env is None:
        raise RuntimeError(
            "No active env with metadata + LLM connection for tenant 1 — "
            "nothing to evaluate against."
        )

    cr = ConnectionRepository(db)
    conn = cr.get_connection_decrypted(env.llm_connection_id, tenant_id=TENANT_ID)
    api_key = (conn or {}).get("config", {}).get("api_key")
    if not api_key:
        raise RuntimeError(
            "Env has llm_connection_id but no decrypted api_key. Is "
            "CREDENTIAL_ENCRYPTION_KEY set correctly?"
        )
    return env, api_key


def _pick_sq205(db):
    req = (
        db.query(Requirement)
        .filter(
            Requirement.tenant_id == TENANT_ID,
            Requirement.jira_key == "SQ-205",
            Requirement.deleted_at.is_(None),
        )
        .order_by(Requirement.updated_at.desc())
        .first()
    )
    if req is None:
        # Fall back to any requirement whose text mentions Case escalation
        req = (
            db.query(Requirement)
            .filter(
                Requirement.tenant_id == TENANT_ID,
                Requirement.deleted_at.is_(None),
                Requirement.jira_summary.ilike("%escalat%"),
            )
            .order_by(Requirement.updated_at.desc())
            .first()
        )
    if req is None:
        raise RuntimeError(
            "Could not find SQ-205 (or any escalation-like requirement) "
            "in tenant 1. Import the demo data first."
        )
    return req


def _build_metadata_context(metadata_repo, meta_version_id):
    objs = metadata_repo.get_objects(meta_version_id) or []
    vrs = metadata_repo.get_validation_rules(meta_version_id) if hasattr(
        metadata_repo, "get_validation_rules") else []
    return {
        "objects": [o.api_name for o in objs[:30]],
        "validation_rules": [
            f"{r.object_api_name}.{r.name}" for r in (vrs or [])[:15]
        ],
    }


def _run_generation(requirement, env, api_key, metadata_context, *,
                    model_override, domain_packs):
    """One real llm_call. Returns the response + derived metrics."""
    context = {
        "requirement": requirement,
        "metadata_context": metadata_context,
        "meta_version_id": env.current_meta_version_id,
        "min_tests": 3,
        "max_tests": 6,
        "domain_packs": domain_packs,
    }
    try:
        resp = llm_call(
            task="test_plan_generation",
            tenant_id=TENANT_ID,
            api_key=api_key,
            context=context,
            requirement_id=getattr(requirement, "id", None),
            model_override=model_override,
        )
    except LLMError as e:
        return None, {"error": f"{e.status}: {e.message}"}

    plan = (resp.parsed_content or {}).get("test_plan") or {}
    tcs = plan.get("test_cases") or []
    confidences = [float(tc.get("confidence_score", 0)) for tc in tcs]
    coverage_dist = {}
    for tc in tcs:
        ct = tc.get("coverage_type", "?")
        coverage_dist[ct] = coverage_dist.get(ct, 0) + 1

    return resp, {
        "model": resp.model,
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "cached_input_tokens": resp.cached_input_tokens,
        "cache_write_tokens": resp.cache_write_tokens,
        "cost_usd": resp.cost_usd,
        "latency_ms": resp.latency_ms,
        "escalated": resp.escalated,
        "tc_count": len(tcs),
        "avg_confidence": mean(confidences) if confidences else 0.0,
        "coverage_types": coverage_dist,
        "test_cases": tcs,
    }


def _validate_plan(metadata_repo, meta_version_id, tcs):
    """Run TestCaseValidator against each TC's steps. Returns per-TC
    summary + aggregate severity counts."""
    validator = TestCaseValidator(metadata_repo, meta_version_id)
    per_tc = []
    critical_total = 0
    warning_total = 0
    for i, tc in enumerate(tcs):
        report = validator.validate(tc.get("steps") or [])
        summary = report.get("summary") or {}
        critical = summary.get("critical") or 0
        warning = summary.get("warning") or 0
        critical_total += critical
        warning_total += warning
        per_tc.append({
            "idx": i + 1,
            "title": (tc.get("title") or "")[:60],
            "coverage": tc.get("coverage_type"),
            "confidence": tc.get("confidence_score"),
            "validator_status": report.get("status"),
            "critical": critical,
            "warning": warning,
        })
    return {
        "per_tc": per_tc,
        "critical_total": critical_total,
        "warning_total": warning_total,
    }


def _render_side_by_side(before, after, before_val, after_val):
    lines = []
    lines.append("=" * 78)
    lines.append(
        "Eval: SQ-205 Opus baseline vs Sonnet + case_escalation pack"
    )
    lines.append("=" * 78)
    lines.append("")
    fmt = "{:<28}{:<25}{:<25}"
    lines.append(fmt.format("Metric", "Opus (baseline)", "Sonnet + Pack"))
    lines.append("-" * 78)
    keys = [
        ("Model", "model"),
        ("Input tokens", "input_tokens"),
        ("Output tokens", "output_tokens"),
        ("Cached input tokens", "cached_input_tokens"),
        ("Cache write tokens", "cache_write_tokens"),
        ("Cost USD", "cost_usd"),
        ("Latency ms", "latency_ms"),
        ("Escalated?", "escalated"),
        ("TC count", "tc_count"),
        ("Avg confidence", "avg_confidence"),
    ]
    for label, k in keys:
        b = before.get(k, "—")
        a = after.get(k, "—")
        if isinstance(b, float):
            b = f"{b:.4f}" if k == "cost_usd" else f"{b:.3f}"
        if isinstance(a, float):
            a = f"{a:.4f}" if k == "cost_usd" else f"{a:.3f}"
        lines.append(fmt.format(label, str(b), str(a)))
    # Coverage distribution
    lines.append(fmt.format(
        "Coverage types",
        str(before.get("coverage_types") or {}),
        str(after.get("coverage_types") or {}),
    ))
    lines.append(fmt.format(
        "Validator critical",
        str(before_val.get("critical_total")),
        str(after_val.get("critical_total")),
    ))
    lines.append(fmt.format(
        "Validator warning",
        str(before_val.get("warning_total")),
        str(after_val.get("warning_total")),
    ))
    lines.append("")

    # Per-TC validator details
    lines.append("--- Before: per-TC validator output ---")
    for row in before_val["per_tc"]:
        lines.append(
            f"  {row['idx']}. [{row['coverage']}] "
            f"conf={row['confidence']} "
            f"status={row['validator_status']} "
            f"C={row['critical']} W={row['warning']} "
            f"— {row['title']}"
        )
    lines.append("")
    lines.append("--- After: per-TC validator output ---")
    for row in after_val["per_tc"]:
        lines.append(
            f"  {row['idx']}. [{row['coverage']}] "
            f"conf={row['confidence']} "
            f"status={row['validator_status']} "
            f"C={row['critical']} W={row['warning']} "
            f"— {row['title']}"
        )

    lines.append("")
    # Acceptance criteria
    crit_new = after_val["critical_total"] - before_val["critical_total"]
    coverage_a = set(before.get("coverage_types") or {})
    coverage_b = set(after.get("coverage_types") or {})
    coverage_equal_or_better = coverage_b >= coverage_a or len(coverage_b) >= len(coverage_a)
    confidence_within = abs(
        after.get("avg_confidence", 0) - before.get("avg_confidence", 0)
    ) <= 0.1

    lines.append("=" * 78)
    lines.append("Acceptance criteria")
    lines.append("=" * 78)
    lines.append(
        f"  (a) Coverage breadth (after ⊇ before or equal count): "
        f"{'PASS' if coverage_equal_or_better else 'FAIL'}"
    )
    lines.append(
        f"  (b) Confidence within 0.1 of Opus: "
        f"{'PASS' if confidence_within else 'FAIL'} "
        f"(Δ = {after.get('avg_confidence', 0) - before.get('avg_confidence', 0):+.3f})"
    )
    lines.append(
        f"  (c) Zero new validator-critical: "
        f"{'PASS' if crit_new <= 0 else 'FAIL'} "
        f"(Δ = {crit_new:+d})"
    )
    lines.append("")
    return "\n".join(lines)


def main():
    db = SessionLocal()
    try:
        env, api_key = _pick_env_and_connection(db)
        requirement = _pick_sq205(db)
        metadata_repo = MetadataRepository(db)
        meta_ctx = _build_metadata_context(metadata_repo, env.current_meta_version_id)

        # Resolve case_escalation pack against SQ-205 text
        provider = DomainPackProvider(packs_dir="salesforce_domain_packs")
        req_text = " ".join(filter(None, [
            requirement.jira_summary or "",
            requirement.jira_description or "",
            requirement.acceptance_criteria or "",
        ]))
        packs, attr = provider.get_packs(
            requirement_text=req_text, referenced_objects=None,
        )

        print(f"Requirement: {requirement.jira_key} — {requirement.jira_summary[:80]}")
        print(f"Packs resolved for after-run: {attr}")
        print()
        print("Running BEFORE (Opus, no packs)...")
        resp_before, before = _run_generation(
            requirement, env, api_key, meta_ctx,
            model_override=OPUS, domain_packs=[],
        )
        if resp_before is None:
            print(f"BEFORE failed: {before['error']}")
            sys.exit(1)

        print("Running AFTER (Sonnet + case_escalation pack)...")
        resp_after, after = _run_generation(
            requirement, env, api_key, meta_ctx,
            model_override=SONNET, domain_packs=packs,
        )
        if resp_after is None:
            print(f"AFTER failed: {after['error']}")
            sys.exit(1)

        before_val = _validate_plan(
            metadata_repo, env.current_meta_version_id, before["test_cases"],
        )
        after_val = _validate_plan(
            metadata_repo, env.current_meta_version_id, after["test_cases"],
        )

        out = _render_side_by_side(before, after, before_val, after_val)
        print(out)
    finally:
        db.close()


if __name__ == "__main__":
    main()
