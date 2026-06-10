"""
Video Analyst Agent — Mode 2: understand a reference video, then clip it.

Two-pass design:
  Pass 1 — UNDERSTAND (fast, sparse, 4s interval)
    Builds VideoUnderstanding (summary + ordered steps) so the Director
    can write a grounded script BEFORE media is touched.

  Pass 2 — CLIP (dense, 2s interval, runs after brief is created)
    Builds a full text timeline of what's happening every 2s.
    Text-matches each narration scene to the best video timestamp.
    Extracts real video clips (mp4) from the source — no static frames.

Documentary pipeline (Mode 2):
  analyze_video()  →  VideoUnderstanding  →  Director  →  brief
  extract_clips_for_brief()  →  list[MediaItem]  →  assemble

Why this beats Mode 1:
  A 10-min source video can be "edited" into a 30s Reel that shows exactly
  the moment the maltose syrup is brushed on, exactly the oven close-up, etc.
  The narration is grounded in the real content; the clips are the real footage.
"""
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import FFMPEG_BIN, FFPROBE_BIN

# Pass 1 — understanding (sparse, fast)
UNDERSTAND_INTERVAL = 4.0
# Pass 2 — timeline / clipping (dense, thorough)
TIMELINE_INTERVAL   = 2.0
# Frames per Qwen-VL call
BATCH_SIZE = 5


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class VideoStep:
    """One understood moment / action in the reference video."""
    timestamp: str       # human-readable e.g. "0:08"
    start_sec: float
    action: str          # short label e.g. "Inflating the duck skin"
    detail: str          # 1-2 sentences: what & why


@dataclass
class TimelineFrame:
    """One frame in the dense analysis timeline."""
    sec:         float
    description: str   # what is happening at this moment


@dataclass
class VideoUnderstanding:
    """Structured knowledge extracted from a reference video."""
    url:            str
    topic:          str
    summary:        str
    steps:          list[VideoStep]
    total_duration: float = 0.0
    video_path:     str   = ""
    timeline:       list[TimelineFrame] = field(default_factory=list)

    def to_director_prompt(self) -> str:
        lines = [
            "═══ VIDEO CONTENT ANALYSIS (Mode 2) ═══",
            f"Topic: {self.topic}",
            f"Duration: {self.total_duration:.0f}s",
            "",
            f"Summary: {self.summary}",
            "",
            "Steps observed in the actual video (write your script AROUND these):",
        ]
        for i, step in enumerate(self.steps, 1):
            lines.append(f"  {i}. [{step.timestamp}] {step.action}")
            lines.append(f"     {step.detail}")
        lines += [
            "",
            "DIRECTOR INSTRUCTION: Your narration MUST reveal these specific techniques "
            "in roughly this order. Use the 'insider knowledge' voice — explain the WHY "
            "behind each step. Do NOT invent steps not shown in this video.",
            "═══════════════════════════════════════",
        ]
        return "\n".join(lines)


# ── Download + ffprobe ────────────────────────────────────────────────────────

def _download_video(url: str, out_path: str) -> bool:
    try:
        r = subprocess.run(
            ["yt-dlp",
             "--format",
             "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]"
             "/bestvideo[height<=480]+bestaudio/best[height<=480]/best",
             "--merge-output-format", "mp4",
             "--no-playlist", "--quiet", "--no-warnings",
             "-o", out_path, url],
            timeout=300, capture_output=True,
        )
        return r.returncode == 0 and Path(out_path).exists()
    except Exception as e:
        print(f"  [VideoAnalyst] yt-dlp error: {e}")
        return False


def _get_duration(video_path: str) -> float:
    try:
        r = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _seconds_to_ts(s: float) -> str:
    m = int(s) // 60
    return f"{m}:{int(s) % 60:02d}"


# ── Frame extraction ──────────────────────────────────────────────────────────

def _extract_frames(video_path: str, interval: float,
                    out_dir: Path) -> list[tuple[float, str]]:
    """Extract one frame every `interval` seconds → [(timestamp_sec, path)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [FFMPEG_BIN, "-y", "-i", video_path,
         "-vf", f"fps=1/{interval}",
         "-q:v", "4", str(out_dir / "f_%05d.jpg")],
        capture_output=True, timeout=180,
    )
    frames = []
    if r.returncode == 0:
        for f in sorted(out_dir.glob("f_*.jpg")):
            idx = int(f.stem.split("_")[1]) - 1
            frames.append((round(idx * interval, 1), str(f)))
    return frames


# ── Qwen-VL batch analysis ────────────────────────────────────────────────────

def _analyse_batch(frames: list[tuple[float, str]], topic: str,
                   mode: str = "step") -> list[dict]:
    """
    mode="step"     → {action, detail}      (Pass 1 — understanding)
    mode="timeline" → {description}          (Pass 2 — dense timeline)
    """
    from agents.vision import analyse_images
    image_paths = [f for _, f in frames]
    labels      = [f"t={_seconds_to_ts(ts)}" for ts, _ in frames]

    if mode == "step":
        prompt = (
            f'Video topic: "{topic}"\n\n'
            "For EACH frame, describe:\n"
            "1. action: what step/action is happening (5-8 words)\n"
            "2. detail: what + why in 1-2 sentences\n\n"
            'Reply as JSON array — one object per frame, same order:\n'
            '[{"action":"...","detail":"..."}, ...]\nNo extra text.'
        )
    else:  # timeline
        prompt = (
            f'Video topic: "{topic}"\n\n'
            "For EACH frame, write ONE short sentence (8-15 words) describing "
            "exactly what is visible — the specific action, object, or scene. "
            "Be concrete, not generic.\n\n"
            'Reply as JSON array — one string per frame:\n'
            '["description at t=X", "description at t=Y", ...]\nNo extra text.'
        )

    try:
        raw = analyse_images(image_paths, prompt, labels=labels,
                             temperature=0.2, max_tokens=900)
        if not raw:
            return []
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        results = []
        if mode == "step" and isinstance(data, list):
            for i, item in enumerate(data):
                if i < len(frames):
                    results.append({"start_sec": frames[i][0],
                                    "action": item.get("action", ""),
                                    "detail": item.get("detail", "")})
        elif mode == "timeline" and isinstance(data, list):
            for i, desc in enumerate(data):
                if i < len(frames):
                    results.append({"start_sec": frames[i][0],
                                    "description": str(desc)})
        return results
    except Exception as e:
        print(f"  [VideoAnalyst] batch error ({mode}): {e}")
    return []


# ── Pass 1: understanding ─────────────────────────────────────────────────────

def _synthesise_steps(all_steps: list[dict], topic: str,
                      total_dur: float) -> tuple[str, list[VideoStep]]:
    """Collapse duplicates, keep ≤8 representative steps, generate summary."""
    # Collapse consecutive same-action frames
    collapsed: list[dict] = []
    for step in all_steps:
        if (collapsed and
                step.get("action","")[:15].lower() == collapsed[-1].get("action","")[:15].lower()):
            if len(step.get("detail","")) > len(collapsed[-1].get("detail","")):
                collapsed[-1]["detail"] = step["detail"]
        else:
            collapsed.append(dict(step))

    # Sub-sample to ≤8
    if len(collapsed) > 8:
        n = len(collapsed)
        collapsed = [collapsed[round(i*(n-1)/7)] for i in range(8)]

    steps = [
        VideoStep(timestamp=_seconds_to_ts(s["start_sec"]),
                  start_sec=s["start_sec"],
                  action=s.get("action",""),
                  detail=s.get("detail",""))
        for s in collapsed if s.get("action")
    ]

    # Generate summary via Qwen text
    summary = f"A step-by-step demonstration of {topic}."
    try:
        from agents.director_agent import _llm_chat
        steps_text = "\n".join(
            f"  {i+1}. [{s.timestamp}] {s.action}: {s.detail}"
            for i, s in enumerate(steps)
        )
        raw = _llm_chat(
            system=("Concise video summariser. Given steps from a cooking/craft video, "
                    "write 2-3 sentences describing the overall process and what makes "
                    "it special. Third-person, present tense. No bullet points."),
            user=f"Topic: {topic}\nDuration: {total_dur:.0f}s\n\nSteps:\n{steps_text}\n\nSummary:",
            temperature=0.4, max_tokens=200,
        )
        if raw and len(raw.strip()) > 20:
            summary = raw.strip()
    except Exception:
        pass

    return summary, steps


# ── Pass 2: dense timeline ────────────────────────────────────────────────────

def build_video_timeline(video_path: str, topic: str,
                         interval: float = TIMELINE_INTERVAL,
                         cache_dir: Path | None = None) -> list[TimelineFrame]:
    """
    Extract one frame every `interval` seconds and build a dense text timeline.
    Returns [{sec, description}, ...] covering the entire video.

    Cached per (video_path, interval) so re-runs don't re-process.
    """
    import hashlib
    vid_key   = hashlib.md5(video_path.encode()).hexdigest()[:8]
    frame_dir = (cache_dir or Path("output/ref_cache")) / f"tl_{vid_key}_{interval:.0f}s"
    cache_file = frame_dir.parent / f"tl_{vid_key}_{interval:.0f}s.json"

    if cache_file.exists():
        print(f"  [VideoAnalyst] Timeline cache hit → {cache_file.name}")
        data = json.loads(cache_file.read_text())
        return [TimelineFrame(**d) for d in data]

    dur = _get_duration(video_path)
    print(f"  [VideoAnalyst] Building dense timeline "
          f"({dur:.0f}s video, 1 frame/{interval:.0f}s)…")

    frames = _extract_frames(video_path, interval, frame_dir)
    n_frames = len(frames)
    if not frames:
        return []

    batches  = [frames[i:i+BATCH_SIZE] for i in range(0, n_frames, BATCH_SIZE)]
    timeline = []
    print(f"  [VideoAnalyst] {n_frames} frames → {len(batches)} Qwen-VL calls…")

    for b_idx, batch in enumerate(batches):
        t0 = _seconds_to_ts(batch[0][0])
        t1 = _seconds_to_ts(batch[-1][0])
        print(f"    [{b_idx+1}/{len(batches)}] t={t0}–{t1}", end="\r")
        results = _analyse_batch(batch, topic, mode="timeline")
        for r in results:
            timeline.append(TimelineFrame(sec=r["start_sec"],
                                          description=r.get("description","")))
    print()

    # Cache to disk
    cache_file.write_text(
        json.dumps([{"sec": f.sec, "description": f.description}
                    for f in timeline], ensure_ascii=False, indent=2)
    )
    print(f"  [VideoAnalyst] Timeline saved → {cache_file.name}")
    return timeline


# ── Scene-to-segment matching ─────────────────────────────────────────────────

def match_scenes_to_segments(
    timeline: list[TimelineFrame],
    narrations: list[str],
    clip_duration: float = 5.0,
    video_duration: float = 0.0,
) -> list[float | None]:
    """
    Use Qwen text to match each narration scene to the best start time
    in the dense timeline.

    Returns a list of start_sec (float) per scene, or None if no good match.
    Matching is text-only (fast, no extra image calls).
    """
    from agents.director_agent import _llm_chat

    # Build a compact timeline string (every 2 entries to stay under token limit)
    step = max(1, len(timeline) // 60)   # cap at ~60 entries in the prompt
    tl_lines = [
        f"[{_seconds_to_ts(f.sec)}={f.sec:.0f}s] {f.description}"
        for f in timeline[::step]
    ]
    timeline_text = "\n".join(tl_lines)

    scenes_text = "\n".join(
        f"Scene {i}: \"{n}\"" for i, n in enumerate(narrations)
    )

    prompt = (
        f"You are a documentary video editor.\n\n"
        f"VIDEO TIMELINE (every ~{step*TIMELINE_INTERVAL:.0f}s):\n"
        f"{timeline_text}\n\n"
        f"NARRATION SCENES to match:\n"
        f"{scenes_text}\n\n"
        f"For each scene, find the video timestamp (in seconds) whose content "
        f"BEST ILLUSTRATES what the narration is describing. "
        f"The clip will be {clip_duration:.0f}s long starting from that point.\n\n"
        f"Rules:\n"
        f"- Each scene should get a DIFFERENT start time if possible (no two scenes "
        f"  pointing to the exact same moment)\n"
        f"- If a scene's narration matches nothing specific, set start_sec to null\n\n"
        f"Reply ONLY as JSON:\n"
        f'{{"matches":[{{"scene":0,"start_sec":4}},{{"scene":1,"start_sec":62}},...]}}'
    )

    try:
        raw = _llm_chat(
            system="You are a precise video editor. Reply only with valid JSON.",
            user=prompt,
            temperature=0.2,
            max_tokens=400,
        )
        if raw:
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw.strip())
            matches = data.get("matches", [])
            result = [None] * len(narrations)
            for m in matches:
                idx = m.get("scene")
                sec = m.get("start_sec")
                if idx is not None and 0 <= idx < len(narrations):
                    if sec is not None:
                        # Clamp so clip doesn't exceed video end
                        max_start = max(0.0, video_duration - clip_duration)
                        result[idx] = min(float(sec), max_start)
            print(f"  [VideoAnalyst] Scene matches:")
            for i, s in enumerate(result):
                ts = _seconds_to_ts(s) if s is not None else "no match"
                print(f"    Scene {i}: t={ts}  \"{narrations[i][:50]}\"")
            return result
    except Exception as e:
        print(f"  [VideoAnalyst] Scene matching failed: {e}")
    return [None] * len(narrations)


# ── Clip extraction ───────────────────────────────────────────────────────────

def extract_scene_clips(
    video_path: str,
    start_times: list[float | None],
    scene_durations: list[float],
    out_dir: Path,
) -> list:   # list[MediaItem | None]
    """
    Extract one video clip per scene from the source video.
    Returns a list where None = no match (caller should use stock fallback).

    Clips are written to out_dir/{i:02d}.mp4.
    """
    from agents.media_agent import MediaItem
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for i, (start, dur) in enumerate(zip(start_times, scene_durations)):
        clip_path = out_dir / f"{i:02d}.mp4"

        if start is None:
            print(f"    scene {i}: no match → stock fallback")
            results.append(None)
            continue

        # Pad the clip slightly longer than the scene duration (QA may need it)
        extract_dur = max(dur + 1.0, 3.0)

        r = subprocess.run(
            [FFMPEG_BIN, "-y",
             "-ss", f"{start:.2f}", "-i", video_path,
             "-t", f"{extract_dur:.2f}",
             "-c:v", "libx264", "-crf", "20", "-preset", "fast",
             "-c:a", "aac",
             str(clip_path)],
            capture_output=True, timeout=60,
        )
        if r.returncode == 0 and clip_path.exists() and clip_path.stat().st_size > 10_000:
            print(f"    scene {i}: t={_seconds_to_ts(start)} → {clip_path.name} ✓")
            results.append(MediaItem(str(clip_path), "clip", start_sec=0.0))
        else:
            print(f"    scene {i}: extraction failed → stock fallback")
            results.append(None)

    return results


# ── Public API: Pass 1 ────────────────────────────────────────────────────────

def analyze_video(url: str, topic: str,
                  sample_interval: float = UNDERSTAND_INTERVAL,
                  cache_dir: str = "output/ref_cache") -> VideoUnderstanding:
    """
    Pass 1 — download and understand a reference video.

    Returns VideoUnderstanding ready for director_agent.create_brief().
    The understanding uses a sparse frame sample (every 4s by default) to keep
    this pass fast — it's just for the Director to write a grounded script.
    """
    from agents.vision import vision_available

    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    import hashlib
    url_key   = hashlib.md5(url.encode()).hexdigest()[:10]
    vid_path  = str(cache / f"analyst_{url_key}.mp4")
    frame_dir = cache / f"analyst_{url_key}_frames"

    # Download
    if not Path(vid_path).exists():
        print(f"  [VideoAnalyst] Downloading: {url}")
        ok = _download_video(url, vid_path)
        if not ok:
            print("  [VideoAnalyst] ❌ Download failed")
            return VideoUnderstanding(url=url, topic=topic,
                                      summary=f"Download failed: {topic}",
                                      steps=[])
        print(f"  [VideoAnalyst] ✅ Downloaded ({Path(vid_path).stat().st_size//1024//1024}MB)")
    else:
        print(f"  [VideoAnalyst] Cache hit → {Path(vid_path).name}")

    total_dur = _get_duration(vid_path)
    print(f"  [VideoAnalyst] Duration: {total_dur:.0f}s ({total_dur/60:.1f} min)")

    # Extract understanding frames
    if not frame_dir.exists() or not any(frame_dir.glob("f_*.jpg")):
        frames = _extract_frames(vid_path, sample_interval, frame_dir)
        print(f"  [VideoAnalyst] {len(frames)} frames @ {sample_interval}s intervals")
    else:
        frames = sorted(
            [(round((int(f.stem.split("_")[1]) - 1) * sample_interval, 1), str(f))
             for f in sorted(frame_dir.glob("f_*.jpg"))],
        )
        print(f"  [VideoAnalyst] {len(frames)} frames from cache")

    if not frames:
        return VideoUnderstanding(url=url, topic=topic,
                                  summary="No frames extracted.",
                                  steps=[], total_duration=total_dur,
                                  video_path=vid_path)

    if not vision_available():
        print("  [VideoAnalyst] ⚠️  Vision unavailable — stub understanding")
        return VideoUnderstanding(url=url, topic=topic,
                                  summary=f"Vision unavailable; video at {vid_path}",
                                  steps=[], total_duration=total_dur,
                                  video_path=vid_path)

    # Analyse in batches
    all_steps: list[dict] = []
    batches = [frames[i:i+BATCH_SIZE] for i in range(0, len(frames), BATCH_SIZE)]
    print(f"  [VideoAnalyst] Analysing {len(frames)} frames in {len(batches)} batches…")
    for b_idx, batch in enumerate(batches):
        print(f"    batch {b_idx+1}/{len(batches)}: "
              f"{_seconds_to_ts(batch[0][0])}–{_seconds_to_ts(batch[-1][0])}")
        all_steps.extend(_analyse_batch(batch, topic, mode="step"))

    summary, steps = _synthesise_steps(all_steps, topic, total_dur)

    print(f"  [VideoAnalyst] ✅ {len(steps)} key steps understood:")
    for s in steps:
        print(f"    [{s.timestamp}] {s.action}")

    return VideoUnderstanding(url=url, topic=topic, summary=summary, steps=steps,
                              total_duration=total_dur, video_path=vid_path)


# ── Public API: Pass 2 ────────────────────────────────────────────────────────

def extract_clips_for_brief(
    video_understanding: VideoUnderstanding,
    narrations: list[str],
    scene_durations: list[float],
    out_dir: Path,
    timeline_interval: float = TIMELINE_INTERVAL,
) -> list:   # list[MediaItem | None]
    """
    Pass 2 — build a dense timeline and extract one video clip per scene.

    Runs AFTER the Director has written the brief (needs narrations + durations).
    Returns a list of MediaItem (clip) or None per scene.
    None entries signal "use stock fallback" for that scene.

    The dense timeline (every 2s) is cached so re-runs are instant.
    """
    video_path = video_understanding.video_path
    if not video_path or not Path(video_path).exists():
        print("  [VideoAnalyst] No video path — skipping clip extraction")
        return [None] * len(narrations)

    total_dur = video_understanding.total_duration or _get_duration(video_path)

    # Build (or load cached) dense timeline
    cache_dir = Path(video_path).parent
    timeline  = build_video_timeline(video_path, video_understanding.topic,
                                     interval=timeline_interval,
                                     cache_dir=cache_dir)
    if not timeline:
        print("  [VideoAnalyst] Empty timeline — stock fallback for all scenes")
        return [None] * len(narrations)

    # Match each narration to a timestamp
    avg_dur = sum(scene_durations) / max(len(scene_durations), 1)
    start_times = match_scenes_to_segments(
        timeline, narrations,
        clip_duration=avg_dur,
        video_duration=total_dur,
    )

    # Extract clips
    print(f"  [VideoAnalyst] Extracting {len(narrations)} clips from source video…")
    clips = extract_scene_clips(video_path, start_times, scene_durations, out_dir)

    matched = sum(1 for c in clips if c is not None)
    print(f"  [VideoAnalyst] ✅ {matched}/{len(narrations)} scenes matched to source video")
    return clips
