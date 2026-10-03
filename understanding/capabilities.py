"""Provider-facing source capabilities kept outside formal payload models."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from .models import TranscriptSpan, TranscriptV1


def probe_media_duration(media_handle: str) -> float:
    if not isinstance(media_handle, str) or not media_handle.strip():
        raise ValueError("media_handle must be a non-empty string")
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            media_handle,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        return float(completed.stdout.strip())
    except ValueError as exc:
        raise ValueError("ffprobe returned an invalid source duration") from exc


class FasterWhisperSourceTranscriber:
    """Convert one resolved media handle into a validated transcript.v1 payload."""

    def __init__(
        self,
        *,
        model_name: str = "small",
        device: str = "auto",
        compute_type: str = "int8",
        model_loader: Callable[[], Any] | None = None,
        duration_probe: Callable[[str], float] = probe_media_duration,
    ) -> None:
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string")
        if not callable(duration_probe):
            raise ValueError("duration_probe must be callable")
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model_loader = model_loader or self._default_model_loader
        self._duration_probe = duration_probe
        self._model: Any | None = None

    def _default_model_loader(self) -> Any:
        from faster_whisper import WhisperModel

        return WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )

    def __call__(self, media_handle: str) -> TranscriptV1:
        duration = self._duration_probe(media_handle)
        if self._model is None:
            self._model = self._model_loader()
        raw_segments, info = self._model.transcribe(
            media_handle,
            vad_filter=True,
            word_timestamps=False,
        )
        spans: list[TranscriptSpan] = []
        observed_segments = 0
        for raw in raw_segments:
            observed_segments += 1
            text = getattr(raw, "text", None)
            if not isinstance(text, str) or not text.strip():
                continue
            spans.append(
                TranscriptSpan(
                    start_sec=getattr(raw, "start", None),
                    end_sec=getattr(raw, "end", None),
                    text=text,
                    speaker_label=None,
                    confidence=None,
                )
            )
        if observed_segments and not spans:
            raise ValueError("ASR returned segments without usable transcript text")
        spans.sort(key=lambda span: (span.start_sec, span.end_sec))
        language = getattr(info, "language", None) if spans else None
        return TranscriptV1(
            source_duration_sec=duration,
            language=language,
            speech_status="speech" if spans else "no_speech",
            spans=tuple(spans),
        )

