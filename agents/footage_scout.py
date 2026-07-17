"""
Footage Scout — automatically find REAL video footage for a Mode 1 topic.

Problem: Mode 1 scenes are mostly won by AI images and stock (few authentic
Chinese visuals on Pexels/Pixabay). Real footage looks far more credible.

Approach: search Bilibili for the topic, download 1-2 short relevant videos
(cached, same cache as Mode 2), sample frame+clip pairs across each video, and
feed them into the EXISTING media competition as reference frames — the judge
already prefers "real reference frame" candidates when they match, and the
winner's sibling .mp4 clip is used instead of the static frame.

Copyright note: excerpts are short (≤6s per scene, sampled from different
sources) and used inside a transformative narrated edit, but short ≠ exempt.
Every scouted source URL is written to sources.json next to the video so
attribution/removal is always possible.

No AI in the selection loop here: search ranking is keyword overlap + duration
filters. (One optional LLM call translates the topic into a Chinese search
query; on failure the raw topic is used.)
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from config.settings import FFMPEG_BIN, FFPROBE_BIN

CACHE_DIR = Path("output/ref_cache")
SOURCES_FILE = Path(__file__).resolve().parent.parent / "config" / "footage_sources.json"
MIN_SRC_SECONDS = 45          # skip shorts/loops
MAX_SRC_SECONDS = 900         # documentaries run longer than vlogs (15 min cap)
TOTAL_FRAMES = 10             # frame budget across all chosen sources
CLIP_SECONDS = 6              # sibling clip length per frame
SCOUT_MAX_H = 1080            # scout clips go into the final video — keep them sharp


def _load_sources() -> dict:
    try:
        return json.loads(SOURCES_FILE.read_text())
    except Exception:
        return {"preferred_uploaders": [], "boost_keywords": [],
                "avoid_keywords": [], "search_suffix": "纪录片", "min_views": 0}


def _zh_query(topic: str) -> str:
    """Short Chinese search query for Bilibili. Falls back to the raw topic."""
    if re.search(r"[一-鿿]", topic):
        return topic
    try:
        from agents.director_agent import _llm_chat
        q = _llm_chat(
            "You translate video topics into short Chinese search queries.",
            f"Topic: {topic}\nReply with ONLY a 2-6 character Chinese search "
            f"query a Bilibili user would type. No punctuation, no explanation.",
            temperature=0.1, max_tokens=20,
        ).strip().splitlines()[0][:20]
        return q or topic
    except Exception:
        return topic


def _bili_search(query: str, n: int = 6) -> list[dict]:
    """Search Bilibili via yt-dlp (bilisearch). Returns entry dicts.
    Full extraction (no --flat-playlist): flat entries carry no title/duration
    for Bilibili, and we need both for filtering + ranking."""
    base = ["yt-dlp", "-J", "--no-warnings", f"bilisearch{n}:{query}"]
    for extra in [[], ["--cookies-from-browser", "chrome"],
                       ["--cookies-from-browser", "safari"]]:
        cmd = base[:1] + extra + base[1:]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode == 0 and r.stdout:
                data = json.loads(r.stdout)
                entries = [e for e in (data.get("entries") or []) if e]
                if entries:          # 412'd entries come back as null — if the
                    return entries   # list is empty, retry with cookies
        except Exception:
            continue
    return []


def _rank_entries(entries: list[dict], terms: list[str]) -> list[dict]:
    """
    Deterministic quality ranking against the curated source config:
      · whitelisted uploader (e.g. CCTV channels)  → dominates everything
      · documentary keywords in title/tags         → boost
      · vlog/mukbang keywords                      → excluded outright
      · below min_views                            → excluded
    """
    cfg = _load_sources()
    uploaders = cfg.get("preferred_uploaders", [])
    boosts    = cfg.get("boost_keywords", [])
    avoids    = cfg.get("avoid_keywords", [])
    min_views = cfg.get("min_views", 0)

    ok = []
    for e in entries:
        dur = e.get("duration") or 0
        if not (MIN_SRC_SECONDS <= dur <= MAX_SRC_SECONDS):
            continue
        views = e.get("view_count") or 0
        if views < min_views:
            continue
        hay = ((e.get("title") or "") + " " + " ".join(e.get("tags") or [])).lower()
        if any(a.lower() in hay for a in avoids):
            continue
        up = (e.get("uploader") or "")
        whitelisted = any(u in up for u in uploaders)
        score = (100 if whitelisted else 0)
        score += 3 * sum(1 for b in boosts if b.lower() in hay)
        score += sum(1 for t in terms if t and t.lower() in hay)
        ok.append((score, views, e, whitelisted))
    ok.sort(key=lambda x: (-x[0], -x[1]))        # quality first, then most viewed
    for score, views, e, wl in ok[:5]:
        print(f"  [Scout]   candidate: “{(e.get('title') or '')[:36]}” "
              f"by {e.get('uploader','?')} — score {score}"
              f"{' ★whitelist' if wl else ''}, {views:,} views")
    return [e for _, _, e, _ in ok]


def _sample_pairs(video_path: str, url_key: str,
                  n_frames: int = 5) -> list[str]:
    """Extract n frame(jpg)+clip(mp4) pairs evenly across [10%, 90%] of the
    video. Cached per source video. Returns jpg paths (mp4 = same stem)."""
    out_dir = CACHE_DIR / f"scout_{url_key}"
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(out_dir.glob("f_*.jpg"))
    if len(existing) >= n_frames:
        return [str(p) for p in existing[:n_frames]]

    try:
        r = subprocess.run([FFPROBE_BIN, "-v", "quiet", "-show_entries",
                            "format=duration", "-of", "csv=p=0", video_path],
                           capture_output=True, text=True, timeout=15)
        dur = float(r.stdout.strip())
    except Exception:
        return []

    lo, hi = dur * 0.10, dur * 0.90
    step = (hi - lo) / max(1, n_frames - 1)
    frames = []
    for i in range(n_frames):
        t = lo + i * step
        jpg = out_dir / f"f_{i:02d}.jpg"
        mp4 = out_dir / f"f_{i:02d}.mp4"
        subprocess.run([FFMPEG_BIN, "-y", "-ss", f"{t:.2f}", "-i", video_path,
                        "-frames:v", "1", "-vf", "scale=960:-1", str(jpg)],
                       capture_output=True, timeout=20)
        if not jpg.exists() or jpg.stat().st_size < 1_000:
            continue
        subprocess.run([FFMPEG_BIN, "-y", "-ss", f"{max(0.0, t - 1.0):.2f}",
                        "-i", video_path, "-t", str(CLIP_SECONDS),
                        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                        "-an", str(mp4)],
                       capture_output=True, timeout=40)
        if mp4.exists() and mp4.stat().st_size < 20_000:
            mp4.unlink(missing_ok=True)
        frames.append(str(jpg))
    return frames


def scout_footage(topic: str, scene_queries: list[str] | None = None,
                  out_dir: Path | str | None = None,
                  n_videos: int = 2) -> list[str]:
    """
    Find real footage for `topic` on Bilibili. Returns reference-frame jpg
    paths (each with a sibling .mp4) for the media competition, or [] on any
    failure — the pipeline then continues with stock + AI as before.
    """
    cfg = _load_sources()
    query = _zh_query(topic)
    suffix = cfg.get("search_suffix", "").strip()
    search_q = f"{query} {suffix}".strip()   # e.g. “成都火锅 纪录片”
    print(f"  [Scout] Searching Bilibili: “{search_q}”…")
    entries = _bili_search(search_q, n=10)
    if not entries:
        print("  [Scout] no search results — continuing without real footage")
        return []

    terms = [w for q in ([topic, query] + (scene_queries or []))
             for w in re.split(r"[\s,]+", q) if len(w) >= 2]
    ranked = _rank_entries(entries, terms)[:n_videos]
    if not ranked:
        print("  [Scout] no quality sources passed the filter — skipping")
        return []

    from agents.video_analyst_agent import _download_video

    # Fewer sources → more frames each (one good documentary carries the video)
    per_video = max(4, TOTAL_FRAMES // len(ranked))

    frames, sources = [], []
    for e in ranked:
        url = (e.get("webpage_url") or e.get("url")
               or f"https://www.bilibili.com/video/{e.get('id')}")
        url_key = hashlib.md5(url.encode()).hexdigest()[:10]
        # Scout keeps its own high-res copy — these clips go into the FINAL
        # video, unlike Mode 2's 480p analysis copy.
        vid_path = CACHE_DIR / f"scout_{url_key}_src.mp4"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not vid_path.exists():
            print(f"  [Scout] Downloading ({SCOUT_MAX_H}p): {e.get('title', url)[:50]}…")
            if not _download_video(url, str(vid_path), max_h=SCOUT_MAX_H):
                print("  [Scout]   download failed — skipping this source")
                continue
        got = _sample_pairs(str(vid_path), url_key, n_frames=per_video)
        frames.extend(got)
        sources.append({"url": url, "title": e.get("title", ""),
                        "uploader": e.get("uploader", ""), "frames": len(got)})
        print(f"  [Scout]   {len(got)} frame+clip pairs from “{e.get('title','')[:40]}”")

    # Attribution trail — every scouted source is recorded next to the video
    if out_dir and sources:
        try:
            p = Path(out_dir) / "sources.json"
            p.write_text(json.dumps(sources, indent=2, ensure_ascii=False))
        except Exception:
            pass

    print(f"  [Scout] ✅ {len(frames)} real-footage candidates ready")
    return frames
