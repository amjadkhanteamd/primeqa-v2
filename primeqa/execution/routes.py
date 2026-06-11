"""API routes for the execution domain.

Endpoints: /api/runs/*, /api/environments/<id>/slots
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.core.permissions import require_run_permission
from primeqa.db import get_db
from primeqa.execution.repository import (
    ExecutionSlotRepository, WorkerHeartbeatRepository,
)
from primeqa.execution.data_engine import DataEngineService, DataTemplate, DataFactory
from primeqa.shared.api import json_error

execution_bp = Blueprint("execution", __name__)


@execution_bp.route("/api/jira/search", methods=["GET"])
@require_auth
def jira_ticket_search():
    """Ticket-level Jira search for the wizard chip picker.

    Accept: text/html (default) \u2192 HTMX fragment at
    `templates/runs/_jira_search_results.html`.
    Accept: application/json or ?format=json \u2192 JSON payload.

    Params:
      env_id  \u2014 env whose jira_connection_id is used (run wizard path)
      conn_id \u2014 direct Jira connection (requirements import path)
      q       (required) \u2014 query string (issue key or free text)
      limit   (optional, default 20, max 50)

    One of env_id / conn_id is required; conn_id wins if both are passed.
    """
    from flask import render_template
    # Accept either `env_id` (canonical) or `environment_id` (matches the form
    # field name, in case a future client uses hx-include on the select).
    env_id = (request.args.get("env_id", type=int)
              or request.args.get("environment_id", type=int))
    # Direct connection: used by the requirements-import chip picker, which
    # doesn't know/need an environment \u2014 the user has explicitly chosen
    # which Jira to pull from.
    conn_id = (request.args.get("conn_id", type=int)
               or request.args.get("jira_connection_id", type=int))
    q = (request.args.get("q") or "").strip()
    limit = request.args.get("limit", default=20, type=int)

    want_json = (request.args.get("format") == "json" or
                 "application/json" in (request.headers.get("Accept") or ""))

    def _render(payload, hint=None, error=None):
        if want_json:
            body = {"results": payload or [], "hint": hint, "error": error,
                    "count": len(payload or [])}
            return jsonify(body), 200
        return render_template("runs/_jira_search_results.html",
                               results=payload or [], hint=hint, error=error), 200

    if not env_id and not conn_id:
        return _render([], hint="Pick an environment or Jira connection to enable search.")

    if len(q) < 2:
        return _render([], hint="Type at least 2 characters\u2026")

    db = next(get_db())
    try:
        if conn_id:
            client = _jira_client(db, conn_id, request.user["tenant_id"])
            if not client:
                return _render([], hint="Jira connection not found or not configured.")
            effective_conn_id = conn_id
        else:
            client, env = _jira_client_for_env(db, env_id, request.user["tenant_id"])
            if not client:
                return _render([], hint=(
                    "This environment has no Jira connection. "
                    "Attach one in Settings \u2192 Environments, or pick a different env."
                ))
            effective_conn_id = env.jira_connection_id
        try:
            results = client.search_issues(q, connection_id=effective_conn_id, limit=limit)
        except Exception as e:
            return _render([], error=f"Jira search failed: {e}")
        return _render(results)
    finally:
        db.close()


# ---- Run preview (live count as wizard selection changes) ------------------

@execution_bp.route("/api/environments/<int:env_id>/slots", methods=["GET"])
@require_auth
def get_slots(env_id):
    svc, db = _get_service()
    try:
        status = svc.get_slot_status(env_id)
        if not status:
            return json_error("NOT_FOUND", "Environment not found", http=404)
        return jsonify(status), 200
    finally:
        db.close()


# --- Results ---

@execution_bp.route("/api/data/templates", methods=["GET"])
@require_auth
def list_data_templates():
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        tmpls = svc.list_templates(request.user["tenant_id"], object_type=request.args.get("object_type"))
        return jsonify([{
            "id": t.id, "name": t.name, "description": t.description,
            "object_type": t.object_type, "field_values": t.field_values,
        } for t in tmpls]), 200
    finally:
        db.close()


@execution_bp.route("/api/data/templates", methods=["POST"])
@require_role("admin", "tester")
def create_data_template():
    data = request.get_json(silent=True) or {}
    for f in ["name", "object_type"]:
        if not data.get(f):
            return json_error("VALIDATION_ERROR", f"{f} is required", http=400)
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        t = svc.create_template(
            request.user["tenant_id"], data["name"], data["object_type"],
            data.get("field_values", {}), request.user["id"],
            description=data.get("description"),
        )
        return jsonify({"id": t.id, "name": t.name}), 201
    finally:
        db.close()


@execution_bp.route("/api/data/factories", methods=["GET"])
@require_auth
def list_data_factories():
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        factories = svc.list_factories(request.user["tenant_id"])
        return jsonify([{
            "id": f.id, "name": f.name, "description": f.description,
            "factory_type": f.factory_type, "config": f.config,
        } for f in factories]), 200
    finally:
        db.close()


@execution_bp.route("/api/data/factories", methods=["POST"])
@require_role("admin", "tester")
def create_data_factory():
    data = request.get_json(silent=True) or {}
    for f in ["name", "factory_type"]:
        if not data.get(f):
            return json_error("VALIDATION_ERROR", f"{f} is required", http=400)
    db = next(get_db())
    try:
        svc = DataEngineService(db)
        factory = svc.create_factory(
            request.user["tenant_id"], data["name"], data["factory_type"],
            data.get("config", {}), request.user["id"],
            description=data.get("description"),
        )
        return jsonify({"id": factory.id, "name": factory.name}), 201
    finally:
        db.close()


@execution_bp.route("/api/data/factories/<int:fid>/preview", methods=["POST"])
@require_auth
def preview_factory(fid):
    db = next(get_db())
    try:
        f = db.query(DataFactory).filter(
            DataFactory.id == fid, DataFactory.tenant_id == request.user["tenant_id"],
        ).first()
        if not f:
            return json_error("NOT_FOUND", "Factory not found", http=404)
        svc = DataEngineService(db)
        samples = [svc.generate_value(f.factory_type, f.config) for _ in range(5)]
        return jsonify({"samples": samples}), 200
    finally:
        db.close()
