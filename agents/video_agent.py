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
from dataclasses import dataclass
from pathlib import Path

from config.settings import (
    YOUTUBE_W, YOUTUBE_H, REELS_W, REELS_H,
    FPS, SLIDE_DURATION, FADE_DURATION, OUTPUT_DIR,
    FFMPEG_BIN, FFPROBE_BIN, HOOK_CARD_SECONDS,
)


@dataclass
class VideoRenderParams:
    """
    Per-video subtitle render parameters. Starts at defaults; QA remediation
    (Phase 3) tweaks a COPY for one video without touching global settings.
    """
    fontsize_pct: float = 0.040    # subtitle font as fraction of video height
    subtitle_y:   float = 0.80     # vertical position (fraction from top)
    max_cue_dur:  float = 10.0     # max seconds a cue stays on screen


# ── Bundled caption font ───────────────────────────────────────────────────────
# Anton — the classic bold condensed font used in most Reels/Shorts captions.
# Bundled in assets/fonts so the look is identical on Mac and on the server.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _caption_font() -> str:
    """Return path to the bundled Anton font, or '' to let FFmpeg pick a default."""
    anton = _PROJECT_ROOT / "assets" / "fonts" / "Anton-Regular.ttf"
    if anton.exists():
        return str(anton)
    # Fall back to a system bold font
    import platform
    sys_fonts = (["/System/Library/Fonts/Helvetica.ttc"]
                 if platform.system() == "Darwin"
                 else ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"])
    return next((f for f in sys_fonts if Path(f).exists()), "")


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

_XFADE = 0.3   # seconds — gentle fade-in/out on each scene (soft transitions)

def _fade_suffix(duration: float) -> str:
    """fade-in at start + fade-out at end, preserving total duration."""
    out_start = max(0.0, duration - _XFADE)
    return f",fade=t=in:st=0:d={_XFADE},fade=t=out:st={out_start:.2f}:d={_XFADE}"


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
        f"{_fade_suffix(duration)}"
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
        f"{_fade_suffix(duration)}"
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
                    video_w: int = 1920, video_h: int = 1080,
                    params: VideoRenderParams | None = None) -> None:
    """Attach audio + burn subtitles: raw video → final MP4.

    subtitle_offset_ms: shift timestamps forward (hook card prepended).
    video_w / video_h:  actual output dimensions — subtitle size scales with these.
    params:             per-video render params (font size / position / cue cap).
    """
    if params is None:
        params = VideoRenderParams()
    mode = _subtitle_mode()

    # If a hook card was prepended, both the subtitles AND the audio must be
    # shifted forward by the hook duration so voice + subtitle + visual all
    # start together at the end of the hook (fixes the voice-leads-subtitle lag).
    active_srt   = srt_path
    active_audio = audio_path
    if subtitle_offset_ms > 0:
        active_srt = _shift_srt(srt_path, subtitle_offset_ms)
        delayed_audio = tempfile.mktemp(suffix="_delayed.m4a")
        da = subprocess.run([
            FFMPEG_BIN, "-y", "-i", audio_path,
            "-af", f"adelay={subtitle_offset_ms}|{subtitle_offset_ms}",
            "-c:a", "aac", delayed_audio,
        ], capture_output=True)
        if da.returncode == 0 and Path(delayed_audio).exists():
            active_audio = delayed_audio
        else:
            print("  [Video] ⚠️  audio delay failed — voice may lead subtitles")

    if mode == "copy":
        vf_args = []
    elif mode == "libass":
        font_size = max(20, int(video_h * (params.fontsize_pct * 0.8)))
        margin_v  = int(video_h * (1 - params.subtitle_y))
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
        cue_dir = Path(tempfile.mkdtemp(prefix="cues_"))
        vf_args = ["-vf", _drawtext_filter(active_srt, cue_dir, video_w, video_h,
                                           fontsize_pct=params.fontsize_pct,
                                           subtitle_y=params.subtitle_y,
                                           max_cue_dur=params.max_cue_dur)]

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", raw_path,
        "-i", active_audio,
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


def _drawtext_filter(srt_path: str, tmp_dir: Path,
                     video_w: int = 1920, video_h: int = 1080,
                     fontsize_pct: float = 0.040,
                     subtitle_y: float = 0.80,
                     max_cue_dur: float = 10.0) -> str:
    """
    Build a drawtext filter chain — one per SRT cue, time-gated with enable='between(t,…)'.

    Uses textfile= (not text=) so subtitle content is read from a file. This
    completely avoids FFmpeg's inline-text escaping problems (colons, commas,
    apostrophes leaking the enable= expression into the visible text).

    Sizing is proportional to video height; Reels (taller) → bigger px font.
    tmp_dir: where per-cue text files are written (caller cleans up).
    """
    import re, platform, textwrap

    # ── Proportional sizing ────────────────────────────────────────────────────
    fontsize = max(28, int(video_h * fontsize_pct))   # ~49px YouTube / 86px Reels
    borderw  = max(2,  int(video_h * 0.0035))
    y_pos    = f"h*{subtitle_y}"

    # Wrap width: keep text inside the frame. Anton is condensed (~0.46× font wide).
    char_w     = 0.46 * fontsize
    max_chars  = max(12, int((video_w * 0.84) / char_w))

    # ── Font: bundled Anton (Reels caption style) ──────────────────────────────
    font_path = _caption_font()
    font_arg  = f"fontfile={font_path.replace(':', chr(92)+':')}:" if font_path else ""

    srt    = Path(srt_path).read_text(encoding="utf-8")
    blocks = re.split(r"\n\n+", srt.strip())
    parts  = []
    cue_n  = 0

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
        if t1 - t0 > max_cue_dur:
            t1 = t0 + max_cue_dur

        txt = " ".join(bl[2:]).strip()
        if not txt:
            continue

        # Wrap to frame width, then render EACH line as its own centered drawtext
        # (multi-line textfile left-aligns; per-line drawtext centers each line).
        lines    = textwrap.wrap(txt, width=max_chars) or [txt]
        line_gap = int(fontsize * 1.30)
        base_y   = int(video_h * subtitle_y)        # bottom anchor of the block
        n_lines  = len(lines)

        for li, line in enumerate(lines):
            cue_file = tmp_dir / f"cue_{cue_n:03d}.txt"
            cue_file.write_text(line, encoding="utf-8")
            tf_path = str(cue_file).replace(":", "\\:")
            cue_n += 1
            # Bottom-anchor: last line at base_y, earlier lines stack upward
            y_line = base_y - (n_lines - 1 - li) * line_gap
            parts.append(
                f"drawtext={font_arg}"
                f"fontsize={fontsize}:fontcolor=white:borderw={borderw}:bordercolor=black@0.9:"
                f"box=1:boxcolor=black@0.45:boxborderw=10:"
                f"x=(w-tw)/2:y={y_line}:"
                f"textfile={tf_path}:"
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
    import textwrap

    # Hook uses the same bundled Anton font as subtitles (consistent brand look)
    font_path = _caption_font()
    font_arg  = f"fontfile={font_path.replace(':', chr(92)+':')}:" if font_path else ""

    # Font size based on WIDTH (not height) so vertical Reels don't get giant text.
    # Wrap width computed from actual frame width + font size so text never clips.
    line_h     = int(w * 0.058)              # Anton condensed → can go a bit bigger
    char_w     = 0.46 * line_h               # Anton glyph width (condensed)
    max_chars  = max(10, int((w * 0.86) / char_w))
    lines      = textwrap.wrap(hook_text, width=max_chars)
    line_gap   = int(line_h * 1.25)
    total_h    = len(lines) * line_gap
    start_y    = (h - total_h) // 2          # vertically centred

    # Write each line to a textfile (robust — no escaping leak like text= had)
    cue_dir = Path(tempfile.mkdtemp(prefix="hook_"))
    dt_with_box = []
    for i, line in enumerate(lines):
        lf = cue_dir / f"hook_{i}.txt"
        lf.write_text(line, encoding="utf-8")
        tf_path = str(lf).replace(":", "\\:")
        y = start_y + i * line_gap
        dt_with_box.append(
            f"drawtext={font_arg}"
            f"fontsize={line_h}:fontcolor=white:borderw=5:bordercolor=black@0.9:"
            f"box=1:boxcolor=black@0.5:boxborderw=16:"
            f"x=(w-tw)/2:y={y}:"
            f"textfile={tf_path}"
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
    # Clean up hook text files
    for f in cue_dir.iterdir():
        f.unlink(missing_ok=True)
    cue_dir.rmdir()
    if r.returncode != 0:
        print(f"  [Video] Hook card failed (non-fatal): {r.stderr[-300:].decode(errors='replace')}")
    return r.returncode == 0


def assemble_video(video_id: str, media_items, audio_path: str,
                   srt_path: str, hook_text: str = "",
                   scene_durations: list[float] | None = None,
                   params: VideoRenderParams | None = None) -> dict[str, str]:
    """
    Build YouTube (16:9) and Reels (9:16) MP4s from mixed media.

    media_items: list of MediaItem(path, kind) OR list of str (legacy photo-only)
    hook_text:   if provided, a HOOK_CARD_SECONDS freeze-frame with bold text
                 is prepended to the video.
    scene_durations: Phase 1 — per-scene durations from TTS. When provided,
                 segment i is rendered at scene_durations[i] (clamped to
                 [MIN_CLIP_SECONDS, MAX_CLIP_SECONDS]) so visuals stay in sync
                 with narration. When None, falls back to SLIDE_DURATION.
    Returns {"youtube": path, "reels": path}
    """
    from config.settings import MIN_CLIP_SECONDS, MAX_CLIP_SECONDS

    if params is None:
        params = VideoRenderParams()

    # Normalise legacy str list → MediaItem list
    from agents.media_agent import MediaItem
    if media_items and isinstance(media_items[0], str):
        media_items = [MediaItem(p, "photo") for p in media_items]

    if len(media_items) < 2:
        raise ValueError(f"Need ≥2 media items, got {len(media_items)}")

    # Resolve per-segment durations
    def _seg_duration(i: int) -> float:
        if scene_durations and i < len(scene_durations):
            d = scene_durations[i]
            if d <= 0:
                return SLIDE_DURATION
            return max(MIN_CLIP_SECONDS, min(MAX_CLIP_SECONDS, d))
        return SLIDE_DURATION

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
        rendered_durs = []   # actual duration requested per rendered segment

        try:
            # ── Step 1: render each item to a normalised segment ──────
            # Each segment is sized to its scene's narration duration (Phase 1)
            for i, item in enumerate(media_items):
                seg = str(tmp_dir / f"seg_{i:03d}.mp4")
                dur = _seg_duration(i)
                ok  = False

                if item.kind == "clip":
                    ok = _make_clip_from_video(item.path, w, h, dur, seg)
                    if not ok:
                        print(f"    clip {i} render failed, falling back to photo mode")

                if not ok:  # photo or failed clip
                    direction = random.randint(0, len(_PAN_DIRECTIONS) - 1)
                    ok = _make_clip_from_photo(item.path, w, h, dur, seg, direction)

                if ok and Path(seg).stat().st_size > 1000:
                    segment_paths.append(seg)
                    rendered_durs.append(dur)
                else:
                    print(f"    item {i} skipped (render failed)")

            if len(segment_paths) < 2:
                raise RuntimeError("Too few segments rendered successfully")

            # ── Step 1b: hook card (prepend if hook_text provided) ────
            has_hook = False
            if hook_text and segment_paths:
                hook_seg = str(tmp_dir / "seg_hook.mp4")
                ok = _make_hook_card(hook_text, segment_paths[0],
                                     w, h, HOOK_CARD_SECONDS, hook_seg)
                if ok and Path(hook_seg).stat().st_size > 1000:
                    segment_paths = [hook_seg] + segment_paths
                    has_hook = True
                    print(f"  [Video] ✅ Hook card added ({HOOK_CARD_SECONDS}s)")

            # ── Step 2: get audio duration ────────────────────────────
            probe = subprocess.run(
                [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", audio_path],
                capture_output=True, text=True,
            )
            audio_dur = float(probe.stdout.strip())
            hook_dur  = HOOK_CARD_SECONDS if has_hook else 0

            # Total content duration from what we actually rendered
            content_dur = sum(rendered_durs)

            # Only loop if content is meaningfully shorter than audio.
            # With per-scene durations, content_dur ≈ audio_dur, so this is skipped.
            if content_dur < audio_dur - 1.0:
                content_segs = segment_paths[1:] if has_hook else segment_paths
                avg = content_dur / max(len(content_segs), 1)
                needed = int((audio_dur - content_dur) / max(avg, 1)) + 1
                extra  = (content_segs * (needed // max(len(content_segs), 1) + 1))[:needed]
                segment_paths = segment_paths + extra
                print(f"  [Video] Content {content_dur:.1f}s < audio {audio_dur:.1f}s "
                      f"— looped {len(extra)} extra segment(s)")

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

            # Persist the raw (no-subtitle) video so QA remediation can re-burn
            # subtitles with adjusted params WITHOUT re-rendering everything.
            raw_keep = str(out_dir / f"{variant}_raw.mp4")
            subprocess.run([FFMPEG_BIN, "-y", "-i", raw_mp4,
                            "-c:v", "copy", raw_keep], capture_output=True)

            # ── Step 4: audio + subtitles → final ────────────────────
            _burn_subtitles(raw_mp4, audio_path, srt_path, out_path,
                            subtitle_offset_ms=int(hook_dur * 1000),
                            video_w=w, video_h=h, params=params)

        finally:
            # Clean up temp segments
            for f in tmp_dir.iterdir():
                f.unlink(missing_ok=True)
            tmp_dir.rmdir()

        print(f"  [Video] ✅ Saved: {out_path}")
        outputs[variant] = out_path

    return outputs


# ── Phase 3: re-burn subtitles only (QA remediation) ──────────────────────────

def rerender_subtitles(video_id: str, variant: str,
                       params: VideoRenderParams,
                       hook_seconds: float = HOOK_CARD_SECONDS) -> str | None:
    """
    Re-burn subtitles onto the saved raw video with adjusted params, WITHOUT
    re-rendering clips. Used by the QA remediation loop (Phase 3).

    Reads:  output/{vid}/{variant}_raw.mp4, audio.mp3, subtitles.srt
    Writes: output/{vid}/{variant}.mp4  (overwrites the final)
    Returns the output path, or None if the raw video isn't available.
    """
    base    = Path(OUTPUT_DIR) / video_id
    raw     = base / f"{variant}_raw.mp4"
    audio   = base / "audio.mp3"
    srt     = base / "subtitles.srt"
    out     = base / f"{variant}.mp4"
    if not (raw.exists() and audio.exists() and srt.exists()):
        print(f"  [Video] rerender: missing raw/audio/srt for {video_id}/{variant}")
        return None

    w, h = (YOUTUBE_W, YOUTUBE_H) if variant == "youtube" else (REELS_W, REELS_H)
    print(f"  [Video] Re-burning {variant} subtitles "
          f"(font={params.fontsize_pct:.3f}, y={params.subtitle_y:.2f})…")
    _burn_subtitles(str(raw), str(audio), str(srt), str(out),
                    subtitle_offset_ms=int(hook_seconds * 1000),
                    video_w=w, video_h=h, params=params)
    return str(out)


def cleanup_raw(video_id: str) -> None:
    """Remove the persisted *_raw.mp4 files once a video is finalised."""
    base = Path(OUTPUT_DIR) / video_id
    for raw in base.glob("*_raw.mp4"):
        raw.unlink(missing_ok=True)
