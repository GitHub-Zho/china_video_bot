"""Publisher Agent — uploads video to YouTube.

Supports two auth modes (auto-detected):
  • Cloud mode  (GitHub Actions): reads YOUTUBE_CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN from env
  • Local mode  (Mac):            uses credentials/token.json + browser OAuth on first run
"""
import json
import os
from pathlib import Path
from datetime import date

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from config.settings import (
    SECRETS_FILE, TOKEN_FILE, PUBLISHED_FILE,
    VIDEO_PRIVACY, YOUTUBE_CATEGORY, CREDENTIALS_DIR
)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _get_credentials_cloud() -> Credentials:
    """Build credentials from environment variables (GitHub Actions)."""
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri=TOKEN_URI,
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    creds.refresh(Request())   # exchange refresh token → access token
    return creds


def _get_credentials_local() -> Credentials:
    """Load cached token or run browser OAuth (Mac first-run setup)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path   = Path(TOKEN_FILE)
    secrets_path = Path(SECRETS_FILE)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            return creds

    if not secrets_path.exists():
        raise FileNotFoundError(
            f"Missing {SECRETS_FILE}.\n"
            "Run: python scripts/setup_youtube_oauth.py\n"
            "Or download client_secrets.json from Google Cloud Console."
        )

    flow  = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    Path(CREDENTIALS_DIR).mkdir(exist_ok=True)
    token_path.write_text(creds.to_json())
    print("  [Publisher] Token saved → credentials/token.json")
    return creds


def _get_credentials() -> Credentials:
    """Auto-detect cloud vs local mode."""
    if os.environ.get("YOUTUBE_REFRESH_TOKEN"):
        print("  [Publisher] Auth mode: cloud (env vars)")
        return _get_credentials_cloud()
    print("  [Publisher] Auth mode: local (token.json)")
    return _get_credentials_local()


def get_youtube_client():
    creds = _get_credentials()
    return build("youtube", "v3", credentials=creds), creds


def upload_video(video_path: str, script_data: dict, privacy: str = None) -> str:
    """Upload MP4 to YouTube. Returns video_id."""
    privacy  = privacy or VIDEO_PRIVACY
    youtube, _ = get_youtube_client()

    print(f"  [Publisher] Uploading: {script_data['title']}")

    insert_req = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title":           script_data["title"],
                "description":     script_data.get("description", ""),
                "tags":            script_data.get("tags", []),
                "categoryId":      YOUTUBE_CATEGORY,
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus":           privacy,
                "selfDeclaredMadeForKids": False,
            },
        },
        media_body=MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    )

    response = None
    while response is None:
        status, response = insert_req.next_chunk()
        if status:
            print(f"  [Publisher] {int(status.progress() * 100)}% uploaded...", end="\r")

    video_id = response["id"]
    print(f"\n  [Publisher] ✅ https://www.youtube.com/watch?v={video_id}")

    # Persist to published_videos.json
    pub_path = Path(PUBLISHED_FILE)
    log = json.loads(pub_path.read_text()) if pub_path.exists() else []
    log.append({
        "video_id":      video_id,
        "topic":         script_data.get("topic", ""),
        "audience_type": script_data.get("audience_type", ""),
        "publish_date":  str(date.today()),
    })
    pub_path.parent.mkdir(exist_ok=True)
    pub_path.write_text(json.dumps(log, indent=2))
    return video_id
