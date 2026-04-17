"""TenantAgentSettings repository + helpers.

Lazy-default: if a tenant has no row yet (onboarding race), callers see
sensible defaults rather than a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from primeqa.core.models import TenantAgentSettings


DEFAULTS = {
    "agent_enabled": True,
    "trust_threshold_high": 0.85,
    "trust_threshold_medium": 0.60,
    "max_fix_attempts_per_run": 3,
}


@dataclass
class AgentSettingsView:
    tenant_id: int
    agent_enabled: bool
    trust_threshold_high: float
    trust_threshold_medium: float
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
            trust_threshold_high=float(row.trust_threshold_high),
            trust_threshold_medium=float(row.trust_threshold_medium),
            max_fix_attempts_per_run=int(row.max_fix_attempts_per_run),
            updated_by=row.updated_by,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )

    def update(self, tenant_id: int, *, updated_by: int, **fields) -> AgentSettingsView:
        row = self._row(tenant_id)
        if not row:
            row = TenantAgentSettings(tenant_id=tenant_id)
            self.db.add(row)

        allowed = {
            "agent_enabled", "trust_threshold_high", "trust_threshold_medium",
            "max_fix_attempts_per_run",
        }
        for k, v in fields.items():
            if k in allowed and v is not None:
                setattr(row, k, v)

        # Validate that thresholds stay sane; the DB CHECK will also enforce this
        hi = float(row.trust_threshold_high)
        md = float(row.trust_threshold_medium)
        if not (0.0 <= md < hi <= 1.0):
            raise ValueError(
                "Trust thresholds must satisfy 0 \u2264 medium < high \u2264 1. "
                f"Got high={hi}, medium={md}."
            )

        row.updated_by = updated_by
        row.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(row)
        return self.get(tenant_id)
