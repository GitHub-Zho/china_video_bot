"""
Account Manager — multi-account credential store for YouTube and Instagram.

Each account is a single JSON file in credentials/accounts/:
  yt_{name}.json   — YouTube channel
  ig_{name}.json   — Instagram account

Format:
  YouTube:
    {"platform":"youtube","name":"main","channel_name":"...","channel_id":"...",
     "client_id":"...","client_secret":"...","refresh_token":"..."}

  Instagram:
    {"platform":"instagram","name":"food","username":"...","user_id":"...",
     "access_token":"...","expires_at":1234567890}

Usage:
  # Get all configured accounts
  from agents.account_manager import get_accounts, get_youtube_client_for, get_ig_token_for

  yt_accounts = get_accounts("youtube")   # → [{"name":"main", ...}, ...]
  ig_accounts = get_accounts("instagram")

  # Publish to one account
  yt = get_youtube_client_for("main")
  token = get_ig_token_for("food")

  # Publish to ALL configured accounts
  for acc in get_accounts("youtube"):
      yt = get_youtube_client_for(acc["name"])
      ...
"""
import json
import os
import time
from pathlib import Path

import requests

ACCOUNTS_DIR = Path("credentials/accounts")


# ── Account discovery ─────────────────────────────────────────────────────────

def get_accounts(platform: str) -> list[dict]:
    """
    Return all configured accounts for a given platform ("youtube" | "instagram").
    Falls back to single-account .env credentials if no accounts/ files exist.
    """
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    prefix = "yt_" if platform == "youtube" else "ig_"
    files  = sorted(ACCOUNTS_DIR.glob(f"{prefix}*.json"))

    if files:
        accounts = []
        for f in files:
            try:
                data = json.loads(f.read_text())
                data["_file"] = str(f)
                accounts.append(data)
            except Exception:
                pass
        return accounts

    # ── Legacy fallback: single-account from .env ────────────────────────────
    if platform == "youtube":
        token_f = Path("credentials/token.json")
        if token_f.exists():
            try:
                d = json.loads(token_f.read_text())
                # token.json from google-auth has a different format — wrap it
                return [{
                    "platform":      "youtube",
                    "name":          "default",
                    "channel_name":  "default",
                    "_token_json":   str(token_f),   # used by get_youtube_client_for
                    "_legacy":       True,
                }]
            except Exception:
                pass
        rt = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
        if rt:
            return [{
                "platform":       "youtube",
                "name":           "default",
                "client_id":      os.environ.get("YOUTUBE_CLIENT_ID", ""),
                "client_secret":  os.environ.get("YOUTUBE_CLIENT_SECRET", ""),
                "refresh_token":  rt,
                "_legacy":        True,
            }]

    if platform == "instagram":
        ig_f = Path("credentials/instagram_token.json")
        if ig_f.exists():
            try:
                d = json.loads(ig_f.read_text())
                d["platform"] = "instagram"
                d["name"]     = "default"
                d["_file"]    = str(ig_f)
                d["_legacy"]  = True
                return [d]
            except Exception:
                pass
        token = os.environ.get("IG_ACCESS_TOKEN", "")
        uid   = os.environ.get("IG_USER_ID", "")
        if token and uid:
            return [{
                "platform":     "instagram",
                "name":         "default",
                "user_id":      uid,
                "access_token": token,
                "expires_at":   None,
                "_legacy":      True,
            }]

    return []


def add_youtube_account(name: str, client_id: str, client_secret: str,
                        refresh_token: str, channel_name: str = "",
                        channel_id: str = "") -> Path:
    """
    Save a new YouTube account credentials file.
    Returns the path to the saved file.
    """
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "platform":      "youtube",
        "name":          name,
        "channel_name":  channel_name,
        "channel_id":    channel_id,
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    path = ACCOUNTS_DIR / f"yt_{name}.json"
    path.write_text(json.dumps(data, indent=2))
    print(f"  [Accounts] ✅ YouTube account '{name}' saved → {path}")
    return path


def add_instagram_account(name: str, user_id: str, access_token: str,
                           expires_at: int | None, username: str = "") -> Path:
    """Save a new Instagram account credentials file."""
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "platform":     "instagram",
        "name":         name,
        "username":     username,
        "user_id":      user_id,
        "access_token": access_token,
        "expires_at":   expires_at,
    }
    path = ACCOUNTS_DIR / f"ig_{name}.json"
    path.write_text(json.dumps(data, indent=2))
    print(f"  [Accounts] ✅ Instagram account '{name}' (@{username}) saved → {path}")
    return path


# ── YouTube client factory ────────────────────────────────────────────────────

def get_youtube_client_for(account_name: str = "default"):
    """
    Return an authenticated YouTube API client for the named account.
    account_name = the 'name' field in yt_{name}.json, or 'default' for legacy.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    TOKEN_URI = "https://oauth2.googleapis.com/token"
    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

    accounts = get_accounts("youtube")
    acc = next((a for a in accounts if a["name"] == account_name), None)
    if not acc:
        raise ValueError(f"YouTube account '{account_name}' not found. "
                         f"Available: {[a['name'] for a in accounts]}")

    # Legacy token.json path
    if acc.get("_token_json"):
        creds = Credentials.from_authorized_user_file(acc["_token_json"], SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            Path(acc["_token_json"]).write_text(creds.to_json())
        return build("youtube", "v3", credentials=creds)

    # Standard refresh-token path
    creds = Credentials(
        token=None,
        refresh_token=acc["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=acc["client_id"],
        client_secret=acc["client_secret"],
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


# ── Instagram token factory ───────────────────────────────────────────────────

def get_ig_token_for(account_name: str = "default") -> dict:
    """
    Return a valid Instagram token dict for the named account,
    auto-refreshing if < 10 days remain.
    Returns {"access_token": str, "user_id": str, "username": str}.
    """
    accounts = get_accounts("instagram")
    acc = next((a for a in accounts if a["name"] == account_name), None)
    if not acc:
        raise ValueError(f"Instagram account '{account_name}' not found. "
                         f"Available: {[a['name'] for a in accounts]}")

    # Auto-refresh if expiring soon
    expires_at = acc.get("expires_at")
    if expires_at and (expires_at - time.time()) < 10 * 86400:
        try:
            r = requests.get(
                "https://graph.instagram.com/refresh_access_token",
                params={"grant_type": "ig_refresh_token",
                        "access_token": acc["access_token"]},
                timeout=20,
            )
            r.raise_for_status()
            d = r.json()
            if "access_token" in d:
                acc["access_token"] = d["access_token"]
                acc["expires_at"]   = int(time.time()) + d.get("expires_in", 60 * 86400)
                # Persist to file
                file_path = acc.get("_file")
                if file_path:
                    save_data = {k: v for k, v in acc.items()
                                 if not k.startswith("_")}
                    Path(file_path).write_text(json.dumps(save_data, indent=2))
                days = d.get("expires_in", 0) // 86400
                print(f"  [Accounts] 🔄 Instagram @{acc.get('username','?')} "
                      f"token refreshed ({days} days)")
        except Exception as e:
            print(f"  [Accounts] ⚠️  Token refresh failed for '{account_name}': {e}")

    return {
        "access_token": acc["access_token"],
        "user_id":      acc["user_id"],
        "username":     acc.get("username", ""),
    }


# ── Convenience: list all accounts ────────────────────────────────────────────

def list_accounts() -> None:
    """Print a human-readable summary of all configured accounts."""
    yt  = get_accounts("youtube")
    ig  = get_accounts("instagram")

    print("\n── YouTube accounts ──────────────────────────────────")
    if yt:
        for a in yt:
            ch = a.get("channel_name") or a.get("channel_id") or "(channel name unknown)"
            print(f"  • {a['name']:20s}  {ch}")
    else:
        print("  (none configured — run: python scripts/setup_youtube_oauth.py)")

    print("\n── Instagram accounts ────────────────────────────────")
    if ig:
        for a in ig:
            u   = "@" + a.get("username", a.get("user_id", "?"))
            exp = a.get("expires_at")
            if exp:
                days = max(0, int((exp - time.time()) // 86400))
                expiry = f"expires in {days}d"
            else:
                expiry = "no expiry info"
            print(f"  • {a['name']:20s}  {u:25s}  [{expiry}]")
    else:
        print("  (none configured — run: python scripts/setup_instagram.py)")
    print()
