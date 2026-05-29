"""Subtitle Agent — fallback utilities only.

Primary subtitle generation is in voice_agent.py (SentenceBoundary timing).
This module provides a naive proportional SRT as fallback when edge-tts
does not return SentenceBoundary events (rare, e.g., very short scripts).
"""
import re


def _ticks(seconds: float) -> str:
    """Seconds → SRT HH:MM:SS,mmm string."""
    ms  = int(seconds * 1000)
    h   = ms // 3_600_000;  ms -= h * 3_600_000
    m   = ms // 60_000;     ms -= m * 60_000
    s   = ms // 1_000;      ms -= s * 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def naive_srt(script: str, words_per_minute: int = 140) -> list[str]:
    """
    Generate proportional SRT lines (list of strings) from script text.
    Used as fallback when accurate timing is unavailable.
    """
    words_per_second = words_per_minute / 60
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())

    lines: list[str] = []
    idx = 1
    t   = 0.0

    for sent in sentences:
        words  = sent.split()
        chunks = [" ".join(words[i:i+6]) for i in range(0, len(words), 6)]
        for chunk in chunks:
            dur = len(chunk.split()) / words_per_second
            lines += [str(idx), f"{_ticks(t)} --> {_ticks(t + dur)}",
                      chunk.upper(), ""]
            t += dur
            idx += 1

    return lines


def get_audio_duration(audio_path: str) -> float:
    """Return audio duration in seconds using ffprobe."""
    import subprocess, json
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", audio_path],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])
