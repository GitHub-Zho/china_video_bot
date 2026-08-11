"""Framework-independent process control for the local launcher."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from urllib.parse import urlparse


Mode = Literal["topic", "video"]


class LaunchValidationError(ValueError):
    """Raised when launcher form values cannot produce a safe CLI command."""


@dataclass(frozen=True)
class LaunchRequest:
    mode: Mode
    prompt: str = ""
    video_type: str = "both"
    video_url: str = ""
    topic: str = ""
    seconds: float | None = None
    review: bool = False
    disable_scout: bool = True
    sample_interval: float = 4.0


@dataclass(frozen=True)
class RunOutputs:
    output_dir: Path | None = None
    youtube: Path | None = None
    reels: Path | None = None
    brief: Path | None = None


@dataclass(frozen=True)
class RunEvent:
    kind: Literal["log", "complete", "error"]
    message: str
    outputs: RunOutputs | None = None


def _number_arg(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _is_supported_video_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    return hostname == "b23.tv" or any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in ("bilibili.com", "youtube.com", "youtu.be")
    )


def build_command(request: LaunchRequest, python_executable: str) -> list[str]:
    """Build an argument-list command that can never publish content."""
    command = [python_executable, "scripts/run.py", "--dry-run"]

    if request.seconds is not None:
        if request.seconds <= 0:
            raise LaunchValidationError("目标时长必须大于 0")

    if request.mode == "topic":
        prompt = request.prompt.strip()
        if not prompt:
            raise LaunchValidationError("请输入主题")
        if request.video_type not in {"growth", "info", "both"}:
            raise LaunchValidationError("内容类型无效")
        command.extend(["--prompt", prompt, "--type", request.video_type])
        if request.disable_scout:
            command.append("--no-scout")
    elif request.mode == "video":
        video_url = request.video_url.strip()
        topic = request.topic.strip()
        if not video_url:
            raise LaunchValidationError("请输入参考视频网址")
        if not topic:
            raise LaunchValidationError("请输入主题说明")
        if not _is_supported_video_url(video_url):
            raise LaunchValidationError("仅支持 Bilibili 或 YouTube 视频网址")
        if request.sample_interval <= 0:
            raise LaunchValidationError("采样间隔必须大于 0")
        command.extend(
            [
                "--from-video",
                video_url,
                "--topic",
                topic,
                "--sample-interval",
                _number_arg(request.sample_interval),
            ]
        )
    else:
        raise LaunchValidationError("生成模式无效")

    if request.seconds is not None:
        command.extend(["--seconds", _number_arg(request.seconds)])
    if request.review:
        command.append("--review")
    return command


def discover_outputs(output_root: Path, started_at: float) -> RunOutputs:
    """Return outputs from the newest run created or updated by this launch."""
    root = output_root.resolve()
    if not root.is_dir():
        return RunOutputs()

    candidates = [
        item
        for item in root.iterdir()
        if item.is_dir() and item.stat().st_mtime >= started_at
    ]
    if not candidates:
        return RunOutputs()

    output_dir = max(candidates, key=lambda item: (item.stat().st_mtime, item.name)).resolve()

    def safe_file(name: str) -> Path | None:
        candidate = (output_dir / name).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return candidate
        return None

    return RunOutputs(
        output_dir=output_dir,
        youtube=safe_file("youtube.mp4"),
        reels=safe_file("reels.mp4"),
        brief=safe_file("brief.json"),
    )


class PipelineRunner:
    """Run one existing CLI pipeline at a time and stream user-safe events."""

    def __init__(
        self,
        repo_root: Path,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.output_root = self.repo_root / "output"
        self._popen = popen
        self._lock = threading.Lock()

    def run(self, request: LaunchRequest) -> Iterator[RunEvent]:
        if not self._lock.acquire(blocking=False):
            yield RunEvent("error", "已有任务正在运行，请等待完成。")
            return

        started_at = time.time()
        try:
            try:
                command = build_command(request, sys.executable)
            except LaunchValidationError as exc:
                yield RunEvent("error", str(exc))
                return

            try:
                process = self._popen(
                    command,
                    cwd=self.repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                yield RunEvent("error", f"无法启动生成进程：{exc}")
                return

            for line in process.stdout or ():
                message = line.rstrip()
                if message:
                    yield RunEvent("log", message)

            returncode = process.wait()
            if returncode != 0:
                yield RunEvent("error", f"生成失败（退出码 {returncode}）。")
                return

            outputs = discover_outputs(self.output_root, started_at)
            yield RunEvent("complete", "生成完成。", outputs)
        finally:
            self._lock.release()
