"""LLM model catalog — refresh against Anthropic's live model list + enable.

Per-tenant model-control arc (migrations 060/061). The selectable-model set is
``router.selectable_model_ids()`` = (code SELECTABLE_MODELS ∪ llm_models
active) − llm_models retired; this module is the ONLY writer of llm_models:

* :func:`refresh_model_catalog` — diff ``GET /v1/models`` (the official Models
  API; no beta header, ``after_id`` pagination) against the selectable set.
  EXPIRED (selectable but gone upstream) → upsert ``status='retired'`` +
  activity_log per pinned tenant + superadmin notification. NEW (upstream but
  not selectable) → returned for the UI's Enable form only — never
  auto-inserted, because the Models API returns NO pricing and enabling
  without entered rates would silently mis-bill. REAPPEARED (retired row seen
  upstream) → flagged for manual un-retire, never silently resurrected.
* :func:`enable_model` — insert/activate a row with superadmin-entered rates;
  from then on it is selectable in every tenant picker and priced via the
  ``pricing.resolve_rates`` overlay.

Two honest limits (by upstream design, not ours): retirement is detectable
only as ABSENCE from the list (no ``retires_at`` — detection is day-of, and
advance warning stays a human channel), and pricing cannot be fetched (the
two rates are typed at enable time).

Callers: the superadmin Models panel on /settings/llm-usage (button + Enable
form) and the daily scheduler tick. Both are best-effort at the edges: no
API key / upstream failure → the refresh raises ``CatalogRefreshError`` and
the caller logs/flashes it; it never crashes a tick or a page.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

_MODELS_URL = "https://api.anthropic.com/v1/models"
_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT_S = 15
_MAX_PAGES = 10   # backstop; the catalog is ~tens of models, one page


class CatalogRefreshError(RuntimeError):
    """The upstream model list could not be fetched (no key, HTTP failure).
    The refresh made NO writes; callers log/flash and move on."""


@dataclass
class RefreshResult:
    seen: int = 0
    expired: List[str] = field(default_factory=list)      # newly retired ids
    new: List[dict] = field(default_factory=list)         # [{id, display_name}]
    reappeared: List[str] = field(default_factory=list)   # retired but seen again
    affected: Dict[str, List[int]] = field(default_factory=dict)  # expired id -> pinned tenants


def fetch_upstream_models(api_key: str) -> List[dict]:
    """All models on ``GET /v1/models`` for this key —
    ``[{"id", "display_name"}, ...]``. Raises :class:`CatalogRefreshError` on
    any transport/HTTP problem (the caller decides how loud to be)."""
    import requests

    if not api_key:
        raise CatalogRefreshError(
            "no Anthropic API key available (no active LLM connection?)")
    out: List[dict] = []
    after_id: Optional[str] = None
    try:
        for _ in range(_MAX_PAGES):
            params = {"limit": 100}
            if after_id:
                params["after_id"] = after_id
            resp = requests.get(
                _MODELS_URL,
                headers={"x-api-key": api_key,
                         "anthropic-version": _ANTHROPIC_VERSION},
                params=params, timeout=_TIMEOUT_S)
            if resp.status_code != 200:
                raise CatalogRefreshError(
                    f"GET /v1/models returned {resp.status_code}")
            payload = resp.json()
            for m in payload.get("data") or []:
                out.append({"id": m.get("id"),
                            "display_name": m.get("display_name")})
            if not payload.get("has_more"):
                break
            after_id = payload.get("last_id")
            if not after_id:
                break
    except CatalogRefreshError:
        raise
    except Exception as e:
        raise CatalogRefreshError(f"models list fetch failed: {e}") from e
    return [m for m in out if m["id"]]


def resolve_platform_api_key() -> Optional[str]:
    """Best-effort key for the refresh: the first active LLM connection's
    decrypted api_key, any tenant (the ``_resolve_any_llm_key`` pattern —
    the model catalog is key-independent platform metadata). None when no
    connection resolves; the caller surfaces that as a skipped refresh."""
    try:
        from primeqa.db import get_db
        from primeqa.core.models import Connection
        from primeqa.core.repository import ConnectionRepository

        db = next(get_db())
        try:
            rows = db.query(Connection.id, Connection.tenant_id).filter(
                Connection.connection_type == "llm",
            ).order_by(Connection.id).limit(10).all()
            for conn_id, tenant_id in rows:
                try:
                    conn = ConnectionRepository(db).get_connection_decrypted(
                        conn_id, tenant_id)
                    key = ((conn or {}).get("config") or {}).get("api_key")
                    if key:
                        return key
                except Exception:
                    continue
        finally:
            db.close()
    except Exception as e:
        log.warning("resolve_platform_api_key failed: %s", e)
    return None


def classify_refresh(upstream: List[dict], *, code_set: frozenset,
                     active: set, retired: set) -> RefreshResult:
    """Pure diff of the upstream model list against the catalog state:
    EXPIRED = selectable but gone upstream; NEW = upstream but neither
    selectable nor retired; REAPPEARED = retired but seen upstream again.
    (``affected`` is filled by the caller — it needs a tenant read.)"""
    upstream_ids = {m["id"] for m in upstream}
    selectable = (code_set | active) - retired
    result = RefreshResult(seen=len(upstream_ids))
    result.expired = sorted(selectable - upstream_ids)
    result.reappeared = sorted(retired & upstream_ids)
    result.new = sorted(
        ({"id": m["id"], "display_name": m.get("display_name")}
         for m in upstream
         if m["id"] not in selectable and m["id"] not in retired),
        key=lambda m: m["id"])
    return result


def refresh_model_catalog(api_key: str, *, actor_user_id: Optional[int] = None,
                          actor_tenant_id: Optional[int] = None) -> RefreshResult:
    """One refresh pass. Writes: retired upserts + last-seen stamps +
    activity_log rows (per pinned tenant, when there is an actor context) —
    and busts the router's selectable cache. NEW models are returned, not
    written (pricing must be entered at enable time)."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    from primeqa.core.models import ActivityLog, LlmModel
    from primeqa.intelligence.llm import router

    upstream = fetch_upstream_models(api_key)
    upstream_ids = {m["id"] for m in upstream}
    names = {m["id"]: m.get("display_name") for m in upstream}

    sess = Session(bind=engine)
    try:
        rows = sess.query(LlmModel).all()
        by_id = {r.model_id: r for r in rows}
        result = classify_refresh(
            upstream,
            code_set=router.SELECTABLE_MODELS,
            active={r.model_id for r in rows if r.status == "active"},
            retired={r.model_id for r in rows if r.status == "retired"})

        # Stamp every known row seen upstream (staleness visibility).
        now = sess.execute(text("SELECT NOW()")).scalar()
        for mid in upstream_ids & set(by_id):
            by_id[mid].last_seen_upstream_at = now

        # EXPIRED → retire (upsert; a code model gets its first row here).
        for mid in result.expired:
            row = by_id.get(mid)
            if row is None:
                row = LlmModel(model_id=mid, display_name=names.get(mid))
                sess.add(row)
            row.status = "retired"
            row.updated_at = now
        expired = result.expired

        # Who is pinned to the expired ids? (fail-loud blast radius)
        if expired:
            pinned = sess.execute(text(
                "SELECT tenant_id, llm_model_override FROM tenant_agent_settings "
                "WHERE llm_model_override = ANY(:ids)"), {"ids": expired}).fetchall()
            for tid, mid in pinned:
                result.affected.setdefault(mid, []).append(tid)
            for mid in expired:
                result.affected.setdefault(mid, [])
            # Audit: one row per pinned tenant (tenant-scoped, meaningful);
            # an actor-less scheduler run with zero pinned tenants leaves
            # only the python log + notification.
            for mid, tids in result.affected.items():
                for tid in tids:
                    sess.add(ActivityLog(
                        tenant_id=tid, user_id=actor_user_id,
                        action="update", entity_type="llm_model_retired",
                        details={"model_id": mid,
                                 "source": "refresh",
                                 "pinned_via": "llm_model_override"}))
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()

    if result.expired:
        log.warning("model catalog refresh: retired upstream: %s (pinned: %s)",
                    result.expired, result.affected)
        from primeqa.shared.notifications import notify_model_retired
        notify_model_retired(result.expired, result.affected)

    # The selectable set changed (or its staleness did) — re-read next call.
    router.selectable_model_ids(refresh=True)
    return result


def enable_model(model_id: str, *, display_name: Optional[str],
                 input_usd_per_mtok: float, output_usd_per_mtok: float,
                 actor_user_id: int, actor_tenant_id: int) -> None:
    """Enable a model from the refresh panel: upsert ``status='active'`` with
    the superadmin-entered rates (NOT NULL by table CHECK — "selectable ⇒
    correctly priced" holds by construction), audit-log it, and bust the
    selectable + rates caches so the picker updates immediately. Also the
    manual un-retire path for a reappeared model (same upsert)."""
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    from primeqa.core.models import ActivityLog, LlmModel
    from primeqa.intelligence.llm import pricing, router

    if not model_id or input_usd_per_mtok <= 0 or output_usd_per_mtok <= 0:
        raise ValueError("model id and positive input/output rates are required")

    sess = Session(bind=engine)
    try:
        row = sess.query(LlmModel).filter(
            LlmModel.model_id == model_id).first()
        old_status = row.status if row else None
        if row is None:
            row = LlmModel(model_id=model_id)
            sess.add(row)
        row.display_name = display_name or row.display_name
        row.status = "active"
        row.input_usd_per_mtok = input_usd_per_mtok
        row.output_usd_per_mtok = output_usd_per_mtok
        sess.add(ActivityLog(
            tenant_id=actor_tenant_id, user_id=actor_user_id,
            action="update" if old_status else "create",
            entity_type="llm_model_enabled",
            details={"model_id": model_id, "old_status": old_status,
                     "input_usd_per_mtok": input_usd_per_mtok,
                     "output_usd_per_mtok": output_usd_per_mtok}))
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()

    pricing._rates_cache.pop(model_id, None)
    router.selectable_model_ids(refresh=True)
