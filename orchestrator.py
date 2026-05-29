"""
Orchestrator — full pipeline for one video.
script → images → voice+srt → video → upload → analytics
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

from agents.script_agent    import generate_script
from agents.image_agent     import download_images
from agents.voice_agent     import generate_voice       # returns (audio_path, srt_path)
from agents.video_agent     import assemble_video
from agents.publisher_agent import upload_video
from agents.analytics_agent import run_pending_analytics
from config.settings        import OUTPUT_DIR


def _video_id() -> str:
    return datetime.now().strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:6]


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

    # ── 1. Script ─────────────────────────────────────────
    print("\n[1/5] Generating script…")
    script_data = generate_script(audience_type)
    print(f"      Topic    : {script_data['topic']}")
    print(f"      Audience : {script_data['audience_type']}")
    # Save metadata alongside output
    out_dir = Path(OUTPUT_DIR) / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(json.dumps(script_data, indent=2))

    # ── 2. Images ─────────────────────────────────────────
    print("\n[2/5] Downloading images…")
    image_paths = download_images(vid, script_data["image_queries"])
    if len(image_paths) < 2:
        raise RuntimeError(
            f"Only {len(image_paths)} image(s) downloaded — check Pexels/Unsplash keys."
        )
    print(f"      {len(image_paths)} images ready")

    # ── 3. Voice + Subtitles (one pass) ───────────────────
    print("\n[3/5] Generating voiceover + subtitles…")
    audio_path, srt_path = generate_voice(vid, script_data["script"])

    # ── 4. Video assembly ─────────────────────────────────
    print("\n[4/5] Assembling video (1-3 min)…")
    video_paths = assemble_video(vid, image_paths, audio_path, srt_path)

    if dry_run:
        print(f"\n✅ DRY RUN — videos saved locally (not uploaded):")
        for k, v in video_paths.items():
            print(f"   {k}: {v}")
        return video_paths.get("youtube", "")

    # ── 5. Publish ────────────────────────────────────────
    print("\n[5/5] Uploading to YouTube…")
    yt_id = upload_video(video_paths["youtube"], script_data)

    # Analytics sweep (non-blocking)
    n = run_pending_analytics()
    if n:
        print(f"  [+] Analytics collected for {n} previous video(s)")

    print(f"\n✅ Done!  https://www.youtube.com/watch?v={yt_id}\n")
    return yt_id
