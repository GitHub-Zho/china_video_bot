"""Voice Agent — generates MP3 audio AND a precisely-timed SRT file in one pass.

Uses edge-tts SentenceBoundary events (offset/duration in 100ns ticks) for
sentence-level timing, then proportionally sub-divides each sentence into
≤6-word subtitle chunks.  No Whisper required.
"""
import asyncio
import re
from pathlib import Path
from config.settings import OUTPUT_DIR

VOICE = "en-US-AriaNeural"   # warm, clear — best for travel content
RATE  = "-5%"                # slightly slower = more natural for ESL audiences
TICKS_PER_SECOND = 10_000_000   # edge-tts offset/duration unit


def _ticks_to_srt(ticks: int) -> str:
    """Convert 100ns ticks → SRT timestamp string HH:MM:SS,mmm."""
    ms  = ticks // 10_000
    h   = ms  // 3_600_000;  ms -= h * 3_600_000
    m   = ms  // 60_000;     ms -= m * 60_000
    s   = ms  // 1_000;      ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _sentence_to_srt_blocks(start_idx: int, text: str,
                             offset_ticks: int, duration_ticks: int) -> list[str]:
    """
    Split one sentence into ≤6-word SRT cues, proportionally timed.
    Returns a list of SRT block strings ready to join with newlines.
    """
    words = text.split()
    if not words:
        return []

    chunks = []
    for i in range(0, len(words), 6):
        chunks.append(" ".join(words[i:i+6]))

    ticks_per_word = duration_ticks / max(len(words), 1)
    blocks = []
    word_cursor = 0

    for i, chunk in enumerate(chunks):
        chunk_words = len(chunk.split())
        start_t = offset_ticks + int(word_cursor * ticks_per_word)
        end_t   = offset_ticks + int((word_cursor + chunk_words) * ticks_per_word)
        word_cursor += chunk_words

        blocks.append(str(start_idx + i))
        blocks.append(f"{_ticks_to_srt(start_t)} --> {_ticks_to_srt(end_t)}")
        blocks.append(chunk.upper())
        blocks.append("")

    return blocks


async def _generate_async(script: str, audio_path: str, srt_path: str) -> None:
    """One streaming pass: write MP3 bytes + collect SentenceBoundary events."""
    import edge_tts
    communicate = edge_tts.Communicate(script, VOICE, rate=RATE)

    sentences: list[dict] = []
    audio_data = b""

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]
        elif chunk["type"] == "SentenceBoundary":
            sentences.append(chunk)   # keys: text, offset, duration (all in ticks)

    Path(audio_path).write_bytes(audio_data)

    # Build SRT
    all_blocks: list[str] = []
    cue_idx = 1
    for sent in sentences:
        blocks = _sentence_to_srt_blocks(
            start_idx=cue_idx,
            text=sent["text"].strip(),
            offset_ticks=sent["offset"],
            duration_ticks=sent["duration"],
        )
        all_blocks.extend(blocks)
        # How many cues did we add?
        words   = len(sent["text"].split())
        cue_idx += max(1, (words + 5) // 6)

    # Fallback: if no sentence boundaries fired, generate naive proportional SRT
    if not sentences:
        print("  [Voice] ⚠️  No SentenceBoundary events — using proportional SRT")
        from agents.subtitle_agent import naive_srt
        all_blocks = naive_srt(script)

    Path(srt_path).write_text("\n".join(all_blocks), encoding="utf-8")


def generate_voice(video_id: str, script: str) -> tuple[str, str]:
    """
    Generate MP3 voiceover + accurately-timed SRT file in one network call.

    Returns:
        (audio_path, srt_path) — both local file paths
    """
    base_dir  = Path(OUTPUT_DIR) / video_id
    base_dir.mkdir(parents=True, exist_ok=True)
    audio_path = str(base_dir / "audio.mp3")
    srt_path   = str(base_dir / "subtitles.srt")

    # Resume-safe: skip if both files already exist
    if (Path(audio_path).exists() and Path(audio_path).stat().st_size > 1000 and
            Path(srt_path).exists()):
        print(f"  [Voice] audio + srt already exist, skipping")
        return audio_path, srt_path

    print(f"  [Voice] Generating TTS: {len(script)} chars, voice={VOICE}")
    asyncio.run(_generate_async(script, audio_path, srt_path))

    audio_kb = Path(audio_path).stat().st_size // 1024
    srt_cues = Path(srt_path).read_text().count("\n\n")
    print(f"  [Voice] MP3: {audio_kb} KB | SRT: {srt_cues} cues")
    return audio_path, srt_path
