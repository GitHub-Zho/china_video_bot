"""
Video Analyst Agent — Mode 2: understand a reference video, then clip it.

Single-pass design:
  ONE analysis pass (dense, 2s interval by default)
    · Extracts frames once, sends to Qwen-VL with rich "step" prompts
    · Builds VideoUnderstanding (summary + key steps) for the Director
    · Stores the full frame timeline so extract_clips_for_brief() can
      match narrations to timestamps WITHOUT a second video read

Documentary pipeline (Mode 2):
  analyze_video()  →  VideoUnderstanding (steps + full timeline)
       ↓
  Director  →  brief  →  TTS  →  scene_durations
       ↓
  extract_clips_for_brief()  →  list[MediaItem]  (uses cached timeline)
       ↓
  assemble_video()

Why this beats the old two-pass approach:
  · Frame extraction: 1× (was 2×)
  · Qwen-VL calls: ~60 for a 10-min video (was ~90)
  · Same data serves both Director (understanding) and clip matching (timeline)
  · Cache covers both uses — re-runs are instant either way

Why Mode 2 beats Mode 1 for how-to content:
  A 10-min source video is "edited" into a 30s Reel that shows exactly
  the moment the maltose syrup is brushed on, exactly the oven close-up, etc.
  The narration is grounded in real content; the clips are real footage.
"""
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import FFMPEG_BIN, FFPROBE_BIN

# Single sample interval — used for both understanding and clip matching
SAMPLE_INTERVAL = 2.0
BATCH_SIZE = 5

# Legacy alias so old code referencing UNDERSTAND_INTERVAL / TIMELINE_INTERVAL still imports
UNDERSTAND_INTERVAL = SAMPLE_INTERVAL
TIMELINE_INTERVAL   = SAMPLE_INTERVAL


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
    """One entry in the dense analysis timeline."""
    sec:         float
    description: str   # "{action}. {detail[:80]}" — concrete enough for clip matching


@dataclass
class VideoUnderstanding:
    """Structured knowledge extracted from a reference video (single-pass)."""
    url:            str
    topic:          str
    summary:        str
    steps:          list[VideoStep]        # ≤8 key steps for the Director prompt
    total_duration: float = 0.0
    video_path:     str   = ""
    timeline:       list[TimelineFrame] = field(default_factory=list)
    # ↑ full dense timeline — every frame's description, used for clip matching
    #   populated by analyze_video(); extract_clips_for_brief() uses it directly

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

def _analyse_batch(frames: list[tuple[float, str]], topic: str) -> list[dict]:
    """
    Analyse a batch of frames with Qwen-VL.
    Returns [{start_sec, action, detail}, ...] — one entry per frame.
    Rich "step" descriptions serve both Director understanding and clip matching.
    """
    from agents.vision import analyse_images
    image_paths = [f for _, f in frames]
    labels      = [f"t={_seconds_to_ts(ts)}" for ts, _ in frames]

    prompt = (
        f'Video topic: "{topic}"\n\n'
        "For EACH frame, describe:\n"
        "1. action: what step/action is happening (5-8 words)\n"
        "2. detail: what + why in 1-2 sentences\n\n"
        'Reply as JSON array — one object per frame, same order:\n'
        '[{"action":"...","detail":"..."}, ...]\nNo extra text.'
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
        if isinstance(data, list):
            for i, item in enumerate(data):
                if i < len(frames):
                    results.append({
                        "start_sec": frames[i][0],
                        "action": item.get("action", ""),
                        "detail": item.get("detail", ""),
                    })
        return results
    except Exception as e:
        print(f"  [VideoAnalyst] batch error: {e}")
    return []


# ── Synthesise key steps for Director ────────────────────────────────────────

def _synthesise_steps(all_steps: list[dict], topic: str,
                      total_dur: float) -> tuple[str, list[VideoStep]]:
    """
    Collapse duplicate/consecutive frames, keep ≤8 representative key steps,
    and generate a 2-3 sentence summary for the Director prompt.
    """
    # Collapse consecutive frames with the same action prefix
    collapsed: list[dict] = []
    for step in all_steps:
        if (collapsed and
                step.get("action", "")[:15].lower() == collapsed[-1].get("action", "")[:15].lower()):
            if len(step.get("detail", "")) > len(collapsed[-1].get("detail", "")):
                collapsed[-1]["detail"] = step["detail"]
        else:
            collapsed.append(dict(step))

    # Sub-sample to ≤8 evenly spaced key steps
    if len(collapsed) > 8:
        n = len(collapsed)
        collapsed = [collapsed[round(i * (n - 1) / 7)] for i in range(8)]

    steps = [
        VideoStep(timestamp=_seconds_to_ts(s["start_sec"]),
                  start_sec=s["start_sec"],
                  action=s.get("action", ""),
                  detail=s.get("detail", ""))
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


# ── Scene-to-segment matching ─────────────────────────────────────────────────

def match_scenes_to_segments(
    timeline: list[TimelineFrame],
    narrations: list[str],
    clip_duration: float = 5.0,
    video_duration: float = 0.0,
) -> list[float | None]:
    """
    Use Qwen text to match each narration scene to the best start time
    in the timeline. Text-only (fast, no extra image calls).

    Returns list of start_sec per scene (None = no good match → stock fallback).
    """
    from agents.director_agent import _llm_chat

    # Build compact timeline string (cap at ~60 entries to stay under token limit)
    step = max(1, len(timeline) // 60)
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
        f"VIDEO TIMELINE (every ~{step * SAMPLE_INTERVAL:.0f}s):\n"
        f"{timeline_text}\n\n"
        f"NARRATION SCENES to match:\n"
        f"{scenes_text}\n\n"
        f"For each scene, find the video timestamp (in seconds) whose content "
        f"BEST ILLUSTRATES what the narration is describing. "
        f"The clip will be {clip_duration:.0f}s long starting from that point.\n\n"
        f"Rules:\n"
        f"- Each scene should get a DIFFERENT start time if possible\n"
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
    Returns list where None = no match (caller uses stock fallback).
    Clips written to out_dir/{i:02d}.mp4.
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


# ── Public API: analyze_video (single pass) ───────────────────────────────────

def analyze_video(url: str, topic: str,
                  sample_interval: float = SAMPLE_INTERVAL,
                  cache_dir: str = "output/ref_cache") -> VideoUnderstanding:
    """
    Download and fully analyse a reference video in ONE dense pass.

    · Extracts one frame every sample_interval seconds (default 2s)
    · Qwen-VL reads every frame with rich "step" prompts (action + detail)
    · Produces VideoUnderstanding with:
        .steps    — ≤8 condensed key steps for the Director prompt
        .timeline — full dense list of TimelineFrame for clip matching
    · The timeline is cached to JSON so extract_clips_for_brief() is instant
      on re-runs (no second download or frame extraction needed)

    Args:
        url:             Bilibili/YouTube URL (anything yt-dlp supports)
        topic:           e.g. "Beijing Roast Duck preparation"
        sample_interval: seconds between sampled frames (default 2.0)
        cache_dir:       directory for video download + frame cache

    Returns: VideoUnderstanding ready for director_agent.create_brief()
    """
    from agents.vision import vision_available
    import hashlib

    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    url_key   = hashlib.md5(url.encode()).hexdigest()[:10]
    vid_path  = str(cache / f"analyst_{url_key}.mp4")
    frame_dir = cache / f"analyst_{url_key}_frames_{sample_interval:.0f}s"
    cache_file = cache / f"analyst_{url_key}_timeline_{sample_interval:.0f}s.json"

    # ── Download ──────────────────────────────────────────────────────────────
    if not Path(vid_path).exists():
        print(f"  [VideoAnalyst] Downloading: {url}")
        ok = _download_video(url, vid_path)
        if not ok:
            print("  [VideoAnalyst] ❌ Download failed")
            return VideoUnderstanding(url=url, topic=topic,
                                      summary=f"Download failed: {topic}",
                                      steps=[])
        print(f"  [VideoAnalyst] ✅ Downloaded "
              f"({Path(vid_path).stat().st_size // 1024 // 1024}MB)")
    else:
        print(f"  [VideoAnalyst] Cache hit → {Path(vid_path).name}")

    total_dur = _get_duration(vid_path)
    print(f"  [VideoAnalyst] Duration: {total_dur:.0f}s ({total_dur / 60:.1f} min)")

    # ── Load or build timeline from cache ─────────────────────────────────────
    all_steps: list[dict] = []

    if cache_file.exists():
        print(f"  [VideoAnalyst] Timeline cache hit → {cache_file.name}")
        cached = json.loads(cache_file.read_text())
        all_steps = cached  # [{start_sec, action, detail}, ...]
    else:
        if not vision_available():
            print("  [VideoAnalyst] ⚠️  Vision unavailable — stub understanding")
            return VideoUnderstanding(url=url, topic=topic,
                                      summary=f"Vision unavailable; video at {vid_path}",
                                      steps=[], total_duration=total_dur,
                                      video_path=vid_path)

        # Extract frames (reuse if already on disk from a previous partial run)
        if not frame_dir.exists() or not any(frame_dir.glob("f_*.jpg")):
            frames = _extract_frames(vid_path, sample_interval, frame_dir)
            print(f"  [VideoAnalyst] {len(frames)} frames @ {sample_interval}s intervals")
        else:
            frames = sorted(
                [(round((int(f.stem.split("_")[1]) - 1) * sample_interval, 1), str(f))
                 for f in sorted(frame_dir.glob("f_*.jpg"))],
            )
            print(f"  [VideoAnalyst] {len(frames)} frames from frame cache")

        if not frames:
            return VideoUnderstanding(url=url, topic=topic,
                                      summary="No frames extracted.",
                                      steps=[], total_duration=total_dur,
                                      video_path=vid_path)

        # Analyse all frames (single pass, "step" mode — rich descriptions)
        batches = [frames[i:i + BATCH_SIZE] for i in range(0, len(frames), BATCH_SIZE)]
        print(f"  [VideoAnalyst] Analysing {len(frames)} frames in "
              f"{len(batches)} Qwen-VL batches…")

        for b_idx, batch in enumerate(batches):
            t0 = _seconds_to_ts(batch[0][0])
            t1 = _seconds_to_ts(batch[-1][0])
            print(f"    [{b_idx + 1}/{len(batches)}] t={t0}–{t1}", end="\r")
            all_steps.extend(_analyse_batch(batch, topic))
        print()

        # Cache raw step data to disk
        cache_file.write_text(
            json.dumps(all_steps, ensure_ascii=False, indent=2)
        )
        print(f"  [VideoAnalyst] ✅ Timeline cached → {cache_file.name}")

    # ── Build full dense timeline (for clip matching) ──────────────────────────
    full_timeline = [
        TimelineFrame(
            sec=s["start_sec"],
            description=f"{s.get('action', '')}. {s.get('detail', '')[:80]}".strip(". ")
        )
        for s in all_steps if s.get("action")
    ]

    # ── Condense to ≤8 key steps (for Director prompt) ────────────────────────
    summary, key_steps = _synthesise_steps(all_steps, topic, total_dur)

    print(f"  [VideoAnalyst] ✅ {len(key_steps)} key steps  |  "
          f"{len(full_timeline)} timeline entries")
    for s in key_steps:
        print(f"    [{s.timestamp}] {s.action}")

    return VideoUnderstanding(
        url=url, topic=topic, summary=summary, steps=key_steps,
        total_duration=total_dur, video_path=vid_path,
        timeline=full_timeline,
    )


# ── Public API: extract_clips_for_brief ──────────────────────────────────────

def extract_clips_for_brief(
    video_understanding: VideoUnderstanding,
    narrations: list[str],
    scene_durations: list[float],
    out_dir: Path,
    timeline_interval: float = SAMPLE_INTERVAL,  # kept for API compatibility; unused
) -> list:   # list[MediaItem | None]
    """
    Match narrations to the pre-built timeline and extract real mp4 clips.

    This is the clip-extraction half of Mode 2. It runs AFTER TTS (needs
    scene_durations). The video_understanding.timeline was populated by
    analyze_video() — no second video read or frame extraction needed.

    Returns list of MediaItem (clip) or None per scene.
    None entries → use stock footage fallback for that scene.
    """
    video_path = video_understanding.video_path
    if not video_path or not Path(video_path).exists():
        print("  [VideoAnalyst] No video path — skipping clip extraction")
        return [None] * len(narrations)

    timeline = video_understanding.timeline
    if not timeline:
        print("  [VideoAnalyst] Empty timeline — stock fallback for all scenes")
        return [None] * len(narrations)

    total_dur = video_understanding.total_duration or _get_duration(video_path)

    # Match narrations to timestamps (text-only Qwen call, fast)
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


# ── Legacy: build_video_timeline (kept for import compatibility) ──────────────

def build_video_timeline(video_path: str, topic: str,
                         interval: float = SAMPLE_INTERVAL,
                         cache_dir: Path | None = None) -> list[TimelineFrame]:
    """
    Deprecated: previously a separate Pass 2 function.
    Now a thin wrapper — rebuilds a timeline from cached step data if present,
    or falls back to re-extracting frames.

    Prefer analyze_video() which populates VideoUnderstanding.timeline.
    """
    import hashlib
    vid_key    = hashlib.md5(video_path.encode()).hexdigest()[:8]
    cache_root = cache_dir or Path("output/ref_cache")
    cache_file = cache_root / f"tl_{vid_key}_{interval:.0f}s.json"

    if cache_file.exists():
        print(f"  [VideoAnalyst] Legacy timeline cache hit → {cache_file.name}")
        data = json.loads(cache_file.read_text())
        # Support both old format (sec+description) and new (start_sec+action+detail)
        result = []
        for d in data:
            if "description" in d:
                result.append(TimelineFrame(**d))
            else:
                result.append(TimelineFrame(
                    sec=d["start_sec"],
                    description=f"{d.get('action', '')}. {d.get('detail', '')[:80]}".strip(". ")
                ))
        return result

    # Full re-extraction (shouldn't be needed in normal flow)
    dur = _get_duration(video_path)
    print(f"  [VideoAnalyst] Legacy build_video_timeline "
          f"({dur:.0f}s, 1 frame/{interval:.0f}s)…")
    frame_dir = (cache_root / f"tl_{vid_key}_{interval:.0f}s")
    frames    = _extract_frames(video_path, interval, frame_dir)
    if not frames:
        return []

    batches  = [frames[i:i + BATCH_SIZE] for i in range(0, len(frames), BATCH_SIZE)]
    timeline = []
    for batch in batches:
        for r in _analyse_batch(batch, "video content"):
            timeline.append(TimelineFrame(
                sec=r["start_sec"],
                description=f"{r.get('action', '')}. {r.get('detail', '')[:80]}".strip(". ")
            ))

    cache_file.write_text(
        json.dumps([{"sec": f.sec, "description": f.description}
                    for f in timeline], ensure_ascii=False, indent=2)
    )
    return timeline
