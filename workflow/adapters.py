"""Adapters that expose existing pipeline functions through logical IDs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .executors import ExecutorRegistry


VIDEO_ANALYSIS_EXECUTOR_ID = "video_grounded.analyze_source"


def register_existing_executors(
    registry: ExecutorRegistry, analyzer: Callable[..., Any] | None = None
) -> None:
    """Register existing pipeline capabilities without importing them eagerly."""
    if analyzer is None:

        def analyzer(*args: Any, **kwargs: Any) -> Any:
            from agents.video_analyst_agent import analyze_video

            return analyze_video(*args, **kwargs)

    registry.register(VIDEO_ANALYSIS_EXECUTOR_ID, analyzer)
