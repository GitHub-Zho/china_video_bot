"""
Media Agent — downloads video clips (Pexels Video) with photo fallback.

Priority:
  1. Pexels Video API  → HD video clips (1920×1080, ≥3s)
  2. Pexels Photos     → fallback if no HD clip found
  3. Unsplash Photos   → fallback if Pexels photo also fails

Returns a list of MediaItem(path, type) so the video agent knows
whether each asset is a 'clip' or a 'photo' and handles accordingly.
"""
import time
import requests
from dataclasses import dataclass
from pathlib import Path
from config.settings import (
    PEXELS_API_KEY, UNSPLASH_ACCESS_KEY,
    IMAGES_PER_VIDEO, IMAGE_DOWNLOAD_DELAY, API_CALL_DELAY, OUTPUT_DIR,
)

# ── Types ──────────────────────────────────────────────────────────────────────

@dataclass
class MediaItem:
    path: str
    kind: str   # "clip" | "photo"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(url: str, **kwargs) -> requests.Response | None:
    """GET with SSL fallback (Mac SSL quirk) and silent error handling."""
    for verify in (True, False):
        try:
            r = requests.get(url, timeout=20, verify=verify, **kwargs)
            r.raise_for_status()
            return r
        except requests.exceptions.SSLError:
            continue
        except Exception as e:
            if not verify:
                raise e
    return None


def _download_bytes(url: str) -> bytes | None:
    """Download binary content with SSL fallback."""
    for verify in (True, False):
        try:
            r = requests.get(url, timeout=60, verify=verify, stream=True)
            r.raise_for_status()
            data = b"".join(r.iter_content(65536))
            return data if len(data) > 10_000 else None
        except requests.exceptions.SSLError:
            continue
        except Exception:
            return None
    return None


# ── Pexels Video ──────────────────────────────────────────────────────────────

def _search_pexels_video(query: str) -> str | None:
    """Return direct URL of best HD landscape clip, or None."""
    try:
        r = _get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "orientation": "landscape",
                    "size": "large", "per_page": 8},
        )
        if not r:
            return None

        for video in r.json().get("videos", []):
            # Collect all HD landscape files
            hd_files = [
                f for f in video.get("video_files", [])
                if f.get("width", 0) >= 1920
                and f.get("height", 0) >= 1080
                and video.get("duration", 0) >= 3
            ]
            if hd_files:
                # Pick highest resolution
                best = max(hd_files, key=lambda f: f.get("width", 0))
                return best["link"]
    except Exception as e:
        print(f"  [Video] Pexels search error: {e}")
    return None


# ── Pexels Photo ──────────────────────────────────────────────────────────────

def _search_pexels_photo(query: str) -> str | None:
    try:
        r = _get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": 1,
                    "orientation": "landscape", "size": "large"},
        )
        if not r:
            return None
        photos = r.json().get("photos", [])
        return photos[0]["src"]["large2x"] if photos else None
    except Exception:
        return None


# ── Unsplash Photo ────────────────────────────────────────────────────────────

def _search_unsplash_photo(query: str) -> str | None:
    try:
        r = _get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            params={"query": query, "per_page": 1, "orientation": "landscape"},
        )
        if not r:
            return None
        results = r.json().get("results", [])
        return results[0]["urls"]["regular"] if results else None
    except Exception:
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def download_media(video_id: str, queries: list[str]) -> list[MediaItem]:
    """
    Download one media item per query. Tries video clip first, photo as fallback.
    Returns list of MediaItem(path, kind).
    Resume-safe: skips already-downloaded files.
    """
    out_dir = Path(OUTPUT_DIR) / video_id / "media"
    out_dir.mkdir(parents=True, exist_ok=True)

    items: list[MediaItem] = []
    target = queries[:IMAGES_PER_VIDEO]

    for i, query in enumerate(target):
        china_q = f"China {query}" if "china" not in query.lower() else query

        # Check resume: clip takes priority if both exist
        clip_path  = out_dir / f"{i:02d}.mp4"
        photo_path = out_dir / f"{i:02d}.jpg"

        if clip_path.exists() and clip_path.stat().st_size > 50_000:
            print(f"  [Media] {i+1}/{len(target)} clip already exists, skipping")
            items.append(MediaItem(str(clip_path), "clip"))
            continue
        if photo_path.exists() and photo_path.stat().st_size > 5_000:
            print(f"  [Media] {i+1}/{len(target)} photo already exists, skipping")
            items.append(MediaItem(str(photo_path), "photo"))
            continue

        print(f"  [Media] {i+1}/{len(target)} '{china_q}'…", end=" ", flush=True)
        time.sleep(API_CALL_DELAY)

        # ── Try 1: Pexels video clip ──────────────────────────────────────
        clip_url = _search_pexels_video(china_q)
        if clip_url:
            data = _download_bytes(clip_url)
            if data and len(data) > 50_000:
                clip_path.write_bytes(data)
                print(f"clip ✓ ({len(data)//1024}KB)")
                items.append(MediaItem(str(clip_path), "clip"))
                time.sleep(IMAGE_DOWNLOAD_DELAY)
                continue

        # ── Try 2: Pexels photo ───────────────────────────────────────────
        time.sleep(API_CALL_DELAY)
        photo_url = _search_pexels_photo(china_q)
        if not photo_url:
            # Try 3: Unsplash photo
            time.sleep(API_CALL_DELAY)
            photo_url = _search_unsplash_photo(china_q)

        if photo_url:
            data = _download_bytes(photo_url)
            if data and len(data) > 5_000:
                photo_path.write_bytes(data)
                print(f"photo ✓ ({len(data)//1024}KB)")
                items.append(MediaItem(str(photo_path), "photo"))
                time.sleep(IMAGE_DOWNLOAD_DELAY)
                continue

        print("skipped (no result)")

    clips  = sum(1 for m in items if m.kind == "clip")
    photos = sum(1 for m in items if m.kind == "photo")
    print(f"  [Media] Total: {len(items)} items ({clips} clips, {photos} photos)")
    return items
