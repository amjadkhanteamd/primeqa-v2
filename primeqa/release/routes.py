"""API routes for the release domain.

Endpoints: /api/releases/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
from primeqa.release.repository import ReleaseRepository
from primeqa.release.service import ReleaseService
from primeqa.shared.api import json_error, json_error_from, ConflictError

release_bp = Blueprint("release", __name__)


def _get_service():
    db = next(get_db())
    return ReleaseService(ReleaseRepository(db)), db


def _hash_poll_token(raw: str) -> str:
    """SHA-256 of the raw status-poll token, hex-encoded — stored in
    releases.status_poll_token_hash (migration 055). Mirrors the
    shared-dashboard link idiom: the raw token is handed to CI once at
    mint time and never persisted, so a DB dump can't leak active tokens.
    Lookups hash the incoming ?token= and match by equality."""
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@release_bp.route("/api/releases", methods=["GET"])
@require_auth
def list_releases():
    svc, db = _get_service()
    try:
        return jsonify(svc.list_releases(
            request.user["tenant_id"], status=request.args.get("status"),
        )), 200
    finally:
        db.close()


@release_bp.route("/api/releases", methods=["POST"])
@require_role("admin", "tester")
def create_release():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return json_error("VALIDATION_ERROR", "name is required", http=400)
    svc, db = _get_service()
    try:
        result = svc.create_release(
            request.user["tenant_id"], data["name"], request.user["id"],
            version_tag=data.get("version_tag"),
            description=data.get("description"),
            target_date=data.get("target_date"),
            decision_criteria=data.get("decision_criteria"),
        )
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.created", result.get("id"), {"name": data["name"]})
        return jsonify(result), 201
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>", methods=["GET"])
@require_auth
def get_release(release_id):
    svc, db = _get_service()
    try:
        detail = svc.get_release_detail(release_id, request.user["tenant_id"])
        if not detail:
            return json_error("NOT_FOUND", "Release not found", http=404)
        return jsonify(detail), 200
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>", methods=["PATCH"])
@require_role("admin", "tester")
def update_release(release_id):
    """Audit M-1: accepts optional `expected_updated_at` — client echoes
    the token it got on the last GET. Mismatch → 409 CONFLICT with the
    current row so the UI can show a "reloaded" diff banner."""
    data = request.get_json(silent=True) or {}
    expected = data.pop("expected_updated_at", None)
    svc, db = _get_service()
    try:
        result = svc.update_release(
            release_id, request.user["tenant_id"], data,
            expected_updated_at=expected,
        )
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.updated", release_id, {"fields": sorted(data.keys())})
        return jsonify(result), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    except ConflictError as e:
        return json_error_from(e)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>", methods=["DELETE"])
@require_role("admin")
def delete_release(release_id):
    svc, db = _get_service()
    try:
        svc.delete_release(release_id, request.user["tenant_id"])
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.deleted", release_id)
        return jsonify(message="Deleted"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/requirements", methods=["POST"])
@require_role("admin", "tester")
def add_requirement(release_id):
    data = request.get_json(silent=True) or {}
    if not data.get("requirement_id"):
        return json_error("VALIDATION_ERROR", "requirement_id is required", http=400)
    svc, db = _get_service()
    try:
        svc.add_requirement(release_id, request.user["tenant_id"],
                            data["requirement_id"], request.user["id"])
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.requirement.added", release_id,
                 {"requirement_id": data["requirement_id"]})
        return jsonify(message="Added"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/requirements/<int:req_id>", methods=["DELETE"])
@require_role("admin", "tester")
def remove_requirement(release_id, req_id):
    svc, db = _get_service()
    try:
        svc.remove_requirement(release_id, request.user["tenant_id"], req_id)
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.requirement.removed", release_id,
                 {"requirement_id": req_id})
        return jsonify(message="Removed"), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/requirements/bulk", methods=["POST"])
@require_role("admin", "tester")
def add_requirements_bulk(release_id):
    """Attach many requirements to a release. Body: {requirement_ids: [...]}.
    Returns {added, already_in, skipped}."""
    from primeqa.shared.api import BULK_MAX_ITEMS
    data = request.get_json(silent=True) or {}
    ids = data.get("requirement_ids") or []
    if not isinstance(ids, list) or not ids:
        return json_error("VALIDATION_ERROR", "requirement_ids must be a non-empty array", http=400)
    if len(ids) > BULK_MAX_ITEMS:  # audit F5
        return json_error(
            "BULK_LIMIT",
            f"Bulk operations are limited to {BULK_MAX_ITEMS} items per call",
            http=400,
        )
    svc, db = _get_service()
    try:
        result = svc.add_requirements_bulk(
            release_id, request.user["tenant_id"], ids, request.user["id"],
        )
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.requirements.bulk", release_id,
                 {"added": len(result.get("added", [])),
                  "skipped": len(result.get("skipped", []))})
        return jsonify(result), 200
    except ValueError as e:
        return json_error("VALIDATION_ERROR", str(e), http=400)
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/evaluate-decision", methods=["POST"])
@require_role("admin", "tester")
def evaluate_decision(release_id):
    # D-198: the composer runs BOTH engines — v1 (zero-diff) + the substrate's
    # evidence-grounded recommendation — combines per
    # decision_criteria.substrate_mode (default advisory), and records ONE
    # decision row whose reasoning JSON carries the full envelope. The response
    # stays v1-shaped at the top level (byte-identical when no substrate
    # evidence applies).
    from primeqa.release.decision_composer import evaluate_and_record
    svc, db = _get_service()
    try:
        release = svc.release_repo.get_release(release_id, request.user["tenant_id"])
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        result = evaluate_and_record(
            db, release, request.user["tenant_id"],
            release_repo=svc.release_repo)
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.decision.evaluated", release_id,
                 {"recommendation": result.get("recommendation")})
        return jsonify(result), 200
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/decisions/<int:decision_id>/finalize", methods=["POST"])
@require_role("admin")
def finalize_decision(release_id, decision_id):
    # Overriding a NO-GO requires `override_quality_gate`; plain GO / conditional
    # approval only needs `approve_release`. The require_any wrapper lets either
    # permission through; the body-level check below enforces the stricter gate
    # for overrides so a user with only `approve_release` can't flip NO-GO -> GO.
    data = request.get_json(silent=True) or {}
    final = data.get("final_decision")
    if final not in ("go", "conditional_go", "no_go"):
        return json_error("VALIDATION_ERROR", "Invalid final_decision", http=400)
    if data.get("override_reason"):
        # Overrides require the stricter permission.
        from flask import g
        perms = getattr(g, "effective_permissions", set()) or set()
        if request.user.get("role") != "superadmin" and "override_quality_gate" not in perms:
            return json_error(
                "INSUFFICIENT_PERMISSIONS",
                "Overriding the quality gate requires override_quality_gate.",
                http=403,
                details={"required": ["override_quality_gate"]},
            )
    svc, db = _get_service()
    try:
        # Tenant isolation: confirm the release belongs to the caller's tenant
        # before finalizing. Mirrors evaluate_decision above — without this the
        # repo would finalize any decision id regardless of tenant.
        release = svc.release_repo.get_release(release_id, request.user["tenant_id"])
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        d = svc.release_repo.finalize_decision(
            decision_id, release_id, final, request.user["id"], data.get("override_reason"))
        if not d:
            return json_error("NOT_FOUND", "Decision not found", http=404)
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.decision.finalized", release_id,
                 {"decision_id": decision_id, "final_decision": d.final_decision,
                  "override": bool(data.get("override_reason"))})
        return jsonify({"final_decision": d.final_decision}), 200
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/status", methods=["GET"])
def public_release_status(release_id):
    """Public endpoint for CI/CD to poll release decision status.

    Capability is a per-release opaque token (migration 055): the caller must
    pass `?token=` matching the release's `status_poll_token_hash`. This both
    authenticates the poll (no interactive login needed, so CI still works) and
    proves the tenant \u2014 the matched release scopes the response. Without a valid
    token the endpoint is a 404, so release ids are no longer an unauthenticated
    cross-tenant oracle. A Release Owner mints/revokes the token via
    POST/DELETE /api/releases/<id>/status-token.

    R5 / Q3: if the latest decision has `agent_verdict_counts=true` (default),
    CI sees the post-agent result (which may flip red\u2192green after the agent
    auto-fixed and rerun passed). If false, CI sees the pre-agent (raw human)
    verdict. Super Admin can toggle per release in the release detail page.
    """
    db = next(get_db())
    try:
        from primeqa.release.models import Release, ReleaseDecision
        from sqlalchemy import desc
        token = request.args.get("token", "")
        if not token:
            return json_error("UNAUTHORIZED", "A status token is required.", http=401)
        # Scope by id AND token hash. A NULL status_poll_token_hash (no token
        # minted) never equals a real hash, so when a token IS supplied,
        # wrong-token / unminted-release / nonexistent-release all return the
        # same 404 \u2014 no cross-tenant existence oracle. (A missing token
        # short-circuits to 401 above, before any release lookup, so it too
        # leaks nothing about which release ids exist.)
        release = db.query(Release).filter(
            Release.id == release_id,
            Release.status_poll_token_hash == _hash_poll_token(token),
        ).first()
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        latest = db.query(ReleaseDecision).filter(
            ReleaseDecision.release_id == release_id,
        ).order_by(desc(ReleaseDecision.created_at)).first()

        # The legacy v1 pass/fail rollup joined ReleaseRun -> pipeline_runs, but
        # the v1 execution engine + its pipeline_runs table were retired (D-221)
        # and PipelineRun was deleted from execution.models — so that import +
        # join were making THIS endpoint 500 on every call (latent since the
        # retirement; the token-gate work is the first caller to exercise it).
        # Post-retirement the verdict CI consumes is the substrate block (D-198),
        # projected below. The rollup is kept (zeroed) for response-shape
        # stability; substrate.metrics now carries the real counts.
        agent_counts = True if latest is None else bool(latest.agent_verdict_counts)
        passed = failed = total = runs_counted = 0

        # D-198 (slice 4): the substrate block for CI — PROJECTED from the latest
        # decision's stored reasoning envelope (no substrate query on this hot
        # path). None when the latest decision predates D-198 or carried no
        # applicable substrate evidence.
        substrate_block = None
        if latest is not None and isinstance(latest.reasoning, dict):
            sub = latest.reasoning.get("substrate")
            if isinstance(sub, dict) and sub.get("applicable"):
                substrate_block = {
                    "recommendation": sub.get("recommendation"),
                    "risk": sub.get("risk"),
                    "metrics": sub.get("metrics"),
                    "mode": latest.reasoning.get("mode"),
                    "recommendation_source":
                        latest.reasoning.get("recommendation_source"),
                }

        return jsonify({
            "release_id": release_id,
            "name": release.name,
            "status": release.status,
            "recommendation": latest.recommendation if latest else None,
            "final_decision": latest.final_decision if latest else None,
            "confidence": latest.confidence if latest else None,
            "decided_at": latest.decided_at.isoformat() if latest and latest.decided_at else None,
            "agent_verdict_counts": agent_counts,
            "rollup": {"passed": passed, "failed": failed, "total": total,
                       "runs_counted": runs_counted},
            "substrate": substrate_block,
        }), 200
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/status-token", methods=["POST"])
@require_role("admin", "tester")
def mint_status_token(release_id):
    """Mint (or rotate) the opaque polling token for this release's public
    /status endpoint. Returns the raw token ONCE — only its SHA-256 hash is
    stored, so it can't be retrieved again; re-POST to rotate. Tenant-scoped
    via get_release so a caller can only mint for their own releases."""
    import secrets
    svc, db = _get_service()
    try:
        release = svc.release_repo.get_release(release_id, request.user["tenant_id"])
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        raw = secrets.token_urlsafe(32)
        release.status_poll_token_hash = _hash_poll_token(raw)
        db.commit()
        # Audit the capability grant — NEVER the raw token, only that one was minted.
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.status_token.minted", release_id)

        proto = request.headers.get("X-Forwarded-Proto", request.scheme or "https")
        if proto not in ("http", "https"):
            proto = "https"
        base = f"{proto}://{request.host}"
        return jsonify({
            "release_id": release_id,
            "token": raw,
            "status_url": f"{base}/api/releases/{release_id}/status?token={raw}",
        }), 201
    finally:
        db.close()


@release_bp.route("/api/releases/<int:release_id>/status-token", methods=["DELETE"])
@require_role("admin", "tester")
def revoke_status_token(release_id):
    """Revoke the release's polling token. Subsequent /status polls 404 until a
    new token is minted. Tenant-scoped; idempotent (already-NULL is a no-op)."""
    svc, db = _get_service()
    try:
        release = svc.release_repo.get_release(release_id, request.user["tenant_id"])
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)
        release.status_poll_token_hash = None
        db.commit()
        svc._log(request.user["tenant_id"], request.user["id"],
                 "release.status_token.revoked", release_id)
        return jsonify({"release_id": release_id, "status": "revoked"}), 200
    finally:
        db.close()


@release_bp.route("/api/webhooks/ci-trigger", methods=["POST"])
def ci_webhook_trigger():
    """CI/CD webhook to trigger release test runs. Expects HMAC-SHA256 signature."""
    import hmac
    import hashlib
    import os

    # Audit fix C-2 (2026-04-19): fail closed. Previously `if secret:`
    # meant that deployment without WEBHOOK_SECRET accepted ANY request
    # — sloppy deploy = open door. Now a missing secret is a
    # configuration error, 503 is returned, and the CI job fails
    # loudly rather than silently succeeding.
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        return json_error(
            "CONFIG_ERROR",
            "WEBHOOK_SECRET is not configured — webhook endpoint disabled",
            http=503,
        )
    provided_sig = request.headers.get("X-PrimeQA-Signature", "")
    body = request.get_data()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided_sig):
        return json_error("UNAUTHORIZED", "Invalid signature", http=401)

    data = request.get_json(silent=True) or {}
    release_id = data.get("release_id")
    environment_id = data.get("environment_id")
    commit_sha = data.get("commit_sha", "unknown")

    if not release_id or not environment_id:
        return json_error("VALIDATION_ERROR", "release_id and environment_id required", http=400)

    db = next(get_db())
    try:
        from primeqa.release.models import Release
        release = db.query(Release).filter(Release.id == release_id).first()
        if not release:
            return json_error("NOT_FOUND", "Release not found", http=404)

        # Tenant hardening (A5): the webhook is authenticated only by the
        # single global WEBHOOK_SECRET, so a holder could otherwise enqueue
        # claims against ANY tenant's environment by passing a foreign
        # environment_id. The tenant is authoritative from the release row;
        # the supplied environment_id must belong to that same tenant.
        from primeqa.core.models import Environment
        env = db.query(Environment).filter(
            Environment.id == environment_id,
            Environment.tenant_id == release.tenant_id,
        ).first()
        if not env:
            return json_error(
                "NOT_FOUND",
                "Environment not found for this release's tenant", http=404)

        # D-221 R3: the v1 pipeline half retired with the engine — the CI
        # trigger is substrate-only now. CI polls /status whose D-198
        # substrate block carries the verdict over fresh evidence.
        from primeqa.intelligence.s4_execution_console import enqueue_claims_for_keys
        from primeqa.release.decision_composer import external_keys_for_requirements
        from primeqa.release.repository import ReleaseRepository
        keys = external_keys_for_requirements(
            ReleaseRepository(db).list_requirements(
                release_id, tenant_id=release.tenant_id))
        substrate = enqueue_claims_for_keys(
            release.tenant_id, keys, environment_id)

        if not substrate.get("enqueued"):
            return json_error(
                "VALIDATION_ERROR",
                "No substrate claims to verify for this release's requirements",
                http=400)

        return jsonify({
            "run_id": None,
            "substrate": {"enqueued_jobs": substrate.get("enqueued", []),
                          "claim_count": substrate.get("claim_count", 0)},
            "status_url": f"/api/releases/{release_id}/status",
        }), 201
    finally:
        db.close()
