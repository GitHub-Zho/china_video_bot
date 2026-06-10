"""
Reference Agent — extract frames + clips from B站/YouTube for use as real footage.

Two modes:

1. TIME RANGE (recommended):
   --reference-url URL --time-range "7:40-8:10"
   Downloads the full range once, samples every 2s, produces N frame+clip pairs.
   Qwen-VL picks the best match per scene from the whole pool.
   Best for: "I know this segment has what I need, let the system pick"

2. SPECIFIC TIMESTAMPS (manual):
   --reference-url URL --timestamps "7:48,8:01,8:06"
   Downloads a 12s window around each timestamp.
   Best for: precise control over exact moments.

How it fits in the pipeline:
    1. extract_reference_from_range() or extract_reference_frames() produces
       (jpg, mp4) pairs — jpg for Qwen-VL scoring, mp4 for the final video.
    2. Those jpg paths are passed as `reference_frames` to compete_and_apply().
    3. compete_and_apply() adds them to the scoring pool (kind="reference").
    4. Qwen-VL scores all candidates; when a reference wins, the matching mp4
       is copied as a real video clip — no Ken Burns, real motion footage.
    5. If no --reference-url is given, nothing changes.
"""
import subprocess
from pathlib import Path

from config.settings import FFMPEG_BIN


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> float:
    """Parse 'H:MM:SS', 'M:SS', or raw seconds string → float seconds."""
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(ts)


def _fmt_ts(secs: float) -> str:
    """Float seconds → 'HH:MM:SS' for yt-dlp --download-sections."""
    secs = max(0.0, secs)
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Core extraction ───────────────────────────────────────────────────────────

_REF_CACHE_DIR = Path("output/ref_cache")


def _cache_key(url: str, ts: str) -> str:
    """Stable filename for a (url, timestamp) pair — avoids re-downloading."""
    import hashlib
    return hashlib.md5(f"{url}|{ts}".encode()).hexdigest()[:12]


def extract_reference_frames(url: str,
                             timestamps: list[str],
                             out_dir: Path,
                             browser: str = "chrome") -> list[str]:
    """
    For each timestamp string (e.g. "2:30", "00:05:15", "312"):
      1. Download a 12-second H264 segment centred on that timestamp (~5 MB).
      2. Extract a single high-quality frame at the centre point.
      3. Delete the clip; keep only the jpg.

    Returns a list of absolute jpg paths for frames that succeeded.
    All frames are put into the same folder — compete_and_apply() will score
    all of them against every scene and pick the best match per scene.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _REF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frames: list[str] = []

    for i, ts in enumerate(timestamps):
        target_secs = _parse_ts(ts)
        half = 6.0
        start = max(0.0, target_secs - half)
        end   = target_secs + half
        offset_in_clip = target_secs - start  # seconds into the downloaded clip

        # Check persistent cache first — avoids re-downloading same URL+timestamp
        cache_key   = _cache_key(url, ts)
        cached_frame = _REF_CACHE_DIR / f"{cache_key}.jpg"
        frame_path   = out_dir / f"ref_frame_{i}.jpg"

        if cached_frame.exists() and cached_frame.stat().st_size > 5_000:
            import shutil as _sh
            _sh.copy(cached_frame, frame_path)
            # Also restore the video clip from cache (if present) so the final video
            # can use the actual video segment, not just a static frame.
            cached_mp4 = _REF_CACHE_DIR / f"{cache_key}.mp4"
            if cached_mp4.exists() and cached_mp4.stat().st_size > 10_000:
                _sh.copy(cached_mp4, out_dir / f"ref_frame_{i}.mp4")
            print(f"  [Ref] timestamp {ts} → cache hit ✅")
            frames.append(str(frame_path))
            continue

        clip_path = out_dir / f"ref_clip_{i}.mp4"
        print(f"  [Ref] timestamp {ts} → downloading {_fmt_ts(start)}-{_fmt_ts(end)} …")

        dl = subprocess.run(
            [
                "yt-dlp",
                "--cookies-from-browser", browser,
                "--download-sections", f"*{_fmt_ts(start)}-{_fmt_ts(end)}",
                "-f", "bestvideo[vcodec^=avc]+bestaudio/best[vcodec^=avc]",
                "--merge-output-format", "mp4",
                "-o", str(clip_path),
                "--quiet", "--no-warnings",
                url,
            ],
            capture_output=True, timeout=120,
        )

        if not clip_path.exists() or clip_path.stat().st_size < 10_000:
            print(f"  [Ref] ⚠️  clip {i} download failed — skipping")
            clip_path.unlink(missing_ok=True)
            continue

        # ── Step 1: blank watermarks on the clip itself (for use as a video in final video)
        # Replace the 12-second clip with a watermark-free version so when it's used
        # as a video segment in the final video, the B站/YouTube logo is already gone.
        clean_path = out_dir / f"ref_clip_{i}_clean.mp4"
        wm_clean = subprocess.run(
            [
                FFMPEG_BIN, "-y", "-i", str(clip_path),
                "-vf", (
                    "drawbox=x=0:y=0:w=iw*0.42:h=ih*0.10:color=black:t=fill,"
                    "drawbox=x=0:y=ih*0.80:w=iw:h=ih*0.20:color=black:t=fill"
                ),
                "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                "-c:a", "copy",
                str(clean_path),
            ],
            capture_output=True, timeout=60,
        )
        if clean_path.exists() and clean_path.stat().st_size > 10_000:
            clip_path.unlink(missing_ok=True)
            clip_path = clean_path  # use the watermark-free version

        # ── Step 2: extract one representative frame for Qwen-VL scoring
        # (The frame is used to JUDGE which scene this clip matches best.
        #  The actual video clip is what goes into the final video.)
        ex = subprocess.run(
            [
                FFMPEG_BIN, "-y",
                "-ss", str(offset_in_clip),
                "-i", str(clip_path),
                "-frames:v", "1",
                "-vf", (
                    "scale=960:-1,"
                    # top-left block only — covers B站/YouTube channel watermark
                    # (smaller area so less content is lost)
                    "drawbox=x=0:y=0:w=iw*0.42:h=ih*0.10:color=black:t=fill,"
                    # bottom strip — covers burned-in subtitles from the source video
                    # 20% coverage (y=80%) needed to fully cover B站 subtitle area
                    "drawbox=x=0:y=ih*0.80:w=iw:h=ih*0.20:color=black:t=fill"
                ),
                str(frame_path),
            ],
            capture_output=True, timeout=30,
        )

        if frame_path.exists() and frame_path.stat().st_size > 5_000:
            # ── Trim + persist BOTH frame and clip to cache ───────────────────
            # Trim the 12s clip to a 7s window starting 2s before the target
            # moment — so _make_clip_from_video takes the first N seconds and
            # naturally captures the key action.
            import shutil as _sh
            cached_mp4 = _REF_CACHE_DIR / f"{cache_key}.mp4"
            if clip_path.exists() and clip_path.stat().st_size > 10_000:
                trim_start = max(0.0, offset_in_clip - 2.0)
                ref_clip_final = out_dir / f"ref_frame_{i}.mp4"
                trim_result = subprocess.run(
                    [
                        FFMPEG_BIN, "-y",
                        "-ss", str(trim_start), "-i", str(clip_path),
                        "-t", "7",    # 7s centred on the key moment
                        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                        "-c:a", "copy",
                        str(ref_clip_final),
                    ],
                    capture_output=True, timeout=30,
                )
                clip_path.unlink(missing_ok=True)
                if ref_clip_final.exists() and ref_clip_final.stat().st_size > 10_000:
                    _sh.copy(frame_path, cached_frame)
                    _sh.copy(str(ref_clip_final), cached_mp4)
                    print(f"  [Ref] ✅ frame+clip {i} → {frame_path.name} + {ref_clip_final.name}")
                else:
                    _sh.copy(frame_path, cached_frame)
                    print(f"  [Ref] ✅ frame {i} → {frame_path.name} (trim failed)")
            else:
                clip_path.unlink(missing_ok=True)
                _sh.copy(frame_path, cached_frame)
                print(f"  [Ref] ✅ frame {i} → {frame_path.name}")
            frames.append(str(frame_path))
        else:
            print(f"  [Ref] ⚠️  frame {i} extraction failed")
            frame_path.unlink(missing_ok=True)
            clip_path.unlink(missing_ok=True)

    print(f"  [Ref] {len(frames)}/{len(timestamps)} reference frames ready")
    return frames


# ── Time-range mode ───────────────────────────────────────────────────────────

def extract_reference_from_range(url: str,
                                  time_range: str,
                                  out_dir: Path,
                                  sample_interval: float = 2.5,
                                  browser: str = "chrome") -> list[str]:
    """
    Download a continuous time range (e.g. "7:40-8:10") once, sample a frame
    every `sample_interval` seconds, and produce (jpg + 6s mp4) pairs.

    This is the preferred mode when the user knows which section of a video is
    relevant but doesn't want to specify exact timestamps manually.  The system
    samples the whole segment; Qwen-VL picks the best match per scene.

    Returns: list of jpg paths (each has a companion .mp4 with the same stem).
    """
    import hashlib, shutil as _sh

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _REF_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Parse "7:40-8:10" → start_s, end_s ───────────────────────────────────
    parts = time_range.strip().split("-")
    if len(parts) != 2:
        print(f"  [Ref] ⚠️  invalid time_range '{time_range}' — expected 'M:SS-M:SS'")
        return []
    start_s = _parse_ts(parts[0].strip())
    end_s   = _parse_ts(parts[1].strip())
    duration_s = end_s - start_s
    if duration_s <= 0:
        print(f"  [Ref] ⚠️  time_range '{time_range}' has zero/negative duration")
        return []

    # ── Cache the whole downloaded range clip ─────────────────────────────────
    range_key   = hashlib.md5(f"{url}|range:{time_range}".encode()).hexdigest()[:12]
    cached_clip = _REF_CACHE_DIR / f"{range_key}_range.mp4"

    if cached_clip.exists() and cached_clip.stat().st_size > 50_000:
        print(f"  [Ref] range {time_range} → cache hit ✅ ({cached_clip.stat().st_size//1024}KB)")
        range_clip = out_dir / "ref_range.mp4"
        _sh.copy(cached_clip, range_clip)
    else:
        range_clip = out_dir / "ref_range_raw.mp4"
        print(f"  [Ref] range {time_range} → downloading {_fmt_ts(start_s)}-{_fmt_ts(end_s)} "
              f"({duration_s:.0f}s) …")
        dl = subprocess.run(
            [
                "yt-dlp",
                "--cookies-from-browser", browser,
                "--download-sections", f"*{_fmt_ts(start_s)}-{_fmt_ts(end_s)}",
                "-f", "bestvideo[vcodec^=avc]+bestaudio/best[vcodec^=avc]",
                "--merge-output-format", "mp4",
                "-o", str(range_clip),
                "--quiet", "--no-warnings",
                url,
            ],
            capture_output=True, timeout=180,
        )
        if not range_clip.exists() or range_clip.stat().st_size < 10_000:
            print(f"  [Ref] ⚠️  range download failed")
            range_clip.unlink(missing_ok=True)
            return []

        # ── Apply watermark removal on the whole clip ─────────────────────────
        clean_clip = out_dir / "ref_range.mp4"
        subprocess.run(
            [
                FFMPEG_BIN, "-y", "-i", str(range_clip),
                "-vf", (
                    "drawbox=x=0:y=0:w=iw*0.42:h=ih*0.10:color=black:t=fill,"
                    "drawbox=x=0:y=ih*0.80:w=iw:h=ih*0.20:color=black:t=fill"
                ),
                "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "copy",
                str(clean_clip),
            ],
            capture_output=True, timeout=120,
        )
        range_clip.unlink(missing_ok=True)
        if not clean_clip.exists() or clean_clip.stat().st_size < 10_000:
            print(f"  [Ref] ⚠️  watermark removal failed")
            return []
        range_clip = clean_clip
        _sh.copy(range_clip, cached_clip)   # cache for future runs
        print(f"  [Ref] ✅ range downloaded + watermarks removed ({range_clip.stat().st_size//1024}KB)")

    # ── Sample frames every sample_interval seconds ───────────────────────────
    # Probe actual clip duration (may differ slightly from requested range)
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(range_clip)],
        capture_output=True, text=True,
    )
    try:
        actual_dur = float(probe.stdout.strip())
    except Exception:
        actual_dur = duration_s

    offsets = []
    t = 1.0   # start 1s in (avoid fade-in / cold start)
    while t < actual_dur - 1.0:
        offsets.append(t)
        t += sample_interval

    print(f"  [Ref] sampling {len(offsets)} frames every {sample_interval}s from {actual_dur:.1f}s clip")

    frames: list[str] = []
    for i, offset in enumerate(offsets):
        # ── Check per-frame cache ─────────────────────────────────────────────
        frame_key    = hashlib.md5(f"{url}|range:{time_range}|f{i}".encode()).hexdigest()[:12]
        cached_frame = _REF_CACHE_DIR / f"{frame_key}.jpg"
        cached_mp4   = _REF_CACHE_DIR / f"{frame_key}.mp4"
        frame_path   = out_dir / f"ref_frame_{i}.jpg"
        clip_path    = out_dir / f"ref_frame_{i}.mp4"

        if cached_frame.exists() and cached_mp4.exists():
            _sh.copy(cached_frame, frame_path)
            _sh.copy(cached_mp4,   clip_path)
            frames.append(str(frame_path))
            continue

        # ── Extract jpg for Qwen-VL scoring ───────────────────────────────────
        subprocess.run(
            [FFMPEG_BIN, "-y", "-ss", str(offset), "-i", str(range_clip),
             "-frames:v", "1", "-vf", "scale=960:-1", str(frame_path)],
            capture_output=True, timeout=15,
        )
        if not frame_path.exists() or frame_path.stat().st_size < 1_000:
            continue

        # ── Extract 6s video clip starting 1s before this frame ──────────────
        trim_start = max(0.0, offset - 1.0)
        subprocess.run(
            [FFMPEG_BIN, "-y", "-ss", str(trim_start), "-i", str(range_clip),
             "-t", "6", "-c:v", "libx264", "-crf", "20", "-preset", "fast",
             "-c:a", "copy", str(clip_path)],
            capture_output=True, timeout=20,
        )
        if not clip_path.exists() or clip_path.stat().st_size < 5_000:
            clip_path.unlink(missing_ok=True)

        # ── Persist to cache ──────────────────────────────────────────────────
        _sh.copy(frame_path, cached_frame)
        if clip_path.exists():
            _sh.copy(clip_path, cached_mp4)

        frames.append(str(frame_path))

    print(f"  [Ref] {len(frames)}/{len(offsets)} frames+clips ready from range {time_range}")
    return frames
