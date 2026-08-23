"""Evidence store (ui-s2.5): R2/S3 upload, verify, sign, orphan sweep.

Design: docs/ui-testing/LLD_EVIDENCE_STORE.md.
  - keys: {tenant_schema}/{manifest_id}/{job_id}/{surface_key}/{attempt}/
           screenshot.png | observation.json — the tenant prefix is DERIVED
           from the session's tenant context, never passed by callers;
  - lifecycle CAPTURED -> UPLOADED -> VERIFIED -> REFERENCED; this module
    performs UPLOADED and VERIFIED; the DB layer (queue.py) records state;
  - credentials: EVIDENCE_S3_* env, read at client-build time, never
    logged or persisted; the bucket stays private (presigned GET only);
  - sweep_orphans is REPORT-ONLY (no deletes in the spike).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass, field

SCREENSHOT = "screenshot"
OBSERVATION = "observation"
_FILENAMES = {SCREENSHOT: "screenshot.png", OBSERVATION: "observation.json"}
_CONTENT_TYPES = {SCREENSHOT: "image/png", OBSERVATION: "application/json"}
DEFAULT_SIGN_EXPIRES_S = 86400


class EvidenceConfigError(RuntimeError):
    pass


def bucket_name() -> str:
    b = os.environ.get("EVIDENCE_S3_BUCKET")
    if not b:
        raise EvidenceConfigError("EVIDENCE_S3_BUCKET unset")
    return b


def client():
    """boto3 S3 client against the configured endpoint. Credentials are
    read here and handed straight to boto3; nothing is logged."""
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("EVIDENCE_S3_ENDPOINT")
    key_id = os.environ.get("EVIDENCE_S3_ACCESS_KEY_ID")
    secret = os.environ.get("EVIDENCE_S3_SECRET_ACCESS_KEY")
    if not (endpoint and key_id and secret):
        raise EvidenceConfigError(
            "EVIDENCE_S3_ENDPOINT/ACCESS_KEY_ID/SECRET_ACCESS_KEY unset")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            # R2: flexible-checksum headers only when the call requires them.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=5, read_timeout=30,
            retries={"max_attempts": 1},
        ),
    )


def key_prefix(session) -> str:
    """Tenant prefix from the session's tenant context (DE-19). Refuses a
    session that carries no tenant scope."""
    schema = (getattr(session, "info", None) or {}).get("tenant_schema")
    if not schema:
        raise EvidenceConfigError("session carries no tenant_schema")
    return schema


def build_keys(prefix: str, manifest_id: str, job_id: str,
               surface_key: str, attempt: int) -> dict:
    base = f"{prefix}/{manifest_id}/{job_id}/{surface_key}/{attempt}"
    return {k: f"{base}/{fn}" for k, fn in _FILENAMES.items()}


@dataclass
class EvidenceRecord:
    keys: dict
    checksums: dict = field(default_factory=dict)     # {artifact: {sha256, md5}}
    sizes: dict = field(default_factory=dict)         # {artifact: bytes}
    content_types: dict = field(default_factory=dict)

    def as_db(self, state: str, detail: dict | None = None) -> dict:
        return {"state": state, "keys": self.keys, "checksums": self.checksums,
                "sizes": self.sizes, "content_types": self.content_types,
                "detail": detail}


def _digests(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "md5": hashlib.md5(data).hexdigest()}


def put_evidence(s3, bucket: str, keys: dict, screenshot_png: bytes,
                 observation: dict) -> EvidenceRecord:
    """UPLOADED: PUT both objects with sha256 metadata; returns the record
    (keys, sha256+md5, sizes, content types). Raises on any failure —
    the caller records EVIDENCE_INCOMPLETE (reached=CAPTURED)."""
    obs_bytes = json.dumps(observation, sort_keys=True,
                           default=str).encode("utf-8")
    rec = EvidenceRecord(keys=dict(keys))
    for artifact, data in ((SCREENSHOT, screenshot_png), (OBSERVATION, obs_bytes)):
        d = _digests(data)
        s3.put_object(Bucket=bucket, Key=keys[artifact], Body=data,
                      ContentType=_CONTENT_TYPES[artifact],
                      Metadata={"sha256": d["sha256"]})
        rec.checksums[artifact] = d
        rec.sizes[artifact] = len(data)
        rec.content_types[artifact] = _CONTENT_TYPES[artifact]
    return rec


def verify_evidence(s3, bucket: str, rec: EvidenceRecord) -> tuple[bool, dict]:
    """VERIFIED iff, for every artifact, HEAD confirms existence + byte
    size + checksum: object metadata sha256 == stored sha256, and — when
    the ETag is a plain MD5 (single-part PUT) — ETag == stored md5."""
    detail = {}
    ok = True
    for artifact, key in rec.keys.items():
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 — report, don't guess
            detail[artifact] = f"head failed: {type(exc).__name__}"
            ok = False
            continue
        problems = []
        if head.get("ContentLength") != rec.sizes.get(artifact):
            problems.append(f"size {head.get('ContentLength')} != {rec.sizes.get(artifact)}")
        meta_sha = (head.get("Metadata") or {}).get("sha256")
        if meta_sha != rec.checksums.get(artifact, {}).get("sha256"):
            problems.append("sha256 metadata mismatch")
        etag = (head.get("ETag") or "").strip('"')
        if len(etag) == 32 and all(c in "0123456789abcdef" for c in etag.lower()):
            if etag.lower() != rec.checksums.get(artifact, {}).get("md5"):
                problems.append("etag/md5 mismatch")
        if problems:
            ok = False
            detail[artifact] = "; ".join(problems)
        else:
            detail[artifact] = "ok"
    return ok, detail


def sign_url(s3, bucket: str, key: str,
             expires_s: int = DEFAULT_SIGN_EXPIRES_S) -> str:
    """Presigned GET; pure/on-demand; nothing stored. Never public."""
    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key},
        ExpiresIn=int(expires_s))


def list_keys(s3, bucket: str, prefix: str) -> list:
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append(obj["Key"])
    return keys


def referenced_keys(session) -> set:
    """Every object key some result row references (both artifacts)."""
    from sqlalchemy import text
    rows = session.execute(text("""
        SELECT evidence_keys FROM s4_ui_inspection_results
        WHERE evidence_keys IS NOT NULL
    """)).fetchall()
    out = set()
    for (keys,) in rows:
        out.update(v for v in (keys or {}).values() if v)
    return out


def sweep_orphans(session, s3, bucket: str, prefix: str) -> list:
    """REPORT-ONLY: object keys under prefix that no result row references.
    The crash window between upload and DB write produces exactly these."""
    listed = set(list_keys(s3, bucket, prefix))
    return sorted(listed - referenced_keys(session))


# ---- manual CLI: sign a result's screenshot; sweep a prefix ---------------

def main() -> int:
    from primeqa.browser_worker.queue import open_tenant_session
    from sqlalchemy import text

    p = argparse.ArgumentParser(prog="python -m primeqa.browser_worker.evidence")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sign", help="print a presigned GET for a result's screenshot")
    s.add_argument("--tenant", type=int, required=True)
    s.add_argument("--job", required=True)
    s.add_argument("--surface", required=True)
    s.add_argument("--expires-s", type=int, default=DEFAULT_SIGN_EXPIRES_S)
    w = sub.add_parser("sweep", help="report orphaned objects under a prefix")
    w.add_argument("--tenant", type=int, required=True)
    w.add_argument("--prefix", default=None,
                   help="defaults to the tenant prefix")
    a = p.parse_args()

    session = open_tenant_session(a.tenant)
    try:
        s3 = client()
        bucket = bucket_name()
        if a.cmd == "sign":
            row = session.execute(text("""
                SELECT evidence_keys, evidence_state FROM s4_ui_inspection_results
                WHERE job_id = :j AND surface_key = :k
            """), {"j": a.job, "k": a.surface}).fetchone()
            if row is None or not row[0]:
                print("no evidence keys for that result"); return 1
            print(f"evidence_state={row[1]}")
            print(sign_url(s3, bucket, row[0][SCREENSHOT], a.expires_s))
        elif a.cmd == "sweep":
            prefix = a.prefix or key_prefix(session)
            orphans = sweep_orphans(session, s3, bucket, prefix)
            print(f"prefix={prefix} orphans={len(orphans)}")
            for k in orphans:
                print(f"  {k}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
