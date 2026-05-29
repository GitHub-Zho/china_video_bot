"""
One-time YouTube OAuth setup — run this ONCE on your Mac.

Prints a Google login URL → you open it in your browser, approve access,
paste the authorization code back. Saves credentials locally AND prints
the three values needed for the Oracle Cloud server .env file.

Usage:
  python scripts/setup_youtube_oauth.py

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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SECRETS_FILE = "credentials/client_secrets.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main():
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

    print("Starting YouTube OAuth (console mode — no browser auto-open)…\n")

    flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)

    # run_local_server with open_browser=False: prints the URL, you open it manually
    creds = flow.run_local_server(port=0, open_browser=False)

    # Extract the three values needed for the server .env
    secrets_data = json.loads(secrets_path.read_text())
    app_type      = "web" if "web" in secrets_data else "installed"
    client_id     = secrets_data[app_type]["client_id"]
    client_secret = secrets_data[app_type]["client_secret"]
    refresh_token = creds.refresh_token

    print("\n" + "="*62)
    print("✅  OAuth successful!  Add these 3 values to your .env:")
    print("="*62)
    print(f"\nYOUTUBE_CLIENT_ID={client_id}")
    print(f"\nYOUTUBE_CLIENT_SECRET={client_secret}")
    print(f"\nYOUTUBE_REFRESH_TOKEN={refresh_token}")
    print("\n" + "="*62)

    # Save locally for Mac testing
    Path("credentials").mkdir(exist_ok=True)
    Path("credentials/token.json").write_text(creds.to_json())
    print("\nAlso saved → credentials/token.json  (for running pipeline on Mac)")


if __name__ == "__main__":
    main()
