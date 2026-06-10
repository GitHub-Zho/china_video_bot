"""
Instagram Agent — uploads Reels to Instagram via Meta Graph API.

Auth flow:
  • One-time: run scripts/setup_instagram.py → saves IG_USER_ID + IG_ACCESS_TOKEN to .env
  • Long-lived tokens last 60 days; this module auto-refreshes when < 10 days remain.
  • Token is stored in credentials/instagram_token.json (local) AND .env (server).

Upload flow (no public URL needed):
  1. POST resumable upload session → get video handle
  2. PUT binary video data
  3. POST /{ig-user-id}/media   (media_type=REELS, video_handle=..., caption=...)
  4. Poll /{container-id}?fields=status_code until FINISHED (up to 5 min)
  5. POST /{ig-user-id}/media_publish → get post ID

Reels requirements:
  • Aspect ratio: 9:16  ✅ (reels.mp4)
  • Duration: 3s – 15 min  ✅
  • Format: MP4 / MOV  ✅
  • Min resolution: 540×960  ✅ (we output 1080×1920)
"""
import json
import os
import time
from pathlib import Path

import requests

# ── Constants ─────────────────────────────────────────────────────────────────

GRAPH_BASE   = "https://graph.facebook.com/v20.0"
UPLOAD_BASE  = "https://rupload.facebook.com/video-upload/v20.0"
TOKEN_FILE   = "credentials/instagram_token.json"


# ── Token helpers ──────────────────────────────────────────────────────────────

def _load_token() -> dict:
    """
    Load token from credentials/instagram_token.json (preferred, stores expiry)
    or fall back to IG_ACCESS_TOKEN env var.
    Returns {"access_token": str, "expires_at": int|None, "user_id": str}.
    """
    tf = Path(TOKEN_FILE)
    if tf.exists():
        try:
            data = json.loads(tf.read_text())
            if data.get("access_token"):
                return data
        except Exception:
            pass

    token = os.environ.get("IG_ACCESS_TOKEN", "")
    user_id = os.environ.get("IG_USER_ID", "")
    if not token or not user_id:
        raise EnvironmentError(
            "Instagram not configured.\n"
            "Run: python scripts/setup_instagram.py\n"
            "Then add IG_USER_ID and IG_ACCESS_TOKEN to your .env"
        )
    return {"access_token": token, "expires_at": None, "user_id": user_id}


def _save_token(data: dict) -> None:
    Path("credentials").mkdir(exist_ok=True)
    Path(TOKEN_FILE).write_text(json.dumps(data, indent=2))


def _refresh_if_needed(token_data: dict) -> dict:
    """
    Instagram long-lived tokens last 60 days and can be refreshed any time
    before they expire.  Refresh when < 10 days remain.
    """
    expires_at = token_data.get("expires_at")
    if expires_at and (expires_at - time.time()) > 10 * 86400:
        return token_data   # plenty of time left

    token = token_data["access_token"]
    try:
        r = requests.get(
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=20,
        )
        r.raise_for_status()
        d = r.json()
        if "access_token" in d:
            new_data = {
                "access_token": d["access_token"],
                "expires_at":   int(time.time()) + d.get("expires_in", 60 * 86400),
                "user_id":      token_data["user_id"],
            }
            _save_token(new_data)
            # Also update .env so it persists on server
            _update_env_token(new_data["access_token"])
            print(f"  [Instagram] 🔄 Token refreshed (valid {d.get('expires_in',0)//86400} more days)")
            return new_data
    except Exception as e:
        print(f"  [Instagram] ⚠️  Token refresh failed: {e} — continuing with existing token")
    return token_data


def _update_env_token(new_token: str) -> None:
    """Update IG_ACCESS_TOKEN in .env in-place (best-effort)."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    updated = []
    found = False
    for line in lines:
        if line.startswith("IG_ACCESS_TOKEN="):
            updated.append(f"IG_ACCESS_TOKEN={new_token}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"IG_ACCESS_TOKEN={new_token}")
    env_path.write_text("\n".join(updated) + "\n")


# ── Core upload ────────────────────────────────────────────────────────────────

def _resumable_upload(video_path: str, access_token: str, ig_user_id: str) -> str:
    """
    Upload the video file to Meta's resumable upload endpoint.
    Returns the video_handle string used to create the media container.
    """
    video_path = Path(video_path)
    file_size  = video_path.stat().st_size

    # Step 1 — create upload session
    print(f"  [Instagram] Creating upload session ({file_size/1_048_576:.1f} MB)…")
    session_r = requests.post(
        f"{UPLOAD_BASE}/{ig_user_id}",
        headers={
            "Authorization":     f"OAuth {access_token}",
            "X-FB-Video-Size":   str(file_size),
            "X-FB-Upload-Type":  "video",
        },
        timeout=30,
    )
    session_r.raise_for_status()
    upload_url = session_r.headers.get("X-FB-Video-Upload-Url") or session_r.json().get("upload_url")
    if not upload_url:
        raise RuntimeError(f"No upload URL in response: {session_r.text[:300]}")

    # Step 2 — stream the file
    print(f"  [Instagram] Uploading video…", end="", flush=True)
    with open(video_path, "rb") as fh:
        upload_r = requests.post(
            upload_url,
            headers={
                "Authorization":    f"OAuth {access_token}",
                "Content-Type":     "application/octet-stream",
                "offset":           "0",
                "file_size":        str(file_size),
            },
            data=fh,
            timeout=300,
        )
    upload_r.raise_for_status()
    handle = upload_r.json().get("h")
    if not handle:
        raise RuntimeError(f"No video handle in upload response: {upload_r.text[:300]}")
    print(" ✅")
    return handle


def _build_caption(script_data: dict) -> str:
    """
    Format the Instagram caption: first line = hook (the reel's opening hook),
    then a blank line, then 5-8 relevant hashtags.
    Keep it under ~2200 chars (Instagram limit).
    """
    topic       = script_data.get("topic", "China")
    description = script_data.get("description", "")
    tags        = script_data.get("tags", [])

    # Use the first sentence of the description as the caption body
    first_para = description.split("\n")[0][:200] if description else topic

    hashtags = []
    # Always include these China travel base tags
    base_tags = ["#china", "#chineseculture", "#chinatravel", "#chinafood",
                 "#exploreChina", "#shorts", "#reels"]
    # Add topic-derived tags from the brief's tag list
    for t in tags[:5]:
        ht = "#" + t.replace(" ", "").replace("-", "").lower()
        if ht not in base_tags:
            hashtags.append(ht)
    hashtags += base_tags
    hashtags = hashtags[:15]  # Instagram allows up to ~30, keep it tidy

    return f"{first_para}\n\n{' '.join(hashtags)}"


def upload_reels(video_path: str, script_data: dict,
                 share_to_feed: bool = True) -> str:
    """
    Upload a Reels video to Instagram and publish it.

    video_path   — path to the reels.mp4 (1080×1920 9:16)
    script_data  — brief metadata dict (topic, description, tags, hook…)
    share_to_feed — also share to Instagram feed (default True)

    Returns the Instagram media ID (post ID).
    """
    token_data = _load_token()
    token_data = _refresh_if_needed(token_data)
    token      = token_data["access_token"]
    ig_user_id = token_data["user_id"]
    caption    = _build_caption(script_data)

    # ── 1. Resumable upload ──────────────────────────────────────────────────
    video_handle = _resumable_upload(video_path, token, ig_user_id)

    # ── 2. Create Reels media container ─────────────────────────────────────
    print("  [Instagram] Creating Reels container…")
    container_r = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        params={
            "media_type":    "REELS",
            "video_handle":  video_handle,
            "caption":       caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "access_token":  token,
        },
        timeout=60,
    )
    container_r.raise_for_status()
    container_id = container_r.json().get("id")
    if not container_id:
        raise RuntimeError(f"No container ID: {container_r.text[:300]}")

    # ── 3. Poll until processing finishes (up to 5 minutes) ─────────────────
    print("  [Instagram] Processing…", end="", flush=True)
    for attempt in range(60):          # 60 × 5s = 5 min
        time.sleep(5)
        status_r = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=20,
        )
        status_r.raise_for_status()
        status_code = status_r.json().get("status_code", "")
        if status_code == "FINISHED":
            print(" ✅")
            break
        if status_code in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                f"Container failed: {status_r.json().get('status', status_code)}"
            )
        if attempt % 6 == 5:
            print(".", end="", flush=True)
    else:
        raise TimeoutError("Instagram container processing timed out (5 min)")

    # ── 4. Publish the container ─────────────────────────────────────────────
    print("  [Instagram] Publishing…")
    pub_r = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        params={"creation_id": container_id, "access_token": token},
        timeout=30,
    )
    pub_r.raise_for_status()
    post_id = pub_r.json().get("id")
    if not post_id:
        raise RuntimeError(f"No post ID in publish response: {pub_r.text[:300]}")

    print(f"  [Instagram] ✅ Published → https://www.instagram.com/p/{post_id}/")
    return post_id


def instagram_available() -> bool:
    """Return True if Instagram credentials are configured."""
    try:
        _load_token()
        return True
    except EnvironmentError:
        return False
