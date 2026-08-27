"""The portal-persona vault (LLD_PRODUCTIONISATION §a).

Per-tenant portal credentials as tenant-scoped ciphertext under
PORTAL_FERNET_KEY — the second key (TAD §3), present ONLY in the
browser-worker service environment. This module is the single
encrypt/decrypt surface; the web tier holds no portal-crypto at all.

Write path: the CLI below, executed in the worker environment.
SECRETS NEVER TRAVEL IN ARGV (the GO amendment): argv carries
non-secrets only; the username, password, and TOTP seed arrive via
interactive prompts (getpass, no echo) or the sourced session env
(PORTAL_REG_USERNAME / PORTAL_REG_PASSWORD / PORTAL_REG_TOTP_SEED).
Registration is a tenant-admin act with real-actor audit.

Read path: the worker resolves at login time from the job's tenant
session; decryption is job-scoped; plaintext never reaches logs,
manifests, evidence, or the DB.
"""
from __future__ import annotations

import os
import uuid as _uuid_mod

from sqlalchemy import text

from primeqa.browser_worker.audit import record_event
from primeqa.browser_worker.session import (
    PERSONA_INACTIVE,
    PERSONA_NOT_FOUND,
    Credentials,
    LoginError,
)

STORABLE_AUTH_MODES = ("NONE", "TOTP_PROVISIONED", "EXEMPT")


class VaultError(ValueError):
    """A refused vault act — the message names the exact cause."""


def _fernet():
    from cryptography.fernet import Fernet

    raw = os.environ.get("PORTAL_FERNET_KEY", "")
    if not raw:
        raise VaultError(
            "PORTAL_FERNET_KEY is not set — vault acts run only where the "
            "second key lives (the browser-worker environment)")
    return Fernet(raw.encode() if isinstance(raw, str) else raw)


def _require_admin_actor(session, tenant_id: int, actor_user_id: int) -> None:
    row = session.execute(text("""
        SELECT role, is_active FROM public.users
        WHERE id = :u AND tenant_id = :t
    """), {"u": actor_user_id, "t": tenant_id}).fetchone()
    if row is None or not row[1] or row[0] not in ("admin", "superadmin"):
        raise VaultError(
            f"actor {actor_user_id} is not an active admin of tenant "
            f"{tenant_id} — registration is a tenant-admin act")


def register_persona(session, *, tenant_id: int, persona_key: str,
                     site: str, auth_mode: str, username: str,
                     password: str, totp_seed: str | None,
                     actor_user_id: int, notes: str = "") -> dict:
    """Register (or rotate, when the key already exists) one persona.
    Returns metadata only — never a secret."""
    if auth_mode == "UNSUPPORTED":
        raise VaultError(
            "auth_mode UNSUPPORTED is refused at registration — an "
            "unsupported MFA posture is a fact to report, never a "
            "credential to store")
    if auth_mode not in STORABLE_AUTH_MODES:
        raise VaultError(f"unknown auth_mode {auth_mode!r}")
    if (auth_mode == "TOTP_PROVISIONED") != bool(totp_seed):
        raise VaultError(
            "the TOTP seed exists exactly when auth_mode is "
            "TOTP_PROVISIONED — got the opposite")
    if not (persona_key and site and username and password):
        raise VaultError("persona_key, site, username, password required")
    _require_admin_actor(session, tenant_id, actor_user_id)

    f = _fernet()
    enc = lambda v: f.encrypt(v.encode()).decode()
    existing = session.execute(text(
        "SELECT id FROM portal_personas WHERE persona_key = :k"),
        {"k": persona_key}).fetchone()
    if existing is None:
        session.execute(text("""
            INSERT INTO portal_personas
                (id, persona_key, site, username_ciphertext,
                 password_ciphertext, totp_seed_ciphertext, auth_mode,
                 active, registered_by, notes)
            VALUES (:i, :k, :s, :u, :p, :ts, :m, TRUE, :actor, :n)
        """), {"i": str(_uuid_mod.uuid4()), "k": persona_key, "s": site,
               "u": enc(username), "p": enc(password),
               "ts": enc(totp_seed) if totp_seed else None,
               "m": auth_mode, "actor": actor_user_id, "n": notes})
        action = "ui.persona_registered"
    else:
        session.execute(text("""
            UPDATE portal_personas
            SET site = :s, username_ciphertext = :u,
                password_ciphertext = :p, totp_seed_ciphertext = :ts,
                auth_mode = :m, active = TRUE, rotated_by = :actor,
                rotated_at = NOW()
            WHERE persona_key = :k
        """), {"k": persona_key, "s": site, "u": enc(username),
               "p": enc(password),
               "ts": enc(totp_seed) if totp_seed else None,
               "m": auth_mode, "actor": actor_user_id})
        action = "ui.persona_rotated"
    record_event(session, action=action,
                 details={"persona_key": persona_key,
                          "auth_mode": auth_mode, "site": site},
                 user_id=actor_user_id)
    session.flush()
    return {"persona_key": persona_key, "auth_mode": auth_mode,
            "action": action}


def resolve_credentials(session, persona_key: str) -> Credentials:
    """The worker's login-time read — job-scoped decrypt. Absent and
    inactive personas are DISTINCT named permanent classes."""
    row = session.execute(text("""
        SELECT username_ciphertext, password_ciphertext,
               totp_seed_ciphertext, active
        FROM portal_personas WHERE persona_key = :k
    """), {"k": persona_key}).fetchone()
    if row is None:
        raise LoginError(PERSONA_NOT_FOUND,
                         f"no vault persona {persona_key!r} in this tenant")
    if not row[3]:
        raise LoginError(PERSONA_INACTIVE,
                         f"vault persona {persona_key!r} is deactivated")
    f = _fernet()
    dec = lambda v: f.decrypt(v.encode()).decode()
    return Credentials(username=dec(row[0]), password=dec(row[1]),
                       totp_seed=dec(row[2]) if row[2] else None)


def deactivate_persona(session, *, tenant_id: int, persona_key: str,
                       actor_user_id: int) -> None:
    _require_admin_actor(session, tenant_id, actor_user_id)
    n = session.execute(text("""
        UPDATE portal_personas SET active = FALSE WHERE persona_key = :k
    """), {"k": persona_key}).rowcount
    if not n:
        raise VaultError(f"no persona {persona_key!r}")
    record_event(session, action="ui.persona_deactivated",
                 details={"persona_key": persona_key},
                 user_id=actor_user_id)
    session.flush()


def list_personas(session) -> list[dict]:
    """Metadata only — never a secret, never ciphertext."""
    rows = session.execute(text("""
        SELECT persona_key, site, auth_mode, active, registered_at,
               rotated_at
        FROM portal_personas ORDER BY persona_key
    """)).fetchall()
    return [{"persona_key": r[0], "site": r[1], "auth_mode": r[2],
             "active": r[3], "registered_at": r[4], "rotated_at": r[5]}
            for r in rows]


def rotate_key(session, *, tenant_id: int, old_key: str, new_key: str,
               actor_user_id: int) -> int:
    """FND-24: decrypt-under-old / encrypt-under-new per row, stamps +
    one audit event per persona. Both keys present transiently in the
    worker env; the caller destroys the old key after the env swap."""
    from cryptography.fernet import Fernet

    _require_admin_actor(session, tenant_id, actor_user_id)
    from cryptography.fernet import InvalidToken

    old, new = Fernet(old_key.encode()), Fernet(new_key.encode())
    re_enc = lambda v: new.encrypt(old.decrypt(v.encode())).decode()
    rows = session.execute(text("""
        SELECT persona_key, username_ciphertext, password_ciphertext,
               totp_seed_ciphertext
        FROM portal_personas
    """)).fetchall()
    # Pre-verify EVERY row decrypts under the old key before writing
    # anything: a mixed-key table (a half-done prior rotation, or a row
    # written under a lost key) must refuse LOUDLY naming the persona —
    # never re-encrypt some rows and corrupt the rest's recoverability.
    for r in rows:
        try:
            old.decrypt(r[1].encode())
        except InvalidToken:
            raise VaultError(
                f"persona {r[0]!r} does not decrypt under the OLD key — "
                f"mixed-key state; resolve it (re-register or remove the "
                f"row) before rotating") from None
    for r in rows:
        session.execute(text("""
            UPDATE portal_personas
            SET username_ciphertext = :u, password_ciphertext = :p,
                totp_seed_ciphertext = :ts, rotated_by = :actor,
                rotated_at = NOW()
            WHERE persona_key = :k
        """), {"k": r[0], "u": re_enc(r[1]), "p": re_enc(r[2]),
               "ts": re_enc(r[3]) if r[3] else None,
               "actor": actor_user_id})
        record_event(session, action="ui.persona_rotated",
                     details={"persona_key": r[0], "kind": "key-rotation"},
                     user_id=actor_user_id)
    session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# CLI — python -m primeqa.browser_worker.vault {register|list|deactivate|rotate-key}
# ---------------------------------------------------------------------------

def _prompt_secret(env_name: str, label: str, required: bool = True) -> str:
    """Sourced session env first; interactive no-echo prompt otherwise.
    NEVER argv."""
    import getpass

    val = os.environ.get(env_name, "")
    if not val:
        val = getpass.getpass(f"{label}: ")
    if required and not val:
        raise VaultError(f"{label} required (env {env_name} or prompt)")
    return val


def main(argv=None) -> int:
    import argparse

    from primeqa.browser_worker.queue import open_tenant_session

    parser = argparse.ArgumentParser(
        prog="python -m primeqa.browser_worker.vault",
        description="Portal-persona vault (secrets via prompt/session "
                    "env only — never argv)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tenant-id", type=int, required=True)
    common.add_argument("--actor-user-id", type=int, required=True)

    p_reg = sub.add_parser("register", parents=[common])
    p_reg.add_argument("--persona", required=True)
    p_reg.add_argument("--site", required=True)
    p_reg.add_argument("--auth-mode", required=True)
    p_reg.add_argument("--notes", default="")
    p_list = sub.add_parser("list")
    p_list.add_argument("--tenant-id", type=int, required=True)
    p_de = sub.add_parser("deactivate", parents=[common])
    p_de.add_argument("--persona", required=True)
    sub.add_parser("rotate-key", parents=[common])

    args = parser.parse_args(argv)
    session = open_tenant_session(args.tenant_id)
    try:
        if args.cmd == "register":
            totp = None
            if args.auth_mode == "TOTP_PROVISIONED":
                totp = _prompt_secret("PORTAL_REG_TOTP_SEED", "TOTP seed")
            out = register_persona(
                session, tenant_id=args.tenant_id, persona_key=args.persona,
                site=args.site, auth_mode=args.auth_mode,
                username=_prompt_secret("PORTAL_REG_USERNAME", "Username"),
                password=_prompt_secret("PORTAL_REG_PASSWORD", "Password"),
                totp_seed=totp, actor_user_id=args.actor_user_id,
                notes=args.notes)
            print(f"{out['action']}: {out['persona_key']} "
                  f"({out['auth_mode']})")
        elif args.cmd == "list":
            for p in list_personas(session):
                print(f"{p['persona_key']:20} {p['auth_mode']:18} "
                      f"{'active' if p['active'] else 'INACTIVE'} "
                      f"site={p['site']}")
        elif args.cmd == "deactivate":
            deactivate_persona(session, tenant_id=args.tenant_id,
                               persona_key=args.persona,
                               actor_user_id=args.actor_user_id)
            print(f"deactivated: {args.persona}")
        elif args.cmd == "rotate-key":
            n = rotate_key(
                session, tenant_id=args.tenant_id,
                old_key=_prompt_secret("PORTAL_OLD_FERNET_KEY", "OLD key"),
                new_key=_prompt_secret("PORTAL_NEW_FERNET_KEY", "NEW key"),
                actor_user_id=args.actor_user_id)
            print(f"re-encrypted {n} persona(s); swap the env var and "
                  f"destroy the old key")
        session.commit()
        return 0
    except (VaultError, LoginError) as exc:
        session.rollback()
        print(f"REFUSED: {exc}")
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
