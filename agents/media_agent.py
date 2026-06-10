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
import re
import subprocess
import tempfile
import time
import requests
from dataclasses import dataclass, field
from pathlib import Path
from config.settings import (
    PEXELS_API_KEY, UNSPLASH_ACCESS_KEY, PIXABAY_API_KEY,
    IMAGES_PER_VIDEO, IMAGE_DOWNLOAD_DELAY, API_CALL_DELAY, OUTPUT_DIR,
    FFMPEG_BIN, FFPROBE_BIN, MAX_CLIP_SECONDS,
)


# ── AI image generation (Wanxiang via DashScope) — last-resort footage ────────

def _dashscope_req(method: str, url: str, retries: int = 5, **kwargs):
    """
    DashScope HTTP call that survives the intermittent SSL EOF drops seen on
    flaky/proxied China connections. Retries with verify toggling; raises the
    last error only after exhausting all attempts. Returns the Response.

    This is critical for Wanxiang generation: the async task is SUBMITTED fine,
    but a single SSL drop while POLLING used to throw away the whole (already
    succeeding) generation — leaving only generic stock footage in the pool.
    """
    last = None
    for attempt in range(retries):
        try:
            kwargs.setdefault("timeout", 40)
            kwargs["verify"] = (attempt % 2 == 0)
            r = requests.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(min(6, 1.0 * (attempt + 1)))
    raise last


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
        r = _dashscope_req(
            "POST",
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
            headers=headers, json=body, timeout=30)
        tid = r.json()["output"]["task_id"]
        poll_h = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
        for _ in range(30):
            time.sleep(3)
            try:
                p = _dashscope_req(
                    "GET", f"https://dashscope.aliyuncs.com/api/v1/tasks/{tid}",
                    retries=3, headers=poll_h, timeout=30).json()
            except Exception:
                continue   # a transient drop must NOT abort an in-flight task
            st = p.get("output", {}).get("task_status")
            if st == "SUCCEEDED":
                out = []
                for i, x in enumerate(p["output"].get("results", [])):
                    u = x.get("url")
                    if not u:
                        continue
                    try:
                        data = _dashscope_req("GET", u, retries=4, timeout=60).content
                    except Exception:
                        continue
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


def generate_video_wanx(prompt: str, out_dir: Path) -> str | None:
    """
    Generate a short VIDEO clip with Wanxiang text-to-video (通义万相 wanx2.1-t2v).
    Returns the saved .mp4 path, or None. Slower than image gen (~30-60s) but gives
    real motion footage of exactly the subject — used when stock has no good match.
    """
    from config.settings import DASHSCOPE_API_KEY
    if not DASHSCOPE_API_KEY:
        return None
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
               "Content-Type": "application/json", "X-DashScope-Async": "enable"}
    body = {"model": "wanx2.1-t2v-turbo",
            "input": {"prompt": f"{prompt}, authentic China, cinematic, realistic, appetizing"},
            "parameters": {"size": "1280*720"}}
    try:
        r = _dashscope_req(
            "POST",
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
            headers=headers, json=body, timeout=30)
        tid = r.json()["output"]["task_id"]
        poll_h = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
        for _ in range(40):
            time.sleep(5)
            try:
                p = _dashscope_req(
                    "GET", f"https://dashscope.aliyuncs.com/api/v1/tasks/{tid}",
                    retries=3, headers=poll_h, timeout=30).json()
            except Exception:
                continue   # a transient drop must NOT abort an in-flight task
            st = p.get("output", {}).get("task_status")
            if st == "SUCCEEDED":
                u = p["output"].get("video_url")
                if not u:
                    return None
                try:
                    data = _dashscope_req("GET", u, retries=4, timeout=120).content
                except Exception:
                    return None
                if len(data) > 50_000:
                    pth = out_dir / "genvideo.mp4"
                    pth.write_bytes(data)
                    return str(pth)
                return None
            if st == "FAILED":
                return None
    except Exception as e:
        print(f"      [Wanx] video gen error: {e}")
    return None

# ── Types ──────────────────────────────────────────────────────────────────────

@dataclass
class MediaItem:
    path: str
    kind: str        # "clip" | "photo"
    start_sec: float = 0.0   # clip only: best-segment start time chosen by Qwen-VL


# ── Smart clip segmentation ───────────────────────────────────────────────────

def pick_clip_segment(clip_path: str, narration: str,
                      target_dur: float = MAX_CLIP_SECONDS) -> float:
    """
    Use Qwen-VL to find the most relevant start time in a stock clip.

    Why: stock clips (Pexels/Pixabay) can be 15-30 s long. The most visually
    relevant content — the close-up, the key action — often isn't at t=0.
    Picking blindly from the start means we frequently use generic establishing
    shots when a much better moment exists 8 s later.

    Strategy:
      1. ffprobe → clip duration
      2. Short clip (≤ 1.5 × target)? return 0.0 (no analysis needed)
      3. Build up to 4 candidate windows evenly spaced across the clip
      4. Extract one frame per window (at window midpoint)
      5. Qwen-VL: "which frame best illustrates: <narration>?"
      6. Return that window's start time; fall back to 0.0 on any failure

    Only called for freshly-downloaded stock clips (not AI-generated video,
    not reference frames — those are already precisely selected).
    """
    # ── 1. Get clip duration ─────────────────────────────────────────────────
    try:
        probe = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", clip_path],
            capture_output=True, text=True, timeout=10,
        )
        clip_dur = float(probe.stdout.strip())
    except Exception:
        return 0.0

    # No need to analyze if the clip is barely longer than what we need
    if clip_dur <= target_dur * 1.5:
        return 0.0

    # ── 2. Candidate windows ─────────────────────────────────────────────────
    max_windows = 4
    n_windows   = min(max_windows, max(2, int(clip_dur / target_dur)))
    max_start   = clip_dur - target_dur          # last valid start position
    starts      = [max_start * i / (n_windows - 1) for i in range(n_windows)]

    # ── 3. Extract one representative frame per window ───────────────────────
    tmp_dir     = Path(tempfile.mkdtemp(prefix="seg_pick_"))
    frame_paths: list[str] = []
    labels:      list[str] = []

    try:
        for idx, start in enumerate(starts):
            seek      = start + target_dur * 0.4   # slightly before midpoint
            out_frame = str(tmp_dir / f"frame_{idx:02d}.jpg")
            r = subprocess.run(
                [FFMPEG_BIN, "-y",
                 "-ss", f"{seek:.2f}", "-i", clip_path,
                 "-frames:v", "1", "-q:v", "3", out_frame],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0 and Path(out_frame).exists():
                frame_paths.append(out_frame)
                labels.append(f"Window {idx + 1} (starts at {start:.1f}s)")

        if len(frame_paths) < 2:
            return 0.0

        # ── 4. Qwen-VL: which window best illustrates the scene? ─────────────
        from agents.vision import analyse_images
        prompt = (
            f"I need to cut a {target_dur:.0f}-second clip for a scene described as:\n"
            f'"{narration}"\n\n'
            f"These {len(frame_paths)} frames are sampled from different time windows "
            f"of the same stock video clip. Each frame represents roughly where that "
            f"window's content looks like.\n\n"
            f"Which window number ({', '.join(str(i+1) for i in range(len(frame_paths)))}) "
            f"best matches the scene description — i.e. shows the most relevant, "
            f"visually specific content?\n\n"
            f"Reply with ONLY the window number. Nothing else."
        )
        response = analyse_images(frame_paths, prompt, labels=labels,
                                  temperature=0.1, max_tokens=8)

        if response:
            m = re.search(r'\b([1-4])\b', response.strip())
            if m:
                chosen_idx = int(m.group(1)) - 1
                if 0 <= chosen_idx < len(starts):
                    chosen = starts[chosen_idx]
                    print(f"    [Segment] window {chosen_idx+1} selected "
                          f"(start={chosen:.1f}s / clip={clip_dur:.1f}s) — {narration[:50]}")
                    return chosen

    except Exception as e:
        print(f"    [Segment] pick_clip_segment error: {e}")
    finally:
        for f in frame_paths:
            Path(f).unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except Exception:
            pass

    return 0.0


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


def compete_and_apply(video_id: str, scene_index: int, search_query: str,
                      narration: str, gen_prompt: str = "", min_score: int = 5,
                      make_video: bool = False, used_ids: set | None = None,
                      reference_frames: list | None = None,
                      used_ref_paths: set | None = None) -> str | None:
    """
    Core selection: build a pool of STOCK candidates + AI-GENERATED footage (image,
    and optionally a video) + optional REFERENCE frames extracted from a real video
    (e.g. B站), then let Qwen score them ALL against the narration and apply the best.

    reference_frames: list of jpg paths extracted by reference_agent. ALL of them
        join the pool for EVERY scene; Qwen will naturally pick the one that best
        matches each scene's narration (e.g. the slicing frame scores high for the
        carving scene, low for the history scene).
    gen_prompt: the rich, full-context prompt from the Art Director.
    make_video: also generate an AI video candidate (slower; used on QA escalation).
    Returns "clip" | "genvideo" | "image" | "reference" if applied, else None.
    """
    import tempfile, shutil, subprocess
    from agents.vision import vision_available, analyse_images_json
    from config.settings import FFMPEG_BIN
    if not vision_available():
        return None

    media_dir = Path(OUTPUT_DIR) / video_id / "media"
    gp = gen_prompt.strip() or f"{narration} ({search_query})"
    china_q = f"China {search_query}" if "china" not in search_query.lower() else search_query
    stock = (_search_pexels_video_candidates(china_q, n=10) +
             _search_pixabay_video_candidates(china_q, n=6))
    if used_ids:
        stock = [c for c in stock if c["id"] not in used_ids]
    stock = [c for c in stock if c.get("preview")][:5]

    work = Path(tempfile.mkdtemp(prefix="pick_"))
    try:
        pool = []
        for c in stock:
            data = _download_bytes(c["preview"])
            if data:
                pp = work / f"st_{len(pool)}.jpg"
                pp.write_bytes(data)
                pool.append({"kind": "clip", "preview": str(pp), "url": c["url"], "id": c["id"]})

        # AI candidates from the RICH context-aware prompt
        if make_video:
            gv = generate_video_wanx(gp, work)
            if gv:
                fr = work / "gv_frame.jpg"
                subprocess.run([FFMPEG_BIN, "-y", "-ss", "2", "-i", gv, "-frames:v", "1",
                                "-vf", "scale=480:-1", str(fr)], capture_output=True)
                if fr.exists():
                    pool.append({"kind": "genvideo", "preview": str(fr), "video": gv})
        for g in generate_images_wanx(gp, 1, work):
            pool.append({"kind": "image", "preview": g, "img": g})

        # Reference frames from a real video (B站/YouTube) — all frames enter
        # every scene's pool; Qwen will pick the one that matches each narration best.
        # Already-used frames are excluded so each real frame is used at most once.
        for rf in (reference_frames or []):
            rf_path = Path(rf)
            if rf_path.exists() and str(rf_path) not in (used_ref_paths or set()):
                pool.append({"kind": "reference", "preview": str(rf_path), "img": str(rf_path)})

        if not pool:
            return None

        def _kind_label(k):
            return {"clip": "stock video", "genvideo": "AI video",
                    "image": "AI-generated image", "reference": "real reference frame"}.get(k, k)

        paths  = [c["preview"] for c in pool]
        labels = [f"[Candidate {i} — {_kind_label(pool[i]['kind'])}]"
                  for i in range(len(pool))]
        prompt = (
            f"Each image is a candidate visual for this narration line:\n\"{narration}\"\n"
            f"(subject: {search_query})\n\n"
            f"Pick the candidate that BEST and most clearly shows what the narration "
            f"describes, and rate it 0-10. Prefer clearly-on-topic over generic. "
            f"'real reference frame' candidates are from an authentic video and should "
            f"be preferred when they clearly match.\n"
            f'Return ONLY JSON: {{"best": <number>, "score": <0-10>, "why": "<short>"}}'
        )
        result = analyse_images_json(paths, prompt, labels)
        if not (isinstance(result, dict) and isinstance(result.get("best"), int)):
            return None
        idx, score = result["best"], result.get("score", 0)
        if not (0 <= idx < len(pool)) or score < min_score:
            print(f"      scene {scene_index}: best only {score}/10 — no good option")
            return None

        w = pool[idx]
        if w["kind"] == "clip":
            data = _download_bytes(w["url"])
            if not data or len(data) < 50_000:
                return None
            (media_dir / f"{scene_index:02d}.jpg").unlink(missing_ok=True)
            (media_dir / f"{scene_index:02d}.mp4").write_bytes(data)
            if used_ids is not None and w.get("id") is not None:
                used_ids.add(w["id"])
            print(f"      scene {scene_index}: ✅ stock clip (score {score}/10)")
        elif w["kind"] == "genvideo":
            (media_dir / f"{scene_index:02d}.jpg").unlink(missing_ok=True)
            shutil.copy(w["video"], media_dir / f"{scene_index:02d}.mp4")
            print(f"      scene {scene_index}: ✅ AI-generated VIDEO (score {score}/10)")
        elif w["kind"] == "reference":
            # Prefer the real VIDEO clip alongside the jpg when available —
            # real video has more authenticity than a static frame, and it
            # avoids the Ken Burns zoompan entirely (real motion looks better).
            ref_jpg = Path(w["img"])
            ref_mp4 = ref_jpg.with_suffix(".mp4")
            if ref_mp4.exists() and ref_mp4.stat().st_size > 50_000:
                (media_dir / f"{scene_index:02d}.jpg").unlink(missing_ok=True)
                shutil.copy(str(ref_mp4), media_dir / f"{scene_index:02d}.mp4")
                if used_ref_paths is not None:
                    used_ref_paths.add(w["img"])
                print(f"      scene {scene_index}: ✅ real reference VIDEO clip (score {score}/10)")
            else:
                (media_dir / f"{scene_index:02d}.mp4").unlink(missing_ok=True)
                shutil.copy(w["img"], media_dir / f"{scene_index:02d}.jpg")
                if used_ref_paths is not None:
                    used_ref_paths.add(w["img"])
                print(f"      scene {scene_index}: ✅ real reference frame (score {score}/10)")
        else:
            (media_dir / f"{scene_index:02d}.mp4").unlink(missing_ok=True)
            shutil.copy(w["img"], media_dir / f"{scene_index:02d}.jpg")
            print(f"      scene {scene_index}: ✅ AI-generated image (score {score}/10)")
        return w["kind"]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def find_replacement_clip(video_id: str, scene_index: int, search_query: str,
                          narration: str, gen_prompt: str = "", min_score: int = 6,
                          reference_frames: list | None = None,
                          used_ref_paths: set | None = None) -> bool:
    """QA-escalation: compete stock + AI image + AI VIDEO + reference frames, apply best ≥ min_score."""
    return compete_and_apply(video_id, scene_index, search_query, narration,
                             gen_prompt=gen_prompt, min_score=min_score,
                             make_video=True,
                             reference_frames=reference_frames,
                             used_ref_paths=used_ref_paths) is not None


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


# ── Pre-assembly content check (Optimization D) ────────────────────────────────

def pre_check_and_fix_media(
    video_id: str,
    media_items: list["MediaItem"],
    match_descriptions: list[str],
    gen_prompts: list[str] | None = None,
    reference_frames: list[str] | None = None,
) -> list["MediaItem"]:
    """
    Pre-assembly quality gate: score each selected media item against its
    narration with ONE Qwen-VL call BEFORE the video is assembled.

    Any scene scoring < 6 gets an immediate re-do (with make_video=True so AI
    video is in the pool), so the video only needs to be assembled ONCE.
    This avoids the QA → content-mismatch → replace → reassemble loop.

    Returns the (possibly updated) media_items list.
    """
    import tempfile
    from agents.vision import vision_available, analyse_images_json
    from config.settings import FFMPEG_BIN
    if not vision_available() or not media_items:
        return media_items

    media_dir = Path(OUTPUT_DIR) / video_id / "media"
    tmp = Path(tempfile.mkdtemp(prefix="precheck_"))
    try:
        # Extract one representative frame per item for Qwen-VL scoring
        frames: list[tuple[int, str]] = []          # (scene_idx, frame_path)
        for i, item in enumerate(media_items):
            fp = tmp / f"f{i:02d}.jpg"
            if item.kind == "clip":
                import subprocess as _sub
                r = _sub.run([
                    FFMPEG_BIN, "-y", "-ss", "1", "-i", item.path,
                    "-frames:v", "1", "-vf", "scale=480:-1", str(fp)
                ], capture_output=True, timeout=15)
                if r.returncode == 0 and fp.exists() and fp.stat().st_size > 1000:
                    frames.append((i, str(fp)))
            else:
                import shutil as _sh
                _sh.copy(item.path, fp)
                frames.append((i, str(fp)))

        if not frames:
            return media_items

        paths  = [p for _, p in frames]
        labels = [
            f'[Scene {idx} — narration: "{match_descriptions[idx] if idx < len(match_descriptions) else ""}"]'
            for idx, _ in frames
        ]
        prompt = (
            "Each image is a SELECTED media item for a China travel/food video scene.\n"
            "For each scene, score how clearly and directly the image SHOWS what the "
            "narration describes (0-10).\n"
            "Score < 6 = wrong footage (wrong subject, unrelated, or too generic).\n"
            "Score ≥ 6 = acceptable (shows the right thing, even if not perfect).\n"
            "Return ONLY JSON array: "
            '[{"scene": <idx>, "score": <0-10>, "ok": <true/false>}, ...]'
        )
        result = analyse_images_json(paths, prompt, labels)
        if not isinstance(result, list):
            return media_items

        bad = [r["scene"] for r in result
               if isinstance(r, dict) and not r.get("ok", True)
               and isinstance(r.get("scene"), int)]

        if bad:
            print(f"  [PreCheck] {len(bad)} scene(s) need better footage: {bad}")
        else:
            print(f"  [PreCheck] ✅ all {len(frames)} scene(s) pass pre-check")

        for idx in bad:
            if idx >= len(media_items):
                continue
            gp   = gen_prompts[idx] if gen_prompts and idx < len(gen_prompts) else ""
            narr = match_descriptions[idx] if idx < len(match_descriptions) else ""
            kind = compete_and_apply(
                video_id, idx, narr, narr, gen_prompt=gp,
                min_score=5, make_video=True, reference_frames=reference_frames,
            )
            if kind:
                clip  = media_dir / f"{idx:02d}.mp4"
                photo = media_dir / f"{idx:02d}.jpg"
                if clip.exists() and clip.stat().st_size > 50_000:
                    media_items[idx] = MediaItem(str(clip), "clip")
                elif photo.exists():
                    media_items[idx] = MediaItem(str(photo), "photo")
                print(f"  [PreCheck] scene {idx}: fixed ({kind})")
            else:
                print(f"  [PreCheck] scene {idx}: no better match found — keeping original")

    except Exception as e:
        print(f"  [PreCheck] skipped ({e})")
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    return media_items


# ── Public API ─────────────────────────────────────────────────────────────────

def download_media(video_id: str, queries: list[str],
                   match_descriptions: list[str] | None = None,
                   gen_prompts: list[str] | None = None,
                   reference_frames: list[str] | None = None) -> list[MediaItem]:
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
    used_ref_paths: set = set()   # dedup: each reference frame used at most once

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

        print(f"  [Media] {i+1}/{len(target)} '{china_q}'…")
        time.sleep(API_CALL_DELAY)

        # ── Preferred: stock + AI-image COMPETE, Qwen picks best ──────────
        # Uses the rich Art-Director gen_prompt so AI generation has full context.
        gp = gen_prompts[i] if gen_prompts and i < len(gen_prompts) else ""
        kind = compete_and_apply(video_id, i, query, judge_q, gen_prompt=gp,
                                 min_score=5, make_video=False, used_ids=used_video_ids,
                                 reference_frames=reference_frames,
                                 used_ref_paths=used_ref_paths)
        if kind == "genvideo":
            # AI-generated video: short + purpose-built for the scene, no analysis needed
            items.append(MediaItem(str(clip_path), "clip", start_sec=0.0))
            continue
        if kind == "clip":
            # Stock clip: find the most relevant segment with Qwen-VL
            start = pick_clip_segment(str(clip_path), judge_q)
            items.append(MediaItem(str(clip_path), "clip", start_sec=start))
            continue
        if kind == "reference":
            # compete_and_apply wrote either .mp4 (real clip) or .jpg (static frame)
            if clip_path.exists() and clip_path.stat().st_size > 50_000:
                items.append(MediaItem(str(clip_path), "clip"))
            elif photo_path.exists():
                items.append(MediaItem(str(photo_path), "photo"))
            continue
        if kind == "image":
            items.append(MediaItem(str(photo_path), "photo"))
            continue

        # ── Fallback (vision unavailable): first stock clip, then photo ────
        candidates = _search_pexels_video_candidates(china_q)
        candidates += _search_pixabay_video_candidates(china_q)
        fresh = [c for c in candidates if c["id"] not in used_video_ids] or candidates
        if fresh:
            data = _download_bytes(fresh[0]["url"])
            if data and len(data) > 50_000:
                clip_path.write_bytes(data)
                used_video_ids.add(fresh[0]["id"])
                print(f"      clip ✓ ({len(data)//1024}KB)")
                # Analyze segment even in fallback — stock clips need it most
                start = pick_clip_segment(str(clip_path), judge_q)
                items.append(MediaItem(str(clip_path), "clip", start_sec=start))
                continue
        photo_url = _search_pexels_photo(china_q) or _search_unsplash_photo(china_q)
        if photo_url:
            data = _download_bytes(photo_url)
            if data and len(data) > 5_000:
                photo_path.write_bytes(data)
                items.append(MediaItem(str(photo_path), "photo"))
                continue
        print("      skipped (no result)")

    clips  = sum(1 for m in items if m.kind == "clip")
    photos = sum(1 for m in items if m.kind == "photo")
    print(f"  [Media] Total: {len(items)} items ({clips} clips, {photos} photos)")
    return items
