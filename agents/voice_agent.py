"""Voice Agent — generates MP3 audio + precisely-timed SRT.

Primary: Kokoro TTS (local, free, natural-sounding)
Fallback: edge-tts (Microsoft Neural, free, flat but reliable)

Kokoro model files are cached at ~/.cache/kokoro/ on first run.
"""
import asyncio
import re
from pathlib import Path
from config.settings import OUTPUT_DIR

# ── Kokoro constants ──────────────────────────────────────────────────────────
KOKORO_MODEL  = str(Path.home() / ".cache/kokoro/kokoro-v1.0.onnx")
KOKORO_VOICES = str(Path.home() / ".cache/kokoro/voices-v1.0.bin")
KOKORO_VOICE  = "af_heart"   # warm American female — best for travel content
KOKORO_SPEED  = 1.15         # faster delivery = more energetic, Reels-paced

# edge-tts fallback constants
EDGE_VOICE = "en-US-AriaNeural"
EDGE_RATE  = "-5%"

TICKS_PER_SECOND = 10_000_000   # edge-tts offset unit (100ns ticks)


# ── SRT helpers ───────────────────────────────────────────────────────────────

def _ms_to_srt(ms: int) -> str:
    h = ms // 3_600_000;  ms -= h * 3_600_000
    m = ms // 60_000;     ms -= m * 60_000
    s = ms // 1_000;      ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ticks_to_srt(ticks: int) -> str:
    return _ms_to_srt(ticks // 10_000)


def _scene_srt(scene_timings: list[tuple[int, int, str]]) -> list[str]:
    """
    Build an SRT with ONE cue per scene — full narration text, spanning the
    scene's full audio window. No ≤6-word splitting (that caused flashing + gaps).

    scene_timings: list of (start_ms, end_ms, text), one per scene.
    """
    blocks: list[str] = []
    idx = 1
    for t0, t1, text in scene_timings:
        text = text.strip()
        if not text or t1 <= t0:
            continue
        blocks += [str(idx),
                   f"{_ms_to_srt(t0)} --> {_ms_to_srt(t1)}",
                   text.upper(), ""]
        idx += 1
    return blocks


def _text_to_srt_blocks(start_idx: int, text: str,
                         t0_ms: int, t1_ms: int) -> list[str]:
    """Split one sentence into ≤6-word SRT cues, proportionally timed."""
    words = text.split()
    if not words:
        return []
    chunks = [" ".join(words[i:i+6]) for i in range(0, len(words), 6)]
    ms_per_word = (t1_ms - t0_ms) / max(len(words), 1)
    blocks, wc = [], 0
    for i, chunk in enumerate(chunks):
        cw = len(chunk.split())
        s  = t0_ms + int(wc * ms_per_word)
        e  = t0_ms + int((wc + cw) * ms_per_word)
        blocks += [str(start_idx + i),
                   f"{_ms_to_srt(s)} --> {_ms_to_srt(e)}",
                   chunk.upper(), ""]
        wc += cw
    return blocks


# ── Kokoro TTS ────────────────────────────────────────────────────────────────

def _generate_kokoro(script: str, audio_path: str, srt_path: str) -> bool:
    """
    Generate WAV (converted to MP3) + word-level SRT using Kokoro TTS.
    Returns True on success, False if Kokoro is unavailable (triggers fallback).
    """
    try:
        from kokoro_onnx import Kokoro
        import soundfile as sf
        import subprocess
        import numpy as np
    except ImportError:
        print("  [Voice] Kokoro not installed — falling back to edge-tts")
        return False

    if not Path(KOKORO_MODEL).exists() or not Path(KOKORO_VOICES).exists():
        print("  [Voice] Kokoro model files missing — falling back to edge-tts")
        return False

    try:
        k = Kokoro(KOKORO_MODEL, KOKORO_VOICES)

        # ── Generate audio ────────────────────────────────────────────────────
        # Split into sentences so we can time each one for the SRT
        sentences = re.split(r'(?<=[.!?])\s+', script.strip())
        if not sentences:
            return False

        all_samples = []
        sample_rate = 24000
        sentence_timings = []   # [(start_ms, end_ms, text), ...]

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            samples, sr = k.create(sent, voice=KOKORO_VOICE, speed=KOKORO_SPEED)
            sample_rate = sr
            start_ms = int(len(all_samples) / sr * 1000)
            all_samples.extend(samples.tolist())
            end_ms   = int(len(all_samples) / sr * 1000)
            sentence_timings.append((start_ms, end_ms, sent))

        # ── Save as WAV then convert to MP3 ──────────────────────────────────
        import tempfile
        wav_tmp = tempfile.mktemp(suffix=".wav")
        sf.write(wav_tmp, np.array(all_samples, dtype='float32'), sample_rate)

        from config.settings import FFMPEG_BIN
        r = subprocess.run([
            FFMPEG_BIN, "-y", "-i", wav_tmp,
            "-codec:a", "libmp3lame", "-qscale:a", "4", audio_path
        ], capture_output=True)
        Path(wav_tmp).unlink(missing_ok=True)
        if r.returncode != 0:
            print(f"  [Voice] Kokoro MP3 conversion failed: {r.stderr[-300:]}")
            return False

        # ── Build SRT from sentence timings ──────────────────────────────────
        blocks, idx = [], 1
        for t0, t1, text in sentence_timings:
            b = _text_to_srt_blocks(idx, text, t0, t1)
            blocks.extend(b)
            idx += max(1, (len(text.split()) + 5) // 6)

        Path(srt_path).write_text("\n".join(blocks), encoding="utf-8")
        print(f"  [Voice] ✅ Kokoro: {int(len(all_samples)/sample_rate)}s, "
              f"{len(sentence_timings)} sentences, voice={KOKORO_VOICE}")
        return True

    except Exception as e:
        print(f"  [Voice] Kokoro error ({e}) — falling back to edge-tts")
        return False


# ── edge-tts fallback ─────────────────────────────────────────────────────────

async def _generate_edge_async(script: str, audio_path: str, srt_path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(script, EDGE_VOICE, rate=EDGE_RATE)
    sentences: list[dict] = []
    audio_data = b""

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
        elif chunk["type"] == "SentenceBoundary":
            sentences.append(chunk)

    Path(audio_path).write_bytes(audio_data)

    all_blocks: list[str] = []
    cue_idx = 1
    for sent in sentences:
        t0 = sent["offset"] // 10_000          # ticks → ms
        t1 = t0 + sent["duration"] // 10_000
        blocks = _text_to_srt_blocks(cue_idx, sent["text"].strip(), t0, t1)
        all_blocks.extend(blocks)
        cue_idx += max(1, (len(sent["text"].split()) + 5) // 6)

    if not sentences:
        print("  [Voice] ⚠️  No SentenceBoundary events — using proportional SRT")
        from agents.subtitle_agent import naive_srt
        all_blocks = naive_srt(script)

    Path(srt_path).write_text("\n".join(all_blocks), encoding="utf-8")


# ── Per-scene generation (Phase 1 — SRT-driven timing) ────────────────────────

def _generate_kokoro_scenes(
    narrations: list[str], audio_path: str, srt_path: str
) -> list[float] | None:
    """
    Generate TTS scene-by-scene with Kokoro. Each narration = one scene = one unit.
    Returns list of per-scene durations (seconds), or None if Kokoro unavailable.

    This is the accurate path: because we generate each scene separately, we know
    EXACTLY how long each scene's audio is — no SRT re-parsing guesswork.
    """
    try:
        from kokoro_onnx import Kokoro
        import soundfile as sf
        import subprocess
        import numpy as np
    except ImportError:
        return None

    if not Path(KOKORO_MODEL).exists() or not Path(KOKORO_VOICES).exists():
        return None

    from config.settings import (SCENE_LEAD_IN, SCENE_TAIL, MIN_SCENE_SECONDS,
                                  HOOK_TO_FIRST_GAP)

    try:
        import numpy as np
        k = Kokoro(KOKORO_MODEL, KOKORO_VOICES)

        all_samples = []
        sample_rate = 24000
        scene_durations: list[float] = []
        scene_timings:   list[tuple[int, int, str]] = []   # (start_ms, end_ms, text)

        def _silence(seconds: float, sr: int) -> list:
            return [0.0] * int(seconds * sr)

        for idx, narration in enumerate(narrations):
            text = narration.strip()
            if not text:
                scene_durations.append(0.0)
                scene_timings.append((0, 0, ""))
                continue

            samples, sr = k.create(text, voice=KOKORO_VOICE, speed=KOKORO_SPEED)
            sample_rate = sr
            nar_dur = len(samples) / sr

            # Lead-in: caption appears, THEN the voice starts (extra on scene 0,
            # so there's a breath after the hook card). Tail: a beat after the line.
            lead = SCENE_LEAD_IN + (HOOK_TO_FIRST_GAP if idx == 0 else 0.0)
            # Whole scene holds at least MIN_SCENE_SECONDS (short lines still breathe)
            scene_dur = max(MIN_SCENE_SECONDS, lead + nar_dur + SCENE_TAIL)
            tail = scene_dur - lead - nar_dur

            scene_start_ms = int(len(all_samples) / sr * 1000)
            all_samples.extend(_silence(lead, sr))      # caption-leads-voice gap
            voice_start_ms = int(len(all_samples) / sr * 1000)
            all_samples.extend(samples.tolist())        # the narration
            voice_end_ms   = int(len(all_samples) / sr * 1000)
            all_samples.extend(_silence(max(0.0, tail), sr))   # breath after

            scene_durations.append(scene_dur)
            # Subtitle spans the whole scene (starts at scene_start → leads the voice
            # by `lead`, so viewers read proper nouns like "Nanluogu Xiang" before hearing them)
            scene_timings.append((scene_start_ms, voice_end_ms + int(tail*1000*0.5), text))

        # ── WAV → MP3 ────────────────────────────────────────────────────────
        import tempfile
        wav_tmp = tempfile.mktemp(suffix=".wav")
        sf.write(wav_tmp, np.array(all_samples, dtype='float32'), sample_rate)
        from config.settings import FFMPEG_BIN
        r = subprocess.run([
            FFMPEG_BIN, "-y", "-i", wav_tmp,
            "-codec:a", "libmp3lame", "-qscale:a", "4", audio_path
        ], capture_output=True)
        Path(wav_tmp).unlink(missing_ok=True)
        if r.returncode != 0:
            print(f"  [Voice] Kokoro MP3 conversion failed")
            return None

        # ── Build SRT: ONE cue per scene, full narration, full duration ──────
        # One subtitle per scene (not ≤6-word chunks) means: no flashing, no
        # mid-sentence gaps, and the subtitle stays up the whole time the scene's
        # clip is on screen. The drawtext filter word-wraps it to fit the frame.
        blocks = _scene_srt(scene_timings)
        Path(srt_path).write_text("\n".join(blocks), encoding="utf-8")

        total = sum(scene_durations)
        print(f"  [Voice] ✅ Kokoro: {total:.1f}s, {len(narrations)} scenes, "
              f"voice={KOKORO_VOICE}")
        print(f"  [Voice] Per-scene durations: "
              f"{[round(d,1) for d in scene_durations]}")
        return scene_durations

    except Exception as e:
        print(f"  [Voice] Kokoro per-scene error ({e})")
        return None


def _generate_edge_scenes(
    narrations: list[str], audio_path: str, srt_path: str
) -> list[float]:
    """
    edge-tts fallback: generate whole script, approximate per-scene durations
    by word-count proportion. Less precise than Kokoro but keeps pipeline working.
    """
    script = " ".join(n.strip() for n in narrations if n.strip())
    asyncio.run(_generate_edge_async(script, audio_path, srt_path))

    # Approximate scene durations from total audio duration × word-count share
    from config.settings import FFPROBE_BIN
    import subprocess
    probe = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    try:
        total_dur = float(probe.stdout.strip())
    except Exception:
        total_dur = sum(len(n.split()) for n in narrations) / 2.5  # ~2.5 wps guess

    total_words = sum(max(1, len(n.split())) for n in narrations)
    scene_durations = [
        total_dur * max(1, len(n.split())) / total_words for n in narrations
    ]
    print(f"  [Voice] edge-tts approx per-scene: "
          f"{[round(d,1) for d in scene_durations]}")
    return scene_durations


def generate_voice_scenes(
    video_id: str, narrations: list[str]
) -> tuple[str, str, list[float]]:
    """
    Phase 1 entry point: generate voiceover scene-by-scene.

    Returns (audio_path, srt_path, scene_durations) where scene_durations[i]
    is the exact spoken length of narrations[i] in seconds.

    The video assembler uses scene_durations to size each scene's clip,
    so visuals stay in sync with narration (no more hardcoded SLIDE_DURATION).
    """
    base_dir   = Path(OUTPUT_DIR) / video_id
    base_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(base_dir / "audio.mp3")
    srt_path   = str(base_dir / "subtitles.srt")

    print(f"  [Voice] Generating TTS for {len(narrations)} scenes")

    # Try Kokoro (accurate, per-scene)
    durations = _generate_kokoro_scenes(narrations, audio_path, srt_path)
    if durations is not None:
        return audio_path, srt_path, durations

    # Fall back to edge-tts (approximate per-scene)
    print(f"  [Voice] Using edge-tts fallback (voice={EDGE_VOICE})")
    durations = _generate_edge_scenes(narrations, audio_path, srt_path)
    return audio_path, srt_path, durations


# ── Legacy API (whole-script, kept for backward compat) ───────────────────────

def generate_voice(video_id: str, script: str) -> tuple[str, str]:
    """
    Generate MP3 voiceover + accurately-timed SRT.
    Tries Kokoro first (natural, local), falls back to edge-tts.

    Returns: (audio_path, srt_path)
    """
    base_dir   = Path(OUTPUT_DIR) / video_id
    base_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(base_dir / "audio.mp3")
    srt_path   = str(base_dir / "subtitles.srt")

    # Resume-safe
    if (Path(audio_path).exists() and Path(audio_path).stat().st_size > 1000 and
            Path(srt_path).exists()):
        print("  [Voice] audio + srt already exist, skipping")
        return audio_path, srt_path

    print(f"  [Voice] Generating TTS: {len(script)} chars")

    # Try Kokoro first
    if _generate_kokoro(script, audio_path, srt_path):
        return audio_path, srt_path

    # Fall back to edge-tts
    print(f"  [Voice] Using edge-tts (voice={EDGE_VOICE})")
    asyncio.run(_generate_edge_async(script, audio_path, srt_path))

    audio_kb  = Path(audio_path).stat().st_size // 1024
    srt_cues  = Path(srt_path).read_text().count("\n\n")
    print(f"  [Voice] MP3: {audio_kb} KB | SRT: {srt_cues} cues")
    return audio_path, srt_path
