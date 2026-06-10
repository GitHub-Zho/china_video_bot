"""
Reference Agent — extract frames from B站/YouTube at user-specified timestamps.

Usage (CLI):
    python scripts/run.py --prompt "..." \
        --reference-url "https://www.bilibili.com/video/BV1xxx" \
        --timestamps "2:30,5:15,8:40"

How it fits in:
    1. Before the pipeline, extract_reference_frames() downloads a ~10s segment
       around each timestamp (H264, ~5MB each) and saves one jpg per timestamp.
    2. Those jpgs are passed as `reference_frames` all the way to compete_and_apply().
    3. compete_and_apply() adds them to the scoring pool with kind="reference".
    4. Qwen-VL scores every candidate (stock + AI-generated + reference) against
       the scene's narration and picks the best one.
    5. If no --reference-url is given, nothing changes — AI generation + stock
       work exactly as before.
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
    frames: list[str] = []

    for i, ts in enumerate(timestamps):
        target_secs = _parse_ts(ts)
        half = 6.0
        start = max(0.0, target_secs - half)
        end   = target_secs + half
        offset_in_clip = target_secs - start  # seconds into the downloaded clip

        clip_path  = out_dir / f"ref_clip_{i}.mp4"
        frame_path = out_dir / f"ref_frame_{i}.jpg"

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

        # Extract the frame and blank out the B站/YouTube watermark in the top-left
        # corner (channel name + platform logo). drawbox fills that region black,
        # keeping the original resolution — no aspect-ratio change.
        # Top strip: full width, top 9% of height. Side strip: left 28%, top 12%.
        # This covers 黑麒麟点评 bilibili / YouTube watermarks without cropping content.
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
                    "drawbox=x=0:y=ih*0.84:w=iw:h=ih*0.16:color=black:t=fill"
                ),
                str(frame_path),
            ],
            capture_output=True, timeout=30,
        )
        clip_path.unlink(missing_ok=True)  # keep only the jpg

        if frame_path.exists() and frame_path.stat().st_size > 5_000:
            print(f"  [Ref] ✅ frame {i} extracted → {frame_path.name}")
            frames.append(str(frame_path))
        else:
            print(f"  [Ref] ⚠️  frame {i} extraction failed")
            frame_path.unlink(missing_ok=True)

    print(f"  [Ref] {len(frames)}/{len(timestamps)} reference frames ready")
    return frames
