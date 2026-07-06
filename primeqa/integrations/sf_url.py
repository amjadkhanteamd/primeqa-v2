"""SEC-3 / SEC-5: the single Salesforce instance/login URL validator.

The server sends the org's decrypted access-token (SEC-3) or, on the OAuth
token POST, the org's ``client_secret`` / password (SEC-5) to whatever host a
tenant admin put in ``sf_instance_url`` / ``instance_url``. Without validation
that host can be an internal metadata endpoint (``169.254.169.254``), an
intranet service, or an attacker's collector — a classic SSRF + credential
exfiltration.

This is the ONE resolver (constitution: one resolver, not two). It is enforced
at write time (environment creation) AND before every outbound Salesforce call
(both ``test_connection`` methods and the ``_oauth_token`` credential
chokepoint used by S1 sync + S4 execution).

Design: a positive-security **allowlist** — https + a Salesforce domain
(``*.salesforce.com`` / ``*.force.com``, which covers ``*.my.salesforce.com``,
``*.lightning.force.com``, ``*.sandbox.my.salesforce.com``, ``login`` /
``test.salesforce.com`` and classic instance hosts). Pure + DNS-free: the
allowlist already excludes every non-Salesforce hostname and every IP-literal
host, so no runtime host resolution (and its network I/O / DNS-rebinding
surface) is needed. IP-literal hosts are rejected outright as belt-and-braces.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# Instance / login / API hosts are always under one of these Salesforce
# registered domains. Suffix match so subdomains (my., lightning., sandbox.my.,
# cs<N>., etc.) are covered; the exact apexes are allowed too.
_SF_ALLOWED_HOST_SUFFIXES = (".salesforce.com", ".force.com")
_SF_ALLOWED_HOST_APEXES = ("salesforce.com", "force.com")


class SalesforceUrlError(ValueError):
    """An ``sf_instance_url`` / ``login_url`` that is not a valid, allowlisted
    Salesforce https endpoint (SEC-3 / SEC-5). A ``ValueError`` subclass so the
    existing write-path handlers (which map ``ValueError`` to a 400) treat it as
    a validation error."""


def validate_sf_instance_url(url: str) -> str:
    """Return the (stripped) URL if it is a valid, allowlisted Salesforce https
    endpoint; otherwise raise :class:`SalesforceUrlError`. Fail loud — never
    returns a "safe default" on a bad input."""
    if not url or not isinstance(url, str):
        raise SalesforceUrlError("Salesforce instance URL is required")
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme != "https":
        raise SalesforceUrlError(
            f"Salesforce instance URL must use https (got {parsed.scheme or 'no scheme'!r})")
    host = (parsed.hostname or "").lower()
    if not host:
        raise SalesforceUrlError("Salesforce instance URL has no host")
    # Reject IP-literal hosts outright (e.g. https://169.254.169.254/...).
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not an IP literal — the allowlist below is the real guard
    else:
        raise SalesforceUrlError(
            "Salesforce instance URL must be a Salesforce domain, not an IP address")
    if host in _SF_ALLOWED_HOST_APEXES or host.endswith(_SF_ALLOWED_HOST_SUFFIXES):
        return cleaned
    raise SalesforceUrlError(
        f"Salesforce instance URL host {host!r} is not an allowed Salesforce "
        "domain (*.salesforce.com / *.force.com)")
