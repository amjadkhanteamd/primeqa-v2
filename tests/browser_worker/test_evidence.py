"""Evidence-store tests (ui-s2.5). Real-bucket tests are gated on the
EVIDENCE_S3_* env (source ~/.plimsol/evidence.env); they write only under
{tenant}/spike-test/<uuid>/ and clean up. The sweep test additionally
needs SPIKE_DATABASE_URL (it compares the prefix against DB references).
"""

import os
import ssl
import time
import urllib.error
import urllib.request
import uuid

import pytest


def _ssl_ctx():
    # Plain urllib with an explicit CA bundle: this Mac's Homebrew python
    # ships no system trust store (boto3 carries its own via certifi).
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover
        return ssl.create_default_context()

_ENV_OK = all(os.environ.get(v) for v in (
    "EVIDENCE_S3_ENDPOINT", "EVIDENCE_S3_BUCKET",
    "EVIDENCE_S3_ACCESS_KEY_ID", "EVIDENCE_S3_SECRET_ACCESS_KEY"))
SPIKE_DB = os.environ.get("SPIKE_DATABASE_URL")

needs_bucket = pytest.mark.skipif(not _ENV_OK, reason="EVIDENCE_S3_* unset")

TEST_PREFIX_ROOT = "tenant_1/spike-test"


class _T1Session:
    """Guard-satisfying stand-in: arm I's guard reads only session.info."""
    info = {"tenant_schema": "tenant_1", "tenant_id": 1}
PNG_1x1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
           b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


# ---------- pure ----------

def test_build_keys_follow_the_scheme():
    from primeqa.browser_worker.evidence import _build_keys
    k = _build_keys("tenant_7", "m1", "j1", "home", 2)
    assert k == {"screenshot": "tenant_7/m1/j1/home/2/screenshot.png",
                 "observation": "tenant_7/m1/j1/home/2/observation.json"}


def test_key_prefix_is_derived_from_session_not_callers():
    from primeqa.browser_worker.evidence import EvidenceConfigError, key_prefix

    class S:
        info = {"tenant_schema": "tenant_3"}
    assert key_prefix(S()) == "tenant_3"

    class NoScope:
        info = {}
    with pytest.raises(EvidenceConfigError):
        key_prefix(NoScope())


def test_finalize_refuses_referenced_state():
    from primeqa.browser_worker.queue import finalize_surface

    class FakeSession:
        def execute(self, *a, **k):
            raise AssertionError("must refuse before any SQL")
    with pytest.raises(ValueError):
        finalize_surface(FakeSession(), "j", "k", 1, {"status": "OK"},
                         evidence={"state": "REFERENCED", "keys": {}})


# ---------- real bucket ----------

@pytest.fixture()
def s3_and_prefix():
    from primeqa.browser_worker import evidence as ev
    s3 = ev.client()
    bucket = ev.bucket_name()
    prefix = f"{TEST_PREFIX_ROOT}/{uuid.uuid4()}"
    yield s3, bucket, prefix
    for key in ev._list_keys(s3, bucket, prefix):
        s3.delete_object(Bucket=bucket, Key=key)


@needs_bucket
def test_round_trip_upload_verify_sign(s3_and_prefix):
    from primeqa.browser_worker import evidence as ev
    s3, bucket, prefix = s3_and_prefix
    keys = ev._build_keys(prefix, "m", "j", "s", 1)
    obs = {"status": "OK", "n": 1}
    rec = ev.put_evidence(_T1Session(), s3, bucket, keys, PNG_1x1, obs)
    assert rec.sizes["screenshot"] == len(PNG_1x1)
    assert len(rec.checksums["screenshot"]["sha256"]) == 64
    ok, detail = ev.verify_evidence(s3, bucket, rec)
    assert ok, detail
    url = ev.sign_url(_T1Session(), s3, bucket, keys["screenshot"], expires_s=300)
    assert url.startswith("https://") and "X-Amz-Signature" in url
    with urllib.request.urlopen(url, timeout=30, context=_ssl_ctx()) as r:
        assert r.status == 200
        assert r.read() == PNG_1x1


@needs_bucket
def test_checksum_mismatch_is_not_verified(s3_and_prefix):
    from primeqa.browser_worker import evidence as ev
    s3, bucket, prefix = s3_and_prefix
    keys = ev._build_keys(prefix, "m", "j", "s", 1)
    rec = ev.put_evidence(_T1Session(), s3, bucket, keys, PNG_1x1, {"status": "OK"})
    rec.checksums["screenshot"]["sha256"] = "0" * 64      # wrong sha
    ok, detail = ev.verify_evidence(s3, bucket, rec)
    assert ok is False
    assert "sha256" in detail["screenshot"]
    assert detail["observation"] == "ok"


@needs_bucket
def test_missing_object_is_not_verified(s3_and_prefix):
    from primeqa.browser_worker import evidence as ev
    s3, bucket, prefix = s3_and_prefix
    rec = ev.EvidenceRecord(keys=ev._build_keys(prefix, "m", "j", "gone", 1),
                            checksums={}, sizes={}, content_types={})
    ok, detail = ev.verify_evidence(s3, bucket, rec)
    assert ok is False and "head failed" in detail["screenshot"]


@needs_bucket
def test_signed_url_expires(s3_and_prefix):
    from primeqa.browser_worker import evidence as ev
    s3, bucket, prefix = s3_and_prefix
    keys = ev._build_keys(prefix, "m", "j", "s", 1)
    ev.put_evidence(_T1Session(), s3, bucket, keys, PNG_1x1, {"status": "OK"})
    url = ev.sign_url(_T1Session(), s3, bucket, keys["screenshot"], expires_s=1)
    time.sleep(3)
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(url, timeout=30, context=_ssl_ctx())
    assert ei.value.code in (400, 403)


@needs_bucket
@pytest.mark.skipif(not SPIKE_DB, reason="set SPIKE_DATABASE_URL")
def test_sweep_finds_planted_orphan_only(s3_and_prefix):
    from primeqa.browser_worker import evidence as ev
    from primeqa.browser_worker.queue import open_tenant_session
    s3, bucket, prefix = s3_and_prefix
    orphan = f"{prefix}/orphan/1/screenshot.png"
    s3.put_object(Bucket=bucket, Key=orphan, Body=PNG_1x1, ContentType="image/png")
    session = open_tenant_session(1, SPIKE_DB)
    try:
        assert ev.sweep_orphans(session, s3, bucket, prefix) == [orphan]
    finally:
        session.close()
