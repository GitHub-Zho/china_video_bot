"""
Media Agent — downloads video clips (Pexels Video) with photo fallback.

Priority:
  1. Pexels Video API  → HD video clips (1920×1080, ≥3s)
  2. Pexels Photos     → fallback if no HD clip found
  3. Unsplash Photos   → fallback if Pexels photo also fails

Returns a list of MediaItem(path, type) so the video agent knows
whether each asset is a 'clip' or a 'photo' and handles accordingly.
"""
import os
import time
import requests
from dataclasses import dataclass
from pathlib import Path
from config.settings import (
    PEXELS_API_KEY, UNSPLASH_ACCESS_KEY, PIXABAY_API_KEY,
    IMAGES_PER_VIDEO, IMAGE_DOWNLOAD_DELAY, API_CALL_DELAY, OUTPUT_DIR,
)


# ── AI image generation (Wanxiang via DashScope) — last-resort footage ────────

def generate_images_wanx(prompt: str, n: int, out_dir: Path) -> list[str]:
    """
    Generate `n` images with Alibaba's Wanxiang (通义万相) text-to-image, used only
    when stock libraries have no good match. Returns list of saved local paths.
    Requires DASHSCOPE_API_KEY; returns [] if unavailable or on failure.
    """
    from config.settings import DASHSCOPE_API_KEY
    if not DASHSCOPE_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
               "Content-Type": "application/json", "X-DashScope-Async": "enable"}
    full_prompt = (f"{prompt}, authentic China, realistic photography, "
                   f"appetizing, high detail, natural lighting")
    body = {"model": "wanx2.1-t2i-turbo",
            "input": {"prompt": full_prompt},
            "parameters": {"n": n, "size": "1280*720"}}
    try:
        r = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
            headers=headers, json=body, verify=False, timeout=30)
        r.raise_for_status()
        tid = r.json()["output"]["task_id"]
        poll_h = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
        for _ in range(30):
            time.sleep(3)
            p = requests.get(f"https://dashscope.aliyuncs.com/api/v1/tasks/{tid}",
                             headers=poll_h, verify=False, timeout=30).json()
            st = p.get("output", {}).get("task_status")
            if st == "SUCCEEDED":
                out = []
                for i, x in enumerate(p["output"].get("results", [])):
                    u = x.get("url")
                    if not u:
                        continue
                    data = requests.get(u, verify=False, timeout=40).content
                    if len(data) > 5000:
                        pth = out_dir / f"gen_{i}.jpg"
                        pth.write_bytes(data)
                        out.append(str(pth))
                return out
            if st == "FAILED":
                return []
    except Exception as e:
        print(f"      [Wanx] generation error: {e}")
    return []

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

def _search_pexels_video_candidates(query: str, n: int = 10) -> list[dict]:
    """
    Return up to n candidate HD clips for a query, each as
    {"id": pexels_video_id, "url": best_file_link}.

    Returning MULTIPLE candidates (not just the first) lets the caller skip
    clips already used by another scene — fixes the repeated-clip problem where
    similar queries all resolved to the same top Pexels video.
    """
    out = []
    try:
        r = _get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "orientation": "landscape",
                    "size": "large", "per_page": 15},
        )
        if not r:
            return out
        for video in r.json().get("videos", []):
            if video.get("duration", 0) < 3:
                continue
            hd_files = [
                f for f in video.get("video_files", [])
                if f.get("width", 0) >= 1920 and f.get("height", 0) >= 1080
            ]
            if hd_files:
                best = max(hd_files, key=lambda f: f.get("width", 0))
                out.append({"id": video.get("id"), "url": best["link"],
                            "preview": video.get("image", "")})
            if len(out) >= n:
                break
    except Exception as e:
        print(f"  [Video] Pexels search error: {e}")
    return out


# ── Pixabay Video ───────────────────────────────────────────────────────────────

def _search_pixabay_video_candidates(query: str, n: int = 10) -> list[dict]:
    """
    Return up to n candidate HD clips from Pixabay, each as
    {"id": "px_<id>", "url": best_file_link}.

    Pixabay is a second free source — adds variety and covers gaps where Pexels
    has little China footage. Video ids are prefixed "px_" so they never collide
    with Pexels integer ids in the shared dedup set.
    """
    out = []
    if not PIXABAY_API_KEY:
        return out
    try:
        r = _get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_API_KEY, "q": query,
                    "per_page": 20, "safesearch": "true"},
        )
        if not r:
            return out
        for hit in r.json().get("hits", []):
            if hit.get("duration", 0) < 3:
                continue
            files = hit.get("videos", {})
            # Prefer large, fall back to medium; require ≥1920 wide
            for size in ("large", "medium"):
                f = files.get(size, {})
                if f.get("width", 0) >= 1920 and f.get("url"):
                    out.append({"id": f"px_{hit.get('id')}", "url": f["url"],
                                "preview": f.get("thumbnail", "")})
                    break
            if len(out) >= n:
                break
    except Exception as e:
        print(f"  [Video] Pixabay search error: {e}")
    return out


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


# ── Vision selection (优中选优) ─────────────────────────────────────────────────

def _pick_best_candidate(candidates: list[dict], query: str) -> dict | None:
    """
    Use the verifier (Gemini) to pick the candidate whose PREVIEW best matches
    the scene query. Downloads only tiny preview thumbnails (cheap), not clips.

    Returns the chosen candidate dict, or None to let the caller fall back to
    "first unused" ordering (when vision is unavailable or no preview).
    """
    import tempfile, shutil
    from agents.vision import vision_available, analyse_images_json

    if not vision_available():
        return None

    with_preview = [c for c in candidates if c.get("preview")][:6]
    if len(with_preview) < 2:
        return None   # nothing to choose between — caller uses first unused

    prev_dir = Path(tempfile.mkdtemp(prefix="prev_"))
    try:
        paths, kept = [], []
        for i, c in enumerate(with_preview):
            data = _download_bytes(c["preview"])
            if data:
                pth = prev_dir / f"prev_{i}.jpg"
                pth.write_bytes(data)
                paths.append(str(pth))
                kept.append(c)
        if len(kept) < 2:
            return None

        labels = [f"[Candidate {i}]" for i in range(len(kept))]
        prompt = (
            f"Each image is a candidate stock-video preview for this scene:\n"
            f'"{query}"\n\n'
            f"Pick the candidate that best matches the scene — correct subject, clearly "
            f"China travel footage. Also rate how well it actually matches (0-10).\n"
            f'Return ONLY JSON: {{"best": <number>, "score": <0-10>, "why": "<short>"}}'
        )
        result = analyse_images_json(paths, prompt, labels)
        if isinstance(result, dict) and isinstance(result.get("best"), int):
            idx = result["best"]
            if 0 <= idx < len(kept):
                chosen = dict(kept[idx])
                chosen["score"] = result.get("score", 0)
                return chosen
        return None
    finally:
        shutil.rmtree(prev_dir, ignore_errors=True)


def find_replacement_clip(video_id: str, scene_index: int, search_query: str,
                          narration: str, min_score: int = 6) -> bool:
    """
    Auto-remediation when QA flags a scene mismatch:
      1. Search fresh STOCK candidates.
      2. If the best stock preview doesn't clearly match, also GENERATE 2 images
         with Wanxiang (通义万相).
      3. Score the stock previews + generated images TOGETHER (Qwen) and pick the
         single best match for the narration.
      4. Replace the scene with the winner — a stock clip (.mp4) or a generated
         image (.jpg, shown with Ken Burns).
    Returns True if replaced, False if nothing good enough exists.
    """
    import tempfile, shutil
    from agents.vision import vision_available, analyse_images_json
    if not vision_available():
        return False

    media_dir = Path(OUTPUT_DIR) / video_id / "media"
    judge = f"{narration} ({search_query})"
    china_q = f"China {search_query}" if "china" not in search_query.lower() else search_query
    stock = (_search_pexels_video_candidates(china_q, n=10) +
             _search_pixabay_video_candidates(china_q, n=6))
    stock = [c for c in stock if c.get("preview")][:5]

    work = Path(tempfile.mkdtemp(prefix="fix_"))
    try:
        # Build a unified candidate pool: stock previews + generated images
        pool = []   # {kind: "clip"|"image", preview_path, url(for clip)|img_path}
        for c in stock:
            data = _download_bytes(c["preview"])
            if data:
                pp = work / f"st_{len(pool)}.jpg"
                pp.write_bytes(data)
                pool.append({"kind": "clip", "preview": str(pp), "url": c["url"]})

        # Generate 2 images as extra candidates (the user's idea — let Qwen make
        # footage when the library has none)
        gen = generate_images_wanx(judge, 2, work)
        for g in gen:
            pool.append({"kind": "image", "preview": g, "img": g})
        if gen:
            print(f"      scene {scene_index}: generated {len(gen)} image(s) to compare")

        if not pool:
            return False

        # Score all candidates together against the narration
        paths  = [c["preview"] for c in pool]
        labels = [f"[Candidate {i} — {'AI-generated' if pool[i]['kind']=='image' else 'stock'}]"
                  for i in range(len(pool))]
        prompt = (
            f"Each image is a candidate visual for this narration line:\n\"{narration}\"\n"
            f"(subject: {search_query})\n\n"
            f"Pick the candidate that BEST shows what the narration describes, and rate it 0-10.\n"
            f'Return ONLY JSON: {{"best": <number>, "score": <0-10>, "why": "<short>"}}'
        )
        result = analyse_images_json(paths, prompt, labels)
        if not (isinstance(result, dict) and isinstance(result.get("best"), int)):
            return False
        idx = result["best"]
        score = result.get("score", 0)
        if not (0 <= idx < len(pool)) or score < min_score:
            print(f"      scene {scene_index}: best only scored {score}/10 — keeping original")
            return False

        winner = pool[idx]
        if winner["kind"] == "clip":
            data = _download_bytes(winner["url"])
            if not data or len(data) < 50_000:
                return False
            (media_dir / f"{scene_index:02d}.jpg").unlink(missing_ok=True)
            (media_dir / f"{scene_index:02d}.mp4").write_bytes(data)
            print(f"      scene {scene_index}: ✅ replaced with stock clip (score {score}/10)")
        else:   # generated image → photo segment (Ken Burns)
            (media_dir / f"{scene_index:02d}.mp4").unlink(missing_ok=True)
            shutil.copy(winner["img"], media_dir / f"{scene_index:02d}.jpg")
            print(f"      scene {scene_index}: ✅ replaced with AI-generated image (score {score}/10)")
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── Scene alternatives (human-in-the-loop mismatch fix) ───────────────────────

def download_scene_alternatives(video_id: str, scene_index: int, query: str,
                                n: int = 3) -> list[dict]:
    """
    When QA flags a content mismatch for a scene, download N ALTERNATIVE clips
    for that scene's query so the user can pick a better match.

    Saves into output/{vid}/alternatives/scene_{i:02d}/:
        alt_0.mp4 / alt_0.jpg (preview), alt_1.mp4 / alt_1.jpg, ...
    Skips any clip identical (same bytes) to the scene's current media.
    Returns [{"index":k, "clip":path, "preview":path}, ...].
    """
    import hashlib
    china_q = f"China {query}" if "china" not in query.lower() else query
    alt_dir = Path(OUTPUT_DIR) / video_id / "alternatives" / f"scene_{scene_index:02d}"
    alt_dir.mkdir(parents=True, exist_ok=True)

    # Hash of the clip currently used for this scene (to exclude it)
    cur = Path(OUTPUT_DIR) / video_id / "media" / f"{scene_index:02d}.mp4"
    cur_hash = (hashlib.md5(cur.read_bytes()).hexdigest()
                if cur.exists() else None)

    candidates = (_search_pexels_video_candidates(china_q, n=10) +
                  _search_pixabay_video_candidates(china_q, n=6))

    out, seen = [], set()
    for c in candidates:
        if len(out) >= n:
            break
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        data = _download_bytes(c["url"])
        if not data or len(data) < 50_000:
            continue
        if cur_hash and hashlib.md5(data).hexdigest() == cur_hash:
            continue   # this is the clip we already used — skip
        k = len(out)
        clip_path = alt_dir / f"alt_{k}.mp4"
        clip_path.write_bytes(data)
        # preview thumbnail (first frame) for quick eyeballing
        prev_path = alt_dir / f"alt_{k}.jpg"
        from config.settings import FFMPEG_BIN
        import subprocess
        subprocess.run([FFMPEG_BIN, "-y", "-ss", "1", "-i", str(clip_path),
                        "-frames:v", "1", "-vf", "scale=480:-1", str(prev_path)],
                       capture_output=True)
        out.append({"index": k, "clip": str(clip_path), "preview": str(prev_path)})
        time.sleep(IMAGE_DOWNLOAD_DELAY)

    print(f"      Scene {scene_index}: {len(out)} alternative(s) → {alt_dir}")
    return out


# ── Public API ─────────────────────────────────────────────────────────────────

def download_media(video_id: str, queries: list[str],
                   match_descriptions: list[str] | None = None) -> list[MediaItem]:
    """
    Download one media item per scene.

    queries:            SHORT keyword queries for the stock SEARCH (the subject/
                        dish/action). Keep these plain — stock sites are keyword
                        matchers, not semantic search.
    match_descriptions: optional richer per-scene descriptions used only to JUDGE
                        which candidate fits best (Gemini vision-pick). Falls back
                        to the search query.
    Resume-safe; tries clip first, photo fallback.
    """
    out_dir = Path(OUTPUT_DIR) / video_id / "media"
    out_dir.mkdir(parents=True, exist_ok=True)

    items: list[MediaItem] = []
    target = queries[:IMAGES_PER_VIDEO]
    used_video_ids: set = set()   # dedup: never use the same clip twice

    for i, query in enumerate(target):
        china_q = f"China {query}" if "china" not in query.lower() else query
        judge_q = (match_descriptions[i] if match_descriptions and i < len(match_descriptions)
                   else china_q)

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

        # ── Try 1: video clip — pool Pexels + Pixabay, dedup, Vision-pick ──
        candidates = _search_pexels_video_candidates(china_q)
        candidates += _search_pixabay_video_candidates(china_q)
        fresh = [c for c in candidates if c["id"] not in used_video_ids]
        # Vision picks the best-matching fresh candidate; falls back to first.
        pick = None
        if fresh:
            best = _pick_best_candidate(fresh, judge_q)
            pick = best if best else fresh[0]
            if best:
                print("(vision-picked) ", end="")
        got_clip = False
        if pick:
            data = _download_bytes(pick["url"])
            if data and len(data) > 50_000:
                clip_path.write_bytes(data)
                used_video_ids.add(pick["id"])
                reused = " (search had only used clips)" if not fresh else ""
                print(f"clip ✓ ({len(data)//1024}KB){reused}")
                items.append(MediaItem(str(clip_path), "clip"))
                got_clip = True
                time.sleep(IMAGE_DOWNLOAD_DELAY)
        if got_clip:
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
