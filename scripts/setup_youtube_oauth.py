"""
One-time YouTube OAuth setup — run this ONCE on your Mac.

It opens a browser window for Google login, then prints the three values
you need to add as GitHub Secrets.

Usage:
  python scripts/setup_youtube_oauth.py

Prerequisites:
  1. Go to https://console.cloud.google.com
  2. Create a project (or use existing)
  3. Enable "YouTube Data API v3"
  4. Create OAuth 2.0 credentials → Desktop app
  5. Download the JSON → save as credentials/client_secrets.json
"""
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

SECRETS_FILE = "credentials/client_secrets.json"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def main():
    secrets_path = Path(SECRETS_FILE)
    if not secrets_path.exists():
        print(f"❌ Missing: {SECRETS_FILE}")
        print()
        print("Steps to create it:")
        print("  1. Go to https://console.cloud.google.com")
        print("  2. APIs & Services → Credentials → + CREATE CREDENTIALS")
        print("  3. OAuth 2.0 Client ID → Desktop app → Create")
        print("  4. Download JSON → rename to client_secrets.json")
        print(f"  5. Move to: {Path(SECRETS_FILE).absolute()}")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Opening browser for Google OAuth...")
    print("(Log in with the Google account that owns your YouTube channel)\n")

    flow  = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    # Extract the values needed for GitHub Secrets
    secrets_data = json.loads(secrets_path.read_text())
    app_type     = "web" if "web" in secrets_data else "installed"
    client_id     = secrets_data[app_type]["client_id"]
    client_secret = secrets_data[app_type]["client_secret"]
    refresh_token = creds.refresh_token

    print("\n" + "="*60)
    print("✅  OAuth successful! Add these 3 values as GitHub Secrets:")
    print("="*60)
    print(f"\nSecret name : YOUTUBE_CLIENT_ID")
    print(f"Secret value: {client_id}")
    print(f"\nSecret name : YOUTUBE_CLIENT_SECRET")
    print(f"Secret value: {client_secret}")
    print(f"\nSecret name : YOUTUBE_REFRESH_TOKEN")
    print(f"Secret value: {refresh_token}")
    print("\n" + "="*60)
    print("\nHow to add GitHub Secrets:")
    print("  GitHub repo → Settings → Secrets and variables → Actions → New secret")
    print()

    # Also save locally for Mac testing
    Path("credentials").mkdir(exist_ok=True)
    Path("credentials/token.json").write_text(creds.to_json())
    print("Also saved locally → credentials/token.json (for running on Mac)")


if __name__ == "__main__":
    main()
