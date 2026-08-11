"""Framework-independent process control for the local launcher."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from urllib.parse import urlparse


Mode = Literal["topic", "video"]
RESULT_MARKER = "[run.py] RESULT_JSON "


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
    kind: Literal["log", "heartbeat", "complete", "error"]
    message: str
    outputs: tuple[RunOutputs, ...] | None = None


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


def extract_result_paths(line: str) -> tuple[str, ...]:
    """Extract path-like string values from one machine-readable result line."""
    if not line.startswith(RESULT_MARKER):
        return ()
    try:
        payload = json.loads(line[len(RESULT_MARKER) :])
    except json.JSONDecodeError:
        return ()

    paths: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    return tuple(paths)


def discover_outputs(
    output_root: Path,
    started_at: float,
    hinted_paths: list[str] | tuple[str, ...] | None = None,
) -> tuple[RunOutputs, ...]:
    """Resolve safe run artifacts, preferring paths emitted by this process."""
    root = output_root.resolve()
    if not root.is_dir():
        return ()

    artifact_names = ("youtube.mp4", "reels.mp4", "brief.json")

    def has_safe_artifact(directory: Path) -> bool:
        return any(
            (directory / name).is_file()
            and (directory / name).resolve().is_relative_to(root)
            for name in artifact_names
        )

    if hinted_paths is not None:
        candidates: list[Path] = []
        seen: set[Path] = set()
        for raw_path in hinted_paths:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = root.parent / candidate
            candidate = candidate.resolve()
            directory = candidate if candidate.is_dir() else candidate.parent
            if (
                directory.is_relative_to(root)
                and directory not in seen
                and has_safe_artifact(directory)
            ):
                candidates.append(directory)
                seen.add(directory)
    else:
        candidates = sorted(
            (
                item
                for item in root.iterdir()
                if item.is_dir()
                and item.stat().st_mtime >= started_at
                and has_safe_artifact(item)
            ),
            key=lambda item: (item.stat().st_mtime, item.name),
            reverse=True,
        )
    if not candidates:
        return ()

    def run_outputs(output_dir: Path) -> RunOutputs:
        output_dir = output_dir.resolve()

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

    return tuple(run_outputs(directory) for directory in candidates)


class PipelineRunner:
    """Run one existing CLI pipeline at a time and stream user-safe events."""

    def __init__(
        self,
        repo_root: Path,
        popen: Callable[..., Any] = subprocess.Popen,
        command_builder: Callable[[LaunchRequest, str], list[str]] = build_command,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.output_root = self.repo_root / "output"
        self._popen = popen
        self._command_builder = command_builder
        self._lock = threading.Lock()

    @staticmethod
    def _stop_process_group(process: Any) -> None:
        """Terminate and reap the child plus FFmpeg/yt-dlp descendants."""
        if process.poll() is not None:
            process.wait()
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

    def run(self, request: LaunchRequest) -> Iterator[RunEvent]:
        if not self._lock.acquire(blocking=False):
            yield RunEvent("error", "已有任务正在运行，请等待完成。")
            return

        started_at = time.time()
        process: Any | None = None
        try:
            try:
                command = self._command_builder(request, sys.executable)
            except LaunchValidationError as exc:
                yield RunEvent("error", str(exc))
                return

            try:
                child_env = os.environ.copy()
                child_env["PYTHONUNBUFFERED"] = "1"
                process = self._popen(
                    command,
                    cwd=self.repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=child_env,
                    start_new_session=True,
                )
            except OSError as exc:
                yield RunEvent("error", f"无法启动生成进程：{exc}")
                return

            process_events: queue.Queue[tuple[str, Any]] = queue.Queue()

            def read_process_output() -> None:
                try:
                    for line in process.stdout or ():
                        process_events.put(("line", line))
                    process_events.put(("done", process.wait()))
                except BaseException as exc:
                    process_events.put(("reader_error", exc))

            reader = threading.Thread(
                target=read_process_output,
                name="china-video-bot-log-reader",
                daemon=True,
            )
            reader.start()

            result_paths: list[str] = []
            returncode: int | None = None
            try:
                while returncode is None:
                    try:
                        event_kind, payload = process_events.get(timeout=0.5)
                    except queue.Empty:
                        yield RunEvent("heartbeat", "")
                        continue

                    if event_kind == "line":
                        message = str(payload).rstrip()
                        marker_paths = extract_result_paths(message)
                        if marker_paths:
                            result_paths.extend(marker_paths)
                        elif message:
                            yield RunEvent("log", message)
                    elif event_kind == "done":
                        returncode = int(payload)
                    else:
                        self._stop_process_group(process)
                        yield RunEvent("error", f"读取运行日志失败：{payload}")
                        return
            except BaseException:
                self._stop_process_group(process)
                reader.join(timeout=6)
                raise

            if returncode != 0:
                yield RunEvent("error", f"生成失败（退出码 {returncode}）。")
                return

            outputs = discover_outputs(
                self.output_root,
                started_at,
                hinted_paths=result_paths,
            )
            yield RunEvent("complete", "生成完成。", outputs)
        finally:
            self._lock.release()
