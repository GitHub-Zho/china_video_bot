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

import re

from config.settings import (
    YOUTUBE_W, YOUTUBE_H, REELS_W, REELS_H,
    FPS, SLIDE_DURATION, FADE_DURATION, OUTPUT_DIR,
    FFMPEG_BIN, FFPROBE_BIN, HOOK_CARD_SECONDS, HOOK_OVERLAY_SECONDS,
)


@dataclass
class VideoRenderParams:
    """
    Per-video subtitle render parameters. Starts at defaults; QA remediation
    (Phase 3) tweaks a COPY for one video without touching global settings.
    """
    fontsize_pct: float = 0.050    # subtitle font as fraction of video height (~43px YouTube, ~77px Reels)
    subtitle_y:   float = 0.77     # vertical position — slightly toward center vs 0.80 (was bottom-heavy)
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


# ── Smart portrait crop: subject-aware x offset ───────────────────────────────

def _find_subject_x_fraction(image_path: str) -> float:
    """
    Ask Qwen-VL where the main subject sits horizontally in the image.
    Returns a float 0.0 (far left) → 1.0 (far right).  Default: 0.5 (center).

    Used when cropping a LANDSCAPE frame into a PORTRAIT strip (Reels 9:16).
    The returned fraction is applied as:
        crop_x = (scaled_width - crop_width) * fraction
    so 0.0 crops from the left edge, 0.5 from center, 1.0 from the right edge.

    Gracefully returns 0.5 if vision is unavailable or the image doesn't exist.
    """
    from pathlib import Path as _P
    if not _P(image_path).exists():
        return 0.5
    try:
        from agents.vision import analyse_images
        prompt = (
            "Look at this image. Where is the MAIN SUBJECT or focal point "
            "positioned HORIZONTALLY?\n\n"
            "Reply with a single integer from 0 to 10:\n"
            "  0-1 = far LEFT\n"
            "  2-3 = left of center\n"
            "  4-6 = CENTER\n"
            "  7-8 = right of center\n"
            "  9-10 = far RIGHT\n\n"
            "Reply with ONLY the number. Nothing else."
        )
        resp = analyse_images([image_path], prompt, temperature=0.1, max_tokens=4)
        if resp:
            m = re.search(r'\b(\d+)\b', resp.strip())
            if m:
                score = max(0, min(10, int(m.group(1))))
                frac  = round(score / 10.0, 2)
                return frac
    except Exception:
        pass
    return 0.5


def _find_subject_x_fraction_from_video(video_path: str, start_sec: float = 0.0) -> float:
    """
    Extract a single representative frame from a video clip and call
    _find_subject_x_fraction on it.  Falls back to 0.5 on any error.
    """
    import tempfile as _tmp
    frame_file = _tmp.mktemp(suffix="_subj.jpg")
    try:
        seek = max(0.0, start_sec)
        r = subprocess.run(
            [FFMPEG_BIN, "-y",
             "-ss", f"{seek:.2f}", "-i", video_path,
             "-frames:v", "1", "-q:v", "3", frame_file],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0 and Path(frame_file).exists():
            frac = _find_subject_x_fraction(frame_file)
            return frac
    except Exception:
        pass
    finally:
        Path(frame_file).unlink(missing_ok=True)
    return 0.5


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
                           out: str, start_sec: float = 0.0) -> bool:
    """
    Trim + scale-crop a video clip to (w,h), no black bars.

    start_sec : where in the source clip to begin (chosen by pick_clip_segment).
    Portrait output (Reels 1080×1920): Qwen-VL determines the crop x so the
    main subject isn't blindly center-cropped out of frame.
    """
    portrait = h > w   # True for Reels (1080×1920)

    if portrait:
        # Landscape → portrait: scale to fill height, then smart-crop a
        # vertical strip.  x_frac from Qwen-VL positions the crop on the subject.
        x_frac  = _find_subject_x_fraction_from_video(src, start_sec)
        x_expr  = f"(iw-{w})*{x_frac:.3f}"
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:{x_expr}:0,setsar=1"
            f"{_fade_suffix(duration)}"
        )
        print(f"    [Crop] portrait video — subject x={x_frac:.2f} "
              f"({'left' if x_frac < 0.4 else 'right' if x_frac > 0.6 else 'center'})")
    else:
        # Landscape → landscape: center crop (small crop, fine as-is)
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1"
            f"{_fade_suffix(duration)}"
        )

    cmd = [
        FFMPEG_BIN, "-y",
        "-ss", str(start_sec),   # seek to Qwen-VL-selected start point
        "-i", src,
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

    For PORTRAIT output (h > w, e.g. Reels 1080×1920): if the source image is
    LANDSCAPE (wider than tall), we first center-crop a vertical strip before
    zoompan — otherwise zoompan would squish/stretch the content to fill the
    portrait frame.

    Upscales to 8000px first (eliminates zoompan jitter per FFmpeg bug #4298).
    """
    n_frames = int(duration * FPS)
    x_expr, y_expr = _PAN_DIRECTIONS[direction_idx % len(_PAN_DIRECTIONS)]

    portrait = h > w  # True for Reels (1080×1920), False for YouTube (1920×1080)
    if portrait:
        # Landscape photo → portrait frame: scale tall, then smart-crop a vertical
        # strip using Qwen-VL's subject position rather than always centering.
        x_frac   = _find_subject_x_fraction(src)
        x_crop   = f"(iw-ih*{w}/{h})*{x_frac:.3f}"
        print(f"    [Crop] portrait photo — subject x={x_frac:.2f} "
              f"({'left' if x_frac < 0.4 else 'right' if x_frac > 0.6 else 'center'})")
        vf = (
            f"scale=-1:8000,"
            f"crop=w='ih*{w}/{h}':h=ih:x='{x_crop}':y=0,"
            f"zoompan=z='min(zoom+0.0015,1.4)':"
            f"x='{x_expr}':y='{y_expr}':"
            f"d={n_frames}:s={w}x{h}:fps={FPS},"
            f"setsar=1"
            f"{_fade_suffix(duration)}"
        )
    else:
        # Landscape → landscape: original path, scale wide and let zoompan crop.
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


# ── Background music ──────────────────────────────────────────────────────────

_MUSIC_DIR = _PROJECT_ROOT / "assets" / "music"
_MUSIC_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}

def _pick_music(seed: str) -> str | None:
    """
    Pick a background track from assets/music/, deterministically per video id
    (so youtube + reels variants — and QA re-renders — get the same track).
    Returns None when the folder is empty; the mix step then skips BGM.
    """
    if not _MUSIC_DIR.exists():
        return None
    tracks = sorted(p for p in _MUSIC_DIR.iterdir()
                    if p.suffix.lower() in _MUSIC_EXTS)
    if not tracks:
        return None
    rng = random.Random(seed)
    return str(rng.choice(tracks))


# Voice loudness target (EBU R128). -16 LUFS is the platform-recommended level
# for online video; without this, TTS output loudness varies run to run.
_LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"

# Ducked BGM graph: music loops under the voice at low volume and dips
# (sidechain compression) whenever the voice speaks.
#
#     voice ── loudnorm ─┬────────────────────────┐
#                        └─(sidechain)─┐          ├─ amix → [aout]
#     music ── volume=0.30 ── sidechaincompress ──┘
_DUCKED_MIX = (
    "[1:a]" + _LOUDNORM + ",asplit=2[vo][vo_sc];"
    "[2:a]volume=0.30[mus];"
    "[mus][vo_sc]sidechaincompress=threshold=0.02:ratio=8:attack=150:release=900[duck];"
    "[vo][duck]amix=inputs=2:duration=first:dropout_transition=2[aout]"
)


# ── Subtitle burn ─────────────────────────────────────────────────────────────

def _corner_label_filter(text: str, video_w: int, video_h: int, tmp_dir: Path) -> str:
    """A small persistent topic badge in the top-left, on every frame, so a viewer
    always knows what the video is about (e.g. 'BEIJING ROAST DUCK')."""
    if not text:
        return ""
    font = _caption_font()
    font_arg = f"fontfile={font.replace(':', chr(92)+':')}:" if font else ""
    fs = max(20, int(video_h * 0.026))
    pad = int(video_w * 0.03)
    lf = tmp_dir / "label.txt"
    lf.write_text(text.upper().strip(), encoding="utf-8")
    tf = str(lf).replace(":", "\\:")
    return (f",drawtext={font_arg}fontsize={fs}:fontcolor=white:borderw=2:"
            f"bordercolor=black@0.9:box=1:boxcolor=black@0.55:boxborderw=10:"
            f"x={pad}:y={pad}:textfile={tf}")


def _hook_overlay_filter(hook_text: str, video_w: int, video_h: int,
                         tmp_dir: Path, duration: float) -> str:
    """
    Large hook title burned over the FIRST seconds of the playing video
    (replaces the old frozen hook card — no dead air, voice starts at t=0).
    Fades out over the last 0.5s of its window. Returns a ','-prefixed
    drawtext chain, or '' when hook_text is empty.
    """
    import textwrap
    if not hook_text:
        return ""
    font = _caption_font()
    font_arg = f"fontfile={font.replace(':', chr(92)+':')}:" if font else ""

    line_h    = int(video_w * 0.058)
    char_w    = 0.46 * line_h
    max_chars = max(10, int((video_w * 0.86) / char_w))
    lines     = textwrap.wrap(hook_text, width=max_chars)
    line_gap  = int(line_h * 1.25)
    total_h   = len(lines) * line_gap
    start_y   = int(video_h * 0.38) - total_h // 2   # upper-middle, clear of subs

    fade_start = duration - 0.5
    alpha = f"if(lt(t\\,{fade_start:.2f})\\,1\\,max(0\\,({duration:.2f}-t)/0.5))"

    parts = []
    for i, line in enumerate(lines):
        lf = tmp_dir / f"hook_{i}.txt"
        lf.write_text(line, encoding="utf-8")
        tf = str(lf).replace(":", "\\:")
        parts.append(
            f"drawtext={font_arg}"
            f"fontsize={line_h}:fontcolor=white:borderw=5:bordercolor=black@0.9:"
            f"box=1:boxcolor=black@0.5:boxborderw=16:"
            f"x=(w-tw)/2:y={start_y + i * line_gap}:"
            f"textfile={tf}:alpha='{alpha}':"
            f"enable='between(t,0,{duration:.2f})'"
        )
    return "," + ",".join(parts)


def _burn_subtitles(raw_path: str, audio_path: str,
                    srt_path: str, out_path: str,
                    subtitle_offset_ms: int = 0,
                    video_w: int = 1920, video_h: int = 1080,
                    params: VideoRenderParams | None = None,
                    corner_label: str = "",
                    hook_text: str = "",
                    music_path: str | None = None,
                    music_offset: float = 0.0) -> None:
    """Attach audio + burn subtitles: raw video → final MP4.

    corner_label: persistent topic badge (top-left) shown on the whole video.
    hook_text:    title overlaid on the first seconds of the video.
    music_path:   optional BGM, looped + ducked under the voice.
    music_offset: start the music this many seconds in (beat alignment).
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

    label_dir = Path(tempfile.mkdtemp(prefix="label_"))
    label_vf  = _corner_label_filter(corner_label, video_w, video_h, label_dir)
    hook_vf   = _hook_overlay_filter(hook_text, video_w, video_h, label_dir,
                                     HOOK_OVERLAY_SECONDS)
    overlays  = label_vf + hook_vf

    # Build the video filter CHAIN (no -vf prefix — it may go into -vf OR into
    # a -filter_complex branch, depending on whether music is mixed in).
    if mode == "copy":
        vf_chain = overlays[1:] if overlays else ""
    elif mode == "libass":
        font_size = max(20, int(video_h * (params.fontsize_pct * 0.8)))
        margin_v  = int(video_h * (1 - params.subtitle_y))
        safe_srt  = active_srt.replace("\\", "/").replace(":", "\\:")
        # Use the bundled Anton via fontsdir so libass matches the drawtext look
        fonts_dir = str(_PROJECT_ROOT / "assets" / "fonts").replace(":", "\\:")
        font_name = "Anton" if (_PROJECT_ROOT / "assets" / "fonts"
                                / "Anton-Regular.ttf").exists() else "Arial Bold"
        vf_chain = (
            f"subtitles={safe_srt}:fontsdir={fonts_dir}:"
            f"force_style='FontName={font_name},FontSize={font_size},Bold=1,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BackColour=&H80000000,"
            f"Outline=2,Shadow=1,BorderStyle=4,"
            f"Alignment=2,MarginV={margin_v}'{overlays}"
        )
    else:
        cue_dir = Path(tempfile.mkdtemp(prefix="cues_"))
        vf_chain = _drawtext_filter(active_srt, cue_dir, video_w, video_h,
                                    fontsize_pct=params.fontsize_pct,
                                    subtitle_y=params.subtitle_y,
                                    max_cue_dur=params.max_cue_dur) + overlays

    # FFmpeg forbids mixing -vf with -filter_complex, so when music is mixed in
    # the video chain moves inside the complex graph as a [0:v]…[vout] branch.
    if music_path:
        music_inputs = ["-stream_loop", "-1"]
        if music_offset > 0:
            music_inputs += ["-ss", f"{music_offset:.3f}"]
        music_inputs += ["-i", music_path]
        if vf_chain:
            graph = f"[0:v]{vf_chain}[vout];{_DUCKED_MIX}"
            filter_args = ["-filter_complex", graph,
                           "-map", "[vout]", "-map", "[aout]"]
            codec_v = "libx264"
        else:
            filter_args = ["-filter_complex", _DUCKED_MIX,
                           "-map", "0:v", "-map", "[aout]"]
            codec_v = "copy"
    else:
        music_inputs = []
        filter_args = (["-vf", vf_chain] if vf_chain else []) + ["-af", _LOUDNORM]
        codec_v = "libx264" if vf_chain else "copy"

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", raw_path,
        "-i", active_audio,
        *music_inputs,
        *filter_args,
        "-c:v", codec_v,
        "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
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
                     fontsize_pct: float = 0.050,
                     subtitle_y: float = 0.77,
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

def assemble_video(video_id: str, media_items, audio_path: str,
                   srt_path: str, hook_text: str = "",
                   scene_durations: list[float] | None = None,
                   params: VideoRenderParams | None = None,
                   corner_label: str = "") -> dict[str, str]:
    """
    Build YouTube (16:9) and Reels (9:16) MP4s from mixed media.

    media_items: list of MediaItem(path, kind) OR list of str (legacy photo-only)
    hook_text:   if provided, burned as a large title overlay on the first
                 HOOK_OVERLAY_SECONDS of the video (voice starts at t=0 —
                 no frozen card, no dead air).
    scene_durations: Phase 1 — per-scene durations from TTS. When provided,
                 segment i is rendered at scene_durations[i] (clamped to
                 [MIN_CLIP_SECONDS, MAX_CLIP_SECONDS]) so visuals stay in sync
                 with narration. When None, falls back to SLIDE_DURATION.
    Returns {"youtube": path, "reels": path}
    """
    from config.settings import MIN_CLIP_SECONDS, MAX_CLIP_SECONDS

    if params is None:
        params = VideoRenderParams()

    # BGM: beat-aligned selection when we know the cut layout (选曲+卡点);
    # random library pick otherwise. Same (track, offset) for both variants.
    music_path, music_offset = None, 0.0
    if scene_durations:
        try:
            from agents.music_agent import pick_music_for_video
            picked = pick_music_for_video(video_id, scene_durations,
                                          topic=corner_label)
            if picked:
                music_path, music_offset = picked
        except Exception as e:
            print(f"  [Music] selector error ({e}) — random pick")
    if music_path is None:
        music_path = _pick_music(video_id)

    # Normalise legacy str list → MediaItem list
    from agents.media_agent import MediaItem
    if media_items and isinstance(media_items[0], str):
        media_items = [MediaItem(p, "photo") for p in media_items]

    if len(media_items) < 2:
        raise ValueError(f"Need ≥2 media items, got {len(media_items)}")

    # Resolve per-segment durations.
    # Aligned durations are ground truth from the voice track — clamping them
    # would desynchronize every later scene, so we only sanity-warn.
    def _seg_duration(i: int) -> float:
        if scene_durations and i < len(scene_durations):
            d = scene_durations[i]
            if d <= 0:
                return SLIDE_DURATION
            if not (MIN_CLIP_SECONDS * 0.5 <= d <= MAX_CLIP_SECONDS * 1.5):
                print(f"    [Video] ⚠️  scene {i} unusual duration {d:.1f}s (kept)")
            return d
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
                    start = getattr(item, "start_sec", 0.0)
                    ok = _make_clip_from_video(item.path, w, h, dur, seg,
                                               start_sec=start)
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

            # ── Step 2: get audio duration ────────────────────────────
            probe = subprocess.run(
                [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", audio_path],
                capture_output=True, text=True,
            )
            audio_dur = float(probe.stdout.strip())

            # Total content duration from what we actually rendered
            content_dur = sum(rendered_durs)

            # Only loop if content is meaningfully shorter than audio.
            # With per-scene durations, content_dur ≈ audio_dur, so this is skipped.
            if content_dur < audio_dur - 1.0:
                avg = content_dur / max(len(segment_paths), 1)
                needed = int((audio_dur - content_dur) / max(avg, 1)) + 1
                extra  = (segment_paths * (needed // max(len(segment_paths), 1) + 1))[:needed]
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
                "-t", str(audio_dur),
                "-c:v", "copy", raw_mp4,
            ], capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(f"Concat failed:\n{r.stderr[-500:].decode()}")

            # Persist the raw (no-subtitle) video so QA remediation can re-burn
            # subtitles with adjusted params WITHOUT re-rendering everything.
            raw_keep = str(out_dir / f"{variant}_raw.mp4")
            subprocess.run([FFMPEG_BIN, "-y", "-i", raw_mp4,
                            "-c:v", "copy", raw_keep], capture_output=True)

            # ── Step 4: audio + subtitles + hook overlay + BGM → final ─
            if music_path:
                print(f"  [Video] 🎵 BGM: {Path(music_path).name} (ducked under voice)")
            _burn_subtitles(raw_mp4, audio_path, srt_path, out_path,
                            video_w=w, video_h=h, params=params,
                            corner_label=corner_label,
                            hook_text=hook_text,
                            music_path=music_path,
                            music_offset=music_offset)

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

    # Hook overlay + BGM are applied at the final-encode stage (not baked into
    # the raw video), so a re-burn must re-apply them. Hook text and cut layout
    # come from the saved state files; the beat-aligned selector is
    # deterministic, so the re-render picks the same (track, offset).
    import json as _json
    hook_text, durations, topic = "", None, ""
    meta = base / "metadata.json"
    if meta.exists():
        try:
            m = _json.loads(meta.read_text())
            hook_text = m.get("hook", "")
            topic     = m.get("topic", "")
        except Exception:
            pass
    state = base / "render_state.json"
    if state.exists():
        try:
            durations = _json.loads(state.read_text()).get("scene_durations")
        except Exception:
            pass

    music_path, music_offset = None, 0.0
    if durations:
        try:
            from agents.music_agent import pick_music_for_video
            picked = pick_music_for_video(video_id, durations, topic=topic)
            if picked:
                music_path, music_offset = picked
        except Exception:
            pass
    if music_path is None:
        music_path = _pick_music(video_id)

    w, h = (YOUTUBE_W, YOUTUBE_H) if variant == "youtube" else (REELS_W, REELS_H)
    print(f"  [Video] Re-burning {variant} subtitles "
          f"(font={params.fontsize_pct:.3f}, y={params.subtitle_y:.2f})…")
    _burn_subtitles(str(raw), str(audio), str(srt), str(out),
                    subtitle_offset_ms=int(hook_seconds * 1000),
                    video_w=w, video_h=h, params=params,
                    hook_text=hook_text,
                    music_path=music_path, music_offset=music_offset)
    return str(out)


def cleanup_raw(video_id: str) -> None:
    """Remove the persisted *_raw.mp4 files once a video is finalised."""
    base = Path(OUTPUT_DIR) / video_id
    for raw in base.glob("*_raw.mp4"):
        raw.unlink(missing_ok=True)
