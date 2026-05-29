"""Image Agent — downloads China photos from Pexels + Unsplash (alternating to respect rate limits)."""
import time
import requests
from pathlib import Path
from config.settings import (
    PEXELS_API_KEY, UNSPLASH_ACCESS_KEY,
    IMAGES_PER_VIDEO, IMAGE_DOWNLOAD_DELAY, API_CALL_DELAY, OUTPUT_DIR
)


def _search_pexels(query: str, count: int = 2) -> list[str]:
    """Return up to `count` image URLs from Pexels."""
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": count, "orientation": "landscape", "size": "large"},
            timeout=10
        )
        r.raise_for_status()
        return [p["src"]["large2x"] for p in r.json().get("photos", [])]
    except Exception as e:
        print(f"  [Pexels] '{query}' failed: {e}")
        return []


def _search_unsplash(query: str, count: int = 2) -> list[str]:
    """Return up to `count` image URLs from Unsplash."""
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            params={"query": query, "per_page": count, "orientation": "landscape"},
            timeout=10
        )
        r.raise_for_status()
        return [p["urls"]["regular"] for p in r.json().get("results", [])]
    except Exception as e:
        print(f"  [Unsplash] '{query}' failed: {e}")
        return []


def download_images(video_id: str, image_queries: list[str]) -> list[str]:
    """
    Downloads one image per query (alternating Pexels/Unsplash).
    Falls back to the other source if primary returns nothing.
    Returns sorted list of local file paths.
    """
    out_dir = Path(OUTPUT_DIR) / video_id / "images"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    queries = image_queries[:IMAGES_PER_VIDEO]  # cap at setting

    for i, query in enumerate(queries):
        china_query = f"China {query}" if "china" not in query.lower() else query
        img_path    = out_dir / f"{i:02d}.jpg"

        # Skip if already downloaded (resume-safe)
        if img_path.exists() and img_path.stat().st_size > 5000:
            print(f"  [Image] {i+1}/{len(queries)} already exists, skipping")
            paths.append(str(img_path))
            continue

        # Alternate sources; odd index → Unsplash, even → Pexels
        time.sleep(API_CALL_DELAY)
        urls = _search_pexels(china_query, 1) if i % 2 == 0 else _search_unsplash(china_query, 1)
        if not urls:
            time.sleep(API_CALL_DELAY)
            urls = _search_unsplash(china_query, 1) if i % 2 == 0 else _search_pexels(china_query, 1)

        if not urls:
            print(f"  [Image] No results for '{china_query}', skipping slot")
            continue

        # Download
        try:
            img_data = requests.get(urls[0], timeout=20).content
            if len(img_data) < 5000:
                print(f"  [Image] Suspiciously small file for '{china_query}', skipping")
                continue
            img_path.write_bytes(img_data)
            paths.append(str(img_path))
            print(f"  [Image] {i+1}/{len(queries)} downloaded: {img_path.name}")
        except Exception as e:
            print(f"  [Image] Download failed for '{china_query}': {e}")

        time.sleep(IMAGE_DOWNLOAD_DELAY)

    return sorted(paths)
