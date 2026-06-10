"""
One-time Instagram OAuth setup — run this ONCE on your Mac.

Prerequisites (do these in the Meta Developer Portal first):
  1. Go to https://developers.facebook.com → My Apps → Create App
     App type: "Business" or "Consumer"
  2. Add products: "Instagram Graph API" and "Facebook Login"
  3. Instagram Graph API → Permissions: add instagram_basic +
     instagram_content_publish + pages_show_list + pages_read_engagement
  4. Facebook Login → Settings → Valid OAuth Redirect URIs:
     add:  https://localhost/callback   (we'll intercept it manually)
  5. App Settings → Basic → copy App ID and App Secret

Usage:
  python scripts/setup_instagram.py                   # single / default account
  python scripts/setup_instagram.py --account food    # named account
  python scripts/setup_instagram.py --account travel  # second account

Multiple accounts: run once per account with different --account names.
Credentials saved to credentials/accounts/ig_{name}.json — auto-renewed every 60 days.
"""
import argparse
import json
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

TOKEN_FILE = "credentials/instagram_token.json"
SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
]
REDIRECT_URI = "http://localhost:8765/callback"

# ── Local HTTP server to catch the OAuth callback ─────────────────────────────

_auth_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _auth_code = qs.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Authorized! You can close this tab.</h1>")

    def log_message(self, *args):
        pass   # silence access log


def _wait_for_callback(timeout: int = 120) -> str:
    global _auth_code
    _auth_code = None
    server = HTTPServer(("localhost", 8765), _CallbackHandler)
    server.timeout = 2

    deadline = time.time() + timeout
    while time.time() < deadline:
        server.handle_request()
        if _auth_code:
            server.server_close()
            return _auth_code
    server.server_close()
    raise TimeoutError("Timed out waiting for OAuth callback (2 min)")


# ── OAuth exchange helpers ────────────────────────────────────────────────────

def _exchange_code(app_id: str, app_secret: str, code: str) -> str:
    """Exchange authorization code → short-lived access token."""
    r = requests.get(
        "https://graph.facebook.com/v20.0/oauth/access_token",
        params={
            "client_id":     app_id,
            "redirect_uri":  REDIRECT_URI,
            "client_secret": app_secret,
            "code":          code,
        },
        timeout=20,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"No token in response: {r.text}")
    return token


def _long_lived_token(app_id: str, app_secret: str, short_token: str) -> tuple[str, int]:
    """Exchange short-lived token → long-lived token (60 days). Returns (token, expires_in_secs)."""
    r = requests.get(
        "https://graph.facebook.com/v20.0/oauth/access_token",
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         app_id,
            "client_secret":     app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=20,
    )
    r.raise_for_status()
    d = r.json()
    return d["access_token"], d.get("expires_in", 60 * 86400)


def _get_ig_accounts(long_token: str) -> list[dict]:
    """
    Find all Instagram Business/Creator accounts connected to this user's
    Facebook Pages. Returns list of {"id", "name", "username"}.
    """
    # Get list of pages this user manages
    pages_r = requests.get(
        "https://graph.facebook.com/v20.0/me/accounts",
        params={"fields": "id,name,instagram_business_account", "access_token": long_token},
        timeout=20,
    )
    pages_r.raise_for_status()
    accounts = []
    for page in pages_r.json().get("data", []):
        ig = page.get("instagram_business_account")
        if ig:
            ig_id = ig["id"]
            # Get IG account details
            detail_r = requests.get(
                f"https://graph.facebook.com/v20.0/{ig_id}",
                params={"fields": "id,name,username", "access_token": long_token},
                timeout=20,
            )
            if detail_r.ok:
                d = detail_r.json()
                accounts.append({
                    "id":       d.get("id", ig_id),
                    "name":     d.get("name", page.get("name", "")),
                    "username": d.get("username", ""),
                })
    return accounts


def _update_env(key: str, value: str) -> None:
    """Upsert a KEY=VALUE line in .env."""
    env_path = Path(".env")
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    updated, found = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"{key}={value}")
    env_path.write_text("\n".join(updated) + "\n")
    print(f"  ✅ {key} updated in .env")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="default",
                        help="Account nickname (e.g. 'food', 'travel'). "
                             "Saved as credentials/accounts/ig_{name}.json")
    args = parser.parse_args()
    account_name = args.account

    print("\n" + "=" * 62)
    print(f"  Instagram Reels Setup — account: '{account_name}'")
    print("=" * 62)
    print()
    print("Prerequisites:")
    print("  1. Meta Developer App with Instagram Graph API enabled")
    print("  2. Permissions: instagram_basic + instagram_content_publish")
    print("     + pages_show_list + pages_read_engagement")
    print("  3. Valid OAuth Redirect URI: http://localhost:8765/callback")
    print()

    app_id     = input("Enter your Meta App ID:     ").strip()
    app_secret = input("Enter your Meta App Secret: ").strip()
    if not app_id or not app_secret:
        print("❌  App ID and Secret are required.")
        sys.exit(1)

    # Build login URL
    auth_url = (
        "https://www.facebook.com/v20.0/dialog/oauth?"
        + urllib.parse.urlencode({
            "client_id":     app_id,
            "redirect_uri":  REDIRECT_URI,
            "scope":         ",".join(SCOPES),
            "response_type": "code",
        })
    )
    print(f"\n{'─'*62}")
    print("Opening browser for authorization…")
    print(f"URL (if browser doesn't open): {auth_url}")
    print(f"{'─'*62}")
    webbrowser.open(auth_url)

    print("\nWaiting for you to approve in the browser…")
    try:
        code = _wait_for_callback(timeout=180)
    except TimeoutError:
        print("❌  Timed out. Run the script again.")
        sys.exit(1)

    print("\n✅ Authorization code received. Exchanging for tokens…")
    short_token              = _exchange_code(app_id, app_secret, code)
    long_token, expires_in   = _long_lived_token(app_id, app_secret, short_token)
    expires_at               = int(time.time()) + expires_in
    days_valid               = expires_in // 86400
    print(f"  ✅ Long-lived token obtained (valid {days_valid} days)")

    # Find IG accounts
    accounts = _get_ig_accounts(long_token)
    if not accounts:
        print("\n❌  No Instagram Business/Creator accounts found.")
        print("    Make sure your Instagram account is a Professional account")
        print("    and is connected to a Facebook Page you manage.")
        sys.exit(1)

    # Let user pick if multiple
    print(f"\nFound {len(accounts)} Instagram account(s):")
    for i, acc in enumerate(accounts):
        print(f"  [{i}] @{acc['username']}  ({acc['name']})  ID: {acc['id']}")

    if len(accounts) == 1:
        chosen = accounts[0]
    else:
        idx = input("\nWhich account to use? Enter number: ").strip()
        chosen = accounts[int(idx)]

    ig_user_id = chosen["id"]
    ig_username = chosen["username"]
    print(f"\n  Using: @{ig_username} (ID: {ig_user_id})")

    # ── Save to multi-account store ───────────────────────────────────────────
    Path("credentials").mkdir(exist_ok=True)
    from agents.account_manager import add_instagram_account
    add_instagram_account(account_name, ig_user_id, long_token, expires_at,
                          username=ig_username)

    # ── Legacy single-account: also update token.json + .env ─────────────────
    if account_name == "default":
        token_data = {
            "access_token": long_token,
            "expires_at":   expires_at,
            "user_id":      ig_user_id,
            "username":     ig_username,
        }
        Path(TOKEN_FILE).write_text(json.dumps(token_data, indent=2))
        print(f"  Also saved → {TOKEN_FILE}  (default account legacy path)")
        print()
        _update_env("IG_USER_ID",      ig_user_id)
        _update_env("IG_ACCESS_TOKEN", long_token)

    print()
    print("=" * 62)
    print(f"✅  Instagram setup complete!  @{ig_username}  [{account_name}]")
    print(f"   Token valid for {days_valid} days (auto-renews before expiry)")
    print("=" * 62)
    print()
    print("Server .env / secrets values:")
    print(f"\n  IG_USER_ID={ig_user_id}")
    print(f"  IG_ACCESS_TOKEN={long_token}")
    print()
    print("To add a second account:")
    print(f"  python scripts/setup_instagram.py --account food")


if __name__ == "__main__":
    main()
