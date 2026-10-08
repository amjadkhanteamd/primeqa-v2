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


class CatalogProbeError(RuntimeError):
    """The forced-tool-choice probe (D-504) could not establish the fact —
    no key, transport failure, an unexpected status, an unknown model id. The
    fact stays UNKNOWN; an enable is refused, a refresh logs and leaves NULL."""


# ---- Forced tool choice probe (D-504) ---------------------------------------
# The exact rejection text the API returns on a model that does not accept
# tool_choice {"type":"tool"} / {"type":"any"} (Claude 5.5 models, Fable 5.1).
# count_tokens validates the request shape exactly as messages.create does and
# is FREE, so it is a truthful, no-spend capability probe (proven on seven
# models 2026-10-08, docs/design/LLD_TOOL_CHOICE_CAPABILITY.md section 2).
FORCED_TOOL_CHOICE_REJECTION = 'tool_choice: type "tool" and "any" are not supported'
_PROBE_TOOL = {
    "name": "probe_tool",
    "description": "Capability probe (never executed).",
    "input_schema": {"type": "object",
                     "properties": {"x": {"type": "string"}},
                     "required": ["x"], "additionalProperties": False},
}


def classify_probe_outcome(exc: Optional[BaseException]) -> bool:
    """Pure classification of a probe attempt: ``None`` (the call succeeded)
    → True; an error carrying ``status_code == 400`` whose text contains
    :data:`FORCED_TOOL_CHOICE_REJECTION` → False; anything else →
    :class:`CatalogProbeError` (the fact stays unknown — never guessed)."""
    if exc is None:
        return True
    status = getattr(exc, "status_code", None)
    # The SDK renders the error body as a Python repr (single-quoted, the
    # inner double quotes literal); a JSON rendering escapes them. Accept both.
    text = str(exc).replace('\\"', '"')
    if status == 400 and FORCED_TOOL_CHOICE_REJECTION in text:
        return False
    raise CatalogProbeError(
        f"forced-tool-choice probe failed ({type(exc).__name__}"
        f"{f' {status}' if status else ''}): {str(exc)[:200]}") from exc


def probe_forced_tool_choice(api_key: str, model_id: str, *, client=None) -> bool:
    """Ask the API, for free, whether ``model_id`` accepts a forced
    ``tool_choice``: one ``count_tokens`` call with a forced one-field tool.
    Returns the FACT (True/False); raises :class:`CatalogProbeError` when it
    cannot be established. ``client=`` injects a fake (unit tests)."""
    if client is None:
        if not api_key:
            raise CatalogProbeError(
                "no Anthropic API key available to probe (no active LLM connection?)")
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=_TIMEOUT_S)
    try:
        client.messages.count_tokens(
            model=model_id,
            messages=[{"role": "user", "content": "Call probe_tool with x='a'."}],
            tools=[_PROBE_TOOL],
            tool_choice={"type": "tool", "name": "probe_tool"},
        )
    except Exception as exc:  # noqa: BLE001 — classified, never swallowed
        return classify_probe_outcome(exc)
    return classify_probe_outcome(None)


@dataclass
class RefreshResult:
    seen: int = 0
    expired: List[str] = field(default_factory=list)      # newly retired ids
    new: List[dict] = field(default_factory=list)         # [{id, display_name}]
    reappeared: List[str] = field(default_factory=list)   # retired but seen again
    affected: Dict[str, List[int]] = field(default_factory=dict)  # expired id -> pinned tenants
    probed: Dict[str, bool] = field(default_factory=dict)         # D-504: id -> fact recorded this pass
    probe_failed: List[str] = field(default_factory=list)         # D-504: ids left UNKNOWN


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
                          actor_tenant_id: Optional[int] = None,
                          probe=None) -> RefreshResult:
    """One refresh pass. Writes: retired upserts + last-seen stamps +
    activity_log rows (per pinned tenant, when there is an actor context) —
    and busts the router's selectable cache. NEW models are returned, not
    written (pricing must be entered at enable time).

    D-504: every ACTIVE row whose ``forced_tool_choice`` is NULL is then
    probed (``probe(model_id) -> bool``, default :func:`probe_forced_tool_choice`
    with this key) and the fact recorded; a per-row :class:`CatalogProbeError`
    is logged, the row stays NULL, and the refresh itself still succeeds."""
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

    # D-504: record the forced-tool-choice fact for active rows that lack it.
    _probe_unknown_active_rows(api_key, result, probe=probe)

    # The selectable set changed (or its staleness did) — re-read next call.
    router.selectable_model_ids(refresh=True)
    return result


def _probe_unknown_active_rows(api_key: str, result: RefreshResult, *, probe=None) -> None:
    """D-504: probe every ACTIVE catalog row whose forced_tool_choice is NULL
    and stamp the fact. One short session per row so a late failure never
    loses an earlier fact. Errors are named in ``result.probe_failed`` and the
    python log; nothing raises out of a refresh for a probe."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    from primeqa.core.models import LlmModel

    if probe is None:
        probe = lambda mid: probe_forced_tool_choice(api_key, mid)  # noqa: E731

    sess = Session(bind=engine)
    try:
        pending = [r.model_id for r in sess.query(LlmModel).filter(
            LlmModel.status == "active",
            LlmModel.forced_tool_choice.is_(None)).order_by(LlmModel.model_id).all()]
    finally:
        sess.close()

    for mid in pending:
        try:
            fact = bool(probe(mid))
        except CatalogProbeError as e:
            log.warning("forced-tool-choice probe for %s failed, fact stays UNKNOWN: %s", mid, e)
            result.probe_failed.append(mid)
            continue
        sess = Session(bind=engine)
        try:
            row = sess.query(LlmModel).filter(LlmModel.model_id == mid).first()
            if row is not None:
                row.forced_tool_choice = fact
                row.forced_tool_choice_probed_at = sess.execute(text("SELECT NOW()")).scalar()
                sess.commit()
                result.probed[mid] = fact
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()


def enable_model(model_id: str, *, display_name: Optional[str],
                 input_usd_per_mtok: float, output_usd_per_mtok: float,
                 actor_user_id: int, actor_tenant_id: int,
                 api_key: Optional[str] = None, probe=None) -> None:
    """Enable a model from the refresh panel: upsert ``status='active'`` with
    the superadmin-entered rates (NOT NULL by table CHECK — "selectable ⇒
    correctly priced" holds by construction), audit-log it, and bust the
    selectable + rates caches so the picker updates immediately. Also the
    manual un-retire path for a reappeared model (same upsert).

    D-504: the forced-tool-choice fact is probed BEFORE the upsert
    (``probe(model_id) -> bool``; default :func:`probe_forced_tool_choice`
    with ``api_key`` or the platform key) and written with the row —
    "selectable ⇒ priced AND its driving shape known". A
    :class:`CatalogProbeError` refuses the enable: nothing is written."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from primeqa.db import engine
    from primeqa.core.models import ActivityLog, LlmModel
    from primeqa.intelligence.llm import pricing, router

    if not model_id or input_usd_per_mtok <= 0 or output_usd_per_mtok <= 0:
        raise ValueError("model id and positive input/output rates are required")

    if probe is None:
        key = api_key or resolve_platform_api_key()
        fact = probe_forced_tool_choice(key, model_id)
    else:
        fact = bool(probe(model_id))

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
        row.forced_tool_choice = fact
        row.forced_tool_choice_probed_at = sess.execute(text("SELECT NOW()")).scalar()
        sess.add(ActivityLog(
            tenant_id=actor_tenant_id, user_id=actor_user_id,
            action="update" if old_status else "create",
            entity_type="llm_model_enabled",
            details={"model_id": model_id, "old_status": old_status,
                     "input_usd_per_mtok": input_usd_per_mtok,
                     "output_usd_per_mtok": output_usd_per_mtok,
                     "forced_tool_choice": fact}))
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()

    pricing._rates_cache.pop(model_id, None)
    router.selectable_model_ids(refresh=True)
