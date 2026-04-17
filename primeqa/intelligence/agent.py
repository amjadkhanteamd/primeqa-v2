"""R5 Agent: triage failures, propose fixes, (optionally) auto-apply on
sandbox, rerun, and audit everything in agent_fix_attempts.

Design decisions baked in (see docs/design/run-experience.md):
  - Q8: full before-state JSON snapshot on every fix. Revert overwrites.
  - Q12: default trust bands high=0.85, medium=0.60. Super Admin configurable.
  - Q-pre: agent auto-apply only on sandbox envs AND confidence >= high.
          Production always goes through human review.
  - Max 3 attempts per (run lineage, test_case). After that, escalate.

Usage pattern:
    orchestrator = AgentOrchestrator(db, anthropic_client=None)  # no LLM = deterministic only
    orchestrator.handle_failure(failed_step_context)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from primeqa.intelligence.models import AgentFixAttempt, FailurePattern

log = logging.getLogger(__name__)


# ---- Triage classifier (deterministic-first) -------------------------------

FAILURE_TAXONOMY = [
    ("transient",        [r"(timed? out|timeout|connection reset|temporarily unavailable|503|502)"]),
    ("metadata_drift",   [r"(invalid field|no such column|field.*not exist|INVALID_FIELD)"]),
    ("data_drift",       [r"(duplicate value|duplicates? on|DUPLICATE_VALUE)"]),
    ("assertion",        [r"(assertion.*failed|expected .* got)"]),
    ("env_issue",        [r"(INSUFFICIENT_ACCESS|INVALID_SESSION|LOGIN_MUST_USE_SECURITY_TOKEN)"]),
    ("test_bug",         [r"(unresolved reference|undefined variable|KeyError|NameError)"]),
]
TAXONOMY_COMPILED = [
    (cls, [re.compile(p, re.IGNORECASE) for p in pats])
    for cls, pats in FAILURE_TAXONOMY
]


@dataclass
class TriageResult:
    failure_class: str
    pattern_id: Optional[int]
    confidence: float  # how sure we are of the classification
    matched_via: str   # 'taxonomy' | 'pattern_db' | 'unknown'


def classify_failure(db, error_message: str,
                     tenant_id: Optional[int] = None,
                     environment_id: Optional[int] = None) -> TriageResult:
    """Two-stage triage: stored patterns first, then taxonomy regex."""
    if not error_message:
        return TriageResult("unknown", None, 0.0, "unknown")

    # 1. Match against FailurePattern.pattern_signature (existing patterns)
    if tenant_id:
        sig = hashlib.sha256(error_message[:300].encode()).hexdigest()[:16]
        row = db.query(FailurePattern).filter(
            FailurePattern.tenant_id == tenant_id,
            FailurePattern.pattern_signature.like(f"{sig[:8]}%"),
            FailurePattern.status == "active",
        ).first()
        if row:
            return TriageResult(row.failure_type, row.id, float(row.confidence), "pattern_db")

    # 2. Taxonomy regex
    for cls, patterns in TAXONOMY_COMPILED:
        for pat in patterns:
            if pat.search(error_message):
                return TriageResult(cls, None, 0.75, "taxonomy")

    return TriageResult("unknown", None, 0.25, "unknown")


# ---- Fix proposer (LLM-backed, optional) -----------------------------------

SYSTEM_PROMPT = """You are a test-repair assistant for Salesforce automated tests.
Given a failed test step and its error details, propose the smallest possible fix.

Return strict JSON:
{
  "root_cause_summary": "<one sentence>",
  "confidence": <0.0..1.0>,
  "proposed_fix_type": "edit_step" | "regenerate_test" | "update_template" | "retry" | "quarantine" | "review",
  "changes": {<fix-type-specific payload>}
}

Fix-type payloads:
- edit_step:        {"step_order": int, "field_values": {...}} (merge into existing)
- regenerate_test:  {"reason": "..."}
- update_template:  {"template_name": "...", "field_changes": {...}}
- retry:            {"max_retries": int, "backoff_s": int}
- quarantine:       {"reason": "..."}
- review:           {"reason": "..."}
"""


@dataclass
class FixProposal:
    root_cause_summary: str
    confidence: float
    proposed_fix_type: str
    changes: Dict[str, Any]
    raw_llm_response: Optional[str] = None


def propose_fix(context: Dict[str, Any],
                anthropic_client=None,
                model: str = "claude-sonnet-4-20250514") -> Optional[FixProposal]:
    """Ask the LLM for a proposed fix.

    Returns None if no client available. Returns a FixProposal otherwise.
    `context` should include: failure_class, step_definition, error_message,
    api_request, api_response, recent_similar_failures, metadata_diff.
    """
    if not anthropic_client:
        return None

    user_msg = (
        "FAILED TEST STEP:\n" +
        json.dumps(context.get("step_definition"), indent=2, default=str) +
        "\n\nERROR:\n" + (context.get("error_message") or "") +
        "\n\nFAILURE CLASS: " + (context.get("failure_class") or "unknown") +
        "\n\nAPI RESPONSE:\n" + json.dumps(context.get("api_response"), indent=2, default=str)[:2000]
    )

    try:
        resp = anthropic_client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        data = json.loads(_extract_json(text))
        return FixProposal(
            root_cause_summary=str(data.get("root_cause_summary", ""))[:1000],
            confidence=float(data.get("confidence", 0.0)),
            proposed_fix_type=str(data.get("proposed_fix_type", "review")),
            changes=data.get("changes", {}) or {},
            raw_llm_response=text,
        )
    except Exception as e:
        log.exception("propose_fix failed: %s", e)
        return None


def _extract_json(text: str) -> str:
    """Best-effort: pull the first {...} block out of the LLM's response."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("no JSON object found in LLM response")
    return text[start:end + 1]


# ---- Trust-band classifier --------------------------------------------------

def trust_band(confidence: float, *, high: float, medium: float) -> str:
    if confidence >= high:
        return "high"
    if confidence >= medium:
        return "medium"
    return "low"


# ---- Orchestrator -----------------------------------------------------------

@dataclass
class AgentDecision:
    fix_attempt_id: int
    auto_applied: bool
    rerun_triggered: bool
    gate_reason: str  # e.g. 'auto_applied_sandbox_high', 'gated_production', 'gated_low_confidence'


class AgentOrchestrator:
    """Glues triage + proposer + gate policy + audit ledger together.

    Keep the orchestrator itself small; individual primitives above are
    unit-testable in isolation.
    """

    MAX_ATTEMPTS_FALLBACK = 3

    def __init__(self, db, *, anthropic_client=None, model: str = "claude-sonnet-4-20250514"):
        self.db = db
        self.anthropic_client = anthropic_client
        self.model = model

    # -- Settings lookup ----------------------------------------------------

    def _settings(self, tenant_id: int):
        from primeqa.core.agent_settings import AgentSettingsRepository
        return AgentSettingsRepository(self.db).get(tenant_id)

    # -- Main entry point ---------------------------------------------------

    def handle_failure(self, *, run_id: int, test_case_id: int,
                       run_test_result_id: Optional[int],
                       run_step_result_id: Optional[int],
                       tenant_id: int, environment_id: int, env_type: str,
                       error_message: str,
                       step_definition: Optional[Dict[str, Any]] = None,
                       api_request: Optional[Dict[str, Any]] = None,
                       api_response: Optional[Dict[str, Any]] = None,
                       pipeline_run=None) -> Optional[AgentDecision]:
        """Called when a step fails. Returns an AgentDecision or None if agent disabled."""
        settings = self._settings(tenant_id)
        if not settings.agent_enabled:
            return None

        # Enforce per-run attempt cap by walking parent lineage
        lineage_root = self._lineage_root_run_id(run_id)
        attempt_count = self.db.query(AgentFixAttempt).filter(
            AgentFixAttempt.run_id.in_(self._lineage_runs(lineage_root)),
            AgentFixAttempt.test_case_id == test_case_id,
        ).count()
        if attempt_count >= (settings.max_fix_attempts_per_run or self.MAX_ATTEMPTS_FALLBACK):
            log.info("agent: cap reached for tc=%s in lineage %s", test_case_id, lineage_root)
            return None

        # 1. Triage
        triage = classify_failure(self.db, error_message,
                                  tenant_id=tenant_id, environment_id=environment_id)

        # 2. Proposal
        proposal: Optional[FixProposal] = None
        try:
            proposal = propose_fix({
                "failure_class": triage.failure_class,
                "step_definition": step_definition,
                "error_message": error_message,
                "api_request": api_request,
                "api_response": api_response,
            }, anthropic_client=self.anthropic_client, model=self.model)
        except Exception as e:
            log.warning("propose_fix crashed: %s", e)

        confidence = proposal.confidence if proposal else triage.confidence
        band = trust_band(confidence,
                          high=float(settings.trust_threshold_high),
                          medium=float(settings.trust_threshold_medium))

        # 3. Gate decision
        auto_applyable = (
            band == "high"
            and env_type != "production"
            and proposal is not None
            and proposal.proposed_fix_type in ("edit_step", "retry")
        )
        if env_type == "production":
            gate_reason = "gated_production"
        elif band != "high":
            gate_reason = f"gated_band_{band}"
        elif proposal is None:
            gate_reason = "no_proposal"
        elif proposal.proposed_fix_type not in ("edit_step", "retry"):
            gate_reason = f"gated_fix_type_{proposal.proposed_fix_type}"
        else:
            gate_reason = "auto_applied_sandbox_high"

        # 4. Capture before-state snapshot (Q8: full snapshot for revert)
        before_state = self._snapshot_test_case(test_case_id)

        # 5. Persist the fix_attempt row (even if not applied, so UI shows triage)
        fix_attempt = AgentFixAttempt(
            run_id=run_id,
            test_case_id=test_case_id,
            run_test_result_id=run_test_result_id,
            run_step_result_id=run_step_result_id,
            failure_class=triage.failure_class,
            pattern_id=triage.pattern_id,
            root_cause_summary=(proposal.root_cause_summary if proposal
                                else f"Triage: {triage.failure_class} ({triage.matched_via})"),
            confidence=confidence,
            trust_band=band,
            proposed_fix_type=(proposal.proposed_fix_type if proposal else "review"),
            before_state=before_state,
            after_state=(self._compute_after_state(before_state, proposal)
                         if proposal else None),
            auto_applied=False,
            rerun_outcome=None,
        )
        self.db.add(fix_attempt)
        self.db.commit()
        self.db.refresh(fix_attempt)

        # 6. Apply + rerun (if gated to auto_applyable)
        rerun_triggered = False
        if auto_applyable:
            try:
                self._apply_fix(test_case_id, proposal)
                fix_attempt.auto_applied = True
                fix_attempt.rerun_outcome = "pending"
                rerun_id = self._trigger_rerun(pipeline_run=pipeline_run,
                                                 test_case_id=test_case_id,
                                                 parent_run_id=run_id,
                                                 environment_id=environment_id)
                fix_attempt.rerun_run_id = rerun_id
                self.db.commit()
                rerun_triggered = True
            except Exception as e:
                log.exception("apply/rerun failed: %s", e)
                fix_attempt.auto_applied = False
                fix_attempt.rerun_outcome = None
                self.db.commit()
                gate_reason = f"apply_failed:{e}"

        return AgentDecision(
            fix_attempt_id=fix_attempt.id,
            auto_applied=fix_attempt.auto_applied,
            rerun_triggered=rerun_triggered,
            gate_reason=gate_reason,
        )

    # -- User decisions (Accept / Revert / Edit) ----------------------------

    def revert(self, fix_attempt_id: int, tenant_id: int, user_id: int) -> bool:
        """Restore the full before_state snapshot (Q8)."""
        row = self.db.query(AgentFixAttempt).filter_by(id=fix_attempt_id).first()
        if not row or not row.before_state:
            return False
        # Tenant scope check
        from primeqa.test_management.models import TestCase
        tc = self.db.query(TestCase).filter(
            TestCase.id == row.test_case_id, TestCase.tenant_id == tenant_id,
        ).first()
        if not tc:
            return False

        # Restore canonical fields from the snapshot
        self._restore_test_case(row.test_case_id, row.before_state)
        row.user_decision = "reverted"
        row.decided_at = datetime.now(timezone.utc)
        row.decided_by = user_id
        self.db.commit()
        return True

    def accept(self, fix_attempt_id: int, tenant_id: int, user_id: int) -> bool:
        row = self.db.query(AgentFixAttempt).filter_by(id=fix_attempt_id).first()
        if not row:
            return False
        from primeqa.test_management.models import TestCase
        tc = self.db.query(TestCase).filter(
            TestCase.id == row.test_case_id, TestCase.tenant_id == tenant_id,
        ).first()
        if not tc:
            return False
        row.user_decision = "accepted"
        row.decided_at = datetime.now(timezone.utc)
        row.decided_by = user_id
        self.db.commit()
        return True

    # -- Internals ----------------------------------------------------------

    def _lineage_root_run_id(self, run_id: int) -> int:
        from primeqa.execution.models import PipelineRun
        current = self.db.query(PipelineRun).filter_by(id=run_id).first()
        while current and current.parent_run_id:
            current = self.db.query(PipelineRun).filter_by(id=current.parent_run_id).first()
        return current.id if current else run_id

    def _lineage_runs(self, root_id: int) -> List[int]:
        from primeqa.execution.models import PipelineRun
        # Walk descendants (BFS); small lineage (cap at 3) so this is cheap
        ids = [root_id]
        frontier = [root_id]
        while frontier:
            children = self.db.query(PipelineRun).filter(
                PipelineRun.parent_run_id.in_(frontier),
            ).all()
            frontier = [c.id for c in children]
            ids.extend(frontier)
        return ids

    def _snapshot_test_case(self, test_case_id: int) -> Dict[str, Any]:
        from primeqa.test_management.models import TestCase, TestCaseVersion
        tc = self.db.query(TestCase).filter_by(id=test_case_id).first()
        if not tc:
            return {}
        cv = self.db.query(TestCaseVersion).filter_by(
            id=tc.current_version_id,
        ).first() if tc.current_version_id else None
        return {
            "test_case": {
                "id": tc.id, "title": tc.title, "status": tc.status,
                "visibility": tc.visibility, "version": tc.version,
                "current_version_id": tc.current_version_id,
            },
            "current_version": ({
                "id": cv.id,
                "steps": cv.steps,
                "expected_results": cv.expected_results,
                "preconditions": cv.preconditions,
                "referenced_entities": cv.referenced_entities,
            } if cv else None),
        }

    def _compute_after_state(self, before_state, proposal: FixProposal) -> Dict[str, Any]:
        """Precompute what the after-state WOULD be if the fix was applied.
        Stored so the UI can show a diff even for fixes that weren't applied."""
        if not proposal or not before_state.get("current_version"):
            return {}
        cv = dict(before_state["current_version"])
        if proposal.proposed_fix_type == "edit_step":
            steps = list(cv.get("steps") or [])
            target_order = proposal.changes.get("step_order")
            for i, step in enumerate(steps):
                if step.get("step_order") == target_order:
                    merged = dict(step)
                    fv = dict(merged.get("field_values") or {})
                    fv.update(proposal.changes.get("field_values") or {})
                    merged["field_values"] = fv
                    steps[i] = merged
                    break
            cv["steps"] = steps
        return {"current_version": cv}

    def _apply_fix(self, test_case_id: int, proposal: FixProposal) -> None:
        """Apply proposal to the test case. For edit_step: create a new version
        with the merged step_values. For retry: no-op (retry happens at execution
        time). For other types, raise so we can see it in the audit."""
        if proposal.proposed_fix_type == "retry":
            return

        if proposal.proposed_fix_type != "edit_step":
            raise ValueError(f"auto-apply not implemented for {proposal.proposed_fix_type}")

        from primeqa.test_management.models import TestCase, TestCaseVersion
        tc = self.db.query(TestCase).filter_by(id=test_case_id).first()
        if not tc or not tc.current_version_id:
            raise ValueError("test case or current version missing")
        cv = self.db.query(TestCaseVersion).filter_by(id=tc.current_version_id).first()
        if not cv:
            raise ValueError("current version row missing")

        new_steps = list(cv.steps or [])
        target_order = proposal.changes.get("step_order")
        for i, step in enumerate(new_steps):
            if step.get("step_order") == target_order:
                merged = dict(step)
                fv = dict(merged.get("field_values") or {})
                fv.update(proposal.changes.get("field_values") or {})
                merged["field_values"] = fv
                new_steps[i] = merged
                break

        # Insert a new version row
        from sqlalchemy import func as sqlfunc
        latest = self.db.query(sqlfunc.max(TestCaseVersion.version_number)).filter(
            TestCaseVersion.test_case_id == test_case_id,
        ).scalar() or 0
        new_cv = TestCaseVersion(
            test_case_id=test_case_id,
            version_number=latest + 1,
            metadata_version_id=cv.metadata_version_id,
            steps=new_steps,
            expected_results=cv.expected_results or [],
            preconditions=cv.preconditions or [],
            referenced_entities=cv.referenced_entities or [],
            generation_method="regenerated",  # agent-produced
            created_by=cv.created_by,
        )
        self.db.add(new_cv)
        self.db.commit()
        self.db.refresh(new_cv)

        tc.current_version_id = new_cv.id
        tc.version = (tc.version or 0) + 1
        tc.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    def _restore_test_case(self, test_case_id: int, snapshot: Dict[str, Any]) -> None:
        """Revert: the before_state snapshot is reapplied."""
        from primeqa.test_management.models import TestCase
        tc = self.db.query(TestCase).filter_by(id=test_case_id).first()
        if not tc or not snapshot:
            return
        snap_tc = snapshot.get("test_case") or {}
        if "current_version_id" in snap_tc:
            tc.current_version_id = snap_tc["current_version_id"]
        if "status" in snap_tc:
            tc.status = snap_tc["status"]
        if "visibility" in snap_tc:
            tc.visibility = snap_tc["visibility"]
        if "title" in snap_tc:
            tc.title = snap_tc["title"]
        tc.version = (tc.version or 0) + 1
        tc.updated_at = datetime.now(timezone.utc)
        self.db.commit()

    def _trigger_rerun(self, *, pipeline_run, test_case_id: int,
                       parent_run_id: int, environment_id: int) -> Optional[int]:
        """Create a new pipeline_run with parent_run_id, re-running just this TC."""
        from primeqa.execution.repository import (
            PipelineRunRepository, PipelineStageRepository,
            ExecutionSlotRepository, WorkerHeartbeatRepository,
        )
        from primeqa.execution.service import PipelineService
        from primeqa.execution.models import PipelineRun

        parent = pipeline_run or self.db.query(PipelineRun).filter_by(id=parent_run_id).first()
        if not parent:
            return None

        svc = PipelineService(
            PipelineRunRepository(self.db), PipelineStageRepository(self.db),
            ExecutionSlotRepository(self.db), WorkerHeartbeatRepository(self.db),
        )
        result = svc.create_run(
            tenant_id=parent.tenant_id,
            environment_id=environment_id,
            triggered_by=parent.triggered_by,
            run_type="execute_only",
            source_type="test_cases",
            source_ids=[test_case_id],
            priority=parent.priority,
            parent_run_id=parent.id,
            source_refs={"agent_rerun": True, "parent_run_id": parent.id,
                         "test_case_id": test_case_id},
        )
        return result["id"]
