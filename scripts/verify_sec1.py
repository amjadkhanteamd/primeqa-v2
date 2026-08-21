#!/usr/bin/env python3
"""verify_sec1.py — prove SEC-1 took effect: GET /api/connections/<id> must
admin-gate (non-admin -> 403) and mask secrets (admin -> 200 with no
client_secret / password / api_token / api_key / refresh_token in the body).

READ-ONLY: issues GET requests (and, in --login mode, one POST to
/api/auth/login) only. Makes NO writes. Verifies SEC-1 against a running
deployment; it has not yet run against production.

Three ways to supply the token (pick one):

  1) --login EMAIL --password PASS   (RECOMMENDED against prod)
        Logs in via /api/auth/login and uses the returned access token. No
        secret handling — the token is issued by the server itself. --role must
        match the account's real role (so the assertion is meaningful).

  2) --token <jwt>
        Use a token you already have (e.g. copied from your browser session).

  3) (default) mint locally
        Signs a JWT with core.secrets.get_jwt_secret(). This ONLY works when the
        local JWT_SECRET equals the target server's — i.e. against a local dev
        server, or against prod only if you `export JWT_SECRET=<the real hex>`.

Usage:
  # dev (local secret matches local server):
  python scripts/verify_sec1.py --base-url http://localhost:5000 --role viewer --conn-id 2

  # prod, login-based (safe — no secret on your shell):
  python scripts/verify_sec1.py --base-url https://…up.railway.app --role admin \
      --conn-id 2 --login admin@primeqa.io --password '<pass>'
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import requests

SECRET_FIELDS = ("client_secret", "password", "api_token", "api_key",
                 "voyage_api_key", "refresh_token")


def token_via_login(base_url, email, password):
    r = requests.post(f"{base_url.rstrip('/')}/api/auth/login",
                      json={"email": email, "password": password}, timeout=20)
    if r.status_code != 200:
        sys.exit(f"login failed: HTTP {r.status_code}: {r.text[:200]}")
    tok = (r.json() or {}).get("access_token")
    if not tok:
        sys.exit(f"login returned no access_token: {r.text[:200]}")
    return tok


def token_via_mint(role, uid, tenant_id):
    import jwt
    from primeqa.core.secrets import get_jwt_secret
    return jwt.encode({"sub": str(uid), "tenant_id": tenant_id, "role": role},
                      get_jwt_secret(), algorithm="HS256")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="e.g. http://localhost:5000")
    ap.add_argument("--role", required=True,
                    help="the EXPECTED role behaviour to assert: viewer/ba/tester -> 403, admin -> 200-masked")
    ap.add_argument("--conn-id", type=int, required=True)
    ap.add_argument("--uid", type=int, default=1, help="(mint mode) any valid user id for sub")
    ap.add_argument("--tenant-id", type=int, default=1)
    ap.add_argument("--login", metavar="EMAIL", help="log in as this user (needs --password)")
    ap.add_argument("--password", help="password for --login")
    ap.add_argument("--token", help="use this bearer token directly")
    args = ap.parse_args()

    if args.token:
        token, src = args.token, "provided token"
    elif args.login:
        if not args.password:
            sys.exit("--login requires --password")
        token, src = token_via_login(args.base_url, args.login, args.password), f"login as {args.login}"
    else:
        token, src = token_via_mint(args.role, args.uid, args.tenant_id), "locally-minted JWT"

    url = f"{args.base_url.rstrip('/')}/api/connections/{args.conn_id}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    body = r.text
    print(f"auth={src}  role={args.role!r}  GET {url}  ->  HTTP {r.status_code}")

    ok = True
    if args.role in ("viewer", "ba", "tester"):
        if r.status_code == 403:
            print(f"  PASS  non-admin role {args.role!r} is denied (403)")
        else:
            print(f"  FAIL  non-admin role {args.role!r} expected 403, got {r.status_code}: {body[:200]}")
            ok = False
    elif args.role in ("admin", "superadmin"):
        if r.status_code != 200:
            print(f"  FAIL  admin expected 200, got {r.status_code}: {body[:200]}")
            ok = False
        else:
            leaked = [f for f in SECRET_FIELDS if f in body]
            has_config = "config" in (r.json() if body else {})
            if leaked:
                print(f"  FAIL  admin response leaked secret field(s): {leaked}")
                ok = False
            elif has_config:
                print("  FAIL  admin response still carries a 'config' block")
                ok = False
            else:
                print("  PASS  admin gets 200 with no secret fields and no config block")
    else:
        print(f"  SKIP  unrecognised role {args.role!r} (expected viewer/ba/tester/admin)")
        ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
