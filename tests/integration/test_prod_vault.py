"""Productionisation DB-real tests — gated on S3A3_TEST_DATABASE_URL
(scratch, tenant_1 at 20260826_0020). Verification (a)–(e): CHECK
semantics, the vault round-trip + hygiene, the named refusal classes,
the enqueue boundary, and the consumer-loop mechanics (scan monkeypatched
— the real-browser pass rides the transcript script and P-1)."""
from __future__ import annotations

import json
import os
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

DB = os.environ.get("S3A3_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DB, reason="set S3A3_TEST_DATABASE_URL "
                       "(non-prod scratch, tenant_1 at 20260826_0020)"),
]

USER_ID = 7   # active admin on scratch


@pytest.fixture()
def key(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setenv("PORTAL_FERNET_KEY", k)
    return k


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    eng = create_engine(DB, pool_pre_ping=True, connect_args={
        "options": "-csearch_path=tenant_1,public -capp.tenant_id=1"})
    s = Session(bind=eng)
    s.info["tenant_schema"] = "tenant_1"
    yield s
    s.rollback()
    s.close()


def test_a_check_semantics(session):
    with session.begin_nested() as sp:
        with pytest.raises(Exception, match="auth_mode_known"):
            session.execute(text("""
                INSERT INTO portal_personas
                    (id, persona_key, site, username_ciphertext,
                     password_ciphertext, auth_mode, registered_by)
                VALUES (:i, 'x1', 's', 'c', 'c', 'UNSUPPORTED', 7)
            """), {"i": str(uuid.uuid4())})
        sp.rollback()
    with session.begin_nested() as sp:
        with pytest.raises(Exception, match="totp_seed_iff_provisioned"):
            session.execute(text("""
                INSERT INTO portal_personas
                    (id, persona_key, site, username_ciphertext,
                     password_ciphertext, auth_mode, registered_by)
                VALUES (:i, 'x2', 's', 'c', 'c', 'TOTP_PROVISIONED', 7)
            """), {"i": str(uuid.uuid4())})
        sp.rollback()


def test_b_round_trip_audit_hygiene_and_key_rotation(session, key):
    from primeqa.browser_worker import vault

    # rotate_key is TABLE-WIDE by design (production has one key);
    # scratch accumulates committed personas under destroyed per-run
    # keys — clear them inside this transaction so the rotation sees a
    # single-key table (and the mixed-key refusal has its own assert).
    session.execute(text("DELETE FROM portal_personas"))

    pk = f"rt-{uuid.uuid4().hex[:8]}"
    secret_user = f"user-{uuid.uuid4().hex}@example.com"
    secret_pw = f"pw-{uuid.uuid4().hex}"
    secret_seed = "JBSWY3DPEHPK3PXP"

    out = vault.register_persona(
        session, tenant_id=1, persona_key=pk, site="portal.example.com",
        auth_mode="TOTP_PROVISIONED", username=secret_user,
        password=secret_pw, totp_seed=secret_seed, actor_user_id=USER_ID)
    assert out["action"] == "ui.persona_registered"

    # real-actor audit row
    row = session.execute(text("""
        SELECT user_id, details->>'persona_key', details->>'auth_mode'
        FROM public.activity_log WHERE action='ui.persona_registered'
          AND details->>'persona_key' = :k"""), {"k": pk}).fetchone()
    assert row == (USER_ID, pk, "TOTP_PROVISIONED")

    # worker-side resolve feeds the EXISTING Credentials shape
    creds = vault.resolve_credentials(session, pk)
    assert (creds.username, creds.password, creds.totp_seed) == (
        secret_user, secret_pw, secret_seed)
    assert repr(creds) == "Credentials(<redacted>)"

    # hygiene: NO plaintext secret substring anywhere in the vault or
    # audit rows — ciphertext only
    for tbl, col in (("portal_personas", "username_ciphertext"),
                     ("portal_personas", "password_ciphertext"),
                     ("portal_personas", "totp_seed_ciphertext")):
        vals = [r[0] for r in session.execute(text(
            f"SELECT {col} FROM {tbl} WHERE persona_key=:k"),
            {"k": pk}).fetchall()]
        for v in vals:
            assert v and secret_user not in v and secret_pw not in v \
                and secret_seed not in v
    blob = json.dumps([list(r) for r in session.execute(text(
        "SELECT details FROM public.activity_log "
        "WHERE details->>'persona_key' = :k"), {"k": pk}).fetchall()],
        default=str)
    assert secret_pw not in blob and secret_seed not in blob \
        and secret_user not in blob

    # re-registration = credential rotation, stamped
    out2 = vault.register_persona(
        session, tenant_id=1, persona_key=pk, site="portal.example.com",
        auth_mode="TOTP_PROVISIONED", username=secret_user,
        password="pw-rotated", totp_seed=secret_seed,
        actor_user_id=USER_ID)
    assert out2["action"] == "ui.persona_rotated"
    assert vault.resolve_credentials(session, pk).password == "pw-rotated"

    # key rotation (FND-24): re-encrypt under NEW; stamps; decrypt works
    new_key = Fernet.generate_key().decode()
    n = vault.rotate_key(session, tenant_id=1, old_key=key,
                         new_key=new_key, actor_user_id=USER_ID)
    assert n >= 1
    os.environ["PORTAL_FERNET_KEY"] = new_key
    creds3 = vault.resolve_credentials(session, pk)
    assert creds3.password == "pw-rotated"
    stamped = session.execute(text(
        "SELECT rotated_by, rotated_at FROM portal_personas "
        "WHERE persona_key=:k"), {"k": pk}).fetchone()
    assert stamped[0] == USER_ID and stamped[1] is not None

    # mixed-key refusal: a row under a FOREIGN key refuses rotation
    # loudly, naming the persona, before any write
    foreign = Fernet(Fernet.generate_key())
    session.execute(text("""
        INSERT INTO portal_personas
            (id, persona_key, site, username_ciphertext,
             password_ciphertext, auth_mode, registered_by)
        VALUES (:i, 'mixed-key-row', 's', :u, :p, 'NONE', 7)
    """), {"i": str(uuid.uuid4()),
           "u": foreign.encrypt(b"x").decode(),
           "p": foreign.encrypt(b"y").decode()})
    with pytest.raises(vault.VaultError, match="mixed-key-row.*OLD key"):
        vault.rotate_key(session, tenant_id=1, old_key=new_key,
                         new_key=Fernet.generate_key().decode(),
                         actor_user_id=USER_ID)


def test_c_named_refusal_classes(session, key):
    from primeqa.browser_worker import vault
    from primeqa.browser_worker.session import (
        LoginError, PERSONA_INACTIVE, PERSONA_NOT_FOUND)

    with pytest.raises(LoginError) as ei:
        vault.resolve_credentials(session, "nobody-here")
    assert ei.value.code == PERSONA_NOT_FOUND

    pk = f"inact-{uuid.uuid4().hex[:8]}"
    vault.register_persona(
        session, tenant_id=1, persona_key=pk, site="s",
        auth_mode="NONE", username="u", password="p", totp_seed=None,
        actor_user_id=USER_ID)
    vault.deactivate_persona(session, tenant_id=1, persona_key=pk,
                             actor_user_id=USER_ID)
    with pytest.raises(LoginError) as ei2:
        vault.resolve_credentials(session, pk)
    assert ei2.value.code == PERSONA_INACTIVE


def test_d_enqueue_boundary(session):
    from primeqa.core.authz import AuthorizationError
    from primeqa.execution_engine.ui_manifest import enqueue_ui_run
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.test_representation.claim_sets import (
        approve_claim_set, create_inventory_version)

    sfx = uuid.uuid4().hex[:8]
    inv = create_inventory_version(session, members=[
        {"site": f"eq-{sfx}.example.com", "path": "/x",
         "persona_scope": "eq"}], created_by=USER_ID)
    res = enumerate_claims(session, catalogue_release_id=2,
                           inventory_version=inv, persona_scope="eq",
                           created_by=USER_ID)
    approve_claim_set(session, claim_set_id=res["claim_set_id"],
                      user_id=USER_ID, tenant_id=1)

    # below-tier: the 403-envelope carrier (AuthorizationError + reason)
    with pytest.raises(AuthorizationError, match="deny: role 'viewer'"):
        enqueue_ui_run(session, subject={"role": "viewer", "user_id": 9,
                                         "tenant_id": 1},
                       claim_set_id=uuid.UUID(str(res["claim_set_id"])))

    # MEMBER passes; the D6 consult ran (a job exists => mode_for passed)
    out = enqueue_ui_run(session,
                         subject={"role": "tester", "user_id": USER_ID,
                                  "tenant_id": 1},
                         claim_set_id=uuid.UUID(str(res["claim_set_id"])))
    assert out["job_id"] and out["manifest_id"]
    assert "allow: role 'tester'" in out["authorized"]
    audit = session.execute(text("""
        SELECT user_id, details->>'job_id' FROM public.activity_log
        WHERE action='ui.run_enqueued' AND details->>'job_id' = :j
    """), {"j": out["job_id"]}).fetchone()
    assert audit == (USER_ID, out["job_id"])


def test_e_loop_mechanics(session, key, monkeypatch, capsys):
    from primeqa.browser_worker import queue as q
    from primeqa.browser_worker.__main__ import (
        _WARNED_SCHEMALESS, _discover_tenant_ids)
    from primeqa.browser_worker.consume import consume_job

    # schema-discovered tick: a planted schemaless ACTIVE tenant row is
    # skipped loudly-once
    session.execute(text("""
        INSERT INTO public.tenants (id, name, slug, status)
        VALUES (99, 'ghost', 'ghost-99', 'active')
        ON CONFLICT (id) DO UPDATE SET status='active'
    """))
    session.commit()
    _WARNED_SCHEMALESS.discard(99)
    try:
        ids = _discover_tenant_ids(DB)
        assert 1 in ids and 99 not in ids
        first = capsys.readouterr().out
        assert "tenant 99" in first and "no provisioned schema" in first
        _discover_tenant_ids(DB)
        assert "tenant 99" not in capsys.readouterr().out   # loudly-ONCE
    finally:
        session.execute(text("DELETE FROM public.tenants WHERE id=99"))
        session.commit()

    # claim -> consume -> SIGTERM-after-current-surface -> reaper takes
    # the lease (scan monkeypatched; the queue/evidence mechanics real)
    # scratch hygiene: park leftover pending jobs (the manifest helpers
    # COMMIT, so earlier tests' jobs persist and claim_one takes oldest)
    session.execute(text("""
        DELETE FROM s4_ui_inspection_results WHERE job_id IN (
            SELECT id FROM s4_ui_inspection_jobs
            WHERE status IN ('pending','in_progress'))"""))
    session.execute(text(
        "DELETE FROM s4_ui_inspection_jobs "
        "WHERE status IN ('pending','in_progress')"))
    session.commit()

    from primeqa.browser_worker.manifest import create_manifest
    mid = create_manifest(session, {
        "surfaces": [{"key": "s1", "url": "http://127.0.0.1:1/a"},
                     {"key": "s2", "url": "http://127.0.0.1:1/b"}],
        "pins": {}, "stabilisation": {},
        "execution": {"mode": "prod-loop-test"}})
    job_id = q.enqueue(session, {"surfaces": [
        {"key": "s1", "url": "http://127.0.0.1:1/a"},
        {"key": "s2", "url": "http://127.0.0.1:1/b"}]}, mid)
    monkeypatch.setattr(
        "primeqa.browser_worker.consume.scan_page",
        lambda url, **kw: {"status": "OK",
                           "fingerprint": {"sha256": "f" * 64},
                           "timings_ms": {"nav": 1.0}})
    job = q.claim_one(session)
    assert job["job_id"] == job_id
    consume_job(session, job, should_stop=lambda: True)   # stop after s1
    st = session.execute(text(
        "SELECT status FROM s4_ui_inspection_jobs WHERE id=:i"),
        {"i": job_id}).scalar_one()
    assert st == "in_progress"                 # lease left for the reaper
    n_results = session.execute(text(
        "SELECT COUNT(*) FROM s4_ui_inspection_results WHERE job_id=:i"),
        {"i": job_id}).scalar_one()
    assert n_results == 1                      # the CURRENT surface finished
    out = capsys.readouterr().out
    assert "died_reason=SIGTERM" in out

    session.execute(text("""
        UPDATE s4_ui_inspection_jobs
        SET heartbeat_at = NOW() - INTERVAL '10 minutes' WHERE id=:i
    """), {"i": job_id})
    assert q.reap_stalled(session) >= 1
    st2, attempts = session.execute(text(
        "SELECT status, attempts FROM s4_ui_inspection_jobs WHERE id=:i"),
        {"i": job_id}).fetchone()
    assert st2 == "pending" and attempts == 1  # claim-only charging

    # the re-claimed job runs to completion
    job2 = q.claim_one(session)
    consume_job(session, job2)
    assert session.execute(text(
        "SELECT status FROM s4_ui_inspection_jobs WHERE id=:i"),
        {"i": job_id}).scalar_one() == "succeeded"
    session.commit()


def test_f_manifest_pins_the_engine_run_set(session):
    """D-465 fix slice §b.1 — the builder resolves the bound engine ids
    for the release and pins them; the job payload carries them to the
    worker (which cannot read S5 itself)."""
    import hashlib

    from primeqa.execution_engine.ui_manifest import (
        engine_run_set, enqueue_ui_run)
    from primeqa.generation.enumeration import enumerate_claims
    from primeqa.test_representation.claim_sets import (
        approve_claim_set, create_inventory_version)

    sfx = uuid.uuid4().hex[:8]
    inv = create_inventory_version(session, members=[
        {"site": f"rs-{sfx}.example.com", "path": "/x",
         "persona_scope": "rs"}], created_by=USER_ID)
    res = enumerate_claims(session, catalogue_release_id=2,
                           inventory_version=inv, persona_scope="rs",
                           created_by=USER_ID)
    approve_claim_set(session, claim_set_id=res["claim_set_id"],
                      user_id=USER_ID, tenant_id=1)
    out = enqueue_ui_run(session,
                         subject={"role": "admin", "user_id": USER_ID,
                                  "tenant_id": 1},
                         claim_set_id=uuid.UUID(str(res["claim_set_id"])))

    pins = session.execute(text(
        "SELECT payload->'pins' FROM s4_ui_run_manifests WHERE id=:i"),
        {"i": out["manifest_id"]}).scalar_one()
    expected = engine_run_set(session, 2, "axe-core", pins["axe_version"])
    assert pins["engine_run_set"] == expected
    assert len(expected) == 72                     # the release's bound set
    assert pins["engine_run_set_hash"] == hashlib.sha256(
        "\n".join(expected).encode()).hexdigest()

    job_payload = session.execute(text(
        "SELECT payload FROM s4_ui_inspection_jobs WHERE id=:i"),
        {"i": out["job_id"]}).scalar_one()
    assert job_payload["engine_run_set"] == expected   # travels as DATA
