"""Flask web entrypoint — registers all route blueprints and loads .env.

Environment variables:
    DATABASE_URL              — PostgreSQL connection string (Railway sets automatically)
    JWT_SECRET                — secret for JWT signing
    CREDENTIAL_ENCRYPTION_KEY — Fernet key for credential encryption
    PORT                      — HTTP port (Railway sets, default 5000)
    FLASK_ENV                 — 'production' on Railway, 'development' locally
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()

from primeqa.db import init_db, SessionLocal

import primeqa.core.models  # noqa: F401
import primeqa.metadata.models  # noqa: F401
import primeqa.test_management.models  # noqa: F401
import primeqa.execution.models  # noqa: F401
import primeqa.intelligence.models  # noqa: F401
import primeqa.vector.models  # noqa: F401
import primeqa.release.models  # noqa: F401
import primeqa.execution.data_engine  # noqa: F401
import primeqa.runs.schedule  # noqa: F401 \u2014 R4 ScheduledRun model

from primeqa.core import csrf
from primeqa.core.routes import core_bp
from primeqa.metadata.routes import metadata_bp
from primeqa.test_management.routes import test_management_bp
from primeqa.execution.routes import execution_bp
from primeqa.intelligence.routes import intelligence_bp
from primeqa.release.routes import release_bp
from primeqa.shared import observability as obs
from primeqa.views import views_bp


def create_app():
    application = Flask(__name__)
    application.config["SECRET_KEY"] = os.getenv("JWT_SECRET", "dev-secret-change-me")

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Railway uses postgres:// but SQLAlchemy needs postgresql://
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        init_db(database_url)

    application.register_blueprint(core_bp)
    application.register_blueprint(metadata_bp)
    application.register_blueprint(test_management_bp)
    application.register_blueprint(execution_bp)
    application.register_blueprint(intelligence_bp)
    application.register_blueprint(release_bp)
    application.register_blueprint(views_bp)

    # Install request-timing + slow-query hooks after blueprints/engine are ready
    obs.install(application)

    # Install CSRF protection (double-submit cookie). See primeqa.core.csrf.
    # Skips /api/* requests that carry Bearer auth; enforced on every other
    # state-changing request.
    csrf.install(application)

    # Audit fix C-3 (2026-04-19): global 500 handler so uncaught
    # exceptions return a clean envelope for /api/* and a friendly HTML
    # page for web routes. Never leak stack traces to the response body.
    # Server-side the exception is still logged via Flask's default.
    import logging
    _err_log = logging.getLogger("primeqa.errors")

    @application.errorhandler(Exception)
    def _handle_unhandled_exception(err):
        # Let Flask's HTTPException subclasses flow through (404, 403,
        # etc.). We only want to catch genuinely unhandled exceptions.
        from werkzeug.exceptions import HTTPException
        if isinstance(err, HTTPException):
            return err
        # Log the full stack once so ops can diagnose.
        _err_log.exception("Unhandled %s on %s %s", type(err).__name__,
                           request.method, request.path)
        from flask import request as _req
        if _req.path.startswith("/api/"):
            return jsonify({"error": {
                "code": "SERVER_ERROR",
                "message": "An unexpected error occurred. Reference the server log.",
            }}), 500
        # HTML path — minimal, no stack. Production Flask already hides
        # stacks when FLASK_ENV != development, but this belt-and-braces
        # prevents leak even with misconfigured env.
        return (
            "<h1>Something went wrong</h1>"
            "<p>We logged the error. Reload and try again.</p>",
            500,
        )

    @application.route("/health")
    def health():
        try:
            if SessionLocal is None:
                return jsonify(status="unhealthy", database="not configured"), 503
            from sqlalchemy import text
            db = SessionLocal()
            db.execute(text("SELECT 1"))
            db.close()
            return jsonify(status="healthy", database="connected"), 200
        except Exception as e:
            return jsonify(status="unhealthy", database=str(e)), 503

    return application


app = create_app()

if __name__ == "__main__":
    debug = os.getenv("FLASK_ENV", "development") != "production"
    app.run(debug=debug, port=int(os.getenv("PORT", 5000)))
