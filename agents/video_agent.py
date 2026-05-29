"""Video Agent — MoviePy slideshow + FFmpeg subtitle burn.

Pipeline:
  1. PIL:     letterbox-resize each image to target resolution
  2. MoviePy: concatenate ImageClips + attach audio → temp MP4 (no subtitles)
  3. FFmpeg:  burn SRT subtitles onto the temp MP4 → final MP4

This split keeps MoviePy code simple and delegates subtitle rendering
to FFmpeg, which has reliable libass support on Ubuntu (Oracle Cloud).
On macOS without libass, a drawtext fallback is used automatically.
"""
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from config.settings import (
    YOUTUBE_W, YOUTUBE_H, REELS_W, REELS_H,
    FPS, SLIDE_DURATION, FADE_DURATION, OUTPUT_DIR
)


# ── Image preprocessing ────────────────────────────────────────────────────────

def _letterbox(img_path: str, w: int, h: int) -> np.ndarray:
    """Resize image to (w, h) with black letterbox bars. Returns np.array."""
    img = Image.open(img_path).convert("RGB")
    img.thumbnail((w, h), Image.LANCZOS)
    bg = Image.new("RGB", (w, h), (0, 0, 0))
    bg.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return np.array(bg)


# ── Subtitle capability check ──────────────────────────────────────────────────

def _ffmpeg_filters() -> set[str]:
    r = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
    out = r.stdout + r.stderr
    found: set[str] = set()
    for line in out.splitlines():
        # lines like " .. drawtext    V->V   ..."
        parts = line.split()
        if len(parts) >= 2:
            found.add(parts[1])
    return found


_SUBTITLE_MODE: str | None = None   # "libass" | "drawtext" | "copy"

def _subtitle_mode() -> str:
    global _SUBTITLE_MODE
    if _SUBTITLE_MODE is None:
        filters = _ffmpeg_filters()
        if "subtitles" in filters:
            _SUBTITLE_MODE = "libass"
        elif "drawtext" in filters:
            _SUBTITLE_MODE = "drawtext"
        else:
            _SUBTITLE_MODE = "copy"
        labels = {"libass": "libass ✅", "drawtext": "drawtext fallback",
                  "copy": "⚠️  no subtitle filters — stream copy (no burned subs)"}
        print(f"  [Video] Subtitle mode: {labels[_SUBTITLE_MODE]}")
    return _SUBTITLE_MODE


# ── MoviePy assembly (no subtitles) ───────────────────────────────────────────

def _make_slideshow(image_paths: list[str], audio_path: str,
                    w: int, h: int, tmp_path: str) -> None:
    """Assemble images + audio into a temporary MP4 (no subtitles)."""
    from moviepy import ImageClip, AudioFileClip, concatenate_videoclips
    from moviepy.video.fx import FadeIn, FadeOut

    clips = []
    for img_path in image_paths:
        frame = _letterbox(img_path, w, h)
        clip  = (
            ImageClip(frame)
            .with_duration(SLIDE_DURATION)
            .with_fps(FPS)
            .with_effects([FadeIn(FADE_DURATION), FadeOut(FADE_DURATION)])
        )
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")
    audio = AudioFileClip(audio_path)

    # Trim audio/video to the shorter of the two
    duration = min(video.duration, audio.duration)
    final    = video.subclipped(0, duration).with_audio(
        audio.subclipped(0, duration)
    )

    final.write_videofile(
        tmp_path,
        codec="libx264", audio_codec="aac",
        fps=FPS, preset="fast",
        logger=None,          # suppress verbose MoviePy output
    )
    final.close()


# ── FFmpeg subtitle burn ───────────────────────────────────────────────────────

def _burn_subtitles(tmp_path: str, srt_path: str, out_path: str) -> None:
    """Add burned-in subtitles to tmp_path → out_path using FFmpeg.

    Falls back gracefully through three modes:
      libass   — Ubuntu/cloud: full styled subtitles via libass
      drawtext — partial FFmpeg builds: SRT parsed manually via sendcmd
      copy     — Mac default FFmpeg (no text filters): stream-copy, no subs
    """
    mode = _subtitle_mode()

    if mode == "copy":
        # No subtitle filters available — just remux without re-encoding
        cmd = [
            "ffmpeg", "-y", "-i", tmp_path,
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "+faststart",
            out_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg remux failed:\n{result.stderr[-800:]}")
        return

    if mode == "libass":
        safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")
        vf = (
            f"subtitles={safe_srt}:"
            f"force_style='FontName=Arial,FontSize=28,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"Outline=2,BorderStyle=1,Shadow=0,Alignment=2,MarginV=45'"
        )
    else:
        # drawtext fallback: parses SRT manually
        vf = _drawtext_filter(srt_path)

    cmd = [
        "ffmpeg", "-y", "-i", tmp_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        out_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg subtitle burn failed:\n{result.stderr[-1500:]}"
        )


def _drawtext_filter(srt_path: str) -> str:
    """
    Build a drawtext+sendcmd filter string by parsing the SRT file.
    Used as fallback when libass is not available (macOS default ffmpeg).
    """
    import re
    srt    = Path(srt_path).read_text(encoding="utf-8")
    blocks = re.split(r"\n\n+", srt.strip())

    cmd_lines: list[str] = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        ts = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
            lines[1]
        )
        if not ts:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, ts.groups())
        start = h1*3600 + m1*60 + s1 + ms1/1000
        end   = h2*3600 + m2*60 + s2 + ms2/1000
        text  = " ".join(lines[2:]).replace("'", "\\'").replace(":", "\\:")
        cmd_lines.append(f"{start:.3f} drawtext reinit text='{text}';")
        cmd_lines.append(f"{end:.3f}   drawtext reinit text='';")

    # Write sendcmd to a temp file (will be cleaned by caller's context)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    tmp.write("\n".join(cmd_lines))
    tmp.close()

    safe_cmd = tmp.name.replace("\\", "/").replace(":", "\\:")
    return (
        f"drawtext=fontsize=28:fontcolor=white:borderw=2:bordercolor=black@0.8:"
        f"x=(w-tw)/2:y=h-70:text='',"
        f"sendcmd=f={safe_cmd}"
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def assemble_video(video_id: str, image_paths: list[str],
                   audio_path: str, srt_path: str) -> dict[str, str]:
    """
    Build YouTube (16:9) and Instagram Reels (9:16) MP4s.
    Resume-safe: skips variants that already exist.
    Returns {"youtube": path, "reels": path}
    """
    if len(image_paths) < 2:
        raise ValueError(f"Need ≥2 images, got {len(image_paths)}")

    out_dir = Path(OUTPUT_DIR) / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}

    for variant, (w, h) in [("youtube", (YOUTUBE_W, YOUTUBE_H)),
                              ("reels",   (REELS_W,   REELS_H))]:
        out_path = str(out_dir / f"{variant}.mp4")
        if Path(out_path).exists() and Path(out_path).stat().st_size > 20_000:
            print(f"  [Video] {variant}.mp4 already exists, skipping")
            outputs[variant] = out_path
            continue

        print(f"  [Video] Building {variant} ({w}×{h}, {len(image_paths)} images)…")

        # Step 1 — MoviePy: images + audio → temp video
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            tmp_path = tf.name
        try:
            _make_slideshow(image_paths, audio_path, w, h, tmp_path)

            # Step 2 — FFmpeg: burn subtitles
            _burn_subtitles(tmp_path, srt_path, out_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        print(f"  [Video] ✅ Saved: {out_path}")
        outputs[variant] = out_path

    return outputs
