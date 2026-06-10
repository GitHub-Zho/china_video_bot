"""
Video Analyst Agent — Mode 2: understand a reference video before writing the script.

Given a URL (Bilibili / YouTube / any yt-dlp source) + a topic:
  1. Download the video with yt-dlp (480p, fast)
  2. Extract one frame every SAMPLE_INTERVAL seconds
  3. Send frames in batches of BATCH_SIZE to Qwen-VL → per-frame descriptions
  4. Synthesise all descriptions into a VideoUnderstanding (summary + ordered steps)

The VideoUnderstanding is passed to the Director, which then writes narration that is
grounded in the actual video content rather than generic topic knowledge.

Mode 1 (existing): topic → Director freely invents scenes
Mode 2 (new):      video → Analyst extracts steps → Director writes from real content

Why quality is higher in Mode 2:
  Mode 1 knows "Beijing roast duck".
  Mode 2 knows "at 0:03 the chef inflates the duck with a pump to separate skin
  from fat; at 0:34 hot water tightens the skin; at 1:00 maltose syrup is brushed
  on for that mahogany glaze; at 2:10 it roasts slowly in a wood-fired oven" — the
  narration has texture, specificity, and teaching value that Mode 1 cannot fake.

Usage:
  from agents.video_analyst_agent import analyze_video
  vu = analyze_video("https://www.bilibili.com/video/BV1EY4y1B7Mc/",
                     topic="Beijing Roast Duck preparation")
  # then pass vu to create_brief(..., video_understanding=vu)
"""
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import FFMPEG_BIN, FFPROBE_BIN

# One frame every N seconds — enough to understand pacing without flooding Qwen-VL
SAMPLE_INTERVAL = 4.0
# How many frames per Qwen-VL call (DashScope handles 5-6 comfortably)
BATCH_SIZE = 5


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class VideoStep:
    """One understood moment / action in the reference video."""
    timestamp: str       # human-readable, e.g. "0:08"
    start_sec: float     # seconds from video start
    action: str          # short label, e.g. "Inflating the duck skin"
    detail: str          # 1-2 sentences explaining what & why


@dataclass
class VideoUnderstanding:
    """Structured knowledge extracted from a reference video."""
    url:            str
    topic:          str
    summary:        str               # 2-3 sentence overview for Director context
    steps:          list[VideoStep]   # ordered process steps
    total_duration: float = 0.0
    video_path:     str = ""          # local path (for reference_agent to re-use)

    def to_director_prompt(self) -> str:
        """
        Format as a Director-readable block that gets injected into the user
        prompt so the LLM writes from actual video content.
        """
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
            "behind each step (e.g. why inflate? to separate fat from skin so it crisps "
            "evenly). Do NOT invent steps not shown in this video.",
            "═══════════════════════════════════════",
        ]
        return "\n".join(lines)


# ── Download ──────────────────────────────────────────────────────────────────

def _download_video(url: str, out_path: str) -> bool:
    """Download video to out_path using yt-dlp (480p max, fast)."""
    try:
        r = subprocess.run(
            [
                "yt-dlp",
                "--format", "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]"
                            "/bestvideo[height<=480]+bestaudio"
                            "/best[height<=480]/best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "--quiet",
                "--no-warnings",
                "-o", out_path,
                url,
            ],
            timeout=300,
            capture_output=True,
        )
        return r.returncode == 0 and Path(out_path).exists()
    except Exception as e:
        print(f"  [VideoAnalyst] yt-dlp error: {e}")
        return False


def _get_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    try:
        r = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ── Frame extraction ──────────────────────────────────────────────────────────

def _extract_frames(video_path: str, interval: float,
                    out_dir: Path) -> list[tuple[float, str]]:
    """
    Extract one frame every `interval` seconds.
    Returns list of (timestamp_sec, frame_path).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [FFMPEG_BIN, "-y", "-i", video_path,
         "-vf", f"fps=1/{interval}",
         "-q:v", "3",
         str(out_dir / "frame_%04d.jpg")],
        capture_output=True, timeout=120,
    )
    frames = []
    if r.returncode == 0:
        for f in sorted(out_dir.glob("frame_*.jpg")):
            idx = int(f.stem.split("_")[1]) - 1   # 0-based
            ts  = idx * interval
            frames.append((ts, str(f)))
    return frames


# ── Qwen-VL frame analysis ────────────────────────────────────────────────────

def _seconds_to_ts(s: float) -> str:
    m = int(s) // 60
    sec = int(s) % 60
    return f"{m}:{sec:02d}"


def _analyse_batch(frames: list[tuple[float, str]], topic: str) -> list[dict]:
    """
    Send a batch of frames to Qwen-VL.
    Returns list of {"start_sec": float, "action": str, "detail": str}.
    """
    from agents.vision import analyse_images

    image_paths = [f for _, f in frames]
    labels      = [f"Frame at {_seconds_to_ts(ts)}" for ts, _ in frames]

    prompt = (
        f"These frames are from a video about: \"{topic}\".\n\n"
        f"For EACH frame (in order), describe:\n"
        f"1. What action or step is happening (5-8 words, e.g. 'Inflating duck skin with pump')\n"
        f"2. A detail sentence explaining WHAT is happening and WHY (1-2 sentences)\n\n"
        f"Reply as a JSON array — one object per frame:\n"
        f'[{{"action":"...","detail":"..."}}, ...]\n'
        f"Preserve the same order as the frames. No extra text."
    )

    try:
        raw = analyse_images(image_paths, prompt, labels=labels,
                             temperature=0.2, max_tokens=800)
        if not raw:
            return []
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        if isinstance(data, list):
            results = []
            for i, item in enumerate(data):
                if i < len(frames):
                    results.append({
                        "start_sec": frames[i][0],
                        "action":    item.get("action", ""),
                        "detail":    item.get("detail", ""),
                    })
            return results
    except Exception as e:
        print(f"  [VideoAnalyst] batch analysis error: {e}")
    return []


def _synthesise_steps(all_steps: list[dict], topic: str,
                      total_dur: float) -> tuple[str, list[VideoStep]]:
    """
    Merge per-frame descriptions into meaningful process steps and write a summary.
    Adjacent frames showing the same action are collapsed.
    Returns (summary, [VideoStep]).
    """
    from agents.vision import analyse_images

    # ── Collapse consecutive duplicate actions ────────────────────────────────
    collapsed: list[dict] = []
    for step in all_steps:
        if (collapsed and
                step["action"].lower()[:15] == collapsed[-1]["action"].lower()[:15]):
            # Same action — extend, keep the richer detail
            if len(step["detail"]) > len(collapsed[-1]["detail"]):
                collapsed[-1]["detail"] = step["detail"]
        else:
            collapsed.append(dict(step))

    # Keep at most 8 meaningful steps (avoid flooding the Director prompt)
    if len(collapsed) > 8:
        # Evenly sample to 8 representative steps
        step_n  = len(collapsed)
        indices = [round(i * (step_n - 1) / 7) for i in range(8)]
        collapsed = [collapsed[i] for i in indices]

    # ── Build VideoStep objects ───────────────────────────────────────────────
    steps = [
        VideoStep(
            timestamp=_seconds_to_ts(s["start_sec"]),
            start_sec=s["start_sec"],
            action=s["action"],
            detail=s["detail"],
        )
        for s in collapsed
        if s["action"]
    ]

    # ── Generate summary with a second Qwen call (text-only) ─────────────────
    summary = f"A step-by-step demonstration of {topic}."
    try:
        from agents.director_agent import _llm_chat  # reuse the Qwen text client
        steps_text = "\n".join(
            f"  {i+1}. [{s.timestamp}] {s.action}: {s.detail}"
            for i, s in enumerate(steps)
        )
        raw = _llm_chat(
            system=(
                "You are a concise video summariser. "
                "Given a list of steps from a cooking/craft video, write 2-3 sentences "
                "summarising the overall process and what makes it special. "
                "Write in third-person, present tense. No bullet points."
            ),
            user=(
                f"Topic: {topic}\nTotal duration: {total_dur:.0f}s\n\n"
                f"Steps:\n{steps_text}\n\nWrite the 2-3 sentence summary now."
            ),
            temperature=0.4,
            max_tokens=200,
        )
        if raw and len(raw.strip()) > 20:
            summary = raw.strip()
    except Exception:
        pass

    return summary, steps


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_video(url: str, topic: str,
                  sample_interval: float = SAMPLE_INTERVAL,
                  cache_dir: str = "output/ref_cache") -> VideoUnderstanding:
    """
    Download a video, extract frames, and use Qwen-VL to understand its content.

    url:             any yt-dlp-compatible URL (Bilibili, YouTube, etc.)
    topic:           what the video is about (used to guide Qwen-VL analysis)
    sample_interval: seconds between sampled frames (default 4s)
    cache_dir:       where to keep the downloaded video + frames

    Returns a VideoUnderstanding ready to be passed to director_agent.create_brief().
    """
    from agents.vision import vision_available

    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    # Stable filename from URL hash
    import hashlib
    url_key   = hashlib.md5(url.encode()).hexdigest()[:10]
    vid_path  = str(cache / f"analyst_{url_key}.mp4")
    frame_dir = cache / f"analyst_{url_key}_frames"

    # ── 1. Download ──────────────────────────────────────────────────────────
    if not Path(vid_path).exists():
        print(f"  [VideoAnalyst] Downloading: {url}")
        ok = _download_video(url, vid_path)
        if not ok:
            print("  [VideoAnalyst] ❌ Download failed")
            return VideoUnderstanding(url=url, topic=topic,
                                      summary=f"Could not download video for: {topic}",
                                      steps=[])
        print(f"  [VideoAnalyst] ✅ Downloaded → {vid_path}")
    else:
        print(f"  [VideoAnalyst] Cache hit → {vid_path}")

    total_dur = _get_duration(vid_path)
    print(f"  [VideoAnalyst] Duration: {total_dur:.0f}s  ({total_dur/60:.1f} min)")

    # ── 2. Extract frames ────────────────────────────────────────────────────
    if not frame_dir.exists() or not any(frame_dir.glob("frame_*.jpg")):
        print(f"  [VideoAnalyst] Extracting frames every {sample_interval}s…")
        frames = _extract_frames(vid_path, sample_interval, frame_dir)
        print(f"  [VideoAnalyst] {len(frames)} frames extracted")
    else:
        frames = sorted(
            [(int(f.stem.split("_")[1] ) * sample_interval - sample_interval,
              str(f))
             for f in sorted(frame_dir.glob("frame_*.jpg"))],
        )
        print(f"  [VideoAnalyst] {len(frames)} frames from cache")

    if not frames:
        return VideoUnderstanding(url=url, topic=topic,
                                  summary=f"No frames extracted from video for: {topic}",
                                  steps=[], total_duration=total_dur,
                                  video_path=vid_path)

    # ── 3. Analyse in batches ────────────────────────────────────────────────
    if not vision_available():
        print("  [VideoAnalyst] ⚠️  Vision not available — returning stub understanding")
        return VideoUnderstanding(url=url, topic=topic,
                                  summary=f"Vision unavailable; video downloaded at {vid_path}",
                                  steps=[], total_duration=total_dur,
                                  video_path=vid_path)

    all_steps: list[dict] = []
    batches = [frames[i:i + BATCH_SIZE] for i in range(0, len(frames), BATCH_SIZE)]
    print(f"  [VideoAnalyst] Analysing {len(frames)} frames in "
          f"{len(batches)} batch(es) via Qwen-VL…")

    for b_idx, batch in enumerate(batches):
        print(f"    batch {b_idx+1}/{len(batches)}: "
              f"frames {batch[0][0]:.0f}s – {batch[-1][0]:.0f}s")
        results = _analyse_batch(batch, topic)
        all_steps.extend(results)

    # ── 4. Synthesise ────────────────────────────────────────────────────────
    print("  [VideoAnalyst] Synthesising steps…")
    summary, steps = _synthesise_steps(all_steps, topic, total_dur)

    print(f"  [VideoAnalyst] ✅ Understood {len(steps)} steps")
    for s in steps:
        print(f"    [{s.timestamp}] {s.action}")

    return VideoUnderstanding(
        url=url, topic=topic, summary=summary, steps=steps,
        total_duration=total_dur, video_path=vid_path,
    )
