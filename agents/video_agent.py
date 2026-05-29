"""Video Agent — assembles video from mixed media (clips + photos) + audio + subtitles.

Pipeline per variant (youtube / reels):
  1. For each MediaItem:
     - clip  → FFmpeg: trim + scale-crop to target size (no black bars)
     - photo → FFmpeg: Ken Burns zoom-pan effect → 5s clip
  2. FFmpeg concat all clips → raw video (no audio)
  3. FFmpeg: attach audio + burn subtitles → final MP4

All heavy lifting is in FFmpeg (no MoviePy dependency for this path),
which means faster processing and no Python memory limits on large frames.
MoviePy path kept as fallback for legacy image-only calls.
"""
import random
import subprocess
import tempfile
from pathlib import Path

from config.settings import (
    YOUTUBE_W, YOUTUBE_H, REELS_W, REELS_H,
    FPS, SLIDE_DURATION, FADE_DURATION, OUTPUT_DIR,
)


# ── Subtitle capability ────────────────────────────────────────────────────────

_SUBTITLE_MODE: str | None = None

def _subtitle_mode() -> str:
    global _SUBTITLE_MODE
    if _SUBTITLE_MODE is None:
        r = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
        out = r.stdout + r.stderr
        filters = {line.split()[1] for line in out.splitlines() if len(line.split()) >= 2}
        if "subtitles" in filters:
            _SUBTITLE_MODE = "libass"
        elif "drawtext" in filters:
            _SUBTITLE_MODE = "drawtext"
        else:
            _SUBTITLE_MODE = "copy"
        labels = {"libass": "libass ✅", "drawtext": "drawtext fallback",
                  "copy": "⚠️  no subtitle filters — stream copy"}
        print(f"  [Video] Subtitle mode: {labels[_SUBTITLE_MODE]}")
    return _SUBTITLE_MODE


# ── Per-item clip generation ───────────────────────────────────────────────────

_PAN_DIRECTIONS = [
    # (x_expr, y_expr) for zoompan — 5 different directions
    ("iw/2-(iw/zoom/2)",  "ih/2-(ih/zoom/2)"),    # center zoom
    ("0",                 "0"),                     # top-left pan
    ("iw-(iw/zoom)",      "0"),                     # top-right pan
    ("0",                 "ih-(ih/zoom)"),           # bottom-left pan
    ("iw-(iw/zoom)",      "ih-(ih/zoom)"),           # bottom-right pan
]


def _make_clip_from_video(src: str, w: int, h: int, duration: float,
                           out: str) -> bool:
    """Trim + scale-crop a video clip to (w,h), no black bars."""
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1"
    )
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-t", str(duration),
        "-vf", vf,
        "-r", str(FPS),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-an", "-pix_fmt", "yuv420p",
        out,
    ]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0


def _make_clip_from_photo(src: str, w: int, h: int, duration: float,
                           out: str, direction_idx: int = 0) -> bool:
    """Ken Burns effect: slow zoom-pan on a photo → video clip.

    Upscales to 8000px first (eliminates zoompan jitter per FFmpeg bug #4298).
    """
    n_frames = int(duration * FPS)
    x_expr, y_expr = _PAN_DIRECTIONS[direction_idx % len(_PAN_DIRECTIONS)]
    # Upscale → zoompan → scale down to target
    vf = (
        f"scale=8000:-1,"
        f"zoompan=z='min(zoom+0.0015,1.4)':"
        f"x='{x_expr}':y='{y_expr}':"
        f"d={n_frames}:s={w}x{h}:fps={FPS},"
        f"setsar=1"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", src,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-an", "-pix_fmt", "yuv420p",
        out,
    ]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0


# ── Subtitle burn ─────────────────────────────────────────────────────────────

def _burn_subtitles(raw_path: str, audio_path: str,
                    srt_path: str, out_path: str) -> None:
    """Attach audio + burn subtitles: raw video → final MP4."""
    mode = _subtitle_mode()

    if mode == "copy":
        vf_args = []
    elif mode == "libass":
        safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")
        # Phase F: improved subtitle style — bold, larger, drop shadow, bottom-centre
        vf_args = [
            "-vf",
            f"subtitles={safe_srt}:"
            f"force_style='FontName=Arial Bold,FontSize=32,Bold=1,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BackColour=&H80000000,"
            f"Outline=2,Shadow=1,BorderStyle=4,"
            f"Alignment=2,MarginV=60'"
        ]
    else:
        vf_args = ["-vf", _drawtext_filter(srt_path)]

    cmd = [
        "ffmpeg", "-y",
        "-i", raw_path,
        "-i", audio_path,
        *vf_args,
        "-c:v", "libx264" if vf_args else "copy",
        "-crf", "20", "-preset", "fast",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg final encode failed:\n{r.stderr[-1500:]}")


def _drawtext_filter(srt_path: str) -> str:
    import re, tempfile as tf_mod
    srt    = Path(srt_path).read_text(encoding="utf-8")
    blocks = re.split(r"\n\n+", srt.strip())
    lines  = []
    for block in blocks:
        bl = block.strip().splitlines()
        if len(bl) < 3:
            continue
        ts = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
            bl[1]
        )
        if not ts:
            continue
        h1,m1,s1,ms1,h2,m2,s2,ms2 = map(int, ts.groups())
        t0 = h1*3600+m1*60+s1+ms1/1000
        t1 = h2*3600+m2*60+s2+ms2/1000
        txt = " ".join(bl[2:]).replace("'", "\\'").replace(":", "\\:")
        lines += [f"{t0:.3f} drawtext reinit text='{txt}';",
                  f"{t1:.3f} drawtext reinit text='';"]
    tmp = tf_mod.NamedTemporaryFile(mode="w", suffix=".txt",
                                    delete=False, encoding="utf-8")
    tmp.write("\n".join(lines)); tmp.close()
    safe = tmp.name.replace("\\", "/").replace(":", "\\:")
    return (f"drawtext=fontsize=28:fontcolor=white:borderw=2:"
            f"bordercolor=black@0.8:x=(w-tw)/2:y=h-70:text='',"
            f"sendcmd=f={safe}")


# ── Public API ────────────────────────────────────────────────────────────────

def assemble_video(video_id: str, media_items, audio_path: str,
                   srt_path: str) -> dict[str, str]:
    """
    Build YouTube (16:9) and Reels (9:16) MP4s from mixed media.

    media_items: list of MediaItem(path, kind) OR list of str (legacy photo-only)
    Returns {"youtube": path, "reels": path}
    """
    # Normalise legacy str list → MediaItem list
    from agents.media_agent import MediaItem
    if media_items and isinstance(media_items[0], str):
        media_items = [MediaItem(p, "photo") for p in media_items]

    if len(media_items) < 2:
        raise ValueError(f"Need ≥2 media items, got {len(media_items)}")

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

        n = len(media_items)
        print(f"  [Video] Building {variant} ({w}×{h}, {n} items)…")

        tmp_dir = Path(tempfile.mkdtemp())
        segment_paths = []

        try:
            # ── Step 1: render each item to a normalised segment ──────
            for i, item in enumerate(media_items):
                seg = str(tmp_dir / f"seg_{i:03d}.mp4")
                ok  = False

                if item.kind == "clip":
                    ok = _make_clip_from_video(item.path, w, h,
                                               SLIDE_DURATION, seg)
                    if not ok:
                        print(f"    clip {i} render failed, falling back to photo mode")

                if not ok:  # photo or failed clip
                    direction = random.randint(0, len(_PAN_DIRECTIONS) - 1)
                    ok = _make_clip_from_photo(item.path, w, h,
                                               SLIDE_DURATION, seg, direction)

                if ok and Path(seg).stat().st_size > 1000:
                    segment_paths.append(seg)
                else:
                    print(f"    item {i} skipped (render failed)")

            if len(segment_paths) < 2:
                raise RuntimeError("Too few segments rendered successfully")

            # ── Step 2: get audio duration, loop segments if needed ───
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", audio_path],
                capture_output=True, text=True,
            )
            audio_dur = float(probe.stdout.strip())
            clips_dur = len(segment_paths) * SLIDE_DURATION

            # If clips total shorter than audio, repeat them
            if clips_dur < audio_dur:
                repeats = int(audio_dur / clips_dur) + 1
                segment_paths = (segment_paths * repeats)[:int(audio_dur / SLIDE_DURATION) + 1]

            # ── Step 3: concat segments ───────────────────────────────
            list_file = tmp_dir / "concat.txt"
            list_file.write_text(
                "\n".join(f"file '{Path(s).absolute()}'" for s in segment_paths)
            )
            raw_mp4 = str(tmp_dir / "raw.mp4")
            r = subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-t", str(audio_dur),
                "-c:v", "copy", raw_mp4,
            ], capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(f"Concat failed:\n{r.stderr[-500:].decode()}")

            # ── Step 4: audio + subtitles → final ────────────────────
            _burn_subtitles(raw_mp4, audio_path, srt_path, out_path)

        finally:
            # Clean up temp segments
            for f in tmp_dir.iterdir():
                f.unlink(missing_ok=True)
            tmp_dir.rmdir()

        print(f"  [Video] ✅ Saved: {out_path}")
        outputs[variant] = out_path

    return outputs
