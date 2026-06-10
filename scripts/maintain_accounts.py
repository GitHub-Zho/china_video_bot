"""
Account Health Maintenance — run periodically to keep all credentials valid.

What it does:
  - Instagram : proactively refreshes tokens if < 15 days remain
                verifies token is accepted by the API
  - YouTube   : lightweight API call to confirm refresh token still works
  - Writes    : credentials/accounts/status.json  (health report)
  - Exit code : 0 = all healthy,  1 = at least one account needs attention

WHY proactive refresh matters:
  Instagram tokens last 60 days.  If you don't publish for a month and only
  refresh lazily at upload time, the token can expire between runs.  This
  script closes that gap — run it weekly regardless of whether you published.

Server setup (Oracle Cloud / any Linux VPS):
  # Run every Sunday at 03:00
  crontab -e
  0 3 * * 0  cd /path/to/china_video_bot && python scripts/maintain_accounts.py >> logs/account_health.log 2>&1

Local usage:
  python scripts/maintain_accounts.py

Check current status without running:
  cat credentials/accounts/status.json
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.account_manager import get_accounts, ACCOUNTS_DIR

STATUS_FILE = ACCOUNTS_DIR / "status.json"
LOGS_DIR    = Path("logs")

# ── Instagram ─────────────────────────────────────────────────────────────────

def _check_instagram(acc: dict) -> dict:
    """Check and (if needed) refresh one Instagram account. Returns status dict."""
    import requests

    name     = acc.get("name", "?")
    username = acc.get("username") or acc.get("user_id", "?")
    token    = acc.get("access_token", "")
    exp      = acc.get("expires_at")

    if not token:
        return {"status": "missing_token", "action": "needs_reauth",
                "message": "no access_token stored"}

    days_left = int((exp - time.time()) // 86400) if exp else None

    # ── Proactive refresh if < 15 days remain ────────────────────────────────
    if exp and days_left is not None and days_left < 15:
        if days_left <= 0:
            return {"status": "expired", "username": f"@{username}",
                    "expires_in_days": 0,
                    "action": "needs_reauth",
                    "message": "token expired — re-run: python scripts/setup_instagram.py "
                               f"--account {name}"}
        # Token still alive but expiring soon — refresh now
        try:
            r = requests.get(
                "https://graph.instagram.com/refresh_access_token",
                params={"grant_type": "ig_refresh_token", "access_token": token},
                timeout=20,
            )
            r.raise_for_status()
            d = r.json()
            if "access_token" in d:
                new_token = d["access_token"]
                new_exp   = int(time.time()) + d.get("expires_in", 60 * 86400)
                new_days  = d.get("expires_in", 0) // 86400
                # Persist to file
                file_path = acc.get("_file")
                if file_path:
                    save = {k: v for k, v in acc.items() if not k.startswith("_")}
                    save["access_token"] = new_token
                    save["expires_at"]   = new_exp
                    Path(file_path).write_text(json.dumps(save, indent=2))
                return {"status": "refreshed", "username": f"@{username}",
                        "expires_in_days": new_days,
                        "action": f"token refreshed (was {days_left}d remaining → now {new_days}d)"}
        except Exception as e:
            return {"status": "refresh_failed", "username": f"@{username}",
                    "expires_in_days": days_left,
                    "action": "retry_later" if days_left > 2 else "needs_reauth",
                    "message": str(e)}

    # ── Verify token is accepted by the API ───────────────────────────────────
    try:
        r = requests.get(
            "https://graph.instagram.com/me",
            params={"fields": "id,username", "access_token": token},
            timeout=15,
        )
        if r.status_code == 200:
            return {"status": "ok", "username": f"@{username}",
                    "expires_in_days": days_left}
        err = r.json().get("error", {}).get("message", r.text[:120])
        return {"status": "api_error", "username": f"@{username}",
                "action": "needs_reauth", "message": err}
    except Exception as e:
        return {"status": "unreachable", "username": f"@{username}",
                "message": str(e)[:120]}


# ── YouTube ───────────────────────────────────────────────────────────────────

def _check_youtube(acc: dict) -> dict:
    """Verify a YouTube account refresh token is still valid."""
    name = acc.get("name", "?")
    ch   = acc.get("channel_name") or acc.get("channel_id") or "?"

    try:
        from agents.account_manager import get_youtube_client_for
        yt      = get_youtube_client_for(name)
        result  = yt.channels().list(part="snippet", mine=True).execute()
        items   = result.get("items", [])
        real_ch = items[0]["snippet"]["title"] if items else ch
        return {"status": "ok", "channel": real_ch}
    except Exception as e:
        msg = str(e)
        if any(w in msg.lower() for w in ("expired", "revoked", "invalid_grant", "invalid")):
            return {"status": "token_revoked", "channel": ch,
                    "action": "needs_reauth",
                    "message": f"re-run: python scripts/setup_youtube_oauth.py --account {name}"}
        return {"status": "unreachable", "channel": ch, "message": msg[:120]}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'═'*60}")
    print(f"  Account Health Check  —  {ts}")
    print(f"{'═'*60}\n")

    results: dict[str, dict] = {}
    needs_attention = False

    # ── Instagram ──────────────────────────────────────────────
    ig_accounts = get_accounts("instagram")
    print(f"Instagram  ({len(ig_accounts)} account(s))")
    if not ig_accounts:
        print("  (none configured — run: python scripts/setup_instagram.py)\n")
    for acc in ig_accounts:
        name = acc.get("name", "?")
        r    = _check_instagram(acc)
        results[f"instagram/{name}"] = r

        ok      = r["status"] in ("ok", "refreshed")
        icon    = "✅" if ok else "❌"
        days    = r.get("expires_in_days")
        ds      = f"  [{days}d left]" if days is not None else ""
        action  = r.get("action", "")
        print(f"  {icon} {name:<20}  {r.get('username','')}{ds}  [{r['status']}]")
        if action:
            print(f"      → {action}")
        if not ok:
            needs_attention = True

    print()

    # ── YouTube ────────────────────────────────────────────────
    yt_accounts = get_accounts("youtube")
    print(f"YouTube    ({len(yt_accounts)} account(s))")
    if not yt_accounts:
        print("  (none configured — run: python scripts/setup_youtube_oauth.py)\n")
    for acc in yt_accounts:
        name = acc.get("name", "?")
        r    = _check_youtube(acc)
        results[f"youtube/{name}"] = r

        ok     = r["status"] == "ok"
        icon   = "✅" if ok else "❌"
        action = r.get("action", "")
        print(f"  {icon} {name:<20}  {r.get('channel',''):<30}  [{r['status']}]")
        if action:
            print(f"      → {action}")
        if not ok:
            needs_attention = True

    print()

    # ── Write status.json ──────────────────────────────────────
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    status_data = {
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "healthy":      not needs_attention,
        "accounts":     results,
    }
    STATUS_FILE.write_text(json.dumps(status_data, indent=2))

    # Also append a one-line summary to logs/account_health.log
    LOGS_DIR.mkdir(exist_ok=True)
    summary_icon = "OK" if not needs_attention else "ATTENTION"
    with open(LOGS_DIR / "account_health.log", "a") as f:
        f.write(f"[{ts}]  {summary_icon}  "
                f"yt={len(yt_accounts)}  ig={len(ig_accounts)}  "
                f"issues={'none' if not needs_attention else 'see status.json'}\n")

    print(f"Status saved  → {STATUS_FILE}")
    print(f"Log appended  → logs/account_health.log")

    if needs_attention:
        print("\n⚠️   Some accounts need attention — see above.")
        print("    Instagram expired : python scripts/setup_instagram.py --account <name>")
        print("    YouTube revoked   : python scripts/setup_youtube_oauth.py --account <name>")
        print()
        return 1

    print("\n✅  All accounts healthy.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
