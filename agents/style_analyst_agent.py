"""
Style Analyst Agent — the NEW agent (ROADMAP §3, Phase 5).

Analyses a reference video (local file or YouTube URL) and extracts a StyleProfile
of the parameters we can actually reproduce: pacing, subtitle style, hook style,
colour mood, shot types. Saves it to data/style_profiles/{name}.json.

Honest scope (risk #3): we extract REPRODUCIBLE traits only. We do NOT copy the
creator's footage, voice, or music. Colour mood is captured as a hint; matching
it exactly would need a future colour-grade pass.

Two consumers:
  - Director: reads the profile to match narration pacing / tone.
  - VideoAgent / QA: reads it to match subtitle style + compare similarity.
"""
import json
import subprocess
import tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path

from config.settings import FFMPEG_BIN, FFPROBE_BIN

STYLE_DIR = Path("data/style_profiles")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class StyleProfile:
    name:              str
    source:            str
    # Pacing
    avg_clip_seconds:  float = 4.0
    total_seconds:     float = 0.0
    # Hook (first 1-2s)
    hook_position:     str = "center"
    hook_style_desc:   str = ""
    # Subtitles
    subtitle_position: str = "bottom"       # bottom | lower-third | center
    subtitle_size:     str = "medium"       # small | medium | large
    subtitle_has_box:  bool = True
    subtitle_desc:     str = ""
    # Visual
    color_mood:        str = "natural"      # warm golden | cool cinematic | vibrant | natural
    shot_types:        list = field(default_factory=list)
    # Narration (inferred from pacing)
    narration_pace:    str = "medium"       # fast | medium | slow
    full_description:  str = ""

    def to_render_hints(self) -> dict:
        """Map style → concrete render params the VideoAgent can use."""
        size_map = {"small": 0.034, "medium": 0.040, "large": 0.048}
        pos_map  = {"bottom": 0.82, "lower-third": 0.78, "center": 0.55}
        return {
            "fontsize_pct": size_map.get(self.subtitle_size, 0.040),
            "subtitle_y":   pos_map.get(self.subtitle_position, 0.80),
            "slide_duration": self.avg_clip_seconds,
        }


# ── Pacing (ffmpeg scene detection) ───────────────────────────────────────────

def _duration(path: str) -> float:
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _avg_clip_seconds(path: str, total: float) -> float:
    """Estimate average shot length via scene-change detection."""
    if total <= 0:
        return 4.0
    r = subprocess.run(
        [FFMPEG_BIN, "-i", path, "-filter:v",
         "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
        capture_output=True, text=True)
    cuts = (r.stderr or "").count("pts_time")
    # cuts ≈ number of scene changes; +1 for the first shot
    shots = max(1, cuts + 1)
    return round(total / shots, 1)


# ── Vision analysis of style ──────────────────────────────────────────────────

_STYLE_PROMPT = """\
You are analysing frames from a short-form travel video to extract its visual STYLE
(not its content). Look across all frames and return ONLY this JSON:

{
  "hook_position": "center|top|bottom",
  "hook_style_desc": "<how the opening text card looks, 1 sentence>",
  "subtitle_position": "bottom|lower-third|center",
  "subtitle_size": "small|medium|large",
  "subtitle_has_box": true,
  "subtitle_desc": "<font weight, colour, background, 1 sentence>",
  "color_mood": "warm golden|cool cinematic|vibrant|natural|moody",
  "shot_types": ["aerial","close-up","wide","pov","timelapse"],
  "full_description": "<2-sentence overall style summary>"
}

If a field can't be judged, use the default shown. Return JSON only."""


def _sample_frames(path: str, total: float, tmp: Path, n: int = 5) -> list[str]:
    times = [max(0.3, total * f) for f in (0.04, 0.25, 0.5, 0.75, 0.95)][:n]
    out = []
    for i, t in enumerate(times):
        f = tmp / f"sf_{i}.jpg"
        r = subprocess.run(
            [FFMPEG_BIN, "-y", "-ss", str(t), "-i", path,
             "-frames:v", "1", "-vf", "scale=720:-1", "-q:v", "5", str(f)],
            capture_output=True)
        if r.returncode == 0 and f.exists() and f.stat().st_size > 1000:
            out.append(str(f))
    return out


# ── YouTube download (optional, yt-dlp) ───────────────────────────────────────

def _maybe_download(source: str, tmp: Path) -> str | None:
    """If source is a URL, try yt-dlp. Returns local path or None."""
    if not (source.startswith("http://") or source.startswith("https://")):
        return source if Path(source).exists() else None
    try:
        out = tmp / "ref.mp4"
        r = subprocess.run(
            ["yt-dlp", "-f", "mp4", "-o", str(out), "--no-playlist", source],
            capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and out.exists():
            return str(out)
        print(f"  [Style] yt-dlp failed: {r.stderr[-200:]}")
    except FileNotFoundError:
        print("  [Style] yt-dlp not installed — install it or use a local file")
    except Exception as e:
        print(f"  [Style] download error: {e}")
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def analyse_style(source: str, name: str) -> StyleProfile | None:
    """
    Analyse a reference video → StyleProfile, saved to data/style_profiles/{name}.json.
    source: local file path OR YouTube URL.
    Returns the profile, or None on failure.
    """
    from agents.vision import vision_available, analyse_images_json

    tmp = Path(tempfile.mkdtemp(prefix="style_"))
    try:
        path = _maybe_download(source, tmp)
        if not path:
            print(f"  [Style] Could not access source: {source}")
            return None

        total = _duration(path)
        avg   = _avg_clip_seconds(path, total)
        print(f"  [Style] {Path(path).name}: {total:.0f}s, ~{avg:.1f}s/shot")

        profile = StyleProfile(name=name, source=source,
                               avg_clip_seconds=avg, total_seconds=round(total, 1))
        profile.narration_pace = ("fast" if avg < 3 else
                                  "slow" if avg > 6 else "medium")

        # Vision pass (optional — degrade gracefully if verifier unavailable)
        if vision_available():
            frames = _sample_frames(path, total, tmp)
            if frames:
                data = analyse_images_json(frames, _STYLE_PROMPT)
                if isinstance(data, dict):
                    for k in ("hook_position", "hook_style_desc", "subtitle_position",
                              "subtitle_size", "subtitle_has_box", "subtitle_desc",
                              "color_mood", "full_description"):
                        if data.get(k) is not None:
                            setattr(profile, k, data[k])
                    if isinstance(data.get("shot_types"), list):
                        profile.shot_types = data["shot_types"]
                    print(f"  [Style] Vision: {profile.color_mood} mood, "
                          f"{profile.subtitle_size} {profile.subtitle_position} subs")
        else:
            print("  [Style] No verifier key — pacing only (no visual style)")

        STYLE_DIR.mkdir(parents=True, exist_ok=True)
        (STYLE_DIR / f"{name}.json").write_text(json.dumps(asdict(profile), indent=2))
        print(f"  [Style] ✅ Saved profile → data/style_profiles/{name}.json")
        return profile
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def load_style(name: str) -> StyleProfile | None:
    """Load a saved StyleProfile by name, or None if it doesn't exist."""
    p = STYLE_DIR / f"{name}.json"
    if not p.exists():
        return None
    try:
        return StyleProfile(**json.loads(p.read_text()))
    except Exception:
        return None


# ── Comparison (does the generated video match the reference style?) ──────────

_COMPARE_PROMPT = """\
You are comparing a GENERATED short-form travel video against a target STYLE.

Target style:
  pacing: ~{avg}s per shot ({pace})
  subtitles: {sub_size} {sub_pos}, {sub_desc}
  colour mood: {mood}
  hook: {hook}

The images are frames from the generated video. Judge how well it matches the
target style and return ONLY JSON:
{{
  "similarity": <0-10>,
  "matches": ["<what matches>"],
  "differences": ["<what differs from the target>"],
  "suggestions": ["<concrete tweak to get closer>"]
}}"""


def compare_to_reference(generated_video: str, profile: StyleProfile) -> dict | None:
    """
    Sample frames from a generated video and ask the verifier how closely it
    matches the reference StyleProfile. Returns a dict with similarity score +
    differences, or None if vision is unavailable.
    """
    from agents.vision import vision_available, analyse_images_json

    if not vision_available():
        print("  [Style] No verifier — cannot compare to reference")
        return None

    total = _duration(generated_video)
    if total <= 0:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="cmp_"))
    try:
        frames = _sample_frames(generated_video, total, tmp)
        if not frames:
            return None
        prompt = _COMPARE_PROMPT.format(
            avg=profile.avg_clip_seconds, pace=profile.narration_pace,
            sub_size=profile.subtitle_size, sub_pos=profile.subtitle_position,
            sub_desc=profile.subtitle_desc or "bold caption",
            mood=profile.color_mood, hook=profile.hook_style_desc or "bold text card",
        )
        result = analyse_images_json(frames, prompt)
        if isinstance(result, dict):
            sim = result.get("similarity", "?")
            print(f"  [Style] Similarity to '{profile.name}': {sim}/10")
            for d in result.get("differences", [])[:3]:
                print(f"    ✗ {d}")
            return result
        return None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
