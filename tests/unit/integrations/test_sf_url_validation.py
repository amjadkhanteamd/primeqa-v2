"""SEC-3 / SEC-5: validate_sf_instance_url — allowlist + SSRF rejection.

Pure-function unit test (no I/O), so it always runs in the fast unit suite.
"""
import pytest

from primeqa.integrations.sf_url import validate_sf_instance_url, SalesforceUrlError


# Legitimate Salesforce instance / login / API / sandbox hosts.
_VALID = [
    "https://acme.my.salesforce.com",
    "https://acme.my.salesforce.com/",              # trailing slash tolerated
    "https://acme.lightning.force.com",
    "https://acme--dev.sandbox.my.salesforce.com",  # sandbox My Domain
    "https://login.salesforce.com",
    "https://test.salesforce.com",
    "https://na1.salesforce.com",                   # classic instance host
    "https://acme.develop.my.salesforce.com",
]

# SSRF / non-Salesforce / scheme violations — all must be refused.
_INVALID = [
    "http://169.254.169.254/latest/meta-data/",     # cloud metadata (http + IP)
    "https://169.254.169.254/services/data/",       # link-local IP literal
    "https://127.0.0.1/",                           # loopback
    "https://10.0.0.5/",                            # RFC-1918
    "https://192.168.1.1/",                         # RFC-1918
    "https://[::1]/",                               # IPv6 loopback
    "https://evil.example.com/",                    # non-Salesforce host
    "https://acme.my.salesforce.com.evil.com/",     # suffix-spoof
    "http://acme.my.salesforce.com/",               # not https
    "ftp://acme.my.salesforce.com/",                # wrong scheme
    "//acme.my.salesforce.com/",                    # no scheme
    "",                                             # empty
    "not-a-url",                                    # no scheme/host
    None,                                           # non-string
]


@pytest.mark.parametrize("url", _VALID)
def test_valid_salesforce_urls_pass(url):
    assert validate_sf_instance_url(url) == url.strip()


@pytest.mark.parametrize("url", _INVALID)
def test_ssrf_and_non_salesforce_urls_rejected(url):
    with pytest.raises(SalesforceUrlError):
        validate_sf_instance_url(url)


def test_error_is_a_valueerror_subclass():
    # so the write-path handlers that map ValueError -> 400 catch it.
    assert issubclass(SalesforceUrlError, ValueError)
