"""TenantAgentSettings repository + helpers.

Lazy-default: if a tenant has no row yet (onboarding race), callers see
sensible defaults rather than a crash.

Step A (LLD_STEP_A_REPAIR_GATE, migration 069): the repair agent's policy
surface is these four fields and nothing else —

  agent_enabled              the master switch: false stops proposal
                             CREATION (the triage tick skips loudly-once)
                             as well as the apply pass
  repair_auto_apply          the AUTONOMOUS apply pass (sandbox only,
                             DERIVED verdicts only, under the switch)
  repair_gate_apply_enabled  the dormant-first switch: while false NO
                             apply path is reachable by anyone
  max_fix_attempts_per_run   the per-claim attempt cap

The trust thresholds were dropped (ruling D2): no apply path reads the
LLM's self-reported confidence, so both were dead controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from primeqa.core.models import TenantAgentSettings


DEFAULTS = {
    "agent_enabled": True,
    "repair_auto_apply": False,
    "repair_gate_apply_enabled": False,
    "max_fix_attempts_per_run": 3,
}

# The audited flags: a change to any of these writes an activity_log row
# (the caller's job — the repository returns the diff it applied).
FLAG_FIELDS = ("agent_enabled", "repair_auto_apply", "repair_gate_apply_enabled")


@dataclass
class AgentSettingsView:
    tenant_id: int
    agent_enabled: bool
    repair_auto_apply: bool
    repair_gate_apply_enabled: bool
    max_fix_attempts_per_run: int
    updated_by: Optional[int]
    updated_at: Optional[str]


class AgentSettingsRepository:
    def __init__(self, db):
        self.db = db

    def _row(self, tenant_id):
        return self.db.query(TenantAgentSettings).filter_by(tenant_id=tenant_id).first()

    def get(self, tenant_id: int) -> AgentSettingsView:
        row = self._row(tenant_id)
        if not row:
            return AgentSettingsView(
                tenant_id=tenant_id, updated_by=None, updated_at=None, **DEFAULTS,
            )
        return AgentSettingsView(
            tenant_id=row.tenant_id,
            agent_enabled=bool(row.agent_enabled),
            repair_auto_apply=bool(row.repair_auto_apply),
            repair_gate_apply_enabled=bool(row.repair_gate_apply_enabled),
            max_fix_attempts_per_run=int(row.max_fix_attempts_per_run),
            updated_by=row.updated_by,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )

    def update(self, tenant_id: int, *, updated_by: int, **fields) -> AgentSettingsView:
        """Apply the allowed fields and return the new view. The applied
        flag diff rides on ``self.last_flag_changes`` as
        ``{field: (old, new)}`` so the route can audit each change."""
        row = self._row(tenant_id)
        if not row:
            row = TenantAgentSettings(tenant_id=tenant_id)
            self.db.add(row)
            self.db.flush()

        allowed = set(FLAG_FIELDS) | {"max_fix_attempts_per_run"}
        changes: dict = {}
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            old = getattr(row, k)
            if k in FLAG_FIELDS:
                v = bool(v)
                old = bool(old) if old is not None else None
            else:
                v = int(v)
                if not (0 <= v <= 10):
                    raise ValueError(
                        f"max_fix_attempts_per_run must be 0..10, got {v}")
            if old != v:
                changes[k] = (old, v)
            setattr(row, k, v)

        row.updated_by = updated_by
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        self.last_flag_changes = {k: c for k, c in changes.items()
                                  if k in FLAG_FIELDS}
        return self.get(tenant_id)
