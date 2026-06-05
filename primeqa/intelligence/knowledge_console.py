"""Knowledge admin console — v1's read-only window into the S5 knowledge
substrate (UI Area 7 slice 7a, D-176).

v1 owns the bridge (the allowed v1→substrate read direction, like
`substrate_insights` / `s1_sync_console`). The S5 package (`primeqa/knowledge/`)
is **read-only + file-backed** — there is no write API and the rule/pack files
are **trusted git-controlled content** (prompt-injection defence; authored via
git PR, never a UI write). This console surfaces the three S5 channels for an
admin viewer; it never mutates anything.

Channels (each read independently, best-effort — a failure in one degrades only
that channel, never raises):
  - **System Rules** — `SystemPromptRulesProvider().get_rules()` over the static
    `salesforce_knowledge/system_rules.json` (global, no tenant key).
  - **Domain Packs** — `DomainPackLibrary(...).load()` over
    `salesforce_domain_packs/*.md` (global; per-tenant enablement is the v1
    `llm_enable_domain_packs` flag, surfaced elsewhere).
  - **Learned Rules** — `feedback_rules.build_rules_block(tenant_id)`, the
    per-tenant "common mistakes" block derived from `generation_quality_signals`
    (empty until feedback signals accrue).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Same resolution as the generation path (generation.py) so the viewer reads the
# exact pack set the LLM gateway loads.
_PACKS_DIR = os.getenv("PRIMEQA_DOMAIN_PACKS_DIR", "salesforce_domain_packs")

# Display ordering for the system-rules table (mirrors the assembler's category
# precedence: field behaviour first, then operations, then assertions).
_CATEGORY_ORDER = {"field_behaviour": 0, "operation": 1, "assertion": 2}


def _read_system_rules() -> dict:
    """All S5 system rules (file-backed, global). Best-effort."""
    try:
        from primeqa.knowledge import QueryContext, SystemPromptRulesProvider
        rules = SystemPromptRulesProvider().get_rules(QueryContext())
        rows = sorted(
            ({"id": r.id, "object_name": r.object_name, "field_name": r.field_name,
              "category": r.category, "rule_text": r.rule_text,
              "confidence": r.confidence, "source": r.source} for r in rules),
            key=lambda d: (_CATEGORY_ORDER.get(d["category"], 9),
                           d["object_name"] or "", d["field_name"] or "", d["id"]))
        return {"available": True, "rules": rows, "count": len(rows)}
    except Exception as exc:                      # missing/malformed file, import error
        log.warning("knowledge_console system rules unavailable: %s", exc)
        return {"available": False, "rules": [], "count": 0}


def _read_domain_packs() -> dict:
    """All loaded S5 domain packs (file-backed, global, trusted git-controlled).
    Best-effort. Exposes only the basename (not the absolute deploy path)."""
    try:
        from primeqa.knowledge import DomainPackLibrary
        packs = DomainPackLibrary(_PACKS_DIR).load()
        rows = [{"id": p.id, "title": p.title, "keywords": list(p.keywords),
                 "objects": list(p.objects), "version": p.version,
                 "measured_tokens": p.measured_tokens,
                 "filename": os.path.basename(p.source_path)} for p in packs]
        return {"available": True, "packs": rows, "count": len(rows)}
    except Exception as exc:
        log.warning("knowledge_console domain packs unavailable: %s", exc)
        return {"available": False, "packs": [], "count": 0}


def _read_learned_rules(tenant_id) -> dict:
    """The tenant's learned-rules block (S5 LearnedRulesProvider over the v1
    generation_quality_signals; empty until signals accrue). Best-effort."""
    try:
        from primeqa.intelligence.llm.feedback_rules import build_rules_block
        block = (build_rules_block(tenant_id) or "").strip()
        return {"available": True, "block": block, "has_signals": bool(block)}
    except Exception as exc:
        log.warning("knowledge_console learned rules unavailable for tenant %s: %s",
                    tenant_id, exc)
        return {"available": False, "block": "", "has_signals": False}


def get_knowledge_overview(tenant_id) -> dict:
    """Best-effort read of the three S5 knowledge channels for the admin viewer.
    Never raises; each channel degrades independently. **Read-only by contract** —
    rule/pack content is trusted git-controlled (edit via PR, never a UI write).
    Returns ``{system_rules, domain_packs, learned_rules, packs_dir}``."""
    return {
        "system_rules": _read_system_rules(),
        "domain_packs": _read_domain_packs(),
        "learned_rules": _read_learned_rules(tenant_id),
        "packs_dir": _PACKS_DIR,
    }
