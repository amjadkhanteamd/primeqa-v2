"""Flask web entrypoint — registers all route blueprints and loads .env."""

import os

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

from primeqa.db import init_db

import primeqa.core.models  # noqa: F401
import primeqa.metadata.models  # noqa: F401
import primeqa.test_management.models  # noqa: F401
import primeqa.execution.models  # noqa: F401
import primeqa.intelligence.models  # noqa: F401
import primeqa.vector.models  # noqa: F401

from primeqa.core.routes import core_bp
from primeqa.metadata.routes import metadata_bp
from primeqa.test_management.routes import test_management_bp
from primeqa.execution.routes import execution_bp
from primeqa.intelligence.routes import intelligence_bp


def create_app():
    application = Flask(__name__)
    application.config["SECRET_KEY"] = os.getenv("JWT_SECRET", "dev-secret-change-me")

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        init_db(database_url)

    application.register_blueprint(core_bp)
    application.register_blueprint(metadata_bp)
    application.register_blueprint(test_management_bp)
    application.register_blueprint(execution_bp)
    application.register_blueprint(intelligence_bp)

    return application


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
