"""SEC-3: the SF-instance-URL SSRF guard is wired into the environment WRITE
path (create_environment), not only the validator. A non-Salesforce / private /
non-https URL is rejected before any DB write; a valid Salesforce URL is
accepted. Integration test against the real Railway DB; self-cleaning.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()

import pytest

import primeqa.app  # noqa: F401 — triggers create_app()/init_db so SessionLocal binds
from primeqa.db import SessionLocal
from primeqa.core.repository import EnvironmentRepository
from primeqa.core.service import EnvironmentService

TENANT_ID = 1


def test_create_environment_rejects_ssrf_url_at_write_time():
    db = SessionLocal()
    try:
        svc = EnvironmentService(EnvironmentRepository(db))
        # SSRF / non-Salesforce URLs must be rejected BEFORE any DB write
        # (validation raises SalesforceUrlError, a ValueError, at write time).
        for bad in ("http://169.254.169.254/latest/meta-data/",
                    "https://evil.example.com/",
                    "https://127.0.0.1/",
                    "http://acme.my.salesforce.com/"):  # non-https
            with pytest.raises(ValueError):
                svc.create_environment(
                    TENANT_ID, f"SSRF-{uuid.uuid4().hex[:6]}", "sandbox",
                    sf_instance_url=bad, sf_api_version="60.0")
    finally:
        db.close()


def test_create_environment_accepts_valid_salesforce_url():
    from primeqa.core.models import User
    db = SessionLocal()
    created = None
    try:
        uid = db.query(User.id).filter(User.tenant_id == TENANT_ID).limit(1).scalar()
        assert uid is not None, "seed data: expected a user in tenant 1"
        svc = EnvironmentService(EnvironmentRepository(db))
        created = svc.create_environment(
            TENANT_ID, f"SF-OK-{uuid.uuid4().hex[:8]}", "sandbox",
            sf_instance_url="https://acme.my.salesforce.com", sf_api_version="60.0",
            created_by=uid)
        assert created and created.get("id"), "a valid Salesforce URL must be accepted"
    finally:
        if created and created.get("id"):
            from primeqa.core.models import Environment
            e = db.query(Environment).filter_by(id=created["id"]).first()
            if e:
                db.delete(e)
                db.commit()
        db.close()


if __name__ == "__main__":
    test_create_environment_rejects_ssrf_url_at_write_time()
    test_create_environment_accepts_valid_salesforce_url()
    print("PASS  SEC-3 write-path SSRF guard")
