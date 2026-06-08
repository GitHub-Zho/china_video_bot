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


def _scene_windows(brief, scene_durations: list[float]) -> list:
    """Build [(start_s, end_s, narration), ...] in VIDEO time (after the hook)
    so QA can match each frame to the narration it should depict."""
    windows = []
    t = HOOK_CARD_SECONDS
    for i, scene in enumerate(brief.scenes):
        dur = scene_durations[i] if i < len(scene_durations) else 4.0
        windows.append((round(t, 2), round(t + dur, 2), scene.narration))
        t += dur
    return windows


def _save_render_state(vid: str, brief, scene_durations: list[float]) -> None:
    """Persist what's needed to re-assemble after a manual scene swap."""
    state = {
        "hook": brief.hook,
        "scene_durations": scene_durations,
        "visual_queries": [s.visual_query for s in brief.scenes],
        "narrations": [s.narration for s in brief.scenes],
    }
    (Path(OUTPUT_DIR) / vid / "render_state.json").write_text(json.dumps(state, indent=2))


def _reassemble_from_media(vid: str, brief, scene_durations: list[float]) -> None:
    """Rebuild both video variants from the current media/ folder (after a clip
    was swapped on disk). Used by Qwen auto-replacement."""
    from agents.media_agent import MediaItem
    base = Path(OUTPUT_DIR) / vid
    media_items = []
    for i in range(len(brief.scenes)):
        clip  = base / "media" / f"{i:02d}.mp4"
        photo = base / "media" / f"{i:02d}.jpg"
        if clip.exists():
            media_items.append(MediaItem(str(clip), "clip"))
        elif photo.exists():
            media_items.append(MediaItem(str(photo), "photo"))
    for f in ("youtube.mp4", "reels.mp4", "youtube_raw.mp4", "reels_raw.mp4"):
        (base / f).unlink(missing_ok=True)
    assemble_video(vid, media_items, str(base / "audio.mp3"),
                   str(base / "subtitles.srt"), hook_text=brief.hook,
                   scene_durations=scene_durations)


def _qa_and_remediate(vid: str, video_paths: dict, brief=None,
                      scene_durations: list[float] | None = None) -> None:
    """
    Phase 3 — QA the video (incl. content-mismatch detection). On fixable subtitle
    issues, auto-adjust params and re-burn. On CONTENT mismatches (footage ≠
    narration), download 3 alternative clips per bad scene so the user can pick a
    better one, and write a review.json describing how to apply the fix.
    """
    yt_path = video_paths.get("youtube", "")
    if not yt_path:
        return

    scene_windows = (_scene_windows(brief, scene_durations)
                     if (brief and scene_durations) else None)
    report = qa_check(yt_path, hook_seconds=HOOK_CARD_SECONDS,
                      scene_windows=scene_windows)

    # ── Content mismatches → Qwen AUTO-replaces the bad clips ─────────────────
    mism = [i for i in report.issues if i.category == "content"]
    if mism and brief and scene_windows:
        from agents.media_agent import (find_replacement_clip,
                                        download_scene_alternatives)
        print(f"  [QC] ⚠️  {len(mism)} content mismatch(es) — Qwen auto-replacing:")
        replaced, unfixable = [], []
        done_scenes = set()
        for issue in mism:
            idx = next((k for k, (s, e, _) in enumerate(scene_windows)
                        if s <= issue.timestamp_s < e), None)
            if idx is None or idx in done_scenes:
                continue
            done_scenes.add(idx)
            print(f"        • scene {idx}: {issue.description[:70]}")
            ok = find_replacement_clip(vid, idx, brief.scenes[idx].stock_query(),
                                       brief.scenes[idx].narration)
            (replaced if ok else unfixable).append(idx)

        if replaced:
            # Clips changed on disk → re-assemble the whole video from media/
            print(f"  [QC] Re-assembling with {len(replaced)} replaced scene(s)…")
            _reassemble_from_media(vid, brief, scene_durations)

        # Scenes with no good footage anywhere → leave alternatives for human pick
        for idx in unfixable:
            alts = download_scene_alternatives(vid, idx, brief.scenes[idx].stock_query(), n=3)
            print(f"  [QC] scene {idx}: no good auto-match — {len(alts)} alternatives "
                  f"saved for manual pick (--fix {vid} --scene {idx} --pick K)")

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


def apply_alternative(video_id: str, scene_index: int, pick: int) -> str:
    """
    Swap a scene's clip for a user-chosen alternative and re-assemble the video.
    `pick` is the alt_N index shown in output/{vid}/review.json.
    Returns the new youtube path.
    """
    from agents.media_agent import MediaItem
    import shutil

    base = Path(OUTPUT_DIR) / video_id
    state_f = base / "render_state.json"
    if not state_f.exists():
        raise FileNotFoundError(f"No render_state.json for {video_id} — can't re-assemble.")
    state = json.loads(state_f.read_text())

    chosen = base / "alternatives" / f"scene_{scene_index:02d}" / f"alt_{pick}.mp4"
    target = base / "media" / f"{scene_index:02d}.mp4"
    if not chosen.exists():
        raise FileNotFoundError(f"Alternative not found: {chosen}")

    print(f"  [Fix] Scene {scene_index}: swapping in alt_{pick} → {target.name}")
    shutil.copy(chosen, target)

    # Rebuild media list from the media/ folder (order = scene order)
    media_items = []
    for i in range(len(state["scene_durations"])):
        clip = base / "media" / f"{i:02d}.mp4"
        photo = base / "media" / f"{i:02d}.jpg"
        if clip.exists():
            media_items.append(MediaItem(str(clip), "clip"))
        elif photo.exists():
            media_items.append(MediaItem(str(photo), "photo"))

    # Remove old finals so assemble_video rebuilds them
    for f in ("youtube.mp4", "reels.mp4", "youtube_raw.mp4", "reels_raw.mp4"):
        (base / f).unlink(missing_ok=True)

    print("  [Fix] Re-assembling video…")
    paths = assemble_video(video_id, media_items,
                           str(base / "audio.mp3"), str(base / "subtitles.srt"),
                           hook_text=state.get("hook", ""),
                           scene_durations=state["scene_durations"])
    print(f"  [Fix] ✅ Rebuilt: {paths.get('youtube')}")
    return paths.get("youtube", "")


def _print_script(brief) -> None:
    """Pretty-print a brief's script for human review."""
    print(f"\n  ── SCRIPT REVIEW ──  topic: {brief.topic}  ({len(brief.scenes)} scenes)")
    print(f"  HOOK: {brief.hook}")
    for s in brief.scenes:
        print(f"   {s.index}. {s.narration}")
        print(f"      ↳ visual: {s.visual_query}")
    print(f"  CTA: {brief.cta}")


def _resolve_style(style: str):
    if not style:
        return None
    from agents.style_analyst_agent import load_style
    sp = load_style(style)
    if not sp:
        print(f"  ⚠️  Style '{style}' not found — using defaults")
        return None
    h = sp.to_render_hints()
    print(f"  Style    : '{style}' ({sp.color_mood}, {sp.subtitle_size} subs)")
    return VideoRenderParams(fontsize_pct=h["fontsize_pct"], subtitle_y=h["subtitle_y"])


def run_pipeline(audience_type: str = None, dry_run: bool = False,
                 prompt: str = "", style: str = "", review: bool = False,
                 target_seconds: float = None, video_type: str = "both"):
    """
    Full pipeline for one video (or both formats).
    video_type     → "growth" (hook/engagement), "info" (educational story), or
                     "both" (default — make one of each for the same topic).
    review=True    → generate the script(s) ONLY and stop for approval.
    dry_run=True   → assemble locally, skip YouTube upload.
    prompt/style/target_seconds → as before.

    Returns a single path/id (one type) or a {type: path/id} dict ("both").
    """
    if video_type == "both":
        results = {}
        for vt in ("growth", "info"):
            print(f"\n############  {vt.upper()} version  ############")
            results[vt] = _run_one(vt, audience_type, dry_run, prompt, style,
                                   review, target_seconds)
        return results
    return _run_one(video_type, audience_type, dry_run, prompt, style,
                    review, target_seconds)


def _run_one(video_type, audience_type, dry_run, prompt, style, review,
             target_seconds) -> str:
    vid = _video_id() + f"_{video_type}"
    print(f"\n{'='*55}")
    print(f"  China Video Bot  ·  {vid}")
    print(f"{'='*55}")

    render_params = _resolve_style(style)

    # ── 1. Director: plan the entire video scene-by-scene ────
    print(f"\n[1/5] Director planning scenes ({video_type})…")
    brief = create_brief(audience_type, prompt=prompt,
                         target_seconds=target_seconds, video_type=video_type)
    print(f"      Topic    : {brief.topic}  |  Audience: {brief.audience_type}")

    out_dir = Path(OUTPUT_DIR) / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(json.dumps(brief.to_metadata_dict(), indent=2))
    (out_dir / "brief.json").write_text(json.dumps(brief.to_metadata_dict(), indent=2))

    # ── Script review gate ───────────────────────────────────
    if review:
        _print_script(brief)
        print(f"\n  📋 {video_type} script saved → output/{vid}/brief.json")
        print(f"     Build it with: python scripts/run.py --from-brief "
              f"output/{vid}/brief.json{' --dry-run' if dry_run else ''}")
        return str(out_dir / "brief.json")

    return _build_from_brief(vid, brief, dry_run=dry_run, render_params=render_params)


def _build_from_brief(vid: str, brief, dry_run: bool = False,
                      render_params=None) -> str:
    """Shared build path: Voice → Media → Assemble → QA → Publish."""
    out_dir = Path(OUTPUT_DIR) / vid
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. Voice + Subtitles (BEFORE media — Phase 1) ────────
    print("\n[2/5] Generating voiceover + subtitles…")
    narrations = [s.narration for s in brief.scenes]
    audio_path, srt_path, scene_durations = generate_voice_scenes(vid, narrations)
    audio_dur = _probe_duration(audio_path)
    print(f"      Audio: {audio_dur:.1f}s  |  Script: {len(brief.script.split())} words")

    # ── 3. Media ─────────────────────────────────────────────
    print("\n[3/5] Downloading media…")
    search_queries = [s.stock_query() for s in brief.scenes]   # plain keywords for search
    match_descs    = [s.visual_query for s in brief.scenes]     # rich text to judge match
    media_items = download_media(vid, search_queries, match_descriptions=match_descs)
    if len(media_items) < 2:
        raise RuntimeError(f"Only {len(media_items)} media item(s) — check API keys.")
    clips  = sum(1 for m in media_items if m.kind == "clip")
    photos = sum(1 for m in media_items if m.kind == "photo")
    print(f"      {len(media_items)} items ready ({clips} clips, {photos} photos)")

    # ── 4. Video assembly (scene-timed) ──────────────────────
    print("\n[4/5] Assembling video…")
    video_paths = assemble_video(vid, media_items, audio_path, srt_path,
                                  hook_text=brief.hook,
                                  scene_durations=scene_durations,
                                  params=render_params)

    # ── Quality check + auto-remediation (Phase 3) ───────────
    print("\n  [QC] Running post-generation checks…")
    yt_path = video_paths.get("youtube", "")
    if yt_path:
        _quality_check(yt_path, audio_path, hook_seconds=HOOK_CARD_SECONDS)
        _save_render_state(vid, brief, scene_durations)
        _qa_and_remediate(vid, video_paths, brief=brief,
                          scene_durations=scene_durations)

    if dry_run:
        print(f"\n✅ DRY RUN — videos saved locally (not uploaded):")
        for k, v in video_paths.items():
            print(f"   {k}: {v}")
        return video_paths.get("youtube", "")

    # ── 5. Publish ────────────────────────────────────────────
    print("\n[5/5] Uploading to YouTube…")
    yt_id = upload_video(video_paths["youtube"], brief.to_metadata_dict())
    n = run_pending_analytics()
    if n:
        print(f"  [+] Analytics collected for {n} previous video(s)")
        extract_insights()
    print(f"\n✅ Done!  https://www.youtube.com/watch?v={yt_id}\n")
    return yt_id


def run_pipeline_from_brief(brief_path: str, dry_run: bool = False,
                            style: str = "") -> str:
    """Build a video from an approved/edited brief.json (the review-gate output)."""
    from agents.director_agent import CreativeBrief
    bp = Path(brief_path)
    if not bp.exists():
        raise FileNotFoundError(f"Brief not found: {brief_path}")
    brief = CreativeBrief.from_metadata_dict(json.loads(bp.read_text()))
    # video_id = the parent folder name if it's an output dir, else a fresh id
    vid = bp.parent.name if bp.parent.parent.name == OUTPUT_DIR else _video_id()
    print(f"\n{'='*55}\n  Building from approved brief · {vid}\n{'='*55}")
    print(f"  Topic: {brief.topic}  ({len(brief.scenes)} scenes)")
    return _build_from_brief(vid, brief, dry_run=dry_run,
                             render_params=_resolve_style(style))


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
        _save_render_state(vid, brief, scene_durations)
        _qa_and_remediate(vid, video_paths, brief=brief,
                          scene_durations=scene_durations)

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
