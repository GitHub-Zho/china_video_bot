"""
Media Analyst Agent — analyse user-provided images/clips with Claude Vision,
then auto-generate a matched CreativeBrief for video production.

Usage (from orchestrator or CLI):
    from agents.media_analyst_agent import analyse_folder
    brief = analyse_folder("/path/to/my/photos")
    # brief is a CreativeBrief — plug straight into the normal pipeline

Supported formats: jpg, jpeg, png, webp, mp4, mov, heic

The agent:
1. Reads every image/clip from the folder
2. Sends images to Claude Vision (Anthropic API) for scene-level analysis
3. Generates a narration line per image that matches what's actually in the photo
4. Assembles a CreativeBrief with local file paths (no Pexels download needed)
"""
import base64
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.director_agent import CreativeBrief, ScenePlan
from agents.media_agent import MediaItem
from config.settings import SLIDE_DURATION, TARGET_YOUTUBE_SECONDS

# Max images to include in one video (avoid overlong)
MAX_SCENES = 10
SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SUPPORTED_VIDEOS = {".mp4", ".mov", ".avi", ".mkv"}

CLAUDE_MODEL = "claude-opus-4-5"   # best vision quality


def _encode_image(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for a local image."""
    suffix = path.suffix.lower()
    media_type_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".webp": "image/webp",
        ".heic": "image/jpeg",   # HEIC treated as jpeg for API
    }
    media_type = media_type_map.get(suffix, "image/jpeg")
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8"), media_type


def _extract_video_frame(video_path: Path, out_jpg: Path) -> bool:
    """Extract the first non-black frame from a video clip as a JPEG."""
    import subprocess
    from config.settings import FFMPEG_BIN
    r = subprocess.run([
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-vf", "select=gt(scene\\,0.1)",   # skip near-black frames
        "-frames:v", "1",
        "-q:v", "2",
        str(out_jpg),
    ], capture_output=True)
    if r.returncode != 0 or not out_jpg.exists():
        # Fallback: just take frame at t=1s
        subprocess.run([
            FFMPEG_BIN, "-y", "-ss", "1", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(out_jpg),
        ], capture_output=True)
    return out_jpg.exists() and out_jpg.stat().st_size > 1000


def _analyse_images_with_claude(image_paths: list[Path]) -> list[dict]:
    """
    Send up to MAX_SCENES images to Claude Vision in one call.
    Returns list of dicts: [{narration, visual_summary, emotion, suggested_hook}, ...]
    """
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    content = []
    valid_paths = []

    for p in image_paths[:MAX_SCENES]:
        try:
            b64, mtype = _encode_image(p)
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mtype, "data": b64}
            })
            content.append({
                "type": "text",
                "text": f"[Image {len(valid_paths)+1}: {p.name}]"
            })
            valid_paths.append(p)
        except Exception as e:
            print(f"  [MediaAnalyst] Skipping {p.name}: {e}")

    if not valid_paths:
        raise ValueError("No valid images found to analyse")

    n = len(valid_paths)
    content.append({
        "type": "text",
        "text": f"""You are analysing {n} image(s) that a travel creator wants to turn into a
short Instagram Reels / YouTube Shorts video about China.

For EACH image (in order), provide:
1. narration: 6-10 punchy words that match exactly what's shown (specific, no generic adjectives)
2. visual_summary: what is literally in the image (2 sentences max)
3. emotion: one of cinematic|energetic|serene|dramatic|warm
4. is_food: true/false (is the main subject food/drink?)

Also provide overall:
- hook: 6-10 word opening question or tension for scene 0
- title: short YouTube title (under 60 chars)
- topic: 3-5 word topic summary
- mood: cinematic|energetic|serene|dramatic
- audience_type: explorer|newcomer

Return ONLY valid JSON, no markdown:
{{
  "title": "...",
  "topic": "...",
  "mood": "...",
  "audience_type": "...",
  "hook": "...",
  "scenes": [
    {{
      "image_index": 0,
      "narration": "...",
      "visual_summary": "...",
      "emotion": "...",
      "is_food": false
    }},
    ...
  ]
}}"""
    })

    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": content}]
    )
    import json
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip()), valid_paths


def analyse_folder(
    folder_path: str,
    target_seconds: Optional[float] = None,
) -> tuple[CreativeBrief, list[MediaItem]]:
    """
    Analyse all images/clips in a folder and return a (CreativeBrief, media_items) pair.

    The CreativeBrief contains Claude-generated narrations matched to each image.
    The media_items list contains MediaItem(path=..., kind=...) for each file.

    Both are ready to plug directly into voice_agent and video_agent.
    """
    import tempfile

    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Media folder not found: {folder_path}")

    if target_seconds is None:
        target_seconds = float(TARGET_YOUTUBE_SECONDS)

    # ── Collect files ──────────────────────────────────────────────────────────
    all_files = sorted(
        f for f in folder.iterdir()
        if f.suffix.lower() in SUPPORTED_IMAGES | SUPPORTED_VIDEOS
    )
    if not all_files:
        raise ValueError(f"No supported media files found in {folder_path}")

    print(f"  [MediaAnalyst] Found {len(all_files)} file(s) in {folder.name}")

    # ── For video clips, extract a thumbnail frame for vision analysis ─────────
    tmp_dir   = Path(tempfile.mkdtemp(prefix="analyst_"))
    image_for_analysis: list[Path] = []
    kind_map:  dict[str, str] = {}   # original path → "clip" | "photo"

    for f in all_files[:MAX_SCENES]:
        if f.suffix.lower() in SUPPORTED_VIDEOS:
            thumb = tmp_dir / f"{f.stem}_thumb.jpg"
            if _extract_video_frame(f, thumb):
                image_for_analysis.append(thumb)
                kind_map[str(f)] = "clip"
            else:
                print(f"  [MediaAnalyst] Could not extract frame from {f.name}, skipping")
        else:
            image_for_analysis.append(f)
            kind_map[str(f)] = "photo"

    if not image_for_analysis:
        raise ValueError("No usable images/frames for analysis")

    # ── Send to Claude Vision ──────────────────────────────────────────────────
    print(f"  [MediaAnalyst] Sending {len(image_for_analysis)} image(s) to Claude Vision…")
    analysis, valid_paths = _analyse_images_with_claude(image_for_analysis)

    # valid_paths maps back to original files (thumbnails for clips)
    # Rebuild original file → analysis scene mapping
    original_files = all_files[:len(valid_paths)]

    # ── Build ScenePlan list ───────────────────────────────────────────────────
    n_scenes    = min(len(analysis["scenes"]), len(original_files))
    secs_per    = round(target_seconds / n_scenes, 1)

    scenes = []
    media_items = []

    for i, scene_data in enumerate(analysis["scenes"][:n_scenes]):
        orig_file = original_files[scene_data.get("image_index", i)]
        kind = kind_map.get(str(orig_file), "photo")

        scenes.append(ScenePlan(
            index=i,
            narration=scene_data.get("narration", ""),
            visual_query=scene_data.get("visual_summary", orig_file.name),
            duration=secs_per,
            emotion=scene_data.get("emotion", "cinematic"),
        ))
        media_items.append(MediaItem(path=str(orig_file), kind=kind))

    # ── Assemble CreativeBrief ─────────────────────────────────────────────────
    brief = CreativeBrief(
        title=analysis.get("title", f"Discover China — {folder.name}"),
        description=f"{analysis.get('topic', 'China travel')} #ChinaTravel #VisitChina #Asia #Travel",
        tags=["China travel", "Chinese food", "Asia travel", "travel 2025", "hidden China"],
        topic=analysis.get("topic", "China travel"),
        audience_type=analysis.get("audience_type", "explorer"),
        mood=analysis.get("mood", "cinematic"),
        hook=analysis.get("hook", scenes[0].narration if scenes else ""),
        cta="Follow for more hidden China adventures.",
        scenes=scenes,
        target_seconds=target_seconds,
    )

    # Cleanup thumbnails
    for f in tmp_dir.iterdir():
        f.unlink(missing_ok=True)
    tmp_dir.rmdir()

    print(f"  [MediaAnalyst] ✅ Brief generated: '{brief.topic}' "
          f"({n_scenes} scenes, hook: '{brief.hook[:50]}')")
    return brief, media_items
