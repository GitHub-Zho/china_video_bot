"""
Orchestrator — full pipeline for one video.
Director → Media → Voice → Video → Upload → Analytics
"""
import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from agents.director_agent   import create_brief
from agents.media_agent      import download_media
from agents.voice_agent      import generate_voice
from agents.video_agent      import assemble_video
from agents.publisher_agent  import upload_video
from agents.analytics_agent  import run_pending_analytics, extract_insights
from config.settings         import OUTPUT_DIR


def _video_id() -> str:
    return datetime.now().strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:6]


def _probe_duration(path: str) -> float:
    """Return duration in seconds via ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def _quality_check(video_path: str, audio_path: str) -> bool:
    """
    Post-generation sanity check.
    Returns True if video passes; prints warnings but never raises.
    """
    ok = True
    try:
        vpath = Path(video_path)
        if not vpath.exists():
            print(f"  [QC] ❌ Video file missing: {video_path}")
            return False

        size_mb = vpath.stat().st_size / 1_048_576
        if size_mb < 0.5:
            print(f"  [QC] ⚠️  Video suspiciously small: {size_mb:.1f} MB")
            ok = False
        else:
            print(f"  [QC] ✅ File size: {size_mb:.1f} MB")

        vid_dur   = _probe_duration(video_path)
        audio_dur = _probe_duration(audio_path)
        diff      = abs(vid_dur - audio_dur)

        if diff > 2.0:
            print(f"  [QC] ⚠️  Duration mismatch — video {vid_dur:.1f}s vs audio {audio_dur:.1f}s "
                  f"(Δ{diff:.1f}s)")
            ok = False
        else:
            print(f"  [QC] ✅ Duration: {vid_dur:.1f}s (audio {audio_dur:.1f}s, Δ{diff:.1f}s)")

        # Quick stream check — ffprobe will error if the file is corrupted
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", video_path],
            capture_output=True, text=True,
        )
        codec = r.stdout.strip()
        if r.returncode != 0 or not codec:
            print(f"  [QC] ⚠️  Cannot read video stream — file may be corrupt")
            ok = False
        else:
            print(f"  [QC] ✅ Video codec: {codec}")

    except Exception as e:
        print(f"  [QC] ⚠️  Quality check error: {e}")
        ok = False

    return ok


def run_pipeline(audience_type: str = None, dry_run: bool = False) -> str:
    """
    Full pipeline for one video.
    dry_run=True  → assembles video locally, skips YouTube upload.
    Returns YouTube video_id string (or local mp4 path if dry_run).
    """
    vid = _video_id()
    print(f"\n{'='*55}")
    print(f"  China Video Bot  ·  {vid}")
    print(f"{'='*55}")

    # ── 1. Director: plan the entire video scene-by-scene ────
    print("\n[1/5] Director planning scenes…")
    brief = create_brief(audience_type)
    print(f"      Topic    : {brief.topic}")
    print(f"      Audience : {brief.audience_type}")
    print(f"      Scenes   : {len(brief.scenes)} × {brief.scenes[0].duration:.0f}s "
          f"= {brief.target_seconds:.0f}s target")

    # Save metadata
    out_dir = Path(OUTPUT_DIR) / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(
        json.dumps(brief.to_metadata_dict(), indent=2)
    )

    # ── 2. Media: download clips/photos for each scene ───────
    print("\n[2/5] Downloading media…")
    visual_queries = [s.visual_query for s in brief.scenes]
    media_items = download_media(vid, visual_queries)
    if len(media_items) < 2:
        raise RuntimeError(
            f"Only {len(media_items)} media item(s) downloaded — check API keys."
        )
    clips  = sum(1 for m in media_items if m.kind == "clip")
    photos = sum(1 for m in media_items if m.kind == "photo")
    print(f"      {len(media_items)} items ready ({clips} clips, {photos} photos)")

    # ── 3. Voice + Subtitles ─────────────────────────────────
    print("\n[3/5] Generating voiceover + subtitles…")
    audio_path, srt_path = generate_voice(vid, brief.script)

    audio_dur = _probe_duration(audio_path)
    print(f"      Audio: {audio_dur:.1f}s  |  Script: {len(brief.script.split())} words")

    # ── 4. Video assembly ─────────────────────────────────────
    print("\n[4/5] Assembling video…")
    video_paths = assemble_video(vid, media_items, audio_path, srt_path)

    # Quality check
    print("\n  [QC] Running post-generation checks…")
    yt_path = video_paths.get("youtube", "")
    qc_pass = _quality_check(yt_path, audio_path) if yt_path else False
    if not qc_pass:
        print("  [QC] ⚠️  Quality check flagged issues — review output before uploading")

    if dry_run:
        print(f"\n✅ DRY RUN — videos saved locally (not uploaded):")
        for k, v in video_paths.items():
            print(f"   {k}: {v}")
        return video_paths.get("youtube", "")

    # ── 5. Publish ────────────────────────────────────────────
    print("\n[5/5] Uploading to YouTube…")
    metadata = brief.to_metadata_dict()
    yt_id = upload_video(video_paths["youtube"], metadata)

    # Analytics sweep (non-blocking — collects data for videos 3+ days old)
    n = run_pending_analytics()
    if n:
        print(f"  [+] Analytics collected for {n} previous video(s)")
        extract_insights()   # refresh data/insights.json for next Director run

    print(f"\n✅ Done!  https://www.youtube.com/watch?v={yt_id}\n")
    return yt_id
