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
    FFMPEG_BIN, FFPROBE_BIN, HOOK_CARD_SECONDS,
)


# ── Subtitle capability ────────────────────────────────────────────────────────

_SUBTITLE_MODE: str | None = None   # cached on first call; reset by restarting Python

def _subtitle_mode() -> str:
    global _SUBTITLE_MODE
    if _SUBTITLE_MODE is None:
        r = subprocess.run([FFMPEG_BIN, "-filters"], capture_output=True, text=True)
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
        FFMPEG_BIN, "-y", "-i", src,
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
        FFMPEG_BIN, "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", src,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-an", "-pix_fmt", "yuv420p",
        out,
    ]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0


# ── Subtitle helpers ──────────────────────────────────────────────────────────

def _shift_srt(srt_path: str, offset_ms: int) -> str:
    """Return path to a new SRT file with all timestamps shifted by offset_ms."""
    import re, tempfile

    def _ms_to_ts(ms: int) -> str:
        h = ms // 3_600_000; ms -= h * 3_600_000
        m = ms // 60_000;    ms -= m * 60_000
        s = ms // 1_000;     ms -= s * 1_000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _shift_line(line: str) -> str:
        m = re.match(
            r"(\d+:\d+:\d+[,\.]\d+)\s*-->\s*(\d+:\d+:\d+[,\.]\d+)", line
        )
        if not m:
            return line
        def _parse(ts: str) -> int:
            h, rest = ts.split(":", 1)
            mi, rest = rest.split(":", 1)
            s, ms_raw = re.split(r"[,\.]", rest)
            return int(h)*3_600_000 + int(mi)*60_000 + int(s)*1_000 + int(ms_raw)
        t0 = _parse(m.group(1)) + offset_ms
        t1 = _parse(m.group(2)) + offset_ms
        return f"{_ms_to_ts(t0)} --> {_ms_to_ts(t1)}"

    content = Path(srt_path).read_text(encoding="utf-8")
    shifted = "\n".join(_shift_line(l) for l in content.splitlines())
    tmp = tempfile.mktemp(suffix="_shifted.srt")
    Path(tmp).write_text(shifted, encoding="utf-8")
    return tmp


# ── Subtitle burn ─────────────────────────────────────────────────────────────

def _burn_subtitles(raw_path: str, audio_path: str,
                    srt_path: str, out_path: str,
                    subtitle_offset_ms: int = 0,
                    video_w: int = 1920, video_h: int = 1080) -> None:
    """Attach audio + burn subtitles: raw video → final MP4.

    subtitle_offset_ms: shift timestamps forward (hook card prepended).
    video_w / video_h:  actual output dimensions — subtitle size scales with these.
    """
    mode = _subtitle_mode()

    # If hook card was added, write a shifted SRT file
    active_srt = srt_path
    if subtitle_offset_ms > 0:
        active_srt = _shift_srt(srt_path, subtitle_offset_ms)

    if mode == "copy":
        vf_args = []
    elif mode == "libass":
        # FontSize in ASS/libass is in "script resolution" units (not pixels).
        # Approximate: FontSize ≈ 3.2% of video height gives good proportions.
        font_size = max(20, int(video_h * 0.032))
        # MarginV: keep text above platform UI (bottom 15% for Instagram/YouTube Shorts)
        margin_v  = int(video_h * 0.12)
        safe_srt  = active_srt.replace("\\", "/").replace(":", "\\:")
        vf_args   = [
            "-vf",
            f"subtitles={safe_srt}:"
            f"force_style='FontName=Arial Bold,FontSize={font_size},Bold=1,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BackColour=&H80000000,"
            f"Outline=2,Shadow=1,BorderStyle=4,"
            f"Alignment=2,MarginV={margin_v}'"
        ]
    else:
        vf_args = ["-vf", _drawtext_filter(active_srt, video_w, video_h)]

    cmd = [
        FFMPEG_BIN, "-y",
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


def _drawtext_filter(srt_path: str, video_w: int = 1920, video_h: int = 1080) -> str:
    """
    Build a drawtext filter chain — one per SRT cue, time-gated with enable='between(t,…)'.

    Subtitle sizing rules (platform-aware):
    - Font size: ~3.2% of video height  → 34px YouTube(1080h) / 61px Reels(1920h)
    - Y position: 82% down — clears platform UI chrome at bottom (Instagram/YouTube Shorts)
    - Max cue duration: 3.5s — prevents any single subtitle from lingering on screen
    - Border width: scales slightly with video height
    """
    import re, platform

    # ── Proportional sizing ────────────────────────────────────────────────────
    fontsize   = max(24, int(video_h * 0.032))      # scales with height
    borderw    = max(2,  int(video_h * 0.003))       # ~3px@1080, ~5px@1920
    # Y: 82% down = safe zone for both Instagram Reels and YouTube Shorts UI
    y_pos      = f"h*0.82"
    MAX_CUE_DUR = 3.5   # seconds — cap any subtitle that lingers too long

    # ── Font selection ─────────────────────────────────────────────────────────
    if platform.system() == "Darwin":
        font_candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    else:
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    font_path = next((f for f in font_candidates if Path(f).exists()), "")
    font_arg  = f"fontfile={font_path.replace(':', chr(92)+':')}:" if font_path else ""

    srt    = Path(srt_path).read_text(encoding="utf-8")
    blocks = re.split(r"\n\n+", srt.strip())
    parts  = []

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

        # Cap cue duration — prevents subtitle from lingering on screen
        if t1 - t0 > MAX_CUE_DUR:
            t1 = t0 + MAX_CUE_DUR

        txt = " ".join(bl[2:])
        txt_esc = (txt
                   .replace("\\", "\\\\")
                   .replace("'",  "\\'")
                   .replace(":",  "\\:")
                   .replace(",",  "\\,"))

        parts.append(
            f"drawtext={font_arg}"
            f"fontsize={fontsize}:fontcolor=white:borderw={borderw}:bordercolor=black@0.9:"
            f"box=1:boxcolor=black@0.35:boxborderw=8:"
            f"x=(w-tw)/2:y={y_pos}:"
            f"text={txt_esc}:"
            f"enable='between(t,{t0:.3f},{t1:.3f})'"
        )

    return ",".join(parts) if parts else "null"


# ── Public API ────────────────────────────────────────────────────────────────

# ── Hook card ─────────────────────────────────────────────────────────────────

def _make_hook_card(hook_text: str, first_clip: str,
                    w: int, h: int, duration: float, out: str) -> bool:
    """
    Freeze the first frame of first_clip and burn large hook text on top.
    Creates a HOOK_CARD_SECONDS-long card that prepends the video.

    hook_text: the Director's hook line (1 sentence)
    """
    import platform, textwrap

    # Font for hook card — larger & bolder than subtitle font
    if platform.system() == "Darwin":
        font_candidates = ["/System/Library/Fonts/Helvetica.ttc",
                           "/Library/Fonts/Arial.ttf"]
    else:
        font_candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                           "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
    font_path = next((f for f in font_candidates if Path(f).exists()), "")
    font_arg  = f"fontfile={font_path.replace(':', chr(92)+':')}:" if font_path else ""

    # Wrap long text — max 30 chars per line for hook card
    lines      = textwrap.wrap(hook_text, width=30)
    line_h     = int(h * 0.075)          # font size ≈ 7.5% of frame height
    total_h    = len(lines) * line_h
    start_y    = (h - total_h) // 2      # vertically centred

    # Build one drawtext filter per line
    dt_parts = []
    for i, line in enumerate(lines):
        esc = (line.replace("\\", "\\\\")
                   .replace("'",  "\\'")
                   .replace(":",  "\\:")
                   .replace(",",  "\\,"))
        y = start_y + i * line_h
        dt_parts.append(
            f"drawtext={font_arg}"
            f"fontsize={line_h}:fontcolor=white:borderw=4:bordercolor=black@0.95:"
            f"x=(w-tw)/2:y={y}:"
            f"text={esc}"
        )

    # Semi-transparent dark overlay behind text  (box behind text)
    # We use drawtext box option directly
    dt_with_box = []
    for i, line in enumerate(lines):
        esc = (line.replace("\\", "\\\\")
                   .replace("'",  "\\'")
                   .replace(":",  "\\:")
                   .replace(",",  "\\,"))
        y = start_y + i * line_h
        dt_with_box.append(
            f"drawtext={font_arg}"
            f"fontsize={line_h}:fontcolor=white:borderw=5:bordercolor=black@0.9:"
            f"box=1:boxcolor=black@0.45:boxborderw=12:"
            f"x=(w-tw)/2:y={y}:"
            f"text={esc}"
        )

    vf_filter = ",".join(dt_with_box)

    # Fade out last 0.4s so the cut to first scene is smooth, not jarring
    fade_start = max(0, duration - 0.4)
    fade_filter = f"fade=t=out:st={fade_start:.2f}:d=0.4"

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", first_clip,
        "-vf", (f"trim=0:{HOOK_CARD_SECONDS},setpts=PTS-STARTPTS,"
                f"{vf_filter},{fade_filter}"),
        "-t", str(duration),
        "-r", str(FPS),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-an", "-pix_fmt", "yuv420p",
        out,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(f"  [Video] Hook card failed (non-fatal): {r.stderr[-300:].decode(errors='replace')}")
    return r.returncode == 0


def assemble_video(video_id: str, media_items, audio_path: str,
                   srt_path: str, hook_text: str = "") -> dict[str, str]:
    """
    Build YouTube (16:9) and Reels (9:16) MP4s from mixed media.

    media_items: list of MediaItem(path, kind) OR list of str (legacy photo-only)
    hook_text:   if provided, a HOOK_CARD_SECONDS freeze-frame with bold text
                 is prepended to the video.
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

            # ── Step 1b: hook card (prepend if hook_text provided) ────
            if hook_text and segment_paths:
                hook_seg = str(tmp_dir / "seg_hook.mp4")
                ok = _make_hook_card(hook_text, segment_paths[0],
                                     w, h, HOOK_CARD_SECONDS, hook_seg)
                if ok and Path(hook_seg).stat().st_size > 1000:
                    segment_paths = [hook_seg] + segment_paths
                    print(f"  [Video] ✅ Hook card added ({HOOK_CARD_SECONDS}s)")

            # ── Step 2: get audio duration, loop segments if needed ───
            probe = subprocess.run(
                [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", audio_path],
                capture_output=True, text=True,
            )
            audio_dur = float(probe.stdout.strip())
            # Total visual duration = hook card + content clips
            hook_dur  = HOOK_CARD_SECONDS if (hook_text and segment_paths) else 0
            clips_dur = len(segment_paths) * SLIDE_DURATION

            # If clips total shorter than audio, repeat content segments
            if clips_dur < audio_dur + hook_dur:
                content_segs = segment_paths[1:] if hook_text else segment_paths
                needed = int((audio_dur + hook_dur) / SLIDE_DURATION) + 2
                segment_paths = (segment_paths[:1] if hook_text else []) + \
                                (content_segs * (needed // max(len(content_segs), 1) + 1))[:needed]

            # ── Step 3: concat segments ───────────────────────────────
            list_file = tmp_dir / "concat.txt"
            list_file.write_text(
                "\n".join(f"file '{Path(s).absolute()}'" for s in segment_paths)
            )
            raw_mp4 = str(tmp_dir / "raw.mp4")
            r = subprocess.run([
                FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-t", str(audio_dur + hook_dur),
                "-c:v", "copy", raw_mp4,
            ], capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(f"Concat failed:\n{r.stderr[-500:].decode()}")

            # ── Step 4: audio + subtitles → final ────────────────────
            _burn_subtitles(raw_mp4, audio_path, srt_path, out_path,
                            subtitle_offset_ms=int(hook_dur * 1000),
                            video_w=w, video_h=h)

        finally:
            # Clean up temp segments
            for f in tmp_dir.iterdir():
                f.unlink(missing_ok=True)
            tmp_dir.rmdir()

        print(f"  [Video] ✅ Saved: {out_path}")
        outputs[variant] = out_path

    return outputs
