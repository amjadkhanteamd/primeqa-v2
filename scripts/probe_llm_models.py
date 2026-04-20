"""Probe Anthropic model availability for every tenant's LLM connection.

Run from Railway so the production CREDENTIAL_ENCRYPTION_KEY decrypts
the stored API key correctly:

    railway run python scripts/probe_llm_models.py

Hits /v1/models for the authoritative list, then runs a 5-token
"say ok" against each candidate model id. Prints a green/red grid
showing which ids we can actually use. Costs sub-$0.01 per tenant.

Use the output to update primeqa/intelligence/llm/router.py constants
(OPUS / SONNET / HAIKU) to valid, non-deprecated model ids.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import primeqa.db as db_mod
db_mod.init_db(os.environ["DATABASE_URL"])

from sqlalchemy.orm import sessionmaker
from primeqa.core.repository import ConnectionRepository


# Candidates to probe. Covers the current 4-series AND every 3.5 fallback
# so we can see which of each is still served. The script doesn't care
# about deprecation — only about whether a call succeeds today.
CANDIDATES = [
    # Claude 4.7 (newest as of 2026-04-20)
    "claude-opus-4-7",
    "claude-opus-4-7-latest",
    # Claude 4.6
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    # Claude 4.5 (dated + alias, correct date this time)
    "claude-opus-4-5-20251101",
    "claude-opus-4-5-latest",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
    # Claude 4.1 (midway 4.x upgrade path)
    "claude-opus-4-1-20250805",
    # Claude 4 (current default, deprecated 6/15/2026)
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
]


def _ansi(s: str, color: str) -> str:
    codes = {"green": "32", "red": "31", "yellow": "33", "grey": "90"}
    return f"\033[{codes.get(color, '37')}m{s}\033[0m"


def probe_connection(conn_id: int, tenant_id: int, name: str) -> None:
    Session = sessionmaker(bind=db_mod.engine)
    sess = Session()
    try:
        repo = ConnectionRepository(sess)
        decrypted = repo.get_connection_decrypted(conn_id, tenant_id=tenant_id)
        if not decrypted:
            print(f"  Conn #{conn_id} ({name}): could not fetch")
            return
        api_key = decrypted["config"].get("api_key", "")
        if api_key.startswith("gAAAAA"):
            print(f"  Conn #{conn_id} ({name}): KEY DECRYPT FAILED "
                  "(CREDENTIAL_ENCRYPTION_KEY probably mismatched)")
            return
        print(f"\n== Tenant {tenant_id} conn #{conn_id} '{name}' "
              f"(key ...{api_key[-4:]}) ==")

        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=15)

        # /v1/models — the authoritative catalog
        try:
            lst = client.models.list(limit=100)
            print(f"\n  /v1/models ({len(lst.data)} entries):")
            for m in lst.data:
                print(f"    \u2022 {m.id}  (display: {getattr(m, 'display_name', '-')})")
        except Exception as e:
            print(f"  /v1/models failed: {type(e).__name__}: {str(e)[:120]}")

        # Live 5-token probe per candidate
        print("\n  Live probe (5-token 'say ok'):")
        for mid in CANDIDATES:
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    r = client.messages.create(
                        model=mid, max_tokens=5,
                        messages=[{"role": "user", "content": "ok"}],
                    )
                txt = (r.content[0].text if r.content else "").strip()[:30]
                print("    " + _ansi(f"\u2713 OK    {mid}  -> '{txt}'", "green"))
            except Exception as e:
                code = getattr(e, "status_code", "?")
                msg = str(e)
                if "not_found_error" in msg:
                    print("    " + _ansi(f"\u2717 404   {mid}  (model not available)", "red"))
                elif "deprecat" in msg.lower():
                    print("    " + _ansi(f"\u26a0  dep  {mid}  (deprecated but served)", "yellow"))
                else:
                    print("    " + _ansi(f"\u2717 {code:<5} {mid}  -> {msg[:80]}", "red"))
    finally:
        sess.close()


def main():
    Session = sessionmaker(bind=db_mod.engine)
    sess = Session()
    try:
        from primeqa.core.models import Connection
        conns = sess.query(Connection).filter_by(connection_type="llm").all()
        if not conns:
            print("No LLM connections found.")
            return
        print(f"Probing {len(conns)} LLM connection(s)...")
        for c in conns:
            probe_connection(c.id, c.tenant_id, c.name)
    finally:
        sess.close()


if __name__ == "__main__":
    main()
