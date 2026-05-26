"""v1-side S3 enqueue bridge (D-106.4 slice 4).

``resolve_requirement`` reads the v1 ``public.requirements`` store — kept
**v1-side** so the substrate core stays caller-fed (option B / Fork A: no
S3 -> v1 schema dependency). The ``views.py`` route glues this to the substrate
``primeqa.generation.intake.enqueue_s3_generation``.

Closes the slice-2-tracked line (``resolve_requirement``).
"""
from __future__ import annotations

from typing import Any, Optional


def _requirement_to_ref(requirement: Any) -> dict:
    """Map a v1 ``Requirement`` to the substrate's caller-supplied ``{key, text}``.

    - ``key`` = ``jira_key`` for Jira-imported reqs, else ``"req-<id>"`` (manual
      reqs carry a NULL ``jira_key``, so they're keyed by their int id).
    - ``text`` = the ``jira_summary`` / ``jira_description`` /
      ``acceptance_criteria`` fields, trimmed and newline-joined (empties
      dropped). Pure — no DB access (testable with a fake requirement)."""
    key = requirement.jira_key or f"req-{requirement.id}"
    parts = [requirement.jira_summary, requirement.jira_description,
             requirement.acceptance_criteria]
    text = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return {"key": key, "text": text}


def resolve_requirement(db, requirement_id: int, tenant_id: int) -> Optional[dict]:
    """Fetch a v1 requirement (tenant-scoped, non-deleted) and map it to
    ``{key, text}``. Returns ``None`` when the requirement is not found. The
    single S3 -> v1 read, kept on the v1 side of the enqueue bridge."""
    from primeqa.test_management.repository import RequirementRepository
    requirement = RequirementRepository(db).get_requirement(requirement_id, tenant_id)
    if requirement is None:
        return None
    return _requirement_to_ref(requirement)
