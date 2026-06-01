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
KOKORO_SPEED  = 1.05         # slightly faster = more energetic, less flat

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


# ── Public API ────────────────────────────────────────────────────────────────

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
