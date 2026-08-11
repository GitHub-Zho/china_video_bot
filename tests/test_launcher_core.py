from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from launcher.core import (
    LaunchRequest,
    LaunchValidationError,
    PipelineRunner,
    RESULT_MARKER,
    RunEvent,
    build_command,
    discover_outputs,
    extract_result_paths,
)


class FakeProcess:
    def __init__(self, lines: list[str], returncode: int) -> None:
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


def test_mode1_command_is_local_only_and_disables_scout() -> None:
    request = LaunchRequest(mode="topic", prompt="Guilin", video_type="info")

    command = build_command(request, "/venv/bin/python")

    assert command == [
        "/venv/bin/python",
        "scripts/run.py",
        "--dry-run",
        "--prompt",
        "Guilin",
        "--type",
        "info",
        "--no-scout",
    ]


def test_mode1_optional_fields_are_explicit_arguments() -> None:
    request = LaunchRequest(
        mode="topic",
        prompt="  Chengdu hot pot  ",
        video_type="growth",
        seconds=18,
        review=True,
        disable_scout=False,
    )

    command = build_command(request, sys.executable)

    assert command == [
        sys.executable,
        "scripts/run.py",
        "--dry-run",
        "--prompt",
        "Chengdu hot pot",
        "--type",
        "growth",
        "--seconds",
        "18",
        "--review",
    ]


def test_mode2_command_uses_reference_url() -> None:
    request = LaunchRequest(
        mode="video",
        video_url="https://www.bilibili.com/video/BV1x",
        topic="Roast duck",
        sample_interval=4.0,
    )

    command = build_command(request, "/venv/bin/python")

    assert command == [
        "/venv/bin/python",
        "scripts/run.py",
        "--dry-run",
        "--from-video",
        "https://www.bilibili.com/video/BV1x",
        "--topic",
        "Roast duck",
        "--sample-interval",
        "4",
    ]


@pytest.mark.parametrize(
    ("form_request", "message"),
    [
        (LaunchRequest(mode="topic", prompt=""), "请输入主题"),
        (LaunchRequest(mode="video", video_url="", topic="Duck"), "请输入参考视频网址"),
        (
            LaunchRequest(mode="video", video_url="https://example.com/v", topic=""),
            "请输入主题说明",
        ),
        (
            LaunchRequest(mode="video", video_url="not-a-url", topic="Duck"),
            "仅支持 Bilibili 或 YouTube 视频网址",
        ),
        (
            LaunchRequest(mode="video", video_url="https://example.com/video", topic="Duck"),
            "仅支持 Bilibili 或 YouTube 视频网址",
        ),
        (LaunchRequest(mode="topic", prompt="Duck", seconds=0), "目标时长必须大于 0"),
        (
            LaunchRequest(
                mode="video",
                video_url="https://www.bilibili.com/video/BV1x",
                topic="Duck",
                sample_interval=0,
            ),
            "采样间隔必须大于 0",
        ),
    ],
)
def test_invalid_fields_are_rejected(form_request: LaunchRequest, message: str) -> None:
    with pytest.raises(LaunchValidationError, match=message):
        build_command(form_request, sys.executable)


def test_discover_outputs_returns_newest_eligible_run(tmp_path: Path) -> None:
    older = tmp_path / "20260811_older_info"
    older.mkdir()
    (older / "youtube.mp4").touch()
    newer = tmp_path / "20260811_newer_info"
    newer.mkdir()
    youtube = newer / "youtube.mp4"
    reels = newer / "reels.mp4"
    youtube.touch()
    reels.touch()
    older.touch()
    newer.touch()

    results = discover_outputs(tmp_path, started_at=0)
    result = results[0]

    assert result.output_dir == newer.resolve()
    assert result.youtube == youtube.resolve()
    assert result.reels == reels.resolve()
    assert result.brief is None


def test_discover_outputs_supports_review_only_brief(tmp_path: Path) -> None:
    run = tmp_path / "20260811_review"
    run.mkdir()
    brief = run / "brief.json"
    brief.touch()

    results = discover_outputs(tmp_path, started_at=0)
    result = results[0]

    assert result.output_dir == run.resolve()
    assert result.brief == brief.resolve()
    assert result.youtube is None
    assert result.reels is None


def test_discover_outputs_ignores_newer_cache_directories(tmp_path: Path) -> None:
    run = tmp_path / "20260811_run"
    run.mkdir()
    youtube = run / "youtube.mp4"
    youtube.touch()
    cache = tmp_path / "video_cache"
    cache.mkdir()
    (cache / "analysis.json").touch()
    cache.touch()

    results = discover_outputs(tmp_path, started_at=0)
    result = results[0]

    assert result.output_dir == run.resolve()
    assert result.youtube == youtube.resolve()


def test_discover_outputs_ignores_runs_older_than_start(tmp_path: Path) -> None:
    run = tmp_path / "old"
    run.mkdir()
    (run / "youtube.mp4").touch()

    results = discover_outputs(tmp_path, started_at=run.stat().st_mtime + 10)

    assert results == ()


def test_extract_result_paths_reads_nested_machine_marker() -> None:
    line = (
        RESULT_MARKER
        + '{"growth": "output/growth/youtube.mp4", '
        + '"info": {"result": "output/info/brief.json"}}'
    )

    assert extract_result_paths(line) == (
        "output/growth/youtube.mp4",
        "output/info/brief.json",
    )


def test_discover_outputs_uses_only_hinted_run_directories(tmp_path: Path) -> None:
    growth = tmp_path / "growth"
    growth.mkdir()
    growth_youtube = growth / "youtube.mp4"
    growth_reels = growth / "reels.mp4"
    growth_youtube.touch()
    growth_reels.touch()
    info = tmp_path / "info"
    info.mkdir()
    info_brief = info / "brief.json"
    info_brief.touch()
    unrelated = tmp_path / "other_run"
    unrelated.mkdir()
    (unrelated / "youtube.mp4").touch()

    results = discover_outputs(
        tmp_path,
        started_at=0,
        hinted_paths=[str(growth_youtube), str(info_brief)],
    )

    assert [result.output_dir for result in results] == [growth.resolve(), info.resolve()]
    assert results[0].youtube == growth_youtube.resolve()
    assert results[0].reels == growth_reels.resolve()
    assert results[1].brief == info_brief.resolve()


def test_runner_streams_logs_and_completion(tmp_path: Path) -> None:
    process = FakeProcess(lines=["first\n", "second\n"], returncode=0)
    runner = PipelineRunner(tmp_path, popen=lambda *args, **kwargs: process)

    events = list(runner.run(LaunchRequest(mode="topic", prompt="Guilin")))

    assert [event.message for event in events if event.kind == "log"] == [
        "first",
        "second",
    ]
    assert events[-1].kind == "complete"


def test_runner_reports_nonzero_exit_and_preserves_logs(tmp_path: Path) -> None:
    process = FakeProcess(lines=["provider failed\n"], returncode=2)
    runner = PipelineRunner(tmp_path, popen=lambda *args, **kwargs: process)

    events = list(runner.run(LaunchRequest(mode="topic", prompt="Guilin")))

    assert events[0].message == "provider failed"
    assert events[-1].kind == "error"
    assert "退出码 2" in events[-1].message


def test_runner_rejects_second_active_run(tmp_path: Path) -> None:
    runner = PipelineRunner(tmp_path)
    runner._lock.acquire()
    try:
        events = list(runner.run(LaunchRequest(mode="topic", prompt="Guilin")))
    finally:
        runner._lock.release()

    assert events[-1].kind == "error"
    assert "已有任务" in events[-1].message


def test_runner_reports_validation_without_starting_process(tmp_path: Path) -> None:
    runner = PipelineRunner(tmp_path)

    events = list(runner.run(LaunchRequest(mode="topic", prompt="")))

    assert events[-1].kind == "error"
    assert events[-1].message == "请输入主题"


def test_runner_streams_real_python_output_before_process_exit(tmp_path: Path) -> None:
    script = "import time; print('first'); time.sleep(1.0); print('second')"
    runner = PipelineRunner(
        tmp_path,
        command_builder=lambda request, python: [python, "-c", script],
    )

    started = time.monotonic()
    events = runner.run(LaunchRequest(mode="topic", prompt="Guilin"))
    first = next(events)
    elapsed = time.monotonic() - started
    remaining = list(events)

    assert first == RunEvent("log", "first")
    assert elapsed < 0.6
    assert any(event.message == "second" for event in remaining)


def test_closing_runner_stops_and_reaps_real_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import os,time,pathlib; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "print('ready'); time.sleep(60)"
    )
    runner = PipelineRunner(
        tmp_path,
        command_builder=lambda request, python: [python, "-c", script],
    )
    events = runner.run(LaunchRequest(mode="topic", prompt="Guilin"))

    assert next(events) == RunEvent("log", "ready")
    events.close()

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        __import__("os").kill(pid, 0)
    assert runner._lock.acquire(blocking=False)
    runner._lock.release()


def test_quiet_process_yields_cancellable_heartbeat(tmp_path: Path) -> None:
    pid_file = tmp_path / "quiet-child.pid"
    script = (
        "import os,time,pathlib; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        "time.sleep(2)"
    )
    runner = PipelineRunner(
        tmp_path,
        command_builder=lambda request, python: [python, "-c", script],
    )
    events = runner.run(LaunchRequest(mode="topic", prompt="Guilin"))

    started = time.monotonic()
    first = next(events)
    elapsed = time.monotonic() - started
    assert first == RunEvent("heartbeat", "")
    assert elapsed < 0.6

    events.close()

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        __import__("os").kill(pid, 0)
    assert runner._lock.acquire(blocking=False)
    runner._lock.release()
