"""One-shot helper to get a refresh_token from your Salesforce Connected App.

Usage:
  1. Set SF_CLIENT_ID and SF_CLIENT_SECRET as env vars (or paste them when prompted)
  2. Run: python3 scripts/get_sf_token.py
  3. Browser opens, log in, click Allow
  4. Script writes refresh_token + instance_url back to terminal
  5. Copy values into .env
"""
import http.server
import secrets
import socketserver
import urllib.parse
import urllib.request
import webbrowser
import os
import sys
import json
import ssl
import certifi

# Edit these for your sandbox
SANDBOX_LOGIN_URL = "https://test.salesforce.com"
CALLBACK_PORT = 1717
CALLBACK_PATH = "/oauth/callback"
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"

CLIENT_ID = os.environ.get("SF_CLIENT_ID") or input("Enter Consumer Key: ").strip()
CLIENT_SECRET = os.environ.get("SF_CLIENT_SECRET") or input("Enter Consumer Secret: ").strip()

state = secrets.token_urlsafe(16)
auth_code = {}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == CALLBACK_PATH:
            qs = urllib.parse.parse_qs(parsed.query)
            auth_code["code"] = qs.get("code", [None])[0]
            auth_code["state"] = qs.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>OK</h1><p>You can close this tab and return to the terminal.</p>")
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *args, **kwargs):
        pass  # suppress access logs

# Build the authorize URL
authorize_url = (
    f"{SANDBOX_LOGIN_URL}/services/oauth2/authorize?"
    + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK_URL,
        "scope": "api refresh_token offline_access",
        "state": state,
    })
)

print("\n[1/3] Opening browser to Salesforce sandbox login...")
print(f"      If the browser does not open, visit:\n      {authorize_url}\n")
webbrowser.open(authorize_url)

print("[2/3] Waiting for redirect on http://localhost:1717 ...")
with socketserver.TCPServer(("localhost", CALLBACK_PORT), Handler) as httpd:
    while "code" not in auth_code:
        httpd.handle_request()

if auth_code.get("state") != state:
    print("ERROR: state mismatch, aborting")
    sys.exit(1)

print("[3/3] Exchanging auth code for tokens...")
token_url = f"{SANDBOX_LOGIN_URL}/services/oauth2/token"
data = urllib.parse.urlencode({
    "grant_type": "authorization_code",
    "code": auth_code["code"],
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": CALLBACK_URL,
}).encode()

req = urllib.request.Request(token_url, data=data, method="POST")
ctx = ssl.create_default_context(cafile=certifi.where())
try:
    with urllib.request.urlopen(req, context=ctx) as resp:
        result = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    print(f"\nERROR: Salesforce returned HTTP {e.code}")
    print(e.read().decode())
    sys.exit(1)

print("\n[OK] SUCCESS")
print(f"\nAdd these to .env:\n")
print(f"SF_CLIENT_ID={CLIENT_ID}")
print(f"SF_CLIENT_SECRET={CLIENT_SECRET}")
print(f"SF_REFRESH_TOKEN={result['refresh_token']}")
print(f"SF_INSTANCE_URL={result['instance_url']}")
print()
