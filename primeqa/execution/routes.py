"""API routes for the execution domain.

Endpoints: /api/runs/*
"""

from flask import Blueprint, jsonify, request

from primeqa.core.auth import require_auth, require_role
from primeqa.db import get_db
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
