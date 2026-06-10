"""
One-time YouTube OAuth setup — run this ONCE per channel.

Usage:
  python scripts/setup_youtube_oauth.py                  # single / default account
  python scripts/setup_youtube_oauth.py --account main   # named account
  python scripts/setup_youtube_oauth.py --account travel # second channel

Multiple channels: run once per channel with different --account names.
Credentials are saved to credentials/accounts/yt_{name}.json — no re-auth needed
after first setup (refresh tokens are permanent).

Prerequisites:
  1. Go to https://console.cloud.google.com
  2. Select/create a project
  3. APIs & Services → Enable APIs → search "YouTube Data API v3" → Enable
  4. APIs & Services → Credentials → + CREATE CREDENTIALS → OAuth 2.0 Client ID
     - Application type: Desktop app
     - Name: china_video_bot (or anything)
  5. Download JSON → rename to client_secrets.json
  6. Move to: credentials/client_secrets.json  (in this project folder)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SECRETS_FILE = "credentials/client_secrets.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _update_env(key: str, value: str) -> None:
    """Upsert KEY=value in .env (creates file if absent)."""
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
    print(f"  .env ← {key} updated")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", default="default",
                        help="Account nickname (e.g. 'main', 'travel'). "
                             "Saved as credentials/accounts/yt_{name}.json")
    args = parser.parse_args()
    account_name = args.account

    secrets_path = Path(SECRETS_FILE)
    if not secrets_path.exists():
        print(f"❌  Missing: {SECRETS_FILE}")
        print()
        print("Steps to create it:")
        print("  1. https://console.cloud.google.com → select/create a project")
        print("  2. APIs & Services → + ENABLE APIS → 'YouTube Data API v3' → Enable")
        print("  3. Credentials → + CREATE CREDENTIALS → OAuth 2.0 Client ID")
        print("     Application type: Desktop app → Create")
        print("  4. Download JSON (arrow button) → rename to client_secrets.json")
        print(f"  5. Move to:  {Path(SECRETS_FILE).absolute()}")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow
    import webbrowser

    print(f"Starting YouTube OAuth for account '{account_name}'…\n")

    flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    # Extract the three values
    secrets_data  = json.loads(secrets_path.read_text())
    app_type      = "web" if "web" in secrets_data else "installed"
    client_id     = secrets_data[app_type]["client_id"]
    client_secret = secrets_data[app_type]["client_secret"]
    refresh_token = creds.refresh_token

    # Get channel info (optional, best-effort)
    try:
        from googleapiclient.discovery import build
        yt = build("youtube", "v3", credentials=creds)
        ch = yt.channels().list(part="snippet", mine=True).execute()
        item     = ch.get("items", [{}])[0]
        ch_name  = item.get("snippet", {}).get("title", "")
        ch_id    = item.get("id", "")
    except Exception:
        ch_name, ch_id = "", ""

    print(f"\n  Channel: {ch_name or '(unknown)'}")

    # ── Save to multi-account store ───────────────────────────────────────────
    from agents.account_manager import add_youtube_account
    add_youtube_account(account_name, client_id, client_secret, refresh_token,
                        channel_name=ch_name, channel_id=ch_id)

    # ── Legacy single-account: also update .env + token.json ────────────────
    if account_name == "default":
        try:
            _update_env("YOUTUBE_CLIENT_ID",     client_id)
            _update_env("YOUTUBE_CLIENT_SECRET", client_secret)
            _update_env("YOUTUBE_REFRESH_TOKEN", refresh_token)
        except Exception:
            pass
        Path("credentials/token.json").write_text(creds.to_json())
        print("  Also updated → credentials/token.json (default account)")

    print("\n" + "="*62)
    print(f"✅  YouTube account '{account_name}' ready!")
    print(f"   Channel: {ch_name or '(not retrieved)'}")
    print("="*62)
    print("\nServer .env values (add to Oracle Cloud / GitHub Actions):")
    print(f"  YOUTUBE_CLIENT_ID={client_id}")
    print(f"  YOUTUBE_CLIENT_SECRET={client_secret}")
    print(f"  YOUTUBE_REFRESH_TOKEN={refresh_token}")
    print()
    print("To add a second channel:")
    print(f"  python scripts/setup_youtube_oauth.py --account second_channel")


if __name__ == "__main__":
    main()
