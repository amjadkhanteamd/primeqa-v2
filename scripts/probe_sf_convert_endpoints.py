"""Probe what Salesforce Lead-conversion endpoints (if any) exist on an env.

Two endpoints tried for Lead convert so far, both 404 on user's sandbox:
  /services/data/vXX/sobjects/LeadConvert       - deprecated/never-GA
  /services/data/vXX/actions/standard/convertLead - should be GA but 404

This script checks what IS available on the org so we know whether to
pursue standard REST, Apex REST, or drop the convert step entirely.

Usage (needs local .env's DATABASE_URL + CREDENTIAL_ENCRYPTION_KEY from
Railway worker service):

    set -a && source .env && set +a
    source venv/bin/activate
    export CREDENTIAL_ENCRYPTION_KEY=$(railway variable list -s worker -k | grep ^CREDENTIAL_ENCRYPTION_KEY= | cut -d= -f2-)
    ./venv/bin/python scripts/probe_sf_convert_endpoints.py 24   # env id
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import primeqa.core.models, primeqa.metadata.models, primeqa.test_management.models  # noqa
import primeqa.execution.models, primeqa.intelligence.models, primeqa.release.models, primeqa.vector.models  # noqa
import primeqa.db as db_mod
db_mod.init_db(os.environ["DATABASE_URL"])

from sqlalchemy.orm import sessionmaker
from primeqa.core.models import Environment
from primeqa.core.repository import EnvironmentRepository, ConnectionRepository
from primeqa.metadata.worker_runner import _oauth_token


def probe(env_id: int):
    import requests as http

    Session = sessionmaker(bind=db_mod.engine)
    sess = Session()
    try:
        env = sess.query(Environment).filter_by(id=env_id).first()
        if not env:
            print(f"env {env_id} not found"); return
        env_repo = EnvironmentRepository(sess)
        conn_repo = ConnectionRepository(sess)
        # Use the SF connection
        conn = conn_repo.get_connection_decrypted(env.connection_id, env.tenant_id)
        if not conn:
            print("No SF connection for env"); return
        token = _oauth_token(env, conn["config"])
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        base = f"{env.sf_instance_url.rstrip('/')}/services/data/v{env.sf_api_version}"

        print(f"\nProbing {env.sf_instance_url.rstrip('/')} v{env.sf_api_version}\n")

        # 1) List all standard invocable actions
        url = f"{base}/actions/standard"
        r = http.get(url, headers=headers, timeout=20)
        print(f"GET /actions/standard  -> {r.status_code}")
        if r.status_code == 200:
            try:
                data = r.json()
                actions = data.get("actions", [])
                print(f"  Total standard actions: {len(actions)}")
                convert_actions = [a for a in actions if "convert" in (a.get("name", "")).lower()]
                print(f"  Actions with 'convert' in name: {len(convert_actions)}")
                for a in convert_actions:
                    print(f"    \u2022 name='{a.get('name')}' label='{a.get('label')}' url='{a.get('url')}'")
                lead_actions = [a for a in actions if "lead" in (a.get("name", "")).lower()]
                if lead_actions:
                    print(f"  Actions with 'lead' in name:")
                    for a in lead_actions:
                        print(f"    \u2022 name='{a.get('name')}' label='{a.get('label')}'")
            except Exception as e:
                print(f"  (failed to parse actions list: {e})")
        else:
            print(f"  body: {r.text[:400]}")

        # 2) Direct probe of the two candidate URLs with tiny payloads
        candidates = [
            ("/actions/standard/convertLead", "POST", {"inputs": [{}]}),
            ("/sobjects/LeadConvert",         "POST", {}),
        ]
        for path, method, body in candidates:
            u = base + path
            try:
                r = http.post(u, json=body, headers={**headers, "Content-Type": "application/json"}, timeout=15)
                snippet = r.text[:240].replace("\n", " ")
                print(f"\n{method} {path:45s} -> {r.status_code}  {snippet}")
            except Exception as e:
                print(f"\n{method} {path:45s} -> EXCEPTION {e}")
    finally:
        sess.close()


if __name__ == "__main__":
    env_id = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    probe(env_id)
