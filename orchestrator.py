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
from agents.voice_agent      import generate_voice, generate_voice_scenes
from agents.video_agent      import assemble_video
from agents.publisher_agent  import upload_video
from agents.analytics_agent  import run_pending_analytics, extract_insights
from agents.qa_agent         import qa_check, adjust_params_from_qa
from agents.video_agent      import VideoRenderParams, rerender_subtitles, cleanup_raw
from config.settings         import OUTPUT_DIR, FFPROBE_BIN, HOOK_CARD_SECONDS


def _video_id() -> str:
    return datetime.now().strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:6]


def _probe_duration(path: str) -> float:
    """Return duration in seconds via ffprobe."""
    r = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def _quality_check(video_path: str, audio_path: str,
                   hook_seconds: float = 0.0) -> bool:
    """
    Post-generation sanity check.
    Returns True if video passes; prints warnings but never raises.

    hook_seconds: the hook card adds silent video time at the start, so the
                  expected video duration = audio + hook_seconds.
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
        expected  = audio_dur + hook_seconds
        diff      = abs(vid_dur - expected)

        if diff > 2.0:
            print(f"  [QC] ⚠️  Duration mismatch — video {vid_dur:.1f}s vs "
                  f"expected {expected:.1f}s (audio {audio_dur:.1f}s + hook {hook_seconds:.0f}s, "
                  f"Δ{diff:.1f}s)")
            ok = False
        else:
            print(f"  [QC] ✅ Duration: {vid_dur:.1f}s "
                  f"(audio {audio_dur:.1f}s + hook {hook_seconds:.0f}s, Δ{diff:.1f}s)")

        # Quick stream check — ffprobe will error if the file is corrupted
        r = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
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


def _qa_and_remediate(vid: str, video_paths: dict) -> None:
    """
    Phase 3 — QA the video, and if the verifier flags fixable subtitle issues,
    adjust render params for THIS video and re-burn subtitles on both variants
    (no full re-render). Bounded to one remediation pass.
    """
    yt_path = video_paths.get("youtube", "")
    if not yt_path:
        return

    report = qa_check(yt_path, hook_seconds=HOOK_CARD_SECONDS)

    params = VideoRenderParams()
    new_params, changed = adjust_params_from_qa(report, params)
    if not changed:
        cleanup_raw(vid)
        return

    # Re-burn subtitles only (no full re-render). One bounded pass — we trust the
    # rule-based adjustment rather than burning another vision call to re-verify.
    print(f"  [QC] Auto-remediation: re-burning subtitles for this video "
          f"(font={new_params.fontsize_pct:.3f}, y={new_params.subtitle_y:.2f})…")
    for variant in ("youtube", "reels"):
        rerender_subtitles(vid, variant, new_params, hook_seconds=HOOK_CARD_SECONDS)
    print("  [QC] ✅ Remediation applied")
    cleanup_raw(vid)


def run_pipeline(audience_type: str = None, dry_run: bool = False,
                 prompt: str = "", style: str = "") -> str:
    """
    Full pipeline for one video.
    dry_run=True  → assembles video locally, skips YouTube upload.
    prompt        → free-text creative direction (e.g. "Xi'an Terracotta Warriors").
    style         → name of a saved StyleProfile to imitate (Phase 5).
    Returns YouTube video_id string (or local mp4 path if dry_run).
    """
    vid = _video_id()
    print(f"\n{'='*55}")
    print(f"  China Video Bot  ·  {vid}")
    print(f"{'='*55}")

    # ── 0. Optional style profile ────────────────────────────
    render_params = None
    if style:
        from agents.style_analyst_agent import load_style
        sp = load_style(style)
        if sp:
            hints = sp.to_render_hints()
            render_params = VideoRenderParams(
                fontsize_pct=hints["fontsize_pct"],
                subtitle_y=hints["subtitle_y"],
            )
            print(f"  Style    : '{style}' ({sp.color_mood}, {sp.subtitle_size} subs, "
                  f"~{sp.avg_clip_seconds}s/shot)")
        else:
            print(f"  ⚠️  Style '{style}' not found — using defaults")

    # ── 1. Director: plan the entire video scene-by-scene ────
    print("\n[1/5] Director planning scenes…")
    brief = create_brief(audience_type, prompt=prompt)
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

    # ── 2. Voice + Subtitles (BEFORE media — Phase 1) ────────
    # TTS runs first so we know each scene's exact spoken duration. The video
    # assembler then sizes each clip to its narration, keeping visuals in sync.
    print("\n[2/5] Generating voiceover + subtitles…")
    narrations = [s.narration for s in brief.scenes]
    audio_path, srt_path, scene_durations = generate_voice_scenes(vid, narrations)
    audio_dur = _probe_duration(audio_path)
    print(f"      Audio: {audio_dur:.1f}s  |  Script: {len(brief.script.split())} words")

    # ── 3. Media: download clips/photos for each scene ───────
    print("\n[3/5] Downloading media…")
    visual_queries = [s.visual_query for s in brief.scenes]
    media_items = download_media(vid, visual_queries)
    if len(media_items) < 2:
        raise RuntimeError(
            f"Only {len(media_items)} media item(s) downloaded — check API keys."
        )
    clips  = sum(1 for m in media_items if m.kind == "clip")
    photos = sum(1 for m in media_items if m.kind == "photo")
    print(f"      {len(media_items)} items ready ({clips} clips, {photos} photos)")

    # ── 4. Video assembly (scene-timed) ──────────────────────
    print("\n[4/5] Assembling video…")
    video_paths = assemble_video(vid, media_items, audio_path, srt_path,
                                  hook_text=brief.hook,
                                  scene_durations=scene_durations,
                                  params=render_params)

    # ── Quality check + auto-remediation (Phase 3) ───────────────
    print("\n  [QC] Running post-generation checks…")
    yt_path = video_paths.get("youtube", "")
    if yt_path:
        _quality_check(yt_path, audio_path, hook_seconds=HOOK_CARD_SECONDS)
        _qa_and_remediate(vid, video_paths)

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


def run_pipeline_from_folder(
    folder_path: str,
    dry_run: bool = False,
    target_seconds: float | None = None,
    style: str = "",
) -> str:
    """
    Alternate pipeline: user provides their own images/clips.

    Instead of Groq + Pexels, this mode:
      1. Reads all images/clips from folder_path
      2. Sends them to Claude Vision for scene-by-scene analysis
      3. Auto-generates narration matched to each photo/clip
      4. Assembles video with the same Voice + Video + Publish pipeline

    Great for: food photos, personal travel photos, event footage.

    Returns YouTube video_id (or local mp4 path if dry_run).
    """
    from agents.media_analyst_agent import analyse_folder

    vid = _video_id()
    print(f"\n{'='*55}")
    print(f"  China Video Bot (from folder)  ·  {vid}")
    print(f"  Folder: {folder_path}")
    print(f"{'='*55}")

    render_params = None
    if style:
        from agents.style_analyst_agent import load_style
        sp = load_style(style)
        if sp:
            hints = sp.to_render_hints()
            render_params = VideoRenderParams(fontsize_pct=hints["fontsize_pct"],
                                              subtitle_y=hints["subtitle_y"])
            print(f"  Style    : '{style}'")

    # ── 1. Analyse user media with Claude Vision ──────────────
    print("\n[1/5] Analysing your media with Claude Vision…")
    brief, media_items = analyse_folder(folder_path, target_seconds)
    print(f"      Topic    : {brief.topic}")
    print(f"      Audience : {brief.audience_type}")
    print(f"      Scenes   : {len(brief.scenes)}")

    out_dir = Path(OUTPUT_DIR) / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(
        json.dumps(brief.to_metadata_dict(), indent=2)
    )

    # ── 2. (Media already provided — no download needed) ─────
    clips  = sum(1 for m in media_items if m.kind == "clip")
    photos = sum(1 for m in media_items if m.kind == "photo")
    print(f"\n[2/5] Media ready: {len(media_items)} items ({clips} clips, {photos} photos)")

    # ── 3. Voice + Subtitles (scene-timed) ───────────────────
    print("\n[3/5] Generating voiceover + subtitles…")
    narrations = [s.narration for s in brief.scenes]
    audio_path, srt_path, scene_durations = generate_voice_scenes(vid, narrations)
    audio_dur = _probe_duration(audio_path)
    print(f"      Audio: {audio_dur:.1f}s  |  Script: {len(brief.script.split())} words")

    # ── 4. Video assembly (scene-timed) ──────────────────────
    print("\n[4/5] Assembling video…")
    video_paths = assemble_video(vid, media_items, audio_path, srt_path,
                                  hook_text=brief.hook,
                                  scene_durations=scene_durations,
                                  params=render_params)

    # ── Quality check + auto-remediation (Phase 3) ───────────────
    print("\n  [QC] Running post-generation checks…")
    yt_path = video_paths.get("youtube", "")
    if yt_path:
        _quality_check(yt_path, audio_path, hook_seconds=HOOK_CARD_SECONDS)
        _qa_and_remediate(vid, video_paths)

    if dry_run:
        print(f"\n✅ DRY RUN — videos saved locally:")
        for k, v in video_paths.items():
            print(f"   {k}: {v}")
        return video_paths.get("youtube", "")

    # ── 5. Publish ────────────────────────────────────────────
    print("\n[5/5] Uploading to YouTube…")
    metadata = brief.to_metadata_dict()
    yt_id = upload_video(video_paths["youtube"], metadata)
    print(f"\n✅ Done!  https://www.youtube.com/watch?v={yt_id}\n")
    return yt_id
