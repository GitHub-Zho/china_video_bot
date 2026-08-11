"""Manual, historical Pexels video-vs-photo smoke experiment.

This is not part of the production pipeline or automated test suite. It relies
on artifacts from the 2026-05-30 run and is retained only for reproducibility.
Run it explicitly from the repository root when those artifacts are present.
"""
import os, requests, subprocess, tempfile, time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
PEXELS_KEY   = os.getenv("PEXELS_API_KEY")
AUDIO_PATH   = "output/20260530_be9594/audio.mp3"
SRT_PATH     = "output/20260530_be9594/subtitles.srt"
OUTPUT_PATH  = "output/test_clips.mp4"
CLIP_DIR     = Path("output/test_clips_raw")
W, H         = 1920, 1080
CLIP_SECONDS = 5          # seconds per clip
QUERIES = [
    "Zhangjiajie mountains mist",
    "Shanghai skyline night",
    "Great Wall of China aerial",
    "Chinese red lanterns",
    "Li River Guilin boats",
]

CLIP_DIR.mkdir(parents=True, exist_ok=True)

# ── 1. Download video clips from Pexels ──────────────────────────────────────
def fetch_clip(query: str, idx: int) -> str | None:
    dest = CLIP_DIR / f"{idx:02d}.mp4"
    if dest.exists() and dest.stat().st_size > 50_000:
        print(f"  [skip] {idx:02d} already downloaded")
        return str(dest)

    url = "https://api.pexels.com/videos/search"
    params = {"query": f"China {query}", "orientation": "landscape",
              "size": "large", "per_page": 5}
    headers = {"Authorization": PEXELS_KEY}

    r = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers,
                             timeout=15, verify=False)
            break
        except Exception as e:
            print(f"  [retry {attempt+1}] API error: {e}")
            time.sleep(2)
    if r is None or r.status_code != 200:
        print(f"  [!] Pexels API failed for '{query}'")
        return None

    videos = r.json().get("videos", [])
    if not videos:
        print(f"  [!] No results for '{query}'")
        return None

    # Pick best HD clip (prefer 1080p landscape)
    best = None
    for video in videos:
        for f in video.get("video_files", []):
            if f.get("quality") == "hd" and f.get("width", 0) >= 1280:
                if best is None or f.get("width", 0) > best.get("width", 0):
                    best = f
    if not best:
        best = videos[0]["video_files"][0]   # fallback: first file

    print(f"  [{idx:02d}] Downloading: '{query}' ({best.get('width')}x{best.get('height')})…")
    # Try with SSL verify, fall back without (Mac SSL quirk)
    for verify in (True, False):
        try:
            r2 = requests.get(best["link"], stream=True,
                              timeout=60, verify=verify)
            with open(dest, "wb") as fh:
                for chunk in r2.iter_content(65536):
                    fh.write(chunk)
            if dest.stat().st_size > 50_000:
                break
        except Exception as e:
            if not verify:
                print(f"  [!] Download failed: {e}")
                return None
    time.sleep(0.4)
    return str(dest)

print("\n[1/3] Downloading video clips from Pexels…")
clips = []
for i, q in enumerate(QUERIES):
    path = fetch_clip(q, i)
    if path:
        clips.append(path)

if len(clips) < 2:
    print("❌ Not enough clips downloaded. Check API key or network.")
    exit(1)
print(f"  {len(clips)} clips ready")

# ── 2. Trim + normalise each clip to 1920×1080, CLIP_SECONDS long ────────────
print("\n[2/3] Trimming + normalising clips…")
trimmed = []
for i, clip in enumerate(clips):
    out = str(CLIP_DIR / f"trim_{i:02d}.mp4")
    if Path(out).exists() and Path(out).stat().st_size > 10_000:
        trimmed.append(out)
        continue

    # scale-to-fill (crop, no black bars) + trim to CLIP_SECONDS
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"setsar=1"
    )
    cmd = [
        "ffmpeg", "-y", "-i", clip,
        "-t", str(CLIP_SECONDS),
        "-vf", vf,
        "-r", "25",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-an",                    # strip audio from clip
        "-pix_fmt", "yuv420p",
        out
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print(f"  [!] trim failed for clip {i}: {r.stderr[-300:].decode()}")
    else:
        trimmed.append(out)

print(f"  {len(trimmed)} clips trimmed")

# ── 3. Concatenate clips + attach audio ──────────────────────────────────────
print("\n[3/3] Assembling final video…")

# Write FFmpeg concat list
list_file = CLIP_DIR / "concat.txt"
list_file.write_text("\n".join(f"file '{Path(t).absolute()}'" for t in trimmed))

# Get audio duration so we know how long to loop clips
probe = subprocess.run(
    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
     "-of", "csv=p=0", AUDIO_PATH],
    capture_output=True, text=True
)
audio_dur = float(probe.stdout.strip())
video_dur = len(trimmed) * CLIP_SECONDS
print(f"  Audio: {audio_dur:.1f}s  |  Video clips: {video_dur}s")

# Concat clips (loop if needed to match audio)
concat_mp4 = str(CLIP_DIR / "concat_raw.mp4")
loop_count = max(1, int(audio_dur / video_dur) + 1)
cmd = [
    "ffmpeg", "-y",
    "-stream_loop", str(loop_count),
    "-f", "concat", "-safe", "0", "-i", str(list_file),
    "-t", str(audio_dur),
    "-c:v", "copy",
    concat_mp4
]
r = subprocess.run(cmd, capture_output=True)
if r.returncode != 0:
    print("concat failed:", r.stderr[-500:].decode())
    exit(1)

# Attach audio + add subtitles (stream-copy fallback on Mac)
cmd_final = [
    "ffmpeg", "-y",
    "-i", concat_mp4,
    "-i", AUDIO_PATH,
    "-c:v", "copy",
    "-c:a", "aac",
    "-shortest",
    "-movflags", "+faststart",
    OUTPUT_PATH
]
r = subprocess.run(cmd_final, capture_output=True)
if r.returncode != 0:
    print("final mux failed:", r.stderr[-500:].decode())
    exit(1)

size_kb = Path(OUTPUT_PATH).stat().st_size // 1024
print(f"\n✅ Done!  {OUTPUT_PATH}  ({size_kb} KB)")
print("\nNow compare:")
print(f"  OLD (photos) : output/20260530_be9594/youtube.mp4")
print(f"  NEW (clips)  : {OUTPUT_PATH}")
print("\nOpening both in Finder…")
subprocess.run(["open", "-R", OUTPUT_PATH])
