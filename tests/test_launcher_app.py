from __future__ import annotations

import json
from pathlib import Path

import gradio as gr

from launcher.app import LAUNCH_OPTIONS, create_app, request_from_form, stream_run
from launcher.core import LaunchRequest, RunEvent, RunOutputs


class StubRunner:
    def __init__(self, events: list[RunEvent]) -> None:
        self.events = events

    def run(self, request: LaunchRequest):
        yield from self.events


def test_topic_form_maps_to_safe_request() -> None:
    request = request_from_form(
        mode_label="主题生成",
        prompt="Guilin",
        video_type="info",
        disable_scout=True,
        video_url="",
        topic="",
        sample_interval=4,
        seconds=32,
        review=False,
    )

    assert request == LaunchRequest(
        mode="topic",
        prompt="Guilin",
        video_type="info",
        disable_scout=True,
        seconds=32,
        review=False,
    )


def test_video_form_maps_to_reference_request() -> None:
    request = request_from_form(
        mode_label="参考视频生成",
        prompt="",
        video_type="both",
        disable_scout=True,
        video_url="https://www.bilibili.com/video/BV1x",
        topic="Roast duck",
        sample_interval=2,
        seconds=None,
        review=True,
    )

    assert request == LaunchRequest(
        mode="video",
        video_url="https://www.bilibili.com/video/BV1x",
        topic="Roast duck",
        sample_interval=2,
        seconds=None,
        review=True,
    )


def test_stream_run_accumulates_logs_and_exposes_outputs(tmp_path: Path) -> None:
    youtube = tmp_path / "youtube.mp4"
    reels = tmp_path / "reels.mp4"
    outputs = RunOutputs(output_dir=tmp_path, youtube=youtube, reels=reels)
    runner = StubRunner(
        [
            RunEvent("log", "first"),
            RunEvent("log", "second"),
            RunEvent("complete", "生成完成。", outputs),
        ]
    )

    updates = list(
        stream_run(
            runner,
            "主题生成",
            "Guilin",
            "info",
            True,
            "",
            "",
            4,
            None,
            False,
        )
    )

    assert updates[0][0] == "⏳ 正在生成…"
    assert updates[-2][1] == "first\nsecond"
    assert updates[-1][0] == "✅ 生成完成。"
    assert updates[-1][2] == str(youtube)
    assert updates[-1][3] == str(reels)
    assert str(tmp_path) in updates[-1][4]


def test_create_app_contains_local_generation_controls() -> None:
    app = create_app(StubRunner([]))

    assert isinstance(app, gr.Blocks)
    config = json.dumps(app.get_config_file(), ensure_ascii=False)
    for label in ["主题生成", "参考视频生成", "关闭自动 Scout", "开始生成"]:
        assert label in config
    buttons = [
        component
        for component in app.get_config_file()["components"]
        if component["type"] == "button"
    ]
    button_values = [component["props"]["value"] for component in buttons]
    assert button_values == ["开始生成"]


def test_launch_configuration_is_loopback_only() -> None:
    assert LAUNCH_OPTIONS == {
        "server_name": "127.0.0.1",
        "server_port": 7860,
        "share": False,
        "inbrowser": True,
    }
