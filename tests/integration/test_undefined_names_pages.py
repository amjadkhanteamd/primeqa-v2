"""AUD-008 on the page: the requirement page's readiness handler is commented
"never breaks the page". Its except clause called `logging.getLogger` with
`logging` unimported, so a readiness failure became a 500 — exactly when the
net was meant to hold. This forces the readiness read to raise and asserts
the page still renders. SCRATCH via the test client (REPORT_PAGES=1 +
S3A3_TEST_DATABASE_URL, the Step 6a page-suite gate)."""
from __future__ import annotations

import os
import re

import pytest

_ENABLED = os.environ.get("REPORT_PAGES") == "1" and bool(os.environ.get("S3A3_TEST_DATABASE_URL"))
pytestmark = [pytest.mark.skipif(not _ENABLED, reason="set REPORT_PAGES=1 + S3A3_TEST_DATABASE_URL")]


def _client(role="admin"):
    import jwt
    from datetime import datetime, timedelta, timezone
    from primeqa.app import create_app
    app = create_app()
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": "1", "tenant_id": 1, "role": role, "email": "t@x", "full_name": "t",
                        "iat": now, "exp": now + timedelta(minutes=10)},
                       os.environ.get("JWT_SECRET", "0123456789abcdef" * 4), algorithm="HS256")
    c = app.test_client(); c.set_cookie("access_token", token)
    return c


@pytest.fixture()
def planted_requirement():
    import primeqa.core.models  # noqa: F401 — registers the FK targets
    import primeqa.db as dbm
    from sqlalchemy import text
    dbm.init_db(os.environ["S3A3_TEST_DATABASE_URL"])
    db = dbm.SessionLocal()
    sid = db.execute(text("SELECT id FROM sections WHERE tenant_id = 1 ORDER BY id LIMIT 1")).scalar()
    if sid is None:
        sid = db.execute(text("INSERT INTO sections (tenant_id, name, created_by) VALUES (1, 'AUD-008', 1) RETURNING id")).scalar()
    rid = db.execute(text(
        "INSERT INTO requirements (tenant_id, section_id, source, created_by, jira_summary, external_key) "
        "VALUES (1, :s, 'manual', 1, 'AUD-008 readiness net', 'req-aud-008') RETURNING id"), {"s": sid}).scalar()
    db.commit()
    yield rid
    db.execute(text("DELETE FROM requirements WHERE id = :r"), {"r": rid}); db.commit(); db.close()


def test_a_readiness_failure_degrades_the_page_instead_of_breaking_it(planted_requirement, monkeypatch):
    import primeqa.intelligence.substrate_decision as sd

    def _boom(*a, **k):
        raise RuntimeError("readiness store unreachable (forced by the test)")

    monkeypatch.setattr(sd, "readiness_for_pairs", _boom)
    r = _client().get("/requirements/%d" % planted_requirement)
    html = r.data.decode()
    assert r.status_code == 200, (r.status_code, html[:200])
    assert "Something went wrong" not in html
    assert re.search(r"AUD-008 readiness net", html), "the requirement page did not render its own title"
